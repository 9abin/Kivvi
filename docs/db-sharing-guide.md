# 팀 공유 DB 접속 안내 (DBeaver)

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
