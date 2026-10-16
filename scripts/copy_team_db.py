"""팀 DB(항 단위)의 public 스키마를 이 PC의 Docker DB에 새 DB로 복사한다. 팀 DB는 읽기만 한다.

팀 DB에 늘 접속할 수 없을 때(박형건 PC가 꺼짐, 다른 네트워크) 같은 데이터로 평가하려고 쓴다.
  - 접속 정보: .env의 TEAM_HOST, TEAM_PORT, TEAM_DATABASE, TEAM_USER, TEAM_PASSWORD (조회 전용 계정이면 됨)
  - 복사 대상: Docker 컨테이너 db-pg의 새 DB(기본 regulations_team, 소유자 rag_reg). 이미 있으면 멈춘다
  - pg_dump·pg_restore는 컨테이너 안의 것을 쓴다 (PostgreSQL 17). 비밀번호는 명령줄이 아니라 환경 변수로 넘긴다
  - 우리 검색 코드가 읽는 chunks.section(장) 칸이 팀 DB에는 없어서, 사본에만 빈 칸으로 추가한다

실행 (저장소 루트에서): uv run python -m scripts.copy_team_db [--target regulations_team]
복사한 뒤: $env:PG_DATABASE = "regulations_team" 으로 평가·검색에 쓴다 (docs/팀_실행_안내.md).
"""
import argparse
import os
import subprocess
import sys

from dotenv import dotenv_values

sys.stdout.reconfigure(encoding="utf-8")
CONTAINER = "db-pg"


def run(args, env=None, check=True):
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    out = (r.stdout + r.stderr).strip()
    if out:
        print(out[-800:])
    if check and r.returncode != 0:
        sys.exit("실패: " + " ".join(args[:6]))
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default="regulations_team")
    target = ap.parse_args().target
    e = dotenv_values(".env")
    psql = ["docker", "exec", CONTAINER, "psql", "-U", "postgres", "-v", "ON_ERROR_STOP=1"]
    exists = run(psql + ["-d", "postgres", "-tAc", f"select 1 from pg_database where datname='{target}'"]).stdout.strip()
    if exists:
        sys.exit(f"{target}이(가) 이미 있습니다. 지우고 다시 받으려면 먼저 DROP DATABASE {target};")
    run(psql + ["-d", "postgres", "-c", f"CREATE DATABASE {target} OWNER rag_reg"])
    run(psql + ["-d", target, "-c", "CREATE EXTENSION IF NOT EXISTS vector"])
    print("팀 DB에서 덤프 중 (읽기만)...")
    run(["docker", "exec", "-e", "PGPASSWORD", CONTAINER, "pg_dump", "-h", e["TEAM_HOST"], "-p", e["TEAM_PORT"],
         "-U", e["TEAM_USER"], "-d", e["TEAM_DATABASE"], "-n", "public", "--no-owner", "--no-privileges",
         "-Fc", "-f", "/tmp/team_public.dump"], env=dict(os.environ, PGPASSWORD=e["TEAM_PASSWORD"]))
    print("복원 중... ('schema public already exists' 오류는 무해)")
    run(["docker", "exec", CONTAINER, "pg_restore", "-U", "rag_reg", "-d", target, "--no-owner", "--no-privileges",
         "/tmp/team_public.dump"], check=False)
    run(psql + ["-d", target, "-c", "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS section text"])
    run(["docker", "exec", CONTAINER, "rm", "-f", "/tmp/team_public.dump"])
    print("청크 / 색인 / 문서:", run(psql + ["-d", target, "-tAc",
          "select count(*), sum(is_indexed::int), (select count(*) from documents) from chunks"]).stdout.strip())


if __name__ == "__main__":
    main()
