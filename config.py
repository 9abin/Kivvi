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
# 2026.10.19 최종: Qwen3-Embedding-4B (Ollama, 2560차원). RAG 조합 실험(99문항, docs/reports/261019-1050-59)에서 최종@10
# bge-m3 0.810 → 4B 0.889, 짝지은 차이 +0.071 [+0.015 ~ +0.131]로 유의한 유일한 부품. 8B는 +0.020(차이 없음)이라 4B에서 멈춤.
# 이름에 ":"가 있으면 Ollama 모델로 보고(OLLAMA_URL), 문서 벡터는 DB가 아니라 RAG_VECTOR_DIR의 파일에 둔다
# (DB chunks.embedding은 vector(1024)라 2560차원이 들어가지 않음. 실험의 src/combo와 같은 방식). 되돌리기: EMBED_MODEL=BAAI/bge-m3
EMBED_MODEL = os.environ.get("EMBED_MODEL", "qwen3-embedding:4b-q8_0")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")  # localhost는 Windows에서 호출마다 약 2초 늦음 (리포트 55)
RAG_VECTOR_DIR = os.path.join(BASE_DIR, "out", "rag_vectors")  # Ollama 임베딩의 문서 벡터 캐시 (DB 본문이 바뀌면 다시 만든다)
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
# 주제 링크 확장 (docs/검색_설계.md 12절, src/search/topics.py): 질문(과 바꿔 쓴 검색어)에 주제어 사전의 주제어가 있으면
# 같은 주제어를 담은 다른 조문을 모아 리랭커로 다시 채점하고 상위 몇 개를 "같은 주제 조문(참고)"으로 붙인다.
# 인용(cites)은 "제N조"로 가리킨 조문만 따라가서, 건강검진 비용(복지후생 운영지침 제3조)과 공가(복무규정 제25조)처럼
# 서로 가리키지 않는 조문은 이 길로만 이어진다. 이미 찾은 조·cites로 따라온 조문·같은 조의 항은 뺀다.
# 2026.10.14 측정(100문항, bge, 끔→켬): 질문 중심 채점 정답 56→58, 오답 5→3, 금지 위반 4→2, 넘어간 근거 묶음 88→95,
# 답 완성 +1.2초(근거 5.4→7.0개). 실행 간 흔들림(3~4문항) 범위라 기본 끔. 환경 변수 TOPIC_LINKS=1로 켠다 (검색_설계.md 12.4).
USE_TOPIC_LINKS = os.environ.get("TOPIC_LINKS", "0") == "1"
# 방식 (2026.10.15, 검색_설계.md 12.5):
#  "rerank"  = 질문의 주제어로 후보를 모아 사전 '함께' 조문 + 원래 질문 리랭크 상위 TOPIC_RELATED_LIMIT개 (측정 완료, 위)
#  "article" = 검색으로 찾은 조마다 미리 만든 연관 조항 표(RELATED_ARTICLES)를 조회해 전부 붙인다. 리랭커 없음.
#              이미 찾은 조·cites로 온 조문·같은 조 항·표끼리 겹치는 것은 뺀다. 개수는 TOPIC_RELATED_LIMIT(0이면 무제한),
#              글자 예산(CONTEXT_CHAR_BUDGET)이 넘치면 뒤부터 건너뛴다. 표는 scripts/build_related_articles.py가 초안을 만든다.
TOPIC_MODE = os.environ.get("TOPIC_MODE", "rerank")
TOPIC_RELATED_LIMIT = int(os.environ.get("TOPIC_LIMIT", "3" if TOPIC_MODE == "rerank" else "0"))  # 0 = 무제한
RELATED_ARTICLES = os.path.join(MANUAL_DIR, "related_articles.json")  # 조 → 연관 조 표 (article 방식, 사람이 다듬는다)
# article 방식에서 질문의 주제어와 겹치는 연관만 붙일지. 찾은 조가 엉뚱한 주제(해외 건강진단)면 그쪽 연관이 따라오는 것을 막는다.
TOPIC_ARTICLE_FILTER = os.environ.get("TOPIC_ARTICLE_FILTER", "0") == "1"
# 리랭커 점수(sigmoid)가 이보다 낮은 후보는 붙이지 않는다. ANSWER_NONE_BELOW와 같은 값: 정답 조문도 질문에 따라 0.0015
# (Q041 복지후생 운영지침 제2조)처럼 낮게 나와서 0.01로 두면 걸린다 (2026.10.14, 11문항 측정). 사전의 '함께' 조문은 이 값과 무관하다.
TOPIC_RELATED_MIN_SCORE = 0.001
# 사례 조문 링크 (2026.10.15, 검색_설계.md 14절). 징계·감사 사례(data/manual/discipline_cases.jsonl) 33건이 짚은 조문 59개를
# "같은 사례에 함께 나온 조문끼리" 양방향으로 이은 표(CASE_LINKS_TABLE, scripts/build_case_links.py). 검색으로 찾은 조가
# 표에 있으면 그 사례의 나머지 조문을 "같은 주제 조문(참고)"으로 붙인다. 사례 본문은 쓰지 않는다. 주제 링크(TOPIC_LINKS)와
# 독립이며 둘 다 켜면 주제 조문 뒤에 붙고 겹치는 조는 뺀다. 기본 끔, 측정 뒤 결정. 환경 변수 CASE_LINKS=1로 켠다.
CASE_LINKS = os.environ.get("CASE_LINKS", "0") == "1"
CASE_LINKS_TABLE = os.path.join(MANUAL_DIR, "case_links.json")
TOPICS = os.path.join(MANUAL_DIR, "topics.json")   # 주제어 사전 (사람이 다듬는다. 주제별 조문 수는 scripts/topic_coverage.py)
# 리랭커 앞에서 조로 합치기 (2026.10.15, 검색_설계.md 5절). 기본은 RRF 항 목록의 앞 30개를 리랭크하는데, 같은 조의 항이
# 여러 개 들어가 창 안의 서로 다른 조가 16~29개뿐이다. 켜면 RRF 순서대로 조를 세어 서로 다른 조 30개를 창으로 잡는다.
#  "all"  = 그 조들에 속한 후보 항을 모두 리랭크 (쌍 30 → 35~45). 항은 지금처럼 따로 채점하므로 긴 조가 불리하지 않다.
#  "best" = 조마다 RRF 최고 순위 항 하나만 리랭크 (쌍 30 그대로)
#  "off"  = 지금 방식. 2026.10.15 측정(93문항 rank_trace): R@5 off 0.565 / all 0.578 / best 0.565 — 창 밖 근거 4묶음이
#           리랭커 앞에 왔지만 2묶음만 5위 안. 효과가 흔들림 수준이라 기본 off (검색_설계.md 5절).
MERGE_BEFORE_RERANK = os.environ.get("MERGE_BEFORE_RERANK", "off")
# 위 창을 잡을 때 한 규정에서 넣을 조 수 상한 (0 = 없음). 2026.10.15 측정: 2개로 두면 R@5 0.510으로 크게 나빠진다
# (정답이 같은 규정의 여러 조인 문항을 창 밖으로 밀어냄). 쓰지 않는다.
RERANK_DOC_CAP = int(os.environ.get("RERANK_DOC_CAP", "0"))
# 2단계 LLM 리랭킹 (2026.10.15, src/search/llm_rerank.py, 검색_설계.md 5절). 교차 인코더(RERANK_MODEL) 상위 LLM_RERANK_TOP개를
# LLM이 문서별로 독립 채점(0~10: 질문의 핵심 요건을 직접 규정 8~10 / 간접 관련 3~7 / 무관 0~2, 병렬 호출)해 다시 정렬한다.
# 동점은 교차 인코더 순서, LLM_RERANK_TOP 밖은 교차 인코더 순서 그대로 뒤에. 호출·파싱 실패 문서는 교차 인코더 자리를 지킨다.
# 점수는 (모델, 질문, chunk_id) 키로 LLM_RERANK_CACHE에 저장해 평가 재실행 비용을 줄인다. 2026.10.15 측정(93문항 rank_trace):
# R@5 0.565→0.619(팀 DB 항 단위), 0.578→0.680(조 단위), 문항당 약 +1.2초. 새 100문항 답변 평가: 질문 중심 정답 51→58·오답 6→3,
# 엄격·단계 채점은 흔들림 범위, 답못함 5→8(별표 조각), 답 완성 +2.5초 (검색_설계.md 5절, 보고서 36). 기본값 미정 — 지금은 끔.
LLM_RERANK = os.environ.get("LLM_RERANK", "0") == "1"
LLM_RERANK_TOP = int(os.environ.get("LLM_RERANK_TOP", "15"))
LLM_RERANK_MODEL = os.environ.get("LLM_RERANK_MODEL", "gpt-4o-mini")  # 문서당 1회라 빠른 모델. 비교 시 환경 변수로 바꾼다
LLM_RERANK_CACHE = os.path.join(BASE_DIR, "eval", "runs", "llm_rerank_cache.json")
# 답변 뒤 누락 검사 (2026.10.15, src/answer/gapcheck.py, 검색_설계.md 13절): 답을 쓴 뒤 모델이 되묻는 질문(최대 GAP_CHECK_MAX_Q개)을
# 만들어 다시 찾고, 리랭크 6위 이하의 단서 문장과 합쳐 "결론을 바꾸거나 조건을 붙이는 조문"이 있으면 답 끝에 "※ 확인 필요"를 붙인다.
# 결론은 바꾸지 않는다. LLM 2회 + 검색 1~3회 (답변 뒤 차례로, 약 +3~5초). 측정 전이라 기본 끔. GAP_CHECK=1로 켠다.
GAP_CHECK = os.environ.get("GAP_CHECK", "0") == "1"
# 되묻기·판정에 쓰는 모델. 답변 모델(luna)로 돌리면 답 완성이 +13초(쉬운 판 50문항 9.0→22.3초)라 빠른 모델을 쓴다.
GAP_CHECK_MODEL = os.environ.get("GAP_CHECK_MODEL", "gpt-4o-mini")
GAP_CHECK_MAX_NOTES = 2  # 답 끝에 붙일 "※ 확인 필요" 수 상한. 1차 판정은 50문항 중 42문항에 평균 2.1개를 붙여 엄격 채점 정답 41→37
GAP_CHECK_MAX_Q = 3      # 되묻는 질문 수 상한
# 되묻는 질문마다 검색 상위 몇 개를 새 조문 후보로 볼지. 게이트 측정(93문항, 2026.10.15): 놓친 정답 66묶음 중
# 상위 1개 → 18묶음(새 조문 1.5/문항), 상위 2개 → 27(3.4), 상위 3개 → 31(5.2). 2개가 건짐과 잡음의 균형점.
GAP_CHECK_PER_Q = 2
GAP_CHECK_MAX_NEW = 8    # 판정에 넘길 새 조문 수 상한 (되묻기 검색 결과 먼저, 버린 후보 단서는 그 뒤)
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
#  - 2026.10.19 끔: Qwen3-4B 임베딩에서는 켬/끔 차이 없음(끔 +0.010 [+0.000 ~ +0.030], 99문항, 리포트 59),
#    「법카 → 법정카드」처럼 잘못 바꾸는 경우가 있고 호출 1번(약 1.7초)이 든다. bge-m3로 되돌리면 켜는 쪽이 나았음(−0.029, 유의하지 않음)
USE_REWRITE = os.environ.get("USE_REWRITE", "0") == "1"

# 답변에 넘기는 근거 전체 글자 수 상한 (임시값, 답변 단계에서 조정)
CONTEXT_CHAR_BUDGET = 8000

# 답변 근거의 출발점이 되는 리랭크 상위 청크 수. 2026.10.14 5에서 8로 바꿈 (docs/검색_실험_261014.md).
# 항 단위 색인 100문항에서 정답 근거 Recall 0.56 → 0.67, 정답 26 → 34, 오답 7 → 3(같은 조 묶기·인용 복구 포함).
# 답변 입력이 길어져도 응답시간은 그대로였다(18.5초 → 18.4초): 시간은 입력 길이보다 모델의 생각 시간이 정한다.
# 2026.10.19 8 → 10: 답변 비교(로컬 30B 정답 47 · luna 60)를 근거 10개로 쟀다(리포트 59). 검색만 보면 4B에서
# 최종@8 0.841 → @10 0.865(+0.024), @12 0.881. 화면(app/web_chat)과 평가 실행기가 모두 이 값을 쓴다.
TOP_K = 10

# 서로 독립인 모델 호출을 동시에 실행한다: 바꿔쓰기와 원질문 검색, 충돌 확인과 답변 생성, 근거 검사와 코드 검사.
# 호출 횟수와 내용은 그대로이고 기다리는 시간만 줄인다. 끄면 예전처럼 순서대로 실행한다(전후 시간 비교용).
PARALLEL_CALLS = True

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
