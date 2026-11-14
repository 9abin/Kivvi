"""변경 감시 화면 API (고도화 6): 법률 DB 판 목록, 「탐색 시작」, 탐색 결과.

  snapshots()           data/law_rag*.sqlite3 (반입된 판)과 지금 서버가 쓰는 판, 마지막 탐색
  start(file, user)     그 판으로 점검을 다시 돌려(regcheck.watch) 직전 탐색과 비교 → amend.scan·scan_item에 기록
  result(id)            탐색 결과: 새로 생김·해결됨 목록, 그대로는 수만
비교 기준은 직전 탐색의 결과(새로 생김 + 그대로), 탐색이 처음이면 기준선 적재본(amend.finding)이다.
쓰기는 editor 역할로 탐색 기록 표만 (점검 결과 표 amend.finding은 바꾸지 않는다 — 기준선 교체는 scripts/amend_load.py).
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from pathlib import Path

from psycopg.rows import dict_row

from regcheck import watch
from regcheck.amend_db import unit_chunk

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
_lock = threading.Lock()
SHOW = ("new", "resolved")
# 새로 생긴 결과마다 다음 행동: 에이전트가 초안까지 만들 수 있는 유형 / 업무 판단이 필요한 유형 (리포트 47)
ACTION = {"법령명_변경": "초안 가능 — 법령명 정비", "법령명_오기": "초안 가능 — 법령명 정비",
          "법령_개정확인": "현업 판단 필요 — 바뀐 법령과 대조", "법령_조문없음": "현업 판단 필요 — 법령 조문 이동·삭제 확인"}


def _q(sql: str, *args) -> list[dict]:
    from web_agent import write_conn   # editor 역할: amend·public 읽기 가능
    with write_conn() as con, con.cursor(row_factory=dict_row) as cur:
        return cur.execute(sql, args).fetchall()


def snapshots() -> dict:
    last = _q("select id, to_char(started_at at time zone 'Asia/Seoul', 'YYYY-MM-DD HH24:MI') as started_at, law_snapshot, law_count, base, counts from amend.scan order by amend.scan.started_at desc limit 1")
    return {"snapshots": watch.snapshots(), "current": "law_rag.sqlite3", "last": last[0] if last else None}


def _file(name: str) -> Path | None:
    """data/ 안의 law_rag*.sqlite3만 (경로 조작 막기)"""
    if not re.fullmatch(r"law_rag[\w.\-]*\.sqlite3", name or ""):
        return None
    p = DATA / name
    return p if p.exists() else None


def _baseline() -> tuple[str, list[dict]]:
    last = _q("select id from amend.scan order by started_at desc limit 1")
    if last:
        sid = last[0]["id"]
        return sid, _q("select review_key, reg_id, reg_name, unit, article, para, chunk_id, type, method, quote, note "
                       "from amend.scan_item where scan_id = %s and change in ('new', 'kept')", sid)
    return "finding", _q("select f.review_key, f.reg_id, d.name as reg_name, f.unit, f.article, f.para, f.chunk_id, f.type, f.method, "
                         "f.quote, f.note from amend.finding f left join public.documents d on d.doc_id = f.reg_id")


def _chunks(found: list[dict]) -> dict[str, str]:
    """항목 ID(24024:4-1-0-0) → 가빈 청크. 부칙·별표·별지는 규정별 덩어리 규칙(amend_db.unit_chunk)"""
    ids = list({f.get("src_id") for f in found if f.get("src_id")})
    m = {r["item_id"]: r["chunk_id"] for r in _q("select item_id, chunk_id from amend.chunk_detail where occ = 0 and item_id = any(%s)", ids)}
    gabin = None
    out = {}
    for f in found:
        sid = f.get("src_id") or ""
        c = m.get(sid)
        if not c and f.get("unit") in ("부칙", "별표", "별지"):
            if gabin is None:
                gabin = {r["chunk_id"] for r in _q("select chunk_id from public.chunks")}
            c = unit_chunk(f["reg_id"], f["unit"], sid, gabin)
        out[sid] = c
    return out


def start(name: str, user: str, rebase: bool = False) -> dict:
    """rebase: 비교하지 않고 이 판의 결과를 기준으로만 기록 (설치 첫날, 또는 기준을 다시 잡을 때)"""
    path = _file(name)
    if not path:
        return {"error": f"반입된 법률 DB가 아닙니다: {name}"}
    if not _lock.acquire(blocking=False):
        return {"error": "다른 탐색이 진행 중입니다"}
    try:
        base_id, prev = ("rebase", []) if rebase else _baseline()
        cur, stats = watch.scan(path)
        d = watch.diff(prev, cur)
        if rebase:
            d = {"new": [], "resolved": [], "kept": d["new"]}
        chunk = _chunks(d["new"] + (d["kept"] if rebase else []))
        sid = "SC" + datetime.now().strftime("%y%m%d-%H%M%S")
        counts = {k: len(v) for k, v in d.items()}
        info = watch.snapshot_info(path)

        prev_chunk = {f["review_key"]: f.get("chunk_id") for f in prev}

        def row(f, change):
            c = prev_chunk.get(f["review_key"]) or chunk.get(f.get("src_id") or "")
            return (sid, f["review_key"], change, f["reg_id"], f.get("reg_name"), f.get("unit"), f.get("article"), f.get("para"),
                    c, f["type"], f.get("method"), f.get("quote"), f.get("note"))

        from web_agent import write_conn
        with write_conn() as con, con.cursor() as cur_:
            cur_.execute("insert into amend.scan (id, law_snapshot, law_count, base, counts, stats, created_by) values (%s,%s,%s,%s,%s,%s,%s)",
                         (sid, info["file"], info["laws"], base_id, json.dumps(counts), json.dumps(stats, ensure_ascii=False), user))
            cur_.executemany("insert into amend.scan_item values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                             [row(f, ch) for ch in ("new", "resolved", "kept") for f in d[ch]])
            con.commit()
        return result(sid)
    finally:
        _lock.release()


def result(sid: str = "") -> dict:
    if not sid:
        last = _q("select id from amend.scan order by started_at desc limit 1")
        if not last:
            return {"scan": None}
        sid = last[0]["id"]
    scan = _q("select id, to_char(started_at at time zone 'Asia/Seoul', 'YYYY-MM-DD HH24:MI') as started_at, law_snapshot, law_count, base, counts, stats, created_by from amend.scan where id = %s", sid)
    if not scan:
        return {"error": f"탐색 기록이 없습니다: {sid}"}
    items = _q("select change, reg_id, reg_name, unit, article, para, chunk_id, type, method, quote, note from amend.scan_item "
               "where scan_id = %s and change = any(%s) order by change, reg_name, article, type", sid, list(SHOW))
    for it in items:
        it["action"] = ACTION.get(it["type"], "확인 필요") if it["change"] == "new" else ""
        it["type"] = it["type"].replace("_", " ")
    history = _q("select id, to_char(started_at at time zone 'Asia/Seoul', 'YYYY-MM-DD HH24:MI') as started_at, law_snapshot, counts from amend.scan order by amend.scan.started_at desc limit 10")
    return {"scan": scan[0], "items": items, "history": history}
