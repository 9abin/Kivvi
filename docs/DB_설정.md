# DB 설정 (pgvector)

규정 검색 색인을 저장하는 PostgreSQL 설정을 정리한다.
청크를 어떻게 만드는지는 `docs/전처리_규칙.md`, 색인 설계는 `docs/archive/experiments/재색인_설계.md`를 본다.

**기준일: 2026.10.06**

---

## 1. 구성 요약

| 항목 | 값 |
|---|---|
| 컨테이너 이름 | `db-pg` |
| 이미지 | `pgvector/pgvector:pg17` (PostgreSQL 17.11) |
| 호스트 포트 | `5432` (컨테이너 `5432`에 연결) |
| 데이터 볼륨 | `db-pg-data` (`/var/lib/postgresql/data`) |
| 규정 DB | `regulations` |
| 규정 계정 | `rag_reg` (`regulations` DB의 소유자) |
| 확장 | `vector` 0.8.6 |

같은 서버에 다른 프로젝트 DB(`library`, `shop`, `hanbit_bank`, `w3school`, `retail`)가 있다. 이 프로젝트는 건드리지 않는다.

`db-pg-data` 볼륨에 있는 데이터는 컨테이너를 지워도 남는다. 볼륨을 지우면 색인이 사라진다.

`db-redis` 컨테이너는 이 프로젝트에서 쓰지 않는다.

---

## 2. 접속 정보

접속 값은 프로젝트 루트의 `.env`에 있다. 이 파일은 `.gitignore`에 들어 있어서 커밋되지 않는다. 암호를 문서나 채팅에 옮기지 않는다.

```
PG_HOST=localhost
PG_PORT=5432
PG_DATABASE=regulations
PG_USER=rag_reg
PG_PASSWORD=(.env에만 있음)
```

Python에서는 `python-dotenv`로 `.env`를 읽는다. 규정 코드는 `postgres` 계정을 쓰지 않는다.

---

## 3. 컨테이너 관리

```
docker ps -a --filter name=db-pg          # 상태 확인
docker start db-pg                        # 실행
docker stop db-pg                         # 중지
docker logs --tail 50 db-pg               # 최근 로그
docker exec db-pg pg_isready -U postgres  # 접속 준비 확인
```

컨테이너를 중지하면 Python 색인과 검색이 모두 안 된다. 작업 전에 `docker ps`로 실행 중인지 확인한다.

---

## 4. 처음 만든 과정 (재현용)

이미 만들어진 `db-pg`를 썼다. 다른 PC에서 다시 만들어야 할 때는 아래 순서를 따른다.

1. 이미지 실행 (새로 만들 때)
   ```
   docker run -d --name db-pg -e POSTGRES_PASSWORD=<관리자암호> -p 5432:5432 -v db-pg-data:/var/lib/postgresql/data pgvector/pgvector:pg17
   ```
2. 규정 DB와 계정 생성 (관리자 계정으로)
   ```
   docker exec db-pg psql -U postgres -c "CREATE USER rag_reg WITH PASSWORD '<규정암호>';"
   docker exec db-pg psql -U postgres -c "CREATE DATABASE regulations OWNER rag_reg;"
   ```
3. 확장 설치 (`regulations` DB에서)
   ```
   docker exec db-pg psql -U postgres -d regulations -c "CREATE EXTENSION IF NOT EXISTS vector;"
   docker exec db-pg psql -U postgres -d regulations -c "GRANT ALL ON SCHEMA public TO rag_reg;"
   ```
4. `.env`에 `PG_*` 값을 넣는다 (2절).

---

## 5. Python 패키지

`pyproject.toml`에 아래 패키지가 들어 있다.

| 패키지 | 용도 |
|---|---|
| `psycopg[binary]` | PostgreSQL 접속 |
| `pgvector` | 벡터 타입 등록 (`register_vector`) |
| `python-dotenv` | `.env` 읽기 |

접속 확인 방법은 `uv run python`에서 `psycopg.connect(...)` 후 `select current_user`를 실행하는 것이다.

---

## 6. DBeaver 연결

| 항목 | 값 |
|---|---|
| Host | `localhost` |
| Port | `5432` |
| Database | `regulations` |
| Username | `rag_reg` |
| Password | `.env`의 `PG_PASSWORD` |

`postgres` 계정으로 연결하면 다른 프로젝트 DB가 함께 보인다. 이 프로젝트 작업은 `rag_reg`로 연결한다.

연결 후 확인:
```sql
select extname, extversion from pg_extension;
```
`vector`가 보이면 정상이다.

---

## 7. 확인 기록

| 날짜 | 확인 내용 | 결과 |
|---|---|---|
| 2026.10.06 | `db-pg` 실행 (3주 전 중지 상태에서 재시작) | 정상 |
| 2026.10.06 | `rag_reg`로 TCP 접속 | 정상 (`current_user` = `rag_reg`, DB = `regulations`) |
| 2026.10.06 | pgvector 거리 계산 (`<=>`) | 정상 |
| 2026.10.06 | DBeaver 연결 | 정상 |

테이블은 아직 만들지 않았다. 다음 단계에서 만든다.

---

## 8. 주의

- `.env`는 커밋하지 않는다. `.gitignore`에 들어 있다.
- `docker volume rm db-pg-data`는 색인을 지운다. 실행하지 않는다.
- `postgres` 관리자 계정은 DB 생성과 권한 작업에만 쓴다. 앱 코드는 `rag_reg`만 쓴다.
- 암호를 바꾸면 `.env`와 DBeaver 연결을 함께 고친다.
