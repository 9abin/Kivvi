#!/bin/bash
# 첫 실행 때만 돈다 (데이터 볼륨이 비어 있을 때). 덤프 복원 → amend 역할·권한·설명 적용
set -e
echo "[mini-cns] 덤프 복원: /seed/regulations.dump"
pg_restore --no-owner --no-privileges -U "$POSTGRES_USER" -d "$POSTGRES_DB" /seed/regulations.dump
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f /seed/amend_schema.sql > /dev/null
psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "select 'chunks ' || count(*) from public.chunks union all select 'edge ' || count(*) from amend.edge union all select 'finding ' || count(*) from amend.finding"
echo "[mini-cns] 준비 완료"
