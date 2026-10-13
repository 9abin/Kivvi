# MINI CNS 팀 공유 DB: 가빈 챗봇 DB(public) + 조항 수정 에이전트(amend)가 들어 있는 PostgreSQL.
# 처음 실행할 때 /seed/regulations.dump를 복원하고 amend 역할·권한·설명을 적용한다.
#   빌드: scripts/db_image.py   실행: docker compose -f db/docker-compose.team.yml up -d
FROM pgvector/pgvector:pg17
ENV POSTGRES_DB=regulations
COPY initdb/ /docker-entrypoint-initdb.d/
COPY seed/ /seed/
