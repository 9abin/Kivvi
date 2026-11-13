"use strict";

// 서버 API는 그대로 쓴다: GET /api/edit/catalog[?reg=], POST /api/edit/review
const $ = (id) => document.getElementById(id);
const view = $("view");
const params = new URLSearchParams(location.search);

// 판단 모델(별도 환경 · 하위 프로세스)과 luna를 거치므로 영향 분석은 오래 걸릴 수 있다
const REVIEW_TIMEOUT_MS = 300000;   // 서버 단계 제한(판단 모델 90초 + luna 60초 × 2 + 검토 문장)보다 길게
const STEPS = ["규정 문서 선택", "조항 선택·수정", "수정 전·후 비교", "영향 확인·검토"];
const NOTE_MAX = 500;

const ICONS = {
  home: '<path d="M3 11.5 12 4l9 7.5"/><path d="M5 10v10h14V10"/>',
  search: '<circle cx="11" cy="11" r="6.5"/><path d="m16 16 4.5 4.5"/>',
  analyze: '<circle cx="6" cy="12" r="2.5"/><circle cx="18" cy="6" r="2.5"/><circle cx="18" cy="18" r="2.5"/><path d="m8.3 11 7.4-3.8M8.3 13l7.4 3.8"/>',
  history: '<path d="M3.5 12a8.5 8.5 0 1 0 2.6-6.1"/><path d="M3.5 4.5v4h4"/><path d="M12 8v4.5l3 1.8"/>',
  chevron: '<path d="m9 6 6 6-6 6"/>',
  check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  doc: '<path fill="currentColor" stroke="none" d="M7.5 2.5h6.6c.5 0 1 .2 1.4.6l3.4 3.4c.4.4.6.9.6 1.4V19a2.5 2.5 0 0 1-2.5 2.5h-9.5A2.5 2.5 0 0 1 5 19V5a2.5 2.5 0 0 1 2.5-2.5Z"/><path fill="#fff" stroke="none" opacity=".55" d="M14.5 2.7V6a1.5 1.5 0 0 0 1.5 1.5h3.3Z"/><path fill="#fff" stroke="none" d="M8.2 12h7.6a.8.8 0 0 1 0 1.6H8.2a.8.8 0 0 1 0-1.6ZM8.2 15.6h7.6a.8.8 0 0 1 0 1.6H8.2a.8.8 0 0 1 0-1.6Z"/>',
  left: '<path d="M19 12H5m6-6-6 6 6 6"/>',
  right: '<path d="M5 12h14m-6-6 6 6-6 6"/>',
  down: '<path d="M12 5v14m-6-6 6 6 6-6"/>',
  bell: '<path d="M12 3.2a5.6 5.6 0 0 0-5.6 5.6v3.1c0 1-.4 2-1.1 2.8l-.8.9c-.7.8-.2 2 .9 2h13.2c1.1 0 1.6-1.2.9-2l-.8-.9a4.2 4.2 0 0 1-1.1-2.8V8.800A5.600 5.600 0 0 0 12 3.200Z"/><path d="M9.900 19.700a2.300 2.300 0 0 0 4.200 0"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
};

function icon(name, cls = "ico") {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", cls);
  svg.setAttribute("aria-hidden", "true");
  svg.innerHTML = ICONS[name];   // 고정 문자열만 넣는다
  return svg;
}

function el(tag, props, ...kids) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value == null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    node.append(typeof kid === "object" ? kid : document.createTextNode(String(kid)));
  }
  return node;
}

async function api(path, body) {
  const options = body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {};
  const response = await fetch(path, { ...options, signal: AbortSignal.timeout(body ? REVIEW_TIMEOUT_MS : 12000) });
  const data = await response.json();
  if (!response.ok || data.error) throw new Error(data.error || "요청을 처리하지 못했습니다.");
  return data;
}

/* ---------- 상태 ---------- */
const S = {
  regs: [], regsError: "",
  regId: "", regName: "", cat: null, catError: "",
  articleId: "", edited: "", editing: false,
  result: null, analyzing: false, analysisError: "", started: 0, timer: 0,
  review: {},            // 후보 id → { done, note } (화면에서만 유지)
};
const catalogs = new Map();

async function loadCatalog(id) {
  if (!catalogs.has(id)) catalogs.set(id, await api("/api/edit/catalog?" + new URLSearchParams({ reg: id })));
  return catalogs.get(id);
}
const articles = () => (S.cat ? S.cat.articles.filter((a) => !a.deleted) : []);
const currentArticle = () => articles().find((a) => a.id === S.articleId) || null;
const isChanged = () => {
  const a = currentArticle();
  return Boolean(a) && S.edited.trim() !== "" && S.edited.trim() !== a.text.trim();
};

/* ---------- 문구 비교(화면용) ---------- */
// 공백 · 숫자 · 한글 덩어리 · 영문 덩어리 · 기호 한 글자씩 나눈다. 「2회(나누어」가 통째로 바뀐 것처럼 보이지 않고 「2」만 표시된다.
const tokens = (text) => text.match(/\s+|\d+|[가-힣]+|[A-Za-z]+|[^]/gu) || [];

function diff(before, after) {
  const a = tokens(before), b = tokens(after), n = a.length, m = b.length;
  const ops = [];
  const push = (type, text) => {
    const last = ops[ops.length - 1];
    if (last && last.type === type) last.text += text; else ops.push({ type, text });
  };
  if (n * m > 4_000_000) {   // 아주 긴 조문은 앞뒤 공통부분만 걷어 낸다
    let i = 0;
    while (i < n && i < m && a[i] === b[i]) i++;
    let j = 0;
    while (j < n - i && j < m - i && a[n - 1 - j] === b[m - 1 - j]) j++;
    push("same", a.slice(0, i).join(""));
    push("del", a.slice(i, n - j).join(""));
    push("add", b.slice(i, m - j).join(""));
    push("same", a.slice(n - j).join(""));
    return ops.filter((o) => o.text);
  }
  const dp = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  }
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) { push("same", a[i]); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { push("del", a[i]); i++; }
    else { push("add", b[j]); j++; }
  }
  while (i < n) push("del", a[i++]);
  while (j < m) push("add", b[j++]);
  return ops;
}

function renderSide(ops, side) {
  const wrap = el("div", { class: "text" });
  for (const op of ops) {
    if (op.type === "same") wrap.append(document.createTextNode(op.text));
    else if (op.type === (side === "old" ? "del" : "add")) wrap.append(el("mark", { class: op.type, text: op.text }));
  }
  return wrap;
}

// 바뀐 곳마다 앞뒤 문맥과 위치(몇 항인지)를 붙여 「어느 숫자를 고쳤는지」가 보이게 한다
function changeList(ops) {
  const flat = (text) => text.replace(/\s+/g, " ");
  const out = [];
  let before = "";   // 바뀌기 전 본문(같은 부분 + 지운 부분)
  for (let i = 0; i < ops.length; i++) {
    if (ops[i].type === "same") { before += ops[i].text; continue; }
    let del = "", add = "";
    const start = i;
    for (; i < ops.length && ops[i].type !== "same"; i++) { if (ops[i].type === "del") del += ops[i].text; else add += ops[i].text; }
    const prev = start > 0 ? flat(ops[start - 1].text) : "";
    const next = i < ops.length ? flat(ops[i].text) : "";
    let head = prev.slice(-16), tail = next.slice(0, 16);
    if (prev.length > 16 && head.includes(" ") && prev[prev.length - 17] !== " ") head = head.slice(head.indexOf(" ") + 1);   // 단어 중간에서 시작하지 않게
    if (next.length > 16 && tail.includes(" ") && next[16] !== " ") tail = tail.slice(0, tail.lastIndexOf(" "));
    const marks = before.match(/[①-⑳]/g);
    out.push({ label: marks ? `${marks[marks.length - 1]}항` : "", head: (start > 0 && prev.length > 16 ? "… " : "") + head.trimStart(), tail: tail.trimEnd() + (next.length > 16 ? " …" : ""), del: flat(del).trim(), add: flat(add).trim() });
    before += del;
    i--;
  }
  return out;
}

function changeCard(c) {
  return el("div", { class: "chg" }, c.label && el("small", { text: c.label }),
    el("p", null, c.head, c.del && el("del", { text: c.del }), c.del && c.add && el("span", { class: "to", text: "→" }), c.add && el("ins", { text: c.add }), c.tail));
}

// 본문 첫 줄에서 「제N조(제목)」 머리말을 걷어 낸다(목록 · 읽기 화면용)
function bodyText(a) {
  const lines = a.text.split("\n");
  const first = lines[0].replace(/^\s*제\s*\d+\s*(?:-\s*\d+\s*)?조(?:\s*의\s*\d+|\d+(?=\s*[(（]))?\s*/, "");
  lines[0] = a.title && (first.startsWith(`(${a.title})`) || first.startsWith(`（${a.title}）`)) ? first.slice(a.title.length + 2).trimStart() : first;
  return lines.join("\n").trimStart();
}
const articleName = (a) => [a.label, a.title && `(${a.title})`].filter(Boolean).join(" ");

/* ---------- 공통 화면 부품 ---------- */
function pageHead(title, sub) {
  return [el("h1", { class: "page-title", text: title }), el("p", { class: "page-sub", text: sub })];
}

function actionBar({ back, next, hint }) {
  const bar = el("div", { class: "actionbar" });
  const backBtn = back && el("button", { type: "button", class: "btn", onclick: back.onclick }, icon("left"), back.label);
  const nextBtn = next && el("button", { type: "button", class: "btn primary", disabled: next.disabled, onclick: next.onclick }, next.label, icon("right"));
  bar.append(backBtn || el("span"), hint ? el("span", { class: "hint", text: hint }) : "", nextBtn || el("span"));
  return { node: bar, next: nextBtn, hint: bar.querySelector(".hint") };
}

function regPicker(onPick) {
  const search = el("input", { type: "search", placeholder: "규정명으로 검색하세요", "aria-label": "규정명 검색" });
  const count = el("p", { class: "count" });
  const list = el("ul", { class: "doc-list", style: "list-style:none" });
  function draw() {
    const query = search.value.replace(/\s+/g, "").toLowerCase();
    list.replaceChildren();
    let shown = 0;
    for (const reg of S.regs) {
      if (!reg.name.replace(/\s+/g, "").toLowerCase().includes(query)) continue;
      shown++;
      list.append(el("li", null, el("button", { type: "button", class: "doc-row", "aria-pressed": String(S.regId === reg.id), onclick: () => onPick(reg) },
        el("span", { class: "radio" }), el("span", { class: "doc-ico" }, icon("doc")),
        el("span", { class: "doc-name", text: reg.name }), el("span", { class: "chev" }, icon("chevron")))));
    }
    count.textContent = `총 ${shown}건`;
    if (!shown) list.append(el("li", { class: "empty", text: S.regsError || "표시할 규정이 없습니다." }));
  }
  search.addEventListener("input", draw);
  draw();
  list.querySelector('[aria-pressed="true"]')?.scrollIntoView({ block: "nearest" });   // 이미 고른 규정이 목록 아래에 있으면 보이게 한다
  return { node: el("div", null, el("div", { class: "search" }, icon("search"), search), count, list), redraw: draw };
}

function emptyPreview(text) {
  return el("div", { class: "empty-state" }, el("img", { src: "img/document-search.png", alt: "", width: 350, height: 220 }), el("p", { text }));
}

// 규정 전문을 쭉 보여 준다(읽기 전용). 규정 탐색과 영향 분석 1단계가 함께 쓴다.
function fillDocument(panel) {
  panel.replaceChildren();
  if (!S.regId) return panel.append(emptyPreview("왼쪽에서 규정 문서를 선택하면\n해당 문서의 내용을 확인할 수 있어요."));
  if (S.catError) return panel.append(el("p", { class: "notice error", text: S.catError }));
  if (!S.cat) return panel.append(el("p", { class: "muted", text: "규정 문서를 불러오는 중입니다…" }));
  panel.append(el("div", { class: "doc-head" }, el("span", { class: "doc-ico" }, icon("doc")), el("h2", { text: S.cat.name })));
  if (S.cat.preamble.length) panel.append(el("p", { class: "doc-text", text: S.cat.preamble.join("\n") }));
  let chapter = "";
  for (const a of S.cat.articles) {
    if (a.chapter && a.chapter !== chapter) panel.append(el("h3", { class: "chapter", text: a.chapter }));
    chapter = a.chapter || chapter;
    panel.append(el("section", { class: a.chapter ? "article indent" : "article" }, el("h3", { text: [a.label, a.title].filter(Boolean).join(" ") }), el("p", { text: bodyText(a) })));
  }
  for (const item of S.cat.sections) panel.append(el("section", { class: "article" }, el("h3", { text: item.label }), el("p", { text: item.text })));
}

// 진행 막대: 왼쪽에 「1 / 4 규정 문서 선택」, 오른쪽에 단계 수만큼 막대
function progress(now) {
  return el("div", { class: "progress", role: "img", "aria-label": `${STEPS.length}단계 중 ${now}단계: ${STEPS[now - 1]}` },
    el("strong", { class: "label" }, el("span", { text: `${now} / ${STEPS.length}` }), el("span", { text: STEPS[now - 1] })),
    el("div", { class: "bars" }, STEPS.map((_, i) => el("span", { class: i < now ? "on" : "" }))));
}

/* ---------- 라우팅 ---------- */
const currentRoute = () => location.hash.replace(/^#\/?/, "") || "home";
const go = (route) => { if (location.hash === "#/" + route) render(); else location.hash = "#/" + route; };

function drawChrome(route) {
  const step = /^analyze\/(\d)$/.exec(route);
  const detail = /^history\/(edit-[0-9a-f]{32})$/.exec(route);
  const section = step ? "analyze/1" : detail ? "history" : route;
  for (const button of document.querySelectorAll("#menu button[data-route]")) {
    button.disabled = S.analyzing;
    if (button.dataset.route === section) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current");
  }
  const back = $("back");
  back.hidden = route === "home";
  back.replaceChildren(icon("left"), "이전으로");
  back.disabled = S.analyzing;
  back.onclick = () => go(step && +step[1] > 1 ? `analyze/${+step[1] - 1}` : detail ? "history" : "home");
}

function render() {
  clearInterval(S.timer);
  let route = currentRoute();
  const step = /^analyze\/(\d)$/.exec(route);
  if (step) {   // 앞 단계가 끝나지 않았으면 그 단계로 돌려보낸다
    const n = +step[1];
    if (n >= 2 && !S.regId) return go("analyze/1");
    if (n >= 3 && !(currentArticle() && isChanged())) return go(S.regId ? "analyze/2" : "analyze/1");
    if (n === 4 && !S.result && !S.analyzing && !S.analysisError) return go("analyze/3");
  } else if (!["home", "browse", "history"].includes(route) && !/^history\/edit-[0-9a-f]{32}$/.test(route)) {
    route = "home";
  }
  drawChrome(route);
  let page;
  if (route === "home") page = pageHome();
  else if (route === "browse") page = pageBrowse();
  else if (route === "history") page = pageHistory();
  else if (route.startsWith("history/")) page = pageHistoryDetail(route.slice(8));
  else page = [pageStep1, pageStep2, pageStep3, pageStep4][+step[1] - 1]();
  view.replaceChildren(page);
  // 하단 버튼 줄은 화면 폭 전체에 깔리도록 본문 밖(#actions)으로 옮긴다
  const bar = page.querySelector(".actionbar");
  $("actions").replaceChildren(...(bar ? [bar] : []));
  $("actions").hidden = !bar;
  window.scrollTo(0, 0);
}
// 설정 · 알림 · 프로필은 아직 연결된 기능이 없다. 눌렀을 때 솔직하게 안내만 한다.
$("settings").prepend(icon("settings"));
$("bell").append(icon("bell"));
$("settings").addEventListener("click", () => toast("설정은 준비 중이에요."));
$("bell").addEventListener("click", () => toast("새 알림이 없어요."));
$("me").addEventListener("click", () => toast("프로필은 준비 중이에요."));
window.addEventListener("hashchange", render);
for (const button of document.querySelectorAll("#menu button[data-route]")) {
  button.prepend(icon(button.dataset.icon));
  button.addEventListener("click", () => go(button.dataset.route));
}

/* ---------- 홈 ---------- */
const HOME_STEPS = [
  ["규정 선택", "수정할 규정 문서를 고릅니다."],
  ["조항 선택·수정", "바꿀 조항을 고르고 수정안을 씁니다."],
  ["수정 전·후 비교", "바뀐 부분을 한눈에 확인합니다."],
  ["영향 확인·검토", "함께 확인할 조항을 보고 검토를 저장합니다."],
];

// 홈에서 규정을 골라 바로 조항 선택으로 간다
function startWith(reg) {
  if (S.regId !== reg.id) { S.articleId = ""; S.edited = ""; S.result = null; S.analysisError = ""; S.review = {}; }
  S.regId = reg.id; S.regName = reg.name; S.cat = null; S.catError = ""; S.editing = false;
  go("analyze/2");
}

function homeSearch() {
  const input = el("input", { type: "search", placeholder: "수정하려는 규정 이름을 검색하세요", "aria-label": "규정 검색", autocomplete: "off", role: "combobox", "aria-expanded": "false", "aria-controls": "home-results" });
  const list = el("ul", { class: "home-results", id: "home-results", role: "listbox", hidden: true });
  let hits = [], active = -1;
  const draw = () => {
    list.replaceChildren(...hits.map((reg, i) => el("li", { role: "option", "aria-selected": String(i === active) },
      el("button", { type: "button", tabindex: "-1", onmousedown: (e) => { e.preventDefault(); startWith(reg); } },
        el("span", { class: "doc-ico" }, icon("doc")), el("span", { text: reg.name }), el("span", { class: "go", text: "조항 선택하기" })))));
    list.hidden = !hits.length;
    input.setAttribute("aria-expanded", String(hits.length > 0));
  };
  const find = () => {
    const q = input.value.replace(/\s+/g, "").toLowerCase();
    hits = q ? S.regs.filter((r) => r.name.replace(/\s+/g, "").toLowerCase().includes(q)).slice(0, 6) : [];
    active = hits.length ? 0 : -1;
    if (q && !hits.length) { list.replaceChildren(el("li", { class: "none", text: "일치하는 규정이 없어요." })); list.hidden = false; return; }
    draw();
  };
  input.addEventListener("input", find);
  input.addEventListener("blur", () => { hits = []; list.hidden = true; });
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      if (!hits.length) return;
      e.preventDefault();
      active = (active + (e.key === "ArrowDown" ? 1 : -1) + hits.length) % hits.length;
      draw();
    } else if (e.key === "Enter" && hits[active]) {
      e.preventDefault();
      startWith(hits[active]);
    } else if (e.key === "Escape") {
      input.value = ""; hits = []; list.hidden = true;
    }
  });
  return el("div", { class: "home-search" }, el("div", { class: "search" }, icon("search"), input), list);
}

function pageHome() {
  const stepNodes = HOME_STEPS.map(([name, text], i) => el("li", { class: "hstep", tabindex: "0" },
    el("span", { class: "num" }, String(i + 1)), el("strong", { text: name }), el("p", { text })));
  let current = 0, paused = false;
  const show = (i) => { current = i; stepNodes.forEach((n, k) => n.classList.toggle("active", k === i)); };
  stepNodes.forEach((node, i) => {
    node.addEventListener("mouseenter", () => { paused = true; show(i); });
    node.addEventListener("focus", () => { paused = true; show(i); });
    node.addEventListener("mouseleave", () => { paused = false; });
    node.addEventListener("blur", () => { paused = false; });
  });
  show(0);
  if (!matchMedia("(prefers-reduced-motion: reduce)").matches) S.timer = setInterval(() => { if (!paused) show((current + 1) % HOME_STEPS.length); }, 2200);

  const recent = el("div", { class: "recent-list" }, el("p", { class: "muted", text: "불러오는 중입니다…" }));
  api("/api/edit/history").then((data) => {
    recent.replaceChildren();
    if (!data.items.length) return recent.append(el("p", { class: "muted", text: "아직 저장한 검토가 없어요. 첫 영향 분석을 시작해 보세요." }));
    for (const r of data.items.slice(0, 3)) {
      recent.append(el("button", { type: "button", class: "recent-card", onclick: () => go(`history/${r.id}`) },
        el("strong", { text: `${r.regulation} ${r.article}` }),
        el("span", { class: "muted", text: `${when(r.saved_at)} 저장` }),
        el("span", { class: "chip " + (r.total && r.done === r.total ? "blue" : ""), text: `${r.total}건 중 ${r.done}건 검토 완료` })));
    }
  }).catch(() => recent.replaceChildren(el("p", { class: "muted", text: "검토 내역을 불러오지 못했어요." })));

  const card = (iconName, title, text, route) => el("button", { type: "button", class: "home-card", onclick: () => go(route) },
    el("span", { class: "doc-ico" }, icon(iconName)), el("span", { class: "body" }, el("strong", { text: title }), el("p", { text })), el("span", { class: "arrow" }, icon("right")));

  return el("section", { class: "page home" },
    el("div", { class: "hero fade" },
      el("div", { class: "hero-text" },
        el("h1", null, "한 조항을 바꿀 때,", el("br"), "함께 확인할 규정까지"),
        el("p", { text: "고치려는 규정을 찾으면, 영향이 갈 수 있는 조항을 근거와 함께 보여 드려요." }),
        homeSearch(),
        S.regsError && el("p", { class: "notice error", text: S.regsError })),
      el("img", { class: "hero-art", src: "img/document-search.png", alt: "", width: 350, height: 220 })),
    el("div", { class: "home-cards fade", style: "--d: .08s" },
      card("search", "규정 탐색", "규정 90건의 내용을 읽어 볼 수 있어요.", "browse"),
      card("analyze", "영향 분석 시작하기", "규정을 고치고, 영향받는 조항을 확인해요.", "analyze/1")),
    el("section", { class: "home-block fade", style: "--d: .16s" }, el("h2", { text: "이렇게 진행돼요" }), el("ol", { class: "hsteps" }, stepNodes)),
    el("section", { class: "home-block fade", style: "--d: .24s" },
      el("h2", null, "최근 검토", el("button", { type: "button", class: "link-btn", onclick: () => go("history") }, "전체 보기")), recent));
}

// 이미 고른 규정을 다시 누르면 선택을 푼다(안내 화면으로 돌아감)
function clearRegSelection() {
  S.regId = ""; S.regName = ""; S.cat = null; S.catError = ""; S.articleId = ""; S.edited = ""; S.editing = false; S.result = null; S.review = {};
}

/* ---------- 규정 탐색(읽기 전용) ---------- */
function pageBrowse() {
  const reader = el("section", { class: "panel reader" });
  const picker = regPicker(async (reg) => {
    if (S.regId === reg.id) { clearRegSelection(); picker.redraw(); drawReader(); return; }
    S.regId = reg.id; S.regName = reg.name; S.cat = null; S.catError = ""; S.articleId = ""; S.edited = ""; S.editing = false; S.result = null; S.review = {};
    picker.redraw();
    drawReader();
    try { S.cat = await loadCatalog(reg.id); } catch (error) { S.catError = error.message; }
    if (S.regId === reg.id) drawReader();
  });
  const drawReader = () => fillDocument(reader);
  drawReader();
  if (S.regId && !S.cat) loadCatalog(S.regId).then((cat) => { S.cat = cat; drawReader(); }).catch((error) => { S.catError = error.message; drawReader(); });
  return el("section", { class: "page browse" }, ...pageHead("규정 탐색", `${S.regs.length || 90}건의 내부규정을 읽어 볼 수 있어요.`),
    el("div", { class: "split" }, el("div", null, picker.node), reader));
}

/* ---------- 영향 분석 1단계: 규정 선택 ---------- */
function pageStep1() {
  const preview = el("section", { class: "panel reader" });
  let next;
  const drawPreview = () => fillDocument(preview);
  const picker = regPicker(async (reg) => {
    if (S.regId === reg.id) { clearRegSelection(); picker.redraw(); drawPreview(); next.next.disabled = true; return; }
    if (S.regId !== reg.id) { S.articleId = ""; S.edited = ""; S.editing = false; S.result = null; S.review = {}; }
    S.regId = reg.id; S.regName = reg.name; S.cat = null; S.catError = "";
    picker.redraw(); drawPreview(); next.next.disabled = true;
    try { S.cat = await loadCatalog(reg.id); } catch (error) { S.catError = error.message; }
    if (S.regId === reg.id) { drawPreview(); next.next.disabled = !S.cat; }
  });
  next = actionBar({ next: { label: "다음 단계로 이동", disabled: !(S.regId && S.cat), onclick: () => { S.editing = Boolean(S.articleId && S.editing); go("analyze/2"); } } });
  if (S.regId && !S.cat) loadCatalog(S.regId).then((cat) => { S.cat = cat; drawPreview(); next.next.disabled = false; }).catch((error) => { S.catError = error.message; drawPreview(); });
  drawPreview();
  return el("section", { class: "page pick" }, progress(1), ...pageHead("수정할 규정 문서를 선택해주세요", `${S.regs.length || 90}건의 규정 문서 중에서 개정할 문서를 찾아 선택하세요.`),
    el("div", { class: "split" }, el("div", null, picker.node), preview), next.node);
}

/* ---------- 영향 분석 2단계: 조항 선택 · 수정안 작성 ---------- */
function pageStep2() {
  const page = el("section", { class: "page" });
  if (!S.cat) {
    page.append(el("p", { class: "muted", text: "규정 문서를 불러오는 중입니다…" }));
    loadCatalog(S.regId).then((cat) => { S.cat = cat; render(); }).catch((error) => { S.catError = error.message; page.replaceChildren(el("p", { class: "notice error", text: error.message })); });
    return page;
  }
  return S.editing && currentArticle() ? stepEdit(page) : stepPick(page);
}

function stepPick(page) {
  // 조를 누르면 고르기만 하고, 「다음 단계로 이동」을 눌러야 수정 화면으로 간다(다른 조로 바꿔 고르기 쉽게)
  const bar = actionBar({
    back: { label: "이전 단계", onclick: () => go("analyze/1") },
    next: { label: "다음 단계로 이동", disabled: !currentArticle(), onclick: () => { S.editing = true; render(); } },
    hint: currentArticle() ? "" : "수정할 조항을 선택해 주세요.",
  });
  const buttons = [];
  let chapter = "";
  const rows = articles().flatMap((a) => {
    const button = el("button", { type: "button", class: "art-row", "aria-pressed": String(S.articleId === a.id), onclick: () => {
      if (S.articleId !== a.id) { S.articleId = a.id; S.edited = a.text; S.result = null; S.analysisError = ""; S.review = {}; }   // 다른 조를 고르면 쓰던 수정안은 버린다
      for (const other of buttons) other.setAttribute("aria-pressed", String(other === button));
      bar.next.disabled = false;
      bar.hint.textContent = "";
    } }, el("span", { class: "body" }, el("strong", { text: a.label + (a.title ? ` (${a.title})` : "") }), el("p", { text: bodyText(a) })));
    buttons.push(button);
    const head = a.chapter && a.chapter !== chapter ? [el("li", { class: "chapter-head", text: a.chapter })] : [];
    chapter = a.chapter || chapter;
    return [...head, el("li", null, button)];
  });
  page.append(progress(2), ...pageHead("수정할 조항을 선택해주세요", `${S.cat.name}에서 개정할 조항을 선택하세요.`),
    el("div", { class: "doc-head" }, el("span", { class: "doc-ico" }, icon("doc")), el("div", null, el("h2", { text: S.cat.name }), el("p", { class: "muted", text: `조 ${buttons.length}개` }))),
    rows.length ? el("ul", { class: "art-list" }, rows) : el("p", { class: "notice", text: "편집할 본문 조항이 없습니다." }), bar.node);
  queueMicrotask(() => buttons.find((x) => x.getAttribute("aria-pressed") === "true")?.scrollIntoView({ block: "nearest" }));
  return page;
}

function stepEdit(page) {
  const a = currentArticle();
  const textarea = el("textarea", { "aria-label": "수정안", rows: 8 });
  textarea.value = S.edited;
  const side = el("aside", { class: "side-panel" });
  const bar = actionBar({
    back: { label: "이전 단계", onclick: () => { S.editing = false; render(); } },
    next: { label: "수정안 비교하기", disabled: !isChanged(), onclick: () => go("analyze/3") },
  });
  function drawSide() {
    const list = isChanged() ? changeList(diff(a.text, S.edited)) : [];
    side.replaceChildren(el("h2", null, "수정 요약", list.length > 0 && el("span", { class: "chip blue", text: `${list.length}곳` })));
    side.append(list.length ? el("div", { class: "chg-list" }, list.map(changeCard)) : el("p", { class: "muted", text: "수정하면 바뀐 부분이 여기에 표시됩니다." }));
  }
  textarea.addEventListener("input", () => { S.edited = textarea.value; drawSide(); bar.next.disabled = !isChanged(); });
  drawSide();
  page.append(progress(2), ...pageHead("규정을 수정해 볼까요?", "기존 내용을 확인하고 변경할 부분을 편집해 주세요."),
    el("div", { class: "edit-layout" },
      el("div", null,
        el("div", { class: "crumb" }, S.cat.name, a.chapter && [icon("chevron"), a.chapter], icon("chevron"), a.label),
        el("h2", { class: "edit-title", text: articleName(a) }),
        el("div", { class: "box" }, el("span", { class: "chip", text: "기존 내용" }), el("div", { text: a.text })),
        el("div", { class: "editor-box" }, el("h3", { text: "수정안" }), textarea),
        el("p", { class: "hint-line", text: "수정한 내용은 자동으로 비교됩니다." })),
      side), bar.node);
  return page;
}

/* ---------- 영향 분석 3단계: 수정 전·후 비교 ---------- */
function pageStep3() {
  const a = currentArticle();
  const ops = diff(a.text, S.edited);
  const bar = actionBar({
    back: { label: "이전 단계", onclick: () => go("analyze/2") },
    next: { label: "영향 분석하기", onclick: runAnalysis },
  });
  return el("section", { class: "page" }, progress(3), ...pageHead("수정 내용을 확인해 주세요", "기존 규정과 개정안의 변경된 내용을 비교해볼 수 있어요."),
    el("div", { class: "compare" },
      el("section", { class: "panel" }, el("span", { class: "chip", text: "기존 규정" }), el("h3", { class: "doc-t", text: articleName(a) }), renderSide(ops, "old")),
      el("section", { class: "panel" }, el("span", { class: "chip blue", text: "개정안" }), el("h3", { class: "doc-t", text: articleName(a) }), renderSide(ops, "new"))),
    bar.node);
}

async function runAnalysis() {
  const a = currentArticle();
  S.analyzing = true; S.result = null; S.analysisError = ""; S.review = {}; S.started = Date.now();
  go("analyze/4");
  try {
    S.result = await api("/api/edit/review", { role: "editor", reg_id: S.regId, article_id: a.id, version: a.version, edited: S.edited });
  } catch (error) {
    S.analysisError = error.name === "TimeoutError" ? `${REVIEW_TIMEOUT_MS / 1000}초 안에 응답을 받지 못했습니다. 서버의 처리 완료 여부를 확인할 수 없습니다.` : error.message;
  } finally {
    S.analyzing = false;
  }
  if (currentRoute() === "analyze/4") render();
}

/* ---------- 영향 분석 4단계: 영향 확인 및 검토 ---------- */
function pageStep4() {
  const title = ["영향받는 조항을 검토해주세요", "분석 결과를 확인하고, 조항별 검토 여부를 표시할 수 있어요."];
  if (S.analyzing) {
    const text = el("p", { text: "관련 조항과 근거를 검토하고 있습니다…" });
    const tick = () => { text.textContent = `관련 조항과 근거를 검토하고 있습니다… ${Math.floor((Date.now() - S.started) / 1000)}초`; };
    tick();
    S.timer = setInterval(tick, 1000);
    return el("section", { class: "page" }, progress(4), ...pageHead(...title), el("div", { class: "panel loading", role: "status" }, el("div", { class: "spinner" }), text,
      el("p", { class: "muted", text: "판단 모델과 Luna를 거치므로 1~2분 걸릴 수 있어요." })));
  }
  if (S.analysisError) {
    return el("section", { class: "page" }, progress(4), ...pageHead(...title), el("p", { class: "notice error", role: "alert", text: S.analysisError }),
      actionBar({ back: { label: "이전 단계", onclick: () => { S.analysisError = ""; go("analyze/3"); } }, next: { label: "다시 분석하기", onclick: runAnalysis } }).node);
  }
  const result = S.result;
  const first = Number.isInteger(result.first_n) ? result.first_n : result.candidates.length;
  const rest = result.candidates.length - first;
  const footer = el("span", { class: "hint" });
  const bar = actionBar({ back: { label: "이전 단계", onclick: () => go("analyze/3") } });
  const save = el("button", { type: "button", class: "btn primary", onclick: () => saveReview(save, result) }, icon("check"), "검토 저장");
  bar.node.replaceChildren(bar.node.firstChild, footer, save);
  const drawCount = () => {
    const done = result.candidates.slice(0, first).filter((c) => S.review[c.id]?.done).length;   // 첫 화면 후보만 센다(「더 보기」는 덜 중요한 후보)
    footer.textContent = first ? `총 ${first}건 중 ${done}건 검토 완료` : "검토할 영향 후보가 없습니다.";
  };
  drawCount();

  // 「상위 3개 후보에만 검토 문장을 생성」은 정상 동작 안내라서 경고로 띄우지 않는다
  const details = (result.notices || []).filter((text) => !/후보에만 검토 문장을 생성/.test(text));
  if (result.candidates.length && !result.options["Luna 리랭킹"]) details.push("관련 조항을 검색 순서로 표시했습니다.");
  const notice = details.length > 0 && el("div", { class: "notice" },
    el("p", { text: "일부 분석 단계를 쓰지 못해 후보가 많거나 순서가 정확하지 않을 수 있어요. 영향 여부는 원문을 확인해 주세요." }),
    el("details", null, el("summary", { text: "자세히 보기" }), details.map((text) => el("p", { text }))));

  const list = el("div", { class: "cand-list" });
  result.candidates.slice(0, first).forEach((c, index) => list.append(candidate(result, c, index, drawCount)));
  const more = rest > 0 && el("details", { class: "more" }, el("summary", { text: `더 보기 (${rest}개)` }));
  if (more) result.candidates.slice(first).forEach((c, offset) => more.append(candidate(result, c, first + offset, drawCount)));

  const side = el("aside", { class: "panel side" });
  side.append(el("h2", { text: "수정안 미리보기" }), previewPanel());

  return el("section", { class: "page" }, progress(4), ...pageHead(...title),
    notice,
    el("div", { class: "review" },
      el("section", { class: "panel" }, el("h2", null, "영향받는 조항 목록", el("small", { text: `(총 ${first}건)` })),
        result.candidates.length ? [list, more] : el("p", { class: "muted", style: "margin-top:14px", text: "검토할 영향 후보가 없습니다." })),
      side), bar.node);
}

// 조문이 길면 근거 문구 주변만 남긴다(저장 크기를 줄이면서 「이 조에는」 문장은 그대로 찾을 수 있게)
function clipAround(text, quote, size = 3000) {
  if (text.length <= size) return text;
  const at = quote ? text.indexOf(quote.trim().split(/\s+/)[0]) : -1;
  const from = Math.max(0, Math.min(at < 0 ? 0 : at - 1000, text.length - size));
  return text.slice(from, from + size);
}

async function saveReview(button, result) {
  const items = result.candidates.map((c) => ({ id: c.id, regulation: c.regulation, label: c.label, title: c.title,
    reason: c.answer || c.evidence?.reason || "", done: Boolean(S.review[c.id]?.done), note: S.review[c.id]?.note || "",
    badge: c.evidence?.relation || (c.paths && c.paths[0]) || c.group || "", quote: c.evidence?.quote || "", text: clipAround(c.text || "", c.evidence?.quote) }));
  const label = button.lastChild;
  button.disabled = true;
  label.textContent = "저장 중…";
  try {
    const a = currentArticle();
    await api("/api/edit/save", { role: "editor", id: result.id, regulation: result.regulation, article: result.article,
      before: a.text, after: S.edited, items });
    label.textContent = "저장했어요";
    toast("검토를 저장했어요. 검토 내역에서 볼 수 있어요.", () => go(`history/${result.id}`), "검토 내역 보기");
    setTimeout(() => { button.disabled = false; label.textContent = "다시 저장"; }, 1500);
  } catch (error) {
    label.textContent = "검토 저장";
    button.disabled = false;
    toast(error.message, null, "", true);
  }
}

function toast(message, onclick, action, error = false) {
  document.querySelector(".toast")?.remove();
  const node = el("div", { class: "toast" + (error ? " error" : ""), role: "status" }, el("span", { text: message }),
    action && el("button", { type: "button", text: action, onclick: () => { node.remove(); onclick?.(); } }));
  document.body.append(node);
  // 저장 알림은 짧게, 오류는 읽을 시간을 더 준다. 사라질 때는 서서히 흐려진다.
  setTimeout(() => { node.classList.add("out"); setTimeout(() => node.remove(), 550); }, error ? 4000 : 1600);
}

/* ---------- 검토 내역 ---------- */
const when = (iso) => {
  const d = new Date(iso);
  const two = (n) => String(n).padStart(2, "0");
  return Number.isNaN(+d) ? "" : `${d.getFullYear()}.${two(d.getMonth() + 1)}.${two(d.getDate())} ${two(d.getHours())}:${two(d.getMinutes())}`;
};

function pageHistory() {
  const list = el("div", { class: "history-list" }, el("p", { class: "muted", text: "불러오는 중입니다…" }));
  api("/api/edit/history").then((data) => {
    list.replaceChildren();
    if (!data.items.length) return list.append(emptyPreview("아직 저장한 검토가 없어요.\n영향 분석에서 검토를 저장하면 여기에 모여요."));
    for (const r of data.items) {
      list.append(el("button", { type: "button", class: "history-row", onclick: () => go(`history/${r.id}`) },
        el("span", { class: "body" }, el("strong", { text: `${r.regulation} ${r.article}` }), el("p", { text: `${when(r.saved_at)} 저장` })),
        el("span", { class: "chip " + (r.total && r.done === r.total ? "blue" : ""), text: `${r.total}건 중 ${r.done}건 검토 완료` }),
        el("span", { class: "chev" }, icon("chevron"))));
    }
  }).catch((error) => { list.replaceChildren(el("p", { class: "notice error", text: error.message })); });
  return el("section", { class: "page" }, ...pageHead("검토 내역", "저장한 영향 검토를 다시 볼 수 있어요."), list);
}

function confirmDialog(message, okLabel) {
  return new Promise((resolve) => {
    const dialog = el("dialog", { class: "confirm" }, el("p", { text: message }),
      el("div", { class: "row" },
        el("button", { type: "button", class: "btn", onclick: () => dialog.close("no") }, "취소"),
        el("button", { type: "button", class: "btn danger", onclick: () => dialog.close("ok") }, okLabel)));
    dialog.addEventListener("close", () => { resolve(dialog.returnValue === "ok"); dialog.remove(); });
    document.body.append(dialog);
    dialog.showModal();
  });
}

function pageHistoryDetail(id) {
  const page = el("section", { class: "page" }, el("p", { class: "muted", text: "불러오는 중입니다…" }));
  api("/api/edit/history?" + new URLSearchParams({ id })).then((r) => {
    const store = {};   // 이 화면의 체크 · 의견(단계 4의 임시 상태와 따로 둔다)
    r.items.forEach((it, i) => { store[it.id] = { done: it.done, note: it.note || "", open: i === 0 }; });
    let dirty = false;
    const sub = el("p", { class: "page-sub" });
    const drawSub = () => { sub.textContent = `${when(r.saved_at)} 저장 · ${r.items.length}건 중 ${r.items.filter((it) => store[it.id].done).length}건 검토 완료`; };
    drawSub();
    const save = el("button", { type: "button", class: "btn primary", disabled: true }, icon("check"), "변경 저장");
    const onChange = () => { dirty = true; save.disabled = false; drawSub(); };

    // 저장해 둔 항목을 단계 4와 같은 모양의 후보로 되돌려 같은 카드를 쓴다
    const asCandidate = (it) => ({ id: it.id, regulation: it.regulation, label: it.label, title: it.title, text: it.text || "", answer: it.reason, group: "", paths: [],
      evidence: { quote: it.quote || "", relation: it.badge || "" } });
    const result = { regulation: r.regulation, article: r.article };
    const cards = r.items.map((it, i) => candidate(result, asCandidate(it), i, onChange, store));

    const remove = el("button", { type: "button", class: "btn danger-ghost", onclick: async () => {
      if (!await confirmDialog("이 검토를 삭제할까요? 삭제하면 되돌릴 수 없어요.", "삭제")) return;
      try {
        await api("/api/edit/delete", { role: "editor", id });
        go("history");
        toast("검토를 삭제했어요.");
      } catch (error) { toast(error.message, null, "", true); }
    } }, "삭제");

    save.addEventListener("click", async () => {
      save.disabled = true;
      try {
        const items = r.items.map((it) => ({ ...it, done: store[it.id].done, note: store[it.id].note }));
        const saved = await api("/api/edit/save", { role: "editor", id, regulation: r.regulation, article: r.article, before: r.before, after: r.after, items });
        r.saved_at = saved.saved_at; dirty = false; drawSub();
        toast("수정한 내용을 저장했어요.");
      } catch (error) { save.disabled = false; toast(error.message, null, "", true); }
    });

    const bar = el("div", { class: "actionbar" }, remove, el("span"), save);
    page.replaceChildren(el("h1", { class: "page-title", text: `${r.regulation} ${r.article}` }), sub,
      el("div", { class: "review" },
        el("section", { class: "panel" }, el("h2", null, "영향받는 조항 목록", el("small", { text: `(총 ${r.items.length}건)` })), el("div", { class: "cand-list" }, cards)),
        el("aside", { class: "panel side" }, el("h2", { text: "바뀐 부분" }), el("div", { class: "chg-list" }, changeList(diff(r.before, r.after)).map(changeCard)))), bar);
    $("actions").replaceChildren(bar);
    $("actions").hidden = false;
    window.addEventListener("beforeunload", (e) => { if (dirty) e.preventDefault(); });
  }).catch((error) => { page.replaceChildren(el("p", { class: "notice error", text: error.message })); });
  return page;
}

function candidate(result, c, index, onCount, store = S.review) {
  const state = (store[c.id] ||= { done: false, note: "", open: index === 0 });
  const evidence = c.evidence || {};
  const reason = c.answer || evidence.reason || "";
  const badge = evidence.relation || (c.paths && c.paths[0]) || c.group;
  const name = [c.regulation, c.label, c.title && `(${c.title})`].filter(Boolean).join(" ");
  const box = el("article", { class: "cand" });
  const body = el("div", { class: "cand-body" });
  function draw() {
    box.classList.toggle("open", state.open);
    body.hidden = !state.open;
    toggle.setAttribute("aria-expanded", String(state.open));
  }
  const check = el("input", { type: "checkbox", "aria-label": `${name} 검토 완료`, onchange: (e) => { state.done = e.target.checked; onCount(); } });
  check.checked = state.done;
  const toggle = el("button", { type: "button", class: "cand-main", onclick: () => { state.open = !state.open; draw(); } },
    el("span", { class: "text" }, el("strong", null, name, el("span", { class: "chip blue", text: badge, title: (c.paths || []).join(" · ") })), reason && el("p", { text: reason })),
    el("span", { class: "chev" }, icon("chevron")));
  box.append(el("div", { class: "cand-head" }, check, toggle), body);

  // 펼치면: 영향받는 조에 쓰여 있는 말(관련 문구 강조) → 확인할 점 → 검토 의견
  const found = foundSentence(c.text, evidence.quote);
  if (found) {
    body.append(el("div", { class: "found" }, el("small", { text: `${c.regulation} ${c.label}에는` }),
      el("p", null, found.before, el("mark", { class: "add", text: found.match }), found.after)));
  } else if (evidence.quote) {
    body.append(el("div", { class: "found" }, el("small", { text: `${c.regulation} ${c.label}에는` }), el("p", { text: evidence.quote })));
  }
  if (reason) body.append(el("div", { class: "reason" }, el("strong", { text: "확인할 점" }), el("p", { text: reason })));
  if (evidence.replacement) body.append(el("p", { class: "muted", text: `참고 수정안 · ${evidence.replacement.before} → ${evidence.replacement.after}` }));
  if (c.text) body.append(originalExcerpt(c.text, evidence.quote, index));

  const note = el("textarea", { maxlength: NOTE_MAX, placeholder: "검토 의견을 입력하세요", "aria-label": `${name} 검토 의견` });
  note.value = state.note;
  const counter = el("span", { class: "counter", text: `${state.note.length} / ${NOTE_MAX}` });
  note.addEventListener("input", () => { state.note = note.value; counter.textContent = `${note.value.length} / ${NOTE_MAX}`; });
  body.append(el("label", { class: "note" }, el("span", null, "검토 의견 ", el("small", { text: "(선택 사항)" })), note, counter));
  draw();
  return box;
}

// 조문에서 관련 문구가 든 줄만 뽑는다(길면 앞뒤를 줄인다). 문구를 못 찾으면 null.
function foundSentence(text, quote) {
  const terms = quote?.trim().split(/\s+/).filter(Boolean).map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")) ?? [];
  const m = terms.length && text ? new RegExp(terms.join("\\s+"), "u").exec(text) : null;
  if (!m) return null;
  const start = text.lastIndexOf("\n", m.index) + 1;
  const endAt = text.indexOf("\n", m.index + m[0].length);
  const end = endAt < 0 ? text.length : endAt;
  let before = text.slice(start, m.index), after = text.slice(m.index + m[0].length, end);
  if (before.length > 90) before = "… " + before.slice(-90);
  if (after.length > 90) after = after.slice(0, 90) + " …";
  return { before, match: m[0], after };
}

function originalExcerpt(text, quote, index) {
  const pre = el("pre", { class: "original", id: `original-${index}`, hidden: true });
  const terms = quote?.trim().split(/\s+/).filter(Boolean).map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")) ?? [];
  const match = terms.length ? new RegExp(terms.join("\\s+"), "u").exec(text) : null;
  if (match) pre.append(text.slice(0, match.index), el("mark", { class: "add", text: match[0] }), text.slice(match.index + match[0].length));
  else pre.textContent = text;
  const button = el("button", { type: "button", class: "original-btn", "aria-expanded": "false", "aria-controls": pre.id, onclick: () => {
    pre.hidden = !pre.hidden;
    button.setAttribute("aria-expanded", String(!pre.hidden));
    button.textContent = pre.hidden ? "조항 전문 보기" : "조항 전문 접기";
  } }, "조항 전문 보기");
  return el("div", null, button, pre);
}

function previewPanel() {
  const a = currentArticle();
  const ops = diff(a.text, S.edited);
  return el("div", { class: "preview-doc" }, el("span", { class: "muted", text: S.cat.name }), el("h3", { text: articleName(a) }), renderSide(ops, "new"));
}

/* ---------- 시작 ---------- */
async function init() {
  try {
    S.regs = (await api("/api/edit/catalog")).regulations;
    if (!S.regs.length) S.regsError = "편집할 규정이 없습니다.";
  } catch (error) {
    S.regsError = error.message;
  }
  // 예전 주소(?reg=&article=)도 받는다
  const reg = S.regs.find((r) => r.id === params.get("reg"));
  if (reg && !location.hash) {
    S.regId = reg.id; S.regName = reg.name;
    try {
      S.cat = await loadCatalog(reg.id);
      const a = articles().find((x) => x.id === params.get("article"));
      if (a) { S.articleId = a.id; S.edited = a.text; S.editing = true; }
    } catch (error) { S.catError = error.message; }
    return go("analyze/2");
  }
  render();
}

init();
