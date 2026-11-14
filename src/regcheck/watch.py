"""변경 감시: 법률 DB 새 판을 반입하면 점검을 다시 돌려 직전 결과와 비교한다 (고도화 6).

  scan(law_db)      규정 원문·인용 엣지(out/)는 그대로, 법률 DB만 바꿔 점검 결과 전체를 다시 만든다 (build_findings와 같은 단계)
  diff(prev, cur)   review_key(규정·단위·조·항·문구·유형 해시)로 맞춰 새로 생김 / 해결됨 / 그대로
  snapshots()       data/law_rag*.sqlite3 목록 (반입된 판)
같은 문제는 실행이 달라도 review_key가 같아, 사람이 이미 본 결과와 새로 볼 결과를 가를 수 있다.
판정이 아니라 「다시 볼 곳」 목록이다 (AGENTS §3).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .citations import Registry
from .findings import duplicate_articles, duplicate_paragraphs, from_edges, from_semantic
from .history import merge, stale_citations
from .lawdb import LawDB, law_changes
from .review import key_baseline

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "out"


def _jl(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()] if p.exists() else []


def inputs() -> tuple[dict, list, list, list]:
    regs = {r["reg_id"]: r for r in (json.loads(p.read_text(encoding="utf-8")) for p in (OUT / "parsed").glob("[0-9]*.json"))}
    return (regs, _jl(OUT / "edges" / "edges_all.jsonl"), _jl(OUT / "semantic" / "stage2.jsonl"),
            _jl(OUT / "relations" / "relations.jsonl"))


def collect(regs: dict, edges: list, sem: list, rels: list, law_db: Path | None) -> tuple[list[dict], dict, dict]:
    """점검 결과 (fid·run 없음), 개정 이력 통계, 법령 DB 통계"""
    found = from_edges(edges) + from_semantic(sem) + duplicate_paragraphs(regs) + duplicate_articles(regs)
    found, hist_stats = merge(found, stale_citations(regs, rels))
    law_stats: dict = {}
    if law_db and Path(law_db).exists():
        db = LawDB(Path(law_db))
        try:
            law_found, law_stats = law_changes(regs, edges, db, Registry(list(regs.values())).law_aliases)
        finally:
            db.con.close()
        found += law_found
    for f in found:
        f["reg_name"] = regs[f["reg_id"]]["name"]
    return found, hist_stats, law_stats


def scan(law_db: Path) -> tuple[list[dict], dict]:
    regs, edges, sem, rels = inputs()
    found, hist, law = collect(regs, edges, sem, rels, law_db)
    return found, {"개정 이력": hist, "법령 DB": law}


def diff(prev: list[dict], cur: list[dict]) -> dict[str, list[dict]]:
    """review_key로 맞춘다. prev 항목에 review_key가 있으면 그대로 쓴다 (DB에서 읽은 경우)"""
    pk = {f.get("review_key") or key_baseline(f): f for f in prev}
    ck = {key_baseline(f): f for f in cur}
    return {"new": [ck[k] | {"review_key": k} for k in ck if k not in pk],
            "resolved": [pk[k] | {"review_key": k} for k in pk if k not in ck],
            "kept": [ck[k] | {"review_key": k} for k in ck if k in pk]}


def snapshot_info(path: Path) -> dict:
    """반입된 법률 DB 한 판: 파일 이름, 법령 수, 가장 늦은 공포일"""
    con = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    try:
        n = con.execute("select count(*) from documents").fetchone()[0]
        last = ""
        for (raw,) in con.execute("select raw_json from documents"):
            d = (json.loads(raw).get("법령", {}).get("기본정보", {}) or {}).get("공포일자") or ""
            last = max(last, d)
    finally:
        con.close()
    return {"file": Path(path).name, "laws": n, "last_promulgated": last, "size_mb": round(Path(path).stat().st_size / 2**20)}


def snapshots() -> list[dict]:
    return [snapshot_info(p) for p in sorted((ROOT / "data").glob("law_rag*.sqlite3"))]
