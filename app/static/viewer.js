// PDF reader with highlights, underline/strike, sticky notes and text search. Local PDF.js only.
import * as pdfjsLib from "/static/vendor/pdf.min.mjs";
pdfjsLib.GlobalWorkerOptions.workerSrc = "/static/vendor/pdf.worker.min.mjs";

export const COLORS = { yellow: "#ffe45c", green: "#8be28b", blue: "#8cc8ff", pink: "#ff9ccf", orange: "#ffb35c" };

export class Viewer {
  constructor(root, opts = {}) {
    this.root = root;               // scroll container
    this.opts = opts;               // { onChange(anns), onToNotes(ann), onPage(n) }
    this.scale = 1.25;
    this.fit = true;
    this.pages = [];
    this.anns = [];
    this.noteMode = false;
    this.textCache = {};
    this.root.classList.add("pdf-root");
    this.inner = document.createElement("div");
    this.inner.className = "pdf-inner";
    this.root.appendChild(this.inner);
    this.root.addEventListener("mouseup", (e) => setTimeout(() => this.onMouseUp(e), 0));
    this.root.addEventListener("click", (e) => this.onClick(e));
    this.root.addEventListener("scroll", () => this.reportPage(), { passive: true });
    new ResizeObserver(() => { if (this.fit && this.pdf) this.fitWidth(); }).observe(this.root);
    document.addEventListener("mousedown", (e) => {
      if (this.menu && !this.menu.contains(e.target)) this.hideMenu();
    });
  }

  async open(docId) {
    this.docId = docId;
    this.inner.innerHTML = "";
    this.hideMenu(); this.closePop();
    this.textCache = {};
    this.pdf = await pdfjsLib.getDocument({ url: `/files/doc/${docId}/pdf`, withCredentials: true }).promise;
    const first = await this.pdf.getPage(1);
    this.baseVp = first.getViewport({ scale: 1 });
    this.pages = [];
    for (let n = 1; n <= this.pdf.numPages; n++) {
      const div = document.createElement("div");
      div.className = "pdf-page";
      div.dataset.page = n;
      div.innerHTML = `<canvas></canvas><div class="annLayer"></div><div class="textLayer"></div><div class="noteLayer"></div><div class="pno">${n}</div>`;
      this.inner.appendChild(div);
      this.pages.push({ n, div, rendered: false, vpBase: n === 1 ? this.baseVp : null });
    }
    this.io = new IntersectionObserver((ents) => ents.forEach((en) => {
      if (en.isIntersecting) this.renderPage(+en.target.dataset.page);
    }), { root: this.root, rootMargin: "800px 0px" });
    this.pages.forEach((p) => this.io.observe(p.div));
    this.anns = await this.opts.loadAnnotations(docId);
    this.fit ? this.fitWidth() : this.setScale(this.scale);
    return this.pdf.numPages;
  }

  fitWidth() {
    const w = this.root.clientWidth - 36;
    this.fit = true;
    this.applyScale(Math.max(0.5, Math.min(3, w / this.baseVp.width)));
  }
  setScale(s) { this.fit = false; this.applyScale(Math.max(0.4, Math.min(4, s))); }
  applyScale(s) {
    const cur = this.currentPage();
    const frac = this.pageOffsetFrac(cur);
    this.scale = s;
    for (const p of this.pages) {
      const vb = p.vpBase || this.baseVp;
      p.div.style.width = vb.width * s + "px";
      p.div.style.height = vb.height * s + "px";
      p.div.style.setProperty("--scale-factor", s);
      p.rendered = false;
      p.renderedScale = null;
    }
    this.pages.forEach((p) => { const r = p.div.getBoundingClientRect(), rr = this.root.getBoundingClientRect();
      if (r.bottom > rr.top - 800 && r.top < rr.bottom + 800) this.renderPage(p.n); });
    if (cur) this.scrollToPage(cur, frac, false);
    this.opts.onScale && this.opts.onScale(s);
  }

  async renderPage(n) {
    const p = this.pages[n - 1];
    if (!p || p.renderedScale === this.scale || p.rendering) return;
    p.rendering = true;
    const scale = this.scale;
    try {
      const page = await this.pdf.getPage(n);
      p.vpBase = page.getViewport({ scale: 1 });
      const vp = page.getViewport({ scale });
      p.div.style.width = vp.width + "px"; p.div.style.height = vp.height + "px";
      const dpr = Math.min(window.devicePixelRatio || 1, 2.5);
      const canvas = p.div.querySelector("canvas");
      const c2 = document.createElement("canvas");
      c2.width = Math.floor(vp.width * dpr); c2.height = Math.floor(vp.height * dpr);
      c2.style.width = vp.width + "px"; c2.style.height = vp.height + "px";
      await page.render({ canvasContext: c2.getContext("2d"), viewport: vp,
        transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : null }).promise;
      canvas.replaceWith(c2);
      const tl = p.div.querySelector(".textLayer");
      tl.innerHTML = "";
      const tc = this.textCache[n] || await page.getTextContent();
      this.textCache[n] = tc;
      await new pdfjsLib.TextLayer({ textContentSource: tc, container: tl, viewport: vp }).render();
      p.renderedScale = scale;
      this.drawAnns(n);
      if (this.searchTerm) this.markSearch(n);
    } catch (e) { console.error(e); }
    finally { p.rendering = false; if (this.scale !== scale) this.renderPage(n); }
  }

  // ---------------- navigation
  currentPage() {
    const top = this.root.getBoundingClientRect().top + 60;
    for (const p of this.pages) { const r = p.div.getBoundingClientRect(); if (r.bottom > top) return p.n; }
    return this.pages.length ? 1 : 0;
  }
  pageOffsetFrac(n) {
    const p = this.pages[n - 1]; if (!p) return 0;
    const r = p.div.getBoundingClientRect(), rr = this.root.getBoundingClientRect();
    return Math.max(0, (rr.top - r.top) / r.height);
  }
  scrollToPage(n, frac = 0, smooth = true) {
    const p = this.pages[n - 1]; if (!p) return;
    this.root.scrollTo({ top: p.div.offsetTop - 10 + frac * p.div.offsetHeight, behavior: smooth ? "smooth" : "auto" });
  }
  reportPage() {
    clearTimeout(this._pt);
    this._pt = setTimeout(() => this.opts.onPage && this.opts.onPage(this.currentPage()), 80);
  }

  // ---------------- annotations
  drawAnns(n) {
    const p = this.pages[n - 1]; if (!p) return;
    const al = p.div.querySelector(".annLayer"), nl = p.div.querySelector(".noteLayer");
    al.innerHTML = ""; nl.innerHTML = "";
    for (const a of this.anns.filter((x) => x.page === n)) {
      const col = COLORS[a.color] || a.color || COLORS.yellow;
      if (a.type === "note") {
        const r = a.rects[0] || [0.05, 0.05, 0, 0];
        const el = document.createElement("div");
        el.className = "sticky"; el.style.left = r[0] * 100 + "%"; el.style.top = r[1] * 100 + "%";
        el.style.background = col; el.title = a.comment || "";
        el.textContent = "✎"; el.dataset.ann = a.id;
        el.onclick = (e) => { e.stopPropagation(); this.openPop(a, e.clientX, e.clientY); };
        nl.appendChild(el);
        continue;
      }
      a.rects.forEach((r, i) => {
        const el = document.createElement("div");
        el.className = "hl hl-" + (a.type || "highlight");
        el.style.left = r[0] * 100 + "%"; el.style.top = r[1] * 100 + "%";
        el.style.width = r[2] * 100 + "%"; el.style.height = r[3] * 100 + "%";
        el.style.setProperty("--c", col);
        el.dataset.ann = a.id;
        al.appendChild(el);
      });
      if (a.comment && a.rects.length) {
        const r = a.rects[a.rects.length - 1];
        const el = document.createElement("div");
        el.className = "cmark"; el.style.left = (r[0] + r[2]) * 100 + "%"; el.style.top = r[1] * 100 + "%";
        el.textContent = "💬"; el.title = a.comment;
        el.onclick = (e) => { e.stopPropagation(); this.openPop(a, e.clientX, e.clientY); };
        nl.appendChild(el);
      }
    }
  }
  redrawAll() { this.pages.forEach((p) => p.renderedScale && this.drawAnns(p.n)); }

  setAnnotations(anns) { this.anns = anns; this.redrawAll(); }

  onMouseUp(e) {
    if (this.menu && this.menu.contains(e.target)) return;
    if (this.pop && this.pop.contains(e.target)) return;
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || !sel.rangeCount) return;
    const range = sel.getRangeAt(0);
    const startEl = range.startContainer.nodeType === 1 ? range.startContainer : range.startContainer.parentElement;
    const pageDiv = startEl && startEl.closest(".pdf-page");
    if (!pageDiv || !this.root.contains(pageDiv)) return;
    const text = sel.toString().replace(/\s+/g, " ").trim();
    if (!text) return;
    const pr = pageDiv.getBoundingClientRect();
    let rects = [...range.getClientRects()]
      .filter((r) => r.width > 1 && r.height > 1 && r.top >= pr.top - 2 && r.bottom <= pr.bottom + 2 && r.width < pr.width * 0.98)
      .map((r) => [(r.left - pr.left) / pr.width, (r.top - pr.top) / pr.height, r.width / pr.width, r.height / pr.height]);
    rects = mergeRects(rects);
    if (!rects.length) return;
    this.pending = { page: +pageDiv.dataset.page, rects, quote: text };
    const last = range.getClientRects()[range.getClientRects().length - 1] || pr;
    this.showMenu(last.right, last.bottom);
  }

  showMenu(x, y) {
    this.hideMenu();
    const m = document.createElement("div");
    m.className = "sel-menu";
    m.innerHTML = Object.entries(COLORS).map(([k, v]) => `<button class="sw" data-c="${k}" style="background:${v}" title="高亮"></button>`).join("") +
      `<span class="sep"></span><button data-t="underline" title="下划线"><u>U</u></button><button data-t="strike" title="删除线"><s>S</s></button>
       <button data-t="comment" title="高亮并添加批注">💬</button><button data-t="notes" title="摘录到笔记">→笔记</button><button data-t="copy" title="复制">⧉</button>`;
    document.body.appendChild(m);
    const w = m.offsetWidth;
    m.style.left = Math.min(window.innerWidth - w - 8, Math.max(8, x - w / 2)) + "px";
    m.style.top = Math.min(window.innerHeight - 50, y + 8) + "px";
    m.onmousedown = (e) => e.preventDefault();
    m.onclick = async (e) => {
      const b = e.target.closest("button"); if (!b || !this.pending) return;
      const pend = this.pending;
      if (b.dataset.c) await this.create({ ...pend, type: "highlight", color: b.dataset.c });
      else if (b.dataset.t === "underline" || b.dataset.t === "strike") await this.create({ ...pend, type: b.dataset.t, color: b.dataset.t === "strike" ? "#e0393e" : "#2f5bd3" });
      else if (b.dataset.t === "comment") { const a = await this.create({ ...pend, type: "highlight", color: "yellow" }); this.openPop(a, e.clientX, e.clientY, true); }
      else if (b.dataset.t === "notes") this.opts.onToNotes && this.opts.onToNotes({ page: pend.page, quote: pend.quote, comment: "" });
      else if (b.dataset.t === "copy") navigator.clipboard.writeText(pend.quote);
      window.getSelection().removeAllRanges();
      this.hideMenu();
    };
    this.menu = m;
  }
  hideMenu() { if (this.menu) { this.menu.remove(); this.menu = null; } }

  async create(a) {
    const saved = await this.opts.saveAnnotation(a);
    const full = { comment: "", ...a, id: saved.id };
    this.anns.push(full);
    this.drawAnns(a.page);
    this.opts.onChange && this.opts.onChange(this.anns);
    return full;
  }

  onClick(e) {
    if (this.pop && this.pop.contains(e.target)) return;
    const pageDiv = e.target.closest(".pdf-page");
    if (!pageDiv) return;
    const pr = pageDiv.getBoundingClientRect();
    const x = (e.clientX - pr.left) / pr.width, y = (e.clientY - pr.top) / pr.height;
    const n = +pageDiv.dataset.page;
    if (this.noteMode) {
      this.create({ page: n, type: "note", color: "yellow", rects: [[x - 0.012, y - 0.012, 0, 0]], quote: "" })
        .then((a) => this.openPop(a, e.clientX, e.clientY, true));
      this.opts.onNoteModeEnd && this.opts.onNoteModeEnd();
      this.noteMode = false;
      return;
    }
    const sel = window.getSelection();
    if (sel && !sel.isCollapsed) return;
    const hit = this.anns.find((a) => a.page === n && a.type !== "note" &&
      a.rects.some((r) => x >= r[0] && x <= r[0] + r[2] && y >= r[1] - 0.003 && y <= r[1] + r[3] + 0.003));
    if (hit) this.openPop(hit, e.clientX, e.clientY);
  }

  openPop(a, x, y, focus = false) {
    this.closePop();
    const p = document.createElement("div");
    p.className = "ann-pop";
    p.innerHTML = `
      <div class="q">${a.quote ? "“" + escapeHtml(a.quote.slice(0, 300)) + (a.quote.length > 300 ? "…" : "") + "”" : "<i>便签 · 第 " + a.page + " 页</i>"}</div>
      <textarea rows="4" placeholder="批注 / 想法…">${escapeHtml(a.comment || "")}</textarea>
      <div class="row">${Object.entries(COLORS).map(([k, v]) => `<button class="sw ${a.color === k ? "on" : ""}" data-c="${k}" style="background:${v}"></button>`).join("")}
        <span class="spacer"></span><button data-act="notes" class="small">→笔记</button><button data-act="del" class="small danger">删除</button><button data-act="save" class="small primary">保存</button></div>`;
    document.body.appendChild(p);
    p.style.left = Math.min(window.innerWidth - 330, Math.max(8, x - 150)) + "px";
    p.style.top = Math.min(window.innerHeight - p.offsetHeight - 8, y + 12) + "px";
    const ta = p.querySelector("textarea");
    if (focus) setTimeout(() => ta.focus(), 30);
    const save = async () => {
      if (ta.value !== (a.comment || "")) {
        a.comment = ta.value;
        await this.opts.updateAnnotation(a.id, { comment: a.comment });
        this.drawAnns(a.page);
        this.opts.onChange && this.opts.onChange(this.anns);
      }
    };
    p.onclick = async (e) => {
      const b = e.target.closest("button"); if (!b) return;
      if (b.dataset.c) {
        a.color = b.dataset.c; await this.opts.updateAnnotation(a.id, { color: a.color });
        p.querySelectorAll(".sw").forEach((s) => s.classList.toggle("on", s.dataset.c === a.color));
        this.drawAnns(a.page); this.opts.onChange && this.opts.onChange(this.anns);
      } else if (b.dataset.act === "save") { await save(); this.closePop(); }
      else if (b.dataset.act === "del") {
        await this.opts.deleteAnnotation(a.id);
        this.anns = this.anns.filter((x) => x.id !== a.id);
        this.drawAnns(a.page); this.opts.onChange && this.opts.onChange(this.anns); this.closePop();
      } else if (b.dataset.act === "notes") { await save(); this.opts.onToNotes && this.opts.onToNotes(a); this.closePop(); }
    };
    ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { save().then(() => this.closePop()); } });
    this.pop = p;
    this._popSave = save;
    setTimeout(() => {
      this._popOutside = (e) => { if (this.pop && !this.pop.contains(e.target) && !e.target.closest(".sticky,.cmark")) { save(); this.closePop(); } };
      document.addEventListener("mousedown", this._popOutside);
    }, 0);
  }
  closePop() {
    if (this.pop) { this.pop.remove(); this.pop = null; }
    if (this._popOutside) { document.removeEventListener("mousedown", this._popOutside); this._popOutside = null; }
  }

  flashAnn(a) {
    this.scrollToPage(a.page, Math.max(0, (a.rects[0] || [0, 0])[1] - 0.15));
    setTimeout(() => {
      $$(`[data-ann="${a.id}"]`, this.root).forEach((el) => { el.classList.add("flash"); setTimeout(() => el.classList.remove("flash"), 1600); });
    }, 500);
  }

  // ---------------- search
  async search(term) {
    this.searchTerm = term.trim().toLowerCase();
    $$(".textLayer .found", this.root).forEach((s) => s.classList.remove("found"));
    if (!this.searchTerm) return [];
    const hits = [];
    for (let n = 1; n <= this.pdf.numPages; n++) {
      if (!this.textCache[n]) this.textCache[n] = await (await this.pdf.getPage(n)).getTextContent();
      const txt = this.textCache[n].items.map((i) => i.str).join(" ").toLowerCase();
      let i = -1, c = 0;
      while ((i = txt.indexOf(this.searchTerm, i + 1)) >= 0) c++;
      if (c) hits.push({ page: n, count: c });
    }
    this.pages.forEach((p) => p.renderedScale && this.markSearch(p.n));
    return hits;
  }
  markSearch(n) {
    const t = this.searchTerm; if (!t) return;
    $$(".textLayer span", this.pages[n - 1].div).forEach((s) => {
      if (s.textContent.toLowerCase().includes(t)) s.classList.add("found");
    });
  }
}

function $$(sel, root) { return [...root.querySelectorAll(sel)]; }
function escapeHtml(s) { return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

// merge rects that sit on the same line and touch / overlap
function mergeRects(rs) {
  rs.sort((a, b) => a[1] - b[1] || a[0] - b[0]);
  const out = [];
  for (const r of rs) {
    const l = out[out.length - 1];
    if (l && Math.abs(l[1] - r[1]) < 0.006 && Math.abs(l[3] - r[3]) < 0.008 && r[0] <= l[0] + l[2] + 0.01) {
      const x1 = Math.min(l[0], r[0]), x2 = Math.max(l[0] + l[2], r[0] + r[2]);
      const y1 = Math.min(l[1], r[1]), y2 = Math.max(l[1] + l[3], r[1] + r[3]);
      out[out.length - 1] = [x1, y1, x2 - x1, y2 - y1];
    } else if (!out.some((o) => r[0] >= o[0] - 0.001 && r[1] >= o[1] - 0.001 && r[0] + r[2] <= o[0] + o[2] + 0.001 && r[1] + r[3] <= o[1] + o[3] + 0.001)) out.push(r);
  }
  return out;
}
