// 동작: 화면 전환, 권한 전환(일반 직원 / 개정 담당자), 근거 원문·개정 정보 탭, 영향 카드 수락·기각.
// 글은 모두 textContent로 넣는다 (규정 원문을 HTML로 해석하지 않게). 아이콘만 아래 고정 SVG 경로를 쓴다.
(function () {
  // 시연용: ?theme=light | dark 로 테마 고정 (없으면 운영체제 설정을 따름)
  const theme = new URLSearchParams(location.search).get("theme");
  if (theme === "light" || theme === "dark") document.documentElement.dataset.theme = theme;
  const D = window.SAMPLE;
  const $ = (s) => document.querySelector(s);
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };

  // 선 아이콘 (24 격자)
  const ICONS = {
    scale: "M12 3v18M5 21h14M6 7h12M6 7l-3 7a3 3 0 0 0 6 0zM18 7l-3 7a3 3 0 0 0 6 0z",
    plus: "M12 5v14M5 12h14",
    chat: "M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z",
    doc: "M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8zM14 3v5h5M9 13h6M9 17h4",
    edit: "M4 20h4L19 9l-4-4L4 16zM14 6l4 4",
    book: "M4 5a2 2 0 0 1 2-2h12v16H6a2 2 0 0 0-2 2zM4 21V5M8 7h6",
    send: "M5 12l14-7-5 14-2-6z",
    copy: "M9 9h10v10H9zM5 15V5h10",
    arrow: "M9 6l6 6-6 6",
    info: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 11v5M12 8h.01",
    download: "M12 4v11M7 10l5 5 5-5M5 20h14",
    spark: "M12 3l2 6 6 2-6 2-2 6-2-6-6-2 6-2z",
    clock: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7v5l3 2",
    up: "M7 10v10H4V10zM7 10l4-7a2 2 0 0 1 3 2l-1 5h6a2 2 0 0 1 2 2l-2 7a2 2 0 0 1-2 1H7",
    link: "M10 14a4 4 0 0 0 6 0l3-3a4 4 0 0 0-6-6l-1 1M14 10a4 4 0 0 0-6 0l-3 3a4 4 0 0 0 6 6l1-1",
    law: "M12 3v18M5 21h14M6 7h12",
    check: "M5 12l5 5L20 7",
    x: "M6 6l12 12M18 6L6 18",
    lock: "M6 11h12v10H6zM8 11V8a4 4 0 0 1 8 0v3",
  };
  const icon = (name) => {
    const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("class", "ico"); s.setAttribute("aria-hidden", "true");
    const p = document.createElementNS("http://www.w3.org/2000/svg", "path"); p.setAttribute("d", ICONS[name] || ""); s.append(p); return s;
  };
  document.querySelectorAll("[data-icon]").forEach((n) => n.replaceWith(icon(n.dataset.icon)));

  const STATUS = { draft: ["초안", "draft"], review: ["검토 요청됨", "review"], change: ["수정 요청", "change"], done: ["확정", "done"] };
  const decisions = {}; // 카드 번호 → "accept" | "reject" (나중에 amend.decision_log로 보냄)

  // ── 사이드바 ──
  $("#user-name").textContent = D.user.name;
  $("#user-dept").textContent = D.user.dept;
  $("#user-initial").textContent = D.user.name[0];
  const conv = $("#conv-list");
  D.conversations.forEach((g) => {
    conv.append(el("div", "nav-group", g.group));
    const ul = el("ul", "nav");
    g.items.forEach((t, i) => {
      const b = el("button"); b.type = "button"; b.append(icon("chat"), el("span", "label", t));
      if (g.group === "오늘" && i === 0) b.setAttribute("aria-current", "page");
      const li = el("li"); li.append(b); ul.append(li);
    });
    conv.append(ul);
  });
  D.changesets.forEach((c) => {
    const b = el("button"); b.type = "button"; b.dataset.view = "review";
    const [label, cls] = STATUS[c.status];
    b.append(icon("doc"), el("span", "label", c.title), el("span", "chip " + cls, label));
    const li = el("li"); li.append(b); $("#cs-list").append(li);
  });

  // ── 질의응답 ──
  const thread = $("#thread");
  thread.append(el("div", "q", D.question));
  const a = el("div", "a");
  const head = el("div", "a-head"); const sp = el("span", "spark"); sp.append(icon("spark"));
  head.append(sp, "규정 도우미", el("span", "chip current", "답변 완료")); a.append(head);
  const card = el("div", "a-card");
  const lead = el("p", "lead"); lead.append(el("b", null, D.answer.lead), " " + D.answer.body);
  const lab = el("div", "label-sm"); lab.append(icon("link"), "근거 조문 1건");
  const cite = el("button", "cite"); cite.type = "button";
  const ct = el("span"); ct.append(el("b", null, `${D.article.reg} ${D.article.label}`), el("small", null, `제2항 · ${D.article.revision}`));
  cite.append(icon("book"), ct, el("span", "chip current", "현행"), icon("arrow"));
  cite.addEventListener("click", () => { selectTab("src"); $("#panel-src").scrollTo({ top: 0, behavior: "smooth" }); });
  const foot = el("div", "a-foot"); foot.append(icon("clock"), D.answer.sec);
  const acts = el("span", "acts");
  [["copy", "답변 복사"], ["up", "도움이 됐어요"]].forEach(([n, t]) => { const b = el("button", "icon-btn"); b.type = "button"; b.setAttribute("aria-label", t); b.append(icon(n)); acts.append(b); });
  foot.append(acts);
  card.append(lead, lab, cite, foot);
  a.append(card, el("div", "label-sm", "이어서 물어볼 만한 질문"));
  const follow = el("div", "follow");
  D.followups.forEach((f) => { const b = el("button"); b.type = "button"; b.append(icon("arrow"), f); follow.append(b); });
  a.append(follow);
  thread.append(a);

  // ── 근거 원문 탭 ──
  const P = $("#panel-src"), art = D.article, F = D.amendFacts;
  P.append(el("div", "eyebrow", "근거 원문"), el("h2", null, art.reg));
  const sub = el("div", "sub"); sub.append(el("span", "chip current", "현행"), art.revision); P.append(sub);
  P.append(el("div", "art-title", art.label));
  art.paras.forEach((t, i) => P.append(el("p", "para" + (i === art.highlight ? " hl" : ""), t)));

  // ── 개정 정보 탭 (우리 기능) ──
  const A = $("#panel-amend");
  if (F.openChange) {
    const s = el("div", "status-banner"); s.append(icon("edit"), `개정 검토 중 · ${F.openChange.status}`, el("span", null, `— 개정안 ${F.openChange.id}`)); A.append(s);
  }
  const fact = (ic, title, rows) => {
    const f = el("section", "fact"); const h = el("h3"); h.append(icon(ic), title, el("span", "n", String(rows.length))); f.append(h);
    rows.forEach(([b, t]) => { const r = el("div", "ref"); r.append(el("b", null, b), t); f.append(r); }); A.append(f);
  };
  fact("link", "이 조를 인용하는 곳", F.inbound.map((x) => [x.where, `「${x.quote}」`]));
  fact("doc", "같은 내용을 정한 조문", F.sameTopic.map((x) => [x.where, `「${x.quote}」 · ${x.note}`]));
  fact("law", "관련 법령", F.law.map((x) => [x.where, x.note]));
  const go = el("button", "btn-primary"); go.type = "button"; go.dataset.view = "review"; go.append(icon("edit"), "이 조항 개정안 작성");
  const hint = el("div", "hint"); hint.append(icon("info"), "개정안은 참고 문안입니다. 자동으로 반영되지 않고 담당자가 승인 또는 기각합니다.");
  A.append(go, hint);
  $("#amend-count").textContent = String(F.inbound.length + F.sameTopic.length + F.law.length);

  function selectTab(k) {
    const src = k === "src";
    $("#tab-src").setAttribute("aria-selected", String(src)); $("#tab-amend").setAttribute("aria-selected", String(!src));
    $("#panel-src").hidden = !src; $("#panel-amend").hidden = src;
  }
  $("#tab-src").addEventListener("click", () => selectTab("src"));
  $("#tab-amend").addEventListener("click", () => selectTab("amend"));

  // ── 개정안 검토 ──
  const C = D.changeset;
  $("#cs-id").textContent = C.id;
  $("#cs-title").textContent = `${C.reg} — ${C.title}`;
  $("#cs-status").textContent = C.status;
  const doc = $("#doc");
  const p1 = el("section", "paper"); const h1 = el("h2"); h1.append(icon("doc"), "개정문"); p1.append(h1, el("p", "amend-text", C.amendment)); doc.append(p1);
  const p2 = el("section", "paper"); const h2 = el("h2"); h2.append(icon("copy"), "신·구조문 대비표"); p2.append(h2);
  const diff = el("div", "diff");
  const side = (title, parts) => { const d = el("div"); d.append(el("span", "head", title)); parts.forEach((p) => d.append(typeof p === "string" ? p : p.del ? el("del", null, p.del) : el("ins", null, p.ins))); return d; };
  diff.append(side("현행", C.diff.old), side("개정안", C.diff.new)); p2.append(diff); doc.append(p2);
  const n = el("div", "hint"); n.append(icon("info"), "부칙 초안·질의서·법령 대조 카드는 패키지에 함께 들어갑니다 (이 틀에서는 생략)."); doc.append(n);

  const S = $("#side");
  const cnt = (k) => C.impacts.filter((x) => x.kind === k).length;
  const stats = el("div", "stats");
  [["zero", 0, "깨짐"], ["ok", cnt("resolved"), "충돌 해소"], ["law", cnt("law"), "법령 대조"], ["warn", cnt("review") + cnt("topic"), "확인 필요"]].forEach(([c, v, t]) => {
    const s = el("div", "stat " + c); s.append(el("b", null, String(v)), el("span", null, t)); stats.append(s);
  });
  S.append(stats, el("div", "label-sm", `영향 검토 ${C.impacts.length}건 · 근거 조문과 함께 판단`));
  C.impacts.forEach((x, i) => {
    const c = el("article", "card"); c.dataset.kind = x.kind;
    const top = el("div", "top"); top.append(el("span", "kind", x.label)); c.append(top, el("div", "where", x.where));
    if (x.quote) c.append(el("div", "quote", `“${x.quote}”`));
    c.append(el("div", "why", x.reason));
    const ac = el("div", "actions");
    const rej = el("button", "rej"), acc = el("button", "acc"); rej.type = acc.type = "button";
    rej.append(icon("x"), "기각"); acc.append(icon("check"), "수락");
    rej.setAttribute("aria-pressed", "false"); acc.setAttribute("aria-pressed", "false");
    rej.addEventListener("click", () => decide(i, c, "reject", acc, rej)); acc.addEventListener("click", () => decide(i, c, "accept", acc, rej));
    ac.append(rej, acc); c.append(ac); S.append(c);
  });
  function decide(i, card, d, acc, rej) {
    decisions[i] = decisions[i] === d ? undefined : d;
    card.classList.toggle("accepted", decisions[i] === "accept");
    card.classList.toggle("rejected", decisions[i] === "reject");
    acc.setAttribute("aria-pressed", String(decisions[i] === "accept"));
    rej.setAttribute("aria-pressed", String(decisions[i] === "reject"));
    tally();
  }
  function tally() {
    const v = Object.values(decisions), N = C.impacts.length;
    const na = v.filter((x) => x === "accept").length, nr = v.filter((x) => x === "reject").length;
    $("#tally").textContent = `수락 ${na} · 기각 ${nr} · 미결정 ${N - na - nr}`;
    $("#p-acc").style.width = (na / N) * 100 + "%"; $("#p-rej").style.width = (nr / N) * 100 + "%";
  }
  tally();

  // ── 화면 전환·권한 ──
  function show(view) {
    $("#view-chat").hidden = view !== "chat";
    $("#view-review").hidden = view !== "review";
    document.querySelectorAll(".nav button[data-view]").forEach((b) => {
      if (b.dataset.view === view && !b.closest("#cs-list")) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
    });
  }
  document.addEventListener("click", (e) => { const b = e.target.closest("[data-view]"); if (b) show(b.dataset.view); });
  function setRole(role) {
    const editor = role === "editor";
    document.querySelectorAll(".seg button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.role === role)));
    document.querySelectorAll(".editor-only").forEach((x) => (x.hidden = !editor));
    if (!editor) { show("chat"); selectTab("src"); }
  }
  document.querySelectorAll(".seg button").forEach((b) => b.addEventListener("click", () => setRole(b.dataset.role)));
  setRole("editor");
})();
