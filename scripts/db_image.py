"""팀 공유 DB 이미지 만들기: 로컬 reg-db(가빈 public + 우리 amend) → 덤프 → 데이터가 든 PostgreSQL 이미지.

  uv run --extra db python scripts/db_image.py              # 이미지 mini-cns-db:{YYMMDD}
  uv run --extra db python scripts/db_image.py --test       # 빌드 후 임시 컨테이너로 복원·건수 확인
  uv run --extra db python scripts/db_image.py --tag ghcr.io/계정/mini-cns-db:261012   # GitHub 패키지용 이름
먼저 파이프라인과 scripts/amend_load.py로 최신 결과를 적재한다.
덤프에는 계정·비밀번호가 없다 (--no-owner --no-privileges, 역할은 amend_schema.sql이 만든다). 팀원은 자기 비밀번호로 띄운다.
"""

import argparse
import datetime
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from regcheck.db import load_env  # noqa: E402

SEED = ROOT / "db" / "seed"
ENV = {**os.environ, "MSYS_NO_PATHCONV": "1"}


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    print("$", " ".join(cmd))
    return subprocess.run(cmd, check=True, env=ENV, **kw)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default=f"mini-cns-db:{datetime.date.today():%y%m%d}")
    ap.add_argument("--test", action="store_true")
    args = ap.parse_args()
    e = load_env()
    SEED.mkdir(parents=True, exist_ok=True)
    run(["docker", "exec", "reg-db", "pg_dump", "-U", e["PG_USER"], "-d", e["PG_DATABASE"], "-Fc", "--no-owner", "--no-privileges",
         "-f", "/tmp/team.dump"])
    run(["docker", "cp", "reg-db:/tmp/team.dump", str(SEED / "regulations.dump")])
    run(["docker", "exec", "reg-db", "rm", "/tmp/team.dump"])
    (SEED / "amend_schema.sql").write_text((ROOT / "sql" / "amend_schema.sql").read_text(encoding="utf-8"), encoding="utf-8")
    print(f"덤프 {(SEED / 'regulations.dump').stat().st_size / 1e6:.1f}MB")
    run(["docker", "build", "-t", args.tag, str(ROOT / "db")])
    if args.test:
        name = "mini-cns-db-test"
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        run(["docker", "run", "-d", "--name", name, "-e", "POSTGRES_USER=t", "-e", "POSTGRES_PASSWORD=t", args.tag])
        try:
            for _ in range(90):
                logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True, encoding="utf-8", errors="replace")
                if "준비 완료" in logs.stdout + logs.stderr and "ready to accept connections" in (logs.stdout + logs.stderr).split("준비 완료")[-1]:
                    break
                time.sleep(2)
            out = subprocess.run(["docker", "exec", name, "psql", "-U", "t", "-d", "regulations", "-Atc",
                                  "select (select count(*) from public.chunks), (select count(*) from amend.edge), (select count(*) from amend.finding), "
                                  "(select count(*) from amend.chunk_detail), (select count(*) from pg_roles where rolname in ('team_ro','editor','chatbot_ro','amend_builder','loader'))"],
                                 capture_output=True, text=True, env=ENV)
            print("시험 복원 (chunks, edge, finding, chunk_detail, 역할):", out.stdout.strip() or out.stderr.strip())
        finally:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    print(f"→ 이미지 {args.tag}")


if __name__ == "__main__":
    main()
