# areview — local paper-review workstation

Manuscripts stay on this laptop (`./data`, mode 700). Everything runs in Docker:

| service | what | network |
|---|---|---|
| app    | FastAPI + web UI, login, SQLite `data/review.sqlite3` | 127.0.0.1:8089 only |
| grobid | local PDF structure / reference parser | internal |
| ollama | local LLM (default `qwen3:8b`, CPU) | internal |

    cd ~/paper-review
    sg docker -c "docker compose up -d --build"     # start / rebuild
    sg docker -c "docker compose logs -f app"       # logs

- Settings (models, Together key, scite token, style samples): web UI → 设置. `.env` only seeds defaults.
- AI gateway: `app/llm.py`. Per-project journal AI policy: none / local / remote(Together).
  Every call is stored in `audit_log` with the exact payload.
- Outbound traffic from the laptop (campus network): Crossref, OpenAlex, Semantic Scholar, scite.ai
  (bibliographic data of published references only), journal websites, Together (only if allowed + confirmed).
- Public access: VPS Caddy (your domain, template in deploy/Caddyfile.vps-snippet) -> VPS 127.0.0.1:18089 -> tunnel (user service
  `areview-tunnel`) -> laptop 127.0.0.1:8089. Snippet: deploy/Caddyfile.vps-snippet
- Backups: deploy/backup.sh, daily timer `areview-backup.timer` -> ~/backups/paper-review (RESTORE.md there).
- Reset password if lost: `sg docker -c "docker compose exec app python -c \"import db,auth;db.init();db.ex('UPDATE users SET pw_hash=? WHERE username=?',(auth.hash_pw('NEW-PASSWORD'),'YOUR-USERNAME'))\""`
