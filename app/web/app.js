// 규정 도우미 화면 동작 — 서버(app/web_server.py) API를 쓴다. 서버가 없으면 sample-data.js 예시로 근거·개정 화면만 그린다.
//  채팅: POST /api/ask (가빈 답변 파이프라인 + 로컬 9B). 답변의 [n]을 누르면 그 근거 조문이 오른쪽에 열린다.
//  개정: 근거 원문 → 개정 정보 → 개정안 작성(POST /api/draft) → 개정안 검토(수락·기각 POST /api/decide, 제출 POST /api/submit, 패키지 내려받기)
//  대화 기록은 이 브라우저에만 저장 (localStorage). 글은 모두 textContent로 넣는다 (원문·답변을 HTML로 해석하지 않게).
(function () {
  const theme = new URLSearchParams(location.search).get("theme");
  if (theme === "light" || theme === "dark") document.documentElement.dataset.theme = theme;
  const S = window.SAMPLE, USER = S.user.name;
  const $ = (s) => document.querySelector(s);
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
  const clear = (n) => { while (n.firstChild) n.firstChild.remove(); return n; };
  let LIVE = false, CURRENT = null, CS = null, ROLE_NOW = "editor";
  const isEditor = () => ROLE_NOW === "editor";
  const api = async (path, body) => {
    const r = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user: USER, ...body }) } : { cache: "no-store" });
    const j = await r.json().catch(() => ({ error: r.statusText }));
    if (!r.ok || j.error) throw new Error(j.error || r.statusText);
    return j;
  };
  const toast = (msg, ms = 2800) => { const t = el("div", "toast", msg); document.body.append(t); setTimeout(() => t.remove(), ms); };

  const ICONS = {
    plus: "M12 5v14M5 12h14", chat: "M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z",
    doc: "M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8zM14 3v5h5", edit: "M4 20h4L19 9l-4-4L4 16z",
    copy: "M9 9h10v10H9zM5 15V5h10", download: "M12 4v11M7 10l5 5 5-5M5 20h14", search: "M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM20 20l-4-4",
    "up-arrow": "M12 19V5M6 11l6-6 6 6", up: "M7 10v10H4V10zM7 10l4-7a2 2 0 0 1 3 2l-1 5h6a2 2 0 0 1 2 2l-2 7a2 2 0 0 1-2 1H7",
    down: "M17 14V4h3v10zM17 14l-4 7a2 2 0 0 1-3-2l1-5H5a2 2 0 0 1-2-2l2-7a2 2 0 0 1 2-1h10",
    check: "M5 12l5 5L20 7", x: "M6 6l12 12M18 6L6 18", arrow: "M5 12h14M13 6l6 6-6 6",
  };
  const icon = (name) => {
    const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("class", "ico"); s.setAttribute("aria-hidden", "true");
    const p = document.createElementNS("http://www.w3.org/2000/svg", "path"); p.setAttribute("d", ICONS[name] || ""); s.append(p); return s;
  };
  document.querySelectorAll("[data-icon]").forEach((n) => n.replaceWith(icon(n.dataset.icon)));
  $("#user-name").textContent = USER; $("#user-dept").textContent = S.user.dept; $("#user-initial").textContent = USER[0];

  // ── 대화 기록 (이 브라우저에만) ─────────────────────────
  const STORE = "kvic-chat-v1";
  const load = () => { try { return JSON.parse(localStorage.getItem(STORE)) || []; } catch { return []; } };
  const save = (v) => { try { localStorage.setItem(STORE, JSON.stringify(v.slice(0, 30))); } catch { /* 저장 못 해도 화면은 동작 */ } };
  let convs = load(), convId = null;
  const conv = () => convs.find((c) => c.id === convId);

  function renderConvList() {
    const box = clear($("#conv-list"));
    if (!convs.length) { box.append(el("div", "nav-group", "대화 기록 없음")); return; }
    const today = new Date().toDateString();
    const groups = [["오늘", (c) => new Date(c.at).toDateString() === today], ["이전", (c) => new Date(c.at).toDateString() !== today]];
    groups.forEach(([g, f]) => {
      const items = convs.filter(f); if (!items.length) return;
      box.append(el("div", "nav-group", g));
      const ul = el("ul", "nav");
      items.forEach((c) => {
        const b = el("button"); b.type = "button"; b.append(el("span", "label", c.title));
        if (c.id === convId) b.setAttribute("aria-current", "page");
        b.addEventListener("click", () => openConv(c.id));
        const li = el("li"); li.append(b); ul.append(li);
      });
      box.append(ul);
    });
  }
  function openConv(id) {
    convId = id; show("chat");
    const c = conv(); $("#chat-title").textContent = c ? c.title : "새 대화";
    const t = clear($("#thread"));
    if (!c) return renderEmpty();
    c.msgs.forEach((m) => t.append(renderMsg(m.q, m.res)));
    renderConvList();
    const last = c.msgs[c.msgs.length - 1];
    const first = last?.res?.entries?.find((e) => (last.res.cited || []).includes(e.n) && isOpenable(e.chunk)) || last?.res?.entries?.find((e) => isOpenable(e.chunk));
    if (first) loadArticle(first.chunk);
    t.scrollTop = t.scrollHeight;
  }
  function newConv() { convId = null; $("#chat-title").textContent = "새 대화"; renderEmpty(); renderConvList(); show("chat"); $("#ask-q").focus(); }

  // ── 채팅 ─────────────────────────────────────────────
  const EXAMPLES = ["육아휴직은 몇 번까지 나눠 쓸 수 있나요?", "반반차(2시간)도 쓸 수 있나요?", "결혼하면 특별휴가 며칠 나와요?"];
  function renderEmpty() {
    const t = clear($("#thread")), box = el("div", "empty-chat");
    box.append(el("h2", null, "내부규정에 대해 물어보세요"), el("span", null, "답에는 근거 조문 번호가 붙고, 누르면 오른쪽에 원문이 열립니다."));
    const ex = el("div", "examples");
    EXAMPLES.forEach((q) => { const b = el("button"); b.type = "button"; b.append(q, icon("arrow")); b.addEventListener("click", () => send(q)); ex.append(b); });
    box.append(ex); t.append(box);
  }
  const isOpenable = (chunk) => chunk && /^\d+_/.test(chunk);

  // 가빈 답변 형식(**제목:**, - 불릿, [n] 인용)을 안전하게 그린다
  function inline(text, entries, into) {
    const parts = text.split(/(\*\*[^*]+\*\*|\[\d+\])/g);
    parts.forEach((p) => {
      if (!p) return;
      const b = p.match(/^\*\*([^*]+)\*\*$/), r = p.match(/^\[(\d+)\]$/);
      if (b) into.append(el("b", null, b[1]));
      else if (r) {
        const n = +r[1], e = entries.find((x) => x.n === n);
        const btn = el("button", "ref", String(n)); btn.type = "button"; btn.title = e ? `${e.reg || ""} ${e.article || ""}` : "";
        btn.addEventListener("click", () => openEntry(e)); into.append(btn);
      } else into.append(p);
    });
  }
  function renderBody(answer, entries) {
    const body = el("div", "body"); let ul = null;
    answer.split("\n").forEach((line) => {
      const s = line.trim();
      if (!s) { ul = null; return; }
      const h = s.match(/^\*\*([^*]{1,20})[:：]?\*\*\s*$/);
      if (h) { body.append(el("h4", null, h[1].replace(/[:：]$/, ""))); ul = null; return; }
      if (/^[-•·]\s+/.test(s)) { if (!ul) { ul = el("ul"); body.append(ul); } const li = el("li"); inline(s.replace(/^[-•·]\s+/, ""), entries, li); ul.append(li); return; }
      ul = null; const p = el("p"); inline(s, entries, p); body.append(p);
    });
    return body;
  }
  function openEntry(e) {
    if (!e) return;
    if (isOpenable(e.chunk)) { loadArticle(e.chunk); selectTab("src"); }
    else toast(`${e.reg || "외부 법령"} ${e.article || ""} — 법령 원문은 내부규정 패널에 열 수 없습니다`);
  }
  function renderMsg(q, res) {
    const wrap = el("div", "msg");
    const qq = el("div", "q"); qq.append(el("span", null, q)); wrap.append(qq);
    const a = el("div", "a"); wrap.append(a);
    if (!res) { const th = el("div", "thinking"); th.append(el("span", "bar"), el("span", "label", "근거 찾는 중 · 0초")); a.append(th); return wrap; }
    if (res.error) { a.append(el("div", "warnline", `답하지 못했습니다 — ${res.error}`)); return wrap; }
    const head = el("div", "a-head");
    const modeTag = { answer: ["답변", "pos"], partial: ["관련 조문만", "warn"], none: ["근거 없음", ""] }[res.mode] || ["답변", ""];
    head.append(el("b", null, "규정 도우미"), el("span", "tag " + modeTag[1], modeTag[0]), el("span", "label", `${res.model} · ${res.sec}초`));
    a.append(head, renderBody(res.answer, res.entries));
    const cited = new Set(res.cited || []);
    const used = res.entries.filter((e) => cited.has(e.n)), rest = res.entries.filter((e) => !cited.has(e.n));
    const shown = used.length ? used : rest.slice(0, 5);
    // 곧 바뀔 수 있는 조문으로 답했는지: 근거 중 개정안(초안·검토·승인)이 걸린 조문
    const changing = shown.filter((e) => e.open?.length);
    if (changing.length) {
      const box = el("div", "changing");
      box.append(el("b", null, "개정이 진행 중인 조문이 근거에 있습니다"));
      const ul = el("ul"), byArt = new Map();   // 같은 조의 여러 항은 한 줄로, 개정안은 겹치지 않게
      changing.forEach((e) => {
        const k = `${e.reg} ${(e.article || "").split(" ")[0]}`;
        if (!byArt.has(k)) byArt.set(k, new Map());
        e.open.forEach((o) => byArt.get(k).set(o.id, o));
      });
      byArt.forEach((opens, art) => {
        const li = el("li"); li.append(el("b", null, art), " — ");
        [...opens.values()].forEach((o, i) => {
          if (i) li.append(" · ");
          li.append(el("span", "tag warn", o.status === "검토" ? "검토 요청됨" : o.status), ` ${o.id}`);
          const go = el("button", "back editor-only", "보기"); go.type = "button"; go.dataset.view = "review"; go.dataset.cs = o.id;
          go.hidden = !isEditor(); li.append(" ", go);
        });
        ul.append(li);
      });
      box.append(ul, el("p", null, "개정이 확정되면 이 답이 달라질 수 있습니다. 지금은 현행 조문 기준입니다."));
      a.append(box);
    }
    const src = el("div", "sources"); src.append(el("span", "label", used.length ? `인용한 근거 ${used.length}` : `근거 후보 ${rest.length}`));
    shown.forEach((e) => {
      const c = el("button", "cite" + (isOpenable(e.chunk) ? "" : " off")); c.type = "button";
      const ROLE = { "검색": "검색 결과", "형제항": "같은 조의 다른 항", "인용": "인용된 조문", "정확조회": "직접 찾은 조문", "관련 조문": "관련 조문" };
      const t = el("span"); t.append(`${e.reg || ""} ${e.article || ""}`, el("small", null, ROLE[e.role] || e.role || ""));
      const tags = el("span", "tags"); tags.append(el("span", "tag " + (e.state === "현행" ? "pos" : ""), e.state || ""));
      if (e.open?.length) tags.append(el("span", "tag warn", "개정 검토 중"));
      c.append(el("span", "num", String(e.n)), t, tags, el("small", null, isOpenable(e.chunk) ? "원문 →" : "법령"));
      c.addEventListener("click", () => openEntry(e)); src.append(c);
    });
    a.append(src);
    (res.warnings || []).slice(0, 3).forEach((w) => a.append(el("div", "warnline", "⚠ " + w)));
    const foot = el("div", "a-foot"); foot.append("참고용 답변 · 최종 판단은 담당 부서");
    const acts = el("span", "acts");
    const cp = el("button", "icon-btn"); cp.type = "button"; cp.setAttribute("aria-label", "답변 복사"); cp.append(icon("copy"));
    cp.addEventListener("click", async () => { try { await navigator.clipboard.writeText(res.answer); toast("답변을 복사했습니다"); } catch { toast("복사하지 못했습니다"); } });
    acts.append(cp);
    [[true, "up", "도움이 됐어요"], [false, "down", "도움이 안 됐어요"]].forEach(([good, ic, label]) => {
      const b = el("button", "icon-btn"); b.type = "button"; b.setAttribute("aria-label", label); b.append(icon(ic));
      b.addEventListener("click", async () => { try { await api("/api/feedback", { q, answer: res.answer, good, model: res.model }); toast("의견을 남겼습니다"); } catch (e) { toast(e.message); } });
      acts.append(b);
    });
    foot.append(acts); a.append(foot);
    return wrap;
  }
  let busy = false;
  async function send(text) {
    const q = (text ?? $("#ask-q").value).trim();
    if (!q || busy) return;
    if (!LIVE) return toast("서버에 연결되지 않아 질문할 수 없습니다");
    busy = true; $("#composer .send").disabled = true; $("#ask-q").value = "";
    if (!conv()) { convId = "c" + Date.now(); convs.unshift({ id: convId, title: q.slice(0, 40), at: Date.now(), msgs: [] }); $("#chat-title").textContent = q.slice(0, 40); }
    const t = $("#thread"); if (t.querySelector(".empty-chat")) clear(t);
    const node = renderMsg(q, null); t.append(node); t.scrollTop = t.scrollHeight;
    const lab = node.querySelector(".thinking .label"), t0 = Date.now();
    const tick = setInterval(() => { const s = Math.round((Date.now() - t0) / 1000); lab.textContent = `${s < 6 ? "근거 찾는 중" : "답변 쓰는 중"} · ${s}초`; }, 1000);
    let res;
    try { res = await api("/api/ask", { q }); } catch (e) { res = { error: e.message }; }
    clearInterval(tick);
    node.replaceWith(renderMsg(q, res));
    const c = conv(); c.msgs.push({ q, res }); c.at = Date.now(); save(convs); renderConvList();
    const first = res.entries?.find((e) => (res.cited || []).includes(e.n) && isOpenable(e.chunk)) || res.entries?.find((e) => isOpenable(e.chunk));
    if (first) loadArticle(first.chunk);
    t.scrollTop = t.scrollHeight;
    busy = false; $("#composer .send").disabled = false; $("#ask-q").focus();
  }
  $("#composer").addEventListener("submit", (e) => { e.preventDefault(); send(); });
  $("#ask-q").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); } });

  // ── 근거 원문 ────────────────────────────────────────
  function renderArticle(art) {
    CURRENT = art;
    const P = clear($("#panel-src"));
    const h = el("header", "doc-head"), meta = el("div", "meta");
    meta.append(el("span", "tag pos", "현행"), art.revision || "");
    h.append(el("h2", null, art.reg), meta);
    P.append(h, el("h3", "art-title", art.label));
    art.paras.forEach((p, i) => P.append(el("p", "para" + (i === art.highlight ? " hl" : "") + (p.status === "삭제" ? " del" : ""), p.text)));
    P.scrollTop = 0; const hl = P.querySelector(".para.hl");   // 머리(규정명)가 보이게 위에서 시작, 강조 항이 화면 밖일 때만 내림
    if (hl && hl.offsetTop + hl.offsetHeight > P.clientHeight) hl.scrollIntoView({ block: "nearest" });
  }
  async function showFullRegulation() {
    if (!CURRENT?.reg_id) return toast("먼저 조문을 여세요");
    try {
      const R = await api(`/api/regulation?reg=${encodeURIComponent(CURRENT.reg_id)}`);
      const P = clear($("#panel-src")), keep = CURRENT;
      const back = el("button", "back", `← ${keep.label}(으)로 돌아가기`); back.type = "button";
      back.addEventListener("click", () => renderArticle(keep));
      const h = el("header", "doc-head"), meta = el("div", "meta"); meta.append(el("span", "tag", "전문"), R.revision || "", `${R.articles.length}개 조`);
      h.append(el("h2", null, R.reg), meta);
      P.append(back, h);
      let cur = null;
      R.articles.forEach((a) => {
        const blk = el("section", "art-block" + (a.id === keep.article_id ? " cur" : ""));
        blk.append(el("h3", "art-title", a.label));
        a.paras.forEach((p) => blk.append(el("p", "para" + (p.chunk === keep.chunk ? " hl" : "") + (p.status === "삭제" ? " del" : ""), p.text)));
        P.append(blk); if (a.id === keep.article_id) cur = blk;
      });
      P.append(el("p", "hint", "부칙·별표·별지는 원문 파일에서 확인하세요."));
      selectTab("src"); cur?.scrollIntoView({ block: "start" });
    } catch (e) { toast(e.message); }
  }
  $("#full-reg").addEventListener("click", showFullRegulation);
  $("#copy-art").addEventListener("click", async () => {
    if (!CURRENT) return;
    try { await navigator.clipboard.writeText([`${CURRENT.reg} ${CURRENT.label}`, ...CURRENT.paras.map((p) => p.text)].join("\n")); toast("조문을 복사했습니다"); } catch { toast("복사하지 못했습니다"); }
  });

  // ── 개정 정보 (개정 담당자) ───────────────────────────
  function renderAmend(m) {
    const A = clear($("#panel-amend"));
    m.open.forEach((o) => {
      const n = el("div", "notice"), link = el("button", "back", `${o.id} 열기 →`); link.type = "button"; link.dataset.view = "review"; link.dataset.cs = o.id;
      n.append(el("span", "tag " + (o.status === "검토" ? "accent" : "warn"), o.status === "검토" ? "검토 요청됨" : o.status), el("b", null, "개정 진행 중"),
               el("p", null, `${o.id} — ${o.title.replace(/\s*\([^)]*\)\s*$/, "")}`), el("span"), link);
      A.append(n);
    });
    const sect = (title, rows, emptyText) => {
      const s = el("section", "sect"), h = el("h3");
      h.append(title, el("span", "num" + (rows.length ? " on" : ""), String(rows.length).padStart(2, "0"))); s.append(h);
      if (!rows.length) s.append(el("div", "none", emptyText));
      rows.forEach(([b, t]) => { const r = el("div", "item"); r.append(el("b", null, b)); if (t) r.append(el("q", null, t)); s.append(r); });
      A.append(s);
    };
    sect("이 조를 인용하는 곳", m.inbound.map((x) => [x.where, x.quote ? `…${x.quote}…` : ""]), "인용하는 내규 없음");
    sect("점검 결과", m.findings.map((x) => [x.type, [x.quote && `「${x.quote}」`, x.note].filter(Boolean).join(" · ")]), "이 조에 걸린 점검 결과 없음");
    sect("이 조가 인용하는 법령", m.laws.map((x) => [x.where, x.quote]), "인용하는 법령 없음");
    sect("제목이 같은 법령 조문", m.sameTitleLaws.map((x) => [`${x.law} ${x.article}`, x.text]), "해당 없음");
    const row = el("div", "actions-row");
    const form = el("form", "draft-form");
    const ta = el("textarea"); ta.placeholder = `이 조를 어떻게 바꿀지 적어 주세요 — 예: 분할 횟수를 3회로 늘린다`; ta.maxLength = 300; ta.setAttribute("aria-label", "개정 요청");
    const btns = el("div", "row-btns"); const go = el("button", "btn primary"); go.type = "submit"; go.append(icon("edit"), "개정안 작성");
    btns.append(go); form.append(ta, btns);
    form.addEventListener("submit", (e) => { e.preventDefault(); draft(ta.value, form, go); });
    row.append(form, el("p", "hint", "로컬 모델이 개정문 초안을 쓰고, 적용기가 원문과 맞는지 확인한 뒤 연쇄 수정·영향 점검까지 합니다. 결과는 참고 문안이며 자동 반영되지 않습니다."));
    A.append(row);
    const n = m.inbound.length + m.findings.length + m.open.length;
    $("#amend-count").textContent = n ? String(n) : "";
  }
  async function draft(request, form, btn) {
    if (!CURRENT) return;
    if (request.trim().length < 4) return toast("어떻게 바꿀지 한 문장으로 적어 주세요");
    btn.disabled = true;
    const th = el("div", "thinking"); th.append(el("span", "bar"), el("span", "label", "개정문 초안 쓰는 중 · 0초")); form.append(th);
    const t0 = Date.now(), tick = setInterval(() => { th.lastChild.textContent = `개정문 초안 쓰고 확인하는 중 · ${Math.round((Date.now() - t0) / 1000)}초`; }, 1000);
    try {
      const r = await api("/api/draft", { chunk: CURRENT.chunk, request });
      toast(`개정안 ${r.id} 작성 — 깨짐 ${r.breaks[0] ?? 0} → ${r.breaks[1] ?? 0}, 함께 개정 ${r.companion}개 규정, 영향 ${r.impacts}건`, 5000);
      await refreshChangesets(); show("review"); await loadChangeset(r.id); loadArticle(CURRENT.chunk);
    } catch (e) { toast(e.message, 6000); }
    finally { clearInterval(tick); th.remove(); btn.disabled = false; }
  }
  function selectTab(k) {
    const src = k === "src";
    $("#tab-src").setAttribute("aria-selected", String(src)); $("#tab-amend").setAttribute("aria-selected", String(!src));
    $("#panel-src").hidden = !src; $("#panel-amend").hidden = src;
  }
  $("#tab-src").addEventListener("click", () => selectTab("src"));
  $("#tab-amend").addEventListener("click", () => selectTab("amend"));

  // ── 개정안 목록·검토 ─────────────────────────────────
  function renderChangesetList(list) {
    const ul = clear($("#cs-list"));
    list.forEach((c) => {
      const b = el("button"); b.type = "button"; b.dataset.view = "review"; b.dataset.cs = c.id;
      b.append(el("span", "tag" + (c.status_label === "검토" ? " accent" : ""), c.id), el("span", "label", c.title.replace(/\s*\([^)]*\)\s*$/, "")));
      const li = el("li"); li.append(b); ul.append(li);
    });
  }
  async function refreshChangesets() { if (LIVE) renderChangesetList(await api("/api/changesets")); }
  // 상태: 초안 → 검토(제출) → 승인(최종안 저장) → 공포.  검토 중 반려 → 기각 (sql/amend_schema.sql CHECK)
  const CS_LABEL = { 초안: ["초안", ""], 검토: ["검토 요청됨", "accent"], 승인: ["승인", "pos"], 공포: ["공포", "pos"], 기각: ["반려", "neg"] };
  const editable = (C) => C.status === "초안" || C.status === "검토";
  async function renderFinal(C, doc) {
    if (!LIVE || !["검토", "승인", "공포"].includes(C.status)) return;
    let F;
    try { F = await api(`/api/final?id=${encodeURIComponent(C.id)}`); } catch (e) { return toast(e.message); }
    if (CS !== C) return;   // 그사이 다른 개정안을 열었음
    const s = el("section", "final");
    const c = F.counts;
    s.append(el("h2", null, C.status === "검토" ? "최종안 미리보기 (수락한 카드만 반영)" : "최종 개정안 (승인됨)"));
    const meta = el("p", "hint", `수락 ${c["수락"]} · 기각 ${c["기각"]} · 미결정 ${c["미결정"]} — 함께 개정에 반영된 고침 ${c["반영"]}/${c["고칠 문구 있음"]}건`);
    s.append(meta);
    if (c["미결정"]) s.append(el("div", "warnline", `결정하지 않은 카드 ${c["미결정"]}건이 남아 있어 아직 승인할 수 없습니다.`));
    const main = el("div", "comp"); main.append(el("b", null, `${F.reg_name} (본 개정)`), el("p", null, F.amendment_final)); s.append(main);
    F.companion.forEach((x) => {
      const d = el("div", "comp"), h = el("b", null, x.reg_name);
      d.append(h, " ", el("span", "tag " + (x.status === "ok" ? "pos" : x.status === "수작업만" ? "" : "neg"), x.status === "ok" ? "적용 확인" : x.status));
      if (x.amendment) d.append(el("p", null, x.amendment));
      if (x.manual?.length) d.append(el("p", "hint", `별표·별지·부칙 안 인용 ${x.manual.length}건은 사람이 직접 고칩니다`));
      s.append(d);
    });
    if (F.excluded.length) {
      const d = el("div", "comp"); d.append(el("b", null, `기각해서 뺀 고침 ${F.excluded.length}건`));
      const ul = el("ul", "qlist"); F.excluded.forEach((x) => ul.append(el("li", null, `${x.where} — 「${x.quote}」 → 「${x.suggest}」`))); d.append(ul); s.append(d);
    }
    if (F.addendum_draft) { const d = el("div", "comp"); d.append(el("b", null, "부칙"), el("p", null, F.addendum_draft)); s.append(d); }
    doc.prepend(s);
  }
  function renderActions(C) {
    const row = clear($(".review-foot .row2"));
    const btn = (cls, label, fn, ic) => { const b = el("button", "btn " + cls); b.type = "button"; if (ic) b.append(icon(ic)); b.append(label); b.addEventListener("click", fn); return b; };
    const link = (label, href) => { const a = el("a", "btn secondary"); a.href = href; a.setAttribute("download", ""); a.append(icon("download"), label); return a; };
    const act = (path, msg) => async () => {
      if (!LIVE) return toast("서버에 연결되지 않았습니다");
      try { const r = await api(path, { id: C.id }); toast(msg(r)); await loadChangeset(C.id); await refreshChangesets(); } catch (e) { toast(e.message, 6000); }
    };
    if (C.status === "초안") {
      if (C.has_package) row.append(link("패키지 내려받기", `/api/package?id=${encodeURIComponent(C.id)}`));
      row.append(btn("primary", "검토자에게 제출", act("/api/submit", (r) => `${C.id} 검토 요청함${r.undecided ? ` — 미결정 ${r.undecided}건 포함` : ""}`)));
    } else if (C.status === "검토") {
      row.append(btn("secondary", "반려", act("/api/reject", () => `${C.id} 반려함`), "x"));
      row.append(btn("primary", "승인 (최종안 확정)", act("/api/approve", (r) => `${C.id} 승인 — 수락 ${r.counts["수락"]}건으로 최종안을 저장했습니다`), "check"));
    } else if (C.status === "승인") {
      row.append(link("최종안 내려받기", `/api/package?id=${encodeURIComponent(C.id)}&final=1`));
      row.append(btn("primary", "공포 처리", act("/api/publish", () => `${C.id} 공포 처리함`)));
    } else {
      if (C.status === "공포") row.append(link("최종안 내려받기", `/api/package?id=${encodeURIComponent(C.id)}&final=1`));
      const b = btn("primary", C.status === "공포" ? "공포됨" : "반려됨", () => {}); b.disabled = true; row.append(b);
    }
  }
  function renderChangeset(C) {
    CS = C;
    $("#cs-id").textContent = C.id; $("#cs-title").textContent = `${C.reg} — ${C.title.replace(/\s*\([^)]*\)\s*$/, "")}`;
    const [lab, tone] = CS_LABEL[C.status] || [C.status, ""];
    $("#cs-status").textContent = lab; $("#cs-status").className = "tag " + tone;
    const doc = clear($("#doc"));
    const s1 = el("section"); s1.append(el("h2", null, "개정문"), el("p", "amend-text", C.amendment)); doc.append(s1);
    const s2 = el("section"); s2.append(el("h2", null, "신·구조문 대비표"));
    const diff = el("div", "diff");
    const side = (title, parts) => { const d = el("div"); d.append(el("span", "head", title)); parts.forEach((p) => d.append(typeof p === "string" ? p : p.del != null ? el("del", null, p.del) : el("ins", null, p.ins))); return d; };
    diff.append(side("현행", C.diff.old), side("개정안", C.diff.new)); s2.append(diff); doc.append(s2);
    if (C.companion?.length) {
      const s = el("section"); s.append(el("h2", null, `함께 개정할 규정 ${C.companion.length}`));
      C.companion.forEach((x) => { const d = el("div", "comp"); d.append(el("b", null, x.reg)); if (x.amendment) d.append(el("p", null, x.amendment)); if (x.manual) d.append(el("p", "hint", `수작업 확인 ${x.manual}건`)); s.append(d); });
      doc.append(s);
    }
    if (C.addendum) { const s = el("section"); s.append(el("h2", null, "부칙 초안"), el("p", "amend-text", C.addendum)); doc.append(s); }
    if (C.questions?.length) {
      const s = el("section"); s.append(el("h2", null, "담당 부서 확인 필요 (질의서)"));
      const ol = el("ol", "qlist"); C.questions.forEach((x) => ol.append(el("li", null, `${x.where} — ${x.question}`))); s.append(ol); doc.append(s);
    }
    if (C.breaks) doc.append(el("p", "hint", `연쇄 수정: 깨지는 인용 ${C.breaks[0] ?? 0} → ${C.breaks[1] ?? 0}`));

    const SD = clear($("#side"));
    const cnt = (k) => C.impacts.filter((x) => x.kind === k).length;
    const sum = el("div", "summary");
    // 깨짐 카드는 에이전트가 본 개정문 안에서 이미 고친 것일 수 있다 (연쇄 수정 breaks[0] → breaks[1])
    const autoFixed = C.breaks && C.breaks[1] === 0 && cnt("break") > 0;
    [[autoFixed ? `깨짐 → 0 자동 수정` : "깨짐", cnt("break"), cnt("break") > 0 && !autoFixed], ["충돌 해소", cnt("resolved")], ["법령 대조", cnt("law")], ["확인 필요", cnt("review") + cnt("topic")]].forEach(([t, v, hot]) => {
      const s = el("span", hot ? "hot" : null); s.append(el("b", null, String(v)), t); sum.append(s);
    });
    SD.append(sum);
    const TONE = { break: "neg", resolved: "pos", law: "accent", review: "warn", topic: "" };
    const ol = el("ol", "rows");
    C.impacts.forEach((x) => {
      const li = el("li", "row"), body = el("div", "body");
      const top = el("div", "top"); top.append(el("span", "tag " + (TONE[x.kind] || ""), x.label), el("span", "where", x.where));
      body.append(top);
      if (x.quote) body.append(el("q", null, x.quote));
      body.append(el("div", "why", x.reason));
      const dc = el("div", "decide");
      const acc = el("button", "acc"), rej = el("button", "rej"); acc.type = rej.type = "button";
      acc.append(icon("check"), "수락"); rej.append(icon("x"), "기각");
      const paint = () => {
        acc.setAttribute("aria-pressed", String(x.state === "accept")); rej.setAttribute("aria-pressed", String(x.state === "reject"));
        li.classList.toggle("rejected", x.state === "reject"); tally();
      };
      if (!editable(C)) { acc.disabled = rej.disabled = true; acc.title = rej.title = "승인·공포·반려된 개정안은 결정을 바꿀 수 없습니다"; }
      const decide = async (d) => {
        if (!editable(C)) return;
        const next = x.state === d ? "clear" : d, prev = x.state;
        x.state = next === "clear" ? null : next; paint();
        if (!LIVE || !x.key) return;
        try { await api("/api/decide", { id: C.id, key: x.key, state: next }); } catch (e) { x.state = prev; paint(); toast(e.message); }
      };
      acc.addEventListener("click", () => decide("accept")); rej.addEventListener("click", () => decide("reject"));
      dc.append(acc, rej); body.append(dc); li.append(body); ol.append(li); paint();
    });
    SD.append(ol);
    renderActions(C);
    renderFinal(C, doc);
    tally();
  }
  function tally() {
    if (!CS) return;
    const N = CS.impacts.length, na = CS.impacts.filter((x) => x.state === "accept").length, nr = CS.impacts.filter((x) => x.state === "reject").length;
    $("#tally").textContent = `수락 ${na} / 기각 ${nr} / 남음 ${N - na - nr}${LIVE ? " · 결정은 바로 저장됩니다" : " · 예시 (저장 안 됨)"}`;
    $("#p-acc").style.width = N ? (na / N) * 100 + "%" : "0"; $("#p-rej").style.width = N ? (nr / N) * 100 + "%" : "0";
  }

  // ── 데이터 불러오기 ──────────────────────────────────
  async function loadArticle(chunk) {
    if (!LIVE || !isOpenable(chunk)) return;
    try {
      const [art, m] = await Promise.all([api(`/api/article?chunk=${encodeURIComponent(chunk)}`), api(`/api/amend?chunk=${encodeURIComponent(chunk)}`)]);
      renderArticle(art); renderAmend(m);
    } catch (e) { toast(e.message); }
  }
  async function loadChangeset(id) {
    if (!LIVE) return;
    try { renderChangeset(await api(`/api/changeset?id=${encodeURIComponent(id)}`)); } catch (e) { toast(e.message); }
  }
  $("#finder").addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = $("#finder-q").value.trim(); if (!text) return;
    if (!LIVE) return toast("서버에 연결되지 않아 예시만 볼 수 있습니다");
    try { const f = await api(`/api/find?q=${encodeURIComponent(text)}`); await loadArticle(f.chunk); selectTab("src"); } catch (err) { toast(err.message); }
  });
  async function pollModel() {
    try {
      const s = await api("/api/chat/status");
      $("#model-state").textContent = s.ready ? `${s.model} · Enter 보내기 · Shift+Enter 줄바꿈` : s.error ? `답변 모델 오류: ${s.error}` : `${s.model} 준비 중… (검색 모델 올리는 중)`;
      if (!s.ready && !s.error) setTimeout(pollModel, 3000);
    } catch { /* 서버 없음 */ }
  }

  function renderSample() {
    const art = { reg: S.article.reg, label: S.article.label, revision: S.article.revision, highlight: S.article.highlight,
                  paras: S.article.paras.map((t, i) => ({ text: t, chunk: `x_${i + 1}항` })) };
    renderArticle(art);
    const F = S.amendFacts;
    renderAmend({ open: [{ id: F.openChange.id, status: F.openChange.status, title: S.changeset.title }], inbound: F.inbound, findings: [],
                  laws: [], sameTitleLaws: F.law.map((x) => ({ law: x.where, article: "", text: x.note })) });
    renderChangesetList(S.changesets.map((c) => ({ ...c, status_label: "" })));
    renderChangeset({ ...S.changeset, impacts: S.changeset.impacts.map((x) => ({ ...x, state: null })) });
  }

  (async function boot() {
    renderConvList(); renderEmpty();
    try {
      const st = await api("/api/status");
      LIVE = true;
      $("#live").className = "live"; $("#live").textContent = `DB · 청크 ${st.chunks.toLocaleString()} · ${st.db_user}`;
      pollModel();
      await refreshChangesets();
      await loadArticle(S.article.chunk_id);
      const list = await api("/api/changesets"); if (list.length) await loadChangeset(list[0].id);
    } catch (e) {
      $("#live").className = "live off"; $("#live").textContent = "예시 데이터 (서버 없음)";
      renderSample();
    }
  })();

  // ── 화면 전환·권한 ──────────────────────────────────
  function show(view) {
    $("#view-chat").hidden = view !== "chat"; $("#view-review").hidden = view !== "review";
    document.querySelectorAll(".nav button[data-view]").forEach((b) => {
      if (b.dataset.view === view && !b.closest("#cs-list")) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
    });
  }
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-view]"); if (!b) return;
    if (b.classList.contains("btn-new")) return newConv();
    show(b.dataset.view);
    if (b.dataset.cs) loadChangeset(b.dataset.cs);
  });
  document.addEventListener("keydown", (e) => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); newConv(); } });
  function setRole(role) {
    const editor = role === "editor"; ROLE_NOW = role;
    document.querySelectorAll(".seg button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.role === role)));
    document.querySelectorAll(".editor-only").forEach((x) => (x.hidden = !editor));
    if (!editor) { show("chat"); selectTab("src"); }
  }
  document.querySelectorAll(".seg button").forEach((b) => b.addEventListener("click", () => setRole(b.dataset.role)));
  setRole("editor");
})();
