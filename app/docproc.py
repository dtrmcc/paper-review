"""Manuscript import and local text processing (PyMuPDF, LibreOffice, GROBID — all on this laptop)."""
import os
import re
import shutil
import subprocess
import tempfile

import fitz  # PyMuPDF
import httpx
from lxml import etree

import db

GROBID_URL = os.environ.get("GROBID_URL", "http://grobid:8070")
TEI = {"t": "http://www.tei-c.org/ns/1.0"}


def doc_dir(project_id, doc_id):
    d = os.path.join(db.FILES_DIR, f"p{project_id}", f"d{doc_id}")
    os.makedirs(d, exist_ok=True)
    return d


def safe_name(name):
    name = os.path.basename(name or "manuscript")
    return re.sub(r"[^\w.\- ()\[\]]+", "_", name)[:150] or "manuscript"


def to_pdf(src, outdir):
    """Word / ODT / RTF -> PDF with LibreOffice headless (local)."""
    with tempfile.TemporaryDirectory() as prof:
        subprocess.run(["soffice", f"-env:UserInstallation=file://{prof}", "--headless",
                        "--convert-to", "pdf", "--outdir", outdir, src],
                       check=True, timeout=300, capture_output=True)
    pdf = os.path.join(outdir, os.path.splitext(os.path.basename(src))[0] + ".pdf")
    if not os.path.exists(pdf):
        raise RuntimeError("LibreOffice conversion failed")
    return pdf


def import_file(project_id, doc_id, upload_path, filename):
    d = doc_dir(project_id, doc_id)
    orig = os.path.join(d, "original_" + safe_name(filename))
    shutil.move(upload_path, orig)
    ext = os.path.splitext(filename.lower())[1]
    if ext == ".pdf":
        pdf = orig
    elif ext in (".doc", ".docx", ".odt", ".rtf", ".txt"):
        pdf = to_pdf(orig, d)
        os.rename(pdf, os.path.join(d, "converted.pdf"))
        pdf = os.path.join(d, "converted.pdf")
    else:
        raise ValueError("只支持 PDF / Word (.doc .docx) / ODT / RTF / TXT")
    pages, text, meta = extract(pdf)
    tp = os.path.join(d, "text.txt")
    with open(tp, "w", encoding="utf-8") as f:
        f.write(text)
    db.ex("UPDATE documents SET orig_path=?, pdf_path=?, pages=?, text_path=?, meta=?, grobid_status='pending'"
          " WHERE id=?", (orig, pdf, pages, tp, db.jdump(meta), doc_id))


def extract(pdf):
    doc = fitz.open(pdf)
    parts = []
    for i, page in enumerate(doc):
        parts.append(f"\n\n[[page {i + 1}]]\n" + page.get_text("text"))
    meta = {k: v for k, v in (doc.metadata or {}).items() if v}
    meta["file_size"] = os.path.getsize(pdf)
    n = doc.page_count
    doc.close()
    return n, "".join(parts), meta


def doc_text(doc, strip_markers=False):
    if not doc or not doc.get("text_path") or not os.path.exists(doc["text_path"]):
        return ""
    t = open(doc["text_path"], encoding="utf-8").read()
    return re.sub(r"\n*\[\[page \d+\]\]\n", "\n", t) if strip_markers else t


# ---------------------------------------------------------------- GROBID

def run_grobid(doc):
    with open(doc["pdf_path"], "rb") as f:
        r = httpx.post(f"{GROBID_URL}/api/processFulltextDocument",
                       files={"input": f},
                       data={"consolidateHeader": "0", "consolidateCitations": "0",
                             "includeRawCitations": "1", "segmentSentences": "0"},
                       timeout=600)
    r.raise_for_status()
    tp = os.path.join(os.path.dirname(doc["pdf_path"]), "grobid.tei.xml")
    with open(tp, "wb") as f:
        f.write(r.content)
    db.ex("UPDATE documents SET tei_path=?, grobid_status='done' WHERE id=?", (tp, doc["id"]))
    return tp


def tei_root(doc):
    if not doc.get("tei_path") or not os.path.exists(doc["tei_path"]):
        return None
    return etree.parse(doc["tei_path"]).getroot()


def _txt(el):
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip() if el is not None else ""


def parse_refs(root):
    out = []
    for i, b in enumerate(root.iterfind(".//t:back//t:listBibl/t:biblStruct", TEI)):
        xml_id = b.get("{http://www.w3.org/XML/1998/namespace}id")
        title = _txt(b.find("t:analytic/t:title", TEI)) or _txt(b.find("t:monogr/t:title", TEI))
        venue = _txt(b.find("t:monogr/t:title", TEI)) if b.find("t:analytic/t:title", TEI) is not None else ""
        authors = []
        for a in b.iterfind(".//t:analytic/t:author/t:persName", TEI) or []:
            authors.append(" ".join(filter(None, [_txt(a.find("t:forename", TEI)), _txt(a.find("t:surname", TEI))])))
        if not authors:
            for a in b.iterfind(".//t:monogr/t:author/t:persName", TEI):
                authors.append(" ".join(filter(None, [_txt(a.find("t:forename", TEI)), _txt(a.find("t:surname", TEI))])))
        date = b.find(".//t:imprint/t:date", TEI)
        year = ""
        if date is not None:
            m = re.search(r"\d{4}", date.get("when", "") + " " + _txt(date))
            year = m.group(0) if m else ""
        doi = _txt(b.find(".//t:idno[@type='DOI']", TEI))
        raw = _txt(b.find("t:note[@type='raw_reference']", TEI))
        out.append({"idx": i + 1, "xml_id": xml_id, "title": title, "venue": venue,
                    "authors": authors, "year": year, "doi": doi.lower(), "raw": raw or title})
    return out


def parse_structure(root):
    """Sections with word counts, abstract, in-text citation targets, header authors, figures."""
    abstract = _txt(root.find(".//t:profileDesc/t:abstract", TEI))
    sections = []
    for div in root.iterfind(".//t:text/t:body/t:div", TEI):
        head = _txt(div.find("t:head", TEI)) or "(untitled)"
        n = div.find("t:head", TEI)
        num = n.get("n", "") if n is not None else ""
        words = sum(len(_txt(p).split()) for p in div.iterfind("t:p", TEI))
        sections.append({"head": (num + " " + head).strip(), "words": words})
    cites = []
    for r in root.iterfind(".//t:text/t:body//t:ref[@type='bibr']", TEI):
        cites.append({"target": (r.get("target") or "").lstrip("#"), "text": _txt(r)})
    authors = []
    for a in root.iterfind(".//t:teiHeader//t:sourceDesc//t:analytic/t:author/t:persName", TEI):
        authors.append(" ".join(filter(None, [_txt(a.find("t:forename", TEI)), _txt(a.find("t:surname", TEI))])))
    figures = []
    for fg in root.iterfind(".//t:text//t:figure", TEI):
        figures.append({"type": "table" if fg.get("type") == "table" else "figure",
                        "label": _txt(fg.find("t:label", TEI)) or _txt(fg.find("t:head", TEI)),
                        "caption": _txt(fg.find("t:figDesc", TEI))[:400]})
    title = _txt(root.find(".//t:teiHeader//t:titleStmt/t:title", TEI))
    return {"title": title, "abstract": abstract, "sections": sections, "cites": cites,
            "authors": authors, "figures": figures}


def store_refs(doc_id, refs):
    db.ex("DELETE FROM refs WHERE document_id=?", (doc_id,))
    for r in refs:
        db.ex("INSERT INTO refs(document_id,idx,xml_id,raw,title,authors,year,venue,doi,status)"
              " VALUES(?,?,?,?,?,?,?,?,?, 'pending')",
              (doc_id, r["idx"], r["xml_id"], r["raw"], r["title"], db.jdump(r["authors"]), r["year"],
               r["venue"], r["doi"]))
