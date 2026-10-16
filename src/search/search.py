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

from config import (EMBED_MODEL, HYBRID_KEYWORD_WEIGHT, HYBRID_VECTOR_WEIGHT,
                    RERANK_HEADER, RERANK_MODEL, RRF_K, USE_SYNONYMS)
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
        self.model = SentenceTransformer(EMBED_MODEL, device="cuda")
        self.model.half()
        self.reranker = CrossEncoder(RERANK_MODEL, device="cuda")
        self.rerank_header = RERANK_HEADER
        self._select_llm = None
        self.synonym_table = synonyms.load() if USE_SYNONYMS else {}
        self.glossary = glossary.load()
        self.glossary_filter = True  # 후보 단계 제외 (전후 비교를 위해 끌 수 있다)
        self.last_glossary_excluded = []
        self.last_hint = ""
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
        vec = self.model.encode([text], normalize_embeddings=True, convert_to_numpy=True)[0]
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

    # 전체 -------------------------------------------------------------------
    def search(self, text, k=5, filters=None, rerank=True, expand_siblings=True, expand_cites=True,
              vector_weight=HYBRID_VECTOR_WEIGHT, keyword_weight=HYBRID_KEYWORD_WEIGHT, rewritten=None):
        """질의 하나에 대해 상위 k개 청크를 돌려준다.

        filters: {"tier": "지침", "doc_ids": ["23234"], "min_year": 2025} (모두 선택)
        rewritten: 질문을 규정 용어로 바꿔 쓴 검색어 (선택). 후보를 찾을 때만 더하고, 리랭킹은 원래 질문으로 한다.
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
            fused = self.rerank(text, fused[:RERANK_TOP])
        # 확인된 다른 개념의 수치 근거는 후보 단계에서 빼서 빈자리를 다음 후보가 채우게 한다 (정확 조회 결과는 건드리지 않는다).
        self.last_glossary_excluded = []
        if self.glossary_filter:
            fused, self.last_glossary_excluded = glossary.filter_candidates(text, fused, self.glossary)
        if GROUP_BY_ARTICLE:
            fused = group_by_article(fused)
        if LLM_SELECT_POOL and rerank and len(fused) > k:
            fused = self._llm_select(text, fused[:LLM_SELECT_POOL], k)
        top = (exact + fused)[:k]

        out = []
        for h in top:
            item = self._entry(h, status=h.get("status"))
            item.update({
                "score": h.get("score"),
                "rerank": h.get("rerank"),
                "match": h.get("match", "정확조회"),
                "siblings": self._siblings_within_limit(h) if expand_siblings else [],
                "cited": self._cited(h) if expand_cites else [],
            })
            out.append(item)
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
