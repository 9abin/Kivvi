"""팀 공유용 읽기 전용 로그인 계정 (DBeaver 접속용).

  uv run --extra db python scripts/db_team_account.py            # 계정 만들기 (이미 있으면 그대로)
  uv run --extra db python scripts/db_team_account.py --rotate   # 비밀번호 새로
계정 이름·비밀번호는 .env.team (git 제외)에 저장한다. 화면에 비밀번호를 출력하지 않는다 — 팀에는 파일 내용을 따로 전달.
계정은 team_ro 역할만 가진다: public(가빈 표)·amend(우리 표) 읽기만. 쓰기·삭제 불가.
"""

import argparse
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from regcheck.db import connect, load_env  # noqa: E402

TEAM = ROOT / ".env.team"
USER = "mini_cns_team"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rotate", action="store_true")
    args = ap.parse_args()
    host = load_env().get("PG_HOST", "localhost")
    if host not in ("localhost", "127.0.0.1"):
        sys.exit("로컬 사본(reg-db)에서만 계정을 만듭니다")
    env = dict(l.split("=", 1) for l in TEAM.read_text(encoding="utf-8").splitlines() if "=" in l) if TEAM.exists() else {}
    pw = env.get("TEAM_PASSWORD") if not args.rotate else None
    pw = pw or secrets.token_urlsafe(18)
    conn = connect(write=True)
    exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (USER,)).fetchone()
    from psycopg import sql
    stmt = "ALTER ROLE {} WITH LOGIN PASSWORD {}" if exists else "CREATE ROLE {} WITH LOGIN PASSWORD {}"
    conn.execute(sql.SQL(stmt).format(sql.Identifier(USER), sql.Literal(pw)))
    conn.execute(sql.SQL("GRANT team_ro TO {}").format(sql.Identifier(USER)))
    conn.execute(sql.SQL("ALTER ROLE {} SET default_transaction_read_only = on").format(sql.Identifier(USER)))
    conn.execute(sql.SQL("ALTER ROLE {} CONNECTION LIMIT 10").format(sql.Identifier(USER)))
    conn.commit()
    TEAM.write_text(f"# DBeaver 팀 공유 계정 (읽기 전용). git 제외. 팀에는 이 파일 내용만 따로 전달\n"
                    f"TEAM_HOST=(이 PC의 와이파이 IP — ipconfig로 확인)\nTEAM_PORT=5432\nTEAM_DATABASE={load_env()['PG_DATABASE']}\n"
                    f"TEAM_USER={USER}\nTEAM_PASSWORD={pw}\n", encoding="utf-8")
    print(f"계정 {USER} {'갱신' if exists else '생성'} (권한: team_ro 읽기 전용) → 접속 정보 {TEAM.name}")


if __name__ == "__main__":
    main()
