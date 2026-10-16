"""Toolbox. Every tool runs only when the user clicks it. All run locally, except
journal_scope (fetches the public journal website)."""
import difflib
import html
import os
import re
from collections import Counter
from datetime import date

import fitz
import httpx
import textstat
from bs4 import BeautifulSoup
from scipy import stats as st

import db
import docproc
from refcheck import norm, surname

CAPTION_RE = re.compile(r"^\s*(Fig\.?|Figure|FIGURE|Table|TABLE)\s*(S?\d+[A-Za-z]?)[.:|\s]", re.M)


# ------------------------------------------------------------------ 6. structure & statistics
def stats(doc):
    text = docproc.doc_text(doc, strip_markers=True)
    meta = db.jload(doc["meta"], {})
    words = re.findall(r"\b[\w'-]+\b", text)
    caps = CAPTION_RE.findall(text)
    figs = sorted({n for k, n in caps if k.lower().startswith("fig")}, key=lambda x: (len(x), x))
    tabs = sorted({n for k, n in caps if k.lower().startswith("tab")}, key=lambda x: (len(x), x))
    out = {
        "pages": doc["pages"], "words": len(words), "characters": len(text),
        "file_size_kb": round(meta.get("file_size", 0) / 1024),
        "figures": len(figs), "tables": len(tabs),
        "reading_minutes": round(len(words) / 230),
        "readability": {"flesch_reading_ease": round(textstat.flesch_reading_ease(text[:200000]), 1),
                        "flesch_kincaid_grade": round(textstat.flesch_kincaid_grade(text[:200000]), 1),
                        "avg_sentence_length": round(textstat.avg_sentence_length(text[:200000]), 1)},
        "pdf_metadata": {k: meta[k] for k in ("title", "author", "creator", "producer", "creationDate",
                                               "modDate", "subject", "keywords") if meta.get(k)},
    }
    root = docproc.tei_root(doc)
    if root is not None:
        s = docproc.parse_structure(root)
        out["title"] = s["title"]
        out["abstract_words"] = len(s["abstract"].split())
        out["sections"] = s["sections"]
        out["references"] = db.q1("SELECT COUNT(*) n FROM refs WHERE document_id=?", (doc["id"],))["n"]
        out["header_authors"] = s["authors"]
    warn = []
    if out["pdf_metadata"].get("author"):
        warn.append(f"PDF 元数据中含作者信息：“{out['pdf_metadata']['author']}”（双盲审稿时需注意）")
    if out.get("header_authors"):
        warn.append("稿件首页解析出作者姓名：" + ", ".join(out["header_authors"][:6]))
    out["warnings"] = warn
    return out


# ------------------------------------------------------------------ 1. citation consistency
def citation_consistency(doc):
    root = docproc.tei_root(doc)
    if root is None:
        return {"error": "需要先完成文献解析（GROBID）"}
    s = docproc.parse_structure(root)
    refs = db.q("SELECT idx, xml_id, title, raw, authors, year FROM refs WHERE document_id=? ORDER BY idx",
                (doc["id"],))
    cited = Counter(c["target"] for c in s["cites"] if c["target"])
    unlinked = [c["text"] for c in s["cites"] if not c["target"]]
    never = [r for r in refs if r["xml_id"] not in cited]
    # author-year style: look for surname + year in body text as a second chance
    body = docproc.doc_text(doc, strip_markers=True)
    cut = re.search(r"\n\s*(References|REFERENCES|Bibliography|Literature Cited)\s*\n", body)
    body_main = body[:cut.start()] if cut else body
    still_never = []
    for r in never:
        au = db.jload(r["authors"], [])
        sn = surname(au[0]) if au else ""
        hit = False
        if sn and r["year"]:
            hit = re.search(re.escape(sn) + r".{0,40}?" + r["year"], norm(body_main).replace("  ", " ")) is not None
        if not hit:
            still_never.append({"idx": r["idx"], "ref": (r["title"] or r["raw"])[:200]})
    dup = Counter(norm(r["title"]) for r in refs if r["title"])
    dups = [t for t, n in dup.items() if n > 1]
    unl = Counter(unlinked)
    return {"in_text_citations": len(s["cites"]), "references": len(refs),
            "cited_references": len(cited),
            "never_cited": still_never,
            "unresolved_in_text": [{"text": t, "count": n} for t, n in unl.most_common(60)],
            "duplicate_references": dups,
            "most_cited": [{"idx": next((r["idx"] for r in refs if r["xml_id"] == k), None), "count": n}
                           for k, n in cited.most_common(8)],
            "note": "“正文引用无法对应”可能是参考文献表缺失条目，也可能是解析误差，请人工核对。"}


# ------------------------------------------------------------------ 2. retractions & corrections
def retractions(doc):
    rows = db.q("SELECT idx, title, raw, status, result FROM refs WHERE document_id=? ORDER BY idx", (doc["id"],))
    if not rows:
        return {"error": "还没有参考文献数据"}
    pending = sum(1 for r in rows if r["status"] == "pending")
    flagged = []
    for r in rows:
        res = db.jload(r["result"], {})
        notes = res.get("notices") or []
        sc = (res.get("scite") or {}).get("notices") or []
        if r["status"] == "retracted" or notes or sc:
            flagged.append({"idx": r["idx"], "ref": (r["title"] or r["raw"])[:200], "status": r["status"],
                            "notices": notes, "scite_notices": sc})
    return {"checked": len(rows) - pending, "pending": pending, "flagged": flagged}


# ------------------------------------------------------------------ 3. self-citation & age
def citation_profile(doc, project):
    rows = db.q("SELECT idx, title, authors, year FROM refs WHERE document_id=? ORDER BY idx", (doc["id"],))
    if not rows:
        return {"error": "还没有参考文献数据"}
    names = [n.strip() for n in (project.get("author_names") or "").split(",") if n.strip()]
    if not names:
        root = docproc.tei_root(doc)
        if root is not None:
            names = docproc.parse_structure(root)["authors"]
    sn = {surname(n) for n in names if surname(n)}
    self_cites = []
    for r in rows:
        au = {surname(a) for a in db.jload(r["authors"], [])}
        hit = au & sn
        if hit:
            self_cites.append({"idx": r["idx"], "title": r["title"], "matched": sorted(hit)})
    years = [int(r["year"]) for r in rows if (r["year"] or "").isdigit()]
    this = date.today().year
    hist = Counter(years)
    return {"manuscript_authors_used": names,
            "self_citations": self_cites,
            "self_citation_rate": round(100 * len(self_cites) / len(rows), 1) if rows else 0,
            "years": sorted(hist.items()),
            "median_year": sorted(years)[len(years) // 2] if years else None,
            "pct_last_5y": round(100 * sum(y >= this - 5 for y in years) / len(years), 1) if years else None,
            "pct_older_10y": round(100 * sum(y < this - 10 for y in years) / len(years), 1) if years else None,
            "no_year": len(rows) - len(years),
            "note": "自引基于姓氏匹配，同姓作者会误报；双盲稿件请在任务设置中手动填写作者（如已知）。"}


# ------------------------------------------------------------------ 4. statistics check
NUM = r"(-?\d*\.?\d+)"
P_RE = r"p\s*([<>=≤≥])\s*(\.\d+|0\.\d+|1(?:\.0+)?)"
PATTERNS = [
    ("t", re.compile(r"\bt\s*\(\s*" + NUM + r"\s*\)\s*=\s*" + NUM + r"\s*,\s*" + P_RE, re.I)),
    ("F", re.compile(r"\bF\s*\(\s*" + NUM + r"\s*,\s*" + NUM + r"\s*\)\s*=\s*" + NUM + r"\s*,\s*" + P_RE, re.I)),
    ("chi2", re.compile(r"(?:χ2|χ²|chi2|chi-square|χ\s*2|X2)\s*\(\s*" + NUM + r"(?:\s*,\s*N\s*=\s*\d+)?\s*\)\s*=\s*"
                        + NUM + r"\s*,\s*" + P_RE, re.I)),
    ("r", re.compile(r"\br\s*\(\s*" + NUM + r"\s*\)\s*=\s*" + NUM + r"\s*,\s*" + P_RE, re.I)),
    ("z", re.compile(r"\b[zZ]\s*=\s*" + NUM + r"\s*,\s*" + P_RE)),
]


STAT_IDX = {"t": 1, "F": 2, "chi2": 1, "r": 1, "z": 0}


def _p(kind, g):
    if kind == "t":
        df, v = float(g[0]), abs(float(g[1]))
        return 2 * st.t.sf(v, df), g[2:]
    if kind == "F":
        d1, d2, v = float(g[0]), float(g[1]), float(g[2])
        return st.f.sf(v, d1, d2), g[3:]
    if kind == "chi2":
        df, v = float(g[0]), float(g[1])
        return st.chi2.sf(v, df), g[2:]
    if kind == "r":
        df, r = float(g[0]), float(g[1])
        if abs(r) >= 1:
            return None, g[2:]
        t = r * ((df / (1 - r * r)) ** 0.5)
        return 2 * st.t.sf(abs(t), df), g[2:]
    if kind == "z":
        return 2 * st.norm.sf(abs(float(g[0]))), g[1:]


def statcheck(doc):
    raw = docproc.doc_text(doc)
    text = raw.replace("−", "-").replace("–", "-").replace(" ", " ").replace("\n", " ")
    page_at = [(m.start(), int(m.group(1))) for m in re.finditer(r"\[\[page (\d+)\]\]", text)]
    out = []
    for kind, rx in PATTERNS:
        for m in rx.finditer(text):
            try:
                p, (op, rep) = _p(kind, m.groups())
            except Exception:
                continue
            if p is None:
                continue
            # the reported statistic is rounded: recompute p over its rounding interval
            g = list(m.groups())
            si = STAT_IDX[kind]
            sdec = len(g[si].split(".")[-1]) if "." in g[si] else 0
            half = 0.5 * 10 ** (-sdec)
            ps = [p]
            for delta in (-half, half):
                gg = g[:]
                gg[si] = str(float(g[si]) + delta)
                try:
                    q = _p(kind, gg)[0]
                    if q is not None:
                        ps.append(q)
                except Exception:
                    pass
            pmin, pmax = min(ps), max(ps)
            rep = float(rep)
            dec = len(m.group(m.lastindex).split(".")[-1]) if "." in m.group(m.lastindex) else 0
            ptol = 0.5 * 10 ** (-dec) + 1e-9
            if op == "=":
                ok = pmin - ptol <= rep <= pmax + ptol
            elif op in "<≤":
                ok = pmin < rep + 1e-9
            else:
                ok = pmax > rep - 1e-9
            gross = (not ok) and ((rep <= 0.05 if op != ">" else rep > 0.05) != (p <= 0.05))
            page = max([pg for pos, pg in page_at if pos <= m.start()] or [None])
            out.append({"test": kind, "text": m.group(0), "page": page, "reported": f"p {op} {rep}",
                        "computed_p": float(f"{p:.4g}"), "consistent": bool(ok), "decision_error": bool(gross)})
    return {"found": len(out), "inconsistent": sum(not o["consistent"] for o in out),
            "decision_errors": sum(o["decision_error"] for o in out), "results": out,
            "note": "仅能识别 APA 风格的完整报告（如 t(28) = 2.20, p = .036）；双尾检验假设。结果供参考。"}


# ------------------------------------------------------------------ 7. figures & tables gallery
def figures(doc):
    d = os.path.join(os.path.dirname(doc["pdf_path"]), "figures")
    os.makedirs(d, exist_ok=True)
    for f in os.listdir(d):
        os.remove(os.path.join(d, f))
    pdf = fitz.open(doc["pdf_path"])
    items = []
    for pno, page in enumerate(pdf, 1):
        for b in page.get_text("blocks"):
            txt = b[4].strip()
            m = CAPTION_RE.match(txt)
            if not m:
                continue
            kind = "table" if m.group(1).lower().startswith("tab") else "figure"
            pr = page.rect
            cap = fitz.Rect(b[:4])
            # figure: caption is usually below the figure; table: caption usually above the table
            if kind == "figure":
                clip = fitz.Rect(pr.x0, max(pr.y0, cap.y0 - pr.height * 0.55), pr.x1, cap.y1 + 4)
            else:
                clip = fitz.Rect(pr.x0, cap.y0 - 4, pr.x1, min(pr.y1, cap.y1 + pr.height * 0.55))
            name = f"p{pno}_{len(items)}.png"
            page.get_pixmap(clip=clip, dpi=110).save(os.path.join(d, name))
            items.append({"kind": kind, "label": f"{m.group(1)} {m.group(2)}", "page": pno,
                          "caption": re.sub(r"\s+", " ", txt)[:400], "img": name})
        if not any(i["page"] == pno for i in items):
            for img in page.get_images(full=True)[:4]:
                try:
                    rects = page.get_image_rects(img[0])
                except Exception:
                    rects = []
                for r in rects:
                    if r.width * r.height < 0.08 * page.rect.width * page.rect.height:
                        continue
                    name = f"p{pno}_{len(items)}.png"
                    page.get_pixmap(clip=r, dpi=110).save(os.path.join(d, name))
                    items.append({"kind": "image", "label": "Image (no caption)", "page": pno, "caption": "",
                                  "img": name})
    pdf.close()
    seen, uniq = set(), []
    for i in items:     # same caption repeated (e.g. "see Figure 2" in text that matched) -> keep first
        key = (i["kind"], i["label"])
        if i["kind"] != "image" and key in seen:
            continue
        seen.add(key)
        uniq.append(i)
    return {"items": uniq, "note": "按图注位置截取页面区域，截取范围是估计值；点击页码可跳转原文。"}


# ------------------------------------------------------------------ 8. version comparison
def _paras(doc):
    t = docproc.doc_text(doc, strip_markers=True)
    t = re.sub(r"-\n(\w)", r"\1", t)
    paras = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n|(?<=[.!?])\n(?=[A-Z])", t)]
    return [p for p in paras if len(p) > 25]


def version_diff(old, new):
    a, b = _paras(old), _paras(new)
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    blocks, added, removed, changed = [], 0, 0, 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if tag == "replace":
            changed += 1
            for x, y in zip(a[i1:i2], b[j1:j2]):
                blocks.append({"type": "changed", "html": _word_diff(x, y)})
            for x in a[i1 + (j2 - j1):i2]:
                blocks.append({"type": "removed", "html": html.escape(x)})
            for y in b[j1 + (i2 - i1):j2]:
                blocks.append({"type": "added", "html": html.escape(y)})
        elif tag == "delete":
            removed += i2 - i1
            blocks += [{"type": "removed", "html": html.escape(x)} for x in a[i1:i2]]
        elif tag == "insert":
            added += j2 - j1
            blocks += [{"type": "added", "html": html.escape(y)} for y in b[j1:j2]]
    return {"old": old["label"], "new": new["label"], "added": added, "removed": removed,
            "changed": changed, "similarity": round(100 * sm.ratio(), 1), "blocks": blocks[:800]}


def _word_diff(x, y):
    a, b = x.split(), y.split()
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b).get_opcodes():
        if tag == "equal":
            out.append(html.escape(" ".join(a[i1:i2])))
        if tag in ("delete", "replace"):
            out.append("<del>" + html.escape(" ".join(a[i1:i2])) + "</del>")
        if tag in ("insert", "replace"):
            out.append("<ins>" + html.escape(" ".join(b[j1:j2])) + "</ins>")
    return " ".join(out)


# ------------------------------------------------------------------ journal aims & scope
SCOPE_WORDS = ("aims", "scope", "about the journal", "about this journal", "overview", "journal information",
               "aims-and-scope", "aims_and_scope", "about")


def journal_scope(project):
    cli = httpx.Client(timeout=25, follow_redirects=True,
                       headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                                              "(KHTML, like Gecko) Chrome/130 Safari/537.36"})
    log, url = [], (project.get("journal_url") or "").strip()
    info = {}
    if project.get("journal"):
        r = cli.get("https://api.openalex.org/sources", params={"search": project["journal"], "per-page": 1})
        if r.status_code == 200 and r.json().get("results"):
            s = r.json()["results"][0]
            info = {"name": s.get("display_name"), "publisher": s.get("host_organization_name"),
                    "issn": s.get("issn"), "homepage": s.get("homepage_url"),
                    "works": s.get("works_count"), "is_oa": s.get("is_oa"),
                    "topics": [t.get("display_name") for t in (s.get("topics") or [])[:12]]}
            url = url or s.get("homepage_url") or ""
            log.append(f"OpenAlex 匹配期刊：{info['name']}")
    if not url:
        return {"journal": info, "error": "找不到期刊网址，请在任务设置里填写期刊主页网址", "log": log}
    pages = []

    def fetch(u):
        try:
            r = cli.get(u)
            log.append(f"{r.status_code} {u}")
            return r if r.status_code == 200 and "html" in r.headers.get("content-type", "") else None
        except Exception as e:
            log.append(f"失败 {u}: {e}")
            return None

    r = fetch(url)
    cands = []
    if r is not None:
        soup = BeautifulSoup(r.text, "lxml")
        pages.append((str(r.url), soup))
        for a in soup.find_all("a", href=True):
            label = (a.get_text(" ", strip=True) + " " + a["href"]).lower()
            score = sum(w in label for w in SCOPE_WORDS[:6]) * 3 + ("about" in label)
            if score:
                cands.append((score, httpx.URL(str(r.url)).join(a["href"])))
    cands.sort(key=lambda x: -x[0])
    tried = set()
    for _, u in cands[:4]:
        if str(u) in tried:
            continue
        tried.add(str(u))
        r2 = fetch(str(u))
        if r2 is not None:
            pages.insert(0, (str(r2.url), BeautifulSoup(r2.text, "lxml")))
    best_text, best_url = "", url
    for u, soup in pages:
        for t in soup(["script", "style", "nav", "header", "footer", "noscript", "form"]):
            t.decompose()
        main = soup.find("main") or soup.find("article") or soup.body or soup
        text = re.sub(r"\n{3,}", "\n\n", main.get_text("\n", strip=True))
        low = text.lower()
        i = max(low.find("aims and scope"), low.find("aims & scope"), low.find("scope"))
        excerpt = text[max(0, i - 200):i + 6000] if i >= 0 else text[:6000]
        if i >= 0 or not best_text:
            best_text, best_url = excerpt, u
            if i >= 0:
                break
    if not pages:
        return {"journal": info, "url": url, "text": "", "log": log,
                "error": "出版社网站拒绝了自动抓取（常见于 Elsevier / Wiley）。请点击“来源”链接打开期刊页面，"
                         "把 Aims & Scope 复制粘贴到下面的文本框并保存。"}
    return {"journal": info, "url": best_url, "text": best_text, "log": log,
            "note": "许多出版社网站会拦截自动抓取。如果内容不对，可以把期刊 Aims & Scope 粘贴到下方保存。"}
