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
  let LIVE = false;
  const api = async (path) => {
    const r = await fetch(path, { cache: "no-store" });
    const j = await r.json();
    if (!r.ok || j.error) throw new Error(j.error || r.statusText);
    return j;
  };
  const toast = (msg) => { const t = el("div", "toast", msg); document.body.append(t); setTimeout(() => t.remove(), 2600); };

  // 선 아이콘 (24 격자)
  const ICONS = {
    scale: "M12 3v18M5 21h14M6 7h12M6 7l-3 7a3 3 0 0 0 6 0zM18 7l-3 7a3 3 0 0 0 6 0z", plus: "M12 5v14M5 12h14",
    chat: "M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z", doc: "M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8zM14 3v5h5M9 13h6M9 17h4",
    edit: "M4 20h4L19 9l-4-4L4 16zM14 6l4 4", book: "M4 5a2 2 0 0 1 2-2h12v16H6a2 2 0 0 0-2 2zM4 21V5M8 7h6", send: "M5 12l14-7-5 14-2-6z",
    copy: "M9 9h10v10H9zM5 15V5h10", arrow: "M9 6l6 6-6 6", info: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 11v5M12 8h.01",
    download: "M12 4v11M7 10l5 5 5-5M5 20h14", spark: "M12 3l2 6 6 2-6 2-2 6-2-6-6-2 6-2z", clock: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7v5l3 2",
    up: "M7 10v10H4V10zM7 10l4-7a2 2 0 0 1 3 2l-1 5h6a2 2 0 0 1 2 2l-2 7a2 2 0 0 1-2 1H7",
    link: "M10 14a4 4 0 0 0 6 0l3-3a4 4 0 0 0-6-6l-1 1M14 10a4 4 0 0 0-6 0l-3 3a4 4 0 0 0 6 6l1-1", law: "M12 3v18M5 21h14M6 7h12",
    check: "M5 12l5 5L20 7", x: "M6 6l12 12M18 6L6 18", alert: "M12 3l9 16H3zM12 10v4M12 17h.01",
  };
  const icon = (name) => {
    const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("class", "ico"); s.setAttribute("aria-hidden", "true");
    const p = document.createElementNS("http://www.w3.org/2000/svg", "path"); p.setAttribute("d", ICONS[name] || ""); s.append(p); return s;
  };
  document.querySelectorAll("[data-icon]").forEach((n) => n.replaceWith(icon(n.dataset.icon)));
  const STATUS = { draft: ["초안", "draft"], review: ["검토 요청됨", "review"], change: ["수정 요청", "change"], done: ["확정", "done"] };

  // ── 사이드바 (대화 목록은 예시) ──
  $("#user-name").textContent = S.user.name; $("#user-dept").textContent = S.user.dept; $("#user-initial").textContent = S.user.name[0];
  S.conversations.forEach((g) => {
    $("#conv-list").append(el("div", "nav-group", g.group));
    const ul = el("ul", "nav");
    g.items.forEach((t, i) => {
      const b = el("button"); b.type = "button"; b.append(icon("chat"), el("span", "label", t));
      if (g.group === "오늘" && i === 0) b.setAttribute("aria-current", "page");
      const li = el("li"); li.append(b); ul.append(li);
    });
    $("#conv-list").append(ul);
  });
  function renderChangesetList(list) {
    const ul = clear($("#cs-list"));
    list.forEach((c) => {
      const b = el("button"); b.type = "button"; b.dataset.view = "review"; b.dataset.cs = c.id;
      const [label, cls] = STATUS[c.status] || STATUS.draft;
      b.append(icon("doc"), el("span", "label", `${c.id} · ${c.title}`), el("span", "chip " + cls, c.status_label || label));
      const li = el("li"); li.append(b); ul.append(li);
    });
  }

  // ── 질의응답 (질문·답은 예시, 근거 카드는 DB) ──
  const thread = $("#thread");
  thread.append(el("div", "q", S.question));
  const a = el("div", "a");
  const head = el("div", "a-head"); const sp = el("span", "spark"); sp.append(icon("spark"));
  head.append(sp, "규정 도우미", el("span", "chip current", "답변 완료")); a.append(head);
  const card = el("div", "a-card");
  const lead = el("p", "lead"); lead.append(el("b", null, S.answer.lead), " " + S.answer.body);
  const lab = el("div", "label-sm"); lab.append(icon("link"), "근거 조문 1건");
  const cite = el("button", "cite"); cite.type = "button";
  cite.addEventListener("click", () => { loadArticle(S.article.chunk_id); selectTab("src"); });
  const foot = el("div", "a-foot"); foot.append(icon("clock"), "질문·답은 예시 (답변 파이프라인 연결 전)");
  card.append(lead, lab, cite, foot);
  a.append(card, el("div", "label-sm", "이어서 물어볼 만한 질문"));
  const follow = el("div", "follow");
  S.followups.forEach((f) => { const b = el("button"); b.type = "button"; b.append(icon("arrow"), f); follow.append(b); });
  a.append(follow); thread.append(a);
  function renderCite(art) {
    clear(cite);
    const p = art.paras[art.highlight] || {};
    const hang = (p.chunk || "").match(/_(\d+)항$/);
    const ct = el("span"); ct.append(el("b", null, `${art.reg} ${art.label}`), el("small", null, `${hang ? `제${hang[1]}항 · ` : ""}${art.revision || ""}`));
    cite.append(icon("book"), ct, el("span", "chip current", "현행"), icon("arrow"));
  }

  // ── 근거 원문 탭 ──
  function renderArticle(art) {
    const P = clear($("#panel-src"));
    P.append(el("div", "eyebrow", "근거 원문"), el("h2", null, art.reg));
    const sub = el("div", "sub"); sub.append(el("span", "chip current", "현행"), art.revision || ""); P.append(sub);
    P.append(el("div", "art-title", art.label));
    art.paras.forEach((p, i) => {
      const n = el("p", "para" + (i === art.highlight ? " hl" : ""), p.text);
      if (p.status === "삭제") n.style.opacity = ".5";
      P.append(n);
    });
    if (i0(art)) P.querySelector(".para.hl")?.scrollIntoView({ block: "nearest" });
  }
  const i0 = (art) => art.highlight > 0;

  // ── 개정 정보 탭 (우리 기능) ──
  function renderAmend(m) {
    const A = clear($("#panel-amend"));
    if (m.open.length) m.open.forEach((o) => {
      const s = el("div", "status-banner"); s.append(icon("edit"), `개정 검토 중 · ${o.status}`, el("span", null, `— ${o.id} ${o.title}`)); A.append(s);
    });
    const fact = (ic, title, rows, emptyText) => {
      const f = el("section", "fact"); const h = el("h3"); h.append(icon(ic), title, el("span", "n", String(rows.length))); f.append(h);
      if (!rows.length) f.append(el("div", "empty", emptyText));
      rows.forEach(([b, t]) => { const r = el("div", "ref"); r.append(el("b", null, b), t || ""); f.append(r); }); A.append(f);
    };
    fact("link", "이 조를 인용하는 곳", m.inbound.map((x) => [x.where, x.quote ? `「…${x.quote}…」` : ""]), "이 조를 인용하는 내규가 없습니다");
    fact("alert", "점검 결과", m.findings.map((x) => [x.type, [x.quote && `「${x.quote}」`, x.note].filter(Boolean).join(" · ")]), "이 조에 걸린 점검 결과가 없습니다");
    fact("law", "이 조가 인용하는 법령", m.laws.map((x) => [x.where, x.quote]), "인용하는 법령이 없습니다");
    fact("doc", "제목이 같은 법령 조문", m.sameTitleLaws.map((x) => [`${x.law} ${x.article}`, x.text]), "제목이 같은 법령 조문이 없습니다");
    const go = el("button", "btn-primary"); go.type = "button";
    if (m.open.length) { go.dataset.view = "review"; go.dataset.cs = m.open[0].id; go.append(icon("edit"), `진행 중인 개정안 ${m.open[0].id} 열기`); }
    else { go.append(icon("edit"), "이 조항 개정안 작성"); go.addEventListener("click", () => toast("개정안 작성(에이전트 C) 연결은 다음 단계입니다")); }
    const hint = el("div", "hint"); hint.append(icon("info"), "개정안은 참고 문안입니다. 자동으로 반영되지 않고 담당자가 승인 또는 기각합니다.");
    A.append(go, hint);
    $("#amend-count").textContent = String(m.inbound.length + m.findings.length + m.open.length);
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
    $("#cs-id").textContent = C.id; $("#cs-title").textContent = `${C.reg} — ${C.title}`; $("#cs-status").textContent = C.status;
    const doc = clear($("#doc"));
    const p1 = el("section", "paper"); const h1 = el("h2"); h1.append(icon("doc"), "개정문"); p1.append(h1, el("p", "amend-text", C.amendment)); doc.append(p1);
    const p2 = el("section", "paper"); const h2 = el("h2"); h2.append(icon("copy"), "신·구조문 대비표"); p2.append(h2);
    const diff = el("div", "diff");
    const side = (title, parts) => {
      const d = el("div"); d.style.whiteSpace = "pre-wrap"; d.append(el("span", "head", title));
      parts.forEach((p) => d.append(typeof p === "string" ? p : p.del != null ? el("del", null, p.del) : el("ins", null, p.ins)));
      return d;
    };
    diff.append(side("현행", C.diff.old), side("개정안", C.diff.new)); p2.append(diff); doc.append(p2);
    const n = el("div", "hint"); n.append(icon("info"), "부칙 초안·질의서·법령 대조 카드는 패키지에 함께 들어갑니다 (이 화면에서는 생략)."); doc.append(n);

    const SD = clear($("#side"));
    const cnt = (k) => C.impacts.filter((x) => x.kind === k).length;
    const stats = el("div", "stats");
    [[cnt("break") ? "warn" : "zero", cnt("break"), "깨짐"], ["ok", cnt("resolved"), "충돌 해소"], ["law", cnt("law"), "법령 대조"], ["warn", cnt("review") + cnt("topic"), "확인 필요"]].forEach(([c, v, t]) => {
      const s = el("div", "stat " + c); s.append(el("b", null, String(v)), el("span", null, t)); stats.append(s);
    });
    SD.append(stats, el("div", "label-sm", `영향 검토 ${C.impacts.length}건 · 근거 조문과 함께 판단`));
    C.impacts.forEach((x, i) => {
      const c = el("article", "card"); c.dataset.kind = x.kind;
      const top = el("div", "top"); top.append(el("span", "kind", x.label)); if (x.priority) top.append(el("span", "chip plain draft", `우선 ${x.priority}`));
      c.append(top, el("div", "where", x.where));
      if (x.quote) c.append(el("div", "quote", `“${x.quote}”`));
      c.append(el("div", "why", x.reason));
      const ac = el("div", "actions");
      const rej = el("button", "rej"), acc = el("button", "acc"); rej.type = acc.type = "button";
      rej.append(icon("x"), "기각"); acc.append(icon("check"), "수락");
      rej.setAttribute("aria-pressed", "false"); acc.setAttribute("aria-pressed", "false");
      const decide = (d) => {
        decisions[i] = decisions[i] === d ? undefined : d;
        c.classList.toggle("accepted", decisions[i] === "accept"); c.classList.toggle("rejected", decisions[i] === "reject");
        acc.setAttribute("aria-pressed", String(decisions[i] === "accept")); rej.setAttribute("aria-pressed", String(decisions[i] === "reject"));
        tally(C.impacts.length);
      };
      rej.addEventListener("click", () => decide("reject")); acc.addEventListener("click", () => decide("accept"));
      ac.append(rej, acc); c.append(ac); SD.append(c);
    });
    tally(C.impacts.length);
  }
  function tally(N) {
    const v = Object.values(decisions), na = v.filter((x) => x === "accept").length, nr = v.filter((x) => x === "reject").length;
    $("#tally").textContent = `수락 ${na} · 기각 ${nr} · 미결정 ${N - na - nr}${LIVE ? " · 결정 저장은 다음 단계 (지금은 화면에만)" : ""}`;
    $("#p-acc").style.width = N ? (na / N) * 100 + "%" : "0"; $("#p-rej").style.width = N ? (nr / N) * 100 + "%" : "0";
  }

  // ── 데이터 불러오기 ──
  async function loadArticle(chunk) {
    if (!LIVE) return;
    try {
      const [art, m] = await Promise.all([api(`/api/article?chunk=${encodeURIComponent(chunk)}`), api(`/api/amend?chunk=${encodeURIComponent(chunk)}`)]);
      renderArticle(art); renderAmend(m); renderCite(art);
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

  // 예시로 그리기 (API 없음)
  function renderSample() {
    const art = { reg: S.article.reg, label: S.article.label, revision: S.article.revision, highlight: S.article.highlight,
                  paras: S.article.paras.map((t, i) => ({ text: t, chunk: `x_${i + 1}항` })) };
    renderArticle(art); renderCite(art);
    const F = S.amendFacts;
    renderAmend({ open: [{ id: F.openChange.id, status: F.openChange.status, title: "" }], inbound: F.inbound, findings: [],
                  laws: [], sameTitleLaws: F.law.map((x) => ({ law: x.where, article: "", text: x.note })) });
    renderChangesetList(S.changesets);
    renderChangeset({ ...S.changeset, impacts: S.changeset.impacts });
  }

  (async function boot() {
    try {
      const st = await api("/api/status");
      LIVE = true;
      $("#live").className = "live"; $("#live").textContent = `DB 연결됨 · 청크 ${st.chunks.toLocaleString()} · ${st.db_user}`;
      const list = await api("/api/changesets");
      renderChangesetList(list);
      await loadArticle(S.article.chunk_id);
      if (list.length) await loadChangeset(list[0].id);
    } catch (e) {
      $("#live").className = "live off"; $("#live").textContent = "예시 데이터 (DB 서버 없음)";
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
  function setRole(role) {
    const editor = role === "editor";
    document.querySelectorAll(".seg button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.role === role)));
    document.querySelectorAll(".editor-only").forEach((x) => (x.hidden = !editor));
    if (!editor) { show("chat"); selectTab("src"); }
  }
  document.querySelectorAll(".seg button").forEach((b) => b.addEventListener("click", () => setRole(b.dataset.role)));
  setRole("editor");
})();
