// 동작: 공유 DB API(app/web_server.py)에서 근거 원문·개정 정보·개정안을 불러와 그린다.
// API가 없으면(파일로 직접 열었거나 서버가 꺼짐) sample-data.js 예시로 그린다.
// 질의응답의 질문·답은 아직 예시다 (가빈 답변 파이프라인 연결 전). 근거 조문부터는 DB 값.
// 글은 모두 textContent로 넣는다 (규정 원문을 HTML로 해석하지 않게).
(function () {
  const theme = new URLSearchParams(location.search).get("theme");
  if (theme === "light" || theme === "dark") document.documentElement.dataset.theme = theme;
  const S = window.SAMPLE;
  const $ = (s) => document.querySelector(s);
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
  const clear = (n) => { while (n.firstChild) n.firstChild.remove(); return n; };
  let LIVE = false, CURRENT = null;
  const api = async (path) => {
    const r = await fetch(path, { cache: "no-store" });
    const j = await r.json();
    if (!r.ok || j.error) throw new Error(j.error || r.statusText);
    return j;
  };
  const toast = (msg) => { const t = el("div", "toast", msg); document.body.append(t); setTimeout(() => t.remove(), 2600); };

  // 선 아이콘 — 기능이 있는 곳에만
  const ICONS = {
    plus: "M12 5v14M5 12h14", chat: "M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z",
    doc: "M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8zM14 3v5h5", edit: "M4 20h4L19 9l-4-4L4 16z",
    copy: "M9 9h10v10H9zM5 15V5h10", download: "M12 4v11M7 10l5 5 5-5M5 20h14", search: "M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM20 20l-4-4",
    "up-arrow": "M12 19V5M6 11l6-6 6 6", up: "M7 10v10H4V10zM7 10l4-7a2 2 0 0 1 3 2l-1 5h6a2 2 0 0 1 2 2l-2 7a2 2 0 0 1-2 1H7",
    check: "M5 12l5 5L20 7", x: "M6 6l12 12M18 6L6 18", arrow: "M5 12h14M13 6l6 6-6 6",
  };
  const icon = (name) => {
    const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("class", "ico"); s.setAttribute("aria-hidden", "true");
    const p = document.createElementNS("http://www.w3.org/2000/svg", "path"); p.setAttribute("d", ICONS[name] || ""); s.append(p); return s;
  };
  document.querySelectorAll("[data-icon]").forEach((n) => n.replaceWith(icon(n.dataset.icon)));
  const STATUS = { draft: "초안", review: "검토 요청됨", change: "수정 요청", done: "확정" };
  const STATUS_TONE = { draft: "", review: "accent", change: "warn", done: "pos" };

  // ── 사이드바 (대화 목록은 예시) ──
  $("#user-name").textContent = S.user.name; $("#user-dept").textContent = S.user.dept; $("#user-initial").textContent = S.user.name[0];
  S.conversations.forEach((g) => {
    $("#conv-list").append(el("div", "nav-group", g.group));
    const ul = el("ul", "nav");
    g.items.forEach((t, i) => {
      const b = el("button"); b.type = "button"; b.append(el("span", "label", t));
      if (g.group === "오늘" && i === 0) b.setAttribute("aria-current", "page");
      const li = el("li"); li.append(b); ul.append(li);
    });
    $("#conv-list").append(ul);
  });
  function renderChangesetList(list) {
    const ul = clear($("#cs-list"));
    list.forEach((c) => {
      const b = el("button"); b.type = "button"; b.dataset.view = "review"; b.dataset.cs = c.id;
      b.append(el("span", "tag", c.id), el("span", "label", c.title.replace(/\s*\([^)]*\)\s*$/, "")));
      const li = el("li"); li.append(b); ul.append(li);
    });
  }

  // ── 질의응답 (질문·답은 예시, 근거는 DB) ──
  const thread = $("#thread");
  const q = el("div", "q"); q.append(el("span", null, S.question)); thread.append(q);
  const a = el("div", "a");
  const head = el("div", "a-head"); head.append(el("b", null, "규정 도우미"), el("span", "label", "근거 1건"));
  const lead = el("p", "lead"); lead.append(el("b", null, S.answer.lead), " " + S.answer.body, el("sup", null, "1"));
  const sources = el("div", "sources"); sources.append(el("span", "label", "근거 조문"));
  const cite = el("button", "cite"); cite.type = "button";
  cite.addEventListener("click", () => { loadArticle(S.article.chunk_id); selectTab("src"); });
  sources.append(cite);
  const foot = el("div", "a-foot"); foot.append("질문과 답은 예시 · 답변 파이프라인 연결 전");
  const acts = el("span", "acts");
  [["copy", "답변 복사"], ["up", "도움이 됐어요"]].forEach(([n, t]) => { const b = el("button", "icon-btn"); b.type = "button"; b.setAttribute("aria-label", t); b.append(icon(n)); acts.append(b); });
  foot.append(acts);
  const follow = el("div", "follow");
  S.followups.forEach((f) => { const b = el("button"); b.type = "button"; b.append(f, icon("arrow")); follow.append(b); });
  a.append(head, lead, sources, foot, el("span", "label", "이어서 물어볼 만한 질문"), follow);
  thread.append(a);
  function renderCite(art) {
    clear(cite);
    const p = art.paras[art.highlight] || {};
    const hang = (p.chunk || "").match(/_(\d+)항$/);
    const t = el("span"); t.append(`${art.reg} ${art.label}`, el("small", null, hang ? `제${hang[1]}항` : ""));
    cite.append(el("span", "num", "1"), t, el("span", "tag pos", "현행"), el("small", null, art.revision || ""));
  }

  // ── 근거 원문 탭 ──
  function renderArticle(art) {
    CURRENT = art;
    const P = clear($("#panel-src"));
    const h = el("header", "doc-head");
    const meta = el("div", "meta"); meta.append(el("span", "tag pos", "현행"), art.revision || "");
    h.append(el("h2", null, art.reg), meta);
    P.append(h, el("h3", "art-title", art.label));
    art.paras.forEach((p, i) => P.append(el("p", "para" + (i === art.highlight ? " hl" : "") + (p.status === "삭제" ? " del" : ""), p.text)));
    if (art.highlight > 0) P.querySelector(".para.hl")?.scrollIntoView({ block: "center" });
  }
  $("#copy-art").addEventListener("click", async () => {
    if (!CURRENT) return;
    const text = [`${CURRENT.reg} ${CURRENT.label}`, ...CURRENT.paras.map((p) => p.text)].join("\n");
    try { await navigator.clipboard.writeText(text); toast("조문을 복사했습니다"); } catch { toast("복사하지 못했습니다"); }
  });

  // ── 개정 정보 탭 (우리 기능) ──
  function renderAmend(m) {
    const A = clear($("#panel-amend"));
    if (m.open.length) {
      const o = m.open[0], n = el("div", "notice");
      const t = el("b"); t.append("개정 검토 중 ");
      n.append(el("span", "tag warn", o.status), t, el("p", null, `${o.id} — ${o.title}`)); A.append(n);
    }
    const sect = (title, rows, emptyText) => {
      const s = el("section", "sect"); const h = el("h3"); h.append(title, el("span", "num" + (rows.length ? " on" : ""), String(rows.length).padStart(2, "0"))); s.append(h);
      if (!rows.length) s.append(el("div", "none", emptyText));
      rows.forEach(([b, t]) => { const r = el("div", "item"); r.append(el("b", null, b)); if (t) r.append(el("q", null, t)); s.append(r); });
      A.append(s);
    };
    sect("이 조를 인용하는 곳", m.inbound.map((x) => [x.where, x.quote ? `…${x.quote}…` : ""]), "인용하는 내규 없음");
    sect("점검 결과", m.findings.map((x) => [x.type, [x.quote && `「${x.quote}」`, x.note].filter(Boolean).join(" · ")]), "이 조에 걸린 점검 결과 없음");
    sect("이 조가 인용하는 법령", m.laws.map((x) => [x.where, x.quote]), "인용하는 법령 없음");
    sect("제목이 같은 법령 조문", m.sameTitleLaws.map((x) => [`${x.law} ${x.article}`, x.text]), "해당 없음");
    const row = el("div", "actions-row");
    const go = el("button", "btn primary"); go.type = "button";
    if (m.open.length) { go.dataset.view = "review"; go.dataset.cs = m.open[0].id; go.append(`개정안 ${m.open[0].id} 열기`, icon("arrow")); }
    else { go.append(icon("edit"), "이 조항 개정안 작성"); go.addEventListener("click", () => toast("개정안 작성(에이전트 C) 연결은 다음 단계입니다")); }
    row.append(go, el("p", "hint", "개정안은 참고 문안입니다. 자동으로 반영되지 않고 담당자가 승인 또는 기각합니다."));
    A.append(row);
    const n = m.inbound.length + m.findings.length + m.open.length;
    $("#amend-count").textContent = n ? String(n) : "";
  }

  function selectTab(k) {
    const src = k === "src";
    $("#tab-src").setAttribute("aria-selected", String(src)); $("#tab-amend").setAttribute("aria-selected", String(!src));
    $("#panel-src").hidden = !src; $("#panel-amend").hidden = src;
  }
  $("#tab-src").addEventListener("click", () => selectTab("src"));
  $("#tab-amend").addEventListener("click", () => selectTab("amend"));

  // ── 개정안 검토 ──
  const decisions = {};
  function renderChangeset(C) {
    Object.keys(decisions).forEach((k) => delete decisions[k]);
    $("#cs-id").textContent = C.id; $("#cs-title").textContent = `${C.reg} — ${C.title.replace(/\s*\([^)]*\)\s*$/, "")}`;
    $("#cs-status").textContent = C.status;
    const doc = clear($("#doc"));
    const s1 = el("section"); s1.append(el("h2", null, "개정문"), el("p", "amend-text", C.amendment)); doc.append(s1);
    const s2 = el("section"); s2.append(el("h2", null, "신·구조문 대비표"));
    const diff = el("div", "diff");
    const side = (title, parts) => {
      const d = el("div"); d.append(el("span", "head", title));
      parts.forEach((p) => d.append(typeof p === "string" ? p : p.del != null ? el("del", null, p.del) : el("ins", null, p.ins)));
      return d;
    };
    diff.append(side("현행", C.diff.old), side("개정안", C.diff.new)); s2.append(diff); doc.append(s2);
    doc.append(el("p", "hint", "부칙 초안·질의서·법령 대조 카드는 패키지에 함께 들어갑니다."));

    const SD = clear($("#side"));
    const cnt = (k) => C.impacts.filter((x) => x.kind === k).length;
    const sum = el("div", "summary");
    [["깨짐", cnt("break"), cnt("break") > 0], ["충돌 해소", cnt("resolved")], ["법령 대조", cnt("law")], ["확인 필요", cnt("review") + cnt("topic")]].forEach(([t, v, hot]) => {
      const s = el("span", hot ? "hot" : null); s.append(el("b", null, String(v)), t); sum.append(s);
    });
    SD.append(sum);
    const TONE = { break: "neg", resolved: "pos", law: "accent", review: "warn", topic: "" };
    const ol = el("ol", "rows");
    C.impacts.forEach((x, i) => {
      const li = el("li", "row"), body = el("div", "body");
      const top = el("div", "top"); top.append(el("span", "tag " + (TONE[x.kind] || ""), x.label), el("span", "where", x.where));
      body.append(top);
      if (x.quote) body.append(el("q", null, x.quote));
      body.append(el("div", "why", x.reason));
      const dc = el("div", "decide");
      const rej = el("button", "rej"), acc = el("button", "acc"); rej.type = acc.type = "button";
      rej.append(icon("x"), "기각"); acc.append(icon("check"), "수락");
      rej.setAttribute("aria-pressed", "false"); acc.setAttribute("aria-pressed", "false");
      const decide = (d) => {
        decisions[i] = decisions[i] === d ? undefined : d;
        li.classList.toggle("rejected", decisions[i] === "reject");
        acc.setAttribute("aria-pressed", String(decisions[i] === "accept")); rej.setAttribute("aria-pressed", String(decisions[i] === "reject"));
        tally(C.impacts.length);
      };
      rej.addEventListener("click", () => decide("reject")); acc.addEventListener("click", () => decide("accept"));
      dc.append(acc, rej); body.append(dc); li.append(body); ol.append(li);
    });
    SD.append(ol);
    tally(C.impacts.length);
  }
  function tally(N) {
    const v = Object.values(decisions), na = v.filter((x) => x === "accept").length, nr = v.filter((x) => x === "reject").length;
    $("#tally").textContent = `수락 ${na} / 기각 ${nr} / 남음 ${N - na - nr}${LIVE ? "  ·  결정 저장은 다음 단계" : ""}`;
    $("#p-acc").style.width = N ? (na / N) * 100 + "%" : "0"; $("#p-rej").style.width = N ? (nr / N) * 100 + "%" : "0";
  }

  // ── 데이터 불러오기 ──
  async function loadArticle(chunk) {
    if (!LIVE) return;
    try {
      const [art, m] = await Promise.all([api(`/api/article?chunk=${encodeURIComponent(chunk)}`), api(`/api/amend?chunk=${encodeURIComponent(chunk)}`)]);
      renderArticle(art); renderAmend(m); if (chunk === S.article.chunk_id) renderCite(art);
    } catch (e) { toast(e.message); }
  }
  async function loadChangeset(id) {
    if (!LIVE) return;
    try { renderChangeset(await api(`/api/changeset?id=${encodeURIComponent(id)}`)); } catch (e) { toast(e.message); }
  }
  $("#finder").addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = $("#finder-q").value.trim(); if (!text) return;
    if (!LIVE) return toast("DB에 연결되지 않아 예시만 볼 수 있습니다");
    try { const f = await api(`/api/find?q=${encodeURIComponent(text)}`); await loadArticle(f.chunk); selectTab("src"); } catch (err) { toast(err.message); }
  });

  function renderSample() {
    const art = { reg: S.article.reg, label: S.article.label, revision: S.article.revision, highlight: S.article.highlight,
                  paras: S.article.paras.map((t, i) => ({ text: t, chunk: `x_${i + 1}항` })) };
    renderArticle(art); renderCite(art);
    const F = S.amendFacts;
    renderAmend({ open: [{ id: F.openChange.id, status: F.openChange.status, title: S.changeset.title }], inbound: F.inbound, findings: [],
                  laws: [], sameTitleLaws: F.law.map((x) => ({ law: x.where, article: "", text: x.note })) });
    renderChangesetList(S.changesets.map((c) => ({ ...c, status_label: STATUS[c.status] })));
    renderChangeset(S.changeset);
  }

  (async function boot() {
    try {
      const st = await api("/api/status");
      LIVE = true;
      $("#live").className = "live"; $("#live").textContent = `DB · 청크 ${st.chunks.toLocaleString()} · ${st.db_user}`;
      const list = await api("/api/changesets");
      renderChangesetList(list);
      await loadArticle(S.article.chunk_id);
      if (list.length) await loadChangeset(list[0].id);
    } catch (e) {
      $("#live").className = "live off"; $("#live").textContent = "예시 데이터";
      renderSample();
    }
  })();

  // ── 화면 전환·권한 ──
  function show(view) {
    $("#view-chat").hidden = view !== "chat"; $("#view-review").hidden = view !== "review";
    document.querySelectorAll(".nav button[data-view]").forEach((b) => {
      if (b.dataset.view === view && !b.closest("#cs-list")) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
    });
  }
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-view]"); if (!b) return;
    show(b.dataset.view);
    if (b.dataset.cs) loadChangeset(b.dataset.cs);
  });
  document.addEventListener("keydown", (e) => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); show("chat"); document.querySelector(".composer textarea").focus(); } });
  function setRole(role) {
    const editor = role === "editor";
    document.querySelectorAll(".seg button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.role === role)));
    document.querySelectorAll(".editor-only").forEach((x) => (x.hidden = !editor));
    if (!editor) { show("chat"); selectTab("src"); }
  }
  document.querySelectorAll(".seg button").forEach((b) => b.addEventListener("click", () => setRole(b.dataset.role)));
  setRole("editor");
})();
