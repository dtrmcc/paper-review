"""Reference verification. Runs on this laptop, so scite.ai is reached through the campus network.
Only bibliographic data of *published* works (title / authors / year / DOI) is sent out."""
import re
import time
import unicodedata
from urllib.parse import quote

import httpx
from rapidfuzz import fuzz

import db

UA = {"User-Agent": "areview-reference-checker/1.0"}
SCITE = "https://api.scite.ai"


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9 ]+", " ", s).strip()


def sim(a, b):
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0
    return max(fuzz.token_sort_ratio(a, b), fuzz.ratio(a, b))


def surname(name):
    parts = norm(name).split()
    return parts[-1] if parts else ""


class Checker:
    def __init__(self):
        self.cli = httpx.Client(timeout=25, headers=UA, follow_redirects=True)
        self.scite_token = db.get_setting("scite_token", "")
        self.use_s2 = db.get_setting("use_semantic_scholar", "1") == "1"

    def get(self, url, **kw):
        for attempt in range(3):
            try:
                r = self.cli.get(url, **kw)
                if r.status_code == 429:
                    time.sleep(3 * (attempt + 1))
                    continue
                return r
            except httpx.HTTPError:
                time.sleep(2)
        return None

    # --- sources --------------------------------------------------------
    def crossref_doi(self, doi):
        r = self.get(f"https://api.crossref.org/works/{quote(doi)}")
        return r.json()["message"] if r is not None and r.status_code == 200 else None

    def crossref_search(self, ref):
        query = " ".join(filter(None, [ref["title"], " ".join(ref["authors"][:2]), ref["year"]])) or ref["raw"]
        r = self.get("https://api.crossref.org/works",
                     params={"query.bibliographic": query[:400], "rows": 3,
                             "select": "DOI,title,author,issued,container-title,type,update-to,relation"})
        return r.json()["message"]["items"] if r is not None and r.status_code == 200 else []

    def crossref_updates(self, doi):
        """Retraction / correction notices that point at this DOI (Crossref + Retraction Watch data)."""
        r = self.get("https://api.crossref.org/works", params={"filter": f"updates:{doi}", "rows": 10,
                                                               "select": "DOI,update-to,title"})
        notes = []
        if r is not None and r.status_code == 200:
            for it in r.json()["message"]["items"]:
                for u in it.get("update-to", []):
                    if (u.get("DOI") or "").lower() == doi.lower():
                        notes.append({"type": u.get("type"), "notice_doi": it.get("DOI"),
                                      "source": u.get("source", "crossref")})
        return notes

    def openalex(self, doi=None, title=None):
        if doi:
            r = self.get(f"https://api.openalex.org/works/doi:{quote(doi)}")
            return r.json() if r is not None and r.status_code == 200 else None
        if title:
            r = self.get("https://api.openalex.org/works", params={"search": title[:300], "per-page": 3})
            if r is not None and r.status_code == 200:
                res = r.json().get("results", [])
                res.sort(key=lambda w: -sim(title, w.get("title") or ""))
                return res[0] if res else None
        return None

    def s2(self, title):
        r = self.get("https://api.semanticscholar.org/graph/v1/paper/search/match",
                     params={"query": title[:300], "fields": "title,year,authors,externalIds,venue"})
        time.sleep(1.1)  # public rate limit
        if r is not None and r.status_code == 200 and r.json().get("data"):
            return r.json()["data"][0]
        return None

    def scite(self, doi):
        if not self.scite_token or not doi:
            return None
        h = {"Authorization": f"Bearer {self.scite_token}"}
        out = {}
        r = self.get(f"{SCITE}/tallies/{quote(doi)}", headers=h)
        if r is None:
            return {"error": "scite unreachable"}
        if r.status_code == 200:
            out["tallies"] = r.json()
        else:
            out["error"] = f"tallies HTTP {r.status_code}"
        r = self.get(f"{SCITE}/papers/{quote(doi)}", headers=h)
        if r is not None and r.status_code == 200:
            p = r.json()
            out["notices"] = p.get("editorialNotices") or []
            out["title"] = p.get("title")
        return out

    # --- one reference -------------------------------------------------
    def check(self, ref):
        res = {"sources": {}, "issues": []}
        title = ref["title"]
        first = surname(ref["authors"][0]) if ref["authors"] else ""
        best = None   # dict(title, year, authors, doi, venue, src, score)

        def consider(src, t, year, authors, doi, venue):
            nonlocal best
            score = sim(title, t) if title else sim(ref["raw"], t) * 0.9
            cand = {"src": src, "title": t, "year": str(year or ""), "authors": authors[:6],
                    "doi": (doi or "").lower(), "venue": venue, "score": round(score)}
            # among similar titles prefer the record whose year and first author also agree
            rank = cand["score"]
            if ref["year"] and cand["year"] and abs(int(ref["year"]) - int(cand["year"])) <= 1:
                rank += 6
            if first and any(first == surname(a) for a in authors[:3]):
                rank += 6
            cand["_rank"] = rank
            if not best or rank > best["_rank"]:
                best = cand

        def from_crossref(m, src="crossref"):
            consider(src, (m.get("title") or [""])[0], (m.get("issued", {}).get("date-parts") or [[None]])[0][0],
                     [" ".join(filter(None, [a.get("given"), a.get("family")])) for a in m.get("author", [])],
                     m.get("DOI"), (m.get("container-title") or [""])[0])

        if ref["doi"]:
            m = self.crossref_doi(ref["doi"])
            res["sources"]["crossref_doi"] = bool(m)
            if m:
                from_crossref(m, "crossref (DOI)")
                if title and sim(title, (m.get("title") or [""])[0]) < 70:
                    res["issues"].append("DOI 对应的文章标题与引用不一致")
            else:
                res["issues"].append("DOI 在 Crossref 中无法解析")
        if not best or best["_rank"] < 102:
            for m in self.crossref_search(ref):
                from_crossref(m)
            res["sources"]["crossref_search"] = True
        doi = ref["doi"] or (best["doi"] if best and best["score"] >= 85 else "")
        oa = self.openalex(doi=doi) if doi else self.openalex(title=title or ref["raw"][:200])
        if oa:
            consider("openalex", oa.get("title") or "", oa.get("publication_year"),
                     [a["author"]["display_name"] for a in oa.get("authorships", [])],
                     (oa.get("doi") or "").replace("https://doi.org/", ""),
                     ((oa.get("primary_location") or {}).get("source") or {}).get("display_name", ""))
            res["openalex"] = {"id": oa.get("id"), "cited_by": oa.get("cited_by_count"),
                               "is_retracted": oa.get("is_retracted"), "type": oa.get("type")}
        if self.use_s2 and (not best or best["score"] < 85 or best["_rank"] < 100):
            s = self.s2(title if len(title) > 15 else ref["raw"])
            if s:
                consider("semantic scholar", s.get("title") or "", s.get("year"),
                         [a.get("name", "") for a in s.get("authors", [])],
                         (s.get("externalIds") or {}).get("DOI"), s.get("venue"))
        if best:
            best.pop("_rank", None)
        res["best"] = best
        found = best and best["score"] >= 85
        doi = ref["doi"] or (best["doi"] if found else "")
        res["doi_used"] = doi

        retracted = False
        if doi:
            notes = self.crossref_updates(doi)
            res["notices"] = notes
            if any("retract" in (n.get("type") or "") for n in notes):
                retracted = True
            if any(n.get("type") in ("correction", "erratum", "corrigendum", "expression_of_concern")
                   for n in notes):
                res["issues"].append("该文献有更正 / 关注声明")
            sc = self.scite(doi)
            if sc:
                res["scite"] = sc
                for n in sc.get("notices") or []:
                    s = str(n).lower()
                    if "retract" in s:
                        retracted = True
        if oa and oa.get("is_retracted"):
            retracted = True

        if found:
            if ref["year"] and best["year"] and abs(int(ref["year"]) - int(best["year"])) > 1:
                res["issues"].append(f"年份不一致：引用 {ref['year']}，记录 {best['year']}")
            if first and best["authors"] and not any(first == surname(a) for a in best["authors"][:3]) \
                    and not any(first in norm(a) for a in best["authors"]):
                res["issues"].append("第一作者与记录不一致")
        if retracted:
            status = "retracted"
        elif not found:
            status = "notfound" if not best or best["score"] < 70 else "mismatch"
            if best and best["score"] >= 70:
                res["issues"].append(f"只找到近似匹配（相似度 {best['score']}）")
        elif res["issues"]:
            status = "mismatch"
        else:
            status = "ok"
        return status, res


def run(doc_id, job_id, only_pending=False):
    ch = Checker()
    sql = "SELECT * FROM refs WHERE document_id=?" + (" AND status IN ('pending','error')" if only_pending else "")
    refs = db.q(sql + " ORDER BY idx", (doc_id,))
    for n, r in enumerate(refs, 1):
        ref = {"title": r["title"] or "", "authors": db.jload(r["authors"], []), "year": r["year"] or "",
               "doi": r["doi"] or "", "raw": r["raw"] or ""}
        try:
            status, res = ch.check(ref)
        except Exception as e:
            status, res = "error", {"error": str(e)}
        db.ex("UPDATE refs SET status=?, result=?, checked_at=? WHERE id=?",
              (status, db.jdump(res), db.now(), r["id"]))
        db.ex("UPDATE jobs SET progress=?, updated=? WHERE id=?", (f"{n}/{len(refs)}", db.now(), job_id))
        time.sleep(0.3)
