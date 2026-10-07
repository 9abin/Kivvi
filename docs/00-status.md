# 00. 현재 상태

새 세션은 `AGENTS.md` 다음에 이 문서를 읽는다. 작업마다 갱신한다. 40줄 이내, 본문 복제 금지(링크만).

- 마지막 갱신: 261007

## 단계
구현 중. 원문 파싱(90건) → 인용 엣지·구조 검사(1주차 §C 구조 대상 11/11) → 벡터 색인(BGE-m3, 4,467 청크) → 대상 불일치 후보(설명구+reranker, 정답 5/5) → 정답 원장 23/23. 실행 기록(역추적), 1차 결과 페이지 `out/report/index.html`(`scripts/make_report.py`). 개정문 적용·신·구조문대비표 v0(S01~S03). 변경안 점검·검토 화면·LLM 선정은 아직 없다.
이 저장소: 조항 수정 에이전트(박형건). 법률 DB는 한석희, 챗봇 RAG는 김가빈. ([AGENTS §1](../AGENTS.md))

## 핵심 결정
- 영향 관계는 미리 연결한 관련 조항 표(근거·출처·신뢰도·검증 상태)로, 수정할 때는 조회만. 그래프 DB 불필요, 임베딩은 관계 구축 보조 → [261006-1744-11](reports/261006-1744-11-relations.md)
- 형태: 챗봇 아님, 사건 기반 점검 + 검토 화면. 수정은 초안 사본에 신·구조문대비표, 변경 단위 수락·거절. PoC는 "설치 첫날" 시나리오 → [261006-1414-06](reports/261006-1414-06-operation-poc.md), [03](03-product-plan.md)
- DB 4계층(원문·관계·의미·점검), `edge` 통합, 후보 수집 후 정밀 점검, 기준선 + 변경분 운영 → [261006-1340-04](reports/261006-1340-04-db-design-baseline.md), [02](02-specs.md)
- 기법: TAG+SQL 중심, RAG는 의미 검사, CAG는 수정안·기준선, 그래프는 RDB 엣지로 (제안) → [261006-1152-03](reports/261006-1152-03-rag-cag-tag-graph.md)
- 관리 구조와 저장소 범위: [261006-1133-01-project-structure](reports/261006-1133-01-project-structure.md)
- 원본 기획 결정: 법령은 RDB로 구성해 폐쇄망에 변경분 반입, 충돌은 자동 판정하지 않고 양쪽 표시, 수정안은 참고 문안 ([03](03-product-plan.md))

## 열린 질문 / 외부 대기
- 규정 원문 DB 공유 (김가빈과 합의) → [regulation-db-sharing](todo/regulation-db-sharing.md), 검토 [261006-1148-02](reports/261006-1148-02-chatbot-db-relation.md)
- 법률 DB 형식·엔진 (한석희와 합의) → [law-db-interface](todo/law-db-interface.md)
- 조문 ID 임시안 공유·합의 (김가빈·한석희) → [261006-1428-07](reports/261006-1428-07-article-parsing.md)
- 오류 주입셋 담당 (원본 기획: 이민형)
- 로컬 LLM, GPU 환경, 대상 법령 범위 ([03 미결](03-product-plan.md))
- git: 로컬 저장소(main, 첫 커밋 952e720). 원격 없음 → 팀 공유 위치 정하기

## 다음 액션
1. 변경안 점검 v0: 대비표의 번호 대응 + 관련 조항 표 → 깨지는 인용, 시나리오 S01~S03 채점
2. 가빈 corrections.json 155건 받아 독립 평가 ([goldset](todo/goldset.md)), 합의: [law-db-interface](todo/law-db-interface.md)(한석희), [regulation-db-sharing](todo/regulation-db-sharing.md)(김가빈); 법령 목록(130종) 한석희 전달, 오류 주입으로 독립 검증, 로컬 LLM 후보 비교 ([error-rules](todo/error-rules.md))

## 최근 변경
- 261007: 개정문 적용·신·구조문대비표 v0, 결과 페이지 대비표 탭 → [261007-1145-14](reports/261007-1145-14-amendment-compare.md)
- 261007: RFP(석유공사) 벤치마킹, 실행 기록(out/runs·eval/runs), 1차 결과 페이지 → [261007-1120-13](reports/261007-1120-13-rfp-benchmark-provenance.md)
- 261007: 로컬 규정 DB(reg-db, 가빈 DB와 같은 계정) 준비, 읽기 전용 접속. 가빈 덤프 복원 대기 → [regulation-db-sharing](todo/regulation-db-sharing.md)
- 261007: 정답 원장·채점 틀, 항번호 중복 점검, 제12-1조 파싱. week1 16/16, 가빈 정리본 7/7(대상 불일치는 낮음 등급) → [261007-1028-12](reports/261007-1028-12-goldset-eval.md)
- 261007: 흐름 이해용 노트북 (134셀, 전체 청킹 절), 로컬 git 저장소
- 261006: 관련 조항 표, 표본 검수(조문 인용 57%→80%) 후 파서·인용 수정 5종 → [261006-1744-11](reports/261006-1744-11-relations.md)
- 261006: 의미 검사 2단계 [261006-1603-10](reports/261006-1603-10-semantic-check.md), 벡터 색인 [261006-1554-09](reports/261006-1554-09-vector-index.md), 인용 추출·구조 검사 v0, 1주차 정답 대조 스크립트, 법령 목록 130종 → [261006-1544-08](reports/261006-1544-08-citation-edges.md)
- 261006: 원문 추출기·조문 파서·전처리, 90건 파싱, 원문 확인 후보(결번·순서 4건, 머리말 불일치 4건), 학습 문서 `learning/261006-article-parsing.html` → [261006-1428-07](reports/261006-1428-07-article-parsing.md)
- ~261005: 규정 90건 수집, 담당 21건 1차 검토, 외부 법령 참고 정리 (`data/한국벤처투자_내부규정/*_노션용.md`)
