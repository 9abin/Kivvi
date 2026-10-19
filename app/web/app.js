// 규정 도우미 화면 동작 — 서버(app/web_server.py) API를 쓴다. 서버가 없으면 sample-data.js 예시로 근거·개정 화면만 그린다.
//  채팅: POST /api/ask (가빈 답변 파이프라인 + 로컬 9B). 답변의 [n]을 누르면 그 근거 조문이 오른쪽에 열린다.
//  개정: 근거 원문 → 개정 정보 → 개정안 작성(POST /api/draft) → 개정안 검토(수락·기각 POST /api/decide, 제출 POST /api/submit, 패키지 내려받기)
//  대화 기록은 이 브라우저에만 저장 (localStorage). 글은 모두 textContent로 넣는다 (원문·답변을 HTML로 해석하지 않게).
(function () {
  const theme = new URLSearchParams(location.search).get("theme");
  if (theme === "light" || theme === "dark") document.documentElement.dataset.theme = theme;
  const S = window.SAMPLE;
  // 역할(시연용 전환): 일반 직원 / 개정 담당자(기안·결정·제출·공포 처리) / 승인권자(승인·반려). 서버가 역할을 확인한다 (app/web_agent.guard)
  // 시연용 사람 (역할마다 이름·부서를 둔다: 아바타는 성씨, 부서 줄이 비면 하단 칸 높이가 역할마다 달라졌음 — 261019)
  const PERSON = { employee: { name: "김지원", dept: "경영지원팀 (시연)" }, editor: { name: S.user.name, dept: S.user.dept }, approver: { name: "이준호", dept: "대표이사 (시연)" } };
  let USER = S.user.name;
  const $ = (s) => document.querySelector(s);
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
  const clear = (n) => { while (n.firstChild) n.firstChild.remove(); return n; };
  let LIVE = false, CURRENT = null, CS = null, ROLE_NOW = "editor", CS_ALL = [];   // CS_ALL: 개정안 목록 (개정 업무 화면의 할 일)
  const isEditor = () => ROLE_NOW === "editor";
  const isApprover = () => ROLE_NOW === "approver";
  const api = async (path, body, signal) => {
    const r = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user: USER, role: ROLE_NOW, ...body }), signal } : { cache: "no-store", signal });
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
    book: "M2 4h6a4 4 0 0 1 4 4v13a3 3 0 0 0-3-3H2zM22 4h-6a4 4 0 0 0-4 4v13a3 3 0 0 1 3-3h7z", panel: "M3 3h18v18H3zM15 3v18",
    sparkle: "M12 3l1.9 5.8L20 10.7l-5.6 2.4L12 19l-2.4-5.9L4 10.7l6.1-1.9z", info: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM12 16v-4M12 8h.01",
    clock: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM12 6v6l4 2", chev: "M9 6l6 6-6 6", "check-c": "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM8 12l3 3 5-5",
    "search-x": "M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.3-4.3M8.5 8.5l5 5M13.5 8.5l-5 5", stop: "M7 7h10v10H7z", tool: "M14 7l3-3 3 3-3 3M17 4L9 12M4 20l5-5 2 2-5 5z",
  };
  const icon = (name) => {
    const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("class", "ico"); s.setAttribute("aria-hidden", "true");
    const p = document.createElementNS("http://www.w3.org/2000/svg", "path"); p.setAttribute("d", ICONS[name] || ""); s.append(p); return s;
  };
  document.querySelectorAll("[data-icon]").forEach((n) => n.replaceWith(icon(n.dataset.icon)));
  $("#user-name").textContent = USER; $("#user-dept").textContent = S.user.dept; $("#user-initial").textContent = USER[0];

  // ── 대화 기록 (이 브라우저에만) ─────────────────────────
  // 역할(일반 직원·개정 담당자·승인권자)은 서로 다른 사람이므로 대화 기록도 역할마다 따로 둔다 (261019 사용자 지적: 셋이 같은 목록을 보던 문제).
  // 키 "kvic-chat-v1:<역할>". 예전 키 "kvic-chat-v1"(역할 구분 없던 때)의 기록은 개정 담당자 것으로 이어받는다
  const STORE = "kvic-chat-v1";
  const storeKey = (role) => `${STORE}:${role}`;
  const loadFor = (role) => {
    try {
      const v = JSON.parse(localStorage.getItem(storeKey(role)));
      if (v) return v;
      return role === "editor" ? (JSON.parse(localStorage.getItem(STORE)) || []) : [];
    } catch { return []; }
  };
  const saveFor = (role, v) => { try { localStorage.setItem(storeKey(role), JSON.stringify(v.slice(0, 30))); } catch { /* 저장 못 해도 화면은 동작 */ } };
  const load = () => loadFor(ROLE_NOW);
  const save = (v) => saveFor(ROLE_NOW, v);
  let convs = load(), convId = null;
  const conv = () => convs.find((c) => c.id === convId);

  function renderConvList() {
    const box = clear($("#conv-list"));
    if (!convs.length) { box.append(el("div", "nav-group", "대화 기록 없음")); return; }
    const day = (t) => { const d = new Date(t); d.setHours(0, 0, 0, 0); return d.getTime(); };
    const today = day(Date.now()), DAY = 86400000;
    const groups = [["오늘", (c) => day(c.at) === today], ["어제", (c) => day(c.at) === today - DAY],
                    ["지난 7일", (c) => day(c.at) < today - DAY && day(c.at) >= today - 7 * DAY], ["이전", (c) => day(c.at) < today - 7 * DAY]];
    groups.forEach(([g, f]) => {
      const items = convs.filter(f); if (!items.length) return;
      box.append(el("div", "nav-group", g));
      const ul = el("ul", "nav");
      items.forEach((c) => {
        const b = el("button"); b.type = "button"; b.append(icon(c.tool ? "tool" : "chat"), el("span", "label", c.title));
        if (c.id === convId) b.setAttribute("aria-current", "page");
        b.addEventListener("click", () => openConv(c.id));
        const del = el("button", "del"); del.type = "button"; del.setAttribute("aria-label", `대화 삭제: ${c.title}`); del.title = "이 대화 삭제 (이 브라우저에서만)";
        del.append(icon("x"));
        del.addEventListener("click", (e) => {
          e.stopPropagation();
          if (pending.has(c.id)) return toast("답변을 기다리는 대화는 지울 수 없습니다");
          convs = convs.filter((x) => x.id !== c.id); save(convs);
          if (convId === c.id) newConv(); else renderConvList();
          toast("대화를 지웠습니다");
        });
        const li = el("li"); li.append(b, del); ul.append(li);
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
    const p = pending.get(id); if (p) t.append(p.node);   // 답을 기다리는 중인 대화로 돌아오면 진행 표시를 다시 붙인다
    renderConvList(); setBusy(busy);   // 입력칸 안내 문구가 대화 종류(질문 · 개정 요청)를 따른다
    const last = c.msgs[c.msgs.length - 1];
    const first = last?.res && leadEntry(last.res);
    if (first) loadArticle(first.chunk);
    t.scrollTop = t.scrollHeight;
  }
  // amend: 개정 업무 화면의 「개정 요청 쓰기」로 연 대화. 이 대화의 메시지는 말투와 상관없이 개정 도구로 보낸다 (conv.mode = "amend")
  let AMEND_NEW = false;
  const amendConv = () => isEditor() && (conv() ? conv().mode === "amend" : AMEND_NEW);
  function newConv(amend = false) {
    convId = null; AMEND_NEW = amend; $("#chat-title").textContent = amend ? "새 개정 요청" : "새 대화";
    renderEmpty(); renderConvList(); show("chat"); setBusy(busy); $("#ask-q").focus();
  }

  // ── 채팅 ─────────────────────────────────────────────
  // 자주 묻는 질문 (Figma 화면 2). 개정 담당자에게는 개정 요청 예시 하나 — 대화창에서 개정 도구를 부르는 시연
  const FAQ = [["인사·복무", "육아휴직은 몇 번까지 나눠 쓸 수 있나요?"], ["인사·복무", "반반차(2시간)도 쓸 수 있나요?"],
               ["복리·휴가", "결혼하면 특별휴가 며칠 나와요?"], ["인사·복무", "연차휴가는 며칠 받을 수 있나요?"]];
  const FAQ_EDITOR = ["개정 요청", "인사규정 제30조 육아휴직 분할 사용 횟수를 3회로 늘려줘"];
  // 개정 요청 대화의 첫 화면: 무엇을 적으면 되는지 · 적으면 무슨 일이 일어나는지 · 예시 (예시는 입력칸에 채우기만, 보내기는 담당자가)
  const AMEND_EX = [["조 번호를 알 때", "인사규정 제30조 육아휴직 분할 사용 횟수를 3회로 늘려줘"],
                    ["조를 지울 때", "인사규정 제37조 당연면직 조항을 삭제해줘"],
                    ["조 번호를 모를 때", "육아휴직을 나눠 쓸 수 있는 횟수를 3회로 늘려줘"]];
  function renderAmendEmpty() {
    const t = clear($("#thread")), box = el("div", "empty-chat amend-start");
    const hero = el("span", "amend-hero"); hero.append(icon("tool"));
    box.append(hero, el("h2", null, "어떤 조문을 어떻게 바꿀까요?"),
      el("span", null, "규정 이름 · 조 번호 · 바꿀 내용을 한 문장으로 적어 주세요. 이 대화의 요청은 모두 개정 도구로 갑니다."));
    const ol = el("ol", "amend-steps");
    [["고칠 조문 찾기", "적은 조문을 엽니다. 조 번호가 없으면 후보를 보여 드리고, 고르는 건 담당자입니다."],
     ["AI 검토", "개정문 · 신구대비표를 쓰고, 깨지는 인용을 연쇄 수정하고, 영향 카드를 만듭니다."],
     ["개정안 검토", "카드마다 수락 · 기각한 뒤 승인권자에게 제출합니다."]].forEach(([b, p], i) => {
      const li = el("li"); li.append(el("span", "sn", String(i + 1)), el("b", null, b), el("p", null, p)); ol.append(li);
    });
    const ex = el("div", "faq"), grid = el("div", "faq-grid three");
    ex.append(el("span", "label", "요청 예시 (누르면 입력칸에 채워집니다)"));
    AMEND_EX.forEach(([cat, q]) => {
      const b = el("button"); b.type = "button";
      b.append(el("span", "tag warn", cat), icon("edit"), el("span", null, q));
      b.addEventListener("click", () => { const ta = $("#ask-q"); ta.value = q; autosize(); ta.focus(); ta.setSelectionRange(q.length, q.length); });
      grid.append(b);
    });
    ex.append(grid); box.append(ol, ex); t.append(box);
  }
  function renderEmpty() {
    if (amendConv()) return renderAmendEmpty();
    const t = clear($("#thread")), box = el("div", "empty-chat");
    const hero = el("img", "hero"); hero.src = "img/kiwi-256.png"; hero.alt = ""; hero.width = hero.height = 112;
    box.append(hero, el("h2", null, "무엇이 궁금하신가요?"),
      el("span", null, isEditor() ? "내부규정 조문을 근거로 답하고, 개정 요청은 개정 도구로 초안을 만들어 드립니다." : "내부규정 조문을 근거로 답해 드립니다."));
    const faq = el("div", "faq"), grid = el("div", "faq-grid");
    faq.append(el("span", "label", "자주 묻는 질문"));
    (isEditor() ? [...FAQ.slice(0, 3), FAQ_EDITOR] : FAQ).forEach(([cat, q]) => {
      const b = el("button"); b.type = "button";
      b.append(el("span", "tag " + (cat === "개정 요청" ? "warn" : "accent"), cat), icon("arrow"), el("span", null, q));
      b.addEventListener("click", () => send(q)); grid.append(b);
    });
    faq.append(grid); box.append(faq); t.append(box);
  }
  const isOpenable = (chunk) => chunk && /^\d+_/.test(chunk);
  // 근거 패널에 먼저 열 조문: 답 본문에서 처음 인용한 번호(보통 결론 문장의 근거) → 인용 목록 → 아무 근거
  function leadEntry(res) {
    const es = (res.entries || []).filter((e) => isOpenable(e.chunk));
    for (const m of (res.answer || "").matchAll(/\[(\d+)\]/g)) { const e = es.find((x) => x.n === +m[1]); if (e) return e; }
    return es.find((e) => (res.cited || []).includes(e.n)) || es[0];
  }

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
    // 세부 불릿은 기본 접는다 (261019 사용자 요청): 결론만 보이고 「세부 N개 보기」로 펼친다. 「세부」 머리(h4)가 있으면 함께 접는다
    const lists = [...body.querySelectorAll("ul")], n = lists.reduce((s, u) => s + u.children.length, 0);
    if (n) {
      const fold = el("div", "fold"); fold.hidden = true;
      lists.forEach((u) => { const h = u.previousElementSibling; if (h && h.tagName === "H4") fold.append(h); fold.append(u); });
      const tg = el("button", "link fold-toggle"); tg.type = "button"; tg.setAttribute("aria-expanded", "false");
      const paint = () => { tg.textContent = fold.hidden ? `세부 ${n}개 보기 ▾` : "세부 접기 ▴"; tg.setAttribute("aria-expanded", String(!fold.hidden)); };
      tg.addEventListener("click", () => { fold.hidden = !fold.hidden; paint(); }); paint();
      body.append(tg, fold);
    }
    return body;
  }
  // 근거 카드 · [n]을 한 번 누르면 바로 오른쪽 패널에 연다: 접혀 있으면 펼치고, 열린 카드를 표시하고, 패널을 잠깐 반짝여 바뀐 것을 보인다
  // (같은 조의 다른 항이면 형광만 옮겨 가서 「안 바뀐 것처럼」 보였음, 261018 사용자 지적)
  async function openEntry(e) {
    if (!e) return;
    if (!isOpenable(e.chunk)) return toast(`${e.reg || "외부 법령"} ${e.article || ""} — 법령 원문은 내부규정 패널에 열 수 없습니다`);
    openPanel(); selectTab("src");
    document.querySelectorAll(".cite[data-chunk]").forEach((b) => b.classList.toggle("open", b.dataset.chunk === e.chunk));
    if (await loadArticle(e.chunk, e)) {
      const P = $("#panel-src"); P.classList.remove("flash"); void P.offsetWidth; P.classList.add("flash");
      const r = $("#src-panel").getBoundingClientRect();
      if (r.top > innerHeight || r.bottom < 0) $("#src-panel").scrollIntoView({ behavior: "smooth", block: "start" });   // 좁은 화면: 패널이 아래에 있음
    }
  }
  // 답변 한 덩어리: 표지 · 이름 · 상태 줄 + 카드 (Figma 화면 4)
  function aiShell(wrap, stateLabel, tone, ic) {
    const row = el("div", "ai"), av = el("div", "ai-av"), img = el("img"); img.src = "img/kiwi-96.png"; img.alt = ""; av.append(img);
    const c = el("div", "ai-c"), head = el("div", "a-head"); head.append(el("b", null, "규정 도우미"));
    if (stateLabel) { const s = el("span", "state " + (tone || "")); s.append(icon(ic), stateLabel); head.append(s); }
    c.append(head); row.append(av, c); wrap.append(row); return c;
  }
  // 생성 중 단계 (Figma 화면 3). 서버가 단계 신호를 보내지 않으므로 경과 시간으로 넘기는 표시용 추정이다
  const STEPS = {
    ask: [["질문 분석", 0], ["문서 검색", 1.5, "내부규정에서 관련 조문을 찾고 있습니다"], ["답변 생성", 6, "찾은 조문을 근거로 답을 쓰고 있습니다"]],
    amend: [["요청 분석", 0], ["고칠 조문 찾기", 1, "규정 이름 · 조 번호 · 바꿀 내용으로 위치를 찾고 있습니다"]],
    draft: [["개정문 초안 작성", 0, "모델이 조문을 다시 쓰고 개정문으로 바꿉니다"], ["원문 적용 확인", 10, "개정문이 원문에 그대로 적용되는지 확인합니다"],
            ["인용 · 영향 점검", 18, "이 조를 인용하는 곳과 관련 법령을 대조합니다"]],
  };
  function stepsCard(kind, title) {
    const card = el("div", "steps-card"), b = el("b"); b.append(icon("sparkle"), title);
    const meter = el("div", "meter"), bar = el("i"); meter.append(bar);
    const ol = el("ol"), items = STEPS[kind].map(([t, , sub], i) => {
      const li = el("li"), s = el("span", null, `${"①②③④"[i]} ${t} 중`); if (sub) s.append(el("small", null, sub)); li.append(s); ol.append(li); return li;
    });
    card.append(b, meter, ol);
    const t0 = Date.now(), tick = () => {
      const sec = (Date.now() - t0) / 1000; let cur = 0;
      STEPS[kind].forEach(([, at], i) => { if (sec >= at) cur = i; });
      items.forEach((li, i) => { li.className = i < cur ? "done" : i === cur ? "now" : ""; });
      bar.style.width = Math.min(92, ((cur + 0.6) / items.length) * 100) + "%";
    };
    tick(); const timer = setInterval(tick, 500);
    card.stop = () => clearInterval(timer);
    return card;
  }
  let REGS = 0;
  function renderMsg(q, res, kind = "ask") {
    const wrap = el("div", "msg");
    const qq = el("div", "q"); qq.append(el("span", null, q)); wrap.append(qq);
    if (!res) {
      const card = stepsCard(kind, kind === "amend" ? "개정 요청을 확인하고 있습니다" : "답변을 준비하고 있습니다");
      aiShell(wrap).append(card); wrap.stop = card.stop; return wrap;
    }
    if (res.tool === "amend") return renderTool(wrap, q, res);
    if (res.error) { aiShell(wrap, "답하지 못함", "warn", "info").append(el("div", "warnline", `답하지 못했습니다 — ${res.error}`)); return wrap; }
    const ST = { answer: ["답변 완료", "pos", "check-c"], partial: ["관련 조문만", "warn", "info"], none: ["근거 없음", "", "info"] }[res.mode] || ["답변 완료", "pos", "check-c"];
    const a = el("div", "a" + (res.mode === "none" ? " muted" : ""));
    const shell = aiShell(wrap, ST[0], ST[1], ST[2]);
    if (res.standalone) {   // 멀티턴: 이어 묻는 질문을 독립 질문으로 바꿔 검색했음 (Figma 화면 4 「이전 질문에 이어서 답변」)
      const f = el("span", "follow-of"); f.title = `검색한 질문: ${res.standalone}`;
      f.append(icon("arrow"), "이전 질문 ", el("b", null, `「${res.followup_of}」`), "에 이어서 답변");
      shell.append(f);
    }
    shell.append(a);
    if (res.mode === "none") {   // 근거 없음 (Figma 화면 5): 추측하지 않고 보류했다는 것을 보인다
      const lead = el("div", "lead"); lead.append(icon("info"), renderBody(res.answer, res.entries)); a.append(lead);
      const nb = el("div", "none-box"), tx = el("div");
      tx.append(el("b", null, "관련 조문을 찾지 못했습니다"), `내부규정${REGS ? " " + REGS + "건" : ""}을 검색했지만 질문과 맞는 조문이 없어, 추측하지 않고 답변을 보류했습니다.`);
      nb.append(icon("search-x"), tx); a.append(nb);
    } else a.append(renderBody(res.answer, res.entries));
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
    const src = el("div", "sources"); src.append(el("span", "label", used.length ? `근거 조문 ${used.length}건` : `근거 후보 ${shown.length}건`));
    if (res.mode === "none") src.hidden = true;
    shown.forEach((e) => {
      const c = el("button", "cite" + (isOpenable(e.chunk) ? "" : " off")); c.type = "button"; if (e.chunk) c.dataset.chunk = e.chunk;
      const ROLE = { "검색": "검색 결과", "형제항": "같은 조의 다른 항", "인용": "인용된 조문", "정확조회": "직접 찾은 조문", "관련 조문": "관련 조문" };
      const t = el("span"); t.append(`${e.reg || ""} ${e.article || ""}`, el("small", null, ROLE[e.role] || e.role || ""));
      const tags = el("span", "tags"); tags.append(el("span", "tag " + (e.state === "현행" ? "pos" : ""), e.state || ""));
      if (e.open?.length) tags.append(el("span", "tag warn", "개정 검토 중"));
      c.append(el("span", "num", String(e.n)), t, tags, el("small", null, isOpenable(e.chunk) ? "원문 →" : "법령"));
      c.addEventListener("click", () => openEntry(e)); src.append(c);
    });
    a.append(src);
    (res.warnings || []).slice(0, 3).forEach((w) => a.append(el("div", "warnline", "⚠ " + w)));
    const foot = el("div", "a-foot"); foot.append("참고용 답변");   // 걸린 시간 · 모델 이름은 화면에 보이지 않는다 (res.sec · res.model은 의견 기록에만)
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
  // ── 개정 도구: 대화창의 개정 요청 → 고칠 조문 찾기(GET /api/find) → 개정안 작성(POST /api/draft) ──
  // 질문은 RAG(가빈 파이프라인)가, 개정 요청은 우리 에이전트가 맡는다. 고르는 기준은 규칙(개정 담당자 + 「바꿔 · 고쳐 · 신설 …」)이고,
  // 잘못 고르면 「질문으로 답 받기」로 되돌린다. 상위 모델이면 모델이 도구를 직접 고르게 할 수 있다 (발표 이야기, 리포트 57)
  const AMEND_RE = /(개정해|개정하|바꿔|바꾸어|바꾸고|고쳐|고치고|수정해|신설|삭제해|없애|추가해|넣어|늘려|줄여|올려|낮춰|변경해)/;
  const wantsAmend = (q) => isEditor() && AMEND_RE.test(q);
  function targetBtn(sel, x) {
    const b = el("button", "cite"); b.type = "button";
    const t = el("span"); t.append(`${x.reg} ${x.label}${x.title ? `(${x.title})` : ""}`, el("small", null, x.how));
    const n = el("span", "num"); n.append(icon("doc"));
    b.append(n, t, el("small", null, "원문 →"));
    b.addEventListener("click", () => {
      loadArticle(x.chunk); selectTab("src"); openPanel();
      if (sel.pickable) { b.parentElement.parentElement.querySelectorAll(".cite").forEach((o) => o.setAttribute("aria-pressed", "false")); b.setAttribute("aria-pressed", "true"); sel.chunk = x.chunk; sel.onPick?.(); }
    });
    return b;
  }
  function draftRow(q, res, sel, wrap) {
    const row = el("div", "tool-row"), go = el("button", "btn primary small"); go.type = "button";
    go.append(icon("edit"), "이 조문으로 개정안 작성");
    const note = el("small", "hint", sel.chunk ? "" : "후보 하나를 고르면 작성할 수 있습니다");
    go.disabled = !sel.chunk; sel.onPick = () => { go.disabled = false; note.textContent = ""; };
    go.addEventListener("click", async () => {
      if (!LIVE) return toast("서버에 연결되지 않았습니다");
      go.disabled = true;
      const card = stepsCard("draft", "개정안을 만들고 있습니다"); row.after(card);
      try {
        const r = await api("/api/draft", { chunk: sel.chunk, request: q });
        res.draft = { id: r.id, breaks: r.breaks, companion: r.companion, impacts: r.impacts, chunk: sel.chunk }; save(convs);
        wrap.replaceWith(renderMsg(q, res)); await refreshChangesets(); loadArticle(sel.chunk);
      } catch (e) { toast(e.message, 6000); go.disabled = false; }
      finally { card.stop(); card.remove(); }
    });
    row.append(go, note); return row;
  }
  function draftDone(d) {
    const box = el("div", "tool-done"), ul = el("ul");
    box.append(el("b", null, `개정안 ${d.id} 초안을 만들었습니다`));
    ul.append(el("li", null, `깨지는 인용 ${d.breaks?.[0] ?? 0} → ${d.breaks?.[1] ?? 0} (다른 규정의 인용까지 연쇄 수정)`),
              el("li", null, `함께 개정할 규정 ${d.companion ?? 0}개 · 확인할 영향 ${d.impacts ?? 0}건`));
    const go = el("button", "btn secondary small staff-only"); go.type = "button"; go.dataset.view = "review"; go.dataset.cs = d.id;
    go.append("개정안 검토 열기", icon("arrow"));
    box.append(ul, go); return box;
  }
  function renderTool(wrap, q, res) {
    const done = !!res.draft, f = res.find || {};
    const a = el("div", "a");
    aiShell(wrap, done ? "개정안 작성됨" : "개정 도구", done ? "pos" : "tool", done ? "check-c" : "tool").append(a);
    const sel = { chunk: f.chunk || res.draft?.chunk || null, pickable: !f.chunk && !done };
    if (res.error || f.error) a.append(el("div", "warnline", res.error || f.error));
    else if (f.chunk) {
      a.append(el("p", "hint", f.guessed ? "개정 요청으로 이해했습니다. 규정 이름이 정확하지 않아 가장 비슷한 규정으로 찾았습니다." : "개정 요청으로 이해했습니다. 이 조문을 고칩니다."));
      const t = el("div", "tool-target"); t.append(targetBtn(sel, { reg: f.reg, label: f.label, how: f.guessed ? "이름으로 추정" : "직접 찾은 조문", chunk: f.chunk })); a.append(t);
    } else if (f.candidates?.length) {
      a.append(el("p", "hint", f.confident ? "개정 요청으로 이해했습니다. 고칠 조문 후보입니다. 맞는 조문을 고르세요." : "고칠 조문이 확실하지 않습니다. 후보에서 고르거나, 규정 이름과 조 번호를 적어 다시 요청하세요."));
      const t = el("div", "tool-target");
      f.candidates.slice(0, 5).forEach((x) => {
        const r = el("div", "tool-row");
        const b = targetBtn(sel, { reg: x.reg_name, label: x.label, title: x.title, how: "모델 추정", chunk: x.chunk });
        if (x.chunk === sel.chunk) b.setAttribute("aria-pressed", "true");
        r.append(b); t.append(r);
      });
      a.append(t);
    } else a.append(el("div", "warnline", "고칠 조문을 찾지 못했습니다. 규정 이름과 조 번호를 함께 적어 주세요."));
    if (done) a.append(draftDone(res.draft));
    else if (!res.error && !f.error && (f.chunk || f.candidates?.length)) a.append(draftRow(q, res, sel, wrap));
    const foot = el("div", "a-foot"), alt = el("button", "link-btn", "질문이었나요? 질문으로 답 받기"); alt.type = "button";
    alt.addEventListener("click", () => send(q, true));
    foot.append("개정문은 참고 문안입니다. 담당자가 카드마다 수락 · 기각합니다.", alt); a.append(foot);
    return wrap;
  }

  // 서버는 질문을 하나씩 처리한다 (GPU 하나, web_chat의 lock) → 화면도 한 번에 하나만 보낸다.
  // 답을 기다리는 동안 다른 대화를 열거나 새 대화를 시작해도, 답은 보낸 대화(cid)에 붙는다.
  const pending = new Map();   // 대화 id → { node }
  let busy = null;             // 지금 보낸 요청의 AbortController
  function setBusy(ctrl) {
    busy = ctrl;
    const s = $("#composer .send"), ta = $("#ask-q");
    s.classList.toggle("stop", !!ctrl); s.setAttribute("aria-label", ctrl ? "멈추기" : "보내기"); s.replaceChildren(icon(ctrl ? "stop" : "up-arrow"));
    ta.placeholder = ctrl ? "답변을 만들고 있습니다… (■ 버튼으로 멈추기)"
      : amendConv() ? "개정 요청: ○○규정 제△조를 어떻게 바꿀지 적어 주세요" : "내부규정에 대해 궁금한 점을 질문해 보세요";
  }
  const autosize = () => { const ta = $("#ask-q"); ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, 200) + "px"; };
  $("#ask-q").addEventListener("input", autosize);
  async function send(text, forceAsk) {
    if (busy) return toast("앞 질문의 답을 만드는 중입니다. 끝나거나 멈춘 뒤 보내 주세요");
    const q = (text ?? $("#ask-q").value).trim();
    if (!q) return;
    if (!LIVE) return toast("서버에 연결되지 않아 질문할 수 없습니다");
    const tool = !forceAsk && (amendConv() || wantsAmend(q));
    if (text == null) { $("#ask-q").value = ""; autosize(); }
    if (!conv()) {
      convId = "c" + Date.now(); convs.unshift({ id: convId, title: q.slice(0, 40), at: Date.now(), msgs: [], tool, mode: amendConv() ? "amend" : "ask" });
      AMEND_NEW = false; $("#chat-title").textContent = q.slice(0, 40);
    }
    const cid = convId, c = conv();
    const role0 = ROLE_NOW, list0 = convs;   // 답을 기다리는 동안 역할을 바꿔도 이 대화는 원래 역할의 기록에 저장한다
    // 멀티턴: 이 대화의 앞 질문(답이 난 것) 3개를 함께 보낸다 → 서버가 이어 묻는 질문을 독립 질문으로 바꿔 검색 (web_chat.standalone)
    const history = c.msgs.filter((m) => m.res && !m.res.tool && !m.res.error && m.res.answer).slice(-3)
      .map((m) => ({ q: m.res.standalone || m.q, a: (m.res.answer || "").slice(0, 400) }));
    const t = $("#thread"); if (t.querySelector(".empty-chat")) clear(t);
    const node = renderMsg(q, null, tool ? "amend" : "ask"); t.append(node); t.scrollTop = t.scrollHeight;
    const ctrl = new AbortController(); pending.set(cid, { node }); setBusy(ctrl);
    let res;
    try { res = tool ? { tool: "amend", find: await api(`/api/find?q=${encodeURIComponent(q)}`, null, ctrl.signal) } : await api("/api/ask", { q, history }, ctrl.signal); }
    catch (e) {
      const msg = e.name === "AbortError" ? "멈췄습니다. 다시 물어보려면 질문을 보내 주세요" : e.message;
      res = tool ? { tool: "amend", error: msg } : { error: msg, stopped: e.name === "AbortError" };
    }
    node.stop?.(); pending.delete(cid);
    const done = renderMsg(q, res);
    if (node.isConnected) node.replaceWith(done);
    c.msgs.push({ q, res }); c.at = Date.now(); saveFor(role0, list0); renderConvList();
    setBusy(null);
    if (ROLE_NOW !== role0) { toast(`${ROLE_LABEL_UI[role0]}의 대화 「${c.title}」에 답이 도착했습니다`); return; }
    if (convId !== cid) { toast(`「${c.title}」 대화에 답이 도착했습니다`); return; }
    const lead = tool ? null : leadEntry(res);
    const first = tool ? (res.find?.chunk || res.find?.candidates?.[0]?.chunk) : lead?.chunk;
    if (first) { loadArticle(first, lead); selectTab("src"); openPanel(); done.querySelectorAll(".cite[data-chunk]").forEach((b) => b.classList.toggle("open", b.dataset.chunk === first)); }
    t.scrollTop = t.scrollHeight; $("#ask-q").focus();
  }
  $("#composer").addEventListener("submit", (e) => { e.preventDefault(); if (busy) busy.abort(); else send(); });
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
    if (art.paras.length > 1 && art.highlight != null && art.highlight >= 0) {
      const n = el("p", "hl-note"); n.append(icon("info"), "노란 표시 부분이 답변 · 요청이 가리킨 항입니다."); P.append(n);
    }
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
    lawSect(A, "이 조가 인용하는 법령", m.laws.map((x) => ({ head: x.where, text: x.quote, interp: x.interp })), "인용하는 법령 없음");
    lawSect(A, "관련 법령 조문", m.sameTitleLaws.map((x) => ({ head: `${x.law} ${x.article}`, tag: x.why, text: x.text, interp: x.interp })), "해당 없음");
    const row = el("div", "actions-row");
    const form = el("form", "draft-form");
    const ta = el("textarea"); ta.placeholder = `이 조를 어떻게 바꿀지 적어 주세요 — 예: 분할 횟수를 3회로 늘린다`; ta.maxLength = 300; ta.setAttribute("aria-label", "개정 요청");
    const btns = el("div", "row-btns"); const go = el("button", "btn primary"); go.type = "submit"; go.append(icon("edit"), "개정안 작성");
    btns.append(go); form.append(ta, btns);
    form.addEventListener("submit", (e) => { e.preventDefault(); draft(ta.value, form, go); });
    row.append(form, el("p", "hint", "모델이 개정문 초안을 쓰고, 적용기가 원문과 맞는지 확인한 뒤 연쇄 수정·영향 점검까지 합니다. 결과는 참고 문안이며 자동 반영되지 않습니다."));
    A.append(row);
    const n = m.inbound.length + m.findings.length + m.open.length;
    $("#amend-count").textContent = n ? String(n) : "";
  }
  // 법제처 해석례 목록 — 판정은 「해석한 조문이 지금도 그대로인가」 (해석이 옳은지가 아님)
  function interpList(list) {
    const box = el("div", "interp");
    box.append(el("span", "label", `법제처 해석례 ${list.length}건`));
    list.forEach((i) => {
      const row = el("div", "interp-row");
      const tone = i.status === "현행과 같음" ? "pos" : i.status === "조 번호 없음" ? "" : i.size && i.size.includes("자구") ? "" : "warn";
      const lab = i.status === "현행과 같음" ? "해석 뒤 조문 그대로" : i.status === "조 번호 없음" ? "조문 특정 안 됨" :
        `해석 뒤 조문 바뀜${i.size ? " · " + i.size.replace(/\(|\)/g, " ").trim() : ""}`;
      const top = el("div", "interp-top"); top.append(el("span", "tag " + tone, lab), el("b", null, i.case_no), el("small", null, i.date || ""));
      const t = i.url ? el("a", "interp-title", i.title) : el("span", "interp-title", i.title);
      if (i.url) { t.href = i.url; t.target = "_blank"; t.rel = "noopener noreferrer"; }
      row.append(top, t);
      if (i.answer) row.append(el("p", "interp-answer", i.answer));
      box.append(row);
    });
    return box;
  }
  function lawSect(A, title, items, emptyText) {
    const s = el("section", "sect"), h = el("h3");
    h.append(title, el("span", "num" + (items.length ? " on" : ""), String(items.length).padStart(2, "0"))); s.append(h);
    if (!items.length) s.append(el("div", "none", emptyText));
    items.forEach((x) => {
      const r = el("div", "item"), b = el("b", null, x.head);
      if (x.tag) b.append(" ", el("span", "tag " + (x.tag.includes("가까운") ? "accent" : ""), x.tag));
      r.append(b); if (x.text) r.append(el("q", null, x.text));
      if (x.interp?.length) r.append(interpList(x.interp));
      s.append(r);
    });
    A.append(s);
  }
  // 개정안 검토 화면의 「법령 대조」 카드 아래 상세: 관련 법조문 · 숫자 대조 · 해석례
  function lawDetail(card, note) {
    const box = el("div", "lawcheck");
    card.related.forEach((r) => {
      const d = el("div", "law-art"), h = el("b", null, `${card.law} ${r.article}(${r.title})`);
      h.append(" ", el("span", "tag " + (r.why.includes("가까운") ? "accent" : ""), r.why));
      d.append(h, el("q", null, r.text.slice(0, 200)));
      if (r.interp?.length) d.append(interpList(r.interp));
      box.append(d);
    });
    if (card.compare.length) {
      const t = el("table", "cmp-num"), hd = el("tr");
      ["단위", "법령 조문 값", "현행 → 개정안", "비교"].forEach((x) => hd.append(el("th", null, x)));
      t.append(hd);
      card.compare.forEach((c) => {
        const tr = el("tr");
        tr.append(el("td", null, c.unit), el("td", null, c.law.length ? c.law.map((v) => v + c.unit).join(", ") : "—"),
                  el("td", null, `${c.old.join(", ")}${c.unit} → ${c.new.join(", ")}${c.unit}`));
        const td = el("td"); td.append(c.note ? el("span", "tag " + (c.note.includes("같아짐") ? "pos" : "warn"), c.note) : el("span", "hint", "문맥이 달라 비교 안 함"));
        tr.append(td); t.append(tr);
      });
      box.append(t);
    }
    box.append(el("p", "hint", note));
    return box;
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
  // 근거 원문 패널 접기 · 펼치기 (Figma 화면 2·3·5는 접힌 세로 막대, 화면 4는 펼침)
  function openPanel() { $("#view-chat").classList.remove("collapsed"); $("#rail").hidden = true; }
  function closePanel() { $("#view-chat").classList.add("collapsed"); $("#rail").hidden = false; }
  $("#panel-close").addEventListener("click", closePanel);
  $("#panel-open").addEventListener("click", openPanel);
  $("#panel-open2").addEventListener("click", openPanel);
  $("#tab-src").addEventListener("click", () => selectTab("src"));
  $("#tab-amend").addEventListener("click", () => selectTab("amend"));

  // ── 개정안 목록·검토 ─────────────────────────────────
  function renderChangesetList(list) {
    const ul = clear($("#cs-list"));
    if (!list.length) ul.append(el("li", "nav-group", "개정안 없음"));
    list.forEach((c) => {
      const b = el("button"); b.type = "button"; b.dataset.view = "review"; b.dataset.cs = c.id;
      const [lab, tone] = CS_LABEL[c.status_label] || [c.status_label || "", ""];
      b.title = `${c.id} · ${c.reg || ""} — ${c.title}`;
      b.append(icon("doc"), el("span", "label", `${c.reg ? c.reg + " " : ""}${c.title.replace(/\s*\([^)]*\)\s*$/, "")}`));
      if (lab) b.append(el("span", "chip " + tone, lab));
      const li = el("li"); li.append(b); ul.append(li);
    });
  }
  async function refreshChangesets() {
    if (!LIVE) return;
    CS_ALL = await api("/api/changesets"); renderChangesetList(CS_ALL);
    if (!$("#view-work").hidden) renderWork();
  }
  // 상태: 초안 → 검토(제출) → 승인(최종안 저장) → 공포.  검토 중 반려 → 기각 (sql/amend_schema.sql CHECK)
  const CS_LABEL = { 초안: ["초안", ""], 검토: ["검토 요청됨", "accent"], 승인: ["승인", "pos"], 공포: ["공포", "pos"], 기각: ["반려", "neg"] };
  const editable = (C) => (C.status === "초안" || C.status === "검토") && isEditor();   // 카드 수락·기각은 담당자만. 승인권자는 보고 승인·반려
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
  // 개정 절차 안내: 이 개정안이 거칠 내부 절차 (GET /api/procedure — 규정 원문에서 읽음). 판정이 아니라 안내
  async function fillProcedure(C, doc) {
    if (!LIVE) return;
    let P;
    try { P = await api(`/api/procedure?id=${encodeURIComponent(C.id)}`); } catch (e) { return; }
    if (CS !== C) return;
    const s = el("section", "procedure");
    s.append(el("h2", null, "개정 절차 안내"), el("p", "hint", `「${P.reg}」 개정이 거칠 절차입니다. 근거는 이 회사 규정 원문이며, 해당 여부는 담당 부서가 확인합니다.`));
    const ol = el("ol", "steps");
    P.steps.forEach((x) => {
      const li = el("li"), top = el("div", "top");
      top.append(el("span", "tag " + ({ "필수": "", "해당": "accent", "확인 필요": "warn" }[x.status] || ""), x.status), el("b", null, x.step), el("small", null, x.who));
      li.append(top, el("p", null, x.note), el("small", "basis", `근거: ${x.basis}`)); ol.append(li);
    });
    s.append(ol); doc.append(s);
  }
  async function fillLawCards(C) {
    const slots = [...document.querySelectorAll("#side .law-slot")];
    if (!LIVE || !slots.length) return;
    let L;
    try { L = await api(`/api/lawcheck?id=${encodeURIComponent(C.id)}`); } catch (e) { return; }
    if (CS !== C) return;
    slots.forEach((s) => {
      const card = L.cards.find((c) => c.quoted === s.dataset.quote) || L.cards[0];
      if (card) s.append(lawDetail(card, L.note));
    });
  }
  function renderActions(C) {
    const row = clear($(".review-foot .row2"));
    const btn = (cls, label, fn, ic) => { const b = el("button", "btn " + cls); b.type = "button"; if (ic) b.append(icon(ic)); b.append(label); b.addEventListener("click", fn); return b; };
    const link = (label, href) => { const a = el("a", "btn secondary"); a.href = href; a.setAttribute("download", ""); a.append(icon("download"), label); return a; };
    const act = (path, msg) => async () => {
      if (!LIVE) return toast("서버에 연결되지 않았습니다");
      try { const r = await api(path, { id: C.id }); toast(msg(r)); await loadChangeset(C.id); await refreshChangesets(); } catch (e) { toast(e.message, 6000); }
    };
    if (!isEditor() && !isApprover()) return;
    if (C.status === "초안") {
      if (!isEditor()) { const b = btn("primary", "담당자가 제출해야 합니다", () => {}); b.disabled = true; row.append(b); return; }
      if (C.has_package) row.append(link("패키지 내려받기", `/api/package?id=${encodeURIComponent(C.id)}`));
      row.append(btn("primary", "검토자에게 제출", act("/api/submit", (r) => `${C.id} 검토 요청함${r.undecided ? ` — 미결정 ${r.undecided}건 포함` : ""}`)));
    } else if (C.status === "검토") {
      if (!isApprover()) { const b = btn("primary", "승인권자의 승인을 기다립니다", () => {}); b.disabled = true; row.append(b); return; }
      row.append(btn("secondary", "반려", act("/api/reject", () => `${C.id} 반려함`), "x"));
      row.append(btn("primary", "승인 (최종안 확정)", act("/api/approve", (r) => `${C.id} 승인 — 수락 ${r.counts["수락"]}건으로 최종안을 저장했습니다`), "check"));
    } else if (C.status === "승인") {
      row.append(link("최종안 내려받기", `/api/package?id=${encodeURIComponent(C.id)}&final=1`));
      if (isEditor()) row.append(btn("primary", "공포 처리 (등록·공지)", act("/api/publish", (r) => `${C.id} 공포 처리함` + ((r.follow_up && r.follow_up.made.length) ? ` — 함께 개정 초안 ${r.follow_up.made.length}건을 만들었습니다 (${r.follow_up.made.map((m) => m.reg).join(", ")})` : ""))));
    } else {
      if (C.status === "공포") row.append(link("최종안 내려받기", `/api/package?id=${encodeURIComponent(C.id)}&final=1`));
      const b = btn("primary", C.status === "공포" ? "공포됨" : "반려됨", () => {}); b.disabled = true; row.append(b);
    }
  }
  function renderChangeset(C) {
    CS = C;
    const ttl = C.title.replace(/\s*\([^)]*\)\s*$/, "").replace(/^\S+\s+—\s+/, "");   // 대화에서 만든 개정안은 제목이 요청 문장(규정 이름 포함)
    $("#cs-id").textContent = `개정안 ${C.id}`; $("#cs-title").textContent = ttl.startsWith(C.reg) ? ttl : `${C.reg} — ${ttl}`;
    const [lab, tone] = CS_LABEL[C.status] || [C.status, ""];
    $("#cs-status").textContent = lab; $("#cs-status").className = "chip " + tone;
    // 진행 단계 (캔버스 화면 9의 단계 표시줄을 우리 상태에 맞춤): 초안 작성 → 검토 요청 → 승인 → 공포
    const flow = clear($("#cs-flow")), ORDER = ["초안", "검토", "승인", "공포"], NAMES = ["초안 작성 · 카드 결정", "검토 요청", "승인 (최종안)", "공포"];
    const at = C.status === "기각" ? 1 : ORDER.indexOf(C.status);
    NAMES.forEach((nm, i) => {
      if (i) flow.append(el("span", "bar" + (i <= at ? " done" : "")));
      const stop = C.status === "기각" && i === 1, last = C.status === "공포" && i === 3;
      const s = el("span", "stp " + (stop ? "stop" : i < at || last ? "done" : i === at ? "cur" : ""));
      const sn = el("span", "sn", stop ? "!" : String(i + 1)); if ((i < at || last) && !stop) { sn.textContent = ""; sn.append(icon("check")); }
      s.append(sn, stop ? "반려됨" : nm); if (i === at) s.setAttribute("aria-current", "step"); flow.append(s);
    });
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
    // 깨지는 인용(연쇄 수정 전 → 후)은 오른쪽 요약 맨 위에 한 줄로 보인다
    if (C.coverage?.not_covered?.length) {   // 구조상 확인하지 못하는 유형 (판단은 사람이 이 범위를 알고 한다). 찾은 경로별 개수는 개발용이라 뺌 (261019)
      const s = el("section", "coverage"); s.append(el("h2", null, "AI가 확인하지 못하는 것"));
      const ul = el("ul", "hint"); C.coverage.not_covered.forEach((t) => ul.append(el("li", null, t)));
      s.append(ul); doc.append(s);
    }
    fillProcedure(C, doc);

    const SD = clear($("#side"));
    // 요약: 담당자의 일(카드 결정)을 맨 위에 → 깨지는 인용 한 줄 → 종류별 거르기 (0인 종류는 뺌, 이름에 마우스를 올리면 뜻)
    const head = el("div", "decide-head");
    head.append(el("b", "decide-left"), el("span", "hint", editable(C) ? "카드마다 수락 · 기각하세요. 수락한 카드만 최종안에 반영됩니다." : "수락한 카드만 최종안에 반영됩니다."));
    const [b0, b1] = C.breaks || [null, null];
    const br = el("p", "break-line " + (b1 ? "neg" : "pos"));
    br.append(icon(b1 ? "info" : "check-c"), b1 ? `깨지는 인용 ${b1}곳이 남아 있습니다 — 깨짐 카드를 확인하세요`
      : b0 ? `깨지는 인용 ${b0}곳 → 연쇄 수정으로 0곳` : "이번 개정으로 깨지는 인용이 없습니다");
    head.append(br);
    const KINDS = [["break", "깨짐", "이번 개정으로 조 번호 · 문구가 맞지 않게 되는 인용"], ["resolved", "충돌 해소", "개정 뒤 다른 규정과 내용이 같아지는 곳"],
                   ["law", "법령 대조", "개정한 조가 인용하는 법령 — 법령과 맞는지 비교할 곳"], ["review", "확인 필요", "같은 제목 · 비슷한 뜻으로 찾은 관련 조문 — 함께 고칠지 판단할 곳"]];
    const kindOf = (x) => (x.kind === "topic" ? "review" : x.kind);
    const fl = el("div", "kind-filter"); fl.setAttribute("role", "group"); fl.setAttribute("aria-label", "카드 종류로 거르기");
    const filt = (k) => {
      fl.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.k === k)));
      SD.querySelectorAll(".rows > li").forEach((li) => (li.hidden = k !== "all" && li.dataset.kind !== k));
    };
    [["all", "전체", "모든 카드"], ...KINDS].forEach(([k, name, desc]) => {
      const n = k === "all" ? C.impacts.length : C.impacts.filter((x) => kindOf(x) === k).length;
      if (!n) return;
      const b = el("button", "kf " + k); b.type = "button"; b.dataset.k = k; b.title = desc; b.setAttribute("aria-pressed", String(k === "all"));
      b.append(name, el("b", null, String(n))); b.addEventListener("click", () => filt(k)); fl.append(b);
    });
    head.append(fl);
    SD.append(head);
    const TONE = { break: "neg", resolved: "pos", law: "accent", review: "warn", topic: "" };
    const ol = el("ol", "rows");
    C.impacts.forEach((x) => {
      const li = el("li", "row"), body = el("div", "body"); li.dataset.kind = kindOf(x);
      const top = el("div", "top"); top.append(el("span", "tag " + (TONE[x.kind] || ""), x.label), el("span", "where", x.where));
      // 근거 종류: 문서 명시(원문 인용) / 낱말 일치(같은 제목·용어) / 모델 추정(의미 유사·오타) — 모델 추정은 담당자 판단이 더 필요
      if (x.basis) { const b = el("span", "basis" + (x.basis === "모델 추정" ? " model" : ""), x.basis); b.title = x.path ? `발견 경로: ${x.path}` : ""; top.append(b); }
      body.append(top);
      if (x.quote) body.append(el("q", null, x.quote));
      body.append(el("div", "why", x.reason));
      if (LIVE && x.key) {   // 설명 보기: 로컬 모델이 두 조문을 근거로 2~3문장으로 풀어 줌 (요약·해석, 판정 아님)
        const ex = el("div", "explain"), eb = el("button", "link", "왜 확인해야 하나? (모델 요약)"); eb.type = "button";
        eb.addEventListener("click", async () => {
          eb.disabled = true; eb.textContent = "풀어 쓰는 중…";
          try { const r = await api(`/api/explain?id=${encodeURIComponent(C.id)}&key=${encodeURIComponent(x.key)}`);
            ex.replaceChildren(el("p", null, r.text || r.note), ...(r.text ? [el("small", "basis model", r.note)] : [])); }
          catch (e) { eb.disabled = false; eb.textContent = "왜 확인해야 하나? (모델 요약)"; toast(e.message); }
        });
        ex.append(eb); body.append(ex);
      }
      if (x.kind === "law") { const slot = el("div", "law-slot"); slot.dataset.quote = x.quote || ""; body.append(slot); }
      const dc = el("div", "decide");
      const acc = el("button", "acc"), rej = el("button", "rej"); acc.type = rej.type = "button";
      acc.append(icon("check"), "수락"); rej.append(icon("x"), "기각");
      const paint = () => {
        acc.setAttribute("aria-pressed", String(x.state === "accept")); rej.setAttribute("aria-pressed", String(x.state === "reject"));
        li.classList.toggle("rejected", x.state === "reject"); tally();
      };
      if (!editable(C)) { acc.disabled = rej.disabled = true; acc.title = rej.title = isEditor() ? "승인·공포·반려된 개정안은 결정을 바꿀 수 없습니다" : "카드 수락·기각은 개정 담당자가 합니다"; }
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
    fillLawCards(C);
    renderActions(C);
    renderFinal(C, doc);
    tally();
  }
  function tally() {
    if (!CS) return;
    const N = CS.impacts.length, na = CS.impacts.filter((x) => x.state === "accept").length, nr = CS.impacts.filter((x) => x.state === "reject").length;
    $("#tally").textContent = `수락 ${na} / 기각 ${nr} / 남음 ${N - na - nr}${LIVE ? " · 결정은 바로 저장됩니다" : " · 예시 (저장 안 됨)"}`;
    const left = $("#side .decide-left");
    if (left) left.textContent = !N ? "결정할 카드가 없습니다" : N - na - nr ? `결정할 카드 ${N}건 중 ${N - na - nr}건 남음` : `카드 ${N}건을 모두 결정했습니다`;
    $("#p-acc").style.width = N ? (na / N) * 100 + "%" : "0"; $("#p-rej").style.width = N ? (nr / N) * 100 + "%" : "0";
  }

  // ── 데이터 불러오기 ──────────────────────────────────
  let artSeq = 0;   // 빠르게 여러 번 누르면 늦게 온 앞 응답이 뒤 응답을 덮지 않게, 마지막 요청만 그린다
  async function loadArticle(chunk, hint) {
    if (!LIVE || !isOpenable(chunk)) return false;
    const my = ++artSeq;
    try {
      const [art, m] = await Promise.all([api(`/api/article?chunk=${encodeURIComponent(chunk)}`), api(`/api/amend?chunk=${encodeURIComponent(chunk)}`)]);
      if (my !== artSeq) return false;
      renderArticle(art); renderAmend(m); return true;
    } catch (e) {
      // 답변 근거는 조 단위 ID(24024_제30조)로 오는데 색인은 항 단위(24024_제30조_2항)라 서버가 못 찾는다 (261019 실측).
      // 근거의 규정명·조(·▶▶ 표시에서 읽은 항)로 /api/find를 불러 실제 항 청크 ID를 받아 다시 연다. 가빈 검색기·서버는 그대로 둔다
      if (hint && hint.reg && hint.article && /청크를 찾지 못했습니다/.test(e.message)) {
        const hit = /▶▶ \[찾은 항\] ([①-⑳])/.exec(hint.text || ""), para = hit ? "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳".indexOf(hit[1]) + 1 : 0;
        try {
          const f = await api(`/api/find?q=${encodeURIComponent(`${hint.reg} ${hint.article}${para ? ` 제${para}항` : ""}`)}`);
          if (f.chunk && f.chunk !== chunk) return loadArticle(f.chunk);
        } catch { /* 되찾기도 실패하면 아래 원래 오류를 보인다 */ }
      }
      toast(e.message); return false;
    }
  }
  async function loadChangeset(id) {
    if (!LIVE) return;
    try { renderChangeset(await api(`/api/changeset?id=${encodeURIComponent(id)}`)); } catch (e) { toast(e.message); }
  }
  $("#finder").addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = $("#finder-q").value.trim(); if (!text) return;
    if (!LIVE) return toast("서버에 연결되지 않아 예시만 볼 수 있습니다");
    const box = clear($("#finder-cands")); box.hidden = true;
    try {
      const f = await api(`/api/find?q=${encodeURIComponent(text)}`);
      if (f.chunk) {
        await loadArticle(f.chunk); selectTab("src");
        if (f.guessed) toast(`「${f.reg}」 ${f.label}(으)로 이해했습니다. 다른 규정이면 정확한 이름을 적어 주세요`);
        return;
      }
      // 규정·조를 모르는 요청 → 고칠 후보 조문 (모델 추정). 위치는 사람이 고른다
      if (!f.candidates || !f.candidates.length) return toast("고칠 조문 후보를 찾지 못했습니다. 규정 이름이나 조 번호를 적어 주세요");
      box.append(el("p", "hint", (f.confident ? "고칠 조문 후보입니다. 맞는 조문을 고르세요" : "후보가 확실하지 않습니다. 규정 이름이나 조 번호를 함께 적으면 더 정확합니다")
        + (f.rewritten ? ` (다시 찾은 검색어: 「${f.rewritten}」)` : "")));
      f.candidates.forEach((c) => {
        const b = el("button", "btn secondary small"); b.type = "button";
        b.append(`${c.reg_name} ${c.label}(${c.title})`, el("small", "basis model", "모델 추정"));
        b.addEventListener("click", async () => { box.hidden = true; await loadArticle(c.chunk); selectTab("src"); });
        box.append(b);
      });
      box.hidden = false;
    } catch (err) { toast(err.message); }
  });
  async function pollModel() {
    try {
      const s = await api("/api/chat/status");
      $("#model-state").textContent = s.ready ? "Enter 보내기 · Shift+Enter 줄바꿈" : s.error ? "답변을 준비하지 못했습니다. 관리자에게 문의하세요" : "답변 준비 중…";
      if (s.error) console.warn("답변 모델 오류:", s.error, s.model);
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
    CS_ALL = S.changesets.map((c) => ({ ...c, status_label: "초안" })); if (!$("#view-work").hidden) renderWork();
    renderChangeset({ ...S.changeset, impacts: S.changeset.impacts.map((x) => ({ ...x, state: null })) });
  }

  (async function boot() {
    renderConvList(); renderEmpty();
    try {
      const st = await api("/api/status");
      LIVE = true; REGS = st.regs || 0;
      if (REGS) $("#scope-text").textContent = `내부규정 ${REGS}건 기준 응답`;
      $("#live").hidden = true;   // 정상일 때는 접속 정보(청크 수 · DB 계정)를 보이지 않는다 (서비스 화면, 261019)
      if (st.stale) {   // 가빈 청크가 다시 적재되거나 단위가 바뀌어 우리 항과의 대응이 끊김 → 근거 원문·개정 정보가 안 열릴 수 있음
        $("#live").hidden = false; $("#live").className = "live warn"; $("#live").textContent = `일부 조문을 열 수 없음`;
        $("#live").title = "챗봇 청크(public.chunks)가 바뀌었습니다. 관리자가 scripts/amend_load.py를 실행해 대응을 다시 계산해야 합니다.";
        console.warn(`청크 대응 끊김 ${st.stale}곳 — scripts/amend_load.py로 다시 계산`);
      }
      pollModel();
      await refreshChangesets();
      await loadArticle(S.article.chunk_id);
      const list = await api("/api/changesets"); if (list.length) await loadChangeset(list[0].id);
    } catch (e) {
      $("#live").className = "live off"; $("#live").textContent = "서버에 연결되지 않음 · 예시 화면";
      renderSample();
    }
  })();

  // ── 변경 감시: 법률 DB 판 선택 → 탐색 시작(POST /api/scan/start) → 새로 생김·해결됨 ────────
  const WATCH_BASE = { finding: "처음 점검 결과", rebase: "비교 없이 기준으로 기록" };
  const when = (s) => String(s || "").slice(0, 16).replace("T", " ");
  async function loadWatch(id) {
    if (!LIVE) { $("#watch-last").textContent = "서버에 연결되지 않아 탐색할 수 없습니다"; return; }
    try {
      const [snap, res] = await Promise.all([api("/api/scan/snapshots"), api(`/api/scan${id ? "?id=" + encodeURIComponent(id) : ""}`)]);
      const sel = clear($("#watch-law"));
      snap.snapshots.forEach((s) => {
        const d = s.last_promulgated, o = el("option", null, `${s.file} · 법령 ${s.laws}개 · 최근 공포 ${d.slice(0, 4)}.${d.slice(4, 6)}.${d.slice(6, 8)}${s.file === snap.current ? " (현재 사용 중)" : ""}`);
        o.value = s.file; if (s.file === snap.current) o.selected = true; sel.append(o);
      });
      $("#watch-last").textContent = snap.last ? `마지막 탐색 ${when(snap.last.started_at)} · ${snap.last.law_snapshot}` : "아직 탐색한 적이 없습니다. 처음이면 지금 점검 결과(기준선)와 비교합니다.";
      renderWatch(res);
    } catch (e) { toast(e.message); }
  }
  async function runWatch(rebase) {
    const law = $("#watch-law").value, btn = rebase ? $("#watch-rebase") : $("#watch-go");
    if (!law) return;
    btn.disabled = true; const old = btn.lastChild.textContent; btn.lastChild.textContent = "탐색 중…";
    try { renderWatch(await api("/api/scan/start", { law, rebase })); await loadWatch(); toast(rebase ? "이 판을 기준으로 기록했습니다" : "탐색을 마쳤습니다"); }
    catch (e) { toast(e.message); }
    finally { btn.disabled = false; btn.lastChild.textContent = old; }
  }
  $("#watch-go").addEventListener("click", () => runWatch(false));
  $("#watch-rebase").addEventListener("click", () => runWatch(true));
  function renderWatch(res) {
    const box = clear($("#watch-result")), hist = clear($("#watch-history"));
    hist.append(el("h2", "side-title", "탐색 기록"));
    const ul = el("ul", "nav watch-hist");
    (res.history || []).forEach((h) => {
      const b = el("button"); b.type = "button";
      b.append(el("span", "label", `${when(h.started_at)} · ${h.law_snapshot}`), el("small", null, `새로 ${h.counts.new} · 해결 ${h.counts.resolved}`));
      if (res.scan && h.id === res.scan.id) b.setAttribute("aria-current", "page");
      b.addEventListener("click", () => loadWatch(h.id));
      const li = el("li"); li.append(b); ul.append(li);
    });
    if (!(res.history || []).length) hist.append(el("div", "none", "기록 없음"));
    hist.append(ul);
    const sc = res.scan; if (!sc) return;
    box.append(el("h2", null, "탐색 결과"));
    box.append(el("p", "hint", `${when(sc.started_at)} 탐색 · 법령 ${sc.law_count}개 · 비교 기준: ${WATCH_BASE[sc.base] || "직전 탐색"}`));
    const sum = el("div", "summary watch-sum");
    [["새로 생김", sc.counts.new, sc.counts.new > 0], ["해결됨", sc.counts.resolved, false], ["그대로", sc.counts.kept, false]].forEach(([t, n, hot]) => {
      const s = el("span", hot ? "hot" : null); s.append(t, el("b", null, String(n))); sum.append(s);
    });
    box.append(sum);
    const draftable = res.items.filter((x) => x.change === "new" && (x.action || "").startsWith("초안")).length;
    if (draftable) {   // 감시 → 초안: 법령명 정비는 아래 「법령명 일괄 정비」에 새 판의 결과까지 들어가 있음
      const p = el("p", "hint"); p.append(`새로 생긴 결과 중 ${draftable}건은 AI가 개정안 초안까지 만들 수 있습니다. `);
      const go = el("button", "btn secondary small"); go.type = "button"; go.append("법령명 정비 초안으로 이동");
      go.addEventListener("click", () => $("#lawname").scrollIntoView({ behavior: "smooth" }));
      p.append(go); box.append(p);
    }
    [["new", "새로 생김", "직전 탐색에 없던 점검 결과입니다. 바뀐 법령 조문을 인용하는 곳부터 확인하세요."],
     ["resolved", "해결됨", "직전 탐색에 있었으나 이번에는 나오지 않은 결과입니다."]].forEach(([k, title, hint]) => {
      const items = res.items.filter((x) => x.change === k);
      const s = el("section", "watch-sect"); s.append(el("h3", null, `${title} ${items.length}건`));
      if (!items.length) { s.append(el("div", "none", k === "new" ? "새로 생긴 결과 없음" : "해결된 결과 없음")); box.append(s); return; }
      s.append(el("p", "hint", hint));
      const list = el("ol", "rows");
      items.forEach((x) => {
        const li = el("li", "row"), body = el("div", "body"), top = el("div", "top");
        top.append(el("span", "tag " + (k === "new" ? "warn" : "pos"), x.type), el("span", "where", `${x.reg_name || x.reg_id} ${x.article || x.unit || ""}`));
        // 다음 행동: 에이전트가 초안까지 만들 수 있는 유형인지, 업무 판단이 필요한 유형인지
        if (x.action) top.append(el("span", "basis" + (x.action.startsWith("초안") ? "" : " model"), x.action));
        body.append(top);
        if (x.quote) body.append(el("q", null, x.quote));
        if (x.note) body.append(el("div", "why", x.note));
        if (x.chunk_id && k === "new") {
          const go = el("button", "btn secondary small"); go.type = "button"; go.append("조문 열기"); go.style.alignSelf = "flex-end";
          go.addEventListener("click", async () => { show("chat"); await loadArticle(x.chunk_id); selectTab("amend"); });
          body.append(go);
        }
        li.append(body); list.append(li);
      });
      s.append(list); box.append(s);
    });
  }

  // ── 법령명 일괄 정비: 점검 결과의 옛 법령명·오기 → 규정별 개정안 (GET /api/lawname, POST /api/lawname/create) ──
  async function loadLawname() {
    if (!LIVE) return;
    try { renderLawname(await api("/api/lawname")); } catch (e) { toast(e.message); }
  }
  function renderLawname(p) {
    const box = clear($("#lawname"));
    box.append(el("h2", null, "법령명 일괄 정비"));
    if (!p.mapping.length) { box.append(el("p", "hint", "점검 결과에 옛 법령명·법령명 오기가 없습니다.")); return; }
    box.append(el("p", "hint", "점검 결과에서 찾은 옛 법령명과 오기를 법률 DB의 지금 이름으로 바꾸는 개정문입니다. 같은 이름이 다른 규정에 있으면 함께 바뀝니다. 고른 규정마다 개정안(초안)이 하나씩 만들어집니다."));
    const t = el("table", "cmp-num lawname-map"), hd = el("tr");
    ["구분", "지금 표기", "바꿀 이름", "점검에서 찾은 곳"].forEach((h) => hd.append(el("th", null, h)));
    t.append(hd);
    p.mapping.forEach((m) => {
      const tr = el("tr");
      [el("span", "tag " + (m.kind === "오기" ? "warn" : "accent"), m.kind), m.old, m.new, m.where.join(", ")].forEach((v) => { const td = el("td"); td.append(v); tr.append(td); });
      t.append(tr);
    });
    box.append(t);
    const list = el("ol", "rows");
    p.packages.forEach((pk) => {
      const li = el("li", "row"), body = el("div", "body"), top = el("div", "top");
      const cb = el("input"); cb.type = "checkbox"; cb.value = pk.reg_id; cb.checked = !pk.open && pk.status === "ok"; cb.disabled = pk.open || pk.status !== "ok";
      cb.setAttribute("aria-label", `${pk.reg_name} 개정안 만들기`);
      top.append(cb, el("span", "where", pk.reg_name), el("small", null, `${pk.count}곳`));
      if (pk.open) top.append(el("span", "tag", "진행 중인 정비 개정안 있음"));
      if (pk.status !== "ok") top.append(el("span", "tag neg", pk.status));
      body.append(top, el("pre", "amend-text", pk.amendment));
      li.append(body); list.append(li);
    });
    box.append(list);
    if (p.manual.length || p.check.length) {
      const s = el("section", "watch-sect");
      s.append(el("h3", null, `직접 고칠 곳 ${p.manual.length + p.check.length}건`),
               el("p", "hint", "별표·별지·부칙은 개정문 적용기가 다루지 않고, 다른 낱말 속에 들어간 이름은 자동으로 바꾸지 않습니다."));
      [...p.manual, ...p.check].forEach((h) => s.append(el("div", "why", `${h.reg_name} ${h.article} — 「${h.old}」 → 「${h.new}」`)));
      box.append(s);
    }
    const go = el("button", "btn primary"); go.type = "button"; go.append(icon("edit"), "고른 규정 개정안 만들기");
    go.addEventListener("click", async () => {
      const regs = [...box.querySelectorAll("input[type=checkbox]:checked")].map((x) => x.value);
      if (!regs.length) return toast("만들 규정을 골라 주세요");
      go.disabled = true; go.lastChild.textContent = "만드는 중…";
      try {
        const r = await api("/api/lawname/create", { regs });
        toast(`개정안 ${r.made.length}건을 만들었습니다${r.skipped.length ? ` (건너뜀 ${r.skipped.length})` : ""} — 왼쪽 「내 개정안」에서 검토하세요`, 5000);
        await refreshChangesets(); await loadLawname();
      } catch (e) { toast(e.message); go.disabled = false; go.lastChild.textContent = "고른 규정 개정안 만들기"; }
    });
    const foot = el("div", "actions-row"); foot.append(go); box.append(foot);
  }

  // ── 개정 업무 첫 화면: 업무가 어떻게 흘러가는지 · 어디서 시작하는지 · 지금 내가 할 일 ──────
  // 단계와 담당은 상태 흐름(초안 → 검토 → 승인 → 공포, sql/amend_schema.sql)과 역할 권한(app/web_agent.guard)에 맞춘다
  const WORK_STEPS = [
    { n: "개정 계기", who: "담당자", what: "질문하다 규정 사이 모순을 발견하거나, 법령이 바뀌었거나, 개정 지시가 내려옴" },
    { n: "개정 요청", who: "담당자", what: "대화창에 「○○규정 제△조 … 바꿔줘」처럼 한 문장으로 요청" },
    { n: "AI 검토", who: "AI", what: "고칠 조문 찾기 → 개정문 · 신구대비표 → 깨지는 인용 연쇄 수정 → 영향 카드" },
    { n: "카드 결정 · 제출", who: "담당자", what: "영향 카드마다 수락 · 기각하고 승인권자에게 제출", status: "초안" },
    { n: "승인 · 반려", who: "승인권자", what: "수락한 카드만 반영된 최종안을 보고 승인하거나 반려", status: "검토" },
    { n: "공포", who: "담당자", what: "최종안 내려받기 · 등록 · 공지. 함께 개정할 규정은 새 초안으로 이어짐", status: "승인" },
  ];
  const TODO = {   // 역할별로 지금 손댈 개정안: 상태 → [할 일, 버튼]
    editor: { 초안: ["영향 카드를 결정하고 제출하세요", "결정하러 가기"], 승인: ["승인됐습니다. 공포 처리하세요", "공포 처리하기"], 검토: ["승인권자 검토를 기다리는 중", "보기"] },
    approver: { 검토: ["담당자가 제출했습니다. 승인 또는 반려하세요", "검토하러 가기"] },
  };
  const csTitle = (c) => {   // 대화에서 만든 개정안은 제목이 「제30조 — 요청 문장(규정 이름 포함)」 (renderChangeset과 같게 정리)
    const t = c.title.replace(/\s*\([^)]*\)\s*$/, "").replace(/^\S+\s+—\s+/, "");
    return !c.reg || t.startsWith(c.reg) ? t : `${c.reg} — ${t}`;
  };
  function renderWork() {
    const D = clear($("#work-doc")), role = isApprover() ? "approver" : "editor";
    const by = (s) => CS_ALL.filter((c) => c.status_label === s);
    $("#work-scope").textContent = role === "editor" ? "개정 담당자" : "승인권자";

    // 1. 흐름: 6단계, 단계마다 누가 하는지와 지금 그 단계에 있는 개정안 수
    const s1 = el("section", "work-flow");
    s1.append(el("h2", null, "규정 개정은 이렇게 진행됩니다"),
      el("p", "hint", "AI는 초안과 영향 점검까지 만듭니다. 고칠지 말지는 카드마다 담당자가 정하고, 확정은 승인권자가 합니다. 법적 판단은 하지 않습니다."));
    const ol = el("ol", "wf");
    WORK_STEPS.forEach((s, i) => {
      const li = el("li", "wf-step" + (s.who === "AI" ? " agent" : "") + (s.who === (role === "editor" ? "담당자" : "승인권자") ? " mine" : ""));
      const top = el("div", "wf-top"); top.append(el("span", "sn", String(i + 1)), el("span", "who", s.who));
      li.append(top, el("b", null, s.n), el("p", null, s.what));
      if (s.status) { const k = by(s.status).length; li.append(el("span", "chip " + (k ? "accent" : ""), `지금 ${k}건`)); }
      ol.append(li);
    });
    s1.append(ol); D.append(s1);

    // 2. 시작하는 곳 (담당자만): 업무는 세 가지 계기에서 시작한다
    if (role === "editor") {
      const s2 = el("section");
      s2.append(el("h2", null, "어디서 시작하나요"));
      const g = el("div", "start-grid");
      const card = (ic, title, desc, label, fn) => {
        const b = el("button", "start"); b.type = "button";
        const h = el("span", "start-h"); h.append(icon(ic), el("b", null, title));
        b.append(h, el("span", null, desc), el("span", "go", label)); b.lastChild.append(icon("arrow"));
        b.addEventListener("click", fn); g.append(b);
      };
      card("edit", "바꿀 내용을 알 때", "개정 요청 대화를 엽니다. 적은 요청으로 AI가 고칠 조문을 찾아 초안을 만듭니다.", "개정 요청 쓰기", () => newConv(true));
      card("search", "법령이 바뀌었을 때", "새로 반입한 법률 DB로 탐색해, 바뀐 법령을 인용하는 내규와 법령명 정비 초안을 봅니다.", "변경 감시 열기", () => show("watch"));
      card("book", "조문부터 보고 싶을 때", "조문을 열어 인용하는 곳 · 점검 결과 · 관련 법령을 본 뒤 그 조의 개정을 요청합니다.", "조문 열기", () => {
        show("chat"); openPanel(); selectTab("amend"); $("#finder-q").focus();
      });
      s2.append(g); D.append(s2);
    }

    // 3. 내가 할 일: 역할에 맞는 상태의 개정안만
    const s3 = el("section");
    const mine = CS_ALL.filter((c) => TODO[role][c.status_label]);
    s3.append(el("h2", null, `내가 할 일 ${mine.filter((c) => !(role === "editor" && c.status_label === "검토")).length}건`));
    if (!LIVE) s3.append(el("p", "hint", "예시 데이터입니다 (서버 없음)."));
    if (!mine.length) s3.append(el("div", "none", role === "editor" ? "진행 중인 개정안이 없습니다. 위에서 개정을 시작하세요." : "검토를 기다리는 개정안이 없습니다."));
    const ul = el("ol", "rows todo");
    mine.sort((a, b) => (a.status_label === "검토") - (b.status_label === "검토")).forEach((c) => {
      const [what, btn] = TODO[role][c.status_label], [lab, tone] = CS_LABEL[c.status_label] || [c.status_label, ""];
      const li = el("li", "row" + (role === "editor" && c.status_label === "검토" ? " wait" : "")), body = el("div", "body"), top = el("div", "top");
      top.append(el("span", "chip " + tone, lab), el("span", "where", csTitle(c)), el("small", null, c.id));
      const go = el("button", "btn " + (role === "editor" && c.status_label === "검토" ? "tertiary" : "primary") + " small"); go.type = "button";
      go.dataset.view = "review"; go.dataset.cs = c.id; go.append(btn, icon("arrow"));
      const row = el("div", "todo-row"); row.append(el("span", "why", what), go);
      body.append(top, row); li.append(body); ul.append(li);
    });
    if (mine.length) s3.append(ul);
    const done = by("공포").length, rej = by("기각").length;
    if (done || rej) s3.append(el("p", "hint", `끝난 개정안: 공포 ${done}건 · 반려 ${rej}건 (왼쪽 「내 개정안」 목록에 있습니다)`));
    D.append(s3);
  }

  // ── 화면 전환·권한 ──────────────────────────────────
  function show(view) {
    $("#view-chat").hidden = view !== "chat"; $("#view-review").hidden = view !== "review"; $("#view-watch").hidden = view !== "watch";
    $("#view-work").hidden = view !== "work";
    if (view === "watch") { loadWatch(); loadLawname(); }
    if (view === "work") renderWork();
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
  const ROLE_LABEL_UI = { employee: "일반 직원", editor: "개정 담당자", approver: "승인권자" };
  function setRole(role, first = false) {
    const prev = ROLE_NOW;
    ROLE_NOW = role; USER = PERSON[role].name;
    document.querySelectorAll(".seg button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.role === role)));
    document.querySelectorAll(".editor-only").forEach((x) => (x.hidden = role !== "editor"));
    document.querySelectorAll(".staff-only").forEach((x) => (x.hidden = role === "employee"));
    $("#user-name").textContent = USER; $("#user-dept").textContent = PERSON[role].dept; $("#user-initial").textContent = USER[0];
    $("#user-role").textContent = ROLE_LABEL_UI[role];
    if (prev !== role) {   // 다른 사람으로 바뀐 것: 그 사람의 대화 기록을 불러오고 열려 있던 대화는 닫는다 (답을 기다리던 대화는 원래 역할의 기록에 저장됨)
      convs = load(); convId = null; AMEND_NEW = false; $("#chat-title").textContent = "새 대화";
      renderConvList(); renderEmpty();
    }
    if (!conv()) renderEmpty();   // 자주 묻는 질문이 역할마다 다르다 (개정 담당자에게만 개정 요청 예시)
    setBusy(busy);
    // 역할을 바꿔도 보던 화면을 유지한다. 그 역할이 못 보는 화면일 때만 기본 화면으로 (처음: 담당자 · 승인권자는 개정 업무)
    const ALLOWED = { employee: ["chat"], editor: ["chat", "work", "review", "watch"], approver: ["chat", "work", "review"] };
    const now = ["chat", "work", "review", "watch"].find((v) => !$(`#view-${v}`).hidden);
    if (first || !ALLOWED[role].includes(now)) show(role === "employee" ? "chat" : "work");
    else if (now === "work") renderWork();   // 할 일 · 시작하는 곳이 역할마다 다르다
    if (role === "employee") selectTab("src");
    if (CS) renderChangeset(CS);   // 역할에 따라 버튼·결정 가능 여부가 달라진다
  }
  document.querySelectorAll(".seg button").forEach((b) => b.addEventListener("click", () => setRole(b.dataset.role)));
  setRole("editor", true);
})();
