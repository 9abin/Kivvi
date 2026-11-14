# 261006-1133 프로젝트 관리 구조 도입

- 관련 TODO: 없음 (착수 작업)
- 변경 범위: AGENTS.md, CLAUDE.md, docs/, .gitignore

## 요약
문서 관리 방식(AGENTS → 00-status → 기준 문서 → todo/reports)을 도입했다.
내용은 원본 기획(Google Docs) 중 B. 조항 수정 에이전트와 1주차 검토 결과로만 채웠다.
코드는 없다. 다음 TODO 2건(법률 DB 연동 합의, 점검 규칙 정의)을 상세화했다.

## 주요 결정
| 결정 | 이유 | 대안 |
|---|---|---|
| 이 저장소는 조항 수정 에이전트(박형건) 전용 | 담당 분리: 법률 DB 한석희, 챗봇 RAG 김가빈 | 팀 전체 저장소 |
| 법률 DB는 읽기만 하고 형식은 합의로 정함 | 구축 담당이 따로 있음. 조인 키가 맞아야 핵심 기능이 동작 | 이 저장소에서 법령 테이블 정의 |
| 원칙은 원본 기획의 결정 사항만 둠 | 다른 프로젝트 원칙을 옮기지 않기 위해 | 일반 원칙 추가 |
| 코드 폴더 구조는 보류 | 법률 DB 형식과 점검 규칙이 정해진 뒤 결정 | 지금 골격 생성 |
| 기존 `data/`, `info/`, `기획서/`는 그대로 둠 | 경로가 바뀌면 팀원 작업과 어긋남 | 지금 이동 |
| git 초기화 보류 | 팀 원격 저장소 여부 미확인 | 지금 `git init` |

## 변경 사항
- 신규: `AGENTS.md`, `CLAUDE.md`, `.gitignore`
- 신규: `docs/archive/plans/00-status.md`, `01-folder-architecture.md`, `02-specs.md`, `03-product-plan.md`, `04-glossary.md`
- 신규: `docs/todo/` (목록, `law-db-interface`, `error-rules`, `ingest-articles`, 템플릿), `docs/reports/` (템플릿, 이 리포트)
- 수정(같은 날): 처음 만든 코드 골격(packages·services·apps 등), 학습 모드 규칙, 코딩 규칙 문서, 출력 유형 태깅 규칙은 다른 프로젝트에서 가져온 것이라 삭제

## 후속 작업
- [ ] 원본 Google Docs와 이 저장소 문서 중 정본 합의
- [ ] git 저장소 초기화, 원격 연결
- [ ] 이민형 역할 확인 (원본 기획: 골든셋·오류 주입셋)
