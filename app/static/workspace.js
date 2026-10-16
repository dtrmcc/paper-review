import { Viewer, COLORS } from "/static/viewer.js";
import { TOOL_LIST, renderTool } from "/static/toolviews.js";

const pid = +location.pathname.split("/")[2];
let project, docs = [], curDoc = null, viewer;

// ------------------------------------------------------------ project header
async function loadProject() {
  project = await api(`/api/projects/${pid}`);
  docs = project.docs;
  document.title = project.title + " · areview";
  $("#pTitle").textContent = project.title;
  $("#pJournal").textContent = [project.journal, project.manuscript_id].filter(Boolean).join(" · ");
  $("#pDeadline").innerHTML = deadlineBadge(project.deadline);
  $("#pPolicy").className = "pill " + POLICY_CLS[project.ai_policy];
  $("#pPolicy").textContent = "AI：" + POLICY[project.ai_policy];
  $("#pStatus").innerHTML = Object.entries(STATUS).map(([k, v]) => `<option value="${k}" ${project.status === k ? "selected" : ""}>${v}</option>`).join("");
  $("#pRec").innerHTML = Object.entries(REC).map(([k, v]) => `<option value="${k}" ${(project.recommendation || "") === k ? "selected" : ""}>${k ? v : "推荐结论：未定"}</option>`).join("");
  renderPolicy();
  renderProc();
}
async function patchProject(body) { await api(`/api/projects/${pid}`, { method: "PATCH", body }); await loadProject(); toast("已更新"); }
$("#pStatus").onchange = (e) => patchProject({ status: e.target.value });
$("#pRec").onchange = (e) => patchProject({ recommendation: e.target.value });
$("#pPolicy").onclick = () => openSettings();
$("#pSettings").onclick = () => openSettings();

function openSettings() {
  const p = project;
  const m = modal(`<h2>任务设置</h2><form id="sf">
    <label>标题</label><input type="text" name="title" value="${esc(p.title)}">
    <div class="row"><div class="grow"><label>期刊</label><input type="text" name="journal" value="${esc(p.journal || "")}"></div>
      <div><label>截止日期</label><input type="date" name="deadline" value="${esc(p.deadline || "")}"></div></div>
    <div class="row"><div class="grow"><label>稿件编号</label><input type="text" name="manuscript_id" value="${esc(p.manuscript_id || "")}"></div>
      <div><label>评审类型</label><select name="blind">${[["unknown", "未知"], ["single", "单盲"], ["double", "双盲"], ["open", "公开"]].map(([v, t]) => `<option value="${v}" ${p.blind === v ? "selected" : ""}>${t}</option>`).join("")}</select></div></div>
    <label>期刊主页 / Aims & Scope 网址</label><input type="url" name="journal_url" value="${esc(p.journal_url || "")}">
    <label>稿件作者（逗号分隔，用于自引检查；双盲可留空）</label><input type="text" name="author_names" value="${esc(p.author_names || "")}">
    <label>期刊 AI 政策</label>
    <select name="ai_policy">${Object.entries(POLICY).map(([k, v]) => `<option value="${k}" ${p.ai_policy === k ? "selected" : ""}>${v}</option>`).join("")}</select>
    <label>备注</label><textarea name="memo" rows="3">${esc(p.memo || "")}</textarea>
    <div class="actions"><button type="button" class="danger" data-del>删除整个任务</button><span class="spacer"></span><button type="button" data-x>取消</button><button class="primary">保存</button></div></form>`);
  $("[data-x]", m.el).onclick = m.close;
  $("[data-del]", m.el).onclick = async () => {
    if (!(await confirmBox("删除任务", `将永久删除“${esc(p.title)}”的稿件文件、笔记、标注、讨论和意见。无法恢复（备份除外）。`, "永久删除"))) return;
    await api(`/api/projects/${pid}`, { method: "DELETE" }); location.href = "/";
  };
  $("#sf", m.el).onsubmit = async (e) => {
    e.preventDefault();
    const body = Object.fromEntries(new FormData(e.target).entries());
    await patchProject(body); m.close();
  };
}

// ------------------------------------------------------------ documents & viewer
function renderDocs() {
  $("#docSel").innerHTML = docs.map((d) => `<option value="${d.id}" ${curDoc && d.id === curDoc.id ? "selected" : ""}>${esc(d.label)} · ${esc(d.filename)}</option>`).join("") || "<option>（无文件）</option>";
}
function renderProc() {
  const j = (project.jobs || [])[0];
  const el = $("#procState");
  if (!j) { el.textContent = ""; return; }
  el.textContent = j.status === "running" ? `后台：${j.kind === "process" ? "处理稿件 / 文献核查" : "文献核查"} ${j.progress || ""}` : j.status === "failed" ? "后台处理失败：" + (j.error || "") : "";
  if (j.status === "running") setTimeout(async () => { project.jobs = (await api(`/api/projects/${pid}`)).jobs; renderProc(); }, 4000);
}
async function openDoc(id) {
  curDoc = docs.find((d) => d.id === id);
  if (!curDoc) return;
  renderDocs();
  $("#dlOrig").href = `/files/doc/${id}/original`;
  const n = await viewer.open(id);
  $("#pgTot").textContent = "/ " + n;
  try { localStorage.setItem("areview-doc-" + pid, id); } catch {}
}
$("#docSel").onchange = (e) => openDoc(+e.target.value);

viewer = new Viewer($("#viewer"), {
  loadAnnotations: (did) => api(`/api/documents/${did}/annotations`),
  saveAnnotation: (a) => api(`/api/documents/${viewer.docId}/annotations`, { method: "POST", body: a }),
  updateAnnotation: (id, b) => api(`/api/annotations/${id}`, { method: "PATCH", body: b }),
  deleteAnnotation: (id) => api(`/api/annotations/${id}`, { method: "DELETE" }),
  onChange: () => renderAnns(),
  onToNotes: (a) => appendToNotes(a),
  onPage: (n) => { if (document.activeElement !== $("#pgIn")) $("#pgIn").value = n; try { localStorage.setItem(`areview-pg-${pid}-${curDoc.id}`, n); } catch {} },
  onScale: (s) => $("#zLvl").textContent = Math.round(s * 100) + "%",
  onNoteModeEnd: () => { $("#noteBtn").classList.remove("active"); $("#viewer").classList.remove("note-mode"); },
});
$("#zIn").onclick = () => viewer.setScale(viewer.scale * 1.15);
$("#zOut").onclick = () => viewer.setScale(viewer.scale / 1.15);
$("#zFit").onclick = () => viewer.fitWidth();
$("#pgIn").onchange = (e) => viewer.scrollToPage(+e.target.value);
$("#noteBtn").onclick = () => {
  viewer.noteMode = !viewer.noteMode;
  $("#noteBtn").classList.toggle("active", viewer.noteMode);
  $("#viewer").classList.toggle("note-mode", viewer.noteMode);
  if (viewer.noteMode) toast("点击页面任意位置放置便签");
};
let findHits = [], findIdx = 0;
$("#findIn").addEventListener("keydown", async (e) => {
  if (e.key !== "Enter") return;
  const term = e.target.value;
  if (term !== viewer.searchTermRaw) {
    viewer.searchTermRaw = term;
    findHits = await viewer.search(term); findIdx = 0;
  } else findIdx = (findIdx + 1) % Math.max(1, findHits.length);
  const tot = findHits.reduce((s, h) => s + h.count, 0);
  $("#findRes").textContent = term ? (tot ? `${tot} 处 · 第 ${findHits[findIdx].page} 页 (${findIdx + 1}/${findHits.length} 页)` : "无结果") : "";
  if (findHits.length) viewer.scrollToPage(findHits[findIdx].page);
});

$("#upVer").onclick = () => {
  const ms = docs.filter((d) => d.kind === "manuscript").length;
  const m = modal(`<h2>上传文件</h2><form id="uf">
    <label>类型</label><select name="kind"><option value="manuscript">稿件新版本（修改稿）</option><option value="response">作者回复信</option><option value="supplement">补充材料</option></select>
    <label>标签</label><input type="text" name="label" value="R${ms}">
    <label>文件（PDF / Word）</label><input type="file" name="file" accept=".pdf,.doc,.docx,.odt,.rtf" required>
    <div class="actions"><button type="button" data-x>取消</button><button class="primary">上传</button></div></form>`);
  $("[name=kind]", m.el).onchange = (e) => $("[name=label]", m.el).value = { manuscript: "R" + ms, response: "Response letter", supplement: "Supplement" }[e.target.value];
  $("[data-x]", m.el).onclick = m.close;
  $("#uf", m.el).onsubmit = async (e) => {
    e.preventDefault();
    const b = $("button.primary", m.el); b.disabled = true; b.textContent = "上传中…";
    try {
      const r = await api(`/api/projects/${pid}/documents`, { method: "POST", body: new FormData(e.target) });
      m.close(); await loadProject(); await openDoc(r.id);
    } catch (err) { toast(err.message, true, 5000); b.disabled = false; b.textContent = "上传"; }
  };
};

// split drag
(() => {
  const sp = $("#split"), left = $("#left");
  let saved; try { saved = localStorage.getItem("areview-split"); } catch {}
  if (saved) left.style.flexBasis = saved;
  sp.onmousedown = (e) => {
    e.preventDefault(); sp.classList.add("drag");
    const move = (ev) => { const pct = Math.min(80, Math.max(30, ev.clientX / window.innerWidth * 100)); left.style.flexBasis = pct + "%"; };
    const up = () => { sp.classList.remove("drag"); document.removeEventListener("mousemove", move); document.removeEventListener("mouseup", up);
      try { localStorage.setItem("areview-split", left.style.flexBasis); } catch {} };
    document.addEventListener("mousemove", move); document.addEventListener("mouseup", up);
  };
})();

// ------------------------------------------------------------ tabs
$$(".tabs button").forEach((b) => b.onclick = () => showTab(b.dataset.tab));
function showTab(t) {
  $$(".tabs button").forEach((x) => x.classList.toggle("on", x.dataset.tab === t));
  $$(".tab").forEach((x) => x.classList.toggle("on", x.id === "tab-" + t));
  if (t === "chat") loadChat();
  if (t === "review") loadReviews();
  if (t === "anns") renderAnns();
  try { localStorage.setItem("areview-tab", t); } catch {}
}

// ------------------------------------------------------------ notes (autosave)
const notesEl = $("#notes");
let lastSaved = "", saving = false, dirty = false, saveTimer;
async function loadNotes() {
  const n = await api(`/api/projects/${pid}/notes`);
  notesEl.value = lastSaved = n.content || "";
  setSave(n.updated ? "已保存 " + fmtTime(n.updated) : "", "ok");
}
function setSave(t, cls = "") { const s = $("#saveState"); s.textContent = t; s.className = "small " + cls; }
async function saveNotes() {
  if (saving) { saveTimer = setTimeout(saveNotes, 500); return; }
  const v = notesEl.value;
  if (v === lastSaved) { dirty = false; return; }
  saving = true; setSave("保存中…");
  try {
    await api(`/api/projects/${pid}/notes`, { method: "PUT", body: { content: v } });
    lastSaved = v; dirty = notesEl.value !== v;
    setSave("已保存 " + new Date().toLocaleTimeString("zh-CN"), "ok");
  } catch (e) {
    setSave("未保存！重试中…", "bad");
    saveTimer = setTimeout(saveNotes, 5000);
  } finally { saving = false; }
}
notesEl.addEventListener("input", () => { dirty = true; setSave("未保存…"); clearTimeout(saveTimer); saveTimer = setTimeout(saveNotes, 1200); });
setInterval(() => { if (dirty) saveNotes(); }, 20000);
notesEl.addEventListener("blur", () => dirty && saveNotes());
document.addEventListener("keydown", (e) => { if ((e.ctrlKey || e.metaKey) && e.key === "s") { e.preventDefault(); saveNotes(); } });
window.addEventListener("beforeunload", (e) => { if (notesEl.value !== lastSaved) { saveNotes(); e.preventDefault(); e.returnValue = ""; } });
function appendToNotes(a) {
  const line = `[p.${a.page}] ` + (a.quote ? `“${a.quote.slice(0, 400)}”` : "") + (a.comment ? ` — ${a.comment}` : " — ");
  notesEl.value = notesEl.value.replace(/\s*$/, "") + (notesEl.value.trim() ? "\n" : "") + line + "\n";
  dirty = true; saveNotes();
  showTab("notes"); notesEl.focus(); notesEl.setSelectionRange(notesEl.value.length, notesEl.value.length);
  notesEl.scrollTop = notesEl.scrollHeight;
}
$("#histBtn").onclick = async () => {
  const hs = await api(`/api/projects/${pid}/notes/history`);
  const m = modal(`<h2>笔记历史版本</h2><p class="small muted">每 5 分钟最多保存一个快照；大段删除时也会额外保存。</p>
    <div style="display:flex;gap:10px"><div style="width:200px;max-height:60vh;overflow:auto">${hs.map((x, i) => `<div class="rv" data-i="${i}">${new Date(x.saved_at).toLocaleString("zh-CN")}<div class="muted">${x.chars} 字符</div></div>`).join("") || "暂无"}</div>
    <pre class="payload" id="hPrev" style="flex:1;min-height:200px"></pre></div>
    <div class="actions"><button data-x>关闭</button><button class="primary" data-r disabled>恢复此版本</button></div>`, { wide: true });
  let sel = null;
  $$(".rv", m.el).forEach((r) => r.onclick = () => { sel = hs[+r.dataset.i]; $("#hPrev", m.el).textContent = sel.content; $("[data-r]", m.el).disabled = false; });
  $("[data-x]", m.el).onclick = m.close;
  $("[data-r]", m.el).onclick = async () => { notesEl.value = sel.content; dirty = true; await saveNotes(); m.close(); toast("已恢复（当前内容也保留在历史中）"); };
};

// ------------------------------------------------------------ AI policy & providers
function renderPolicy() {
  const p = project.ai_policy;
  const txt = { none: "本任务设置为“不使用 AI”（期刊政策）。AI 讨论和生成意见已禁用。", local: "仅本地 AI：所有内容只在本机模型中处理，不会离开这台电脑。", remote: "允许 Together AI：选择 Together 时，每次发送前都会显示将发送的完整内容并要求确认。本地模型同样可用。" }[p];
  ["#chatPolicy", "#revPolicy"].forEach((s) => { $(s).className = "policybar " + p; $(s).textContent = txt; });
  const opts = p === "none" ? [] : p === "local" ? [["local", "本地模型"]] : [["local", "本地模型"], ["together", "Together AI"]];
  ["#chatProv", "#revProv"].forEach((s) => {
    const cur = $(s).value;
    $(s).innerHTML = opts.map(([v, t]) => `<option value="${v}">${t}</option>`).join("");
    if (opts.some((o) => o[0] === cur)) $(s).value = cur;
    else if (window.defaultProvider && opts.some((o) => o[0] === window.defaultProvider)) $(s).value = window.defaultProvider;
  });
  const dis = p === "none";
  ["#chatSend", "#revGen", "#chatIn"].forEach((s) => $(s).disabled = dis);
  ["#chatMs", "#revMs"].forEach((s) => { $(s).disabled = !project.has_ai_context || dis; if ($(s).disabled) $(s).checked = false;
    $(s).parentElement.title = project.has_ai_context ? "" : "先在工具箱“准备发送给 AI 的文本”中保存遮挡后的文本"; });
}

async function confirmRemote(url, body) {
  if (body.provider !== "together") return true;
  const pv = await api(url, { method: "POST", body: { ...body, preview: true } });
  const txt = pv.messages.map((m) => `=== ${m.role} ===\n${m.content}`).join("\n\n");
  return confirmBox("确认发送到 Together AI", `<p>将发送 <b>${pv.chars.toLocaleString()}</b> 个字符到 Together AI${body.include_manuscript ? "，<b style='color:var(--bad)'>包含遮挡后的稿件文本</b>" : "（不含稿件文本）"}。以下是完整内容：</p><pre class="payload">${esc(txt)}</pre>`, "确认发送");
}

// ------------------------------------------------------------ chat
async function loadChat() {
  const rows = await api(`/api/projects/${pid}/chat`);
  $("#msgs").innerHTML = rows.map(msgHtml).join("") || `<div class="muted small">还没有讨论。可以把你的零碎想法发过来，让 AI 帮你推敲。</div>`;
  $("#msgs").scrollTop = $("#msgs").scrollHeight;
}
function msgHtml(r) {
  return `<div class="msg ${r.role}">${esc(r.content)}<div class="meta">${fmtTime(r.created)}${r.role === "assistant" ? " · " + (r.provider === "together" ? "Together" : "本地") + (r.model ? " " + esc(r.model) : "") : ""}${r.included_manuscript ? " · 含稿件" : ""}</div></div>`;
}
async function sendChat() {
  const msg = $("#chatIn").value.trim();
  if (!msg) return;
  const body = { message: msg, provider: $("#chatProv").value, include_manuscript: $("#chatMs").checked, include_notes: $("#chatNotes").checked };
  try { if (!(await confirmRemote(`/api/projects/${pid}/chat`, body))) return; } catch (e) { toast(e.message, true, 5000); return; }
  $("#chatIn").value = "";
  if (!$("#msgs .msg")) $("#msgs").innerHTML = "";
  $("#msgs").insertAdjacentHTML("beforeend", msgHtml({ role: "user", content: msg, created: new Date().toISOString() }));
  const out = h(`<div class="msg assistant"><span class="muted">思考中…${body.provider === "local" ? "（本地模型在 CPU 上运行，可能需要几十秒）" : ""}</span></div>`);
  $("#msgs").appendChild(out); $("#msgs").scrollTop = $("#msgs").scrollHeight;
  $("#chatSend").disabled = true;
  try {
    await apiStream(`/api/projects/${pid}/chat`, body, (t) => { if (t) { out.textContent = t; $("#msgs").scrollTop = $("#msgs").scrollHeight; } });
    loadChat();
  } catch (e) { out.innerHTML = `<span style="color:var(--bad)">${esc(e.message)}</span>`; }
  finally { $("#chatSend").disabled = project.ai_policy === "none"; }
}
$("#chatSend").onclick = sendChat;
$("#chatIn").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) sendChat(); });
$("#chatClear").onclick = async () => {
  if (!(await confirmBox("清空对话", "删除本任务的全部 AI 讨论记录？"))) return;
  await api(`/api/projects/${pid}/chat`, { method: "DELETE" }); loadChat();
};

// ------------------------------------------------------------ review drafting
let revKind = "authors", reviews = [];
$$("#revKind button").forEach((b) => b.onclick = () => { revKind = b.dataset.k; $$("#revKind button").forEach((x) => x.classList.toggle("on", x === b)); showLatest(); });
async function loadReviews() {
  reviews = await api(`/api/projects/${pid}/reviews`);
  $("#revList").innerHTML = reviews.map((r, i) => `<div class="rv" data-i="${i}"><b>${r.kind === "editor" ? "给编辑" : "给作者"}</b> · ${new Date(r.created).toLocaleString("zh-CN")} · ${r.provider === "manual" ? "手动修改" : (r.provider === "together" ? "Together " : "本地 ") + esc(r.model || "")}
    <div class="muted">${esc(r.content.slice(0, 140))}…</div></div>`).join("") || "<span class='muted'>暂无</span>";
  $$("#revList .rv").forEach((el) => el.onclick = () => { const r = reviews[+el.dataset.i]; $("#revOut").value = r.content; revInfo(r); });
  if (!$("#revOut").value) showLatest();
}
function showLatest() { const r = reviews.find((x) => x.kind === revKind); $("#revOut").value = r ? r.content : ""; revInfo(r); }
function revInfo(r) { $("#revInfo").textContent = r ? `${r.kind === "editor" ? "给编辑" : "给作者"} · ${new Date(r.created).toLocaleString("zh-CN")} · ${r.content.split(/\s+/).length} 词` : ""; }
$("#revGen").onclick = async () => {
  if (notesEl.value !== lastSaved) await saveNotes();
  const body = { kind: revKind, provider: $("#revProv").value, include_manuscript: $("#revMs").checked, instructions: $("#revInstr").value };
  try { if (!(await confirmRemote(`/api/projects/${pid}/review/draft`, body))) return; } catch (e) { toast(e.message, true, 5000); return; }
  $("#revGen").disabled = true; $("#revOut").value = ""; $("#revInfo").textContent = "生成中…" + (body.provider === "local" ? "（本地模型较慢，请稍候）" : "");
  try {
    await apiStream(`/api/projects/${pid}/review/draft`, body, (t) => { $("#revOut").value = t; $("#revOut").scrollTop = $("#revOut").scrollHeight; });
    await loadReviews(); showLatest(); toast("已生成并保存");
  } catch (e) { toast(e.message, true, 6000); }
  finally { $("#revGen").disabled = project.ai_policy === "none"; }
};
$("#revCopy").onclick = () => copyText($("#revOut").value);
$("#revSave").onclick = async () => {
  if (!$("#revOut").value.trim()) return;
  await api(`/api/projects/${pid}/reviews`, { method: "POST", body: { kind: revKind, content: $("#revOut").value } });
  await loadReviews(); toast("已保存为新版本");
};

// ------------------------------------------------------------ toolbox
$("#toolHome").innerHTML = TOOL_LIST.map((t) => `<div class="tcard" data-t="${t.id}"><b>${t.icon} ${t.name}</b><p>${t.desc}</p></div>`).join("");
$$(".tcard").forEach((c) => c.onclick = () => openTool(c.dataset.t));
$("#toolBack").onclick = () => { $("#toolView").classList.add("hidden"); $("#toolHome").classList.remove("hidden"); };
function toolCtx() {
  const ms = docs.filter((d) => d.kind === "manuscript");
  const mainDoc = curDoc && curDoc.kind === "manuscript" ? curDoc : ms[ms.length - 1];
  return { pid, project, docs, mainDoc, api, toast, jump: (n) => { if (mainDoc && curDoc && curDoc.id !== mainDoc.id) openDoc(mainDoc.id).then(() => viewer.scrollToPage(n)); else viewer.scrollToPage(n); },
    refresh: loadProject };
}
function openTool(id) {
  const t = TOOL_LIST.find((x) => x.id === id);
  $("#toolHome").classList.add("hidden"); $("#toolView").classList.remove("hidden");
  $("#toolName").textContent = t.icon + " " + t.name;
  $("#toolPop").href = id === "refs" ? `/p/${pid}/refs` : `/p/${pid}/tool/${id}`;
  const body = document.createElement("div");
  $("#toolBody").replaceChildren(body);
  renderTool(id, toolCtx(), body);
}

// ------------------------------------------------------------ annotation list
function renderAnns() {
  const anns = viewer.anns || [];
  $("#annCount").textContent = anns.length || "";
  $("#annList").innerHTML = anns.slice().sort((a, b) => a.page - b.page || (a.rects[0]?.[1] ?? 0) - (b.rects[0]?.[1] ?? 0)).map((a) =>
    `<div class="annitem" data-id="${a.id}" style="border-color:${COLORS[a.color] || a.color || "#ccc"}"><span class="small muted">p.${a.page} · ${{ highlight: "高亮", underline: "下划线", strike: "删除线", note: "便签" }[a.type] || a.type}</span>
      ${a.quote ? `<div class="q">“${esc(a.quote.slice(0, 200))}”</div>` : ""}${a.comment ? `<div>${esc(a.comment)}</div>` : ""}</div>`).join("") || "<p class='muted'>还没有标注。在左侧选中文字即可高亮或批注。</p>";
  $$(".annitem").forEach((el) => el.onclick = () => viewer.flashAnn(viewer.anns.find((a) => a.id === +el.dataset.id)));
}
$("#annsToNotes").onclick = () => {
  const lines = viewer.anns.filter((a) => a.comment).sort((a, b) => a.page - b.page)
    .map((a) => `[p.${a.page}] ` + (a.quote ? `“${a.quote.slice(0, 300)}” — ` : "") + a.comment);
  if (!lines.length) return toast("没有带批注的标注");
  notesEl.value = notesEl.value.replace(/\s*$/, "") + "\n\n— 标注批注 —\n" + lines.join("\n") + "\n";
  dirty = true; saveNotes(); showTab("notes");
};

// ------------------------------------------------------------ init
(async () => {
  try { window.defaultProvider = (await api("/api/settings")).default_provider; } catch {}
  await loadProject();
  await loadNotes();
  let start;
  try { start = +localStorage.getItem("areview-doc-" + pid); } catch {}
  const ms = docs.filter((d) => d.kind === "manuscript");
  const first = docs.find((d) => d.id === start) || ms[ms.length - 1] || docs[0];
  renderDocs();
  if (first) {
    await openDoc(first.id);
    let pg; try { pg = +localStorage.getItem(`areview-pg-${pid}-${first.id}`); } catch {}
    if (pg > 1) setTimeout(() => viewer.scrollToPage(pg, 0, false), 300);
    renderAnns();
  } else viewer.inner.innerHTML = `<p style="padding:30px" class="muted">还没有稿件。点击上方“⤒ 上传”导入 PDF 或 Word。</p>`;
  let tab; try { tab = localStorage.getItem("areview-tab"); } catch {}
  showTab(tab || "notes");
})();
