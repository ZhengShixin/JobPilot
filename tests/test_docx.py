"""The standard-library DOCX engine: reading paragraphs, applying edits without touching untouched bytes, writing new
resumes from Markdown. Offline; the LibreOffice checks are skipped when LibreOffice is missing."""
import tempfile
import unittest
import zipfile
from pathlib import Path

import support  # noqa: F401  (puts the plugin on sys.path)
from jobpilot import docx

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

# A document as Word writes it: namespaces listed in mc:Ignorable, w14:paraId attributes, a bullet typed as "•"+tab in
# its own run, a word split across runs with different formatting, a hyperlink, a table, a text box stored twice
# (mc:Choice + mc:Fallback), a tracked deletion and the body's section properties.
DOCUMENT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:wpc="http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:v="urn:schemas-microsoft-com:vml" xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" mc:Ignorable="w14 wp14"><w:body>
<w:p w14:paraId="1A2B3C01" w14:textId="77777777"><w:pPr><w:pStyle w:val="Title"/><w:jc w:val="center"/></w:pPr><w:r><w:rPr><w:b/><w:sz w:val="32"/></w:rPr><w:t>Jordan Rivera</w:t></w:r></w:p>
<w:p w14:paraId="1A2B3C02"><w:r><w:t xml:space="preserve">New York, NY · </w:t></w:r><w:hyperlink r:id="rId9"><w:r><w:rPr><w:rStyle w:val="Hyperlink"/></w:rPr><w:t>jordan@example.com</w:t></w:r></w:hyperlink></w:p>
<w:p w14:paraId="1A2B3C03"><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Experience</w:t></w:r></w:p>
<w:p w14:paraId="1A2B3C04"><w:pPr><w:ind w:left="360" w:hanging="360"/></w:pPr><w:r><w:t>•</w:t></w:r><w:r><w:tab/></w:r><w:r><w:rPr><w:b/></w:rPr><w:t>Ranking Models:</w:t></w:r><w:r><w:t xml:space="preserve"> Trained gradient-boo</w:t></w:r><w:r><w:rPr><w:i/></w:rPr><w:t>sted and transformer ranking models in PyTorch</w:t></w:r></w:p>
<w:p w14:paraId="1A2B3C05"><w:pPr><w:ind w:left="360" w:hanging="360"/></w:pPr><w:r><w:t>•</w:t></w:r><w:r><w:tab/></w:r><w:r><w:rPr><w:b/></w:rPr><w:t>Feature Pipeline:</w:t></w:r><w:r><w:t xml:space="preserve"> Built a feature pipeline in Python and SQL</w:t></w:r><w:del w:id="1" w:author="x" w:date="2026-01-01T00:00:00Z"><w:r><w:delText> (old words)</w:delText></w:r></w:del></w:p>
<w:p w14:paraId="1A2B3C06"><w:pPr><w:ind w:left="360" w:hanging="360"/></w:pPr><w:r><w:t>•</w:t></w:r><w:r><w:tab/></w:r><w:r><w:rPr><w:b/></w:rPr><w:t>Inference Latency:</w:t></w:r><w:r><w:t xml:space="preserve"> Cut inference latency by 38% &amp; costs by batching</w:t></w:r></w:p>
<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/></w:tblPr><w:tblGrid><w:gridCol w:w="4000"/><w:gridCol w:w="4000"/></w:tblGrid><w:tr><w:tc><w:tcPr><w:tcW w:w="4000" w:type="dxa"/></w:tcPr><w:p><w:r><w:t>Python, SQL</w:t></w:r></w:p><w:p><w:r><w:t>PyTorch, Docker</w:t></w:r></w:p></w:tc><w:tc><w:tcPr><w:tcW w:w="4000" w:type="dxa"/></w:tcPr><w:p><w:r><w:t>Statistics</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
<w:p w14:paraId="1A2B3C07"><w:r><mc:AlternateContent><mc:Choice Requires="wps"><w:drawing><wp:anchor><wp:docPr id="1" name="Box 1"/><a:graphic><a:graphicData uri="http://schemas.microsoft.com/office/word/2010/wordprocessingShape"><wps:wsp><wps:txbx><w:txbxContent><w:p><w:r><w:t>Open to relocation</w:t></w:r></w:p></w:txbxContent></wps:txbx></wps:wsp></a:graphicData></a:graphic></wp:anchor></w:drawing></mc:Choice><mc:Fallback><w:pict><v:shape><v:textbox><w:txbxContent><w:p><w:r><w:t>Open to relocation</w:t></w:r></w:p></w:txbxContent></v:textbox></v:shape></w:pict></mc:Fallback></mc:AlternateContent></w:r></w:p>
<w:p w14:paraId="1A2B3C08"><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Education</w:t></w:r></w:p>
<w:p w14:paraId="1A2B3C09"><w:pPr><w:tabs><w:tab w:val="right" w:pos="9000"/></w:tabs></w:pPr><w:r><w:rPr><w:b/></w:rPr><w:t>M.S. Computer Science</w:t></w:r><w:r><w:tab/><w:t>2023</w:t></w:r></w:p>
<w:sectPr><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="720" w:right="720" w:bottom="720" w:left="720" w:header="0" w:footer="0" w:gutter="0"/></w:sectPr></w:body></w:document>"""

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/></Types>"""
RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>"""
DOC_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId9" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="mailto:jordan@example.com" TargetMode="External"/><Relationship Id="rId10" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header1.xml"/></Relationships>"""
HEADER = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:t>Resume header line</w:t></w:r></w:p></w:hdr>"""

RESUME_MD = """# Jordan Rivera
New York, NY · jordan@example.com · [linkedin.com/in/jordan-rivera](https://www.linkedin.com/in/jordan-rivera)

## Experience
### Machine Learning Engineer — Brightline Analytics | Jun 2023 – Present
*New York, NY*
- **Ranking Models:** Trained gradient-boosted and transformer ranking models in PyTorch for marketplace search
- **Feature Pipeline:** Built a feature pipeline in Python and SQL that feeds daily model retraining
- **Inference Latency:** Cut inference latency by 38% by profiling and batching model calls
### Data Science Intern — Harbor Retail | Summer 2022
- Analyzed A/B tests and built demand forecasts in Python

## Education
### M.S. Computer Science — Northfield Institute of Technology | 2023
### B.S. Statistics — Lakeside State University | 2021

## Skills
**Languages:** Python, SQL
**ML:** PyTorch, scikit-learn, AWS SageMaker, Docker, Git
"""


def make_complex(path):
    """Write the hand-built document (stored members, the way some tools write them) to `path`."""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("_rels/.rels", RELS, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("word/document.xml", DOCUMENT, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("word/_rels/document.xml.rels", DOC_RELS, compress_type=zipfile.ZIP_STORED)
        z.writestr("word/header1.xml", HEADER, compress_type=zipfile.ZIP_STORED)
    return path


def doc_xml(path):
    with zipfile.ZipFile(path) as z:
        return z.read("word/document.xml").decode("utf-8")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="jobpilot-docx-"))
        self.src = make_complex(self.tmp / "complex.docx")
        self.dst = self.tmp / "out.docx"

    def texts(self, path):
        return [p["text"] for p in docx.paragraphs(path)]


class ReadTest(Base):
    def test_paragraphs_in_order(self):
        paras = docx.paragraphs(self.src)
        texts = [p["text"] for p in paras]
        self.assertEqual(texts[0], "Jordan Rivera")
        self.assertEqual(texts[1], "New York, NY · jordan@example.com")
        self.assertEqual(texts[3], "•\tRanking Models: Trained gradient-boosted and transformer ranking models in PyTorch")
        # the tracked deletion is not text
        self.assertEqual(texts[4], "•\tFeature Pipeline: Built a feature pipeline in Python and SQL")
        self.assertIn("Cut inference latency by 38% & costs by batching", texts[5])
        self.assertEqual(texts[-1], "M.S. Computer Science\t2023")  # the tab stop in pPr is not a tab character
        # the text box appears once (its mc:Fallback copy is hidden) and its host paragraph is flagged
        self.assertEqual(texts.count("Open to relocation"), 1)
        host = next(p for p in paras if p["has_nested"])
        self.assertEqual(host["text"], "")
        box = next(p for p in paras if p["text"] == "Open to relocation")
        self.assertTrue(box["in_textbox"])
        cell = next(p for p in paras if p["text"] == "Python, SQL")
        self.assertTrue(cell["in_table"])
        self.assertEqual(paras[0]["style"], "Title")
        self.assertEqual(paras[3]["label"], "Ranking Models:")
        self.assertEqual(paras[0]["label"], "")
        self.assertEqual([p["index"] for p in paras], list(range(len(paras))))

    def test_text_of_includes_header(self):
        text = docx.text_of(self.src)
        self.assertTrue(text.startswith("Resume header line"))
        self.assertIn("Ranking Models", text)

    def test_text_of_markdown_and_errors(self):
        md = self.tmp / "r.md"
        md.write_text("# Name\n")
        self.assertEqual(docx.text_of(md), "# Name\n")
        with self.assertRaises(docx.JobPilotError):
            docx.text_of(self.tmp / "missing.docx")
        bad = self.tmp / "bad.docx"
        bad.write_text("not a zip")
        with self.assertRaises(docx.JobPilotError):
            docx.paragraphs(bad)


class ReplaceTest(Base):
    def test_replace_across_runs_keeps_formatting(self):
        changes = docx.apply(self.src, self.dst, [
            {"op": "replace", "find": "gradient-boosted and transformer ranking models",
             "with": "gradient-boosted & transformer ranking models <v2>"}])
        self.assertEqual(changes[0]["op"], "replace")
        self.assertIn("gradient-boosted & transformer ranking models <v2> in PyTorch", self.texts(self.dst)[3])
        xml = doc_xml(self.dst)
        # only the part that differs is rewritten, in the run where that part starts (here the italic run)
        self.assertIn('<w:t xml:space="preserve"> Trained gradient-boo</w:t>', xml)
        self.assertIn('<w:rPr><w:i/></w:rPr><w:t xml:space="preserve">sted &amp; transformer ranking models &lt;v2&gt; '
                      'in PyTorch</w:t>', xml)
        self.assertIn('<w:rPr><w:b/></w:rPr><w:t>Ranking Models:</w:t>', xml)

    def test_replace_keeps_label_and_body_formatting(self):
        docx.apply(self.src, self.dst, [
            {"op": "replace", "find": "Feature Pipeline: Built a feature pipeline",
             "with": "Data Pipeline: Designed a feature pipeline"},
            {"op": "replace", "find": "Inference Latency: Cut inference latency",
             "with": "Serving Latency: Cut inference latency"}])
        xml = doc_xml(self.dst)
        self.assertIn('<w:rPr><w:b/></w:rPr><w:t xml:space="preserve">Data Pipeline:</w:t>', xml)
        self.assertIn('<w:r><w:t xml:space="preserve"> Designed a feature pipeline in Python and SQL</w:t>', xml)
        self.assertIn('<w:rPr><w:b/></w:rPr><w:t xml:space="preserve">Serving Latency:</w:t>', xml)
        self.assertIn('<w:t xml:space="preserve"> Cut inference latency by 38% &amp; costs by batching</w:t>', xml)

    def test_untouched_bytes_stay(self):
        before = doc_xml(self.src)
        docx.apply(self.src, self.dst, [{"op": "replace", "find": "38%", "with": "38 percent"}])
        after = doc_xml(self.dst)
        cut = before.index("38%")
        self.assertEqual(after[:cut], before[:cut])
        self.assertEqual(after[cut + len("38 percent"):], before[cut + len("38%"):])
        self.assertIn('mc:Ignorable="w14 wp14"', after)
        self.assertIn('w14:paraId="1A2B3C06"', after)

    def test_zip_members_order_and_compression(self):
        docx.apply(self.src, self.dst, [{"op": "replace", "find": "Statistics", "with": "Applied statistics"}])
        with zipfile.ZipFile(self.src) as a, zipfile.ZipFile(self.dst) as b:
            self.assertIsNone(b.testzip())
            self.assertEqual([i.filename for i in a.infolist()], [i.filename for i in b.infolist()])
            self.assertEqual([i.compress_type for i in a.infolist()], [i.compress_type for i in b.infolist()])
            for name in a.namelist():
                if name != "word/document.xml":
                    self.assertEqual(a.read(name), b.read(name), name)

    def test_text_box_edit_is_mirrored(self):
        docx.apply(self.src, self.dst, [{"op": "replace", "find": "Open to relocation", "with": "Open to relocation "
                                                                                            "within the US"}])
        xml = doc_xml(self.dst)
        self.assertEqual(xml.count("Open to relocation within the US"), 2)
        self.assertEqual(self.texts(self.dst).count("Open to relocation within the US"), 1)

    def test_not_found_and_not_unique(self):
        with self.assertRaises(docx.EditError) as cm:
            docx.apply(self.src, self.dst, [{"op": "replace", "find": "Kubernetes clusters", "with": "x"}])
        self.assertIn("edit 1 (replace)", str(cm.exception))
        with self.assertRaises(docx.EditError) as cm:
            docx.apply(self.src, self.dst, [{"op": "replace", "find": "Experience", "with": "Work"},
                                            {"op": "replace", "find": "•", "with": "-"}])
        self.assertIn("edit 2", str(cm.exception))
        self.assertIn("occurs", str(cm.exception))
        self.assertFalse(self.dst.exists())

    def test_hint_names_the_closest_paragraph(self):
        with self.assertRaises(docx.EditError) as cm:
            docx.apply(self.src, self.dst, [{"op": "replace", "find": "Built a feature pipline in Python", "with": "x"}])
        self.assertIn("closest paragraph", str(cm.exception))

    def test_typography_fold(self):
        with self.assertRaises(docx.EditError):  # "·" is not a dash: folding never invents a match
            docx.apply(self.src, self.dst, [{"op": "replace", "find": "New York, NY - ", "with": "Remote · "}])
        # a curly apostrophe in `find` still matches a straight one in the document
        src2 = self.tmp / "q.docx"
        docx.from_markdown("# A\n\n## S\n- It's done\n", src2)
        docx.apply(src2, self.dst, [{"op": "replace", "find": "It’s done", "with": "Done"}])
        self.assertIn("Done", self.texts(self.dst))

    def test_refuses_tabs_links_and_newlines(self):
        for op, why in (({"op": "replace", "find": "•\tRanking", "with": "x"}, "tab"),
                        ({"op": "replace", "find": "jordan@example.com", "with": "j@x.io"}, "hyperlink"),
                        ({"op": "replace", "find": "Statistics", "with": "a\nb"}, "line breaks")):
            with self.assertRaises(docx.EditError) as cm:
                docx.apply(self.src, self.dst, [op])
            self.assertIn(why, str(cm.exception))


class StructureTest(Base):
    def test_delete(self):
        docx.apply(self.src, self.dst, [{"op": "delete", "paragraph": "Feature Pipeline:"}])
        texts = self.texts(self.dst)
        self.assertFalse(any("Feature Pipeline" in t for t in texts))
        self.assertEqual(len(texts), len(self.texts(self.src)) - 1)

    def test_delete_refusals_and_last_cell_paragraph(self):
        with self.assertRaises(docx.EditError):
            docx.apply(self.src, self.dst, [{"op": "delete", "paragraph": "Open to"},
                                            {"op": "delete", "paragraph": "zzz"}])
        # the only paragraph of a table cell is emptied, not removed (a cell needs one)
        docx.apply(self.src, self.dst, [{"op": "delete", "paragraph": "Statistics"}])
        xml = doc_xml(self.dst)
        self.assertIn("<w:tc><w:tcPr><w:tcW w:w=\"4000\" w:type=\"dxa\"/></w:tcPr><w:p><w:r><w:t></w:t></w:r></w:p>"
                      "</w:tc>", xml)

    def test_move_before_and_after(self):
        docx.apply(self.src, self.dst, [{"op": "move", "paragraph": "Inference Latency:", "before": "Ranking Models:"}])
        t = self.texts(self.dst)
        self.assertLess(t.index(next(x for x in t if "Inference" in x)), t.index(next(x for x in t if "Ranking" in x)))
        docx.apply(self.src, self.dst, [{"op": "move", "paragraph": "Ranking Models:", "after": "Inference Latency:"}])
        t = self.texts(self.dst)
        self.assertEqual([x.split(":")[0].strip("•\t") for x in t[3:6]], ["Feature Pipeline", "Inference Latency",
                                                                           "Ranking Models"])
        self.assertEqual(sorted(t), sorted(self.texts(self.src)))

    def test_move_refuses_other_containers(self):
        with self.assertRaises(docx.EditError) as cm:
            docx.apply(self.src, self.dst, [{"op": "move", "paragraph": "Python, SQL", "before": "Ranking Models:"}])
        self.assertIn("same section", str(cm.exception))
        docx.apply(self.src, self.dst, [{"op": "move", "paragraph": "PyTorch, Docker", "before": "Python, SQL"}])
        t = self.texts(self.dst)
        self.assertLess(t.index("PyTorch, Docker"), t.index("Python, SQL"))

    def test_insert_after_with_label(self):
        docx.apply(self.src, self.dst, [{"op": "insert_after", "anchor": "Feature Pipeline:", "label": "Model Serving:",
                                         "text": "Served models behind batch inference endpoints"}])
        t = self.texts(self.dst)
        i = t.index("•\tModel Serving: Served models behind batch inference endpoints")
        self.assertIn("Feature Pipeline", t[i - 1])
        xml = doc_xml(self.dst)
        new = xml[xml.index("Model Serving:") - 400: xml.index("Served models") + 80]
        self.assertIn('<w:ind w:left="360" w:hanging="360"/>', new)
        self.assertIn('<w:rPr><w:b/></w:rPr><w:t xml:space="preserve">Model Serving: </w:t>', new)
        self.assertEqual(xml.count('w14:paraId="1A2B3C05"'), 1)  # the clone drops the paragraph id
        para = next(p for p in docx.paragraphs(self.dst) if "Model Serving" in p["text"])
        self.assertEqual(para["label"], "Model Serving:")

    def test_insert_after_plain_and_inline_bullet(self):
        src = self.tmp / "inline.docx"
        docx.from_markdown("# A\n\n## S\nSkills line one\n", src)
        xml = doc_xml(src).replace("Skills line one", "• Skills line one")
        with zipfile.ZipFile(src) as z:
            members = {n: z.read(n) for n in z.namelist()}
        members["word/document.xml"] = xml.encode()
        with zipfile.ZipFile(src, "w") as z:
            for n, data in members.items():
                z.writestr(n, data)
        docx.apply(src, self.dst, [{"op": "insert_after", "anchor": "Skills line one", "text": "Skills line two"}])
        self.assertIn("• Skills line two", self.texts(self.dst))

    def test_bad_ops(self):
        for ops in ([{"op": "rewrite"}], [{"op": "move", "paragraph": "Ranking", "before": "Feature", "after": "Inf"}],
                    [{"op": "insert_after", "anchor": "Ranking"}], [{"op": "delete"}]):
            with self.assertRaises(docx.EditError):
                docx.apply(self.src, self.dst, ops)

    def test_sequence_sees_earlier_edits(self):
        changes = docx.apply(self.src, self.dst, [
            {"op": "replace", "find": "Built a feature pipeline", "with": "Designed a feature pipeline"},
            {"op": "replace", "find": "Designed a feature pipeline in Python", "with": "Designed a feature pipeline in "
                                                                                        "Python 3"}])
        self.assertEqual([c["edit"] for c in changes], [1, 2])
        self.assertIn("Designed a feature pipeline in Python 3 and SQL", self.texts(self.dst)[4])


class MarkdownTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="jobpilot-md-"))

    def test_resume_structure(self):
        out = docx.from_markdown(RESUME_MD, self.tmp / "r.docx")
        with zipfile.ZipFile(out) as z:
            self.assertIsNone(z.testzip())
            names = z.namelist()
            for part in ("[Content_Types].xml", "word/document.xml", "word/styles.xml", "word/numbering.xml",
                         "docProps/core.xml", "word/_rels/document.xml.rels"):
                self.assertIn(part, names)
            rels = z.read("word/_rels/document.xml.rels").decode()
            self.assertIn('Target="https://www.linkedin.com/in/jordan-rivera" TargetMode="External"', rels)
        paras = docx.paragraphs(out)
        styles = {p["text"]: p["style"] for p in paras}
        self.assertEqual(styles["Jordan Rivera"], "Title")
        self.assertEqual(styles["Experience"], "Heading1")
        self.assertEqual(styles["Machine Learning Engineer — Brightline Analytics\tJun 2023 – Present"], "Entry")
        self.assertEqual(styles["New York, NY"], "EntryDetail")
        bullet = next(p for p in paras if p["text"].startswith("Ranking Models:"))
        self.assertEqual((bullet["style"], bullet["label"]), ("ListBullet", "Ranking Models:"))
        self.assertIn("Languages: Python, SQL", styles)
        self.assertIn("ML: PyTorch, scikit-learn, AWS SageMaker, Docker, Git", styles)

    def test_letter(self):
        out = docx.from_markdown("# Jordan Rivera\njordan@example.com\n\nDear Hiring Team,\n\nFirst paragraph.\n\n"
                                 "Sincerely,\nJordan Rivera\n", self.tmp / "l.docx", kind="letter",
                                 date_line="October 19, 2026")
        texts = [p["text"] for p in docx.paragraphs(out)]
        self.assertEqual(texts[:3], ["Jordan Rivera", "jordan@example.com", "October 19, 2026"])
        self.assertIn("Sincerely,\nJordan Rivera", texts)

    def test_empty_markdown(self):
        with self.assertRaises(docx.JobPilotError):
            docx.from_markdown("\n\n", self.tmp / "e.docx")


@unittest.skipUnless(docx.soffice(), "LibreOffice is not installed")
class LibreOfficeTest(unittest.TestCase):
    def test_generated_resume_renders_on_one_page(self):
        tmp = Path(tempfile.mkdtemp(prefix="jobpilot-lo-"))
        out = docx.from_markdown(RESUME_MD, tmp / "r.docx")
        pdf = docx.to_pdf(out)
        self.assertTrue(pdf.exists())
        self.assertEqual(docx.page_count(pdf), 1)

    def test_edited_word_file_still_opens(self):
        tmp = Path(tempfile.mkdtemp(prefix="jobpilot-lo-"))
        src = make_complex(tmp / "c.docx")
        dst = tmp / "edited.docx"
        docx.apply(src, dst, [{"op": "replace", "find": "38%", "with": "38 percent"},
                              {"op": "insert_after", "anchor": "Ranking Models:", "label": "Evaluation:",
                               "text": "Ran offline evaluation of every model"},
                              {"op": "move", "paragraph": "Inference Latency:", "before": "Ranking Models:"},
                              {"op": "delete", "paragraph": "Feature Pipeline:"}])
        pdf = docx.to_pdf(dst)
        self.assertGreaterEqual(docx.page_count(pdf), 1)
        with zipfile.ZipFile(dst) as z:
            self.assertIsNone(z.testzip())


if __name__ == "__main__":
    unittest.main()
