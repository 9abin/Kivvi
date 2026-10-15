"""검색 파이프라인 (`docs/검색_설계.md` 9절 순서).

  1. 메타데이터 필터로 문서 범위를 먼저 좁힌다 (위계, 문서, 개정연도)
  2. 벡터 검색과 BM25 검색으로 후보를 각각 뽑는다
  3. 가중 RRF로 합친다 (벡터 0.7 : 키워드 0.3, config.py)
  4. 상위 후보를 리랭커로 다시 점수 매긴다
  5. 상위 k개에 형제 항을 붙이고 (3,000자 상한), cites를 1단계 따라간다
     (청크가 조 단위라 형제 항은 보통 없다. 긴 별표 조각처럼 같은 조항 이름을 나눠 가진 청크만 붙는다)

사용 예:
  from src.search.search import Searcher
  s = Searcher()
  s.search("직위해제 기간", k=5)
  s.search("감사 종류", k=5, filters={"tier": "규정"})
  s.search("감사 종류", k=5, filters={"doc_ids": ["23234"], "min_year": 2025})
"""
import re
from collections import defaultdict

from kiwipiepy import Kiwi
from sentence_transformers import CrossEncoder, SentenceTransformer

from config import (EMBED_MODEL, EMBED_QUERY_INSTRUCT, HIGHLIGHT_HITS, HYBRID_KEYWORD_WEIGHT, HYBRID_VECTOR_WEIGHT,
                    LAW_CITES_FROM_HITS, LLM_RERANK, RERANK_HEADER, RERANK_MODEL, RETURN_ARTICLE, RRF_K, USE_SYNONYMS)
from src.search import glossary, synonyms
from src.search.bm25 import BM25
from config import CONTEXT_CHAR_BUDGET
from src.search.exact import parse_reference
from src.store.pgstore import PgStore

CANDIDATES = 50         # 방식별로 합치기 전 가져올 후보 수
RERANK_TOP = 30         # 리랭커에 넘기는 후보 수 (RRF 상위)
SIBLING_LIMIT = 3000    # 형제 항을 붙일 때의 글자 수 상한 (재색인 설계 4절)
CITE_LIMIT = 5          # 청크 하나당 cites에서 따라갈 최대 개수
CITE_OK_STATUS = ("정상연결", "정정 확정", "외부연결")  # 실제 대상 청크가 있는 인용만 따라간다
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
        self.model = SentenceTransformer(EMBED_MODEL, device="cuda")
        self.model.half()
        self.reranker = CrossEncoder(RERANK_MODEL, device="cuda")
        self.rerank_header = RERANK_HEADER
        self.synonym_table = synonyms.load() if USE_SYNONYMS else {}
        self.glossary = glossary.load()
        self.glossary_filter = True  # 후보 단계 제외 (전후 비교를 위해 끌 수 있다)
        self.last_glossary_excluded = []
        self.last_hint = ""
        # 2단계 LLM 리랭커 (LLM_RERANK, 검색_설계.md 5절): 교차 인코더 상위 LLM_RERANK_TOP개를 LLM이 다시 채점한다
        self.llm_reranker = None
        if LLM_RERANK:
            from src.search.llm_rerank import LLMReranker
            self.llm_reranker = LLMReranker()
        self._load_index()

    def _load_index(self):
        self.version = self.store.index_version()
        self.documents = {d["doc_id"]: {**d, "year": _year(d["revision"])}
                          for d in self.store.load_documents()}
        rows = self.store.load_indexed_rows()
        self.row_info = {r["chunk_id"]: r for r in rows}
        docs = [(r["chunk_id"], (r["body_tokens"] or "").split()) for r in rows]
        self.bm25 = BM25(docs)

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
        """
        self._refresh_if_stale()
        exact = self.exact_hits(text)
        taken = {h["chunk_id"] for h in exact}

        doc_ids = self._allowed_doc_ids(filters)
        vec_hits = self.vector_hits(text, doc_ids)
        key_hits = self.bm25_hits(text, doc_ids)
        if rewritten_future is not None:
            rewritten = rewritten_future.result()
        # 바꿔 쓴 검색어와 동의어 사전의 규정 용어를 합쳐 후보 찾기에만 더한다 (리랭킹은 원래 질문).
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
            fused = self.rerank(text, fused[:RERANK_TOP])
            if getattr(self, "llm_reranker", None) is not None:  # 2단계: 상위 LLM_RERANK_TOP개를 LLM이 재채점 (실패 문서는 그 자리)
                fused = self.llm_reranker.rerank(text, fused, self.rerank_text)
        # 확인된 다른 개념의 수치 근거는 후보 단계에서 빼서 빈자리를 다음 후보가 채우게 한다 (정확 조회 결과는 건드리지 않는다).
        self.last_glossary_excluded = []
        if self.glossary_filter:
            fused, self.last_glossary_excluded = glossary.filter_candidates(text, fused, self.glossary)
        if RETURN_ARTICLE:
            return self._as_articles(exact + fused, k, expand_cites)
        top = (exact + fused)[:k]

        out = []
        for h in top:
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
        return interleave(lists, total)

    def assemble(self, results, budget=CONTEXT_CHAR_BUDGET):
        """검색 결과를 답변 입력으로 조립한다.

        순서: 검색 결과 → 인용 대상 → 형제 항. 같은 청크는 한 번만 넣고,
        전체 글자 수가 budget을 넘으면 그 항목은 건너뛴다. 결과는 (문서, 조) 단위로 묶는다.
        """
        seen = set()
        used = 0
        groups = {}

        def add(entry, role, rank):
            nonlocal used
            cid = entry["chunk_id"]
            if cid in seen:
                return
            n = len(entry["body"])
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
