"use strict";

const $ = (id) => document.getElementById(id);
const reg = $("regulation");
const article = $("article");
const edited = $("edited");
const submit = $("submit");
const params = new URLSearchParams(location.search);
let articles = new Map();
let loading = false;
let saving = false;
let requestNumber = 0;
let regulations = [];
const reader = $("reader");
const home = $("home");
const workspace = $("workspace");
const editor = $("editor");
const results = $("results");
const resultDialog = $("result-dialog");
const resultField = (id) => results.querySelector("#" + id);

function element(tag, text) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  return node;
}

function status(message, error = false) {
  $("status").textContent = message;
  $("status").classList.toggle("error", error);
}

async function api(path, body) {
  const options = body ? {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  } : {};
  const response = await fetch(path, { ...options, signal: AbortSignal.timeout(body ? 70000 : 12000) });
  const data = await response.json();
  if (!response.ok || data.error) throw new Error(data.error || "요청을 처리하지 못했습니다.");
  return data;
}

function updateControls() {
  const active = articles.get(article.value);
  reg.disabled = saving || reg.options.length < 2;
  article.disabled = saving || loading || articles.size === 0;
  edited.disabled = saving || !active;
  submit.disabled = saving || !active || !edited.value.trim() || edited.value.trim() === active.text.trim();
  for (const button of document.querySelectorAll("#documents button, .article-block button[data-edit]")) button.disabled = saving;
  $("home-button").disabled = saving;
}

function selectArticle() {
  const active = articles.get(article.value);
  edited.value = active?.text ?? "";
  editor.hidden = !active;
  if (active) {
    const block = $(`article-${active.id}`);
    if (block) block.after(editor);
    $("editor-heading").textContent = `${active.label} 편집`;
    $("return-to-article").href = `#article-${active.id}`;
    status("");
  }
  updateControls();
}

function renderDocuments() {
  const query = $("document-search").value.replace(/\s+/g, "").toLowerCase();
  const list = $("documents");
  list.replaceChildren();
  for (const doc of regulations) {
    if (!doc.name.replace(/\s+/g, "").toLowerCase().includes(query)) continue;
    const button = element("button", doc.name);
    button.type = "button";
    button.disabled = saving;
    button.setAttribute("aria-pressed", String(reg.value === doc.id));
    button.addEventListener("click", () => {
      if (reg.value === doc.id) return;
      reg.value = doc.id;
      const url = new URL(location.href);
      url.searchParams.set("reg", doc.id);
      url.searchParams.delete("article");
      url.hash = "";
      history.replaceState(null, "", url);
      renderDocuments();
      loadArticles();
    });
    list.append(button);
  }
  if (!list.childElementCount) list.append(element("p", "표시할 규정이 없습니다."));
}

function renderDocument(data) {
  $("document-title").textContent = data.name;
  const body = $("document-body");
  body.replaceChildren();
  if (data.preamble.length) {
    const intro = element("p", data.preamble.join("\n"));
    intro.className = "doc-text";
    body.append(intro);
  }
  for (const a of data.articles) {
    const section = element("section");
    section.className = "article-block";
    section.id = `article-${a.id}`;
    const heading = element("header");
    heading.append(element("h3", [a.label, a.title].filter(Boolean).join(" ")));
    if (!a.deleted) {
      const button = element("button", "편집");
      button.type = "button";
      button.dataset.edit = a.id;
      button.setAttribute("aria-label", `${a.label} 편집`);
      button.addEventListener("click", () => {
        article.value = a.id;
        selectArticle();
        edited.focus();
      });
      heading.append(button);
    }
    const lines = a.text.split("\n");
    const first = lines[0].replace(/^\s*제\s*\d+\s*(?:-\s*\d+\s*)?조(?:\s*의\s*\d+|\d+(?=\s*[(（]))?\s*/, "");
    if (a.title && (first.startsWith(`(${a.title})`) || first.startsWith(`（${a.title}）`))) {
      lines[0] = first.slice(a.title.length + 2).trimStart();
    } else {
      lines[0] = first;
    }
    section.append(heading, element("p", lines.join("\n").trimStart()));
    body.append(section);
  }
  for (const item of data.sections) {
    const section = element("section");
    section.className = "article-block";
    section.append(element("h3", item.label), element("p", item.text));
    body.append(section);
  }
}

async function loadArticles(preselected = "") {
  const request = ++requestNumber;
  articles = new Map();
  article.replaceChildren(new Option("조항을 선택하세요", ""));
  loading = Boolean(reg.value);
  selectArticle();
  reader.after(editor);
  $("document-body").replaceChildren();
  if (!loading) {
    $("document-title").textContent = "규정 문서를 선택하세요";
    status("");
    return;
  }
  $("document-title").textContent = "규정 문서를 불러오는 중입니다…";
  try {
    const data = await api("/api/edit/catalog?" + new URLSearchParams({ reg: reg.value }));
    if (request !== requestNumber) return;
    renderDocument(data);
    articles = new Map(data.articles.filter((a) => !a.deleted).map((a) => [a.id, a]));
    for (const a of articles.values()) article.add(new Option([a.label, a.title].filter(Boolean).join(" "), a.id));
    if (articles.has(preselected)) article.value = preselected;
    status(articles.size ? "" : "편집할 본문 조항이 없습니다.");
  } catch (error) {
    if (request === requestNumber) $("document-title").textContent = error.message;
  } finally {
    if (request === requestNumber) {
      loading = false;
      selectArticle();
    }
  }
}

function comparison(result) {
  const table = element("table");
  const heading = table.createTHead().insertRow();
  for (const name of ["현행", "개정안"]) {
    const th = element("th", name);
    th.scope = "col";
    heading.append(th);
  }
  const body = table.createTBody();
  for (const row of result.rows) {
    const tr = body.insertRow();
    for (const side of ["old", "new"]) {
      const cell = tr.insertCell();
      for (const line of row[side]) {
        const div = element("div");
        for (const [text, changed] of line) div.append(changed ? element("mark", text) : document.createTextNode(text));
        cell.append(div);
      }
    }
  }
  const amendment = element("details");
  amendment.append(element("summary", "자동 생성한 개정문"), element("pre", result.amendment));
  resultField("comparison").replaceChildren(table, amendment);
}

function changePreview(rows) {
  const preview = element("section");
  preview.className = "change-preview";
  preview.append(element("h3", "바뀐 문구"));
  const changes = element("div");
  changes.className = "change-pair";
  let more = false;
  for (const [side, label] of [["old", "현행"], ["new", "개정안"]]) {
    const column = element("div");
    column.className = "change-" + side;
    column.append(element("strong", label));
    const lines = rows.flatMap((row) => row[side]).filter((line) => line.some(([, changed]) => changed));
    more ||= lines.length > 3;
    for (const line of lines.slice(0, 3)) {
      const text = element("p");
      for (const [part, changed] of line) text.append(changed ? element("mark", part) : document.createTextNode(part));
      column.append(text);
    }
    if (!lines.length) column.append(element("p", "—"));
    changes.append(column);
  }
  preview.append(changes);
  if (more) {
    preview.append(element("small", "나머지 변경 문구는 신·구조문대비표에서 확인할 수 있습니다."));
  }
  return preview;
}

function evidenceBlock(label, title, quote) {
  const block = element("div");
  block.className = "evidence-block";
  block.append(element("small", label), element("strong", title));
  if (quote) block.append(element("blockquote", quote));
  return block;
}

function originalExcerpt(text, quote) {
  const original = element("details");
  original.className = "candidate-original";
  original.append(element("summary", "후보 조항 전문 보기"));
  const excerpt = element("pre");
  const terms = quote?.trim().split(/\s+/).filter(Boolean) ?? [];
  const escaped = terms.map((term) => term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const match = escaped.length ? new RegExp(escaped.join("\\s+"), "u").exec(text) : null;
  if (match) excerpt.append(text.slice(0, match.index), element("mark", match[0]), text.slice(match.index + match[0].length));
  else excerpt.textContent = text;
  original.append(excerpt);
  return original;
}

function impactList(result) {
  const section = resultField("impacts");
  const total = result.rule.length + result.other.length;
  section.replaceChildren(changePreview(result.rows), element("p", `검토 후보 ${total}개 · 선별에서 제외 ${result.hidden}개`));
  if (!total) section.append(element("p", "검토할 영향 후보가 없습니다."));
  for (const [name, items] of [["규칙 후보", result.rule], ["의미 검색 · 도메인 그래프 후보", result.other]]) {
    if (!items.length) continue;
    section.append(element("h3", `${name} (${items.length})`));
    for (const [index, item] of items.entries()) {
      const details = element("details");
      details.className = "candidate";
      details.open = index === 0;
      const title = [item.regulation, item.label, item.title].filter(Boolean).join(" ");
      const summary = element("summary");
      const tier = element("span", item.tier);
      tier.className = "impact-tier";
      summary.append(element("span", title), tier);
      details.append(summary);
      const evidence = item.evidence;
      const body = element("div");
      body.className = "candidate-body";
      const reason = item.answer || evidence.reason;
      if (reason) {
        const why = element("div");
        why.className = "impact-reason";
        why.append(element("strong", "왜 확인해야 하나요?"), element("p", reason));
        body.append(why);
      }
      const connection = element("div");
      connection.className = "impact-connection";
      const arrow = element("span", "→");
      arrow.setAttribute("aria-hidden", "true");
      connection.append(evidenceBlock("수정한 조항", `${result.regulation} ${result.article}`, evidence.source_quote),
        arrow, evidenceBlock("함께 확인할 조항", title, evidence.quote));
      body.append(connection);
      if (evidence.replacement) {
        body.append(element("p", `참고 수정안 · ${evidence.replacement.before} → ${evidence.replacement.after}`));
      }
      const paths = element("ul");
      paths.className = "impact-paths";
      paths.setAttribute("aria-label", "발견 경로");
      for (const path of item.paths) paths.append(element("li", path));
      if (paths.childElementCount) body.append(paths);
      if (item.text) body.append(originalExcerpt(item.text, evidence.quote));
      details.append(body);
      section.append(details);
    }
  }
}

function showPanel(id) {
  for (const button of results.querySelectorAll("[data-panel]")) {
    const active = button.dataset.panel === id;
    button.setAttribute("aria-pressed", String(active));
    resultField(button.dataset.panel).hidden = !active;
  }
}

for (const button of results.querySelectorAll("[data-panel]")) {
  button.addEventListener("click", () => showPanel(button.dataset.panel));
}

function openResults() {
  resultField("result-heading").textContent = "영향 분석";
  resultField("result-meta").textContent = "";
  resultField("analysis-notices").hidden = true;
  resultField("result-switch").hidden = true;
  resultField("impacts").hidden = resultField("comparison").hidden = true;
  resultField("result-status").classList.remove("error");
  resultField("result-status").textContent = "관련 조항을 찾고 있습니다…";
  if (!resultDialog.open) resultDialog.showModal();
}

resultField("close-result").addEventListener("click", () => {
  resultDialog.close();
});

reg.addEventListener("change", () => loadArticles());
article.addEventListener("change", () => { selectArticle(); status(""); });
$("document-search").addEventListener("input", renderDocuments);
function showWorkspace(open) {
  home.hidden = open;
  workspace.hidden = !open;
  window.scrollTo(0, 0);
}

$("start-review").addEventListener("click", () => {
  showWorkspace(true);
  $("document-search").focus();
});
$("home-button").addEventListener("click", () => {
  reg.value = "";
  const url = new URL(location.href);
  url.searchParams.delete("reg");
  url.searchParams.delete("article");
  url.hash = "";
  history.replaceState(null, "", url);
  renderDocuments();
  loadArticles();
  showWorkspace(false);
  $("start-review").focus();
});
edited.addEventListener("input", updateControls);
$("editor").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (submit.disabled) return;
  const active = articles.get(article.value);
  saving = true;
  updateControls();
  openResults();
  status("영향 분석을 진행 중입니다…");
  const started = Date.now();
  const timer = setInterval(() => {
    resultField("result-status").textContent = `관련 조항과 근거를 검토하고 있습니다… ${Math.floor((Date.now() - started) / 1000)}초`;
  }, 1000);
  try {
    const result = await api("/api/edit/review", {
      role: "editor", reg_id: reg.value, article_id: active.id, version: active.version,
      edited: edited.value,
    });
    resultField("result-heading").textContent = `${result.regulation} ${result.article} 검토 결과`;
    const used = Object.entries(result.options).filter(([, on]) => on).map(([name]) => name);
    resultField("result-meta").textContent = [result.kind, result.change_evidence, "규칙 그래프", ...used].filter(Boolean).join(" · ");
    const notices = resultField("analysis-notices");
    notices.replaceChildren(...(result.notices ?? []).map((message) => element("li", message)));
    notices.hidden = !notices.childElementCount;
    comparison(result);
    impactList(result);
    showPanel("impacts");
    resultField("result-switch").hidden = false;
    resultField("result-status").textContent = "검토 초안을 저장했습니다.";
    results.focus();
    status("검토 초안을 저장했습니다. 결과 창에서 확인하세요.");
  } catch (error) {
    const message = error.name === "TimeoutError" ? "70초 안에 응답을 받지 못했습니다. 서버의 처리 완료 여부를 확인할 수 없습니다." : error.message;
    resultField("result-status").classList.add("error");
    resultField("result-status").textContent = message;
    status(message, true);
  } finally {
    clearInterval(timer);
    saving = false;
    updateControls();
  }
});

async function init() {
  reg.add(new Option("규정을 선택하세요", ""));
  article.add(new Option("조항을 선택하세요", ""));
  $("home-status").textContent = "규정 목록을 불러오는 중입니다…";
  try {
    const data = await api("/api/edit/catalog");
    regulations = data.regulations;
    for (const r of regulations) reg.add(new Option(r.name, r.id));
    updateControls();
    $("home-status").textContent = regulations.length ? "" : "편집할 규정이 없습니다.";
    if (regulations.some((r) => r.id === params.get("reg"))) reg.value = params.get("reg");
    renderDocuments();
    if (reg.value) {
      showWorkspace(true);
      await loadArticles(params.get("article"));
    }
  } catch (error) {
    $("home-status").textContent = error.message;
  }
}

init();
