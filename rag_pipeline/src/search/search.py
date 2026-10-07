"""검색 파이프라인 (`docs/검색_설계.md` 9절 순서).

  1. 메타데이터 필터로 문서 범위를 먼저 좁힌다 (위계, 문서, 개정연도)
  2. 벡터 검색과 BM25 검색으로 후보를 각각 뽑는다
  3. 가중 RRF로 합친다 (벡터 0.7 : 키워드 0.3, config.py)
  4. 상위 후보를 리랭커로 다시 점수 매긴다
  5. 상위 k개에 형제 항을 붙이고 (3,000자 상한), cites를 1단계 따라간다

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
                    RERANK_HEADER, RERANK_MODEL, RRF_K)
from src.search.bm25 import BM25
from config import CONTEXT_CHAR_BUDGET
from src.search.exact import parse_reference
from src.store.pgstore import PgStore

CANDIDATES = 50         # 방식별로 합치기 전 가져올 후보 수
RERANK_TOP = 30         # 리랭커에 넘기는 후보 수 (RRF 상위)
SIBLING_LIMIT = 3000    # 형제 항을 붙일 때의 글자 수 상한 (재색인 설계 4절)
CITE_LIMIT = 5          # 청크 하나당 cites에서 따라갈 최대 개수
CITE_OK_STATUS = ("정상연결", "정정 확정")  # 실제 대상 청크가 있는 인용만 따라간다

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


class Searcher:
    def __init__(self):
        self.store = PgStore()
        self.kiwi = Kiwi()
        self.model = SentenceTransformer(EMBED_MODEL, device="cuda")
        self.model.half()
        self.reranker = CrossEncoder(RERANK_MODEL, device="cuda")
        self.rerank_header = RERANK_HEADER
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
    def fuse(self, vec_hits, key_hits):
        score = defaultdict(float)
        info = {}
        origin = defaultdict(set)
        for name, hits, weight in (("벡터", vec_hits, HYBRID_VECTOR_WEIGHT),
                                   ("키워드", key_hits, HYBRID_KEYWORD_WEIGHT)):
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
        """리랭커에 넣는 청크 글자. 머리말을 켜면 '[문서명 조항] 본문_검색' 형태가 된다."""
        row = self.row_info[chunk_id]
        if not self.rerank_header:
            return row["body_search"]
        title = self.documents.get(row["doc_id"], {}).get("title") or row["doc_id"]
        return f"[{title} {row['article']}] {row['body_search']}"

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
        """청크의 cites 중 실제 대상이 있는 것을 1단계만 따라간다. 대상이 부칙·삭제여도 상태를 붙인다."""
        targets = []
        for c in self.row_info.get(hit["chunk_id"], {}).get("cites") or []:
            if c.get("대상") and c.get("상태") in CITE_OK_STATUS and c["대상"] != hit["chunk_id"]:
                targets.append(c)
            if len(targets) >= CITE_LIMIT:
                break
        if not targets:
            return []
        found = {r["chunk_id"]: r for r in self.store.get([t["대상"] for t in targets])}
        out = []
        for t in targets:
            r = found.get(t["대상"])
            if r:
                entry = self._entry(r)
                entry["인용원문"] = t["원문"]
                entry["연결상태"] = t["상태"]
                out.append(entry)
        return out

    # 전체 -------------------------------------------------------------------
    def search(self, text, k=5, filters=None, rerank=True, expand_siblings=True, expand_cites=True):
        """질의 하나에 대해 상위 k개 청크를 돌려준다.

        filters: {"tier": "지침", "doc_ids": ["23234"], "min_year": 2025} (모두 선택)
        반환: [{"chunk_id", "doc_id", "article", "body", "score", "rerank", "match",
                "siblings": [...], "cited": [...]}, ...]
        """
        self._refresh_if_stale()
        exact = self.exact_hits(text)
        taken = {h["chunk_id"] for h in exact}

        doc_ids = self._allowed_doc_ids(filters)
        vec_hits = self.vector_hits(text, doc_ids)
        key_hits = self.bm25_hits(text, doc_ids)
        fused = [h for h in self.fuse(vec_hits, key_hits) if h["chunk_id"] not in taken]

        if rerank:
            fused = self.rerank(text, fused[:RERANK_TOP])
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
