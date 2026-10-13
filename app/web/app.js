// 기본 틀 동작: 화면 전환, 권한 전환(일반 직원 / 개정 담당자), 영향 카드 수락·기각.
// 글은 모두 textContent로 넣는다 (규정 원문을 HTML로 해석하지 않게).
(function () {
  const D = window.SAMPLE;
  const $ = (s) => document.querySelector(s);
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
  const STATUS = { draft: ["초안", "draft"], review: ["검토 요청됨", "review"], change: ["수정 요청", "change"], done: ["확정", "done"] };
  const decisions = {}; // 카드 번호 → "accept" | "reject" (나중에 amend.decision_log로 보냄)

  // ── 사이드바 ──
  $("#user-name").textContent = `${D.user.name} · ${D.user.dept}`;
  $("#user-initial").textContent = D.user.name[0];
  const conv = $("#conv-list");
  D.conversations.forEach((g) => {
    conv.append(el("div", "nav-group", g.group));
    const ul = el("ul", "nav");
    g.items.forEach((t, i) => { const li = el("li"); const b = el("button", null, "💬 " + t); b.type = "button"; if (g.group === "오늘" && i === 0) b.setAttribute("aria-current", "page"); li.append(b); ul.append(li); });
    conv.append(ul);
  });
  const csList = $("#cs-list");
  D.changesets.forEach((c) => {
    const li = el("li"); const b = el("button"); b.type = "button"; b.dataset.view = "review";
    const [label, cls] = STATUS[c.status];
    const t = el("span", null, c.title); t.style.cssText = "flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap";
    b.append(t, el("span", "chip " + cls, label)); li.append(b); csList.append(li);
  });

  // ── 질의응답 ──
  const thread = $("#thread");
  thread.append(el("div", "q", D.question));
  const a = el("div", "a");
  a.append(el("div", "a-head", "✦ 규정 도우미"));
  const card = el("div", "a-card");
  const lead = el("p", null); lead.style.margin = "0"; lead.append(el("b", null, D.answer.lead), " " + D.answer.body);
  const cite = el("button", "cite"); cite.type = "button";
  const ct = el("span"); ct.append(el("b", null, `${D.article.reg} ${D.article.label}`), el("small", null, `제2항 · ${D.article.revision}`));
  cite.append("📖", ct, el("span", "chip current", "현행"));
  cite.addEventListener("click", () => document.getElementById("panel").scrollTo({ top: 0, behavior: "smooth" }));
  card.append(lead, el("div", "note", "근거 조문 1건"), cite, el("div", "note", `⏱ ${D.answer.sec}`));
  a.append(card, el("div", "note", "이어서 물어볼 만한 질문"));
  const follow = el("div", "follow");
  D.followups.forEach((f) => { const b = el("button", null, "→ " + f); b.type = "button"; follow.append(b); });
  a.append(follow);
  thread.append(a);

  // ── 근거 원문 패널 ──
  const P = $("#panel"), art = D.article, F = D.amendFacts;
  P.append(el("div", "note", "📖 근거 원문"), el("h2", null, art.reg));
  const sub = el("div", "sub"); sub.append(el("span", "chip current", "현행"), art.revision); P.append(sub);
  P.append(el("div", "art-title", art.label));
  art.paras.forEach((t, i) => P.append(el("p", "para" + (i === art.highlight ? " hl" : ""), t)));

  // 개정 담당자 영역 (우리 기능 자리)
  const box = el("section", "amend-box editor-only"); box.setAttribute("aria-label", "개정 담당자 정보");
  const h = el("h3", null, "개정 담당자");
  if (F.openChange) h.append(el("span", "chip badge-open", `개정 검토 중 · ${F.openChange.status}`));
  box.append(h);
  const facts = el("ul", "facts");
  const fact = (title, rows) => { const li = el("li"); li.append(el("b", null, title)); rows.forEach((r) => li.append(el("span", "ref", r))); facts.append(li); };
  fact(`이 조를 인용하는 곳 ${F.inbound.length}건`, F.inbound.map((x) => `${x.where} — 「${x.quote}」`));
  fact(`같은 내용을 정한 조문 ${F.sameTopic.length}건`, F.sameTopic.map((x) => `${x.where} — 「${x.quote}」 (${x.note})`));
  fact(`관련 법령 ${F.law.length}건`, F.law.map((x) => `${x.where} — ${x.note}`));
  box.append(facts);
  const go = el("button", "btn-primary", "이 조항 개정안 작성 →"); go.type = "button"; go.dataset.view = "review";
  box.append(go, el("div", "note", "개정안은 참고 문안입니다. 자동으로 반영되지 않고 담당자가 승인합니다."));
  P.append(box);

  // ── 개정안 검토 ──
  const C = D.changeset;
  $("#cs-title").textContent = `${C.reg} — ${C.title}`;
  $("#cs-status").textContent = C.status;
  const doc = $("#doc");
  const legend = el("div", "legend");
  [["var(--red)", "삭제"], ["var(--green)", "추가"]].forEach(([c, t]) => { const s = el("span"); const i = el("i"); i.style.background = c; s.append(i, t); legend.append(s); });
  doc.append(legend);
  const p1 = el("div", "paper"); p1.append(el("h2", null, "개정문"), el("p", "amend-text", C.amendment)); doc.append(p1);
  const p2 = el("div", "paper"); p2.append(el("h2", null, "신·구조문 대비표"));
  const tbl = el("table", "diff"); const tr1 = el("tr"); tr1.append(el("th", null, "현행"), el("th", null, "개정안")); const tr2 = el("tr");
  const cell = (parts) => { const td = el("td"); parts.forEach((p) => td.append(typeof p === "string" ? p : p.del ? el("del", null, p.del) : el("ins", null, p.ins))); return td; };
  tr2.append(cell(C.diff.old), cell(C.diff.new)); tbl.append(tr1, tr2); p2.append(tbl); doc.append(p2);
  doc.append(el("div", "note", "ⓘ 부칙 초안·질의서·법령 대조 카드는 패키지에 함께 들어갑니다 (이 틀에서는 생략)."));

  const cards = $("#cards");
  const counts = {}; C.impacts.forEach((x) => (counts[x.label] = (counts[x.label] || 0) + 1));
  const sum = el("div", "summary");
  const color = { "충돌 해소": "var(--green)", "법령 대조": "var(--blue)", "확인 필요": "var(--orange)", "같은 주제": "var(--text-3)", "깨짐": "var(--red)" };
  sum.append(el("span", null, "깨짐 0"));
  Object.entries(counts).forEach(([k, v]) => { const s = el("span"); const d = el("span", "dot"); d.style.background = color[k] || "var(--text-3)"; s.append(d, `${k} ${v}`); sum.append(s); });
  cards.append(sum, el("div", "note", `영향 검토 ${C.impacts.length}건`));
  C.impacts.forEach((x, i) => {
    const c = el("article", "card");
    c.append(el("span", "kind " + x.kind, x.label), el("div", "where", x.where));
    if (x.quote) c.append(el("div", "quote", `“${x.quote}”`));
    c.append(el("div", null, x.reason));
    const acts = el("div", "actions");
    const acc = el("button", "btn-line", "수락"), rej = el("button", "btn-line", "기각"); acc.type = rej.type = "button";
    acc.addEventListener("click", () => decide(i, c, "accept")); rej.addEventListener("click", () => decide(i, c, "reject"));
    acts.append(rej, acc); c.append(acts); cards.append(c);
  });
  function decide(i, card, d) {
    decisions[i] = decisions[i] === d ? undefined : d;
    card.classList.toggle("accepted", decisions[i] === "accept");
    card.classList.toggle("rejected", decisions[i] === "reject");
    tally();
  }
  function tally() {
    const v = Object.values(decisions);
    $("#tally").textContent = `수락 ${v.filter((x) => x === "accept").length} · 기각 ${v.filter((x) => x === "reject").length} · 미결정 ${C.impacts.length - v.filter(Boolean).length}`;
  }
  tally();

  // ── 화면 전환·권한 ──
  function show(view) {
    $("#view-chat").hidden = view !== "chat";
    $("#view-review").hidden = view !== "review";
    document.querySelectorAll(".nav button[data-view]").forEach((b) => {
      if (b.dataset.view === view && b.closest("#cs-list") === null) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
    });
  }
  document.addEventListener("click", (e) => { const b = e.target.closest("[data-view]"); if (b) show(b.dataset.view); });
  function applyRole() {
    const editor = $("#role").value === "editor";
    document.querySelectorAll(".editor-only").forEach((n) => (n.hidden = !editor));
    if (!editor) show("chat");
  }
  $("#role").addEventListener("change", applyRole);
  applyRole();
})();
