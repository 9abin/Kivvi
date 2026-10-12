# 01. 폴더 구조

```
MINI_CNS/
├─ AGENTS.md / CLAUDE.md   # 에이전트 진입점
├─ docs/                   # 기준 문서와 작업 기록
│  ├─ 00~04                # 현재 상태, 구조, 스펙, 제품 계획, 용어집
│  ├─ labeling-guideline.md # 오류 원장 라벨링 기준
│  ├─ todo/                # 할 일
│  ├─ reports/             # 완료한 일, 주요 결정
│  └─ learning/            # 학습 문서 (cognitive-teacher, 참고용)
├─ src/regcheck/           # 코드 (Python 패키지)
│  ├─ extract.py           # HWP·HWPX·PDF → 문단 텍스트
│  ├─ parse.py             # 문단 → 본문 조·항·호·목, 부칙, 별표, 별지
│  ├─ normalize.py         # 비교·검색용 정규화 (원문은 보존)
│  ├─ citations.py         # 인용 추출 → 엣지, 대상 존재·제목·규정명 확인
│  ├─ db.py                # 규정 원문 DB(가빈 DB 로컬 사본) 접속. 기본 읽기 전용, 보정 칸 읽기 금지
│  ├─ amend_db.py          # amend 스키마 적재 (가빈 chunk_id 대응, amend_builder 역할로 쓰기)
│  ├─ findings.py          # 점검 결과 통합 형식(원문 위치 고정), 항번호 중복, 정답 매칭 규칙
│  ├─ relations.py         # 관련 조항 표 (근거·출처·신뢰도·검증 상태·원문 버전), 조회
│  └─ vectors.py           # 항 단위 청크, BGE-m3 임베딩, 검색 (uv sync --extra vector)
│  ├─ build.py             # 메타데이터(규정ID·위계·개정일·출처) + 조문 ID → JSON
├─ scripts/                # parse_regs.py → extract_edges.py → eval_week1.py → build_relations.py / build_vectors.py → vector_check.py → semantic_check.py → build_findings.py → score.py → make_report.py, 개정 초안: amend.py → check_change.py (정답: make_ledger.py, import_corrections.py)
├─ eval/                   # 정답 원장 ledger/*.jsonl, 확인 대기 review_queue.csv, 검토 결정 review_log.jsonl(덧붙이기만), 채점 기록 runs/, 정답셋 (week1_citations.json), 관계 표본 검수(relation_audit*.jsonl), 사람 결정(relation_decisions.json). 점검 코드는 정답을 읽지 않는다
├─ sql/amend_schema.sql   # amend 스키마 (개정 작업 표·뷰·역할). 적재: scripts/amend_load.py
├─ docker-compose.yml      # reg-db: 규정 원문 DB 로컬 사본 (pgvector:pg17, 접속 정보는 .env)
├─ app/                    # review_app.py: 검토 화면 (uv run streamlit run app/review_app.py, uv sync --group app)
├─ notebooks/              # 01-pipeline-walkthrough.ipynb: 흐름을 단계별로 따라가기 (생성기 scripts/make_notebook.py, uv sync --group notebook)
├─ tests/                  # pytest
├─ out/                    # 생성물 (git 제외): parsed/{규정ID}.json, edges/edges.jsonl, edges/laws_all.tsv, vector/{모델}/, runs/(실행 기록), changes/{id}/(개정 초안·대비표·영향 점검), report/index.html(1차 결과 페이지)
├─ data/
│  └─ 한국벤처투자_내부규정/  # 원문 90건 + 수집 목록 + 1주차 검토 결과(*_노션용.md)
├─ info/                   # 배경 조사 자료 (RFP, 보도자료). 기준 문서 아님
└─ 기획서/                  # 제출용 기획서 (docx)
```

DB: `docker compose up -d` → `scripts/db_restore.py 덤프` → `uv run --extra db python scripts/db_check.py` → `scripts/amend_load.py` (amend 스키마). 전체 점검: `scripts/audit.py` (구조·그래프를 원문과 대조)
실행: `uv sync` → `uv run pytest` → `scripts/parse_regs.py --all` → `scripts/extract_edges.py [--all]` → `scripts/eval_week1.py`.
점검·DB 적재 모듈은 엔진 합의 후 `src/regcheck/` 아래에 추가한다.

## 기존 자료

- `data/한국벤처투자_내부규정/다운로드목록.json`, `파일목록.md`: 수집 목록. `다운로드목록.json`의 `path`는 예전 PC 경로라 쓰지 않는다.
- `data/한국벤처투자_내부규정/*_노션용.md`: 1주차 검토 결과(담당 21건). 실제 사례 정답의 원천.
  - `검토결과_노션용.md`: 충돌·중복·오인용·표기 오류 원본 목록 (A·B·C·D 분류)
  - `최종_문제목록_노션용.md`: 같은 내용을 처리 방식별로 재분류 (F 고칠 수 있음 / J 판단 가능 / X 판단 불가)
  - `외부법령_참고_노션용.md`: 규정이 인용·의존하는 외부 법령 (E1~E5)
