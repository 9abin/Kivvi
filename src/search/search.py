"""검색 파이프라인 (`docs/검색_설계.md` 9절 순서).

  1. 메타데이터 필터로 문서 범위를 먼저 좁힌다 (위계, 문서, 개정연도)
  2. 벡터 검색과 BM25 검색으로 후보를 각각 뽑는다
  3. 가중 RRF로 합친다 (벡터 0.7 : 키워드 0.3, config.py)
  4. 상위 후보를 리랭커로 다시 점수 매긴다
  5. 상위 k개에 형제 항을 붙이고 (3,000자 상한), cites를 1단계 따라간다
     (청크가 조 단위라 형제 항은 보통 없다. 긴 별표 조각처럼 같은 조항 이름을 나눠 가진 청크만 붙는다)
  6. (config.USE_TOPIC_LINKS) 질문의 주제어로 흩어진 조문을 모아 리랭커로 고른 몇 개를 붙인다 (_related, topics.py).
     이미 찾은 조·cites로 따라온 조문·같은 조의 항은 뺀다.

사용 예:
  from src.search.search import Searcher
  s = Searcher()
  s.search("직위해제 기간", k=5)
  s.search("감사 종류", k=5, filters={"tier": "규정"})
  s.search("감사 종류", k=5, filters={"doc_ids": ["23234"], "min_year": 2025})
"""
import os
import re
from collections import defaultdict

import numpy as np

from kiwipiepy import Kiwi
from sentence_transformers import CrossEncoder, SentenceTransformer

from config import (EMBED_MODEL, EMBED_QUERY_INSTRUCT, OLLAMA_URL, RAG_VECTOR_DIR, HIGHLIGHT_HITS, HYBRID_KEYWORD_WEIGHT, HYBRID_VECTOR_WEIGHT,
                    LAW_CITES_FROM_HITS, LLM_RERANK, MERGE_BEFORE_RERANK, RERANK_DOC_CAP, RERANK_HEADER, RERANK_MODEL,
                    RETURN_ARTICLE, RRF_K, TOPIC_ARTICLE_FILTER, TOPIC_MODE, TOPIC_RELATED_LIMIT, CASE_LINKS, CASE_LINKS_TABLE,
                    TOPIC_RELATED_MIN_SCORE, USE_SYNONYMS, USE_TOPIC_LINKS)
from src.search import glossary, synonyms, topics
from src.search.bm25 import BM25
from config import CONTEXT_CHAR_BUDGET
from src.search.exact import parse_reference
from src.store.pgstore import PgStore

CANDIDATES = 50         # 방식별로 합치기 전 가져올 후보 수
RERANK_TOP = 30         # 리랭커에 넘기는 후보 수 (RRF 상위)
SIBLING_LIMIT = 3000    # 형제 항을 붙일 때의 글자 수 상한 (재색인 설계 4절)
SHORTEN_LONG = True     # 조 전체가 예산(CONTEXT_CHAR_BUDGET)을 넘으면 가장 긴 조부터 찾은 항과 이웃 항만 남긴다. 끄면 안 들어가는 조는 통째로 빠진다
CITE_LIMIT = 5          # 청크 하나당 cites에서 따라갈 최대 개수
CITE_OK_STATUS = ("정상연결", "정정 확정", "외부연결")  # 실제 대상 청크가 있는 인용만 따라간다
LLM_SELECT_POOL = 0  # 0이면 끔. 16이면 리랭크 상위 16개 조를 LLM이 보고 k개를 고른다 (scripts/exp_listwise_topk.py 실험, 평가 실행기의 --select-pool로 켠다)
SELECT_PROMPT = """한국벤처투자 내부 규정 검색 결과에서 직원 질문에 답하는 데 필요한 조문을 정확히 {k}개 고른다.
- 질문에 직접 답하는 조문을 앞에 두고, 답의 조건·예외·정의·기준표를 알려 주는 조문도 포함한다.
- 같은 규정의 비슷한 조문만 몰아 고르지 말고, 질문에 꼭 필요한 것만 고른다.
- 후보 번호만 JSON으로 답한다: {{"순서": [번호, 번호, ...]}}

질문: {q}

후보:
{cands}"""
GROUP_BY_ARTICLE = True  # 상위 k개를 고를 때 같은 조의 항을 한 자리로 묶어 서로 다른 조를 k개 확보한다(같은 조의 다른 항은 형제 항 확장이 붙인다). 조 단위 색인에서는 효과가 없다
CITE_WHOLE_ARTICLE = True  # 조 전체를 가리키는 인용을 항 단위 색인에서도 따라간다(_whole_article). 전후 비교를 위해 끌 수 있다
RELATED_RERANK_TOP = 30  # 주제 링크 후보 중 리랭커에 넘기는 수 (후보 단계의 RERANK_TOP과 같게 두어 비용을 묶는다)
# HIGHLIGHT_HITS: 조 전체를 넘길 때의 표시 (generate.HIT_NOTE가 답변 모델에 뜻을 알려 준다)
HIT_MARK = "▶▶ [찾은 항] "    # 검색이 실제로 찾은 항 (강하게)
SIB_MARK = "▷ [같은 조] "     # 같은 조의 나머지 항 (약하게)
PROVISO_MARK = "【단서】"       # 단서·예외·한정 문장. 빠뜨리기 쉬운 조건이라 항 구분 없이 붙인다
_PROVISO = re.compile(r"다만|^단\s*,|제외한다|제외하고|제외된다|에 한한다|에 한하여|에 한정|한정한다|한정하여|예외로|그러하지 아니하다")


def mark_provisos(text):
    """조 본문의 문장 중 단서·예외·한정 문장 앞에 PROVISO_MARK를 붙인다.
    빠뜨린 정답 요지의 대부분이 같은 조의 "다만 …" 단서였다 (불러왔는데 안 쓴 19문항, 2026.10.14 검토)."""
    out = []
    for line in text.split("\n"):
        parts = re.split(r"(?<=다\.)\s+", line)
        out.append(" ".join(PROVISO_MARK + p if _PROVISO.search(p) and PROVISO_MARK not in p else p for p in parts))
    return "\n".join(out)


def is_ollama_model(name):
    """Ollama 모델 이름(「qwen3-embedding:4b-q8_0」처럼 태그가 붙음)인가. sentence-transformers 이름은 「BAAI/bge-m3」 꼴."""
    return ":" in name and "/" not in name


def query_prompt(model):
    """질문 임베딩 접두어. 문서와 질문을 다르게 넣는 모델만 해당한다 (bge-m3·KURE는 없음).
    Qwen3-Embedding은 작업 지시문을, snowflake-arctic-embed는 모델에 저장된 "query: "를 붙인다."""
    if "qwen3-embedding" in EMBED_MODEL.lower():
        return {"prompt": EMBED_QUERY_INSTRUCT}
    return {"prompt_name": "query"} if "query" in (getattr(model, "prompts", None) or {}) else {}


def strip_marks(text):
    """사람에게 보여 줄 때 HIT_MARK·SIB_MARK·PROVISO_MARK를 지운다 (표시는 답변 모델용)."""
    for m in (HIT_MARK, SIB_MARK, PROVISO_MARK):
        text = text.replace(m, "")
    return text


SHORTENED_NOTE = "\n(분량 때문에 이 조의 나머지 항은 생략함)"
MIN_SHORTENED = 500  # 조 하나를 이보다 짧게는 줄이지 않는다


def water_level(lengths, budget, floor=MIN_SHORTENED):
    """조 길이 목록을 모두 min(길이, L)로 깎았을 때 합이 budget 안에 드는 가장 큰 L (floor 아래로는 내리지 않는다)."""
    lo, hi = floor, max(lengths)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if sum(min(n, mid) for n in lengths) <= budget:
            lo = mid
        else:
            hi = mid - 1
    return lo

_PARA_START = re.compile(r"(?m)^(?=" + re.escape(HIT_MARK) + "|" + re.escape(SIB_MARK) + ")")


def shorten_article(body, limit):
    """조 전체(body)가 예산에 안 들어갈 때 줄여 넣는다: 찾은 항(HIT_MARK)과 이웃 항을 limit자 안에서 항 순서대로 남긴다.
    표시가 없는 조(항이 하나)는 앞에서부터 줄 단위로 limit자까지 남긴다. 줄인 곳에는 SHORTENED_NOTE가 붙는다."""
    parts = [p for p in _PARA_START.split(body) if p]
    hit = next((i for i, p in enumerate(parts) if p.startswith(HIT_MARK)), 0)
    keep, used = {hit}, len(parts[hit])
    lo, hi = hit - 1, hit + 1
    while used < limit and (lo >= 0 or hi < len(parts)):
        for i in (hi, lo):  # 뒤 항을 먼저, 그다음 앞 항
            if 0 <= i < len(parts) and i not in keep and used + len(parts[i]) <= limit:
                keep.add(i)
                used += len(parts[i])
        lo, hi = lo - 1, hi + 1
    text = "".join(parts[i] for i in sorted(keep))
    if len(text) > limit:  # 찾은 항 하나가 limit보다 길면 줄 단위로 자른다
        cut = text[:limit]
        text = cut[:cut.rfind("\n")] if "\n" in cut[limit // 2:] else cut
    return text.rstrip() + SHORTENED_NOTE if len(text) < len(body) else body


# 키워드 질의에서 뺄 형태소 태그: 조사, 어미, 문장부호
_SKIP_TAGS = ("J", "E", "SF", "SP", "SS", "SE", "SO")


def _year(revision):
    """개정표기 '2026년도 08월 28일 개정'에서 연도를 꺼낸다. 없으면 None."""
    m = re.search(r"(\d{4})년", revision or "")
    return int(m.group(1)) if m else None


def _state(chunk_id, status):
    """조문 상태: 현행 / 부칙 / 삭제. 답변 단계가 현행 근거로 오해하지 않도록 항상 붙인다."""
    if status == "삭제":
        return "삭제"
    if chunk_id.endswith("_부칙"):
        return "부칙"
    return "현행"


def _order_key(article):
    """조항 표기를 항 순서대로 정렬하는 키. '제10조' > '제2조' 가 되도록 숫자로 비교한다."""
    return tuple(int(n) for n in re.findall(r"\d+", article or ""))


def _article_base(article):
    return (article or "").split(" ")[0]


def group_by_article(hits):
    """순위를 유지한 채 같은 (문서, 조)의 항은 처음 나온 하나만 남긴다. 한 조의 항들이 상위 k개를 독차지하지 않게 하려는 것이다.
    남은 항에는 형제 항 확장이 같은 조의 다른 항을 붙인다."""
    seen, out = set(), []
    for h in hits:
        key = (h["doc_id"], _article_base(h["article"]))
        if key not in seen:
            seen.add(key)
            out.append(h)
    return out


def interleave(lists, limit):
    """여러 결과 목록을 순위별로 번갈아 합친다 (각 목록 1위, 각 목록 2위, …). 같은 청크는 처음 나온 것만 남긴다."""
    out, seen = [], set()
    for rank in range(max((len(x) for x in lists), default=0)):
        for hits in lists:
            if rank < len(hits) and hits[rank]["chunk_id"] not in seen:
                seen.add(hits[rank]["chunk_id"])
                out.append(hits[rank])
                if len(out) >= limit:
                    return out
    return out


class Searcher:
    def __init__(self):
        self.store = PgStore()
        self.kiwi = Kiwi()
        # Ollama 임베딩(이름에 ":", 2026.10.19 기본 qwen3-embedding:4b-q8_0)은 문서 벡터를 DB 대신 파일에 둔다 (config.RAG_VECTOR_DIR)
        self.embedder, self._vec_ids, self._vec_mat, self._vec_doc = None, None, None, None
        if is_ollama_model(EMBED_MODEL):
            from src.combo.models import OllamaEmbedder
            self.model = None
            self.embedder = OllamaEmbedder(EMBED_MODEL, base_url=OLLAMA_URL, instruction=EMBED_QUERY_INSTRUCT)
        else:
            self.model = SentenceTransformer(EMBED_MODEL, device="cuda")
            self.model.half()
        # fp16 (2026.10.19): 조합 실험의 기준 구성 R1과 같게 (fp32와 순위 같음, 질문당 0.66 → 0.19초, configs/combo/baseline.toml).
        # Ollama 임베딩(4B)과 8GB GPU를 나눠 쓰므로 메모리도 절반으로
        self.reranker = CrossEncoder(RERANK_MODEL, device="cuda", model_kwargs={"dtype": "float16"})
        self.rerank_header = RERANK_HEADER
        self._select_llm = None
        self.synonym_table = synonyms.load() if USE_SYNONYMS else {}
        self.glossary = glossary.load()
        self.glossary_filter = True  # 후보 단계 제외 (전후 비교를 위해 끌 수 있다)
        self.last_glossary_excluded = []
        self.last_hint = ""
        # 주제어 사전 (검색_설계.md 12절). 사례 조문 링크(14절)도 주제 게이트에 쓴다
        self.topics = topics.load() if (USE_TOPIC_LINKS or CASE_LINKS) else {}
        # 조 → 연관 조 표 (TOPIC_MODE="article", 검색_설계.md 12.5). 찾은 조마다 조회만 한다
        self.related_table = topics.load_related() if USE_TOPIC_LINKS and TOPIC_MODE == "article" else {}
        # 사례 조문 링크 표 (CASE_LINKS, 검색_설계.md 14절). 같은 형식, 같은 조회 방식(_related_articles)
        self.case_table = topics.load_related(CASE_LINKS_TABLE) if CASE_LINKS else {}
        # 2단계 LLM 리랭커 (LLM_RERANK, 검색_설계.md 5절): 교차 인코더 상위 LLM_RERANK_TOP개를 LLM이 다시 채점한다
        self.llm_reranker = None
        if LLM_RERANK:
            from src.search.llm_rerank import LLMReranker
            self.llm_reranker = LLMReranker()
        self.last_related = []
        self.last_reranked = []
        self._load_index()

    def _load_index(self):
        self.version = self.store.index_version()
        self.documents = {d["doc_id"]: {**d, "year": _year(d["revision"])}
                          for d in self.store.load_documents()}
        rows = self.store.load_indexed_rows()
        self.row_info = {r["chunk_id"]: r for r in rows}
        docs = [(r["chunk_id"], (r["body_tokens"] or "").split()) for r in rows]
        self.bm25 = BM25(docs)
        # 주제 → 청크 표. 색인 행을 그대로 훑으므로 조 단위·항 단위 어느 색인이든 맞고, 재색인되면 함께 다시 만든다.
        self.topic_chunks, self.chunk_topics = topics.build_index(rows, self.topics)
        # (문서, 조) → 항 청크 ID 목록 (항 순서). 사전의 '함께' 조문("23970_제25조")을 지금 색인의 청크로 바꿀 때 쓴다.
        self.article_rows = defaultdict(list)
        for r in sorted(rows, key=lambda r: _order_key(r["article"])):
            self.article_rows[(r["doc_id"], _article_base(r["article"]))].append(r["chunk_id"])
        if self.embedder is not None:
            self._load_doc_vectors()

    def _load_doc_vectors(self):
        """Ollama 임베딩의 문서 벡터 (색인 청크 전체). 임베딩 입력은 src/index/build.py · src/combo/searcher.py와 같다
        (장이 있으면 머리말, 본문은 body_search). 같은 모델 · 본문이면 캐시를 쓰고, DB 본문이 바뀌었으면 다시 만든다 (처음 한 번 수 분)."""
        from src.combo.models import doc_vectors, safe, sha
        ids = list(self.row_info)
        texts = [f"[{r['section']}]\n{r['body_search']}" if r.get("section") else (r["body_search"] or "")
                 for r in (self.row_info[c] for c in ids)]
        meta = {"model": self.embedder.name, "doc_prefix": "", "max_seq_length": 0, "n": len(texts), "text_hash": sha(*texts)}
        os.makedirs(RAG_VECTOR_DIR, exist_ok=True)
        path = os.path.join(RAG_VECTOR_DIR, f"emb_{safe(self.embedder.name)}.npy")
        try:
            mat = doc_vectors(self.embedder, texts, path, meta)
        except RuntimeError:   # 다른 본문으로 만든 캐시 → 지우고 다시
            for p in (path, path[:-4] + ".meta.json"):
                if os.path.exists(p):
                    os.remove(p)
            mat = doc_vectors(self.embedder, texts, path, meta)
        self._vec_ids, self._vec_mat = ids, mat
        self._vec_doc = np.array([self.row_info[c]["doc_id"] for c in ids])

    def _refresh_if_stale(self):
        """재색인으로 DB가 바뀌었으면 메모리의 BM25와 목록을 다시 읽는다. 검색 호출마다 한 번 확인한다."""
        if self.store.index_version() != self.version:
            self._load_index()
            return True
        return False

    def close(self):
        self.store.close()

    # 1. 메타데이터 필터 ----------------------------------------------------
    def _allowed_doc_ids(self, filters):
        """필터에 맞는 문서 고유번호 집합. 필터가 없으면 None (전체)."""
        if not filters:
            return None
        allowed = set(self.documents)
        if filters.get("tier"):
            allowed &= {d for d, v in self.documents.items() if v["tier"] == filters["tier"]}
        if filters.get("doc_ids"):
            allowed &= set(filters["doc_ids"])
        if filters.get("min_year"):
            allowed &= {d for d, v in self.documents.items()
                        if v["year"] is not None and v["year"] >= filters["min_year"]}
        return allowed

    # 2. 후보 검색 ----------------------------------------------------------
    def _query_tokens(self, text):
        return [t.form for t in self.kiwi.tokenize(text) if not t.tag.startswith(_SKIP_TAGS)]

    def vector_hits(self, text, doc_ids):
        if self.embedder is not None:   # 파일 문서 벡터와 내적 (src/combo/searcher.ComboSearcher.vector_hits와 같음)
            qv = self.embedder.encode_queries([text])[0]
            sims = self._vec_mat @ qv
            if doc_ids is not None:
                sims = np.where(np.isin(self._vec_doc, list(doc_ids)), sims, -np.inf)
            top = [i for i in np.argsort(-sims)[:CANDIDATES] if sims[i] > -np.inf]
            return [{**self.row_info[self._vec_ids[i]], "chunk_id": self._vec_ids[i], "score": float(sims[i])} for i in top]
        vec = self.model.encode([text], normalize_embeddings=True, convert_to_numpy=True, **query_prompt(self.model))[0]
        return self.store.vector_search(vec, k=CANDIDATES, doc_ids=doc_ids)

    def bm25_hits(self, text, doc_ids):
        allowed_chunks = None
        if doc_ids is not None:
            allowed_chunks = {cid for cid, r in self.row_info.items() if r["doc_id"] in doc_ids}
        pairs = self.bm25.top(self._query_tokens(text), k=CANDIDATES, allowed_ids=allowed_chunks)
        return [{"chunk_id": cid, **self.row_info[cid]} for cid, _ in pairs]

    # 3. 결합 ---------------------------------------------------------------
    def fuse(self, vec_hits, key_hits, vector_weight=HYBRID_VECTOR_WEIGHT, keyword_weight=HYBRID_KEYWORD_WEIGHT,
             extra=()):
        """가중 RRF로 합친다. 가중치 인자는 기본값이 config.py 값이라 평소 호출은 그대로 동작한다.
        가중치를 바꿔가며 비교할 때만(src/eval/sweep_weights.py) 인자로 넘긴다.
        extra: 다른 질의(바꿔 쓴 질문)의 (이름, 후보 목록, 가중치) 목록. 같은 방식으로 점수를 더한다."""
        score = defaultdict(float)
        info = {}
        origin = defaultdict(set)
        for name, hits, weight in (("벡터", vec_hits, vector_weight),
                                   ("키워드", key_hits, keyword_weight), *extra):
            for rank, h in enumerate(hits, start=1):
                cid = h["chunk_id"]
                score[cid] += weight / (RRF_K + rank)
                origin[cid].add(name)
                info.setdefault(cid, h)
        ranked = sorted(score, key=lambda c: score[c], reverse=True)
        return [{**info[c], "chunk_id": c, "score": score[c], "match": "+".join(sorted(origin[c]))}
                for c in ranked]

    # 4. 리랭킹 -------------------------------------------------------------
    def rerank_text(self, chunk_id):
        """리랭커에 넣는 청크 글자. 머리말을 켜면 '[문서명 장 조항] 본문_검색' 형태가 된다.
        문서명은 임베딩·BM25 색인에는 넣지 않고 여기서만 붙인다 (src/index/build.py embed_text)."""
        row = self.row_info[chunk_id]
        if not self.rerank_header:
            return row["body_search"]
        title = self.documents.get(row["doc_id"], {}).get("title") or row["doc_id"]
        label = " ".join(x for x in (title, row.get("section"), row["article"]) if x)
        return f"[{label}] {row['body_search']}"

    def rerank_window(self, fused, top=RERANK_TOP, mode=None, doc_cap=None):
        """리랭커에 넘길 후보. 기본("off")은 RRF 순위 앞 top개 항.
        MERGE_BEFORE_RERANK(검색_설계.md 5절)이 "all"/"best"면 RRF 순서대로 조를 세어 서로 다른 조 top개를 창으로 잡는다.
        "all"은 그 조에 속한 후보 항을 모두, "best"는 조마다 RRF 최고 순위 항 하나만 돌려준다. 항은 그대로 짧게 채점되므로
        긴 조가 불리해지지 않는다. doc_cap(RERANK_DOC_CAP)이 있으면 한 규정에서 창에 넣는 조 수를 막는다 (넘친 조는 뒤로)."""
        mode = MERGE_BEFORE_RERANK if mode is None else mode
        doc_cap = RERANK_DOC_CAP if doc_cap is None else doc_cap
        if mode == "off":
            return fused[:top]
        chosen, per_doc, skipped = {}, defaultdict(int), set()
        for h in fused:
            key = (h["doc_id"], _article_base(h["article"]))
            if key in chosen or key in skipped:
                continue
            if doc_cap and per_doc[h["doc_id"]] >= doc_cap:
                skipped.add(key)
                continue
            chosen[key] = h  # RRF 최고 순위 항
            per_doc[h["doc_id"]] += 1
            if len(chosen) >= top:
                break
        if mode == "best":
            return list(chosen.values())
        return [h for h in fused if (h["doc_id"], _article_base(h["article"])) in chosen]

    def rerank(self, text, cands):
        if not cands:
            return cands
        pairs = [(text, self.rerank_text(c["chunk_id"])) for c in cands]
        scores = self.reranker.predict(pairs, batch_size=16)
        for c, s in zip(cands, scores):
            c["rerank"] = float(s)
        return sorted(cands, key=lambda c: c["rerank"], reverse=True)

    # 5. 확장 ---------------------------------------------------------------
    def _entry(self, h, status=None):
        """검색 결과 한 줄에 들어갈 공통 항목. 출처와 조문 상태를 함께 담는다."""
        meta = self.row_info.get(h["chunk_id"], {})
        doc = self.documents.get(h["doc_id"], {})
        if status is None:
            status = h.get("status", "정상")
        return {
            "chunk_id": h["chunk_id"],
            "doc_id": h["doc_id"],
            "문서명": doc.get("title"),
            "위계": doc.get("tier"),
            "개정표기": doc.get("revision"),
            "출처URL": doc.get("source_url"),
            "article": h["article"],
            "조문상태": _state(h["chunk_id"], status),
            "body": h["body"],
            "정정안내": h.get("corrections_note", meta.get("corrections_note")),
        }

    def _siblings_within_limit(self, hit):
        """같은 조의 다른 항을 항 순서대로 담되, 본문 글자 수 합계가 상한을 넘지 않게 한다."""
        used = len(hit["body"])
        picked = []
        for s in self.store.siblings(hit["doc_id"], hit["article"], exclude_chunk_id=hit["chunk_id"]):
            if used + len(s["body"]) > SIBLING_LIMIT:
                break
            picked.append(self._entry(s))
            used += len(s["body"])
        return picked

    def _cited(self, hit):
        """청크의 cites 중 실제 대상이 있는 것을 1단계만 따라간다. 대상이 부칙·삭제여도 상태를 붙인다.
        외부연결(법령, law_lookup.py)은 내부 chunk_id가 아니라 cites에 이미 저장된 본문을 그대로 쓴다."""
        targets, seen = [], set()
        for c in self.row_info.get(hit["chunk_id"], {}).get("cites") or []:
            # 조 단위 청크는 여러 항의 인용을 함께 가지므로, 같은 조를 가리키는 인용은 한 번만 따라간다.
            # 같은 조 안의 인용('제1항에 따라')은 이미 이 청크 본문에 있으므로 따라가지 않는다.
            if c.get("대상") and c.get("상태") in CITE_OK_STATUS and c["대상"] != hit["chunk_id"] \
                    and c["대상"] not in seen:
                seen.add(c["대상"])
                targets.append(c)
            if len(targets) >= CITE_LIMIT:
                break
        if not targets:
            return []
        internal = [t for t in targets if t["상태"] != "외부연결"]
        found = {r["chunk_id"]: r for r in self.store.get([t["대상"] for t in internal])} if internal else {}
        out = []
        for t in targets:
            if t["상태"] == "외부연결":
                out.append({"chunk_id": t["대상"], "doc_id": t["대상"], "문서명": t.get("법령명"),
                           "위계": "외부법령", "개정표기": None, "출처URL": None, "article": "제" + t["대상"].split("_")[-1] + "조",
                           "조문상태": "현행", "body": t.get("외부본문", ""), "정정안내": None,
                           "인용원문": t["원문"], "연결상태": t["상태"]})
                continue
            r = found.get(t["대상"])
            if r:
                entry = self._entry(r)
                entry["인용원문"] = t["원문"]
                entry["연결상태"] = t["상태"]
                out.append(entry)
            elif CITE_WHOLE_ARTICLE:
                out.extend(self._whole_article(t))
        return out

    def _whole_article(self, cite):
        """조 전체를 가리키는 인용('제16조')은 대상 ID에 항이 없다. 그 조가 항별 청크로 쪼개져 있으면
        같은 ID의 청크가 없으므로, 그 조의 항을 항 순서대로 가져온다(글자 수 상한은 형제 항과 같다).
        조 단위 색인에서는 같은 ID의 청크가 있어 이 길로 오지 않는다."""
        doc_id, _, article = cite["대상"].partition("_")
        used, out = 0, []
        for r in self.store.siblings(doc_id, article):
            if used + len(r["body"]) > SIBLING_LIMIT:
                break
            entry = self._entry(r)
            entry["인용원문"] = cite["원문"]
            entry["연결상태"] = cite["상태"]
            out.append(entry)
            used += len(r["body"])
        return out

    def _llm_select(self, text, cands, k):
        """리랭크 상위 후보(조 단위)를 LLM이 보고 질문에 필요한 k개를 중요한 순서로 고른다. 실패하거나 모자라면 리랭크 순서로 채운다."""
        import json
        import re as _re
        if self._select_llm is None:
            from langchain_openai import ChatOpenAI
            from config import JUDGE_MODEL
            self._select_llm = ChatOpenAI(model=JUDGE_MODEL, temperature=0, timeout=120, max_retries=2)
        lines = []
        for i, h in enumerate(cands, 1):
            r = self.row_info[h["chunk_id"]]
            title = self.documents.get(r["doc_id"], {}).get("title") or r["doc_id"]
            lines.append(f"[{i}] {title} {r['article']} | " + _re.sub(r"\s+", " ", r["body_search"] or "")[:220])
        try:
            out = self._select_llm.invoke(SELECT_PROMPT.format(k=k, q=text, cands="\n".join(lines))).content
            ids = [int(x) for x in json.loads(_re.search(r"\{.*\}", out, _re.S).group(0))["순서"]]
        except Exception:  # noqa: BLE001
            ids = []
        chosen, seen = [], set()
        for i in ids:
            if 1 <= i <= len(cands) and i not in seen:
                seen.add(i)
                chosen.append(cands[i - 1])
        chosen = chosen[:k]
        return chosen + [h for h in cands if h["chunk_id"] not in {c["chunk_id"] for c in chosen}][:k - len(chosen)]
    def _article_item(self, h):
        """항 청크 h가 속한 조 전체를 한 항목으로 (RETURN_ARTICLE일 때 주제 링크 결과에 쓴다).
        _as_articles와 같은 모양이지만 찾은 항 표시(HIT_MARK·SIB_MARK)는 붙이지 않는다. 단서 표시는 붙인다."""
        doc_id, base = h["doc_id"], _article_base(h["article"])
        rows = sorted(self.store.find_article(doc_id, base), key=lambda r: _order_key(r["article"]))
        body = "\n".join((mark_provisos(r["body"]) if HIGHLIGHT_HITS else r["body"]) for r in rows) or h["body"]
        item = self._entry(h, status=h.get("status"))
        item.update({"chunk_id": f"{doc_id}_{base}", "article": base, "body": body, "siblings": [], "cited": []})
        return item

    def _related(self, text, hint, results):
        """주제 링크 확장 (검색_설계.md 12절, topics.py). 질문(과 바꿔 쓴 검색어)에 사전의 주제어가 있으면
        같은 주제어를 담은 조문을 모아 리랭커로 다시 채점해 상위 TOPIC_RELATED_LIMIT개(서로 다른 조)를 돌려준다.

        뺀다: 검색 결과 조, cites로 따라온 조문, 형제 항, 그리고 그 조들의 다른 항 (같은 내용이 두 번 들어가지 않게).
        먼저 사전의 "함께" 조문(사람이 정한 보충 조문)을 점수와 무관하게 붙이고, 남은 자리를 리랭커 점수 순으로 채운다.
        후보가 많으면 (제목이 주제어인 조, 주제어 등장 횟수) 순으로 RELATED_RERANK_TOP개만 리랭커에 넘긴다.
        리랭커 점수가 TOPIC_RELATED_MIN_SCORE보다 낮은 것은 붙이지 않는다 (채용 건강검진처럼 주제어만 같은 조문).
        반환 항목에는 "주제"(어느 주제어로 왔는지), "match": "주제링크", "연결상태": "주제연결"이 붙고,
        "함께" 조문에는 "주제이유"(사전에 적은 이유)가 더 붙는다."""
        self.last_related = []
        found = topics.detect(" ".join(x for x in (text, hint) if x), self.topics) if self.topics else []
        if not found:
            return []
        excluded, taken_articles = set(), set()
        for r in results:
            for e in (r, *r.get("cited", []), *r.get("siblings", [])):
                excluded.add(e["chunk_id"])
                taken_articles.add((e["doc_id"], _article_base(e["article"])))
        seen = set()
        pinned = []  # [(chunk_id, 주제, 이유)] 사전의 '함께' 조문. 같은 조는 한 번만, 이미 들어간 조는 뺀다
        for t in found:
            for p in self.topics[t].get("함께", []):
                doc_id, _, base = p["대상"].partition("_")
                key = (doc_id, base)
                ids = self.article_rows.get(key) or []
                if key in taken_articles or not ids:
                    continue
                # 항 단위 색인이면 그 조에서 주제어를 담은 항을, 없으면 첫 항을 대표로 삼는다
                cid = next((c for c in ids if t in self.chunk_topics.get(c, {})), ids[0])
                taken_articles.add(key)
                seen.add(cid)
                pinned.append((cid, t, p.get("이유", "")))
        cands = []
        for t in found:
            for cid in self.topic_chunks.get(t, []):
                row = self.row_info.get(cid)
                if cid in seen or cid in excluded or row is None:
                    continue
                if (row["doc_id"], _article_base(row["article"])) in taken_articles:
                    continue
                seen.add(cid)
                cands.append(cid)
        if not pinned and not cands:
            return []
        top = topics.rank_candidates(cands, self.chunk_topics, found, RELATED_RERANK_TOP)
        ranked = self.rerank(text, [{"chunk_id": cid, **self.row_info[cid]} for cid in [p[0] for p in pinned] + top])
        by_id = {h["chunk_id"]: h for h in ranked}
        reasons = {cid: (t, why) for cid, t, why in pinned}
        ordered = [by_id[cid] for cid, _, _ in pinned] + [h for h in ranked if h["chunk_id"] not in reasons]
        out, chosen = [], set()
        for h in ordered:
            is_pinned = h["chunk_id"] in reasons
            if not is_pinned and h["rerank"] < TOPIC_RELATED_MIN_SCORE:
                break
            key = (h["doc_id"], _article_base(h["article"]))
            if key in chosen:
                continue
            chosen.add(key)
            item = self._article_item(h) if RETURN_ARTICLE else self._entry(h, status=h.get("status"))
            topics_of = [t for t in found if t in self.chunk_topics.get(h["chunk_id"], {})]
            if is_pinned and not topics_of:
                topics_of = [reasons[h["chunk_id"]][0]]
            item.update({"rerank": h["rerank"], "match": "주제링크", "연결상태": "주제연결", "주제": topics_of})
            if is_pinned:
                item["주제이유"] = reasons[h["chunk_id"]][1]
            out.append(item)
            if len(out) >= TOPIC_RELATED_LIMIT:
                break
        self.last_related = [(i["문서명"], i["article"], i["주제"], round(i["rerank"], 3), "함께" if "주제이유" in i else "")
                             for i in out]
        return out

    def _related_articles(self, text, hint, results, table=None, exclude=(), use_filter=None, limit=None, match="연관조항"):
        """연관 조항 표 방식 (TOPIC_MODE="article", 검색_설계.md 12.5). 검색으로 찾은 조마다 미리 만든 표
        (data/manual/related_articles.json: 조 → [{"대상", "주제", "등급", "이유"}])를 조회해 연관 조를 전부 붙인다.
        리랭커를 쓰지 않는다. 뺀다: 찾은 조, cites로 온 조문, 형제 항, 표끼리 겹치는 조. 순서: 찾은 조 순위 → 등급(핵심 먼저).
        TOPIC_ARTICLE_FILTER면 질문·바꿔 쓴 검색어의 주제어와 겹치는 연관만 남긴다.
        TOPIC_RELATED_LIMIT이 0이면 무제한(글자 예산이 정한다).
        table·exclude·use_filter·limit·match를 주면 사례 링크 표(_case_links)가 같은 절차를 다른 표로 돈다."""
        table = self.related_table if table is None else table
        use_filter = TOPIC_ARTICLE_FILTER if use_filter is None else use_filter
        limit = TOPIC_RELATED_LIMIT if limit is None else limit
        self.last_related = []
        if not table:
            return []
        asked = set(topics.detect(" ".join(x for x in (text, hint) if x), self.topics)) if use_filter else None
        taken = set()
        for e in exclude:
            taken.add((e["doc_id"], _article_base(e["article"])))
        for r in results:
            for e in (r, *r.get("cited", []), *r.get("siblings", [])):
                taken.add((e["doc_id"], _article_base(e["article"])))
        picked = []  # (등급 순서, 찾은 조 순위, 대상 key, 항목)
        for rank, r in enumerate(results):
            src = f"{r['doc_id']}_{_article_base(r['article'])}"
            for link in table.get(src, []):
                doc_id, _, base = link["대상"].partition("_")
                key = (doc_id, base)
                if key in taken or not self.article_rows.get(key):
                    continue
                shown = link.get("주제", "")
                if asked is not None:
                    names = link.get("주제들") if "주제들" in link else [link.get("주제")]
                    matched = [n for n in names if n in asked]
                    if names and not matched:
                        continue
                    shown = matched[0] if matched else shown  # 머리에 질문과 맞은 주제어를 보인다
                taken.add(key)
                picked.append((0 if link.get("등급", "참고") == "핵심" else 1, rank, key, link, shown))
        picked.sort(key=lambda p: (p[1], p[0]))
        out = []
        for grade, rank, key, link, shown in picked:
            ids = self.article_rows[key]
            cid = next((c for c in ids if link.get("주제") in self.chunk_topics.get(c, {})), ids[0])
            h = {"chunk_id": cid, **self.row_info[cid]}
            item = self._article_item(h) if RETURN_ARTICLE else self._entry(h, status=h.get("status"))
            src = results[rank]
            src_title = src.get("문서명") or self.documents.get(src["doc_id"], {}).get("title") or src["doc_id"]
            item.update({"rerank": None, "match": match, "연결상태": match, "주제": [shown],
                         "등급": "핵심" if grade == 0 else "참고",
                         "연관출발": f"{src_title} {_article_base(src['article'])}"})
            if link.get("이유"):
                item["주제이유"] = link["이유"]
            out.append(item)
            if limit and len(out) >= limit:
                break
        self.last_related = [(i["문서명"], i["article"], i["주제"], i["연관출발"], i["등급"]) for i in out]
        return out

    def _case_links(self, text, hint, results, already=()):
        """사례 조문 링크 (config.CASE_LINKS, 검색_설계.md 14절). 찾은 조가 같은 징계·감사 사례에 나온 조문이면 그 사례의
        나머지 조문을 붙인다. 표는 data/manual/case_links.json (scripts/build_case_links.py). 개수 상한 없음(사례당 1~3개).
        링크의 주제어(사례 유형에서 뽑은 topics.json 이름)가 질문·바꿔 쓴 검색어에 있을 때만 따라간다 (징계지침 제17조처럼
        여러 사례가 지나는 조에서 엉뚱한 사례의 조문이 따라오지 않게). already(주제 링크로 이미 붙은 항목)와 겹치는 조는
        뺀다. 사례 본문은 어디에도 넣지 않는다."""
        if not self.case_table or not results:
            return []
        out = self._related_articles(text, hint, results, table=self.case_table, exclude=already, use_filter=True, limit=0, match="사례링크")
        for i in out:
            i["주제"] = [t for t in i["주제"] if t] or ["사례"]
        return out

    # 전체 -------------------------------------------------------------------
    def search(self, text, k=5, filters=None, rerank=True, expand_siblings=True, expand_cites=True,
              vector_weight=HYBRID_VECTOR_WEIGHT, keyword_weight=HYBRID_KEYWORD_WEIGHT, rewritten=None,
              rewritten_future=None):
        """질의 하나에 대해 상위 k개 청크를 돌려준다.

        filters: {"tier": "지침", "doc_ids": ["23234"], "min_year": 2025} (모두 선택)
        rewritten: 질문을 규정 용어로 바꿔 쓴 검색어 (선택). 후보를 찾을 때만 더하고, 리랭킹은 원래 질문으로 한다.
        rewritten_future: rewritten 대신 아직 GPT가 만드는 중인 검색어(concurrent.futures.Future). 원래 질문의
                    벡터·BM25 검색을 먼저 한 뒤에 기다린다. 결과는 rewritten을 넘긴 것과 같고 기다리는 시간만 줄어든다.
        vector_weight, keyword_weight: 기본값은 config.py 값. 가중치 비교(src/eval/sweep_weights.py)에서만 넘긴다.
        반환: [{"chunk_id", "doc_id", "article", "body", "score", "rerank", "match",
                "siblings": [...], "cited": [...]}, ...]
              USE_TOPIC_LINKS이면 첫 항목에 "related": [...] (주제 링크 확장, _related)가 더 붙는다.
        """
        self._refresh_if_stale()
        self.last_related = []
        exact = self.exact_hits(text)
        taken = {h["chunk_id"] for h in exact}

        doc_ids = self._allowed_doc_ids(filters)
        vec_hits = self.vector_hits(text, doc_ids)
        key_hits = self.bm25_hits(text, doc_ids)
        if rewritten_future is not None:
            rewritten = rewritten_future.result()
        # 바꿔 쓴 검색어와 동의어 사전의 규정 용어를 합쳐 후보 찾기에만 더한다 (리랭킹은 원래 질문).
        if hasattr(rewritten, "result"):  # 바꿔쓰기를 동시에 돌리는 중이면 원질문 검색을 마친 뒤에 결과를 받는다
            rewritten = rewritten.result()
        hint = " ".join(x for x in (rewritten, synonyms.expand(text, self.synonym_table),
                                    glossary.signal_terms(text, self.glossary)) if x)
        self.last_hint = hint  # 기록용: 이번 검색에서 후보 찾기에 더해진 검색어(바꿔 쓴 검색어, 동의어 사전, 용어집 신호)
        extra = ()
        if hint:
            extra = (("벡터(바꿔쓴)", self.vector_hits(hint, doc_ids), vector_weight),
                     ("키워드(바꿔쓴)", self.bm25_hits(hint, doc_ids), keyword_weight))
        fused = [h for h in self.fuse(vec_hits, key_hits, vector_weight, keyword_weight, extra)
                if h["chunk_id"] not in taken]

        if rerank:
            fused = self.rerank(text, self.rerank_window(fused))
            if getattr(self, "llm_reranker", None) is not None:  # 2단계: 상위 LLM_RERANK_TOP개를 LLM이 재채점 (실패 문서는 그 자리)
                fused = self.llm_reranker.rerank(text, fused, self.rerank_text)
        self.last_reranked = list(fused)  # 기록용: 리랭크된 후보 전체 (답변 뒤 누락 검사가 6위 이하를 되짚는 데 쓴다)
        # 확인된 다른 개념의 수치 근거는 후보 단계에서 빼서 빈자리를 다음 후보가 채우게 한다 (정확 조회 결과는 건드리지 않는다).
        self.last_glossary_excluded = []
        if self.glossary_filter:
            fused, self.last_glossary_excluded = glossary.filter_candidates(text, fused, self.glossary)
        if GROUP_BY_ARTICLE:
            fused = group_by_article(fused)
        if LLM_SELECT_POOL and rerank and len(fused) > k:
            fused = self._llm_select(text, fused[:LLM_SELECT_POOL], k)
        if RETURN_ARTICLE:
            out = self._as_articles(exact + fused, k, expand_cites)
        else:
            out = []
            for h in (exact + fused)[:k]:
                item = self._entry(h, status=h.get("status"))
                item.update({
                    "score": h.get("score"),
                    "rerank": h.get("rerank"),
                    "llm_rerank": h.get("llm_rerank"),  # 2단계 LLM 점수 (0~10). 끄거나 LLM_RERANK_TOP 밖·실패면 None
                    "match": h.get("match", "정확조회"),
                    "siblings": self._siblings_within_limit(h) if expand_siblings else [],
                    "cited": self._cited(h) if expand_cites else [],
                })
                out.append(item)
        # 주제 링크 확장은 인용 확장과 같은 "답변 근거 넓히기"라 expand_cites를 끈 검색 평가(src/eval/run)에서는 돌지 않는다.
        if expand_cites and out:
            related = []
            if USE_TOPIC_LINKS:
                related = (self._related_articles(text, hint, out) if TOPIC_MODE == "article"
                           else self._related(text, hint, out))
            if CASE_LINKS:
                topic_last = list(self.last_related)
                related = related + self._case_links(text, hint, out, already=related)
                self.last_related = topic_last + self.last_related
            if USE_TOPIC_LINKS or CASE_LINKS:
                out[0]["related"] = related
        return out

    def _as_articles(self, ranked, k, expand_cites=True):
        """항 단위로 찾고 조 전체를 넘기기 (config.RETURN_ARTICLE). ranked는 항 청크의 최종 순위(정확 조회 + 리랭크).

        같은 조의 항은 처음 나온 순위 하나로 합쳐 서로 다른 조 k개를 고르고, 각 조의 모든 항을 항 순서대로 이어 붙인다.
        점수·리랭크·조문 상태는 그 조에서 가장 앞선 항의 값을 쓴다. 용어집 후보 필터가 뺀 항은 이어 붙일 때도 뺀다.
        인용 대상은 그 조의 모든 항이 가진 cites를 합쳐 한 번씩만 따라간다. 형제 항은 이미 다 들어 있어 붙이지 않는다.
        HIGHLIGHT_HITS: 이 조를 상위로 올린 항 앞에 HIT_MARK, 같은 조의 나머지 항 앞에 SIB_MARK를 붙이고
                        (조에 항이 둘 이상일 때만), 단서·예외 문장 앞에 PROVISO_MARK를 붙인다 (mark_provisos).
        LAW_CITES_FROM_HITS: 외부 법령 인용(외부연결)은 검색 결과에 오른 항이 가진 것만 따라간다."""
        dropped = {(d, a) for d, a, _ in self.last_glossary_excluded}
        hit_ids = {h["chunk_id"] for h in ranked}
        firsts, seen = [], set()
        for h in ranked:
            key = (h["doc_id"], _article_base(h["article"]))
            if key not in seen:
                seen.add(key)
                firsts.append(h)
            if len(firsts) >= k:
                break
        out = []
        for h in firsts:
            doc_id, base = h["doc_id"], _article_base(h["article"])
            rows = [r for r in self.store.find_article(doc_id, base) if (doc_id, r["article"]) not in dropped]
            rows.sort(key=lambda r: _order_key(r["article"]))
            mark = HIGHLIGHT_HITS and len(rows) > 1

            def text(r):
                body = mark_provisos(r["body"]) if HIGHLIGHT_HITS else r["body"]
                if not mark:
                    return body
                # 찾은 항(이 조를 상위 k개로 올린 항)은 강하게, 같은 조의 나머지 항은 약하게 표시한다
                return (HIT_MARK if r["chunk_id"] == h["chunk_id"] else SIB_MARK) + body

            item = self._entry(h, status=h.get("status"))
            item.update({
                "chunk_id": f"{doc_id}_{base}", "article": base,
                "hit_chunk": h["chunk_id"],   # 이 조를 올린 항 (화면이 그 항을 강조할 때 씀, 261019)
                "body": "\n".join(text(r) for r in rows) or h["body"],
                "score": h.get("score"), "rerank": h.get("rerank"), "llm_rerank": h.get("llm_rerank"),
                "match": h.get("match", "정확조회"), "siblings": [], "cited": [],
            })
            if expand_cites:
                members = {r["chunk_id"] for r in rows}
                cited, taken = [], set()
                for r in rows:
                    for c in self._cited({"chunk_id": r["chunk_id"], "doc_id": doc_id, "article": r["article"]}):
                        if LAW_CITES_FROM_HITS and c.get("연결상태") == "외부연결" and r["chunk_id"] not in hit_ids:
                            continue  # 찾은 항이 아닌 다른 항의 법령 인용
                        if c["chunk_id"] not in taken and c["chunk_id"] not in members:
                            taken.add(c["chunk_id"])
                            cited.append(c)
                item["cited"] = cited
            out.append(item)
        # 이미 조 전체로 들어간 조의 항을 가리키는 인용은 같은 내용이 두 번 들어가므로 뺀다
        chosen = {(e["doc_id"], e["article"]) for e in out}
        for e in out:
            e["cited"] = [c for c in e["cited"] if (c["doc_id"], _article_base(c["article"])) not in chosen]
        return out

    def search_plan(self, plan, k=5, filters=None, **kw):
        """하위 질문 목록(Generator.plan_queries)을 각각 검색해 합친다 (Azure agentic retrieval 방식).

        plan: [{"질문", "검색어"}, ...]. 하위 질문마다 search()를 따로 부르므로 리랭킹도 하위 질문으로 한다.
              원래 질문 전체로 리랭킹하면 B를 찾는 조문이 질문의 절반과만 맞아서 밀리기 때문이다.
        합치기: 하위 질문별 결과를 1위끼리, 2위끼리 번갈아 담는다(interleave). 어느 하위 질문도 자리를 뺏기지 않는다.
              하위 질문이 둘 이상이면 하나당 최소 3자리가 되도록 k를 늘린다.
        하위 질문이 하나면 search()와 같다(검색어는 rewritten으로 넘긴다).
        """
        total = k if len(plan) <= 1 else max(k, 3 * len(plan))
        lists, hints, excluded = [], [], []
        for sub in plan:
            hits = self.search(sub["질문"], k=total, filters=filters, rewritten=sub.get("검색어"), **kw)
            if len(plan) > 1:
                for h in hits:
                    h["하위질문"] = sub["질문"]
            lists.append(hits)
            hints.append(self.last_hint)
            excluded += self.last_glossary_excluded
        self.last_hint = " / ".join(h for h in hints if h)
        self.last_glossary_excluded = excluded
        merged = interleave(lists, total)
        # 하위 질문별 주제 링크 결과는 그대로 두되, 합친 결과에 이미 있는 조와 서로 겹치는 것은 한 번만 남긴다
        chosen = {(h["doc_id"], _article_base(h["article"])) for h in merged}
        for h in merged:
            kept = []
            for r in h.get("related") or []:
                key = (r["doc_id"], _article_base(r["article"]))
                if key not in chosen:
                    chosen.add(key)
                    kept.append(r)
            if "related" in h:
                h["related"] = kept
        return merged

    def assemble(self, results, budget=CONTEXT_CHAR_BUDGET):
        """검색 결과를 답변 입력으로 조립한다.

        순서: 검색 결과 → 인용 대상 → 같은 주제 조문(주제 링크) → 형제 항. 같은 청크는 한 번만 넣고,
        전체 글자 수가 budget을 넘으면 그 항목은 건너뛴다. 결과는 (문서, 조) 단위로 묶는다.
        """
        seen = set()
        used = 0
        groups = {}
        # 검색 결과 조들을 통째로 넣어도 예산 안이면 그대로 둔다. 넘치면 긴 조부터 같은 길이(cap)로 깎아 모두 들어가게 한다.
        # 한 조가 예산을 다 써서 뒤 순위의 짧은 조(정답일 수 있음)가 통째로 밀려나는 것보다, 긴 조의 먼 항을 줄이는 쪽이 낫다.
        cap = None
        if SHORTEN_LONG:
            lengths = [len(r["body"]) for r in results]
            if sum(lengths) > budget:
                cap = water_level(lengths, budget)

        def add(entry, role, rank):
            nonlocal used
            cid = entry["chunk_id"]
            if cid in seen:
                return
            n = len(entry["body"])
            if cap and n > cap and role in ("정확조회", "검색"):
                short = shorten_article(entry["body"], cap - len(SHORTENED_NOTE))
                if len(short) < n:
                    entry = {**entry, "body": short}
                    n = len(short)
            if used + n > budget:
                return
            seen.add(cid)
            used += n
            key = (entry["doc_id"], _article_base(entry["article"]))
            group = groups.setdefault(key, {"문서명": entry.get("문서명"), "doc_id": entry["doc_id"],
                                            "조": key[1], "items": [], "rank": rank})
            group["rank"] = min(group["rank"], rank)
            group["items"].append({**entry, "역할": role})

        for i, r in enumerate(results):
            add(r, "정확조회" if r.get("match") == "정확조회" else "검색", i)
        for i, r in enumerate(results):
            for c in r.get("cited", []):
                add(c, "인용", i)
        for i, r in enumerate(results):
            # LangChain Document를 거친 항목은 related가 None일 수 있다 (langchain_retriever._META_KEYS)
            for c in r.get("related") or []:
                add(c, "주제", len(results) + i)  # 같은 주제 조문은 검색으로 찾은 조문들 뒤에 둔다
        for i, r in enumerate(results):
            for sib in r.get("siblings", []):
                add(sib, "형제항", i)

        ordered = sorted(groups.values(), key=lambda g: g["rank"])
        for g in ordered:
            g["items"].sort(key=lambda x: _order_key(x["article"]))
            del g["rank"]
        return {"groups": ordered, "chars": used, "budget": budget}

    def exact_hits(self, text):
        """질문이 문서·조(·항)를 확정할 때 해당 조문을 직접 가져온다. 아니면 빈 목록."""
        ref = parse_reference(text, list(self.documents.values()))
        if ref is None:
            return []
        rows = self.store.find_article(ref.doc_id, ref.base, ref.para)
        return [{**r, "match": "정확조회"} for r in rows]


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    s = Searcher()
    for q in sys.argv[1:] or ["감사 종류", "직위해제 기간", "직원채용지침 제2조 제2항"]:
        print(f"\n[{q}]")
        results = s.search(q, k=3)
        ctx = s.assemble(results)
        print(f"  근거 {ctx['chars']}자 / 예산 {ctx['budget']}자")
        for g in ctx["groups"]:
            print(f"  ▸ {g['문서명']} {g['조']}")
            for it in g["items"]:
                print(f"      [{it['역할']}] {it['chunk_id']} ({it['조문상태']}) {it['body'][:35]}".replace("\n", " "))
    s.close()
