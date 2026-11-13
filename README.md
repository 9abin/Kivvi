# 한국벤처투자 내부규정 RAG 전처리

내부규정 90건을 조 단위 청크로 만들고(장·절 제목은 메타데이터로), 조문 간 참조(cites)를 해석하고, 확정된 보정을 적용하는 전처리 파이프라인입니다.
상세 규칙은 `docs/전처리_규칙.md`를 보세요.
검색·답변(RAG 챗봇)의 추천 구성과 성능, 평가 방법은 `docs/팀_실행_안내.md`, 질문 하나가 처리되는 순서는 `docs/질의_흐름.md`에 있습니다.

## 1. 환경 조건

| 항목 | 조건 |
|---|---|
| Python | **3.12 이상** (`pyproject.toml`의 `requires-python`) |
| 패키지 관리 | **uv** (`uv.lock`으로 버전 고정) |
| 원본 위치 | 이 폴더의 **상위 폴더**에 `한국벤처투자_내부규정` 폴더가 있어야 합니다 |
| 원본 구조 | 각 규정은 `고유번호_문서명` 폴더 안에 있어야 합니다 (폴더명 끝 숫자 = 알리오 고유번호) |
| 원본 파일 | HWP 84, HWPX 3, PDF 3 (총 90건) |
| 외부 의존 | 없음 (인터넷·API 불필요) |

폴더 구조 예시:
```
한국벤처투자_내부규정_최신개정본/
├── 한국벤처투자_내부규정/          ← 원본 (90건)
│   ├── 감사규정_23234/...
│   └── ...
├── src/ app/ eval/ docs/ ...       ← 이 저장소 (RAG 챗봇 + 개정안 에이전트)
```

## 2. 실행 순서

```
$env:UV_PROJECT_ENVIRONMENT = ".venv"      # 저장소 안에 격리된 환경 사용
uv sync                                    # 패키지 설치 (처음 한 번)
.\.venv\Scripts\python.exe run.py          # 추출 → 청킹 → 참조 해석 → 보정 적용 → out/ 저장
.\.venv\Scripts\python.exe -m unittest discover tests
.\.venv\Scripts\python.exe -m streamlit run app\streamlit_app.py
.\.venv\Scripts\python.exe -m src.eval.run # 검색 평가 (PostgreSQL 색인 필요, 약 6분)
```

`run.py`가 하는 일:
1. 원본 90건 텍스트 추출 → `out/text/`
2. 조 단위 청킹(장·절 제목 분리), 부칙 분리, 삭제 조 표시
3. 약칭 사전 생성, 참조 해석과 상태 분류
4. 옛 규정 참조 자동 판정 (현행 규정명이 문맥에 있는지)
5. 확정된 보정 적용 (오탈자 치환, 참조 정정, 근거 없음 표시)
6. 보정 검증: 대상 조문과 치환 글자가 실제로 있는지 확인 (없으면 `경고:` 출력)
7. 산출물 저장

### 개정안 검토 화면 (Kivvi)

**처음 한 번 준비** (생성물 `out/`은 git에 올라가지 않아 각자 만듭니다. 규정 원문 폴더 `한국벤처투자_내부규정/`가 저장소 옆에 있어야 합니다)

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

기존 규정 도우미 웹 화면(`index.html`)을 사용 중이라면 조항을 연 뒤 **이 조항 직접 편집**으로 새 화면에 들어갈 수 있습니다.

## 3. 산출물 (`out/`)

| 파일 | 내용 |
|---|---|
| `text/{고유번호}.txt` | 문서별 추출 텍스트 |
| `chunks.jsonl` | 청크 원본 (본문, 검색용 본문, 메타데이터, cites, 정정안내) |
| `aliases.json` | 문서별 약칭 사전 |
| `cite_review.csv` | 참조 검토 목록 (외부 스킵 포함) |
| `항번호_정정목록.csv` | 원문 항 번호 중복 정정 내역 |

## 4. 보정 목록 (`data/manual/`)

| 파일 | 내용 |
|---|---|
| `corrections.json` | 사람이 판정한 보정 기록 (오탈자 치환 쌍, 참조 정정, 근거 없음, 옛 규정) |
| `manual_chunks.jsonl` | 조문 없는 문서의 수동 청크 (윤리헌장) |

- 오탈자 치환 쌍 후보를 정정안에서 뽑으려면: `uv run python -m src.corrections.typo_pairs`
- 보정 상태: `확정(…)`은 적용, `후보(…)`는 적용하지 않음, `없음(…)`은 근거 없음으로 표시

## 5. 폴더 구조

```
config.py              경로·설정 (여기서만 바꿈)
run.py                 실행 진입점
src/
  extract/             원문 추출 (hwp.py, hwpx.py, __init__.py)
  preprocess/          정제(clean.py), 메타데이터(metadata.py)
  chunk/articles.py    조·항 분리, 중복 항 번호 정정, 부칙 분리
  citations/refs.py    약칭 사전, 참조 해석, 옛 규정 판정
  corrections/
    store.py           보정 목록 읽기·검증
    apply.py           보정 적용 (검색 텍스트, 참조, 근거 없음)
    typo_pairs.py      치환 쌍 후보 추출 도구
docs/전처리_규칙.md    상세 규칙과 판정 기록
tests/                 테스트 (구조 검증 위주)
archive/               작업 중 만든 임시 파일 보관 (실행에 쓰지 않음)
```

## 6. 재현 시 주의

- 원본 파일을 바꾸면 보정 목록의 원문·대상 ID가 맞지 않을 수 있습니다. `run.py`의 `경고:` 출력을 확인하세요.
- `out/`은 실행할 때마다 다시 만들어집니다. 직접 수정하지 마세요.
- `archive/`의 파일은 작업 이력입니다. 실행에 쓰이지 않습니다.
- 외부 법령(90건 밖)을 참고하는 항목은 이 저장소에서 처리하지 않습니다(외부 DB에서 별도 작업).
- 검색 평가 질문-정답 세트는 `eval/questions.jsonl`에 있습니다(277개, 유형·세부유형 포함). 추가·수정은 이 JSONL 파일을 직접 고칩니다. `docs/평가_라벨링.md`는 라벨링 기준과 세부유형 정의를 설명하지만, 그 문서가 말하는 **Streamlit 라벨링 화면과 오류 원장(`eval/error_ledger.jsonl`)은 이 저장소에 포함되지 않았습니다.** 별도 작업으로 가져올지 판단 중입니다.
