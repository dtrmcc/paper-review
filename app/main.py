"""areview — local paper-review workspace. Manuscripts never leave this laptop unless the
user explicitly sends (redacted) text to Together AI on a project whose journal allows it."""
import os
import re
import shutil
import tempfile
import threading
import traceback

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import auth
import db
import docproc
import llm
import prompts
import refcheck
import tools

STATIC = os.path.join(os.path.dirname(__file__), "static")
app = FastAPI(title="areview", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
MAX_UPLOAD = 200 * 1024 * 1024


@app.on_event("startup")
def startup():
    db.init()
    pw = auth.ensure_user(os.environ.get("ADMIN_USER", "admin"), os.environ.get("ADMIN_INITIAL_PASSWORD"))
    if pw and not os.environ.get("ADMIN_INITIAL_PASSWORD"):
        print(f"*** created user with password: {pw}", flush=True)
    # jobs interrupted by a restart
    db.ex("UPDATE jobs SET status='failed', error='interrupted by restart' WHERE status='running'")


@app.middleware("http")
async def security_headers(req: Request, call_next):
    resp = await call_next(req)
    resp.headers["X-Frame-Options"] = "SAMEORIGIN"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    if req.url.path.startswith("/api/") or req.url.path.startswith("/files/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


def page(name):
    return HTMLResponse(open(os.path.join(STATIC, name), encoding="utf-8").read())


# ------------------------------------------------------------------ pages & auth
@app.get("/favicon.ico")
def favicon():
    return FileResponse(os.path.join(STATIC, "favicon.svg"), media_type="image/svg+xml")


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/login")
def login_page(req: Request):
    if auth.current_user(req):
        return RedirectResponse("/", 302)
    return page("login.html")


@app.post("/login")
def login(req: Request, username: str = Form(...), password: str = Form(...)):
    ip = auth.client_ip(req)
    if auth.too_many_failures(ip):
        return RedirectResponse("/login?e=locked", 303)
    u = db.q1("SELECT * FROM users WHERE username=?", (username.strip(),))
    if not u or not auth.check_pw(password, u["pw_hash"]):
        auth.record_failure(ip)
        return RedirectResponse("/login?e=bad", 303)
    tok = auth.create_session(u["id"])
    r = RedirectResponse("/", 303)
    secure = req.headers.get("x-forwarded-proto") == "https"
    r.set_cookie(auth.COOKIE, tok, max_age=auth.SESSION_DAYS * 86400, httponly=True, secure=secure,
                 samesite="strict", path="/")
    return r


@app.get("/logout")
def logout(req: Request):
    auth.drop_session(req.cookies.get(auth.COOKIE))
    r = RedirectResponse("/login", 303)
    r.delete_cookie(auth.COOKIE, path="/")
    return r


def guarded(name):
    def h(req: Request):
        if not auth.current_user(req):
            return RedirectResponse("/login", 302)
        return page(name)
    return h


app.get("/")(guarded("dashboard.html"))
app.get("/p/{pid}")(guarded("workspace.html"))
app.get("/p/{pid}/refs")(guarded("refs.html"))
app.get("/p/{pid}/tool/{tool}")(guarded("tool.html"))
app.get("/settings")(guarded("settings.html"))

U = Depends(auth.require_user)


# ------------------------------------------------------------------ helpers
def project_or_404(pid):
    p = db.q1("SELECT * FROM projects WHERE id=?", (pid,))
    if not p:
        raise HTTPException(404, "project not found")
    return p


def doc_or_404(did):
    d = db.q1("SELECT * FROM documents WHERE id=?", (did,))
    if not d:
        raise HTTPException(404, "document not found")
    return d


def main_doc(pid):
    """Latest manuscript version (R2 > R1 > Original)."""
    return db.q1("SELECT * FROM documents WHERE project_id=? AND kind='manuscript' ORDER BY id DESC LIMIT 1", (pid,))


def touch(pid):
    db.ex("UPDATE projects SET updated=? WHERE id=?", (db.now(), pid))


def start_job(pid, did, kind, fn):
    jid = db.ex("INSERT INTO jobs(project_id,document_id,kind,status,progress,created,updated)"
                " VALUES(?,?,?,'running','',?,?)", (pid, did, kind, db.now(), db.now()))

    def run():
        try:
            fn(jid)
            db.ex("UPDATE jobs SET status='done', updated=? WHERE id=?", (db.now(), jid))
        except Exception as e:
            traceback.print_exc()
            db.ex("UPDATE jobs SET status='failed', error=?, updated=? WHERE id=?", (str(e)[:500], db.now(), jid))
    threading.Thread(target=run, daemon=True).start()
    return jid


def process_document(pid, did, check_refs=True):
    """Background: GROBID parse -> store references -> verify references."""
    def job(jid):
        d = doc_or_404(did)
        db.ex("UPDATE jobs SET progress='解析全文与参考文献 (GROBID)' WHERE id=?", (jid,))
        try:
            docproc.run_grobid(d)
        except Exception:
            db.ex("UPDATE documents SET grobid_status='failed' WHERE id=?", (did,))
            raise
        d = doc_or_404(did)
        refs = docproc.parse_refs(docproc.tei_root(d))
        docproc.store_refs(did, refs)
        if check_refs and refs and d["kind"] == "manuscript" \
                and db.get_setting("auto_refcheck", "1") == "1":
            db.ex("UPDATE jobs SET progress='0/%d' WHERE id=?" % len(refs), (jid,))
            refcheck.run(did, jid)
    return start_job(pid, did, "process", job)


# ------------------------------------------------------------------ projects
class ProjectIn(BaseModel):
    title: str | None = None
    journal: str | None = None
    journal_url: str | None = None
    manuscript_id: str | None = None
    deadline: str | None = None
    received: str | None = None
    status: str | None = None
    ai_policy: str | None = None
    recommendation: str | None = None
    blind: str | None = None
    author_names: str | None = None
    memo: str | None = None


@app.get("/api/projects")
def list_projects(u=U):
    ps = db.q("SELECT * FROM projects ORDER BY CASE WHEN status IN ('submitted','archived') THEN 1 ELSE 0 END,"
              " COALESCE(deadline,'9999'), id DESC")
    for p in ps:
        p.pop("ai_context", None)
        p["docs"] = db.q("SELECT id,label,kind,filename,pages FROM documents WHERE project_id=? ORDER BY id", (p["id"],))
        n = db.q1("SELECT content FROM notes WHERE project_id=?", (p["id"],))
        p["note_words"] = len((n or {}).get("content", "").split()) if n else 0
        p["reviews"] = db.q1("SELECT COUNT(*) n FROM reviews WHERE project_id=?", (p["id"],))["n"]
        p["annotations"] = db.q1("SELECT COUNT(*) n FROM annotations a JOIN documents d ON d.id=a.document_id"
                                 " WHERE d.project_id=?", (p["id"],))["n"]
        md = main_doc(p["id"])
        if md:
            p["refs"] = db.q1("SELECT COUNT(*) n, SUM(status='ok') ok, SUM(status IN ('mismatch','notfound',"
                              "'retracted')) bad, SUM(status='pending') pending FROM refs WHERE document_id=?",
                              (md["id"],))
    return ps


@app.post("/api/projects")
async def create_project(title: str = Form(...), journal: str = Form(""), journal_url: str = Form(""),
                         manuscript_id: str = Form(""), deadline: str = Form(""), ai_policy: str = Form("local"),
                         blind: str = Form("unknown"), memo: str = Form(""),
                         file: UploadFile | None = File(None), u=U):
    pid = db.ex("INSERT INTO projects(title,journal,journal_url,manuscript_id,deadline,received,status,ai_policy,"
                "blind,memo,created,updated) VALUES(?,?,?,?,?,date('now'),'new',?,?,?,?,?)",
                (title.strip(), journal.strip(), journal_url.strip(), manuscript_id.strip(), deadline or None,
                 ai_policy if ai_policy in ("none", "local", "remote") else "local", blind, memo,
                 db.now(), db.now()))
    if file and file.filename:
        await add_document(pid, file, "Original", "manuscript")
    return {"id": pid}


@app.get("/api/projects/{pid}")
def get_project(pid: int, u=U):
    p = project_or_404(pid)
    p["docs"] = db.q("SELECT id,label,kind,filename,pages,grobid_status,uploaded FROM documents"
                     " WHERE project_id=? ORDER BY id", (pid,))
    p["jobs"] = db.q("SELECT id,document_id,kind,status,progress,error,updated FROM jobs WHERE project_id=?"
                     " ORDER BY id DESC LIMIT 5", (pid,))
    p["has_ai_context"] = bool(p.pop("ai_context", None))
    return p


@app.patch("/api/projects/{pid}")
def update_project(pid: int, body: ProjectIn, u=U):
    project_or_404(pid)
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if "ai_policy" in fields and fields["ai_policy"] not in ("none", "local", "remote"):
        raise HTTPException(400, "bad ai_policy")
    for k, v in fields.items():
        db.ex(f"UPDATE projects SET {k}=? WHERE id=?", (v, pid))
    touch(pid)
    return get_project(pid)


@app.delete("/api/projects/{pid}")
def delete_project(pid: int, u=U):
    project_or_404(pid)
    for d in db.q("SELECT id FROM documents WHERE project_id=?", (pid,)):
        db.ex("DELETE FROM annotations WHERE document_id=?", (d["id"],))
        db.ex("DELETE FROM refs WHERE document_id=?", (d["id"],))
    for t in ("documents", "notes", "note_history", "chats", "reviews", "tool_results", "response_items", "jobs"):
        db.ex(f"DELETE FROM {t} WHERE project_id=?", (pid,))
    db.ex("DELETE FROM projects WHERE id=?", (pid,))
    shutil.rmtree(os.path.join(db.FILES_DIR, f"p{pid}"), ignore_errors=True)
    return {"ok": True}


# ------------------------------------------------------------------ documents
async def add_document(pid, file: UploadFile, label, kind):
    did = db.ex("INSERT INTO documents(project_id,label,kind,filename,uploaded) VALUES(?,?,?,?,?)",
                (pid, label, kind, docproc.safe_name(file.filename), db.now()))
    fd, tmp = tempfile.mkstemp(dir=db.FILES_DIR)
    size = 0
    with os.fdopen(fd, "wb") as f:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > MAX_UPLOAD:
                os.remove(tmp)
                db.ex("DELETE FROM documents WHERE id=?", (did,))
                raise HTTPException(413, "文件太大（上限 200MB）")
            f.write(chunk)
    try:
        docproc.import_file(pid, did, tmp, file.filename)
    except Exception as e:
        db.ex("DELETE FROM documents WHERE id=?", (did,))
        if os.path.exists(tmp):
            os.remove(tmp)
        raise HTTPException(400, f"导入失败：{e}")
    process_document(pid, did, check_refs=(kind == "manuscript"))
    touch(pid)
    return did


@app.post("/api/projects/{pid}/documents")
async def upload_document(pid: int, label: str = Form("R1"), kind: str = Form("manuscript"),
                          file: UploadFile = File(...), u=U):
    project_or_404(pid)
    did = await add_document(pid, file, label, kind)
    return {"id": did}


@app.delete("/api/documents/{did}")
def delete_document(did: int, u=U):
    d = doc_or_404(did)
    db.ex("DELETE FROM annotations WHERE document_id=?", (did,))
    db.ex("DELETE FROM refs WHERE document_id=?", (did,))
    db.ex("DELETE FROM documents WHERE id=?", (did,))
    shutil.rmtree(os.path.dirname(d["pdf_path"] or d["orig_path"] or "/nonexistent"), ignore_errors=True)
    return {"ok": True}


@app.get("/files/doc/{did}/pdf")
def doc_pdf(did: int, u=Depends(auth.require_user)):
    d = doc_or_404(did)
    return FileResponse(d["pdf_path"], media_type="application/pdf")


@app.get("/files/doc/{did}/original")
def doc_original(did: int, u=Depends(auth.require_user)):
    d = doc_or_404(did)
    return FileResponse(d["orig_path"], filename=d["filename"])


@app.get("/files/doc/{did}/fig/{name}")
def doc_fig(did: int, name: str, u=Depends(auth.require_user)):
    d = doc_or_404(did)
    if not re.fullmatch(r"p\d+_\d+\.png", name):
        raise HTTPException(404)
    return FileResponse(os.path.join(os.path.dirname(d["pdf_path"]), "figures", name))


@app.get("/api/documents/{did}/text")
def document_text(did: int, u=U):
    return {"text": docproc.doc_text(doc_or_404(did))}


@app.post("/api/documents/{did}/reprocess")
def reprocess(did: int, u=U):
    d = doc_or_404(did)
    return {"job": process_document(d["project_id"], did)}


# ------------------------------------------------------------------ annotations
class AnnIn(BaseModel):
    page: int | None = None
    type: str | None = None
    color: str | None = None
    rects: list | None = None
    quote: str | None = None
    comment: str | None = None


@app.get("/api/documents/{did}/annotations")
def list_ann(did: int, u=U):
    rows = db.q("SELECT * FROM annotations WHERE document_id=? ORDER BY page, id", (did,))
    for r in rows:
        r["rects"] = db.jload(r["rects"], [])
    return rows


@app.post("/api/documents/{did}/annotations")
def add_ann(did: int, a: AnnIn, u=U):
    d = doc_or_404(did)
    aid = db.ex("INSERT INTO annotations(document_id,page,type,color,rects,quote,comment,created,updated)"
                " VALUES(?,?,?,?,?,?,?,?,?)", (did, a.page, a.type, a.color, db.jdump(a.rects or []),
                                               a.quote, a.comment, db.now(), db.now()))
    touch(d["project_id"])
    return {"id": aid}


@app.patch("/api/annotations/{aid}")
def edit_ann(aid: int, a: AnnIn, u=U):
    for k, v in a.model_dump().items():
        if v is not None:
            db.ex(f"UPDATE annotations SET {k}=?, updated=? WHERE id=?",
                  (db.jdump(v) if k == "rects" else v, db.now(), aid))
    return {"ok": True}


@app.delete("/api/annotations/{aid}")
def del_ann(aid: int, u=U):
    db.ex("DELETE FROM annotations WHERE id=?", (aid,))
    return {"ok": True}


# ------------------------------------------------------------------ notes (autosave + history)
class NoteIn(BaseModel):
    content: str


@app.get("/api/projects/{pid}/notes")
def get_notes(pid: int, u=U):
    n = db.q1("SELECT * FROM notes WHERE project_id=?", (pid,))
    return n or {"project_id": pid, "content": "", "updated": None}


@app.put("/api/projects/{pid}/notes")
def save_notes(pid: int, n: NoteIn, u=U):
    project_or_404(pid)
    db.ex("INSERT INTO notes(project_id,content,updated) VALUES(?,?,?) ON CONFLICT(project_id) DO UPDATE SET"
          " content=excluded.content, updated=excluded.updated", (pid, n.content, db.now()))
    last = db.q1("SELECT content, saved_at FROM note_history WHERE project_id=? ORDER BY id DESC LIMIT 1", (pid,))
    # snapshot at most every 5 minutes, or when a lot was removed (protects against accidental deletion)
    from datetime import datetime, timedelta, timezone
    old = not last or datetime.fromisoformat(last["saved_at"]) < datetime.now(timezone.utc) - timedelta(minutes=5)
    big_cut = last and len(n.content) < len(last["content"]) * 0.7
    if (old or big_cut) and (not last or last["content"] != n.content):
        db.ex("INSERT INTO note_history(project_id,content,saved_at) VALUES(?,?,?)", (pid, n.content, db.now()))
    if db.q1("SELECT status FROM projects WHERE id=?", (pid,))["status"] == "new":
        db.ex("UPDATE projects SET status='reading' WHERE id=?", (pid,))
    touch(pid)
    return {"updated": db.now()}


@app.get("/api/projects/{pid}/notes/history")
def note_history(pid: int, u=U):
    return db.q("SELECT id, saved_at, length(content) chars, content FROM note_history WHERE project_id=?"
                " ORDER BY id DESC LIMIT 100", (pid,))


# ------------------------------------------------------------------ AI context (redacted manuscript)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
URL_RE = re.compile(r"https?://\S+|www\.\S+")
ORCID_RE = re.compile(r"\b\d{4}-\d{4}-\d{4}-\d{3}[\dX]\b")


@app.get("/api/projects/{pid}/ai-context")
def get_ai_context(pid: int, u=U):
    p = project_or_404(pid)
    d = main_doc(pid)
    suggestions = []
    if d:
        root = docproc.tei_root(d)
        if root is not None:
            suggestions = docproc.parse_structure(root)["authors"]
    return {"context": p["ai_context"] or "", "updated": p["ai_context_updated"], "author_suggestions": suggestions}


class RedactIn(BaseModel):
    terms: list[str] = []
    drop_references: bool = True
    drop_front_matter: bool = True


@app.post("/api/projects/{pid}/ai-context/draft")
def draft_ai_context(pid: int, r: RedactIn, u=U):
    """Build a redacted plain-text version locally; the user reviews/edits it before saving."""
    d = main_doc(pid)
    if not d:
        raise HTTPException(400, "没有稿件")
    t = docproc.doc_text(d, strip_markers=True)
    root = docproc.tei_root(d)
    if r.drop_front_matter and root is not None:
        abs_ = docproc.parse_structure(root)["abstract"]
        i = t.find(abs_[:60]) if abs_ else -1
        if i > 0:
            t = "[front matter removed]\n" + t[i:]
    if r.drop_references:
        m = list(re.finditer(r"\n\s*(References|REFERENCES|Bibliography|Literature Cited)\s*\n", t))
        if m:
            t = t[:m[-1].start()] + "\n[reference list removed]\n"
    t = EMAIL_RE.sub("[email]", t)
    t = URL_RE.sub("[url]", t)
    t = ORCID_RE.sub("[orcid]", t)
    for term in sorted({x.strip() for x in r.terms if len(x.strip()) > 1}, key=len, reverse=True):
        t = re.sub(re.escape(term), "[redacted]", t, flags=re.I)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return {"context": t}


class CtxIn(BaseModel):
    context: str


@app.put("/api/projects/{pid}/ai-context")
def save_ai_context(pid: int, c: CtxIn, u=U):
    db.ex("UPDATE projects SET ai_context=?, ai_context_updated=? WHERE id=?", (c.context, db.now(), pid))
    return {"ok": True}


# ------------------------------------------------------------------ AI chat & review drafting
class ChatIn(BaseModel):
    message: str
    provider: str = "local"
    include_manuscript: bool = False
    include_notes: bool = True
    preview: bool = False


@app.get("/api/projects/{pid}/chat")
def get_chat(pid: int, u=U):
    return db.q("SELECT * FROM chats WHERE project_id=? ORDER BY id", (pid,))


@app.delete("/api/projects/{pid}/chat")
def clear_chat(pid: int, u=U):
    db.ex("DELETE FROM chats WHERE project_id=?", (pid,))
    return {"ok": True}


def _stream_and_save(gen, on_done):
    def body():
        buf = []
        try:
            for piece in gen:
                buf.append(piece)
                yield piece
        except llm.PolicyError as e:
            yield f"\n[阻止] {e}"
            return
        except Exception as e:
            yield f"\n[错误] {e}"
            return
        on_done(llm.strip_think("".join(buf)).strip())
    return StreamingResponse(body(), media_type="text/plain; charset=utf-8",
                             headers={"X-Accel-Buffering": "no"})


@app.post("/api/projects/{pid}/chat")
def chat(pid: int, c: ChatIn, u=U):
    p = project_or_404(pid)
    try:
        llm.check_policy(p, c.provider)
    except llm.PolicyError as e:
        raise HTTPException(403, str(e))
    if c.include_manuscript and not p["ai_context"]:
        raise HTTPException(400, "还没有准备“可发送的稿件文本”（遮挡敏感信息后保存）")
    history = db.q("SELECT role, content FROM chats WHERE project_id=? ORDER BY id", (pid,))
    notes = (get_notes(pid)["content"] or "") if c.include_notes else ""
    msgs = prompts.chat_messages(p, notes, history, c.message, p["ai_context"] if c.include_manuscript else None)
    if c.preview:
        return {"messages": msgs, "chars": sum(len(m["content"]) for m in msgs),
                "model": llm.endpoint(c.provider)[2] if c.provider != "together" or llm.together_key() else "?"}
    db.ex("INSERT INTO chats(project_id,role,content,provider,included_manuscript,created) VALUES(?,?,?,?,?,?)",
          (pid, "user", c.message, c.provider, int(c.include_manuscript), db.now()))
    model = llm.cfg("together_model" if c.provider == "together" else "local_model")

    def done(text):
        db.ex("INSERT INTO chats(project_id,role,content,provider,model,included_manuscript,created)"
              " VALUES(?,?,?,?,?,?,?)", (pid, "assistant", text, c.provider, model, int(c.include_manuscript), db.now()))
        if p["status"] in ("new", "reading"):
            db.ex("UPDATE projects SET status='drafting' WHERE id=?", (pid,))
        touch(pid)
    return _stream_and_save(llm.stream_chat(p, c.provider, msgs, "discussion", c.include_manuscript), done)


class DraftIn(BaseModel):
    kind: str = "authors"          # authors / editor
    provider: str = "local"
    include_manuscript: bool = False
    instructions: str = ""
    preview: bool = False


@app.post("/api/projects/{pid}/review/draft")
def draft_review(pid: int, r: DraftIn, u=U):
    p = project_or_404(pid)
    try:
        llm.check_policy(p, r.provider)
    except llm.PolicyError as e:
        raise HTTPException(403, str(e))
    if r.include_manuscript and not p["ai_context"]:
        raise HTTPException(400, "还没有准备“可发送的稿件文本”")
    notes = get_notes(pid)["content"] or ""
    chat_hist = db.q("SELECT role, content FROM chats WHERE project_id=? ORDER BY id", (pid,))
    anns = db.q("SELECT a.page, a.quote, a.comment FROM annotations a JOIN documents d ON d.id=a.document_id"
                " WHERE d.project_id=? AND COALESCE(a.comment,'')<>'' ORDER BY a.page", (pid,))
    prev = db.q1("SELECT content FROM reviews WHERE project_id=? AND kind=? ORDER BY id DESC LIMIT 1", (pid, r.kind))
    msgs = prompts.review_messages(p, r.kind, notes, chat_hist, anns, r.instructions,
                                   db.get_setting("style_samples", ""), prev["content"] if prev else "",
                                   p["ai_context"] if r.include_manuscript else None,
                                   r.provider == "together")
    if r.preview:
        return {"messages": msgs, "chars": sum(len(m["content"]) for m in msgs)}
    model = llm.cfg("together_model" if r.provider == "together" else "local_model")

    def done(text):
        db.ex("INSERT INTO reviews(project_id,kind,content,provider,model,created) VALUES(?,?,?,?,?,?)",
              (pid, r.kind, text, r.provider, model, db.now()))
        touch(pid)
    return _stream_and_save(llm.stream_chat(p, r.provider, msgs, f"review-{r.kind}", r.include_manuscript, 0.5), done)


@app.get("/api/projects/{pid}/reviews")
def list_reviews(pid: int, u=U):
    return db.q("SELECT * FROM reviews WHERE project_id=? ORDER BY id DESC", (pid,))


class ReviewIn(BaseModel):
    kind: str = "authors"
    content: str


@app.post("/api/projects/{pid}/reviews")
def save_review(pid: int, r: ReviewIn, u=U):
    """Save a hand-edited version as a new row (all versions are kept)."""
    rid = db.ex("INSERT INTO reviews(project_id,kind,content,provider,model,edited,created) VALUES(?,?,?,?,?,1,?)",
                (pid, r.kind, r.content, "manual", "", db.now()))
    touch(pid)
    return {"id": rid}


@app.delete("/api/reviews/{rid}")
def delete_review(rid: int, u=U):
    db.ex("DELETE FROM reviews WHERE id=?", (rid,))
    return {"ok": True}


@app.get("/api/projects/{pid}/audit")
def project_audit(pid: int, u=U):
    return db.q("SELECT id, at, provider, model, purpose, included_manuscript, chars FROM audit_log"
                " WHERE project_id=? ORDER BY id DESC", (pid,))


@app.get("/api/audit/{aid}")
def audit_detail(aid: int, u=U):
    return db.q1("SELECT * FROM audit_log WHERE id=?", (aid,))


# ------------------------------------------------------------------ references
@app.get("/api/documents/{did}/refs")
def list_refs(did: int, u=U):
    rows = db.q("SELECT * FROM refs WHERE document_id=? ORDER BY idx", (did,))
    for r in rows:
        r["authors"] = db.jload(r["authors"], [])
        r["result"] = db.jload(r["result"], {})
    job = db.q1("SELECT * FROM jobs WHERE document_id=? ORDER BY id DESC LIMIT 1", (did,))
    return {"refs": rows, "job": job, "scite": bool(db.get_setting("scite_token"))}


@app.post("/api/documents/{did}/refs/check")
def recheck_refs(did: int, only_pending: bool = False, u=U):
    d = doc_or_404(did)
    if not only_pending:
        db.ex("UPDATE refs SET status='pending' WHERE document_id=?", (did,))
    return {"job": start_job(d["project_id"], did, "refcheck", lambda jid: refcheck.run(did, jid, only_pending))}


@app.post("/api/refs/{rid}/check")
def recheck_one(rid: int, u=U):
    r = db.q1("SELECT * FROM refs WHERE id=?", (rid,))
    ref = {"title": r["title"] or "", "authors": db.jload(r["authors"], []), "year": r["year"] or "",
           "doi": r["doi"] or "", "raw": r["raw"] or ""}
    status, res = refcheck.Checker().check(ref)
    db.ex("UPDATE refs SET status=?, result=?, checked_at=? WHERE id=?", (status, db.jdump(res), db.now(), rid))
    return {"status": status, "result": res}


class RefEdit(BaseModel):
    title: str | None = None
    doi: str | None = None
    year: str | None = None
    user_flag: str | None = None
    user_note: str | None = None


@app.patch("/api/refs/{rid}")
def edit_ref(rid: int, e: RefEdit, u=U):
    for k, v in e.model_dump().items():
        if v is not None:
            db.ex(f"UPDATE refs SET {k}=? WHERE id=?", (v, rid))
    return {"ok": True}


# ------------------------------------------------------------------ toolbox
TOOLS = {"stats", "citations", "retractions", "profile", "statcheck", "figures", "diff", "journal"}


@app.get("/api/projects/{pid}/tools/{tool}")
def tool_result(pid: int, tool: str, u=U):
    r = db.q1("SELECT * FROM tool_results WHERE project_id=? AND tool=? ORDER BY id DESC LIMIT 1", (pid, tool))
    if r:
        r["result"] = db.jload(r["result"], {})
    return r or {}


class ToolIn(BaseModel):
    document_id: int | None = None
    old_id: int | None = None
    new_id: int | None = None


@app.post("/api/projects/{pid}/tools/{tool}")
def run_tool(pid: int, tool: str, t: ToolIn, u=U):
    if tool not in TOOLS:
        raise HTTPException(404)
    p = project_or_404(pid)
    d = doc_or_404(t.document_id) if t.document_id else main_doc(pid)
    if not d and tool not in ("journal",):
        raise HTTPException(400, "没有稿件")
    if tool == "stats":
        res = tools.stats(d)
    elif tool == "citations":
        res = tools.citation_consistency(d)
    elif tool == "retractions":
        res = tools.retractions(d)
    elif tool == "profile":
        res = tools.citation_profile(d, p)
    elif tool == "statcheck":
        res = tools.statcheck(d)
    elif tool == "figures":
        res = tools.figures(d)
        res["document_id"] = d["id"]
    elif tool == "diff":
        res = tools.version_diff(doc_or_404(t.old_id), doc_or_404(t.new_id))
    elif tool == "journal":
        res = tools.journal_scope(p)
    db.ex("INSERT INTO tool_results(project_id,document_id,tool,result,created) VALUES(?,?,?,?,?)",
          (pid, d["id"] if d else None, tool, db.jdump(res), db.now()))
    return {"result": res, "created": db.now()}


class ScopeIn(BaseModel):
    text: str
    url: str = ""


@app.put("/api/projects/{pid}/tools/journal")
def save_scope(pid: int, s: ScopeIn, u=U):
    prev = tool_result(pid, "journal").get("result") or {}
    prev.update({"text": s.text, "url": s.url or prev.get("url", ""), "manual": True})
    db.ex("INSERT INTO tool_results(project_id,tool,result,created) VALUES(?,?,?,?)",
          (pid, "journal", db.jdump(prev), db.now()))
    return {"ok": True}


# --- response-to-reviewers tracking (revision rounds)
@app.get("/api/projects/{pid}/responses")
def list_responses(pid: int, u=U):
    return db.q("SELECT * FROM response_items WHERE project_id=? ORDER BY position, id", (pid,))


class RespImport(BaseModel):
    text: str


@app.post("/api/projects/{pid}/responses/import")
def import_responses(pid: int, r: RespImport, u=U):
    """Split my previous comments into numbered items (1. / 1) / - / blank-line paragraphs)."""
    t = r.text.strip()
    items = re.split(r"\n\s*(?=(?:\d+[.)]|[-•*])\s)", "\n" + t)
    items = [i.strip() for i in items if i.strip()]
    if len(items) <= 1:
        items = [i.strip() for i in re.split(r"\n\s*\n", t) if i.strip()]
    base = db.q1("SELECT COALESCE(MAX(position),0) m FROM response_items WHERE project_id=?", (pid,))["m"]
    for n, it in enumerate(items, 1):
        db.ex("INSERT INTO response_items(project_id,position,comment,status) VALUES(?,?,?,'open')",
              (pid, base + n, it))
    return {"added": len(items)}


class RespItem(BaseModel):
    comment: str | None = None
    response: str | None = None
    status: str | None = None
    my_note: str | None = None


@app.patch("/api/responses/{iid}")
def edit_response(iid: int, r: RespItem, u=U):
    for k, v in r.model_dump().items():
        if v is not None:
            db.ex(f"UPDATE response_items SET {k}=? WHERE id=?", (v, iid))
    return {"ok": True}


@app.delete("/api/responses/{iid}")
def delete_response(iid: int, u=U):
    db.ex("DELETE FROM response_items WHERE id=?", (iid,))
    return {"ok": True}


@app.post("/api/projects/{pid}/responses")
def add_response(pid: int, r: RespItem, u=U):
    base = db.q1("SELECT COALESCE(MAX(position),0) m FROM response_items WHERE project_id=?", (pid,))["m"]
    return {"id": db.ex("INSERT INTO response_items(project_id,position,comment,status) VALUES(?,?,?,'open')",
                        (pid, base + 1, r.comment or ""))}


# ------------------------------------------------------------------ settings
class SettingsIn(BaseModel):
    default_provider: str | None = None
    local_model: str | None = None
    together_model: str | None = None
    together_api_key: str | None = None
    scite_token: str | None = None
    style_samples: str | None = None
    auto_refcheck: str | None = None
    use_semantic_scholar: str | None = None


def mask(s):
    return (s[:6] + "…" + s[-4:]) if s and len(s) > 12 else ("已设置" if s else "")


@app.get("/api/settings")
def get_settings(u=U):
    return {"default_provider": llm.cfg("default_provider"), "local_model": llm.cfg("local_model"),
            "together_model": llm.cfg("together_model"), "together_api_key": mask(llm.together_key()),
            "scite_token": mask(db.get_setting("scite_token", "")),
            "style_samples": db.get_setting("style_samples", ""),
            "auto_refcheck": db.get_setting("auto_refcheck", "1"),
            "use_semantic_scholar": db.get_setting("use_semantic_scholar", "1")}


@app.put("/api/settings")
def put_settings(s: SettingsIn, u=U):
    for k, v in s.model_dump().items():
        if v is None:
            continue
        if k in ("together_api_key", "scite_token") and ("…" in v or v == "已设置"):
            continue      # masked value sent back unchanged
        db.set_setting(k, v.strip() if k != "style_samples" else v)
    return get_settings()


@app.get("/api/settings/models/{provider}")
def settings_models(provider: str, u=U):
    return {"models": llm.list_models(provider)}


@app.post("/api/settings/test/{provider}")
def settings_test(provider: str, u=U):
    """Test with a harmless prompt (no manuscript content)."""
    fake = {"id": None, "ai_policy": "remote"}
    try:
        txt = llm.complete(fake, provider, [{"role": "user", "content": "Reply with exactly: OK"}], "connection-test",
                           False)
        return {"ok": True, "reply": txt[:200], "model": llm.cfg("together_model" if provider == "together"
                                                                 else "local_model")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/settings/test-scite")
def settings_test_scite(u=U):
    c = refcheck.Checker()
    if not c.scite_token:
        return {"ok": False, "error": "未设置 scite token"}
    res = c.scite("10.1038/nature14539")
    return {"ok": bool(res and "tallies" in res), "result": res}


_pull_state = {}


@app.post("/api/settings/pull-model")
def pull_model(body: dict, u=U):
    name = body.get("name") or llm.cfg("local_model")

    def run():
        _pull_state[name] = {"status": "starting"}
        try:
            for st in llm.pull_local_model(name):
                _pull_state[name] = st
            _pull_state[name] = {"status": "done"}
        except Exception as e:
            _pull_state[name] = {"status": "error", "error": str(e)}
    threading.Thread(target=run, daemon=True).start()
    return {"ok": True}


@app.get("/api/settings/pull-model")
def pull_state(u=U):
    return _pull_state


class PwIn(BaseModel):
    old: str
    new: str


@app.post("/api/settings/password")
def change_password(p: PwIn, u=U):
    row = db.q1("SELECT * FROM users WHERE id=?", (u["id"],))
    if not auth.check_pw(p.old, row["pw_hash"]):
        raise HTTPException(400, "旧密码不正确")
    if len(p.new) < 12:
        raise HTTPException(400, "新密码至少 12 位")
    db.ex("UPDATE users SET pw_hash=? WHERE id=?", (auth.hash_pw(p.new), u["id"]))
    return {"ok": True}


@app.exception_handler(HTTPException)
async def http_exc(req: Request, exc: HTTPException):
    if exc.status_code == 401 and not req.url.path.startswith(("/api/", "/files/")):
        return RedirectResponse("/login", 302)
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
