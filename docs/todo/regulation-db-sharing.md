# TODO: 규정 원문 DB 공유 합의 (챗봇과)

- 상태: `todo`
- 우선순위: P0
- 담당: 박형건 ↔ 김가빈
- 관련 문서: 02-specs.md, [261006-1148-02](../reports/261006-1148-02-chatbot-db-relation.md)

## 목표
챗봇과 수정 에이전트가 같은 규정 원문 DB와 조문 ID를 쓸지 정하고, 쓴다면 형식과 담당을 정한다.

## 배경
finding과 챗봇 답변이 같은 조문을 가리켜야 연결된다. 개정본이 들어오면 한 번 적재해 둘 다 쓰는 것이 자연스럽다.
수정안 승인은 개정이 아니므로 챗봇 DB는 공지된 개정본(시행일 기준)으로만 바뀐다 (내규관리규정 제22~24조).

## 합의할 항목
- [ ] 공유 여부와 범위 (`regulation`, `article`, `amendment`, `appendix_table`)
- [ ] 조문 ID 형식 (조·항·호·목, `제N조의M`, 부칙, 별표)
- [ ] 버전: `article` 시행일·종료일, 시행 예정 조문 처리
- [ ] 파싱·분할 담당 → [ingest-articles](ingest-articles.md)
- [ ] 임베딩 공유: 청크 단위(항 + 조 제목 접두어)·모델(BGE-m3)이 같으면 벡터를 같이 쓸 수 있음 ([261006-1554-09](../reports/261006-1554-09-vector-index.md))
- [ ] 챗봇이 승인 finding을 주석으로 쓸지, 쓴다면 읽는 형식
- [ ] 합의 결과 `02-specs.md` 반영, 리포트 기록

## 진행 기록
- 261006: TODO 생성
- 261007: 가빈 DB 접속 정보 받음(.env). 가빈 PC의 localhost라 이 PC에서 직접 접속 불가 → 이 PC에 같은 계정의 빈 DB(reg-db, pgvector:pg17)를 띄움. 접속·읽기 전용·보정 칸 차단 확인. **덤프 대기** (`docker exec db-pg pg_dump -U rag_reg -Fc regulations > regulations.dump`)

## 막힌 점 / 필요한 것
- 법률 DB 엔진([law-db-interface](law-db-interface.md))과 같은 엔진을 쓰면 규정 ↔ 법령 조인이 쉬움. 세 명이 함께 정하는 편이 낫다.
- 261007: 가빈 저장소 sql/schema.sql 확인 — documents(doc_id, title, tier, revision, source_url, file_name), chunks(chunk_id, doc_id, article, status, is_indexed, body, body_search, body_tokens, corrections_note, cites, embedding vector(1024) BGE-m3, …). 읽기 금지 칸 확정: body_search·corrections_note·cites·body_tokens(보정본 기반 여부 확인 필요). 데이터 덤프는 아직 이 PC에 없음

