"""가빈 DB 덤프를 로컬 reg-db 컨테이너에 복원한다.

  docker compose up -d
  uv run python scripts/db_restore.py 경로/regulations.dump      # pg_dump -Fc 형식
  uv run python scripts/db_restore.py 경로/regulations.sql       # 일반 SQL 형식
덤프 만드는 법 (가빈 PC): docker exec db-pg pg_dump -U rag_reg -Fc regulations > regulations.dump
기존 내용은 지우고(--clean) 다시 채운다. 복원 대상은 이 PC의 reg-db 컨테이너뿐이다.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from regcheck.db import load_env  # noqa: E402

CONTAINER = "reg-db"


def run(cmd: list[str], **kw) -> None:
    print("$", " ".join(cmd))
    subprocess.run(cmd, check=True, **kw)


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    dump = Path(sys.argv[1]).resolve()
    if not dump.exists():
        sys.exit(f"파일 없음: {dump}")
    e = load_env()
    user, db = e["PG_USER"], e["PG_DATABASE"]
    # amend 뷰가 public.chunks를 참조하면 --clean이 표를 못 지워 옛 데이터가 남는다 → 먼저 지우고, 복원 뒤 amend_load.py가 다시 만든다
    subprocess.run(["docker", "exec", CONTAINER, "psql", "-U", user, "-d", db, "-c", "DROP VIEW IF EXISTS amend.v_chunk_unmatched"])
    run(["docker", "cp", str(dump), f"{CONTAINER}:/tmp/restore.dump"])
    if dump.suffix == ".sql":
        run(["docker", "exec", CONTAINER, "psql", "-U", user, "-d", db, "-v", "ON_ERROR_STOP=1", "-f", "/tmp/restore.dump"])
    else:
        # 소유자·권한 차이로 나는 경고는 무시하고 데이터만 복원
        subprocess.run(["docker", "exec", CONTAINER, "pg_restore", "-U", user, "-d", db,
                        "--clean", "--if-exists", "--no-owner", "--no-privileges", "/tmp/restore.dump"])
    run(["docker", "exec", CONTAINER, "rm", "/tmp/restore.dump"])
    print("복원 끝. 다음: uv run --extra db python scripts/amend_load.py (amend 뷰·권한 다시 만들기) → scripts/db_check.py")


if __name__ == "__main__":
    main()
