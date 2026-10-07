"""규정 원문 DB 상태 확인 (읽기 전용).

  uv run --extra db python scripts/db_check.py
테이블·행 수·칸 목록을 보여 주고, 덤프 복원 후에는 가빈에게 확인할 항목(별표·별지, 부칙, 본문 표기)을 함께 본다.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from regcheck.db import FORBIDDEN_COLUMNS, connect  # noqa: E402


def main() -> None:
    conn = connect()
    db, user = conn.execute("select current_database(), current_user").fetchone()
    print(f"접속: {db} / {user} (읽기 전용)")
    tables = [r[0] for r in conn.execute(
        "select table_name from information_schema.tables where table_schema='public' order by 1").fetchall()]
    if not tables:
        print("테이블 없음 — 가빈 덤프를 복원해야 함: uv run python scripts/db_restore.py 덤프파일")
        return
    for t in tables:
        n = conn.execute(f'select count(*) from "{t}"').fetchone()[0]
        cols = conn.execute(
            "select column_name, data_type from information_schema.columns where table_name=%s order by ordinal_position", (t,)
        ).fetchall()
        print(f"\n■ {t}: {n}행")
        for c, ty in cols:
            mark = "  ← 점검 코드 읽기 금지" if c in FORBIDDEN_COLUMNS else ""
            print(f"   {c:24} {ty}{mark}")


if __name__ == "__main__":
    main()
