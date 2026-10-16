// Shared helpers (plain script, exposes globals)
async function api(path, opts = {}) {
  const o = { method: opts.method || "GET", headers: { "X-Areview": "1" }, credentials: "same-origin" };
  if (opts.body instanceof FormData) o.body = opts.body;
  else if (opts.body !== undefined) { o.body = JSON.stringify(opts.body); o.headers["Content-Type"] = "application/json"; }
  const r = await fetch(path, o);
  if (r.status === 401) { location.href = "/login"; throw new Error("not logged in"); }
  if (opts.raw) return r;
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error((data && data.detail) || data || r.statusText);
  return data;
}

// Streams a text response; calls onChunk(fullTextSoFar). Returns full text.
async function apiStream(path, body, onChunk) {
  const r = await fetch(path, { method: "POST", credentials: "same-origin",
    headers: { "X-Areview": "1", "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (r.status === 401) { location.href = "/login"; return ""; }
  if (!r.ok) { let d; try { d = (await r.json()).detail; } catch { d = r.statusText; } throw new Error(d); }
  const rd = r.body.getReader(), dec = new TextDecoder();
  let full = "";
  for (;;) {
    const { value, done } = await rd.read();
    if (done) break;
    full += dec.decode(value, { stream: true });
    onChunk(stripThink(full));
  }
  return stripThink(full);
}
function stripThink(t) { t = t.replace(/<think>[\s\S]*?<\/think>\s*/g, ""); return t.includes("<think>") ? "" : t; }

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

function h(html) { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstElementChild; }

function toast(msg, err = false, ms = 2600) {
  const t = h(`<div class="toast ${err ? "err" : ""}">${esc(msg)}</div>`);
  document.body.appendChild(t); setTimeout(() => t.remove(), ms);
}

function modal(inner, { wide = false } = {}) {
  const bg = h(`<div class="modal-bg"><div class="modal ${wide ? "wide" : ""}"></div></div>`);
  const m = bg.firstElementChild;
  if (typeof inner === "string") m.innerHTML = inner; else m.appendChild(inner);
  bg.addEventListener("mousedown", (e) => { if (e.target === bg) close(); });
  const onKey = (e) => { if (e.key === "Escape") close(); };
  document.addEventListener("keydown", onKey);
  function close() { bg.remove(); document.removeEventListener("keydown", onKey); }
  document.body.appendChild(bg);
  return { el: m, close };
}

function confirmBox(title, html, okText = "确定") {
  return new Promise((res) => {
    const m = modal(`<h2>${esc(title)}</h2><div>${html}</div>
      <div class="actions"><button data-x>取消</button><button class="primary" data-ok>${esc(okText)}</button></div>`, { wide: true });
    $("[data-x]", m.el).onclick = () => { m.close(); res(false); };
    $("[data-ok]", m.el).onclick = () => { m.close(); res(true); };
  });
}

// Deadline colours: red when 7 days or less remain, orange up to 14, green otherwise.
function deadlineInfo(d) {
  if (!d) return { cls: "none", text: "无截止日期", days: null };
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const dd = new Date(d + "T00:00:00");
  const days = Math.round((dd - today) / 86400000);
  let cls = days < 0 ? "over" : days <= 7 ? "red" : days <= 14 ? "orange" : "green";
  const text = days < 0 ? `已逾期 ${-days} 天` : days === 0 ? "今天截止" : `剩 ${days} 天`;
  return { cls, text, days };
}
function deadlineBadge(d) {
  const i = deadlineInfo(d);
  return `<span class="dl ${i.cls}" title="${esc(d || "")}">${esc(i.text)}${d ? ` · ${esc(d)}` : ""}</span>`;
}

const STATUS = { new: "新任务", reading: "阅读中", drafting: "撰写中", submitted: "已提交", archived: "已归档" };
const POLICY = { none: "不使用 AI", local: "仅本地 AI", remote: "允许 Together AI" };
const POLICY_CLS = { none: "bad", local: "ok", remote: "warn" };
const REC = { "": "未定", accept: "Accept", minor: "Minor revision", major: "Major revision", reject: "Reject" };

function fmtTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}
function copyText(t) { navigator.clipboard.writeText(t).then(() => toast("已复制"), () => toast("复制失败", true)); }
