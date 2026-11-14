# 접속 정보 한눈에 (주소·IP·포트·계정·링크)

- 갱신: 261014 박형건 (와이파이 IP 192.168.0.28 → 192.168.0.21, 재부팅 때 바뀜). **비밀번호는 여기 적지 않는다** (어느 파일에 있는지만)
- 와이파이 IP는 공유기가 나눠 주는 값이라 와이파이를 바꾸거나 오래 지나면 바뀐다 → `ipconfig`의 "무선 LAN 어댑터 Wi-Fi" IPv4로 다시 확인

## 1. 네트워크 (같은 와이파이 192.168.0.x)

| 누구 | IP | 비고 |
|---|---|---|
| 박형건 PC | `192.168.0.21` | 우리 DB(reg-db)가 도는 PC |
| 김가빈 PC | `192.168.0.84` | 가빈 챗봇 DB 원본 (`db-pg` 컨테이너) |
| 공유기 | `192.168.0.1` | 기본 게이트웨이. DB 아님 |

## 2. 데이터베이스

| DB | 주소 | DB 이름 | 계정 | 비밀번호 위치 | 권한 |
|---|---|---|---|---|---|
| **우리 DB (reg-db)** — 박형건 PC 안에서 | `localhost:5432` | `regulations` | `rag_reg` | `.env` (`PG_PASSWORD`) | 관리자 |
| **우리 DB (reg-db)** — 팀원이 DBeaver로 | `192.168.0.21:5432` | `regulations` | `mini_cns_team` | `.env.team` (`TEAM_PASSWORD`) | 조회 전용 |
| 가빈 DB 원본 | `192.168.0.84:5432` | `regulations` | `rag_reg` | `.env`와 같은 값 | 우리는 읽기만 (접속하려면 가빈 PC 방화벽 필요) |
| 법률 DB (한석희) | 파일 `data/law_rag.sqlite3` (261012 수집본, 법령 284 / 이전본 `law_rag.20261007.sqlite3`) | — | — | — | 읽기 전용 (SQLite, DBeaver에서 파일로 연다) |

우리 DB 안의 스키마: `public` = 가빈 챗봇 사본 (규정 90, 청크 4,810 — 261012 16:26 가빈 수정본, 덤프 `data/external/gabin/regulations_261012b.dump`) · `amend` = 우리 결과 (관계 1,884, 점검 결과 256, 항·호·목 7,382)

## 3. Docker (박형건 PC)

| 컨테이너 | 이미지 | 포트 | 데이터 볼륨 | 켜기 |
|---|---|---|---|---|
| `reg-db` | `pgvector/pgvector:pg17` | `5432` | `mini_cns_reg-db-data` | 저장소 폴더에서 `docker compose up -d` (PC를 다시 켜면 직접 켜야 함). Docker Desktop을 막 켠 직후엔 5432가 안 붙을 수 있음 → `docker ps`에 `0.0.0.0:5432->5432`가 없으면 `docker start reg-db` 한 번 더 (261013) |
| `db-pg` | `pgvector/pgvector:pg17` | `5432` | `db-pg-data` | 다른 실습 프로젝트 DB. **reg-db와 포트가 같아 동시에 켜지 않는다** |
| `coding_platform-ollama-1` | — | 11434 (내부) | — | 다른 프로젝트 |

- 로컬 LLM(Ollama): `http://localhost:11434` (`src/regcheck/nl.py`, 모델 `qwen2.5:7b`)
- 검토 화면: `uv run streamlit run app/review_app.py` → `http://localhost:8501`
- 팀 공유 DB 이미지(방법 A, 아직 올리지 않음): 로컬 `mini-cns-db:261012`, 올릴 이름 `ghcr.io/broogun/mini-cns-db`

## 4. 박형건 PC 설정 (팀원 접속용)

| 항목 | 값 | 지우기 |
|---|---|---|
| Windows 방화벽 규칙 | `MINI CNS reg-db 5432` (TCP 5432 들어옴, 모든 프로필) | `Remove-NetFirewallRule -DisplayName "MINI CNS reg-db 5432"` |
| Hyper-V 방화벽 규칙 (WSL mirrored용) | `MINI-CNS-5432` | `Remove-NetFirewallHyperVRule -Name MINI-CNS-5432` |
| `C:\Users\Admin\.wslconfig` | `networkingMode=mirrored`, `[experimental] hostAddressLoopback=true` | 백업 `.wslconfig.bak-261012`로 되돌린 뒤 `wsl --shutdown` |

## 5. 비밀 정보가 든 파일 (모두 git 제외)

| 파일 | 내용 |
|---|---|
| `.env` | `PG_HOST`·`PG_PORT`·`PG_DATABASE`·`PG_USER`·`PG_PASSWORD` (관리자, 가빈 DB와 같은 값 — 대화에 노출된 적 있어 바꾸기 권장) |
| `.env.team` | 팀 조회 계정 `mini_cns_team`의 비밀번호 (`scripts/db_team_account.py --rotate`로 바꿈) |

## 6. 링크

| 무엇 | 주소 |
|---|---|
| 원본 기획 (Google Docs) | https://docs.google.com/document/d/1R_LI4P210kNSGdPyo2dS8vZjy5vz2heoGuUNAWFMLEM/edit |
| 가빈 저장소 (GitHub, 공개) | https://github.com/9abin/kvic-internal-regulations-review |
| 팀원용 DB 접속 가이드 | https://claude.ai/artifact/CqqnKy39Ty39Q4KJhRmAAg |
| 챗봇 통합 결정과 요청 (가빈에게) | https://claude.ai/artifact/FUmVQNaT4Y8yFDW6Av2bWs |
| 팀 챗봇 UI 디자인 (Figma, 진행 중) | https://www.figma.com/design/yiXHZ59iuuCnoRwbuukjRw/?node-id=1-2 |
| 데이터 지도 (DB 구조·그래프) | https://claude.ai/artifact/Nxtp7jxhxCNLA5nDVxBeQo |
| 학습 문서 | `docs/archive/learning/261012-amendment-agent.html` (Chrome에서 열기) |

- 이 저장소 git: 브랜치 `work/261007`. 원격 `origin`이 **가빈 저장소로 잡혀 있음** — push하면 공개 저장소로 간다. 우리 원격은 아직 없음
- artifact 링크는 박형건 계정 비공개. 다른 사람에게 보낼 때는 각 페이지 공유 메뉴에서 공유
