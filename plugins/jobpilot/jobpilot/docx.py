"""A standard-library DOCX engine for resumes: read paragraphs, apply small text edits, and write new documents.

    paragraphs(path)            -> [{index, text, style, container, has_nested, in_table, in_textbox, label}]
    apply(src, dst, edits)      -> [{op, index, before, after}]   (edits a copy; raises EditError)
    text_of(path)               -> plain text of a .docx (body, headers, footers), .md/.txt or .pdf
    from_markdown(md, dst, kind="resume"|"letter", title="", author="")  -> a clean, ATS-friendly .docx
    to_docx(path, dst)          -> converts .doc/.odt/.rtf with LibreOffice

Edits never re-serialize the XML: the engine finds the exact character spans of the text it changes inside
word/document.xml and rewrites only those, so namespaces, mc:Ignorable lists, styles, numbering, fonts and every
untouched byte stay as the user's Word file had them. Members of the zip are rewritten in their original order
with their original compression. Supported ops (each validated before anything is written):

    {"op": "replace", "find": "<exact text, unique in the document>", "with": "<new text>"}
    {"op": "delete", "paragraph": "<unique substring of one paragraph>"}
    {"op": "move", "paragraph": "<unique substring>", "before" | "after": "<unique substring of a sibling>"}
    {"op": "insert_after", "anchor": "<unique substring>", "text": "<new paragraph>", "label": "<optional bold lead-in>"}

Replaced text that spans several runs takes the formatting of the run where the match starts. Paragraphs inside a
text box exist twice in many files (mc:Choice and its mc:Fallback copy); edits to one are mirrored to the other.
"""
import datetime
import html
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from .util import JobPilotError

W_NS = ("http://schemas.openxmlformats.org/wordprocessingml/2006/main",
        "http://purl.oclc.org/ooxml/wordprocessingml/main")
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
DOCUMENT = "word/document.xml"

_TOKEN = re.compile(r"<!--.*?-->|<!\[CDATA\[.*?\]\]>|<\?.*?\?>|<(?:[^>\"']|\"[^\"]*\"|'[^']*')*>", re.S)
_NAME = re.compile(r"</?\s*([^\s/>]+)")
_ATTR = re.compile(r"([^\s=/<>]+)\s*=\s*(\"[^\"]*\"|'[^']*')")
_SYM_CHARS = {"F0B7": "•", "F0A7": "▪", "F076": "❖", "F0D8": "➢", "F0FC": "✓", "F0A8": "□"}
# Length-preserving folds used only to locate a `find` text that differs from the document in typography.
_FOLD = str.maketrans({" ": " ", " ": " ", " ": " ", "‘": "'", "’": "'", "“": '"',
                       "”": '"', "–": "-", "—": "-", "−": "-", "‑": "-"})


class EditError(JobPilotError):
    """An edit that cannot be applied; the message names the op so a retry can fix it."""


def _escape(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _attrs(tag):
    return {k: v[1:-1] for k, v in _ATTR.findall(tag)}


# ---------------------------------------------------------------- reading

def _read_member(path, name):
    try:
        with zipfile.ZipFile(path) as z:
            raw = z.read(name)
    except KeyError:
        raise JobPilotError("%s has no %s (is it a Word .docx file?)" % (path, name)) from None
    except zipfile.BadZipFile:
        raise JobPilotError("%s is not a .docx file (not a zip archive)" % path) from None
    bom = raw.startswith(b"\xef\xbb\xbf")
    return raw[3:].decode("utf-8") if bom else raw.decode("utf-8"), bom


def _prefixes(xml):
    """(w prefix, mc prefix) as declared in the document ('' for a default namespace)."""
    w, mc = "w", "mc"
    for k, v in _ATTR.findall(xml[:20000]):
        val = v[1:-1]
        if k.startswith("xmlns"):
            prefix = k.split(":", 1)[1] if ":" in k else ""
            if val in W_NS:
                w = prefix
            elif val == MC_NS:
                mc = prefix
    return w, mc


class _Doc:
    """One scan of word/document.xml: paragraphs (with exact text spans) in document order."""

    def __init__(self, xml):
        self.xml = xml
        wp, mp = _prefixes(xml)
        q = lambda p, n: ("%s:%s" % (p, n)) if p else n  # noqa: E731
        self.P, self.R, self.T = q(wp, "p"), q(wp, "r"), q(wp, "t")
        self.TAB, self.BR, self.CR = q(wp, "tab"), q(wp, "br"), q(wp, "cr")
        self.NBH, self.SYM, self.PPR, self.PSTYLE = q(wp, "noBreakHyphen"), q(wp, "sym"), q(wp, "pPr"), q(wp, "pStyle")
        self.HYPER, self.TC, self.TXBX, self.SECT = q(wp, "hyperlink"), q(wp, "tc"), q(wp, "txbxContent"), q(wp, "sectPr")
        self.RPR, self.BOLD = q(wp, "rPr"), q(wp, "b")
        self.AC, self.CHOICE, self.FALLBACK = q(mp, "AlternateContent"), q(mp, "Choice"), q(mp, "Fallback")
        self.wp = wp
        self.all = []
        self._scan()
        self.paras = [p for p in self.all if not p["fallback"]]
        for i, p in enumerate(self.paras):
            p["index"] = i

    def _scan(self):
        xml = self.xml
        stack = []          # [(name, start)]
        paras = []          # open paragraphs, innermost last
        t_open = None       # (paragraph, tag_start, content_start)
        for m in _TOKEN.finditer(xml):
            tok = m.group(0)
            if tok.startswith(("<!--", "<![CDATA[", "<?")):
                continue
            nm = _NAME.match(tok)
            if not nm:
                continue
            name = nm.group(1)
            closing = tok.startswith("</")
            selfclose = tok.endswith("/>")
            if closing:
                if name == self.T and t_open:
                    para, tag_start, c0 = t_open
                    raw = xml[c0:m.start()]
                    run = next((st for n, st in reversed(stack) if n == self.R and st >= para["start"]), None)
                    para["segs"].append({"kind": "t", "tag": (tag_start, c0), "span": (c0, m.start()),
                                         "text": html.unescape(raw), "link": self._inside(stack, self.HYPER, para),
                                         "run": run})
                    t_open = None
                if name == self.P and paras:
                    para = paras.pop()
                    para["end"] = m.end()
                    self.all.append(para)
                while stack and stack[-1][0] != name:
                    stack.pop()  # tolerate unbalanced markup
                if stack:
                    stack.pop()
                continue
            if name == self.P:
                para = self._new_para(m.start(), stack)
                if paras:
                    paras[-1]["has_nested"] = True
                if selfclose:
                    para["end"] = m.end()
                    self.all.append(para)
                else:
                    paras.append(para)
                    stack.append((name, m.start()))
                continue
            if paras:
                para = paras[-1]
                in_run = self._inside(stack, self.R, para)
                if name == self.T and not selfclose:
                    t_open = (para, m.start(), m.end())
                elif in_run and name in (self.TAB, self.BR, self.CR) and selfclose:
                    para["segs"].append({"kind": "x", "text": "\t" if name == self.TAB else "\n"})
                elif in_run and name == self.NBH:
                    para["segs"].append({"kind": "x", "text": "-"})
                elif in_run and name == self.SYM:
                    ch = _attrs(tok).get(("%s:char" % self.wp) if self.wp else "char", "")
                    para["segs"].append({"kind": "x", "text": _SYM_CHARS.get(ch.upper(), "")})
                elif name == self.PSTYLE and stack and stack[-1][0] == self.PPR and self._parent_is(stack, para):
                    para["style"] = _attrs(tok).get(("%s:val" % self.wp) if self.wp else "val", "")
                elif name == self.SECT and stack and stack[-1][0] == self.PPR and self._parent_is(stack, para):
                    para["has_sectpr"] = True
            if not selfclose:
                stack.append((name, m.start()))
        for p in self.all:
            p["text"] = "".join(s["text"] for s in p["segs"])
        self.all.sort(key=lambda p: p["start"])

    def _new_para(self, start, stack):
        container = stack[-1] if stack else ("", -1)
        names = [n for n, _ in stack]
        ac = None
        if self.CHOICE in names:
            ci = len(names) - 1 - names[::-1].index(self.CHOICE)
            ac = next((s for n, s in reversed(stack[:ci]) if n == self.AC), None)
        fb_ac = None
        if self.FALLBACK in names:
            fi = len(names) - 1 - names[::-1].index(self.FALLBACK)
            fb_ac = next((s for n, s in reversed(stack[:fi]) if n == self.AC), None)
        return {"start": start, "end": None, "segs": [], "has_nested": False, "style": "", "has_sectpr": False,
                "container": container, "fallback": self.FALLBACK in names, "choice_ac": ac, "fallback_ac": fb_ac,
                "in_table": self.TC in names, "in_textbox": self.TXBX in names, "depth": len(stack)}

    def _inside(self, stack, name, para):
        """Whether `name` is open between the paragraph's start and now (on the stack above the paragraph)."""
        for n, s in reversed(stack):
            if s < para["start"]:
                return False
            if n == name:
                return True
        return False

    def _parent_is(self, stack, para):
        """The element under the top of the stack is the paragraph itself (a direct w:pPr child)."""
        return len(stack) >= 2 and stack[-2] == (self.P, para["start"])

    # ------------------------------------------------------------ lookups
    def find_text(self, needle, op):
        """The single (paragraph, start, end) where `needle` occurs; folds typography if the exact text is absent."""
        hits = [(p, i) for p in self.paras for i in _all_index(p["text"], needle)]
        if not hits:
            folded = needle.translate(_FOLD)
            hits = [(p, i) for p in self.paras for i in _all_index(p["text"].translate(_FOLD), folded)]
        if not hits:
            raise EditError("%s: text not found in the resume: %r%s" % (op, _short(needle), self._hint(needle)))
        if len(hits) > 1:
            raise EditError("%s: text occurs %d times; quote more of the paragraph to make it unique: %r"
                            % (op, len(hits), _short(needle)))
        p, i = hits[0]
        return p, i, i + len(needle)

    def find_para(self, needle, op):
        hits = [p for p in self.paras if needle in p["text"]]
        if not hits:
            folded = needle.translate(_FOLD)
            hits = [p for p in self.paras if folded in p["text"].translate(_FOLD)]
        if not hits:
            raise EditError("%s: no paragraph contains %r%s" % (op, _short(needle), self._hint(needle)))
        if len(hits) > 1:
            raise EditError("%s: %d paragraphs contain %r; quote more to pick one" % (op, len(hits), _short(needle)))
        return hits[0]

    def _hint(self, needle):
        words = set(re.findall(r"\w{4,}", needle.lower()))
        if not words:
            return ""
        best = max(self.paras, key=lambda p: len(words & set(re.findall(r"\w{4,}", p["text"].lower()))), default=None)
        if best and words & set(re.findall(r"\w{4,}", best["text"].lower())):
            return " (closest paragraph [%d]: %r)" % (best["index"], _short(best["text"], 120))
        return ""

    def mirrors(self, para):
        """Fallback copies of a text-box paragraph (same AlternateContent, same text)."""
        if not para.get("choice_ac"):
            return []
        return [q for q in self.all if q["fallback"] and q["fallback_ac"] == para["choice_ac"]
                and q["text"] == para["text"]]


def _all_index(text, needle):
    out, i = [], text.find(needle)
    while needle and i >= 0:
        out.append(i)
        i = text.find(needle, i + len(needle))
    return out


def _short(text, n=90):
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


_BOLD_OFF = re.compile(r'val="(?:0|false|off)"')


def _is_bold(rpr, doc):
    m = re.search(r"<%s(\s[^>]*)?/>" % re.escape(doc.BOLD), rpr or "")
    return bool(m) and not _BOLD_OFF.search(m.group(0))


def _run_rpr(doc, run_start, limit):
    """The w:rPr of the run that starts at run_start (searched only up to `limit`), or ''."""
    if run_start is None:
        return ""
    m = re.compile(r"<%s(?:\s[^>]*)?>(.*?)</%s>" % (re.escape(doc.RPR), re.escape(doc.RPR)), re.S).search(
        doc.xml, run_start, limit)
    return m.group(0) if m else ""


def _label(para, doc):
    """A bold lead-in such as "Ranking Models:" (the paragraph's first text run is bold and ends with ':'), else ''."""
    seg = next((g for g in para["segs"] if g["kind"] == "t" and g["text"].strip(" •·▪\t")), None)
    if not seg:
        return ""
    lab = seg["text"].strip(" •·▪\t")
    if not lab.endswith(":") or len(lab.split()) > 6:
        return ""
    return lab if _is_bold(_run_rpr(doc, seg.get("run"), seg["tag"][0]), doc) else ""


def paragraphs(path):
    """The body's paragraphs in document order, as the tailoring prompt and `jobpilot resume paragraphs` show them."""
    xml, _ = _read_member(path, DOCUMENT)
    doc = _Doc(xml)
    out = []
    for p in doc.paras:
        out.append({"index": p["index"], "text": p["text"], "style": p["style"], "container": p["container"][0],
                    "has_nested": p["has_nested"], "in_table": p["in_table"], "in_textbox": p["in_textbox"],
                    "label": _label(p, doc)})
    return out


def _part_text(xml):
    return "\n".join(p["text"] for p in _Doc(xml).paras)


def text_of(path):
    """Plain text of a resume file (.docx with headers and footers, .md, .txt or .pdf)."""
    path = Path(path)
    suffix = path.suffix.lower()
    if not path.exists():
        raise JobPilotError("no such file: %s" % path)
    if suffix in (".md", ".markdown", ".txt", ".text"):
        return path.read_text(encoding="utf-8")
    if suffix == ".pdf":
        tool = shutil.which("pdftotext") or ("/opt/homebrew/bin/pdftotext"
                                            if os.path.exists("/opt/homebrew/bin/pdftotext") else None)
        if not tool:
            raise JobPilotError("reading a PDF needs pdftotext (macOS: brew install poppler; Debian/Ubuntu: apt "
                                "install poppler-utils), or open the PDF in Claude Code directly")
        r = subprocess.run([tool, "-layout", "-enc", "UTF-8", str(path), "-"], capture_output=True, text=True,
                           timeout=60)
        if r.returncode != 0:
            raise JobPilotError("pdftotext could not read %s: %s" % (path, r.stderr.strip()[:200]))
        return r.stdout
    if suffix in (".doc", ".odt", ".rtf"):
        with tempfile.TemporaryDirectory(prefix="jobpilot-conv-") as tmp:
            return text_of(to_docx(path, Path(tmp) / (path.stem + ".docx")))
    if suffix != ".docx":
        raise JobPilotError("unsupported resume format %s (use .docx, .pdf, .md or .txt)" % suffix)
    parts = []
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        heads = sorted(n for n in names if re.fullmatch(r"word/header\d*\.xml", n))
        feet = sorted(n for n in names if re.fullmatch(r"word/footer\d*\.xml", n))
    for n in heads:
        parts.append(_part_text(_read_member(path, n)[0]))
    parts.append(_part_text(_read_member(path, DOCUMENT)[0]))
    for n in feet:
        parts.append(_part_text(_read_member(path, n)[0]))
    return "\n".join(p for p in parts if p.strip())


# ---------------------------------------------------------------- editing

def _set_spans(xml, changes):
    """Apply [(start, end, text)] to the string, last span first."""
    for start, end, text in sorted(changes, key=lambda c: c[0], reverse=True):
        xml = xml[:start] + text + xml[end:]
    return xml


def _replace_in_para(doc, para, start, end, new):
    """Span changes that put `new` where para.text[start:end] was. `new` is one string (it goes into the run where
    the span starts) or one string per touched text run."""
    changes, pos, k = [], 0, 0
    parts = new if isinstance(new, list) else None
    for seg in para["segs"]:
        a, b = pos, pos + len(seg["text"])
        pos = b
        if b <= start or a >= end:
            continue
        if seg["kind"] != "t":
            raise EditError("replace: the text crosses a tab, line break or symbol; edit the text on one side of it")
        if a == b:
            continue
        if seg.get("link"):
            raise EditError("replace: that text is a hyperlink (contact details and links are not tailored)")
        head = seg["text"][: max(0, start - a)]
        tail = seg["text"][end - a:] if b > end else ""
        piece = (parts[k] if k < len(parts) else "") if parts is not None else (new if k == 0 else "")
        k += 1
        c0, c1 = seg["span"]
        changes.append((c0, c1, _escape(head + piece + tail)))
        t0, t1 = seg["tag"]
        tag = doc.xml[t0:t1]
        if "xml:space" not in tag:
            changes.append((t0, t1, tag[:-1].rstrip() + ' xml:space="preserve">'))
    if k == 0:
        raise EditError("replace: nothing editable at that position")
    return changes


def _changed_span(text, s, e, new):
    """(start, end, replacement) covering only what differs between text[s:e] and `new`: the common beginning and
    end stay untouched, so a reworded bullet keeps its bold label and the label keeps the body's formatting. A pure
    insertion takes one neighboring character along so the new text joins an existing run."""
    old = text[s:e]
    p = 0
    while p < min(len(old), len(new)) and old[p] == new[p]:
        p += 1
    q = 0
    while q < min(len(old), len(new)) - p and old[len(old) - 1 - q] == new[len(new) - 1 - q]:
        q += 1
    s2, e2, mid = s + p, e - q, new[p:len(new) - q]
    if s2 == e2:
        if s2 > 0 and text[s2 - 1] not in "\t\n":
            s2, mid = s2 - 1, text[s2 - 1] + mid
        elif e2 < len(text) and text[e2] not in "\t\n":
            e2, mid = e2 + 1, mid + text[e2]
    return s2, e2, mid


def _split_for_runs(doc, para, s, e, mid):
    """When the changed span starts in a bold lead-in ("Label:") and runs into the body, keep the new label in the
    label's run and the rest in the body's run; otherwise everything goes into the first run."""
    touched, pos = [], 0
    for seg in para["segs"]:
        a, b = pos, pos + len(seg["text"])
        pos = b
        if b > s and a < e and seg["kind"] == "t" and a != b:
            touched.append(seg["text"][max(0, s - a): min(len(seg["text"]), e - a)])
    if len(touched) < 2 or not touched[0].rstrip().endswith(":") or ":" not in mid:
        return mid
    cut = mid.index(":") + 1
    want = len(touched[0]) - len(touched[0].rstrip())
    while want and cut < len(mid) and mid[cut] == " ":
        cut, want = cut + 1, want - 1
    return [mid[:cut], mid[cut:]] + [""] * (len(touched) - 2)


def _op_replace(doc, op, n):
    find, new = op.get("find"), op.get("with")
    if not isinstance(find, str) or not find:
        raise EditError("edit %d (replace): `find` is required" % n)
    if not isinstance(new, str):
        raise EditError("edit %d (replace): `with` is required (use delete to remove a paragraph)" % n)
    if "\n" in new or "\t" in new:
        raise EditError("edit %d (replace): `with` may not contain tabs or line breaks" % n)
    para, s, e = doc.find_text(find, "edit %d (replace)" % n)
    before = para["text"]
    after = before[:s] + new + before[e:]
    s2, e2, mid = _changed_span(before, s, e, new)
    if s2 == e2 and not mid:
        return doc.xml, {"op": "replace", "index": para["index"], "before": before, "after": after}
    parts = _split_for_runs(doc, para, s2, e2, mid)
    changes = _replace_in_para(doc, para, s2, e2, parts)
    for mirror in doc.mirrors(para):
        changes += _replace_in_para(doc, mirror, s2, e2, parts)
    return _set_spans(doc.xml, changes), {"op": "replace", "index": para["index"], "before": before, "after": after}


def _op_delete(doc, op, n):
    needle = op.get("paragraph")
    if not isinstance(needle, str) or not needle:
        raise EditError("edit %d (delete): `paragraph` is required" % n)
    para = doc.find_para(needle, "edit %d (delete)" % n)
    if para["has_nested"]:
        raise EditError("edit %d (delete): that paragraph holds a text box or shape; it cannot be deleted" % n)
    if para["has_sectpr"]:
        raise EditError("edit %d (delete): that paragraph ends a page section; it cannot be deleted" % n)
    siblings = [p for p in doc.paras if p["container"] == para["container"]]
    if para["container"][0] in (doc.TC, doc.TXBX) and len(siblings) == 1:
        # a table cell or a text box must keep one paragraph: empty it instead
        changes = []
        for seg in para["segs"]:
            if seg["kind"] == "t" and seg["text"]:
                changes.append((seg["span"][0], seg["span"][1], ""))
        xml = _set_spans(doc.xml, changes)
    else:
        spans = [(para["start"], para["end"], "")] + [(q["start"], q["end"], "") for q in doc.mirrors(para)]
        xml = _set_spans(doc.xml, spans)
    return xml, {"op": "delete", "index": para["index"], "before": para["text"], "after": None}


def _op_move(doc, op, n):
    needle = op.get("paragraph")
    where = "before" if op.get("before") else "after" if op.get("after") else None
    if not isinstance(needle, str) or not needle or not where or (op.get("before") and op.get("after")):
        raise EditError("edit %d (move): give `paragraph` and exactly one of `before` / `after`" % n)
    para = doc.find_para(needle, "edit %d (move)" % n)
    target = doc.find_para(op[where], "edit %d (move %s)" % (n, where))
    if para is target:
        raise EditError("edit %d (move): a paragraph cannot move relative to itself" % n)
    if para["container"] != target["container"]:
        raise EditError("edit %d (move): both paragraphs must sit in the same section, table cell or text box" % n)
    if para.get("choice_ac") or para["has_sectpr"] or target["has_sectpr"]:
        raise EditError("edit %d (move): paragraphs in text boxes or at section breaks cannot be moved" % n)
    chunk = doc.xml[para["start"]:para["end"]]
    xml = doc.xml[:para["start"]] + doc.xml[para["end"]:]
    shift = len(chunk) if target["start"] > para["start"] else 0
    pos = (target["start"] if where == "before" else target["end"]) - shift
    xml = xml[:pos] + chunk + xml[pos:]
    return xml, {"op": "move", "index": para["index"], "before": para["text"], "after": para["text"],
                 "where": "%s [%d]" % (where, target["index"])}


def _children(xml, start, end):
    """[(name, child_start, child_end)] of the direct child elements of the element spanning xml[start:end]."""
    out, depth, cur = [], 0, None
    first = _TOKEN.match(xml, start)
    pos = first.end() if first else start
    for m in _TOKEN.finditer(xml, pos, end):
        tok = m.group(0)
        if tok.startswith(("<!--", "<![CDATA[", "<?")):
            continue
        nm = _NAME.match(tok)
        if not nm:
            continue
        if tok.startswith("</"):
            depth -= 1
            if depth == 0 and cur:
                out.append((cur[0], cur[1], m.end()))
                cur = None
            if depth < 0:
                break
            continue
        if tok.endswith("/>"):
            if depth == 0:
                out.append((nm.group(1), m.start(), m.end()))
            continue
        if depth == 0:
            cur = (nm.group(1), m.start())
        depth += 1
    return out


def _op_insert_after(doc, op, n):
    anchor, text, label = op.get("anchor"), op.get("text"), (op.get("label") or "").strip()
    if not isinstance(anchor, str) or not anchor or not isinstance(text, str) or not text.strip():
        raise EditError("edit %d (insert_after): give `anchor` and `text`" % n)
    if any(c in text + label for c in "\n\t"):
        raise EditError("edit %d (insert_after): text may not contain tabs or line breaks" % n)
    para = doc.find_para(anchor, "edit %d (insert_after)" % n)
    if para.get("choice_ac") or para["has_sectpr"] or para["has_nested"]:
        raise EditError("edit %d (insert_after): cannot insert after a paragraph in a text box, with a shape or at a "
                        "section break" % n)
    xml = doc.xml
    open_tag = _TOKEN.match(xml, para["start"]).group(0)
    if open_tag.endswith("/>"):
        raise EditError("edit %d (insert_after): the anchor paragraph is empty" % n)
    open_tag = re.sub(r'\s+w14:(?:paraId|textId)="[^"]*"', "", open_tag)
    kids = _children(xml, para["start"], para["end"])
    ppr = next((xml[a:b] for nm, a, b in kids if nm == doc.PPR), "")
    if ppr:
        ppr = re.sub(r"<%s(?:\s[^>]*)?>.*?</%s>" % (re.escape(doc.SECT), re.escape(doc.SECT)), "", ppr, flags=re.S)
    t_rx = re.compile(r"<%s(?:\s[^>]*)?>(.*?)</%s>" % (re.escape(doc.T), re.escape(doc.T)), re.S)
    tab_rx = re.compile(r"<%s(?:\s[^>]*)?/>" % re.escape(doc.TAB))
    prefix, text_runs = [], []
    for nm, a, b in kids:
        if nm != doc.R:
            continue
        run = xml[a:b]
        t = "".join(html.unescape(x) for x in t_rx.findall(run))
        if not text_runs and (tab_rx.search(run) or t) and not t.strip(" •·▪◦‣-–*"):
            prefix.append(run)          # a manual bullet ("•" and a tab) that the new paragraph keeps
        elif t.strip():
            text_runs.append(run)
    if not text_runs:
        raise EditError("edit %d (insert_after): the anchor paragraph has no text run to copy formatting from" % n)
    rpr_rx = re.compile(r"<%s(?:\s[^>]*)?>.*?</%s>|<%s/>" % ((re.escape(doc.RPR),) * 3), re.S)
    rpr = lambda run: (rpr_rx.search(run).group(0) if rpr_rx.search(run) else "")  # noqa: E731
    first_rpr, body_rpr = rpr(text_runs[0]), rpr(text_runs[-1])
    bold_rx = re.compile(r"<%s(?:\s[^>]*)?/>" % re.escape(doc.BOLD))
    if label and len(text_runs) == 1 and _is_bold(body_rpr, doc):
        body_rpr = bold_rx.sub("", body_rpr)   # one bold run in the anchor: the body of the new one is not bold
    new_runs = []
    if not prefix:
        # a manual bullet typed into the first text run ("• Trained ...", "•<tab>Trained ...") is kept as well
        lead = re.match(r"\s*([•·▪◦‣*–-])(\s+)", _run_text(text_runs[0], doc))
        if lead:
            gap = "<%s/>" % doc.TAB if "\t" in lead.group(2) else '<%s xml:space="preserve"> </%s>' % (doc.T, doc.T)
            new_runs.append("<%s>%s<%s>%s</%s>%s</%s>" % (doc.R, first_rpr if not _is_bold(first_rpr, doc) else
                                                         bold_rx.sub("", first_rpr), doc.T, _escape(lead.group(1)),
                                                         doc.T, gap, doc.R))
    if label:
        if _is_bold(first_rpr, doc):
            lab_rpr = first_rpr
        elif body_rpr.endswith("/>"):
            lab_rpr = "<%s><%s/></%s>" % (doc.RPR, doc.BOLD, doc.RPR)
        elif body_rpr:
            lab_rpr = _with_bold(body_rpr, doc)
        else:
            lab_rpr = "<%s><%s/></%s>" % (doc.RPR, doc.BOLD, doc.RPR)
        new_runs.append(_mk_run(doc, lab_rpr, label + " "))
    new_runs.append(_mk_run(doc, body_rpr, text.strip() if label else text))
    new_xml = open_tag + ppr + "".join(prefix) + "".join(new_runs) + "</%s>" % doc.P
    out = xml[:para["end"]] + new_xml + xml[para["end"]:]
    return out, {"op": "insert_after", "index": para["index"] + 1, "before": None,
                 "after": (label + " " + text.strip()) if label else text}


# Schema order of the run properties that may precede w:b inside w:rPr.
_BEFORE_B = ("rStyle", "rFonts")


def _with_bold(rpr, doc):
    """The run properties with w:b added at its schema position (after rStyle/rFonts)."""
    kids = _children(rpr, 0, len(rpr))
    local = lambda nm: nm.split(":", 1)[-1]  # noqa: E731
    insert_at = None
    for nm, a, b in kids:
        if local(nm) not in _BEFORE_B:
            insert_at = a
            break
    if insert_at is None:
        insert_at = rpr.rfind("</")
    return rpr[:insert_at] + "<%s/>" % doc.BOLD + rpr[insert_at:]


def _run_text(run, doc):
    """The text of one run, tabs and breaks included."""
    out = []
    for m in re.finditer(r"<%s(?:\s[^>]*)?>(.*?)</%s>|<(%s|%s|%s)(?:\s[^>]*)?/>" % (
            re.escape(doc.T), re.escape(doc.T), re.escape(doc.TAB), re.escape(doc.BR), re.escape(doc.CR)), run, re.S):
        out.append(html.unescape(m.group(1)) if m.group(1) is not None else ("\t" if m.group(2) == doc.TAB else "\n"))
    return "".join(out)


def _mk_run(doc, rpr, text):
    return '<%s>%s<%s xml:space="preserve">%s</%s></%s>' % (doc.R, rpr, doc.T, _escape(text), doc.T, doc.R)


OPS = {"replace": _op_replace, "delete": _op_delete, "move": _op_move, "insert_after": _op_insert_after}


def apply_xml(xml, edits):
    """(new document.xml, changes) after applying the edits in order; raises EditError naming the failing edit."""
    changes = []
    for n, op in enumerate(edits or [], 1):
        if not isinstance(op, dict) or op.get("op") not in OPS:
            raise EditError("edit %d: op must be one of %s" % (n, ", ".join(OPS)))
        doc = _Doc(xml)
        xml, change = OPS[op["op"]](doc, op, n)
        change["edit"] = n
        changes.append(change)
    return xml, changes


def apply(src, dst, edits):
    """Write a copy of the .docx `src` to `dst` with the edits applied. Returns the change list."""
    xml, bom = _read_member(src, DOCUMENT)
    new_xml, changes = apply_xml(xml, edits)
    data = (b"\xef\xbb\xbf" if bom else b"") + new_xml.encode("utf-8")
    _rezip(src, dst, {DOCUMENT: data})
    return changes


def _rezip(src, dst, replace):
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dst.parent, prefix=".tmp-", suffix=".docx")
    os.close(fd)
    try:
        with zipfile.ZipFile(src) as zin, zipfile.ZipFile(tmp, "w") as zout:
            for info in zin.infolist():
                data = replace.get(info.filename)
                if data is None:
                    data = zin.read(info.filename)
                out = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                out.compress_type = info.compress_type
                out.external_attr = info.external_attr
                out.create_system = info.create_system
                zout.writestr(out, data)
        os.replace(tmp, dst)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


# ---------------------------------------------------------------- conversion

def soffice():
    for c in (os.environ.get("JOBPILOT_SOFFICE"), shutil.which("soffice"), shutil.which("libreoffice"),
              "/opt/homebrew/bin/soffice", "/Applications/LibreOffice.app/Contents/MacOS/soffice"):
        if c and os.path.exists(c):
            return c
    return None


def _convert(path, fmt, outdir, timeout=120):
    exe = soffice()
    if not exe:
        raise JobPilotError("LibreOffice is not installed (macOS: brew install --cask libreoffice; Linux: apt install "
                            "libreoffice-writer)")
    with tempfile.TemporaryDirectory(prefix="jobpilot-lo-") as tmp:
        profile = Path(tmp) / "profile"
        cmd = [exe, "-env:UserInstallation=%s" % profile.as_uri(), "--headless", "--norestore", "--nologo",
               "--convert-to", fmt, "--outdir", str(tmp), str(path)]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise JobPilotError("LibreOffice did not finish within %d s" % timeout) from None
        produced = Path(tmp) / (Path(path).stem + "." + fmt.split(":")[0])
        if not produced.exists():
            raise JobPilotError("LibreOffice produced no %s: %s" % (fmt, (r.stderr or r.stdout).strip()[-300:]))
        target = Path(outdir) / produced.name
        shutil.copyfile(produced, target)
        return target


def to_pdf(docx_path, pdf_path=None, timeout=120):
    """Render a .docx to PDF with LibreOffice (an isolated profile, so a running LibreOffice is not disturbed)."""
    docx_path = Path(docx_path)
    produced = _convert(docx_path, "pdf", docx_path.parent, timeout)
    if pdf_path and Path(pdf_path) != produced:
        shutil.move(str(produced), str(pdf_path))
        return Path(pdf_path)
    return produced


def to_docx(path, dst):
    """Convert .doc/.odt/.rtf to .docx with LibreOffice."""
    dst = Path(dst)
    with tempfile.TemporaryDirectory(prefix="jobpilot-docx-") as tmp:
        produced = _convert(path, "docx:MS Word 2007 XML", tmp)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(produced, dst)
    return dst


def page_count(pdf_path):
    for tool in (shutil.which("pdfinfo"), "/opt/homebrew/bin/pdfinfo"):
        if tool and os.path.exists(tool):
            try:
                r = subprocess.run([tool, str(pdf_path)], capture_output=True, text=True, timeout=60)
                m = re.search(r"^Pages:\s+(\d+)", r.stdout, re.M)
                if m:
                    return int(m.group(1))
            except (OSError, subprocess.SubprocessError):
                pass
    data = Path(pdf_path).read_bytes()
    return len(re.findall(rb"/Type\s*/Page(?!s)", data))


# ---------------------------------------------------------------- writing new documents from Markdown

_CT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>
<Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>"""

_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>"""

_W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" ' \
     'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'

# Letter paper, 0.6 inch margins: the text width is 12240 - 2 * 864 = 10512 twips (the right tab stop for dates).
PAGE = {"w": 12240, "h": 15840, "margin": 864, "text": 10512}


def _styles(body_pt=10.5, font="Calibri"):
    sz = int(round(body_pt * 2))
    fonts = '<w:rFonts w:ascii="{f}" w:hAnsi="{f}" w:eastAsia="{f}" w:cs="{f}"/>'.format(f=font)
    border = '<w:pBdr><w:bottom w:val="single" w:sz="6" w:space="1" w:color="444444"/></w:pBdr>'
    right_tab = '<w:tabs><w:tab w:val="right" w:pos="%d"/></w:tabs>' % PAGE["text"]
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles %(ns)s>
<w:docDefaults><w:rPrDefault><w:rPr>%(fonts)s<w:sz w:val="%(sz)d"/><w:szCs w:val="%(sz)d"/><w:lang w:val="en-US"/></w:rPr></w:rPrDefault>
<w:pPrDefault><w:pPr><w:spacing w:after="0" w:line="252" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>
<w:pPr><w:spacing w:after="40"/><w:jc w:val="center"/></w:pPr><w:rPr><w:b/><w:sz w:val="36"/><w:szCs w:val="36"/></w:rPr></w:style>
<w:style w:type="paragraph" w:customStyle="1" w:styleId="Contact"><w:name w:val="Contact"/><w:basedOn w:val="Normal"/><w:qFormat/>
<w:pPr><w:spacing w:after="60"/><w:jc w:val="center"/></w:pPr><w:rPr><w:sz w:val="%(small)d"/><w:szCs w:val="%(small)d"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>
<w:pPr><w:keepNext/>%(border)s<w:spacing w:before="160" w:after="60"/><w:outlineLvl w:val="0"/></w:pPr>
<w:rPr><w:b/><w:caps/><w:spacing w:val="10"/><w:sz w:val="%(head)d"/><w:szCs w:val="%(head)d"/></w:rPr></w:style>
<w:style w:type="paragraph" w:customStyle="1" w:styleId="Entry"><w:name w:val="Entry"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>
<w:pPr><w:keepNext/>%(right_tab)s<w:spacing w:before="80"/></w:pPr></w:style>
<w:style w:type="paragraph" w:customStyle="1" w:styleId="EntryDetail"><w:name w:val="Entry Detail"/><w:basedOn w:val="Normal"/><w:qFormat/>
<w:pPr><w:keepNext/>%(right_tab)s</w:pPr><w:rPr><w:i/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="ListBullet"><w:name w:val="List Bullet"/><w:basedOn w:val="Normal"/><w:qFormat/>
<w:pPr><w:numPr><w:numId w:val="1"/></w:numPr><w:spacing w:before="20"/><w:ind w:left="288" w:hanging="216"/></w:pPr></w:style>
<w:style w:type="paragraph" w:customStyle="1" w:styleId="LetterBody"><w:name w:val="Letter Body"/><w:basedOn w:val="Normal"/><w:qFormat/>
<w:pPr><w:spacing w:after="160" w:line="276" w:lineRule="auto"/></w:pPr><w:rPr><w:sz w:val="22"/><w:szCs w:val="22"/></w:rPr></w:style>
<w:style w:type="character" w:default="1" w:styleId="DefaultParagraphFont"><w:name w:val="Default Paragraph Font"/><w:uiPriority w:val="1"/><w:semiHidden/></w:style>
<w:style w:type="character" w:styleId="Hyperlink"><w:name w:val="Hyperlink"/><w:basedOn w:val="DefaultParagraphFont"/><w:rPr><w:color w:val="1F4E9C"/></w:rPr></w:style>
</w:styles>""" % {"ns": _W, "fonts": fonts, "sz": sz, "small": max(sz - 2, 16), "head": sz + 2, "border": border,
                    "right_tab": right_tab}


_NUMBERING = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering %s>
<w:abstractNum w:abstractNumId="0"><w:multiLevelType w:val="singleLevel"/>
<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="•"/><w:lvlJc w:val="left"/>
<w:pPr><w:ind w:left="288" w:hanging="216"/></w:pPr><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/></w:rPr></w:lvl>
</w:abstractNum>
<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>
</w:numbering>""" % _W

_SETTINGS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:settings %s><w:defaultTabStop w:val="720"/><w:characterSpacingControl w:val="doNotCompress"/>
<w:compat><w:compatSetting w:name="compatibilityMode" w:uri="http://schemas.microsoft.com/office/word" w:val="15"/></w:compat>
</w:settings>""" % _W


def _core(title, author):
    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<dc:title>%s</dc:title><dc:creator>%s</dc:creator><cp:lastModifiedBy>%s</cp:lastModifiedBy>
<dcterms:created xsi:type="dcterms:W3CDTF">%s</dcterms:created><dcterms:modified xsi:type="dcterms:W3CDTF">%s</dcterms:modified>
</cp:coreProperties>""" % (_escape(title), _escape(author), _escape(author), now, now)


_APP = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"><Application>JobPilot</Application></Properties>"""

_INLINE = re.compile(r"\*\*(.+?)\*\*|(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])|(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w_])|"
                     r"\[([^\]]+)\]\(([^)\s]+)\)")


class _Builder:
    def __init__(self):
        self.body = []
        self.links = []   # external hyperlink targets -> rId4...

    def runs(self, text, bold=False, italic=False):
        out, pos = [], 0
        for m in _INLINE.finditer(text):
            if m.start() > pos:
                out.append(self.run(text[pos:m.start()], bold, italic))
            if m.group(1) is not None:
                out.append(self.run(m.group(1), True, italic))
            elif m.group(2) is not None or m.group(3) is not None:
                out.append(self.run(m.group(2) or m.group(3), bold, True))
            else:
                out.append(self.link(m.group(4), m.group(5), bold, italic))
            pos = m.end()
        if pos < len(text):
            out.append(self.run(text[pos:], bold, italic))
        return "".join(out)

    @staticmethod
    def run(text, bold=False, italic=False, style=""):
        if not text:
            return ""
        # w:rPr children follow the schema order: rStyle, then b, then i.
        props = ('<w:rStyle w:val="%s"/>' % style if style else "") + ("<w:b/>" if bold else "") + \
            ("<w:i/>" if italic else "")
        rpr = "<w:rPr>%s</w:rPr>" % props if props else ""
        inner = []
        for k, line in enumerate(text.split("\n")):
            if k:
                inner.append("<w:br/>")
            parts = line.split("\t")
            inner.append("<w:tab/>".join('<w:t xml:space="preserve">%s</w:t>' % _escape(p) if p else "" for p in parts))
        return "<w:r>%s%s</w:r>" % (rpr, "".join(inner))

    def link(self, text, url, bold=False, italic=False):
        if not re.match(r"(?i)(https?://|mailto:)", url):
            url = ("mailto:" + url) if "@" in url else ("https://" + url)
        self.links.append(url)
        rid = "rId%d" % (len(self.links) + 3)
        return '<w:hyperlink r:id="%s" w:history="1">%s</w:hyperlink>' % (rid, self.run(text, bold, italic, "Hyperlink"))

    def para(self, style, content, extra_ppr=""):
        ppr = '<w:pPr><w:pStyle w:val="%s"/>%s</w:pPr>' % (style, extra_ppr) if style else (
            "<w:pPr>%s</w:pPr>" % extra_ppr if extra_ppr else "")
        self.body.append("<w:p>%s%s</w:p>" % (ppr, content))

    def document(self):
        sect = ('<w:sectPr><w:pgSz w:w="%(w)d" w:h="%(h)d"/><w:pgMar w:top="%(margin)d" w:right="%(margin)d" '
                'w:bottom="%(margin)d" w:left="%(margin)d" w:header="432" w:footer="432" w:gutter="0"/></w:sectPr>' % PAGE)
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:document %s><w:body>%s%s</w:body>'
                '</w:document>' % (_W, "".join(self.body), sect))

    def rels(self):
        rows = ['<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>',
                '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>',
                '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" Target="settings.xml"/>']
        for i, url in enumerate(self.links, 4):
            rows.append('<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
                        'hyperlink" Target="%s" TargetMode="External"/>' % (i, _escape(url).replace('"', "&quot;")))
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="http://schemas.'
                'openxmlformats.org/package/2006/relationships">%s</Relationships>' % "".join(rows))


def _split_right(text):
    """'Role — Org | Jun 2023 – Present' -> ('Role — Org', 'Jun 2023 – Present')."""
    if " | " in text:
        left, right = text.rsplit(" | ", 1)
        return left.strip(), right.strip()
    return text.strip(), ""


def _resume_body(b, lines):
    seen_name = seen_section = False
    pending = []  # [style, text] of a bullet or paragraph that indented continuation lines extend

    def flush():
        if pending:
            b.para(pending[0], b.runs(pending[1]))
            pending.clear()

    for raw in lines:
        line = raw.rstrip()
        s = line.strip()
        if pending and s and raw[:1] in (" ", "\t") and not re.match(r"^(?:[-*+•]\s|#)", s):
            pending[1] += " " + s  # a wrapped line continues the bullet above it
            continue
        if not s or re.fullmatch(r"-{3,}|\*{3,}|_{3,}", s):
            flush()
            continue
        if s.startswith("# ") and not seen_name:
            flush()
            b.para("Title", b.runs(s[2:].strip()))
            seen_name = True
            continue
        if s.startswith("## ") or (s.startswith("# ") and seen_name):
            flush()
            b.para("Heading1", b.runs(s.lstrip("#").strip()))
            seen_section = True
            continue
        if s.startswith("### "):
            flush()
            left, right = _split_right(s[4:].strip())
            if " — " in left and "**" not in left:
                role, org = left.split(" — ", 1)
                content = b.runs(role, bold=True) + b.run(" — ") + b.runs(org)
            else:
                content = b.runs(left, bold="**" not in left)
            if right:
                content += b.run("\t") + b.runs(right)
            b.para("Entry", content)
            continue
        m = re.match(r"^([-*+•])\s+(.*)$", s)
        if m and seen_name:
            flush()
            pending[:] = ["ListBullet", m.group(2).strip()]
            continue
        if not seen_section and seen_name:
            flush()
            b.para("Contact", b.runs(s))
            continue
        if re.fullmatch(r"\*[^*].*\*|_[^_].*_", s) or (s.startswith("*") and " | " in s and s.endswith("*")):
            flush()
            left, right = _split_right(s.strip("*_ "))
            b.para("EntryDetail", b.runs(left) + (b.run("\t") + b.runs(right) if right else ""))
            continue
        flush()
        pending[:] = [None, s]
    flush()


def _letter_body(b, lines, date_line):
    seen_name, header_done = False, False
    buf = []

    def flush():
        if buf:
            b.para("LetterBody", b.runs("\n".join(buf)))
            buf.clear()

    for raw in lines:
        s = raw.strip()
        if s.startswith("# ") and not seen_name:
            b.para("Title", b.runs(s[2:].strip()), '<w:jc w:val="left"/>')
            seen_name = True
            continue
        if seen_name and not header_done and s and not buf:
            if s.startswith(("Dear ", "To ")) or len(s) > 120:
                header_done = True
                if date_line:
                    b.para("LetterBody", b.runs(date_line))
            else:
                b.para("Contact", b.runs(s), '<w:jc w:val="left"/>')
                continue
        if not s:
            flush()
            continue
        if not seen_name:
            header_done = True
        buf.append(s)
    flush()


def from_markdown(md, dst, kind="resume", title="", author="", font="Calibri", body_pt=10.5, date_line=None):
    """Write a .docx built from a Markdown resume (or cover letter) to `dst`. Returns dst.

    Resume Markdown: `# Name`, contact line(s), `## Section` headings, `### Role — Organization | Dates` entries, an
    optional `*Location | Detail*` line, `- ` bullets (an optional `**Label:**` lead-in), and plain lines such as
    `**Languages:** Python, SQL`. **bold**, *italic* and [links](https://...) work everywhere."""
    lines = (md or "").replace("\r\n", "\n").split("\n")
    b = _Builder()
    if kind == "letter":
        today = datetime.date.today()
        _letter_body(b, lines, date_line if date_line is not None else "%s %d, %d" % (today.strftime("%B"), today.day,
                                                                                      today.year))
    else:
        _resume_body(b, lines)
    if not b.body:
        raise JobPilotError("the Markdown has no content")
    name = title or next((ln.strip()[2:].strip() for ln in lines if ln.strip().startswith("# ")), "Resume")
    parts = {
        "[Content_Types].xml": _CT,
        "_rels/.rels": _RELS,
        "word/document.xml": b.document(),
        "word/_rels/document.xml.rels": b.rels(),
        "word/styles.xml": _styles(body_pt if kind != "letter" else 11, font),
        "word/numbering.xml": _NUMBERING,
        "word/settings.xml": _SETTINGS,
        "docProps/core.xml": _core(name, author or name),
        "docProps/app.xml": _APP,
    }
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dst.parent, prefix=".tmp-", suffix=".docx")
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            for n, text in parts.items():
                z.writestr(n, text.encode("utf-8"))
        os.replace(tmp, dst)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return dst
