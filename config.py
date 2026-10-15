"""경로와 설정. 여기서만 바꾸면 된다."""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 원본 규정 폴더 (폴더명 끝 숫자 = 알리오 고유번호)
SOURCE_ROOT = os.path.abspath(os.path.join(BASE_DIR, "한국벤처투자_내부규정"))
DOWNLOAD_LIST = os.path.join(SOURCE_ROOT, "다운로드목록.json")

DATA_DIR = os.path.join(BASE_DIR, "data")
MANUAL_DIR = os.path.join(DATA_DIR, "manual")
OUT_DIR = os.path.join(BASE_DIR, "out")
TEXT_DIR = os.path.join(OUT_DIR, "text")

MANUAL_CHUNKS = os.path.join(MANUAL_DIR, "manual_chunks.jsonl")   # 조문 없는 문서 등 수동 청크
CORRECTIONS = os.path.join(MANUAL_DIR, "corrections.json")        # 확정된 보정 목록 (사람 확정 후 기록)
ANNEX_OVERRIDES = os.path.join(MANUAL_DIR, "annex_overrides.json")  # 추출이 깨진 별표를 원본 표로 복원한 글 (별표 ID → 본문)
LAW_DB = os.path.join(MANUAL_DIR, "law_rag.261012.sqlite3")  # 외부 법령 원문. 없으면 외부 인용 연결을 건너뜀

# HWP 추출 시 사용할 Python (olefile 설치 필요)
HWP_SUBPROCESS_PYTHON = None  # None이면 현재 실행 중인 Python 사용

# 검색 색인 (BGE-m3 임베딩, 저장은 PostgreSQL + pgvector: docs/DB_설정.md)
# 임베딩 모델 비교(2026.10.14): 환경 변수 EMBED_MODEL로 바꾼다. DB의 벡터도 같은 모델로 만든 것이어야 한다
# (scripts/embed_variant.py가 모델별 DB 사본을 만든다). 질문용 접두어가 있는 모델은 search.py가 알아서 붙인다.
EMBED_MODEL = os.environ.get("EMBED_MODEL", "BAAI/bge-m3")  # 첫 실행 때 내려받음 (약 2.2GB)
# 지시문을 받는 임베딩 모델(Qwen3-Embedding)용 질문 지시문
EMBED_QUERY_INSTRUCT = "Instruct: Given an employee's question, retrieve the internal regulation clause that answers it\nQuery: "
EMBED_DIM = 1024
EMBED_BATCH = 32

# 하이브리드 검색 (검색_설계.md 3절). 코드를 고치지 않고 여기서만 바꾼다.
RRF_K = 60                  # RRF 상수 (표준값)
HYBRID_VECTOR_WEIGHT = 0.7  # 측정 완료(2026.10.07, 질문 277개). 바꿀 근거 없음. 검색_설계.md 3.2절
HYBRID_KEYWORD_WEIGHT = 0.3
RERANK_MODEL = "BAAI/bge-reranker-v2-m3"  # 첫 실행 때 내려받음
# 리랭커 입력 앞에 "[문서명 조항]"을 붙인다. 주어가 앞 항에만 있는 항이나 "하나, 우리는…"처럼
# 본문만으로 어느 규정인지 알 수 없는 청크를 위해서다. 임베딩·BM25 색인에는 붙이지 않는다 (재색인 불필요).
RERANK_HEADER = True
BM25_K1 = 1.5
# 질문 나누기: 여러 가지를 묻는 질문을 GPT가 하위 질문(최대 3개)으로 나눠 각각 검색한다 (docs/질의_흐름.md).
# 2026.10.13 결정: 끈다. 190문항 검색에서 좋아진 3 / 나빠진 4로 차이가 없고, 리랭킹이 하위 질문 수만큼 돌아
# GPU 시간이 2~3배다. 끄면 질문 한 줄 + GPT가 바꿔 쓴 검색어로 검색한다(예전 방식).
QUERY_PLAN = False
# 동의어 사전(data/manual/synonyms.json): 질문의 구어체 말("법카")에 규정 용어("법인카드")를 검색어로 덧붙인다.
# 2026.10.13 결정: 끈다. 190문항 중 73문항에서 작동하는데 "동기"→이해충돌처럼 엉뚱하게 붙는 경우가 있고,
# 벡터 검색이 구어체 질문도 대부분 찾는다. 켬/끔 효과 측정은 docs/팀_실행_안내.md 2.1 (아직 결과 없음).
USE_SYNONYMS = False
# 기준 구성 (2026.10.15 확정): 팀 DB 항 단위 색인(.env PG_DATABASE=regulations_team)에서 찾고 조 전체를 넘긴다 (아래 세 스위치 켬).
# 조 단위 색인 `regulations`는 초기 실험용 잔재로 남겨 두었을 뿐 기본이 아니다. 그 DB로 돌릴 때는 아래 세 스위치를 0으로 끈다.
# 항 단위로 찾고 조 전체를 넘기기(small-to-big): 항 단위 색인(팀 DB)에서 검색·리랭크한 뒤,
# 같은 조의 항은 하나로 합쳐 서로 다른 조 k개를 고르고, 각 조의 모든 항을 이어 붙여 답변에 넘긴다.
# 조 단위 색인에서는 효과가 없다. 측정(2026.10.14, 검색_설계.md 9.6) 뒤 2026.10.15 기본 켬. 환경 변수 RETURN_ARTICLE=0으로 끈다.
RETURN_ARTICLE = os.environ.get("RETURN_ARTICLE", "1") == "1"
# RETURN_ARTICLE일 때만 쓰인다 (기본 켬):
#  HIGHLIGHT_HITS: 근거마다 역할(질문으로 찾은 조문 / 인용된 조문(참고) / 같은 조 다른 항)을 적고, 조를 올린 항 앞에 "▶"를 붙인다.
#                  인용 조문이 많으면 찾은 조문이 묻혀서 넣었다. 답변 모델에 그 뜻을 알려 준다(generate.HIT_NOTE).
#  LAW_CITES_FROM_HITS: 외부 법령 인용은 찾은 항이 직접 인용한 것만 따라간다 (조 안 다른 항의 법령 인용은 뺀다).
HIGHLIGHT_HITS = os.environ.get("HIGHLIGHT_HITS", "1") == "1"
LAW_CITES_FROM_HITS = os.environ.get("LAW_CITES_FROM_HITS", "1") == "1"
# 응답 시간 줄이기 (2026.10.14). 호출 순서만 바꿔서 답변 내용은 같다. 끄면(FAST_PIPELINE=0) 예전처럼 하나씩 차례로 한다.
#  - GPT 질문 바꿔쓰기를 기다리는 동안 원래 질문의 벡터·BM25 검색을 먼저 한다
#  - 충돌 확인을 답변 작성과 동시에 한다 (근거만 보므로 답변을 기다릴 필요가 없다)
#  - 문장 근거 검사(경고만 붙임)는 답을 돌려준 뒤 뒤에서 하고, Assistant.finish()에서 경고를 붙인다
FAST_PIPELINE = os.environ.get("FAST_PIPELINE", "1") == "1"
# GPT 질문 바꿔쓰기(rewrite_query)를 검색어로 더할지. 기본 켬 (2026.10.14 결정).
#  - 깨끗한 질문 검색(조 단위 93문항)에서는 차이 없음 (Hit@5 켬 0.77 / 끔 0.80)
#  - 오타 질문에서는 도움: Hit@10 0.78 → 0.84, Recall@10 0.665 → 0.714
#  - 답변 100문항(팀 DB 항→조 + 강조 + 시간 개선): 오답 8 → 4, 답못함 11 → 8, 금지 위반 6 → 3, 정답 29 같음
#  - 비용: 답 완성까지 약 1.7초 (8.7 → 10.4초)
USE_REWRITE = os.environ.get("USE_REWRITE", "1") == "1"

# 답변에 넘기는 근거 전체 글자 수 상한 (임시값, 답변 단계에서 조정)
CONTEXT_CHAR_BUDGET = 8000

# 답변 생성 (OpenAI API, 키는 프로젝트 상위 폴더의 .env에 있다)
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.6-luna")  # 2026.10.13 gpt-4o-mini에서 바꿈. 환경 변수로 바꿀 수 있다
# 채점 모델 (judge_answers 등). 답변 모델과 따로 둔다. 2026.10.13 gpt-4o-mini에서 바꿈:
# 그 전 채점 결과(eval/answer_runs/*/judged.jsonl)는 gpt-4o-mini 채점이라, 비교하려면 같은 runs를 다시 채점한다.
JUDGE_MODEL = "gpt-5.6-luna"

# 근거 점수 구간 (리랭커 1위 점수). 임시값: 측정 질문 35개로 정했다. 평가셋을 만든 뒤 다시 본다 (답변_설계.md 3절)
# 2026.10.07 재보정: 블라인드 질문 35개(v3 답있음 16 + 답없음/부분 19)의 실제 점수를 보니
# "있음"과 "없음"의 점수 구간이 0.03~0.6에서 크게 겹쳐서, 점수 하나로는 못 가른다
# (검색_설계.md 0.1절, 답변_설계.md 7절). 그래서 "점수로 미리 끊기"를 최소화하고,
# 확실히 아무것도 없는 경우만 막은 뒤 나머지는 모델 판단 + verify()에 맡긴다.
# partial(점수만 보고 고정 문구로 답하는 중간 구간)은 사실상 비활성화한다
# (NONE_BELOW와 값을 같게 둬서 그 구간이 비게 만듦).
# 2026.10.10 재보정: 평가셋 190문항 검색 점수로 보니 0.05는 정답 조문이 상위 5개에 있는 질문의 26%(33/125)를
# 막았다. 0.001이면 1%(1/125)만 막는다. 임계값을 0으로 두고 "없음" 28문항 + 막혔던 정답 33문항을 답변까지
# 생성해 본 결과, 없음 질문 28개 중 지어낸 답은 1개(점수 0.64라 어느 임계값에서도 통과)였다.
ANSWER_NONE_BELOW = 0.001    # 이보다 낮으면 "관련 내용 없음" (모델을 부르지 않음)
ANSWER_PARTIAL_BELOW = 0.001  # NONE_BELOW와 같음 = partial 구간 비활성화 (아래 설명 참고)
PARTIAL_SHOW = 3            # partial이 쓰일 때 보여줄 관련 조문 수 (지금은 거의 안 쓰임)
BM25_B = 0.75

TIER_RULES = [  # 문서명 끝 글자 → 위계
    ("정관", "정관"), ("규정", "규정"), ("지침", "지침"), ("헌장", "헌장"), ("강령", "강령"),
]
