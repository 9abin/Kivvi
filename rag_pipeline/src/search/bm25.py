"""BM25 역색인. PostgreSQL 확장 없이 Python에서 계산한다 (`검색_설계.md` 2절).

색인 대상 청크(4,465개)가 많지 않아, 전체 토큰을 메모리에 올려 역색인을 만든다.
"""
import math
from collections import Counter, defaultdict

from config import BM25_B, BM25_K1


class BM25:
    def __init__(self, docs, k1=BM25_K1, b=BM25_B):
        """docs: [(chunk_id, 토큰 목록), ...]. 토큰은 Kiwi로 나눈 형태소(body_tokens)다."""
        self.k1, self.b = k1, b
        self.ids = [d[0] for d in docs]
        self.doc_len = [len(d[1]) for d in docs]
        self.n = len(docs)
        self.avgdl = (sum(self.doc_len) / self.n) if self.n else 1.0

        self.inverted = defaultdict(list)  # 형태소 -> [(문서 인덱스, 빈도), ...]
        df = Counter()
        for i, (_, tokens) in enumerate(docs):
            c = Counter(tokens)
            for term, f in c.items():
                self.inverted[term].append((i, f))
            df.update(c.keys())
        self.idf = {t: math.log(1 + (self.n - n + 0.5) / (n + 0.5)) for t, n in df.items()}

    def top(self, query_tokens, k=50, allowed_ids=None):
        """BM25 점수 상위 k개. allowed_ids를 주면 그 청크 ID만 후보로 본다 (위계 필터 등)."""
        scores = defaultdict(float)
        for term in set(query_tokens):
            idf = self.idf.get(term)
            if idf is None:
                continue
            for idx, f in self.inverted[term]:
                dl = self.doc_len[idx]
                denom = f + self.k1 * (1 - self.b + self.b * dl / self.avgdl)
                scores[idx] += idf * f * (self.k1 + 1) / denom

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        out = []
        for idx, score in ranked:
            cid = self.ids[idx]
            if allowed_ids is not None and cid not in allowed_ids:
                continue
            out.append((cid, score))
            if len(out) >= k:
                break
        return out
