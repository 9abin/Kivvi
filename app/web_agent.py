"""개정안 작성(에이전트 C)·검토 결정·제출·패키지 파일 — 화면 서버(app/web_server.py)가 부른다.

쓰기는 .env 계정으로 접속한 뒤 `SET ROLE editor`로 권한을 낮춰서 한다 (amend.changeset·impact 쓰기, decision_log 덧붙이기만).
개정안은 참고 문안이다. 규정 원문(public)과 우리 파싱 결과(out/parsed)는 바꾸지 않는다 (AGENTS §3).
  draft(chunk, request)        자연어 요청 → 로컬 LLM(DRAFT_MODEL, 기본 qwen2.5:7b) 개정문 초안 → 적용기 검증 → 연쇄 수정 → 영향 → DB 초안 저장
  decide(cid, key, state, ..)  영향 카드 수락·기각 → decision_log
  submit(cid, ..)              개정안 상태 「검토」(화면: 검토 요청됨) + decision_log. 상태 값은 sql/amend_schema.sql CHECK (초안·검토·승인·공포·기각)
  package_html(cid)            개정 패키지 HTML (내려받기)
"""

from __future__ import annotations

import json
import os
import sys
import threading
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from regcheck.db import load_env  # noqa: E402

DRAFT_MODEL = os.environ.get("DRAFT_MODEL", "qwen2.5:7b")   # 자연어 개정 평가에서 7/8 (리포트 28). 9B는 6/8
STATES = {"accept": "수락", "reject": "기각"}
_lock = threading.Lock()


def write_conn():
    e = load_env()
    con = psycopg.connect(host="127.0.0.1", port=e.get("PG_PORT", "5432"), dbname=e["PG_DATABASE"], user=e["PG_USER"],
                          password=e["PG_PASSWORD"], gssencmode="disable", connect_timeout=5)
    con.execute("SET ROLE editor")
    return con


@lru_cache(maxsize=1)
def corpus():
    """우리 파싱·관계·인용 (에이전트 입력). 처음 한 번 읽는다."""
    regs = {r["reg_id"]: r for r in (json.loads(p.read_text(encoding="utf-8")) for p in (ROOT / "out" / "parsed").glob("[0-9]*.json"))}
    rels = [json.loads(l) for l in (ROOT / "out" / "relations" / "relations.jsonl").read_text(encoding="utf-8").splitlines()]
    edges = [json.loads(l) for l in (ROOT / "out" / "edges" / "edges_all.jsonl").read_text(encoding="utf-8").splitlines()]
    return regs, rels, edges


def package_path(cid: str) -> Path:
    return ROOT / "out" / "changes" / cid / "package.json"


def load_package(cid: str) -> dict | None:
    p = package_path(cid)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def draft(chunk: str, request: str, user: str) -> dict:
    request = (request or "").strip()
    if len(request) < 4:
        return {"error": "어떻게 바꿀지 한 문장으로 적어 주세요 (예: 분할 횟수를 3회로 늘린다)"}
    reg_id = (chunk or "").split("_")[0]
    regs, rels, edges = corpus()
    reg = regs.get(reg_id)
    if not reg:
        return {"error": f"규정을 찾지 못했습니다: {reg_id}"}
    label = (chunk.split("_")[1] if "_" in chunk else "")
    full_request = f"{reg['name']} {label} {request}" if label and label not in request else f"{reg['name']} {request}"
    from regcheck.nl import draft as nl_draft
    from regcheck.agent import build_package
    from regcheck.lawdb import DEFAULT as LAW_DB, LawDB
    from regcheck.amend_db import impact_rows
    from agent_fix import render
    with _lock:   # 로컬 LLM·GPU를 채팅과 나눠 쓰므로 한 번에 하나
        res = nl_draft(regs, full_request, model=DRAFT_MODEL, reg=reg)
        if not res.get("ok"):
            last = (res.get("tries") or [{}])[-1].get("error", "")
            return {"error": "개정문을 만들지 못했습니다. 바꿀 조·항과 값을 더 구체적으로 적어 주세요." + (f" ({last[:80]})" if last else ""),
                    "tries": len(res.get("tries", []))}
        cid = "W" + datetime.now().strftime("%y%m%d-%H%M%S")
        cs = res["changeset"]
        cs["id"], cs["title"] = cid, request
        pkg = build_package(cs, regs, rels, edges, lawdb=LawDB(LAW_DB) if LAW_DB.exists() else None)
    pkg["run"] = cid
    out = package_path(cid).parent
    out.mkdir(parents=True, exist_ok=True)
    package_path(cid).write_text(json.dumps(pkg, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "package.html").write_text(render(pkg), encoding="utf-8")
    found = pkg["law_check"] + pkg["review"] + [{**b, "category": "breaks"} for b in pkg.get("remaining_breaks", []) if isinstance(b, dict)]
    changes = cs.get("changes") or [{"origin": r["origin"]} for r in pkg["rows"] if r.get("origin")]   # v_chunk_open_change가 바뀌는 조를 여기서 찾음
    payload = {"id": cid, "run": cid, "rows": pkg["rows"], "changes": changes, "request": request, "model": DRAFT_MODEL}
    with write_conn() as con, con.cursor() as cur:
        cur.execute("INSERT INTO amend.changeset (id, reg_id, title, amendment, status, origin_chunk, created_by, payload) "
                    "VALUES (%s, %s, %s, %s, '초안', %s, %s, %s)",
                    (cid, reg_id, f"{label} — {request}" if label else request, pkg["amendment_final"], chunk, user, json.dumps(payload, ensure_ascii=False)))
        rows = impact_rows(cid, found)
        if rows:
            cur.executemany("INSERT INTO amend.impact VALUES (" + ",".join(["%s"] * 14) + ")", rows)
        cur.execute("INSERT INTO amend.decision_log (key, kind, prev, state, reviewer, comment, ref) VALUES (%s, 'changeset', NULL, '초안', %s, %s, %s)",
                    (f"C:{cid}", user, request, json.dumps({"chunk": chunk, "model": DRAFT_MODEL}, ensure_ascii=False)))
    return {"id": cid, "breaks": [pkg["breaks_before"], pkg["breaks_after"]], "companion": len(pkg["companion"]), "impacts": len(rows)}


def decide(cid: str, key: str, state: str, user: str) -> dict:
    if state not in (*STATES, "clear"):
        return {"error": "state는 accept · reject · clear"}
    if not key.startswith(f"I:{cid}:"):
        return {"error": "이 개정안의 영향 카드가 아닙니다"}
    with write_conn() as con, con.cursor() as cur:
        prev = cur.execute("SELECT state FROM amend.v_decision_state WHERE key = %s", (key,)).fetchone()
        new = "미결정" if state == "clear" else STATES[state]
        cur.execute("INSERT INTO amend.decision_log (key, kind, prev, state, reviewer, ref) VALUES (%s, 'impact', %s, %s, %s, %s)",
                    (key, prev[0] if prev else None, new, user, json.dumps({"changeset": cid}, ensure_ascii=False)))
    return {"key": key, "state": new}


def submit(cid: str, user: str, comment: str = "") -> dict:
    with write_conn() as con, con.cursor() as cur:
        row = cur.execute("SELECT status FROM amend.changeset WHERE id = %s", (cid,)).fetchone()
        if not row:
            return {"error": f"개정안이 없습니다: {cid}"}
        open_ = cur.execute("SELECT count(*) FROM amend.impact i LEFT JOIN amend.v_decision_state d ON d.key = i.review_key "
                            "WHERE i.changeset_id = %s AND coalesce(d.state, '미결정') NOT IN ('수락', '기각')", (cid,)).fetchone()[0]
        cur.execute("UPDATE amend.changeset SET status = '검토' WHERE id = %s", (cid,))
        cur.execute("INSERT INTO amend.decision_log (key, kind, prev, state, reviewer, comment, ref) VALUES (%s, 'changeset', %s, '검토', %s, %s, %s)",
                    (f"C:{cid}", row[0], user, comment or None, json.dumps({"undecided": open_}, ensure_ascii=False)))
    return {"id": cid, "status": "검토", "undecided": open_}


def package_html(cid: str) -> str | None:
    p = package_path(cid)
    html = p.parent / "package.html"
    if html.exists():
        return html.read_text(encoding="utf-8")
    pkg = load_package(cid)
    if pkg:
        from agent_fix import render
        return render(pkg)
    return None
