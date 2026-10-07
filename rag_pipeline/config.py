"""경로와 설정. 여기서만 바꾸면 된다."""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 원본 규정 폴더 (폴더명 끝 숫자 = 알리오 고유번호)
SOURCE_ROOT = os.path.abspath(os.path.join(BASE_DIR, "..", "한국벤처투자_내부규정"))
DOWNLOAD_LIST = os.path.join(SOURCE_ROOT, "다운로드목록.json")

DATA_DIR = os.path.join(BASE_DIR, "data")
MANUAL_DIR = os.path.join(DATA_DIR, "manual")
OUT_DIR = os.path.join(BASE_DIR, "out")
TEXT_DIR = os.path.join(OUT_DIR, "text")

MANUAL_CHUNKS = os.path.join(MANUAL_DIR, "manual_chunks.jsonl")   # 조문 없는 문서 등 수동 청크
CORRECTIONS = os.path.join(MANUAL_DIR, "corrections.json")        # 확정된 보정 목록 (사람 확정 후 기록)

# HWP 추출 시 사용할 Python (olefile 설치 필요)
HWP_SUBPROCESS_PYTHON = None  # None이면 현재 실행 중인 Python 사용

# 검색 색인 (BGE-m3 임베딩, 저장은 PostgreSQL + pgvector: docs/DB_설정.md)
EMBED_MODEL = "BAAI/bge-m3"          # 첫 실행 때 내려받음 (약 2.2GB)
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

# 답변에 넘기는 근거 전체 글자 수 상한 (임시값, 답변 단계에서 조정)
CONTEXT_CHAR_BUDGET = 8000

# 답변 생성 (OpenAI API, 키는 프로젝트 상위 폴더의 .env에 있다)
OPENAI_MODEL = "gpt-4o-mini"

# 근거 점수 구간 (리랭커 1위 점수). 임시값: 측정 질문 35개로 정했다. 평가셋을 만든 뒤 다시 본다 (답변_설계.md 3절)
ANSWER_NONE_BELOW = 0.3     # 이보다 낮으면 "관련 내용 없음" (모델을 부르지 않음)
ANSWER_PARTIAL_BELOW = 0.6  # 이보다 낮으면 "관련 조문만 제시" (모델을 부르지 않음)
PARTIAL_SHOW = 3            # 애매함 구간에서 보여줄 관련 조문 수
BM25_B = 0.75

TIER_RULES = [  # 문서명 끝 글자 → 위계
    ("정관", "정관"), ("규정", "규정"), ("지침", "지침"), ("헌장", "헌장"), ("강령", "강령"),
]
