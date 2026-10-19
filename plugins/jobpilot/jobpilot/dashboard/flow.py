"""Application flow: where every application went, drawn server-side as an SVG sankey (standard library only).

    compute(ws, lang="en") -> {"nodes", "links", "totals", "sig"}        over the ledger
    render_svg(f, theme="light", width=960, lang="en") -> str           a self-contained SVG for the dashboard
    check(f) -> [problems]                                               [] when the flow adds up

Every application you sent (it reached "applied" or later and was not voided) is one path

    Applied -> Assessment 1 -> Assessment 2 ... -> Interview 1 -> Interview 2 ... -> its end

where the end is Offer, Rejected or Closed · withdrawn. An application still in progress has no end: it stays at the
last stage it reached. One that reached no stage and is still open ends at "Recruiter replied" when someone wrote to
you personally about it, else at "No answer yet"; a receipt is not an answer. Rejected is one node for every
rejection, whatever the stage. A node's count is the number of applications
that reached it; for a stage, the count minus what flowed on is still in progress there (`open`).

Rounds, in ledger order: an assessment round is open until oa_done or any interview event, and another oa_invited
while it is open is the same round (a reminder is not a second assessment). An interview round is opened by
interview_invited, or by interview_scheduled when no round is open (the next round after one ended), and stays open
until interview_done; a rescheduled time never opens a round.

Colors follow the data's job: the stages are ordinal (one blue ramp, deeper = darker in light mode and brighter in
dark mode, validated with the dataviz ordinal checks; a recruiter's reply sits one step past Applied), the outcomes wear the status colors (an offer is good, a
rejection critical) and the two neutral ends are grays; every node is labeled with its name and count, so no color
carries meaning alone, and the dashboard offers the same numbers as a table. The SVG carries only stage names and
counts; the node lists in the JSON name the workspace's own applications (id, company, role).
"""
import hashlib
import json
from xml.sax.saxutils import escape, quoteattr

from .. import ledger

ENDS = ("offer", "replied", "none", "withdrawn", "reject")
GROUP = {"offer": 0, "replied": 1, "none": 2, "withdrawn": 3, "reject": 4}     # vertical order inside a column
LABELS = {
    "en": {"applied": "Applied", "oa": "Assessment", "iv": "Interview", "offer": "Offer", "replied": "Recruiter replied",
           "none": "No answer yet", "withdrawn": "Closed · withdrawn", "reject": "Rejected",
           "title": "Application flow", "apps": "Applications", "share": "Share of applied",
           "open": "Still in progress", "empty": "No applications sent yet"},
    "zh": {"applied": "已投递", "oa": "测评", "iv": "面试", "offer": "Offer", "replied": "招聘方回复", "none": "暂无回复",
           "withdrawn": "关闭·撤回", "reject": "被拒", "title": "投递流向", "apps": "投递数", "share": "占已投递",
           "open": "进行中", "empty": "还没有投出的申请"},
}
# Light: the stages deepen toward the offer (blue 250 -> 450 -> 650); dark: they brighten (600 -> 400 -> 200).
THEMES = {
    "light": {"bg": "#fcfcfb", "ink": "#0b0b0b", "ink2": "#52514e", "applied": "#86b6ef", "replied": "#5598e7",
              "oa": "#2a78d6", "iv": "#104281", "offer": "#0ca30c", "reject": "#d03b3b", "none": "#898781",
              "withdrawn": "#c3c2b7", "band": 0.30, "band_kind": {"none": 0.36, "withdrawn": 0.5}},
    "dark": {"bg": "#1a1a19", "ink": "#f0efec", "ink2": "#c3c2b7", "applied": "#184f95", "replied": "#256abf",
             "oa": "#3987e5", "iv": "#9ec5f4", "offer": "#0ca30c", "reject": "#d03b3b", "none": "#a8a69e",
             "withdrawn": "#6e6d68", "band": 0.38, "band_kind": {"applied": 0.5, "withdrawn": 0.5}},
}
FONT = 'system-ui, -apple-system, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif'
SETTLE = ("rejected", "withdrawn", "closed")


# ---------------------------------------------------------------- nodes

def is_stage(nid):
    return nid[:2] in ("oa", "iv") and nid[2:].isdigit()


def kind(nid):
    return nid[:2] if is_stage(nid) else nid


def rank(nid):
    """Path order: every link goes from a lower rank to a higher one."""
    if nid == "applied":
        return 0
    if is_stage(nid):
        return int(nid[2:]) + (0 if nid.startswith("oa") else 10000)
    return 20000 + GROUP[nid]


def group(nid):
    return GROUP.get(nid, 0)


def label(nid, lang="en", rounds=None):
    t = LABELS.get(lang) or LABELS["en"]
    if is_stage(nid):
        k = nid[:2]
        return t[k] if (rounds or {}).get(k, 2) <= 1 else "%s %s" % (t[k], nid[2:])
    return t[nid]


# ---------------------------------------------------------------- derivation

def path_of(events, st):
    """(stages, end) of one application from its events (ledger order) and its derived state, or None when it was
    never sent. stages: "oa1".., then "iv1"..; end: "offer" / "reject" / "withdrawn" / "replied" / "none", or None
    while the application is still in progress at its last stage."""
    if st is None or st["voided"] or not st["applied"]:
        return None
    oa = iv = 0
    oa_open = iv_open = offer = replied = False
    for e in events:
        ev = e.get("event")
        if ev == "recruiter_reply":
            replied = True
        elif ev == "oa_invited":
            if not oa_open:
                oa += 1
            oa_open = True
        elif ev == "oa_done":
            oa = max(oa, 1)
            oa_open = False
        elif ev == "interview_invited":
            oa_open = False
            if not iv_open:
                iv += 1
            iv_open = True
        elif ev == "interview_scheduled":
            oa_open = False
            if not iv_open:
                iv += 1          # the next round, scheduled without a separate invitation
                iv_open = True
        elif ev == "interview_done":
            oa_open = False
            iv = max(iv, 1)
            iv_open = False
        elif ev == "offer":
            offer = True
    if st["stage"] == "oa":
        oa = max(oa, 1)
    if st["stage"] == "interview":
        iv = max(iv, 1)
    stages = ["oa%d" % i for i in range(1, oa + 1)] + ["iv%d" % i for i in range(1, iv + 1)]
    if offer or st["stage"] == "offer":
        end = "offer"
    elif st["stage"] == "rejected":
        end = "reject"
    elif st["stage"] in ("withdrawn", "closed"):
        end = "withdrawn"
    else:
        end = None if stages else "replied" if replied else "none"
    return stages, end


def columns(ids, links):
    """Longest-path depth from the source per node id."""
    into = {}
    for a, b in links:
        into.setdefault(b, []).append(a)
    col = {}
    for nid in sorted(ids, key=rank):    # sources always rank lower than their targets
        col[nid] = max([col[a] + 1 for a in into.get(nid, [])] + [0])
    return col


def compute(ws, lang="en"):
    """Nodes, links and totals of the flow; zero-count nodes are left out."""
    by_app = {}
    for e in ledger.events(ws):
        by_app.setdefault(e.get("app_id"), []).append(e)
    states = ledger.states(ws)
    nodes, links, paths = {}, {}, []
    for app_id in sorted(by_app, key=str):
        st = states.get(app_id)
        derived = path_of(by_app[app_id], st)
        if derived is None:
            continue
        stages, end = derived
        path = ["applied"] + stages + ([end] if end else [])
        paths.append((stages, end))
        app = {"id": st["id"], "company": st["company"], "role": st["role"], "applied": st.get("applied_date"),
               "last": st.get("last_date")}
        for nid in path:
            v = nodes.setdefault(nid, {"id": nid, "kind": kind(nid), "count": 0, "apps": []})
            v["count"] += 1
            v["apps"].append(app)
        for a, b in zip(path, path[1:]):
            links[(a, b)] = links.get((a, b), 0) + 1
    rounds = {"oa": max([int(n[2:]) for n in nodes if n.startswith("oa")] + [0]),
              "iv": max([int(n[2:]) for n in nodes if n.startswith("iv")] + [0])}
    col = columns(nodes, links)
    out = {}
    for (a, _), val in links.items():
        out[a] = out.get(a, 0) + val
    ordered = sorted(nodes.values(), key=lambda v: (col[v["id"]], group(v["id"]), rank(v["id"])))
    for v in ordered:
        v["label"] = label(v["id"], lang, rounds)
        v["column"] = col[v["id"]]
        v["open"] = v["count"] - out.get(v["id"], 0) if is_stage(v["id"]) else None
        v["apps"].sort(key=lambda a: (a["last"] or a["applied"] or "", a["id"]), reverse=True)
    count = lambda nid: nodes[nid]["count"] if nid in nodes else 0  # noqa: E731
    applied = len(paths)
    responded = sum(1 for stages, end in paths if stages or end in ("reject", "offer", "replied"))
    totals = {"applied": applied, "responded": responded, "oa": count("oa1"), "interview": count("iv1"),
              "offer": count("offer"), "rejected": count("reject"), "replied": count("replied"),
              "no_answer": count("none"),
              "withdrawn": count("withdrawn"), "in_progress": sum(v["open"] or 0 for v in ordered),
              "reply_rate": round(responded / applied, 3) if applied else None}
    t = LABELS.get(lang) or LABELS["en"]
    totals["summary"] = "; ".join("%s %d" % (v["label"], v["count"]) for v in ordered) if applied else t["empty"]
    link_list = [{"source": a, "target": b, "value": val} for (a, b), val in
                 sorted(links.items(), key=lambda kv: (col[kv[0][0]], rank(kv[0][0]), rank(kv[0][1])))]
    f = {"nodes": ordered, "links": link_list, "totals": totals, "lang": lang, "rounds": rounds}
    f["sig"] = hashlib.sha1(json.dumps([[(v["id"], v["count"]) for v in ordered], link_list, lang],
                                       sort_keys=True).encode()).hexdigest()[:12]
    return f


def check(f):
    """[] when the flow adds up: the source sends out every application, every other node's count is its inflow,
    end nodes send nothing on, a stage's outflow never exceeds its inflow (the rest is `open`), and
    No answer + Rejected + Closed + Offer + the open ones = Applied."""
    problems = []
    inflow, outflow = {}, {}
    for lk in f["links"]:
        outflow[lk["source"]] = outflow.get(lk["source"], 0) + lk["value"]
        inflow[lk["target"]] = inflow.get(lk["target"], 0) + lk["value"]
    for v in f["nodes"]:
        nid, i, o = v["id"], inflow.get(v["id"], 0), outflow.get(v["id"], 0)
        if nid == "applied":
            if o != v["count"] or i:
                problems.append("applied: in %d out %d count %d" % (i, o, v["count"]))
        elif i != v["count"]:
            problems.append("%s: in %d count %d" % (nid, i, v["count"]))
        elif is_stage(nid) and (o > i or v["open"] != i - o):
            problems.append("%s: in %d out %d open %s" % (nid, i, o, v["open"]))
        elif not is_stage(nid) and o:
            problems.append("%s: an end with outflow %d" % (nid, o))
    ends = sum(v["count"] for v in f["nodes"] if v["id"] in ENDS) + sum(v["open"] or 0 for v in f["nodes"])
    if ends != f["totals"]["applied"]:
        problems.append("ends + open %d != applied %d" % (ends, f["totals"]["applied"]))
    return problems


# ---------------------------------------------------------------- layout

def text_width(s, size, bold=False):
    """A conservative width estimate of `s` in px (CJK full width; Latin by character class)."""
    w = 0.0
    for ch in s:
        o = ord(ch)
        if o >= 0x2E80:
            w += 1.0
        elif ch.isdigit():
            w += 0.6
        elif ch.isupper():
            w += 0.68
        elif ch.islower():
            w += 0.56
        elif ch == " ":
            w += 0.3
        else:
            w += 0.45
    return w * size * (1.07 if bold else 1.0)


def _params(width):
    narrow = width < 640
    return {"nw": 12 if narrow else 14, "pad": 12, "top": 14, "bottom": 14, "minh": 8.0, "offerh": 12.0,
            "line": 20.0, "fs": 12.5, "cfs": 13.5, "hsrc": 200.0 if narrow else 280.0, "lgap": 6.0, "edge": 8.0,
            "slack": 16.0, "pill": 3.0, "above": 22.0, "mingap": 44.0 if narrow else 64.0, "maxgap": 220.0}


def _label_box(v, p, pos):
    name, count = v["label"], str(v["count"])
    if name[-1:].isdigit():
        name += " ·"            # "Interview 1 · 5", never "Interview 1 5"
    w = text_width(name + " ", p["fs"]) + text_width(count, p["cfs"], True)
    return {"pos": pos, "w": w, "hgt": p["line"] - 3 if pos == "above" else p["line"], "name": name, "count": count}


def layout(f, width=960):
    """Geometry for render_svg. Columns are the longest-path depth from Applied; heights are proportional to counts
    (with a minimum); a link that spans columns runs through a slot in every column it crosses, so it never passes
    behind a node. Inside a column: stages and Offer on top, then No answer yet, Closed, Rejected, each ordered by the
    weighted position of its sources (fewer crossings). Labels: Applied on its left, a node with outgoing bands above
    it, No answer yet short of the last column on its left, every other node on its right; slots (node and label)
    never overlap. Gaps fit the labels beside them and take at most `maxgap` of spare width, so a small history keeps
    its natural width (the returned width can be narrower than the requested one)."""
    width = int(max(320, min(2000, width)))
    p = _params(width)
    n_applied = f["totals"]["applied"]
    if not n_applied:
        return {"empty": True, "width": width, "height": 96, "p": p, "nodes": {}, "bands": [], "ncol": 0}
    nodes = {v["id"]: dict(v) for v in f["nodes"] if v["count"] > 0}
    links = [lk for lk in f["links"] if lk["value"] > 0 and lk["source"] in nodes and lk["target"] in nodes]
    col = columns(nodes, [(lk["source"], lk["target"]) for lk in links])
    ncol = max(col.values()) + 1
    has_out = {lk["source"] for lk in links}
    k = max(p["hsrc"], min(float(n_applied), 480.0)) / n_applied
    for nid, v in nodes.items():
        v.update(col=col[nid], key=nid, h=max(p["offerh"] if nid == "offer" else p["minh"], k * v["count"]))
        left = nid == "applied" or (nid == "none" and col[nid] < ncol - 1)
        v["lab"] = _label_box(v, p, "left" if left else "above" if nid in has_out else "right")
    items, bands, segs_in, segs_out = dict(nodes), [], {}, {}
    for i, lk in enumerate(links):
        s, t, val = lk["source"], lk["target"], lk["value"]
        chain = [s]
        for c in range(col[s] + 1, col[t]):
            did = "dummy:%d:%d" % (i, c)
            items[did] = {"id": did, "dummy": True, "key": t, "col": c, "h": k * val, "count": val}
            chain.append(did)
        chain.append(t)
        b = {"source": s, "target": t, "value": val, "chain": chain, "w": k * val}
        bands.append(b)
        for a, z in zip(chain, chain[1:]):
            seg = {"band": b, "a": a, "z": z}
            segs_out.setdefault(a, []).append(seg)
            segs_in.setdefault(z, []).append(seg)
    cols = [[] for _ in range(ncol)]
    for it in items.values():
        cols[it["col"]].append(it)

    def extent(it):
        """(up, down): how far the item's slot (node and label) reaches above and below its center."""
        half = it["h"] / 2
        if it.get("dummy"):
            return half, half
        if it["lab"]["pos"] == "above":
            return half + p["above"], half
        s = max(it["h"], it["lab"]["hgt"]) / 2
        return s, s

    def stack_out(src):
        """Out-slots from the top of the node, in the order of the targets (what is left below is still open)."""
        y = src["y"]
        for s in sorted(segs_out.get(src["id"], []), key=lambda s: items[s["z"]]["order"]):
            s["y0"] = y
            y += s["band"]["w"]

    for c, its in enumerate(cols):
        if c == 0:
            for it in its:
                it["order"], it["cy"] = 0, p["top"] + extent(it)[0]
                it["y"] = it["cy"] - it["h"] / 2
            continue
        for it in its:
            src = segs_in.get(it["id"], [])
            wsum = sum(s["band"]["value"] for s in src) or 1
            it["bary"] = sum(items[s["a"]]["cy"] * s["band"]["value"] for s in src) / wsum
        its.sort(key=lambda it: (group(it["key"]), round(it["bary"], 3), rank(it["key"])))
        for i, it in enumerate(its):
            it["order"] = i
        for prev in cols[c - 1]:
            stack_out(prev)
        lo = p["top"]
        for it in its:
            src = segs_in.get(it["id"], [])
            wsum = sum(s["band"]["value"] for s in src) or 1
            want = sum((s["y0"] + s["band"]["w"] / 2) * s["band"]["value"] for s in src) / wsum
            up, down = extent(it)
            it["cy"] = max(want, lo + up)
            it["y"] = it["cy"] - it["h"] / 2
            lo = it["cy"] + down + p["pad"]
    for it in items.values():   # in-slots, ordered by where the band leaves; centered on a node raised to its minimum
        segs = sorted(segs_in.get(it["id"], []), key=lambda s: s["y0"])
        y = it["y"] + max(0.0, (it["h"] - sum(s["band"]["w"] for s in segs)) / 2)
        for s in segs:
            s["y1"] = y
            y += s["band"]["w"]
    nw, pill = p["nw"], p["pill"]
    ml = p["edge"] + pill + nodes["applied"]["lab"]["w"] + p["lgap"]

    def need(it):
        lab = it["lab"]
        if lab["pos"] == "above":
            return lab["w"] - nw + pill + p["slack"]
        return lab["w"] + p["lgap"] + pill + p["slack"] if lab["pos"] == "right" else 0   # left: in the gap before

    def need_left(it):
        return it["lab"]["w"] + p["lgap"] + pill + p["slack"] if it["lab"]["pos"] == "left" else 0

    gap_need = [max(p["mingap"], max([need(it) for it in cols[c] if c and not it.get("dummy")] + [0])
                    + max([need_left(it) for it in cols[c + 1] if not it.get("dummy")] + [0])) for c in range(ncol - 1)]
    mr = max([it["lab"]["w"] for it in cols[-1] if not it.get("dummy")] + [0]) + p["lgap"] + pill + p["edge"]
    spare = width - (ml + ncol * nw + sum(gap_need) + mr)
    gaps = [max(g, min(g + spare / len(gap_need), p["maxgap"])) for g in gap_need] if spare > 0 and gap_need \
        else list(gap_need)
    xs = [ml]
    for g in gaps:
        xs.append(xs[-1] + nw + g)
    height = p["top"]
    for it in items.values():
        it["x"] = xs[it["col"]]
        up, down = extent(it)
        height = max(height, it["cy"] + down)
        if it.get("dummy"):
            continue
        lab = it["lab"]
        if lab["pos"] == "above":
            lab["x"], lab["y"], lab["anchor"] = it["x"], it["y"] - p["above"] + pill, "start"
            lab["base"] = lab["y"] + 13.0
        elif lab["pos"] == "left":
            lab["x"], lab["y"], lab["anchor"] = it["x"] - p["lgap"] - lab["w"], it["cy"] - lab["hgt"] / 2, "end"
            lab["base"] = it["cy"] + 4.5
        else:
            lab["x"], lab["y"], lab["anchor"] = it["x"] + nw + p["lgap"], it["cy"] - lab["hgt"] / 2, "start"
            lab["base"] = it["cy"] + 4.5
        lab["anchor_x"] = lab["x"] + lab["w"] if lab["anchor"] == "end" else lab["x"]
        lab["pill"] = (lab["x"] - pill, lab["y"] - 2, lab["w"] + 2 * pill, lab["hgt"] + 4)
        # a pill under every label so a band behind cannot cut the text, except No answer yet's left label when its
        # own band is tall enough to hold it (nothing else passes there)
        lab["bare"] = lab["pos"] == "left" and it["id"] == "none" and it["h"] >= lab["hgt"] + 4
    for b in bands:
        segs = [next(s for s in segs_out[a] if s["band"] is b) for a in b["chain"][:-1]]
        b["path"] = _band_path(segs, items, nw, b["w"])
    return {"empty": False, "width": round(xs[-1] + nw + mr, 1), "height": round(height + p["bottom"], 1), "p": p,
            "ncol": ncol, "nodes": {nid: items[nid] for nid in nodes}, "bands": bands, "xs": xs, "gaps": gaps}


def _f(x):
    return ("%.1f" % x).rstrip("0").rstrip(".")


def _band_path(segs, items, nw, w):
    """Closed path: the top edge left to right through every segment (cubics with horizontal tangents), the bottom
    edge back."""
    top, bot = [], []
    for j, s in enumerate(segs):
        x0, x1 = items[s["a"]]["x"] + nw, items[s["z"]]["x"]
        xm, y0, y1 = (x0 + x1) / 2, s["y0"], s["y1"]
        top.append("%s%s,%s" % ("L" if j else "M", _f(x0), _f(y0)))
        top.append("C%s,%s %s,%s %s,%s" % (_f(xm), _f(y0), _f(xm), _f(y1), _f(x1), _f(y1)))
        bot.insert(0, "L%s,%s C%s,%s %s,%s %s,%s" % (_f(x1), _f(y1 + w), _f(xm), _f(y1 + w), _f(xm), _f(y0 + w),
                                                     _f(x0), _f(y0 + w)))
    return " ".join(top + bot + ["Z"])


# ---------------------------------------------------------------- SVG

def share(n, total):
    if not total:
        return "–"
    pc = 100.0 * n / total
    return ("%.1f%%" % pc) if 0 < pc < 10 else ("%d%%" % round(pc))


def _tip(title, rows):
    return json.dumps({"title": title, "rows": [[k, v] for k, v in rows]}, ensure_ascii=False, separators=(",", ":"))


def render_svg(f, theme="light", width=960, lang=None):
    """The flow as one SVG in the dashboard's palette (light or dark): bands, node bars, labels on pills; every node
    and band carries a data-tip for the dashboard's tooltip, and nodes are focusable with an aria-label."""
    lang = lang or f.get("lang") or "en"
    t = LABELS.get(lang) or LABELS["en"]
    pal = THEMES.get(theme) or THEMES["light"]
    g = layout(f, width)
    tot = f["totals"]
    W, H = g["width"], g["height"]
    out = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %s %s" width="%s" height="%s" role="group" '
           'aria-labelledby="jp-flow-t jp-flow-d" font-family=%s data-sig=%s data-width="%s" data-flow-theme=%s>'
           % (_f(W), _f(H), _f(W), _f(H), quoteattr(FONT), quoteattr(f.get("sig") or ""), _f(W), quoteattr(theme))]
    out.append('<title id="jp-flow-t">%s</title><desc id="jp-flow-d">%s</desc>' % (escape(t["title"]),
                                                                                 escape(tot.get("summary") or "")))
    out.append('<rect x="0" y="0" width="%s" height="%s" fill="%s"/>' % (_f(W), _f(H), pal["bg"]))
    if g["empty"]:
        out.append('<text x="%s" y="%s" text-anchor="middle" font-size="13" fill="%s">%s</text></svg>'
                   % (_f(W / 2), _f(H / 2 + 5), pal["ink2"], escape(t["empty"])))
        return "".join(out)
    nodes, p, n = g["nodes"], g["p"], tot["applied"]
    out.append('<g data-layer="bands">')
    for b in g["bands"]:
        src, tgt = nodes[b["source"]], nodes[b["target"]]
        tip = _tip("%s → %s" % (src["label"], tgt["label"]), [(t["apps"], str(b["value"])),
                                                            (t["share"], share(b["value"], n))])
        out.append('<path class="band" d="%s" fill="%s" fill-opacity="%s" data-link=%s data-tip=%s/>' % (
            b["path"], pal[tgt["kind"]], pal["band_kind"].get(tgt["kind"], pal["band"]),
            quoteattr(b["source"] + ">" + b["target"]), quoteattr(tip)))
    out.append("</g>")
    for nid, v in sorted(nodes.items(), key=lambda kv: (kv[1]["col"], kv[1]["order"])):
        rows = [(t["apps"], str(v["count"]))]
        if nid != "applied":
            rows.append((t["share"], share(v["count"], n)))
        if v.get("open"):
            rows.append((t["open"], str(v["open"])))
        aria = "%s: %d%s" % (v["label"], v["count"], "" if nid == "applied" else " (%s)" % share(v["count"], n))
        out.append('<g class="node" data-node=%s data-tip=%s tabindex="0" role="button" aria-label=%s>' % (
            quoteattr(nid), quoteattr(_tip(v["label"], rows)), quoteattr(aria)))
        out.append('<rect x="%s" y="%s" width="%s" height="%s" rx="3" fill="%s"/>' % (
            _f(v["x"]), _f(v["y"]), _f(p["nw"]), _f(v["h"]), pal[v["kind"]]))
        lab = v["lab"]
        if not lab["bare"]:
            px, py, pw, ph = lab["pill"]
            out.append('<rect x="%s" y="%s" width="%s" height="%s" rx="4" fill="%s" fill-opacity="0.94"/>'
                       % (_f(px), _f(py), _f(pw), _f(ph), pal["bg"]))
        out.append('<text x="%s" y="%s" font-size="%s" fill="%s"%s>%s <tspan font-size="%s" font-weight="700" '
                   'fill="%s">%s</tspan></text>' % (
                       _f(lab["anchor_x"]), _f(lab["base"]), _f(p["fs"]), pal["ink2"],
                       ' text-anchor="end"' if lab["anchor"] == "end" else "", escape(lab["name"]), _f(p["cfs"]),
                       pal["ink"], escape(lab["count"])))
        out.append("</g>")
    out.append("</svg>")
    return "".join(out)
