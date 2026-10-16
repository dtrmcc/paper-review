// Toolbox: each tool runs only when clicked. Shared by workspace side panel and /p/{id}/tool/{name}.
export const TOOL_LIST = [
  { id: "refs", icon: "📚", name: "文献核查", desc: "参考文献真实性与准确性：Crossref / OpenAlex / scite（校园网）+ Google Scholar 链接。导入后自动在后台运行。" },
  { id: "stats", icon: "📊", name: "文稿统计", desc: "页数、字数、各章节字数、摘要字数、图表数量、可读性、PDF 元数据（双盲泄露检查）。" },
  { id: "citations", icon: "🔗", name: "引用一致性", desc: "正文引用 vs 参考文献表：未被引用的条目、无法对应的正文引用、重复条目。" },
  { id: "retractions", icon: "⛔", name: "撤稿 / 更正检测", desc: "汇总参考文献中被撤稿、更正或有关注声明的条目（来自文献核查结果）。" },
  { id: "profile", icon: "🧭", name: "自引与文献年代", desc: "自引比例（姓氏匹配），参考文献年代分布、近 5 年比例、老旧文献比例。" },
  { id: "statcheck", icon: "🧮", name: "统计数值复核", desc: "识别 t / F / χ² / r / z 检验报告，根据统计量和自由度重算 p 值，标出不一致。" },
  { id: "figures", icon: "🖼", name: "图表一览", desc: "截取所有图和表（按图注位置），集中浏览，点击跳转原文。" },
  { id: "diff", icon: "🔀", name: "版本对比", desc: "修改稿与上一版逐段对比（新增 / 删除 / 修改）。需要上传 R1 等新版本。" },
  { id: "responses", icon: "✅", name: "修改回复追踪", desc: "上一轮审稿意见逐条对照作者回复，标记已解决 / 部分 / 未解决。" },
  { id: "journal", icon: "🏛", name: "期刊 Aims & Scope", desc: "抓取期刊网页的宗旨与范围，判断稿件是否匹配。可手动粘贴。" },
  { id: "redact", icon: "🕶", name: "准备发送给 AI 的文本", desc: "提取全文，自动遮挡邮箱/网址/ORCID/作者等，人工检查后保存；只在 AI 对话中勾选时才会使用。" },
  { id: "audit", icon: "🧾", name: "AI 调用记录", desc: "本任务每次 AI 调用的提供方、模型、是否含稿件、发送的完整内容。" },
];

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pageLink = (n) => n ? `<a href="#" data-page="${n}">p.${n}</a>` : "";

export async function renderTool(id, ctx, el) {
  // ctx: { pid, project, docs, mainDoc, api, toast, jump(page), full }
  const R = RENDER[id];
  el.innerHTML = `<div class="muted">加载中…</div>`;
  try { await R(ctx, el); }
  catch (e) { el.innerHTML = `<div class="pill bad">${esc(e.message)}</div>`; }
  el.addEventListener("click", (e) => {
    const a = e.target.closest("[data-page]");
    if (a) { e.preventDefault(); ctx.jump(+a.dataset.page); }
  });
}

async function runOrShow(ctx, el, tool, body, draw, { autoRun = false } = {}) {
  const prev = await ctx.api(`/api/projects/${ctx.pid}/tools/${tool}`);
  const head = (when) => `<div class="row tool-head"><span class="muted small">${when ? "上次运行：" + new Date(when).toLocaleString("zh-CN") : "尚未运行"}</span><span class="spacer"></span><button class="small primary" data-run>${when ? "重新运行" : "运行"}</button></div>`;
  const show = (res, when) => {
    el.innerHTML = head(when) + `<div class="tool-body"></div>`;
    draw(res, el.querySelector(".tool-body"));
    el.querySelector("[data-run]").onclick = run;
  };
  const run = async () => {
    el.innerHTML = head(null) + `<div class="muted">运行中…</div>`;
    try {
      const r = await ctx.api(`/api/projects/${ctx.pid}/tools/${tool}`, { method: "POST", body: body() });
      show(r.result, r.created);
    } catch (e) { el.innerHTML = head(null) + `<div class="pill bad">${esc(e.message)}</div>`; el.querySelector("[data-run]").onclick = run; }
  };
  if (prev && prev.result) show(prev.result, prev.created);
  else if (autoRun) run();
  else { el.innerHTML = head(null); el.querySelector("[data-run]").onclick = run; }
}

const body = (ctx) => () => ({ document_id: ctx.mainDoc && ctx.mainDoc.id });
const errBox = (r) => r.error ? `<div class="pill bad">${esc(r.error)}</div>` : "";

const RENDER = {
  async refs(ctx, el) {
    if (!ctx.mainDoc) { el.innerHTML = "没有稿件"; return; }
    const d = await ctx.api(`/api/documents/${ctx.mainDoc.id}/refs`);
    const c = (s) => d.refs.filter((r) => r.status === s).length;
    const j = d.job;
    el.innerHTML = `
      <div class="kv">
        <div>参考文献</div><div>${d.refs.length} 条（${esc(ctx.mainDoc.label)}）</div>
        <div>已核实</div><div><span class="pill ok">${c("ok")}</span></div>
        <div>信息不符</div><div><span class="pill warn">${c("mismatch")}</span></div>
        <div>未找到</div><div><span class="pill bad">${c("notfound")}</span></div>
        <div>撤稿</div><div><span class="pill dark">${c("retracted")}</span></div>
        <div>待核查</div><div>${c("pending") + c("error")}</div>
        <div>scite</div><div>${d.scite ? "已配置（经本机校园网）" : "<span class='muted'>未配置 token（设置页）</span>"}</div>
        <div>后台任务</div><div>${j ? esc(j.status) + " " + esc(j.progress || "") + (j.error ? " · " + esc(j.error) : "") : "—"}</div>
      </div>
      <p><a class="btn-like" href="/p/${ctx.pid}/refs" target="_blank">打开文献核查页面 ↗</a></p>`;
  },

  async stats(ctx, el) {
    await runOrShow(ctx, el, "stats", body(ctx), (r, b) => {
      b.innerHTML = errBox(r) + (r.warnings || []).map((w) => `<div class="pill warn" style="white-space:normal;margin:2px 0">⚠ ${esc(w)}</div>`).join("") + `
        <div class="kv" style="margin-top:8px">
          ${r.title ? `<div>标题</div><div>${esc(r.title)}</div>` : ""}
          <div>页数</div><div>${r.pages}</div><div>总词数</div><div>${r.words?.toLocaleString()}</div>
          <div>字符数</div><div>${r.characters?.toLocaleString()}</div>
          ${r.abstract_words != null ? `<div>摘要词数</div><div>${r.abstract_words}</div>` : ""}
          <div>图 / 表</div><div>${r.figures} / ${r.tables}</div>
          ${r.references != null ? `<div>参考文献</div><div>${r.references}</div>` : ""}
          <div>预计阅读</div><div>${r.reading_minutes} 分钟</div>
          <div>Flesch 易读度</div><div>${r.readability?.flesch_reading_ease}（越低越难，学术文常见 10–40）</div>
          <div>FK 年级</div><div>${r.readability?.flesch_kincaid_grade}</div>
          <div>平均句长</div><div>${r.readability?.avg_sentence_length} 词</div>
          <div>文件大小</div><div>${r.file_size_kb} KB</div>
        </div>
        ${r.sections?.length ? `<h4>各章节词数</h4><table class="grid">${r.sections.map((s) => `<tr><td>${esc(s.head)}</td><td style="text-align:right">${s.words}</td></tr>`).join("")}</table>` : ""}
        ${Object.keys(r.pdf_metadata || {}).length ? `<h4>PDF 元数据</h4><div class="kv">${Object.entries(r.pdf_metadata).map(([k, v]) => `<div>${esc(k)}</div><div>${esc(v)}</div>`).join("")}</div>` : ""}`;
    }, { autoRun: true });
  },

  async citations(ctx, el) {
    await runOrShow(ctx, el, "citations", body(ctx), (r, b) => {
      if (r.error) { b.innerHTML = errBox(r); return; }
      b.innerHTML = `<div class="kv"><div>正文引用</div><div>${r.in_text_citations} 处</div><div>参考文献</div><div>${r.references} 条</div><div>被引用条目</div><div>${r.cited_references}</div></div>
        <h4>参考文献表中未被正文引用（${r.never_cited.length}）</h4>
        ${r.never_cited.length ? `<ol class="small">${r.never_cited.map((x) => `<li value="${x.idx}">${esc(x.ref)}</li>`).join("")}</ol>` : "<div class='pill ok'>无</div>"}
        <h4>无法对应到参考文献的正文引用（${r.unresolved_in_text.length}）</h4>
        ${r.unresolved_in_text.length ? `<div class="small">${r.unresolved_in_text.map((x) => `<span class="pill" style="margin:2px">${esc(x.text)}${x.count > 1 ? " ×" + x.count : ""}</span>`).join("")}</div>` : "<div class='pill ok'>无</div>"}
        ${r.duplicate_references.length ? `<h4>可能重复的条目</h4><ul class="small">${r.duplicate_references.map((t) => `<li>${esc(t)}</li>`).join("")}</ul>` : ""}
        <p class="muted small">${esc(r.note)}</p>`;
    });
  },

  async retractions(ctx, el) {
    await runOrShow(ctx, el, "retractions", body(ctx), (r, b) => {
      if (r.error) { b.innerHTML = errBox(r); return; }
      b.innerHTML = `<div class="kv"><div>已核查</div><div>${r.checked}</div><div>待核查</div><div>${r.pending}</div></div>
        ${r.pending ? "<p class='muted small'>文献核查仍在进行，完成后请重新运行。</p>" : ""}
        <h4>有撤稿 / 更正 / 声明的文献（${r.flagged.length}）</h4>
        ${r.flagged.length ? `<table class="grid small">${r.flagged.map((f) => `<tr><td>[${f.idx}]</td><td>${esc(f.ref)}<br>
          ${f.status === "retracted" ? "<span class='pill dark'>撤稿</span>" : ""}
          ${f.notices.map((n) => `<span class="pill warn">${esc(n.type)}</span> <a href="https://doi.org/${esc(n.notice_doi)}" target="_blank" rel="noreferrer">声明</a>`).join(" ")}
          ${(f.scite_notices || []).map((n) => `<span class="pill warn">scite: ${esc(typeof n === "string" ? n : (n.status || n.type || JSON.stringify(n)).slice(0, 60))}</span>`).join(" ")}</td></tr>`).join("")}</table>` : "<div class='pill ok'>未发现</div>"}`;
    });
  },

  async profile(ctx, el) {
    await runOrShow(ctx, el, "profile", body(ctx), (r, b) => {
      if (r.error) { b.innerHTML = errBox(r); return; }
      const max = Math.max(1, ...r.years.map((y) => y[1]));
      b.innerHTML = `<div class="kv">
          <div>用于匹配的作者</div><div>${r.manuscript_authors_used.length ? esc(r.manuscript_authors_used.join(", ")) : "<span class='muted'>未知（双盲？可在任务设置中填写）</span>"}</div>
          <div>自引</div><div>${r.self_citations.length} 条（${r.self_citation_rate}%）</div>
          <div>中位年份</div><div>${r.median_year ?? "—"}</div>
          <div>近 5 年</div><div>${r.pct_last_5y ?? "—"}%</div>
          <div>超过 10 年</div><div>${r.pct_older_10y ?? "—"}%</div>
          <div>无年份</div><div>${r.no_year}</div></div>
        <h4>年代分布</h4>
        <div class="yearbars">${r.years.map(([y, n]) => `<div title="${y}: ${n}"><i style="height:${(n / max) * 100}%"></i><span>${String(y).slice(2)}</span></div>`).join("")}</div>
        ${r.self_citations.length ? `<h4>可能的自引</h4><ol class="small">${r.self_citations.map((s) => `<li value="${s.idx}">${esc(s.title)} <span class="muted">(${esc(s.matched.join(", "))})</span></li>`).join("")}</ol>` : ""}
        <p class="muted small">${esc(r.note)}</p>`;
    });
  },

  async statcheck(ctx, el) {
    await runOrShow(ctx, el, "statcheck", body(ctx), (r, b) => {
      b.innerHTML = `<div class="kv"><div>识别到的检验</div><div>${r.found}</div><div>p 值不一致</div><div>${r.inconsistent ? `<span class="pill warn">${r.inconsistent}</span>` : 0}</div>
          <div>影响显著性结论</div><div>${r.decision_errors ? `<span class="pill bad">${r.decision_errors}</span>` : 0}</div></div>
        ${r.results.length ? `<table class="grid small" style="margin-top:8px"><tr><th>页</th><th>原文</th><th>报告</th><th>重算 p</th><th></th></tr>
          ${r.results.sort((a, b) => a.consistent - b.consistent).map((x) => `<tr><td>${pageLink(x.page)}</td><td><code>${esc(x.text)}</code></td><td>${esc(x.reported)}</td><td>${x.computed_p}</td>
          <td>${x.consistent ? "<span class='pill ok'>✓</span>" : x.decision_error ? "<span class='pill bad'>结论错误</span>" : "<span class='pill warn'>不一致</span>"}</td></tr>`).join("")}</table>` : ""}
        <p class="muted small">${esc(r.note)}</p>`;
    });
  },

  async figures(ctx, el) {
    await runOrShow(ctx, el, "figures", body(ctx), (r, b) => {
      b.innerHTML = `<div class="figgrid">${r.items.map((f) => `<figure><img loading="lazy" src="/files/doc/${r.document_id}/fig/${f.img}">
        <figcaption><b>${esc(f.label)}</b> · ${pageLink(f.page)}<br><span class="muted">${esc(f.caption.slice(0, 220))}</span></figcaption></figure>`).join("") || "未找到图注"}</div>
        <p class="muted small">${esc(r.note)}</p>`;
      b.querySelectorAll("img").forEach((img) => img.onclick = () => window.open(img.src, "_blank"));
    });
  },

  async diff(ctx, el) {
    const ms = ctx.docs.filter((d) => d.kind === "manuscript");
    if (ms.length < 2) { el.innerHTML = `<p>需要至少两个稿件版本。点击顶部“上传新版本”导入修改稿（R1）。</p>`; return; }
    const opt = (sel) => ms.map((d) => `<option value="${d.id}" ${d.id === sel ? "selected" : ""}>${esc(d.label)} · ${esc(d.filename)}</option>`).join("");
    el.innerHTML = `<div class="row wrap"><select id="dOld" style="width:auto">${opt(ms[ms.length - 2].id)}</select> → <select id="dNew" style="width:auto">${opt(ms[ms.length - 1].id)}</select><button class="primary small" id="dRun">对比</button></div><div id="dOut"></div>`;
    const draw = (r) => {
      el.querySelector("#dOut").innerHTML = `<p>${esc(r.old)} → ${esc(r.new)}：相似度 ${r.similarity}% · 新增 ${r.added} 段 · 删除 ${r.removed} 段 · 修改 ${r.changed} 处</p>
        <div class="difflist">${r.blocks.map((b) => `<div class="dblock ${b.type}"><span class="pill ${b.type === "added" ? "ok" : b.type === "removed" ? "bad" : "warn"}">${{ added: "新增", removed: "删除", changed: "修改" }[b.type]}</span> ${b.html}</div>`).join("")}</div>`;
    };
    const prev = await ctx.api(`/api/projects/${ctx.pid}/tools/diff`);
    if (prev && prev.result) draw(prev.result);
    el.querySelector("#dRun").onclick = async () => {
      el.querySelector("#dOut").innerHTML = "<div class='muted'>对比中…</div>";
      const r = await ctx.api(`/api/projects/${ctx.pid}/tools/diff`, { method: "POST", body: { old_id: +el.querySelector("#dOld").value, new_id: +el.querySelector("#dNew").value } });
      draw(r.result);
    };
  },

  async responses(ctx, el) {
    const load = async () => {
      const items = await ctx.api(`/api/projects/${ctx.pid}/responses`);
      const done = items.filter((i) => i.status === "resolved").length;
      el.innerHTML = `
        <p class="small muted">把上一轮自己的审稿意见粘贴进来（会按编号自动拆分），然后逐条对照作者回复与修改稿。</p>
        <details ${items.length ? "" : "open"}><summary>导入上一轮意见</summary>
          <textarea id="rImp" rows="6" placeholder="1. The authors should ...&#10;2. ..."></textarea>
          <div class="row"><button class="small" id="rPrev">使用本系统里上一版“给作者”意见</button><span class="spacer"></span><button class="small primary" id="rDo">拆分导入</button></div>
        </details>
        <p><b>${done} / ${items.length}</b> 已解决</p>
        ${items.map((i, n) => `<div class="ritem" data-id="${i.id}">
          <div class="row"><b>#${n + 1}</b><select data-f="status" style="width:auto">
            ${[["open", "未看"], ["resolved", "已解决"], ["partial", "部分解决"], ["unresolved", "未解决"]].map(([v, t]) => `<option value="${v}" ${i.status === v ? "selected" : ""}>${t}</option>`).join("")}</select>
            <span class="spacer"></span><button class="small danger" data-del>删除</button></div>
          <div class="rc">${esc(i.comment)}</div>
          <textarea data-f="response" rows="2" placeholder="作者的回复（可粘贴）">${esc(i.response || "")}</textarea>
          <textarea data-f="my_note" rows="2" placeholder="我的判断">${esc(i.my_note || "")}</textarea></div>`).join("")}
        <button class="small" id="rAdd">＋ 添加一条</button>`;
      el.querySelector("#rDo").onclick = async () => {
        const t = el.querySelector("#rImp").value; if (!t.trim()) return;
        await ctx.api(`/api/projects/${ctx.pid}/responses/import`, { method: "POST", body: { text: t } }); load();
      };
      el.querySelector("#rPrev").onclick = async () => {
        const rv = (await ctx.api(`/api/projects/${ctx.pid}/reviews`)).find((r) => r.kind === "authors");
        if (rv) el.querySelector("#rImp").value = rv.content; else ctx.toast("没有找到已保存的意见", true);
      };
      el.querySelector("#rAdd").onclick = async () => { await ctx.api(`/api/projects/${ctx.pid}/responses`, { method: "POST", body: { comment: "" } }); load(); };
      el.querySelectorAll(".ritem").forEach((it) => {
        const id = it.dataset.id;
        it.querySelectorAll("[data-f]").forEach((f) => f.onchange = () => ctx.api(`/api/responses/${id}`, { method: "PATCH", body: { [f.dataset.f]: f.value } }).then(() => ctx.toast("已保存")));
        it.querySelector("[data-del]").onclick = async () => { await ctx.api(`/api/responses/${id}`, { method: "DELETE" }); load(); };
      });
    };
    await load();
  },

  async journal(ctx, el) {
    await runOrShow(ctx, el, "journal", () => ({}), (r, b) => {
      const j = r.journal || {};
      b.innerHTML = `${errBox(r)}
        ${j.name ? `<div class="kv"><div>期刊</div><div>${esc(j.name)}</div><div>出版社</div><div>${esc(j.publisher || "")}</div>
          <div>ISSN</div><div>${esc((j.issn || []).join(", "))}</div><div>OA</div><div>${j.is_oa ? "是" : "否"}</div>
          <div>主页</div><div>${j.homepage ? `<a href="${esc(j.homepage)}" target="_blank" rel="noreferrer">${esc(j.homepage)}</a>` : ""}</div></div>
          ${j.topics?.length ? `<p class="small">OpenAlex 主题：${j.topics.map((t) => `<span class="pill">${esc(t)}</span>`).join(" ")}</p>` : ""}` : ""}
        <h4>Aims & Scope ${r.manual ? "<span class='pill blue'>手动</span>" : ""} ${r.url ? `<a class="small" href="${esc(r.url)}" target="_blank" rel="noreferrer">来源 ↗</a>` : ""}</h4>
        <textarea id="jText" rows="14">${esc(r.text || "")}</textarea>
        <div class="row"><span class="muted small">${esc(r.note || "")}</span><span class="spacer"></span><button class="small primary" id="jSave">保存手动修改</button></div>
        ${r.log ? `<details class="small muted"><summary>抓取日志</summary>${r.log.map(esc).join("<br>")}</details>` : ""}`;
      b.querySelector("#jSave").onclick = async () => {
        await ctx.api(`/api/projects/${ctx.pid}/tools/journal`, { method: "PUT", body: { text: b.querySelector("#jText").value, url: r.url || "" } });
        ctx.toast("已保存");
      };
    });
    if (!ctx.project.journal && !ctx.project.journal_url) el.insertAdjacentHTML("afterbegin", `<p class="pill warn">请先在任务设置中填写期刊名或网址</p>`);
  },

  async redact(ctx, el) {
    const c = await ctx.api(`/api/projects/${ctx.pid}/ai-context`);
    el.innerHTML = `
      <p class="small">这一步完全在本机进行。保存的文本只有在 AI 对话 / 生成意见时勾选“包含稿件文本”才会被使用；发送到 Together 前还会再次确认。</p>
      <label>需要遮挡的词（作者姓名、单位、项目编号、地名等，每行一个）</label>
      <textarea id="xTerms" rows="4">${esc((c.author_suggestions || []).join("\n"))}</textarea>
      <div class="row wrap"><label class="inline"><input type="checkbox" id="xRefs" checked> 去掉参考文献表</label>
        <label class="inline"><input type="checkbox" id="xFront" checked> 去掉摘要之前的首页信息</label>
        <span class="spacer"></span><button class="small" id="xDraft">生成遮挡后的文本</button></div>
      <label>可发送文本（可直接编辑删改；${c.updated ? "上次保存 " + new Date(c.updated).toLocaleString("zh-CN") : "尚未保存"}）</label>
      <textarea id="xText" rows="18">${esc(c.context)}</textarea>
      <div class="row"><span class="muted small" id="xLen">${c.context.length.toLocaleString()} 字符</span><span class="spacer"></span>
        <button class="small danger" id="xClear">清空</button><button class="small primary" id="xSave">保存</button></div>`;
    const t = el.querySelector("#xText");
    t.oninput = () => el.querySelector("#xLen").textContent = t.value.length.toLocaleString() + " 字符";
    el.querySelector("#xDraft").onclick = async () => {
      const r = await ctx.api(`/api/projects/${ctx.pid}/ai-context/draft`, { method: "POST", body: {
        terms: el.querySelector("#xTerms").value.split("\n"), drop_references: el.querySelector("#xRefs").checked, drop_front_matter: el.querySelector("#xFront").checked } });
      t.value = r.context; t.oninput();
      ctx.toast("已生成，请检查后保存");
    };
    el.querySelector("#xSave").onclick = async () => { await ctx.api(`/api/projects/${ctx.pid}/ai-context`, { method: "PUT", body: { context: t.value } }); ctx.toast("已保存"); ctx.refresh && ctx.refresh(); };
    el.querySelector("#xClear").onclick = async () => { t.value = ""; await ctx.api(`/api/projects/${ctx.pid}/ai-context`, { method: "PUT", body: { context: "" } }); ctx.toast("已清空"); ctx.refresh && ctx.refresh(); };
  },

  async audit(ctx, el) {
    const rows = await ctx.api(`/api/projects/${ctx.pid}/audit`);
    el.innerHTML = rows.length ? `<table class="grid small"><tr><th>时间</th><th>提供方</th><th>用途</th><th>含稿件</th><th>字符</th><th></th></tr>
      ${rows.map((r) => `<tr><td>${new Date(r.at).toLocaleString("zh-CN")}</td><td><span class="pill ${r.provider === "together" ? "warn" : "ok"}">${r.provider === "together" ? "Together" : "本地"}</span><div class="muted">${esc(r.model)}</div></td>
        <td>${esc(r.purpose)}</td><td>${r.included_manuscript ? "是" : "否"}</td><td>${r.chars.toLocaleString()}</td><td><button class="small" data-a="${r.id}">内容</button></td></tr>`).join("")}</table>`
      : "<p class='muted'>本任务还没有 AI 调用。</p>";
    el.querySelectorAll("[data-a]").forEach((b) => b.onclick = async () => {
      const d = await ctx.api(`/api/audit/${b.dataset.a}`);
      const msgs = JSON.parse(d.payload);
      const w = window.open("", "_blank");
      w.document.write(`<meta charset="utf-8"><title>AI call ${d.id}</title><pre style="white-space:pre-wrap;font:13px/1.5 monospace;padding:16px">${esc(msgs.map((m) => "=== " + m.role + " ===\n" + m.content).join("\n\n"))}</pre>`);
    });
  },
};
