# 한국벤처투자 내부규정 영향 분석 (Kivvi)

내부규정의 한 조를 고칠 때, **함께 확인해야 할 다른 조문을 찾아 근거와 함께 보여 주는** 도구입니다. 최종 판단은 사람이 합니다.

한국벤처투자 내부규정 90건(HWP 84, HWPX 3, PDF 3)을 조 단위로 정리하고, 조 사이의 연결(인용·위임·용어)을 규정 영향 온톨로지로 만든 뒤, 개정안이 들어오면 영향 후보를 찾습니다.

## 무엇을 하나

1. **규정 정리**: 원문을 추출해 조·항·호 단위로 나누고, 조문 간 참조를 해석합니다. 확정된 오류는 보정 목록으로 적용합니다.
2. **영향 후보 찾기**: 고친 조를 입력하면 세 가지 방법으로 후보를 모읍니다.
   - 규정 영향 온톨로지 경로
   - 개정문 낱말 일치
   - 의미 검색(RAG)
3. **거르고 근거 쓰기**: 판단 모델이 규칙 후보를 거르고, LLM(Luna)이 의미 검색 후보를 고른 뒤 위쪽 후보의 근거를 설명합니다.
4. **사람이 검토**: 담당자가 후보마다 검토 완료와 의견을 남기고 저장합니다. 저장한 검토는 검토 내역에서 다시 봅니다.

## 폴더 구조

```
app/                   웹 서버와 화면 (Kivvi: app/web/edit-review.*)
src/                   추출·청킹·참조 해석·보정, 영향 후보 파이프라인(regcheck), 검색·평가
scripts/               구축·실험 실행 스크립트
eval/                  평가셋과 실험 결과
data/manual/           사람이 정한 보정 목록
configs/               실험 설정
docs/                  현재 유지하는 설명 문서
docs/reports/          작업 기록(날짜·번호순)
docs/archive/          지난 계획서·실험 안내·챗봇 연동 기록·작업 중 만든 임시 파일(현재와 다를 수 있음)
ontology-search/       온톨로지 검색만 떼어 낸 독립 실행 폴더
한국벤처투자_내부규정/     규정 원본 90건
config.py, run.py      경로·설정, 전처리 실행 진입점
```

현재 설명 문서: [전처리 규칙](docs/전처리_규칙.md) · [검색 설계](docs/검색_설계.md) · [답변 설계](docs/답변_설계.md) · [질의 흐름](docs/질의_흐름.md) · [충돌 정의](docs/06-conflict-definition.md) · [용어집](docs/04-glossary.md) · [DB 설정](docs/DB_설정.md)

## 환경 조건

| 항목 | 조건 |
|---|---|
| Python | **3.12 이상** (`pyproject.toml`의 `requires-python`) |
| 패키지 관리 | **uv** (`uv.lock`으로 버전 고정) |
| 규정 원본 | 저장소 안의 `한국벤처투자_내부규정/` (각 규정은 `고유번호_문서명` 폴더, 폴더명 끝 숫자 = 알리오 고유번호) |
| 선택 | 의미 검색 색인, `OPENAI_API_KEY`(Luna), 판단 모델. 없으면 영향 분석 결과가 일부만 나옵니다 |

## 빠른 시작: 개정안 검토 화면 (Kivvi)

**처음 한 번 준비** (생성물 `out/`은 git에 올라가지 않아 각자 만듭니다. 규정 원문은 저장소 안의 `한국벤처투자_내부규정/` 폴더를 씁니다)

```powershell
uv sync
uv run python scripts/parse_regs.py --all        # 규정 90건 → out/parsed/
uv run python scripts/extract_edges.py --all     # 조항 연결 추출
uv run python scripts/build_kg.py                # 지식 그래프 → out/kg/
```

**서버 켜기**

```powershell
uv run --extra db python app/web_server.py --review-only
```

`http://127.0.0.1:8765/edit-review.html`을 엽니다. 화면은 다섯 가지입니다.

| 메뉴 | 하는 일 |
|---|---|
| 홈 | 규정 검색, 진행 안내, 최근 검토 |
| 규정 탐색 | 규정 90건 본문 읽기(읽기 전용) |
| 영향 분석 | 1 규정 선택 → 2 조항 선택·수정안 작성 → 3 수정 전·후 비교 → 4 영향 확인·검토 |
| 검토 내역 | 저장한 검토를 다시 열어 체크·의견을 고치거나 삭제 |
| 설정, 알림, 프로필 | 아직 연결된 기능이 없습니다(눌러도 안내만 뜹니다) |

- 영향 분석은 개정 영향 온톨로지 경로와 개정문 낱말 일치(판단 모델로 거름), 의미 검색(Luna가 고름)으로 후보를 찾아 첫 화면에 10개(확정급은 10개를 넘어도 포함)를 보이고 나머지는 「더 보기」에 둡니다.
- 규정 원문 DB와 온톨로지 DB는 읽기 전용입니다. 분석 초안은 `out/changes/<id>/changeset.json`, 4단계에서 「검토 저장」을 누른 검토는 같은 폴더의 `review.json`에 저장됩니다(git 제외). 검토 삭제는 `review.json`만 지웁니다.
- 서버 주소: 조항 목록 `GET /api/edit/catalog`, 영향 분석 `POST /api/edit/review`, 검토 저장 `POST /api/edit/save`, 검토 삭제 `POST /api/edit/delete`, 검토 내역 `GET /api/edit/history`.

**위 준비만 하면 되는 것과 아닌 것**

- 홈, 규정 탐색, 조항 수정, 수정 전·후 비교, 검토 내역은 위 준비만으로 동작합니다.
- 영향 분석 결과는 아래 세 가지가 없으면 일부만 나옵니다. 꺼진 단계가 있으면 4단계 위에 안내 문구가 뜨고, 판단 모델이 꺼지면 규칙 후보를 거르지 않고 모두 보여 줍니다.
  - **의미 검색 색인**: `uv run --extra vector python scripts/build_vectors.py`로 `out/vector/`를 만듭니다. Ollama에 `bge-m3`가 있으면 `uv run python scripts/build_vectors.py --ollama bge-m3`도 됩니다.
  - **Luna**: `.env` 또는 서버 환경에 `OPENAI_API_KEY`를 둡니다. 이때 개정 내용과 후보 조항이 외부 API로 전송됩니다.
  - **판단 모델**: 서버를 켜기 전에 환경변수 `IMPACT_DECIDE_PYTHON`(판단 모델용 Python 실행 파일)과 `IMPACT_DECIDE_MODEL`(모델 폴더)을 지정합니다. 설정 예와 주의점은 리포트 99(`docs/reports/261113-1700-99-web-run-pipeline.md`)에 있습니다.
- **글꼴**: Pretendard 파일(`app/web/fonts/PretendardVariable.woff2`)은 저장소에 없습니다. 받는 방법은 `app/web/fonts/README.md`에 있고, 없으면 맑은 고딕으로 보입니다.


## 전처리 (`run.py`)

```powershell
uv sync
uv run python run.py                  # 추출 → 청킹 → 참조 해석 → 보정 적용 → out/ 저장
uv run python -m unittest discover tests
```

`run.py`가 하는 일: 원본 90건 텍스트 추출(`out/text/`) → 조 단위 청킹과 부칙 분리 → 약칭 사전과 참조 해석 → 옛 규정 참조 판정 → 확정된 보정 적용 → 보정 검증(`경고:` 출력) → `out/` 저장. 상세 규칙은 [전처리 규칙](docs/전처리_규칙.md)에 있습니다.

보정 목록은 `data/manual/`에 있습니다. `corrections.json`은 사람이 판정한 보정 기록(오탈자 치환 쌍, 참조 정정, 근거 없음, 옛 규정), `manual_chunks.jsonl`은 조문 없는 문서의 수동 청크입니다. 보정 상태는 `확정(…)`은 적용, `후보(…)`는 적용하지 않음, `없음(…)`은 근거 없음으로 표시합니다.

## 재현할 때 주의

- 원본 파일을 바꾸면 보정 목록의 원문·대상 ID가 맞지 않을 수 있습니다. `run.py`의 `경고:` 출력을 확인하세요.
- `out/`은 실행할 때마다 다시 만들어지며 git에 올라가지 않습니다. 직접 수정하지 마세요.
- 외부 법령(90건 밖)을 참고하는 항목은 이 저장소에서 처리하지 않습니다.
- `docs/archive/`와 `docs/reports/`의 오래된 문서는 당시 기준이라 지금 구현과 다를 수 있습니다.
