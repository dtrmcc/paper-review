"""Single gateway for every AI call. Enforces the per-project journal AI policy and logs
exactly what was sent to the audit log.

Providers (both OpenAI-compatible chat APIs):
  local    -> Ollama container on this laptop (nothing leaves the machine)
  together -> Together AI (only when the project's policy is 'remote' and the user confirmed)
"""
import json
import os
import re

import httpx

import db

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ollama:11434")
TOGETHER_URL = "https://api.together.xyz/v1"

DEFAULTS = {
    "local_model": os.environ.get("OLLAMA_MODEL", "qwen3:8b"),
    "together_model": os.environ.get("TOGETHER_MODEL", "meta-llama/Llama-3.3-70B-Instruct-Turbo"),
    "default_provider": "local",
}


class PolicyError(Exception):
    pass


def cfg(key):
    return db.get_setting(key, DEFAULTS.get(key))


def together_key():
    return db.get_setting("together_api_key") or os.environ.get("TOGETHER_API_KEY", "")


def check_policy(project, provider):
    pol = project.get("ai_policy") or "local"
    if pol == "none":
        raise PolicyError("本任务设置为“不使用 AI”（期刊政策）。如需使用请先在任务设置中修改。")
    if provider == "together" and pol != "remote":
        raise PolicyError("本任务只允许本地 AI。如期刊允许，请在任务设置中把 AI 政策改为“允许 Together AI”。")


def endpoint(provider):
    if provider == "together":
        k = together_key()
        if not k:
            raise PolicyError("未设置 Together API key（设置页）。")
        return TOGETHER_URL, {"Authorization": f"Bearer {k}"}, cfg("together_model")
    return f"{OLLAMA_URL}/v1", {}, cfg("local_model")


def audit(project_id, provider, model, purpose, included, messages):
    payload = db.jdump(messages)
    db.ex("INSERT INTO audit_log(project_id,at,provider,model,purpose,included_manuscript,chars,payload)"
          " VALUES(?,?,?,?,?,?,?,?)",
          (project_id, db.now(), provider, model, purpose, int(bool(included)), len(payload), payload))


THINK_RE = re.compile(r"<think>.*?</think>\s*", re.S)


def strip_think(t: str) -> str:
    t = THINK_RE.sub("", t)
    if "<think>" in t and "</think>" not in t:   # still thinking (streaming)
        return ""
    return t


def stream_chat(project, provider, messages, purpose, included_manuscript, temperature=0.4):
    """Generator yielding text chunks. Raises PolicyError before anything is sent."""
    check_policy(project, provider)
    base, headers, model = endpoint(provider)
    audit(project["id"], provider, model, purpose, included_manuscript, messages)
    timeout = httpx.Timeout(connect=10, read=900, write=60, pool=10)
    if provider == "local":
        # Ollama native API: honours think=false (qwen3-style models answer directly)
        url = f"{OLLAMA_URL}/api/chat"
        body = {"model": model, "messages": messages, "stream": True, "think": False,
                "options": {"num_ctx": 16384, "temperature": temperature}}
    else:
        url = f"{base}/chat/completions"
        body = {"model": model, "messages": messages, "stream": True, "temperature": temperature,
                "max_tokens": 4096}
    in_think = False
    with httpx.Client(timeout=timeout) as cli:
        with cli.stream("POST", url, headers=headers, json=body) as r:
            if r.status_code >= 400:
                err = r.read().decode(errors="replace")[:500]
                raise RuntimeError(f"{provider} error {r.status_code}: {err}")
            for line in r.iter_lines():
                if provider == "local":
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    if d.get("error"):
                        raise RuntimeError(d["error"])
                    piece = (d.get("message") or {}).get("content") or ""
                    if d.get("done") and not piece:
                        break
                else:
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        delta = json.loads(data)["choices"][0].get("delta", {})
                    except Exception:
                        continue
                    piece = delta.get("content") or ""
                if not piece:
                    continue
                # drop <think> ... </think> blocks that some models emit inline
                if "<think>" in piece:
                    in_think = True
                    piece = piece.split("<think>")[0]
                if in_think:
                    if "</think>" in piece:
                        in_think = False
                        piece = piece.split("</think>", 1)[1]
                    else:
                        continue
                if piece:
                    yield piece


def complete(project, provider, messages, purpose, included_manuscript, temperature=0.3):
    return strip_think("".join(stream_chat(project, provider, messages, purpose,
                                           included_manuscript, temperature))).strip()


def list_models(provider):
    try:
        if provider == "together":
            r = httpx.get(f"{TOGETHER_URL}/models", headers={"Authorization": f"Bearer {together_key()}"},
                          timeout=20)
            d = r.json()
            d = d if isinstance(d, list) else d.get("data", [])
            return sorted(m["id"] for m in d if m.get("type") == "chat")
        r = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=10)
        return [m["name"] for m in r.json().get("models", [])]
    except Exception as e:
        return {"error": str(e)}


def pull_local_model(name):
    """Blocking pull of an Ollama model (used from a background job)."""
    with httpx.Client(timeout=None) as cli:
        with cli.stream("POST", f"{OLLAMA_URL}/api/pull", json={"name": name}) as r:
            last = {}
            for line in r.iter_lines():
                if line:
                    last = json.loads(line)
                    yield last
