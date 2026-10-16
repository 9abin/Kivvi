"""Searcher에 임베딩·리랭커를 바꿔 끼운 검색기. 후보 합치기, 용어집, 조 묶기, 확장, 인용 복구, 근거 조립은 운영 코드(src/search/search.py)를 그대로 쓴다."""
import json
import os
import time
from unittest import mock

import numpy as np

import src.search.search as search_module
from config import RERANK_HEADER
from src.combo.models import doc_vectors, sha
from src.search.search import CANDIDATES, Searcher


class _NoModel:
    """Searcher.__init__이 기본 모델(BGE-M3, BGE 리랭커)을 GPU에 올리지 않게 하는 자리 채움."""

    def __init__(self, *a, **k):
        pass

    def half(self):
        return self


class ComboSearcher(Searcher):
    def __init__(self, embedder=None, reranker=None):
        with mock.patch.object(search_module, "SentenceTransformer", _NoModel), \
                mock.patch.object(search_module, "CrossEncoder", _NoModel):
            super().__init__()  # store, kiwi, glossary, 색인 행, BM25 등은 운영 코드가 그대로 만든다
        self.model = None
        self.embedder = embedder
        self.reranker = reranker
        header = getattr(reranker, "header", None)
        self.rerank_header = RERANK_HEADER if header is None else header
        self._ids = self._mat = self._doc_of = None
        self.embed_time = 0.0  # 질문 임베딩에 쓴 시간(초). 후보 단계가 질문마다 읽고 0으로 되돌린다

    @staticmethod
    def embed_text(r):
        """색인 때와 같은 임베딩 입력(src/index/build.py embed_text): 장이 있으면 머리말로 붙인다."""
        return f"[{r['section']}]\n{r['body_search']}" if r.get("section") else (r["body_search"] or "")

    def load_doc_vectors(self, out_dir, label):
        """문서 벡터를 읽거나 만든다: <out_dir>/emb_<label>.npy (+ .meta.json, .ids.json)."""
        self._ids = list(self.row_info)
        texts = [self.embed_text(self.row_info[c]) for c in self._ids]
        meta = {"model": self.embedder.name, "doc_prefix": self.embedder.doc_prefix, "max_seq_length": self.embedder.max_seq_length,
                "n": len(texts), "text_hash": sha(*texts)}
        path = os.path.join(out_dir, f"emb_{label}.npy")
        self._mat = doc_vectors(self.embedder, texts, path, meta)
        json.dump(self._ids, open(path[:-4] + ".ids.json", "w", encoding="utf-8"))
        self._doc_of = np.array([self.row_info[c]["doc_id"] for c in self._ids])

    def vector_hits(self, text, doc_ids):
        t = time.perf_counter()
        qv = self.embedder.encode_queries([text])[0]
        self.embed_time += time.perf_counter() - t
        sims = self._mat @ qv
        if doc_ids is not None:
            sims = np.where(np.isin(self._doc_of, list(doc_ids)), sims, -np.inf)
        top = [i for i in np.argsort(-sims)[:CANDIDATES] if sims[i] > -np.inf]
        return [{**self.row_info[self._ids[i]], "chunk_id": self._ids[i], "score": float(sims[i])} for i in top]

    def rerank(self, text, cands):
        """리랭커 없음(R0)이면 점수 없이 RRF 순서를 그대로 쓴다."""
        if self.reranker is None:
            for c in cands:
                c["rerank"] = None
            return cands
        return super().rerank(text, cands)
