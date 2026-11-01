"""온톨로지 그래프 검색: 질문 -> 개념·규정문 -> 그 규정문이 있는 조.

벡터·키워드와 나란히 도는 세 번째 검색 경로다. 합치는 방식은 설정 [onto] mode로 고른다.
  A  그래프가 찾은 조의 청크를 리랭커 후보에 넣고, 리랭크 점수로 한 줄로 세운 뒤 상위 k를 고른다
  B  하이브리드 상위 k는 그대로 두고, 그래프가 찾은 조를 최대 max개까지 검색 결과 뒤에 따로 붙인다
질문에서 출발점을 찾는 방법은 [onto] source로 고른다.
  understand  모델이 개념 목록에서 개념을 고른다 (scripts/onto_expand_eval.py와 같은 코드·캐시)
  stmt        규정문 요지를 임베딩해 두고, 질문(과 바꿔 쓴 질문)과 가까운 규정문의 조를 고른다. 개념 이름을 몰라도 닿는다
  both        두 결과를 번갈아 늘어놓는다
  triple      자유 트리플(scripts/triple_build.py)의 관계·개체 글로 stmt와 같은 방식으로 고른다. onto.dir은 triples.json이 있는 폴더
"""
import collections
import hashlib
import json
import re
import os
import sys

import numpy as np
import requests

import config as app_config

sys.path.insert(0, "scripts")
import onto_expand_eval as OE  # noqa: E402

EMB_MODEL, OLLAMA = "qwen3-embedding:4b-q8_0", "http://127.0.0.1:11434/api/embed"


def embed(texts, bs=32):
    out = []
    for i in range(0, len(texts), bs):
        r = requests.post(OLLAMA, json={"model": EMB_MODEL, "input": texts[i:i + bs], "truncate": True}, timeout=600)
        r.raise_for_status()
        out.append(np.array(r.json()["embeddings"], dtype=np.float32))
    v = np.vstack(out)
    return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-12)


class OntoSearch:
    def __init__(self, onto_dir, gold, cache_name, source="understand", rewrites=None, top_stmt=20, max_df=10, stmt_title=False, deep=0):
        # deep: 조를 더 깊이(규정문 deep개까지) 훑는다. 리랭커 후보로 쓸 조 목록용이고, nodes()(앞 top_stmt개)는 그대로다
        self.source, self.top, self.max_df, self.stmt_title, self.deep = source, top_stmt, max_df, stmt_title, deep
        self.stmt_deep = {}
        if source in ("triple", "triple_ent"):  # 자유 트리플 (scripts/triple_build.py): 개체·관계 글을 임베딩해 같은 방식으로 고른다
            self.O, self.picks = None, {}
            rows = self._triple_rows(onto_dir)
            # 개체 따라가기(triple_ent): 고른 트리플의 개체가 나오는 다른 조로 한 번 건넌다. 조 max_df개 넘게 나오는 흔한 개체는 건너지 않는다
            self.ent_arts = collections.defaultdict(set)
            for n, _, ents in rows:
                for e in ents:
                    self.ent_arts[e].add(n)
            self.stmt_nodes = self._stmt_search(onto_dir, gold, rewrites or {}, rows)
            return
        self.O = OE.load(onto_dir)
        self.picks = OE.understand(gold, self.O, os.path.join(onto_dir, f"understand_{cache_name}.json")) if source != "stmt" else {}
        self.stmt_nodes = self._stmt_search(onto_dir, gold, rewrites or {}) if source != "understand" else {}

    @staticmethod
    def _triple_rows(d):
        tr = json.load(open(os.path.join(d, "triples.json"), encoding="utf-8"))
        rows = []
        key = lambda x: re.sub(r"[\s·ㆍ‧]", "", x)  # noqa: E731
        for aid, v in tr.items():
            rows += [(OE.node(aid), f"{r['출발']} -{r['관계']}-> {r['도착']}: {r['설명']}", [key(r["출발"]), key(r["도착"])]) for r in v["관계"]]
            rows += [(OE.node(aid), f"{e['이름']}: {e['설명']}", [key(e["이름"])]) for e in v["개체"] if e.get("설명")]
        return rows

    def _stmt_search(self, onto_dir, gold, rewrites, rows=None):
        if rows is None:
            ex = json.load(open(os.path.join(onto_dir, "extract.json"), encoding="utf-8"))
            head = {}
            if self.stmt_title:  # 앞에 "규정 이름 > 조 제목"을 붙인다: 요지만으로는 어느 규정의 어떤 조인지 몰라 비슷한 요지끼리 섞인다 (점검 F7-h)
                from onto_build import read_items
                arts = read_items()
                head = {aid: f"{arts[aid]['doc']} > {arts[aid]['title'] or ''} | " for aid in ex if aid in arts}
            rows = [(OE.node(aid), head.get(aid, "") + f"{s['개념']}: {s['요지']}" + (f" ({s['조건원문'][:80]})" if s.get("조건원문") else ""), [])
                    for aid, v in ex.items() for s in v["규정문"]]
        texts = [t for _, t, _ in rows]
        key = hashlib.sha1("\n".join(texts).encode("utf-8")).hexdigest()[:12]
        path = os.path.join(onto_dir, f"stmt_emb_{key}.npy")
        if os.path.exists(path):
            D = np.load(path)
        else:
            D = embed(texts)
            np.save(path, D)
        qs = [g["q"] for g in gold]
        Q = embed([app_config.EMBED_QUERY_INSTRUCT + q for q in qs])
        sims = Q @ D.T
        rw = [rewrites.get(g["id"]) for g in gold]
        if any(rw):  # 바꿔 쓴 질문도 쓴다: 규정문마다 두 질문 중 더 가까운 쪽 점수
            R = embed([app_config.EMBED_QUERY_INSTRUCT + (r or q) for r, q in zip(rw, qs)])
            sims = np.maximum(sims, R @ D.T)
        out = {}
        for i, g in enumerate(gold):
            order, order_deep = [], []
            for rank_, j in enumerate(np.argsort(-sims[i])[:max(self.top, self.deep)]):
                if rows[j][0] not in order_deep:
                    order_deep.append(rows[j][0])
                if rank_ >= self.top:
                    continue
                if rows[j][0] not in order:
                    order.append(rows[j][0])
                if self.source == "triple_ent":  # 이 트리플의 개체가 나오는 다른 조를 바로 뒤에
                    for e in rows[j][2]:
                        arts = self.ent_arts.get(e, ())
                        if len(e) >= 2 and len(arts) <= self.max_df:
                            order += [a for a in sorted(arts) if a not in order]
            out[g["id"]] = order
            self.stmt_deep[g["id"]] = order_deep
        return out

    def deep_nodes(self, qid, n):
        """규정문 검색에서 가까운 순서의 조 앞 n개 (리랭커 후보용)."""
        return self.stmt_deep.get(qid, [])[:n]

    def nodes(self, qid):
        """찾은 조를 가까운 순서로 늘어놓는다 (understand는 모델이 고른 순서, 한 개념 안에서는 조 번호 순)."""
        und = []
        for p in self.picks.get(qid, []):
            for n in sorted(OE.understood_nodes([p], self.O, "이해")):
                if n not in und:
                    und.append(n)
        st = self.stmt_nodes.get(qid, [])
        if self.source == "understand":
            return und
        if self.source in ("stmt", "triple", "triple_ent"):
            return st
        out = []
        for pair in zip(st, und):
            out += [n for n in pair if n not in out]
        return out + [n for n in st + und if n not in out]
