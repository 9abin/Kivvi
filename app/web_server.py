"""규정 도우미 조항 수정 화면(app/web)과 읽기 전용 API를 함께 내보내는 작은 서버.

  uv run --extra db python app/web_server.py            # http://127.0.0.1:8765
  uv run --extra db python app/web_server.py --port 9000

DB 읽기: 공유 DB(reg-db)에 팀 읽기 전용 계정(.env.team의 TEAM_*)으로 접속한다. 없으면 .env의 PG_*를 읽기 전용 세션으로 쓴다.
DB 쓰기: app/web_agent.py만 (editor 역할). 규정 원문(public)은 바꾸지 않는다.
API (GET, JSON)
  /api/status                      접속 확인, 청크·개정안 수
  /api/find?q=인사규정 제30조 2항   규정명 + 조(·항) → chunk_id
  /api/article?chunk=<chunk_id>    근거 원문: 규정·조 제목·항 전체·강조할 항
  /api/amend?chunk=<chunk_id>      개정 정보: 진행 중인 개정안, 인용하는 곳, 점검 결과, 인용하는 법령, 제목이 같은 법령 조문
  /api/changesets                  개정안 목록
  /api/changeset?id=S01            개정문, 대비표, 함께 개정안·부칙·질의(패키지), 영향과 결정 상태
  /api/regulation?reg=24024        규정 전문 · /api/chat/status 답변 모델 상태 · /api/package?id= 패키지 내려받기
POST (JSON): /api/ask {q} 채팅(로컬 9B) · /api/draft {chunk, request} 개정안 작성 · /api/decide {id, key, state} · /api/submit {id}
             /api/approve·reject·publish {id} 승인 흐름 · /api/feedback · /api/scan/start {law, rebase} 변경 감시 탐색
GET /api/scan/snapshots 반입된 법률 DB 판 · /api/scan?id= 탐색 결과(새로 생김·해결됨) · /api/lawname 법령명 일괄 정비 초안 · /api/procedure?id= 개정 절차 안내
POST는 모두 role(employee·editor·approver)을 보낸다. 승인·반려는 승인권자만(기안자 본인은 불가), 공포(등록·공지)는 담당자
POST /api/lawname/create {regs} 고른 규정마다 법령명 정비 개정안(초안)  GET /api/final?id= 최종안 미리보기 · /api/package?id=&final=1
쓰기는 app/web_agent.py가 editor 역할로 (개정안·영향·결정 기록만)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from functools import lru_cache
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "app" / "web"
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "app"))

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

from regcheck.change_check import basis_of  # noqa: E402
from regcheck.db import load_env  # noqa: E402

IMPACT_LABEL = {"breaks": ("break", "깨짐"), "resolved": ("resolved", "충돌 해소"), "law_check": ("law", "법령 대조"),
                "review": ("review", "확인 필요"), "undeterminable": ("review", "판단 불가"), "info": ("topic", "참고")}
# 법령마다 흔한 조 제목 — 같은 제목이라도 내용이 관계없어 「제목이 같은 법령 조문」에서 뺀다
GENERIC_TITLES = {t.replace(" ", "") for t in ("목적", "정의", "용어의 정의", "용어의 뜻", "적용범위", "적용 범위", "시행일", "다른 법률과의 관계",
                                               "다른 규정과의 관계", "준용", "위임", "벌칙", "과태료", "권한의 위임", "시행세칙", "기능", "구성", "운영",
                                               "보칙", "업무", "임무", "책임", "경과조치", "기본원칙", "세부사항", "그 밖의 사항")}
STATUS = {"초안": "draft", "검토": "review", "승인": "done", "공포": "done", "기각": "change"}   # sql/amend_schema.sql CHECK


def connect():
    team = load_env(ROOT / ".env.team")
    if team.get("TEAM_USER"):
        dsn = dict(host="127.0.0.1", port=team.get("TEAM_PORT", "5432"), dbname=team["TEAM_DATABASE"],
                   user=team["TEAM_USER"], password=team["TEAM_PASSWORD"])
    else:
        e = load_env()
        dsn = dict(host=e.get("PG_HOST", "127.0.0.1"), port=e.get("PG_PORT", "5432"), dbname=e["PG_DATABASE"],
                   user=e["PG_USER"], password=e["PG_PASSWORD"])
    con = psycopg.connect(**dsn, gssencmode="disable", autocommit=True, connect_timeout=5)
    con.execute("SET default_transaction_read_only = on")
    return con


def q(sql: str, *args) -> list[dict]:
    with connect() as con, con.cursor(row_factory=dict_row) as cur:
        return cur.execute(sql, args).fetchall()


@lru_cache(maxsize=1)
def lawdb():
    try:
        from regcheck.lawdb import LawDB
        db = LawDB()
        _ = db.laws  # 여기서 다 읽어 둔다. SQLite 연결은 만든 스레드에서만 쓸 수 있어서, 요청 스레드가 연결을 다시 쓰지 않게
        return db
    except Exception:  # 법률 DB 파일이 없으면 법령 조문 대조만 빠진다
        return None


def reg_titles() -> dict[str, str]:
    return {r["doc_id"]: r["title"] for r in q("select doc_id, title from public.documents")}


def status() -> dict:
    """접속 확인 + 가빈 청크와 우리 항의 대응 상태. 가빈이 public.chunks를 다시 적재하거나 청크 단위·ID 규칙을 바꾸면
    「가빈 청크 사라짐」이 늘어난다 → 화면이 경고하고, scripts/amend_load.py로 대응을 다시 계산한다 (docs/db-sharing-guide.md)"""
    st = q("select (select count(*) from public.chunks) as chunks, (select count(*) from public.documents) as regs, "
           "(select count(*) from amend.changeset) as changesets, current_user as db_user")[0]
    st["unmatched"] = {r["problem"]: r["n"] for r in q("select problem, count(*) as n from amend.v_chunk_unmatched group by 1")}
    st["stale"] = st["unmatched"].get("가빈 청크 사라짐", 0)
    return st


def find(text: str) -> dict:
    docs = sorted(reg_titles().items(), key=lambda x: -len(x[1]))
    flat = text.replace(" ", "")
    hit = next(((d, t) for d, t in docs if t.replace(" ", "") in flat), None)
    guessed = False
    if not hit:   # 「법인카드 지침 제1조」처럼 이름을 대충 쓴 경우: 비슷한 이름이 하나로 좁혀지면 그 규정으로 (regcheck.nl.find_regulation, 리포트 56)
        from regcheck.nl import find_regulation
        r = find_regulation(web_agent.corpus()[0], text)
        if r:
            hit, guessed = (r["reg_id"], r["name"]), True
    m = re.search(r"제?\s*(\d+)\s*조(?:\s*의\s*(\d+))?(?:\s*제?\s*(\d+)\s*항)?", text)
    if not hit and m:   # 이름이 애매하지만(「보안지침」) 조 번호가 있으면: 이름이 비슷한 규정들의 그 조를 후보로 (조 번호를 버리고 일반 검색으로 가지 않게)
        from regcheck.nl import similar_regulations
        label = f"제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "")
        cands = []
        for score, r in similar_regulations(web_agent.corpus()[0], text, k=5):
            a = next((a for a in r["articles"] if a["label"] == label and not a.get("deleted")), None)
            rows = q("select chunk_id from amend.chunk_detail where reg_id = %s and label = %s and chunk_id is not null "
                     "group by chunk_id, hang order by min(hang) limit 1", r["reg_id"], label) if a and score >= 0.3 else []
            if rows:
                cands.append({"reg_id": r["reg_id"], "reg_name": r["name"], "label": label, "title": a["title"], "score": round(score, 3),
                              "how": "이름이 비슷한 규정의 같은 조", "chunk": rows[0]["chunk_id"]})
        if cands:
            return {"candidates": cands, "query": text, "rewritten": "", "confident": False, "index": "규정 이름"}
    if not hit or not m:
        # 규정명·조 번호가 없으면 「무엇을 바꾸고 싶은지」로 보고 고칠 후보 조문을 찾는다 (사람이 고름, regcheck.target)
        if len(text.strip()) < 4:
            return {"error": "규정명과 조 번호, 또는 바꾸고 싶은 내용을 적어 주세요 (예: 인사규정 제30조 2항 / 출장비 기준을 올리고 싶다)"}
        t = web_agent.targets(text)
        for c in t["candidates"]:
            rows = q("select chunk_id from amend.chunk_detail where reg_id = %s and label = %s and chunk_id is not null "
                     "group by chunk_id, hang order by min(hang) limit 1", c["reg_id"], c["label"])
            c["chunk"] = rows[0]["chunk_id"] if rows else None
        t["candidates"] = [c for c in t["candidates"] if c["chunk"]]
        return t
    label = f"제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "")
    rows = q("select chunk_id from amend.chunk_detail where reg_id = %s and label = %s and chunk_id is not null "
             "group by chunk_id, hang order by min(hang)", hit[0], label)
    if not rows:
        return {"error": f"{hit[1]} {label}을(를) 찾지 못했습니다"}
    want = f"_{m.group(3)}항" if m.group(3) else None
    chunk = next((r["chunk_id"] for r in rows if want and r["chunk_id"].endswith(want)), rows[0]["chunk_id"])
    return {"chunk": chunk, "reg": hit[1], "label": label, "guessed": guessed}


def article(chunk: str) -> dict:
    head = q("select d.reg_id, d.article_id, d.label, d.title, doc.title as reg, doc.revision "
             "from amend.chunk_detail d join public.documents doc on doc.doc_id = d.reg_id where d.chunk_id = %s limit 1", chunk)
    if not head:   # 별표·별지·부칙 청크는 우리 항·호·목 표에 없다 → 청크 하나를 그대로 보여 준다
        one = q("select c.chunk_id, c.doc_id, c.article, c.body, c.status, d.title, d.revision from public.chunks c "
                "join public.documents d using (doc_id) where c.chunk_id = %s", chunk)
        if not one:
            return {"error": f"청크를 찾지 못했습니다: {chunk}"}
        o = one[0]
        return {"chunk": chunk, "reg_id": o["doc_id"], "article_id": None, "reg": o["title"], "revision": o["revision"],
                "label": o["article"], "title": None, "paras": [{"chunk": chunk, "text": o["body"], "status": o["status"]}], "highlight": 0}
    h = head[0]
    paras = q("select c.chunk_id, c.body, c.status from public.chunks c join ("
              "  select chunk_id, min(item_id) k from amend.chunk_detail where article_id = %s and chunk_id is not null group by chunk_id"
              ") d using (chunk_id) order by d.k", h["article_id"])
    return {"chunk": chunk, "reg_id": h["reg_id"], "article_id": h["article_id"], "reg": h["reg"],
            "revision": h["revision"], "label": f"{h['label']}({h['title']})" if h["title"] else h["label"], "title": h["title"],
            "paras": [{"chunk": p["chunk_id"], "text": p["body"], "status": p["status"]} for p in paras],
            "highlight": next((i for i, p in enumerate(paras) if p["chunk_id"] == chunk), 0)}


def amend(chunk: str) -> dict:
    a = article(chunk)
    if "error" in a:
        return a
    aid, titles = a["article_id"] or "-", reg_titles()   # 별표·부칙 청크는 조 ID가 없음 → 인용받음·인용 법령은 비어 나온다
    chunks = [p["chunk"] for p in a["paras"]]
    open_ = q("select distinct changeset_id, status, title from amend.v_chunk_open_change where chunk_id = any(%s)", chunks)
    inbound = q("select i.src, i.src_label, i.context, i.kind, d.label, d.hang from amend.v_inbound i "
                "left join amend.chunk_detail d on d.item_id = i.src where i.target = %s or i.target like %s", aid, aid + "-%")
    findings = q("select type, quote, note, suggest from amend.finding where chunk_id = any(%s) order by type", chunks)
    laws = q("select target, min(context) as context from amend.edge where src_article = %s and target_type = 'law' group by target", aid)
    same = []
    db = lawdb()
    text = " ".join(p["text"] for p in a["paras"])
    if db and a["title"] and a["title"].replace(" ", "") not in GENERIC_TITLES:
        # 이 규정이 실제로 인용하는 법령을 앞에 (흔하지 않은 제목도 다른 법에 우연히 같을 수 있어서)
        cited = {r["d"].replace(" ", "") for r in q("select distinct regexp_replace(target, '\\s*제\\s*\\d+.*$', '') d from amend.edge "
                                                    "where src like %s and target_type = 'law'", a["reg_id"] + ":%")}
        cand = db.same_title(a["title"], limit=20)
        cand.sort(key=lambda x: x[0]["title"].replace(" ", "") not in cited)
        for law, key, u in cand[:2]:
            # 제목이 같은 조 + 내용이 가까운 조 (육아휴직: 제19조 + 분할 사용을 정한 제19조의4)
            for r in web_law.related(law, text, k=2, prefer=key):
                if any(s["law"] == law["title"] and s["key"] == r["key"] for s in same):
                    continue
                same.append({"law": law["title"], "key": r["key"], "article": f"{r['article']}({r['title']})", "text": r["text"][:220],
                             "why": "제목이 같은 조" if r["key"] == key else "내용이 가까운 조",
                             "interp": web_law.interpretations(law["id"], r["key"], 3)})
    cited_laws = []
    for l in laws:
        law, key = web_law.article_ref(db, l["target"])
        cited_laws.append({"where": l["target"], "quote": (l["context"] or "")[:90],
                           "interp": web_law.interpretations(law["id"], key, 3) if law and key else []})

    def where(r):
        reg_id = r["src"].split(":")[0]
        place = r["label"] or r["src"].split(":")[1].split("-")[0]
        return f"{titles.get(reg_id, reg_id)} {place}" + (f" 제{r['hang']}항" if r.get("hang") else "")
    return {"article": {k: a[k] for k in ("reg", "label", "chunk")},
            "open": [{"id": o["changeset_id"], "status": o["status"], "title": o["title"]} for o in open_],
            "inbound": [{"where": where(r), "quote": (r["context"] or "").strip()[:90], "kind": r["kind"]} for r in inbound],
            "findings": [{"type": f["type"].replace("_", " "), "quote": f["quote"], "note": f["note"] or f["suggest"] or ""} for f in findings],
            "laws": cited_laws,
            "sameTitleLaws": same}


def lawcheck(cid: str) -> dict:
    """개정안의 법령 대조 카드: 법령 대조 영향마다 관련 법조문(내용이 가까운 항), 숫자 비교, 해석례"""
    rows = q("select reg_id, payload from amend.changeset where id = %s", cid)
    if not rows:
        return {"error": f"개정안이 없습니다: {cid}"}
    payload = rows[0]["payload"] or {}
    from web_agent import load_package
    pkg = load_package(cid) or {}
    prow = pkg.get("rows") or payload.get("rows", [])
    plain = lambda parts: "".join(p if isinstance(p, str) else (p.get("del") or p.get("ins") or "") for p in parts)  # noqa: E731
    old, new = plain(segments(prow, "old")), plain(segments(prow, "new"))
    focus, units = web_law.focus_of(old, new)
    db, cards = lawdb(), []
    for r in q("select quote, reason from amend.impact where changeset_id = %s and category = 'law_check'", cid):
        law, key = web_law.article_ref(db, r["quote"] or "")
        if not law:
            continue
        rel = web_law.related(law, old, k=2, prefer=key or None, focus=focus, units=units)
        for x in rel:
            x["interp"] = web_law.interpretations(law["id"], x["key"], 3)
            x["why"] = "제목이 같은 조" if x["key"] == key else "바뀐 구절과 내용이 가까운 조"
        cards.append({"law": law["title"], "quoted": r["quote"], "related": rel,
                      "compare": web_law.compare([x["text"] for x in rel], old, new)})
    return {"id": cid, "cards": cards, "note": "숫자 대조는 참고용입니다. 같은 단위라도 세는 대상이 다를 수 있어, 문맥이 비슷할 때만 같아짐·다름을 표시합니다."}


def changesets() -> list[dict]:
    titles = reg_titles()
    return [{"id": r["id"], "reg": titles.get(r["reg_id"], r["reg_id"]), "title": r["title"], "status": STATUS.get(r["status"], "draft"),
             "status_label": r["status"], "origin": r["origin_chunk"]}
            for r in q("select id, reg_id, title, status, origin_chunk from amend.changeset order by created_at desc, id")]


def segments(rows: list, side: str) -> list:
    """payload rows[].old/new: [[[글, 바뀜?], ...] 줄] → 화면 조각 [str | {del}/{ins}] (생략 줄은 뺌)"""
    out = []
    for r in rows:
        for line in r.get(side) or []:
            text = "".join(t for t, _ in line)
            if text.strip() in ("(생 략)", "(현행과 같음)"):
                continue
            if out:
                out.append("\n")
            for t, changed in line:
                out.append({("del" if side == "old" else "ins"): t} if changed else t)
    return out


def readable(reason: str) -> str:
    """점검 코드가 남긴 값 목록 「현행 ['3년', '2회'] → 개정안 [...]」을 사람이 읽는 문장으로"""
    def vals(s: str) -> str:
        return " · ".join(re.findall(r"'([^']*)'", s))
    m = re.search(r"\s*/\s*바뀐 항의 값 현행 (\[[^\]]*\]) → 개정안 (\[[^\]]*\]), 상대 (\[[^\]]*\])", reason)
    if m:
        reason = reason[:m.start()] + f". 값: 현행 {vals(m.group(1))} → 개정안 {vals(m.group(2))} (상대 조문 {vals(m.group(3))})" + reason[m.end():]
    reason = reason.replace(" (법률 DB 연결 후)", "")   # 개발 단계 메모 (이미 저장된 카드에 남아 있음, 261019 change_check에서 뺌)
    return re.sub(r"\[([^\]]*)\]", lambda x: vals(x.group(0)) or x.group(0), reason)


def changeset(cid: str) -> dict:
    rows = q("select id, reg_id, title, amendment, status, payload from amend.changeset where id = %s", cid)
    if not rows:
        return {"error": f"개정안이 없습니다: {cid}"}
    c, titles = rows[0], reg_titles()
    payload = c["payload"] or {}
    imp = q("select i.review_key, i.category, i.reg_id, i.unit, i.article, i.para, i.quote, i.reason, i.handling, i.priority, i.path, "
            "coalesce(d.state, '미결정') as state from amend.impact i left join amend.v_decision_state d on d.key = i.review_key "
            "where i.changeset_id = %s order by case i.category when 'breaks' then 0 when 'law_check' then 1 when 'resolved' then 2 else 3 end, "
            "case i.priority when '높음' then 0 when '중간' then 1 else 2 end, i.reg_id, i.article", cid)
    impacts = []
    for r in imp:
        kind, label = IMPACT_LABEL.get(r["category"], ("review", r["category"]))
        place = (r["article"] or "") + (f" 제{r['para']}항" if r["para"] else "")
        impacts.append({"key": r["review_key"], "kind": kind, "label": label, "where": f"{titles.get(r['reg_id'], r['reg_id'])} {place}",
                        "quote": r["quote"] or "", "reason": readable(r["reason"] or ""), "priority": r["priority"],
                        "basis": basis_of(r["path"]), "path": r["path"] or "",
                        "state": {"수락": "accept", "기각": "reject"}.get(r["state"])})
    from web_agent import load_package
    pkg = load_package(cid) or {}
    return {"id": c["id"], "reg": titles.get(c["reg_id"], c["reg_id"]), "title": c["title"], "status": c["status"],
            "amendment": pkg.get("amendment_final") or c["amendment"],
            "diff": {"old": segments(pkg.get("rows") or payload.get("rows", []), "old"), "new": segments(pkg.get("rows") or payload.get("rows", []), "new")},
            "companion": [{"reg": x["reg_name"], "amendment": x.get("amendment") or "", "manual": len(x.get("manual") or [])} for x in pkg.get("companion", [])],
            "addendum": pkg.get("addendum_draft") or "", "questions": pkg.get("questions") or [],
            "breaks": [pkg.get("breaks_before"), pkg.get("breaks_after")] if pkg else None,
            "impacts": impacts, "has_package": bool(pkg), "coverage": pkg.get("coverage")}


_EXPLAIN: dict[tuple[str, str], dict] = {}


def explain_card(cid: str, key: str) -> dict:
    """카드 하나의 「왜 확인할 만한가」를 로컬 모델이 풀어 쓴다 (요청할 때만, 판정 아님, 검증 실패 시 기존 이유 문구 유지)"""
    if (cid, key) in _EXPLAIN:
        return _EXPLAIN[(cid, key)]
    rows = q("select i.reg_id, i.article, i.para, i.quote, i.reason, i.path, c.amendment, c.payload from amend.impact i "
             "join amend.changeset c on c.id = i.changeset_id where i.changeset_id = %s and i.review_key = %s", cid, key)
    if not rows:
        return {"error": "카드를 찾지 못했습니다"}
    r, titles = rows[0], reg_titles()
    from web_agent import HYPO_MODEL, load_package
    from regcheck.explain import explain
    pkg = load_package(cid) or {}
    where = f"{titles.get(r['reg_id'], r['reg_id'])} {r['article'] or ''}" + (f" 제{r['para']}항" if r["para"] else "")
    try:
        text = explain(pkg.get("amendment_final") or r["amendment"], where, r["quote"] or "", readable(r["reason"] or ""), basis_of(r["path"]), HYPO_MODEL)
    except OSError:
        return {"error": "설명 모델에 연결하지 못했습니다. " + ("API 키와 네트워크를 확인해 주세요" if HYPO_MODEL.startswith("gpt-") else "Ollama가 켜져 있는지 확인해 주세요")}
    out = {"text": text, "note": "모델 요약 — 판정이 아닙니다. 담당자가 원문을 보고 판단하세요" if text else "설명을 만들지 못했습니다(검증 실패). 위 이유 문구를 참고하세요"}
    _EXPLAIN[(cid, key)] = out
    return out


def regulation(reg_id: str) -> dict:
    """규정 전문 (본문 조만, 조 순서대로). 부칙·별표·별지는 원문 링크로"""
    rows = q("select d.article_id, d.label, d.title, c.chunk_id, c.body, c.status from ("
             "  select article_id, label, title, chunk_id, min(item_id) k from amend.chunk_detail "
             "  where reg_id = %s and chunk_id is not null group by 1, 2, 3, 4) d join public.chunks c using (chunk_id) "
             "order by (substring(d.article_id from ':(\\d+)'))::int, coalesce((substring(d.article_id from '의(\\d+)'))::int, 0), d.k", reg_id)
    doc = q("select title, revision, source_url from public.documents where doc_id = %s", reg_id)
    if not doc:
        return {"error": f"규정이 없습니다: {reg_id}"}
    arts, cur = [], None
    for r in rows:
        if not cur or cur["id"] != r["article_id"]:
            cur = {"id": r["article_id"], "label": f"{r['label']}({r['title']})" if r["title"] else r["label"], "paras": []}
            arts.append(cur)
        cur["paras"].append({"chunk": r["chunk_id"], "text": r["body"], "status": r["status"]})
    return {"reg_id": reg_id, "reg": doc[0]["title"], "revision": doc[0]["revision"], "source_url": doc[0]["source_url"], "articles": arts}


ROUTES = {
    "/api/status": lambda p: status(),
    "/api/find": lambda p: find(p.get("q", "")),
    "/api/article": lambda p: article(p.get("chunk", "")),
    "/api/amend": lambda p: amend(p.get("chunk", "")),
    "/api/changesets": lambda p: changesets(),
    "/api/changeset": lambda p: changeset(p.get("id", "")),
    "/api/explain": lambda p: explain_card(p.get("id", ""), p.get("key", "")),
    "/api/regulation": lambda p: regulation(p.get("reg", "")),
    "/api/chat/status": lambda p: web_chat.status(),
    "/api/lawcheck": lambda p: lawcheck(p.get("id", "")),
    "/api/final": lambda p: {k: v for k, v in web_agent.final_package(p.get("id", "")).items() if k != "_companion_rows"},
    "/api/scan/snapshots": lambda p: web_watch.snapshots(),
    "/api/scan": lambda p: web_watch.result(p.get("id", "")),
    "/api/lawname": lambda p: web_agent.lawname_plan(),
    "/api/procedure": lambda p: procedure(p.get("id", "")),
}
# 쓰기·실행 (POST, JSON 본문). user는 화면의 사용자 이름 (시연용 — 실제 배포는 로그인 계정으로 바꿈)
def with_open_changes(res: dict) -> dict:
    """답변 근거마다 진행 중인 개정안(초안·검토·승인)을 붙인다 — 곧 바뀔 조문으로 답했음을 직원에게 알리려고"""
    chunks = [e["chunk"] for e in res.get("entries", []) if e.get("chunk")]
    if not chunks:
        return res
    open_ = {}
    for r in q("select chunk_id, changeset_id, status, title from amend.v_chunk_open_change where chunk_id = any(%s) order by changeset_id", chunks):
        open_.setdefault(r["chunk_id"], []).append({"id": r["changeset_id"], "status": r["status"], "title": r["title"]})
    for e in res["entries"]:
        e["open"] = open_.get(e.get("chunk"), [])
    return res


POSTS = {
    "/api/ask": lambda b: with_open_changes(web_chat.ask(b.get("q", ""), b.get("history") or [])),
    "/api/draft": lambda b: allowed(b, "draft") or web_agent.draft(b.get("chunk", ""), b.get("request", ""), b.get("user", "web")),
    "/api/decide": lambda b: allowed(b, "decide") or web_agent.decide(b.get("id", ""), b.get("key", ""), b.get("state", ""), b.get("user", "web")),
    "/api/submit": lambda b: allowed(b, "submit") or web_agent.submit(b.get("id", ""), b.get("user", "web"), b.get("comment", "")),
    "/api/approve": lambda b: allowed(b, "approve") or web_agent.approve(b.get("id", ""), b.get("user", "web"), b.get("comment", "")),
    "/api/reject": lambda b: allowed(b, "reject") or web_agent.reject_cs(b.get("id", ""), b.get("user", "web"), b.get("comment", "")),
    "/api/publish": lambda b: allowed(b, "publish") or web_agent.publish(b.get("id", ""), b.get("user", "web"), b.get("comment", "")),
    "/api/feedback": lambda b: feedback(b),
    "/api/scan/start": lambda b: allowed(b, "scan") or web_watch.start(b.get("law", ""), b.get("user", "web"), bool(b.get("rebase"))),
    "/api/lawname/create": lambda b: allowed(b, "lawname_create") or web_agent.lawname_create(list(b.get("regs") or []), b.get("user", "web")),
}


def procedure(cid: str) -> dict:
    """이 개정안이 거칠 내부 절차 (regcheck.procedure — 규정 원문에서 읽음)"""
    rows = q("select reg_id, payload->>'kind' as kind from amend.changeset where id = %s", cid)
    if not rows:
        return {"error": f"개정안이 없습니다: {cid}"}
    from regcheck.procedure import for_changeset
    regs, _, _ = web_agent.corpus()
    return {"id": cid, "reg": reg_titles().get(rows[0]["reg_id"], rows[0]["reg_id"]),
            "steps": for_changeset(rows[0]["reg_id"], regs, rows[0]["kind"] or "")}


def allowed(b: dict, action: str) -> dict | None:
    """역할 확인 (web_agent.guard). 허용되면 None, 아니면 {"error"}"""
    return web_agent.guard(b.get("role", ""), action, b.get("user", ""), b.get("id", ""))


def feedback(b: dict) -> dict:
    """답변 「도움이 됐어요/아니에요」 — out/web_feedback.jsonl에 한 줄 (가빈 평가에 쓸 수 있게)"""
    path = ROOT / "out" / "web_feedback.jsonl"
    rec = {k: b.get(k) for k in ("q", "answer", "good", "model", "user")}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return {"ok": True}


import web_agent  # noqa: E402
import web_chat  # noqa: E402
import web_law  # noqa: E402
import web_watch  # noqa: E402


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(WEB), **kw)

    def _json(self, body, code=200):
        data = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        u = urlparse(self.path)
        params = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path == "/api/package":   # 개정 패키지 HTML 내려받기
            cid = re.sub(r"[^A-Za-z0-9_-]", "", params.get("id", ""))
            final = params.get("final") == "1"   # 승인된 최종안 (수락한 카드만 반영)
            html = web_agent.final_file(cid) if final else web_agent.package_html(cid)
            if not html:
                return self._json({"error": f"{'최종안' if final else '패키지'} 파일이 없습니다: {cid}"}, 404)
            data = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Disposition", f"attachment; filename=\"{'final' if final else 'package'}-{cid}.html\"")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return self.wfile.write(data)
        route = ROUTES.get(u.path)
        if not route:
            return super().do_GET()
        try:
            body, code = route(params), 200
        except Exception as e:  # 화면에 원인을 보여 준다 (비밀번호 등은 담기지 않음)
            body, code = {"error": f"{type(e).__name__}: {str(e)[:200]}"}, 500
        self._json(body, code)

    def do_POST(self):  # noqa: N802
        route = POSTS.get(urlparse(self.path).path)
        if not route:
            return self._json({"error": "없는 주소"}, 404)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 20000:
                return self._json({"error": "요청이 너무 큽니다"}, 413)
            body = json.loads(self.rfile.read(n) or b"{}")
            out = route(body)
            self._json(out, 400 if isinstance(out, dict) and out.get("error") else 200)
        except Exception as e:
            self._json({"error": f"{type(e).__name__}: {str(e)[:200]}"}, 500)

    def log_message(self, fmt, *args):  # API 호출만 짧게
        if self.path.startswith("/api/"):
            sys.stderr.write(f"{self.address_string()} {self.path[:80]} {args[1] if len(args) > 1 else ''}\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1", help="같은 와이파이 팀원에게 보이려면 0.0.0.0 (방화벽 규칙 필요)")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    web_law.init(lawdb())  # 법률 DB를 시작할 때 한 번 읽음 (첫 요청이 느려지지 않게, SQLite는 이 스레드에서만)
    web_agent.corpus()
    web_chat.warmup()  # 가빈 검색 모델(GPU)을 뒤에서 미리 올림
    print(f"규정 도우미 화면: http://{a.host}:{a.port}/  (DB 상태: /api/status)")
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
