# TODO: 챗봇 통합 (권한형 개정 기능)

- 상태: `doing`
- 우선순위: P0
- 담당: 박형건 ↔ 김가빈
- 관련 문서: [합의안](../chatbot-integration-proposal.md), [261012-1500-29](../reports/261012-1500-29-chatbot-integration.md), [regulation-db-sharing](regulation-db-sharing.md)

## 목표
챗봇 화면 하나에서 editor 권한자가 답변의 인용 조항으로 개정안을 작성·검토한다.

## 배경
261012 팀 방향 변경: 별도 도구 대신 기존 챗봇에 권한형 기능으로 붙인다. 가빈 DB와 키·스키마·권한을 맞춰야 한다.

## 완료 조건
- [ ] 합의안 ①~⑦ 결정 기록
- [x] 가빈 덤프 복원 (261012, `data/external/gabin/regulations_261012.dump`, git 제외)
- [ ] `amend` 스키마 DDL + `chunk_map` 적재
- [x] 파서 수정: hwpx 머리말 속 본문, 한 줄에 붙은 항·호 → 우리 몫 0 (남은 3건은 원문 사정) [261012-1525-30-parser-inline-paras](../reports/261012-1525-30-parser-inline-paras.md)
- [ ] 멀티페이지 앱: 질의응답 → 「개정안 작성」(chunk_id 전달), editor 역할 확인
- [ ] 시연 리허설 (S01 육아휴직, 직제 개편)

## 진행 기록
- 261012: 가빈 스키마·앱 확인, ID 대응 실측 99.2%, 합의안·리포트 작성
- 261012: 가빈 DB 직접 접속(같은 와이파이, `PG_HOST` 환경변수로만 지정, .env 그대로). chunks 4,774·별표·별지 195. 조문 대응 99.2% 동일, 내규관리규정 제10조까지 동일
- 261012: 원격 pg_dump(reg-db 컨테이너의 pg17 클라이언트) → 21.6MB, 로컬 reg-db 복원. chunks 4,774(벡터 4,551)·documents 90, 색인 6개(HNSW 포함) 확인
- 261012: 파서 수정 → 3개 규정 24개 조 복구, 항 차이 29→3, 채점 그대로 [261012-1525-30-parser-inline-paras](../reports/261012-1525-30-parser-inline-paras.md)

## 막힌 점 / 필요한 것
- 가빈: 합의안 검토, 내규관리규정 제11~26조 누락 확인, 확인 끝나면 5432 방화벽 규칙 제거
