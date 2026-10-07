"""규정 원문 DB(김가빈 챗봇 DB의 로컬 사본) 읽기 전용 접속.

접속 정보는 .env(PG_*)에서 읽는다. 세션을 읽기 전용으로 연다 (수정 에이전트는 이 DB를 고치지 않는다).
정답 누수 방지: `본문_검색`(오탈자 보정본)과 보정이 반영된 `cites`는 점검 코드에서 읽지 않는다.
읽어도 되는 칸만 `safe_select()`로 고른다. 칸 이름은 덤프를 받은 뒤 실제 스키마에 맞춰 확정한다.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# 점검 코드가 읽으면 안 되는 칸 (사람 보정이 반영된 값). 가빈 sql/schema.sql(chunks 테이블) 기준으로 확정 (261007)
#   body_search: 확정 오탈자 반영본 / corrections_note: 정정 안내 / cites: 보정 반영 인용 목록
#   body_tokens: 형태소 토큰 — 보정본에서 만들었을 수 있어 확인 전까지 금지
# 읽어도 되는 칸: documents(doc_id, title, tier, revision, source_url, file_name), chunks(chunk_id, doc_id, article, status, is_indexed, body)
FORBIDDEN_COLUMNS = {"body_search", "corrections_note", "cites", "body_tokens", "본문_검색", "정정안내"}


def load_env(path: Path = ROOT / ".env") -> dict[str, str]:
    env = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return {**env, **{k: v for k, v in os.environ.items() if k.startswith("PG_")}}


def connect():
    import psycopg

    e = load_env()
    conn = psycopg.connect(
        host=e.get("PG_HOST", "localhost"), port=int(e.get("PG_PORT", "5432")),
        dbname=e["PG_DATABASE"], user=e["PG_USER"], password=e["PG_PASSWORD"],
        # Windows + localhost에서 GSS 암호화 협상으로 접속이 멈추는 경우가 있어 끈다 (로컬 Docker DB라 불필요)
        gssencmode="disable", connect_timeout=10,
    )
    conn.execute("SET default_transaction_read_only = on")
    conn.commit()
    return conn


def safe_select(conn, table: str, columns: list[str], where: str = "", params: tuple = ()):
    """금지 칸을 고르면 거부한다 (점검 코드에서 정답이 새지 않게)."""
    bad = [c for c in columns if c in FORBIDDEN_COLUMNS]
    if bad:
        raise ValueError(f"점검 코드에서 읽으면 안 되는 칸: {bad} (보정이 반영된 값)")
    cols = ", ".join(f'"{c}"' for c in columns)
    sql = f'SELECT {cols} FROM "{table}"' + (f" WHERE {where}" if where else "")
    return conn.execute(sql, params).fetchall()
