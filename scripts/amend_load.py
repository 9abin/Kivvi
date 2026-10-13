"""amend 스키마 만들기·적재 (가빈 규정 DB의 로컬 사본, reg-db).

  uv run --extra db python scripts/amend_load.py            # 스키마 적용(다시 실행해도 됨) + out/ 산출물 적재
  uv run --extra db python scripts/amend_load.py --reset    # amend 스키마를 지우고 새로 (결정 기록이 있으면 거부)
입력: out/parsed, out/relations/relations.jsonl, out/findings/findings.jsonl, out/changes/*/changeset.json·check.json,
      eval/review_log.jsonl (사람 결정, 없는 줄만 덧붙임)
합의 전이라 이 PC의 사본(localhost)에만 쓴다. 가빈 DB 본체에 만드는 것은 합의 후 (docs/chatbot-integration-proposal.md).
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from regcheck import amend_db  # noqa: E402
from regcheck.db import connect, load_env  # noqa: E402


def jl(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()] if p.exists() else []


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()
    host = load_env().get("PG_HOST", "localhost")
    if host not in ("localhost", "127.0.0.1"):
        sys.exit(f"PG_HOST={host}: 합의 전에는 로컬 사본에만 씁니다")
    conn = connect(write=True)
    if args.reset:
        n = conn.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='amend' AND table_name='decision_log'").fetchone()[0]
        if n and conn.execute("SELECT count(*) FROM amend.decision_log").fetchone()[0]:
            sys.exit("amend.decision_log에 사람 결정이 있어 --reset 하지 않습니다")
        conn.execute("DROP SCHEMA IF EXISTS amend CASCADE")
        conn.commit()
    amend_db.apply_schema(conn)
    regs = {r["reg_id"]: r for r in (json.loads(p.read_text(encoding="utf-8")) for p in sorted((ROOT / "out" / "parsed").glob("[0-9]*.json")))}
    changes = []
    for d in sorted((ROOT / "out" / "changes").glob("*/changeset.json")):
        cs = json.loads(d.read_text(encoding="utf-8"))
        chk = d.parent / "check.json"
        changes.append((cs, json.loads(chk.read_text(encoding="utf-8"))["found"] if chk.exists() else []))
    stats = amend_db.load(conn, regs, jl(ROOT / "out" / "relations" / "relations.jsonl"), jl(ROOT / "out" / "findings" / "findings.jsonl"),
                          changes, jl(ROOT / "eval" / "review_log.jsonl"))
    print("amend 적재:", ", ".join(f"{k} {v:,}" for k, v in stats.items()))
    for row in conn.execute("SELECT problem, count(*) FROM amend.v_chunk_unmatched GROUP BY 1"):
        print("  가빈 청크 대응 확인:", row[0], row[1])


if __name__ == "__main__":
    main()
