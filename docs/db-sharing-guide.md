# 팀 공유 DB 안내 (DBeaver)

**권장: 방법 B — 박형건 PC에 DBeaver로 접속** (팀원은 DBeaver만 있으면 됨, 261012 와이파이 주소로 접속 확인). 팀원용 한 장 안내: https://claude.ai/artifact/CqqnKy39Ty39Q4KJhRmAAg · 방법 A(Docker 이미지)는 박형건 PC 없이 써야 할 때.

## 방법 A. Docker 이미지 (각자 PC)

데이터가 든 PostgreSQL 이미지 `mini-cns-db:{날짜}`를 받아 실행한다. 처음 실행 때 1~2분 동안 덤프를 복원한다.

```bash
# db/ 폴더에서. .env 파일: POSTGRES_USER=mini  POSTGRES_PASSWORD=내가정한값
docker compose -f docker-compose.team.yml up -d
docker logs mini-cns-db          # "[mini-cns] 준비 완료"가 보이면 끝
```
DBeaver: Host `localhost` · Port `15432` · Database `regulations` · 계정은 위 .env 값 (내 PC 전용이라 쓰기도 가능, 원본에는 영향 없음)

새 버전을 받을 때: `docker compose -f docker-compose.team.yml down -v` → 새 태그로 `up -d` (내 PC의 이전 데이터는 지워짐)

만드는 쪽(박형건): 파이프라인 → `scripts/amend_load.py` → `uv run --extra db python scripts/db_image.py --test` (덤프 → 이미지 → 임시 컨테이너로 복원 확인)

## 방법 B. 박형건 PC에 접속 (같은 와이파이)

- 이 PC는 WSL `networkingMode=mirrored`라 Windows 방화벽 규칙(`MINI CNS reg-db 5432`)과 Hyper-V 방화벽 규칙(`New-NetFirewallHyperVRule -Name MINI-CNS-5432 … -VMCreatorId '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' -LocalPorts 5432`)이 둘 다 필요
- 이 PC에서 자기 와이파이 주소로 시험하려면 `~/.wslconfig`의 `[experimental] hostAddressLoopback=true` + `wsl --shutdown` (261012 적용, 백업 `.wslconfig.bak-261012`). WSL을 다시 시작하면 `docker compose up -d`로 reg-db를 다시 켠다 (재시작 정책 없음)
- 다 쓴 뒤: `Remove-NetFirewallRule -DisplayName "MINI CNS reg-db 5432"`, `Remove-NetFirewallHyperVRule -Name MINI-CNS-5432`

- 작성: 261012 박형건
- DB: 박형건 PC의 `reg-db` (PostgreSQL 17 + pgvector). 가빈 챗봇 DB 사본(`public`) + 조항 수정 에이전트 데이터(`amend`)
- 계정: `mini_cns_team` — **읽기 전용** (조회만, 쓰기·삭제·표 만들기 불가). 비밀번호는 박형건에게 따로 받는다 (저장소에 없음)
- 같은 와이파이에 있고 박형건 PC가 켜져 있을 때만 접속된다

## 1. 박형건 PC에서 할 일 (처음 한 번)

```powershell
# 관리자 PowerShell. 이 PC 네트워크가 '공용'이라 Any로 연다 (회사·카페 와이파이면 쓴 뒤 지운다)
New-NetFirewallRule -DisplayName "MINI CNS reg-db 5432" -Direction Inbound -Protocol TCP -LocalPort 5432 -Action Allow -Profile Any
# 다 쓰면
Remove-NetFirewallRule -DisplayName "MINI CNS reg-db 5432"
```
```bash
docker compose up -d                                        # DB 켜기
uv run --extra db python scripts/amend_load.py              # 최신 결과 적재 (파이프라인 다시 돌린 뒤)
uv run --extra db python scripts/db_team_account.py         # 팀 계정 (처음 한 번, 접속 정보는 .env.team)
ipconfig                                                    # 무선 LAN IPv4 주소 → 팀에 알림 (와이파이를 바꾸면 달라짐)
```

## 2. 팀원 DBeaver 설정

1. 새 연결 → **PostgreSQL**
2. Host: 박형건 PC IP (예 `192.168.0.40`) · Port `5432` · Database `regulations`
3. Username `mini_cns_team` · Password: 따로 받은 값
4. **PostgreSQL 탭 → "Show all databases" 끄기**, Schemas 필터에 `public`, `amend`
5. 드라이버 받기 묻으면 받는다 → Test Connection → 완료

## 3. 무엇이 있나

| 스키마 | 표 | 내용 |
|---|---|---|
| `public` (가빈) | `documents` 90, `chunks` 4,774 | 규정 문서, 조·항 청크(벡터 포함). **점검 코드는 `body_search`·`corrections_note`·`cites`·`body_tokens`를 읽지 않는다** (보정본) |
| `amend` (박형건) | `chunk_detail` 7,382 | 항·호·목 원문 + 가빈 `chunk_id` 대응 |
| | `edge` 1,884 | 관련 조항 표 (그래프 간선) |
| | `finding` 256 | 점검 결과 (오류 후보) |
| | `changeset`·`impact` | 개정안과 영향 (S01~S03) |
| | `article_version`, `decision_log` | 조문 사본, 사람 결정 기록 |
| | 뷰 `v_inbound`, `v_chunk_open_change`, `v_chunk_unmatched`, `v_decision_state` | 인용받는 곳, 개정 진행 중 청크, 대응 확인, 결정 상태 |

표·칸 설명은 DB에 들어 있어 DBeaver 속성 창에서 보인다. 전체 그림: [데이터 지도](https://claude.ai/artifact/Nxtp7jxhxCNLA5nDVxBeQo) · 정의: `sql/amend_schema.sql`

## 4. 자주 쓰는 질의

```sql
-- 직제규정 제7조를 인용하는 곳 (조를 고치기 전에)
SELECT src_reg, src_label, evidence, context FROM amend.v_inbound WHERE target = '24184:7';

-- 챗봇 청크 하나에 걸린 점검 결과
SELECT type, quote, suggest, note FROM amend.finding WHERE chunk_id = '24024_제37조_1항';

-- 규정별 점검 결과 수
SELECT d.title, count(*) FROM amend.finding f JOIN public.documents d ON d.doc_id = f.reg_id GROUP BY 1 ORDER BY 2 DESC;

-- 삭제됐거나 없는 조를 가리키는 인용
SELECT src, evidence, target, check_status FROM amend.edge WHERE check_status IN ('missing_article', 'deleted_target');
```

## 5. 알아 둘 것

- **ER 다이어그램에 선이 거의 없다**: 가빈 표 재적재를 막지 않으려고 `public` 쪽 외래키를 걸지 않았다 ([31](reports/261012-1540-31-amend-schema.md)). 연결 키는 `chunk_detail.chunk_id = chunks.chunk_id`, `edge.target = chunk_detail.article_id`, `finding.src_id = chunk_detail.item_id`, `*.reg_id = documents.doc_id`. DBeaver에서 보려면 ER 다이어그램 → 가상 키(Virtual key)로 추가
- 데이터는 박형건이 파이프라인을 다시 돌리고 `amend_load.py`를 실행할 때만 바뀐다. 기준 시점은 `amend.finding.run` (실행 ID)
- 법률 DB(한석희 SQLite)는 이 DB에 없다. DBeaver에서 SQLite로 따로 연다 (`data/law_rag.sqlite3`)
- 원문(HWP)은 KVIC 저작물. DB 내용을 외부로 내보낼 때는 원문 대신 ALIO 링크 (AGENTS §4)
