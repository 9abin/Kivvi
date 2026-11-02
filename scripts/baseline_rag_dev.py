"""강한 RAG 기준선(R1): 개정문으로 영향받는 조를 찾는 일반적인 RAG 검색 (영향 점검 평가셋 68, 개발 분할).

  uv run python scripts/baseline_rag_dev.py                 # 개발 분할
  uv run python scripts/baseline_rag_dev.py --split 봉인 --yes-open-sealed   # 봉인은 한 번만

사다리 (각 단계가 앞 단계 위에 하나씩 쌓인다):
  R0   수정한 조 원문과 임베딩이 가까운 조 (가장 단순)
  R1a  질의 = 조 제목 + 수정되는 항(없으면 조) 원문 + 개정문, 밀집 검색(bge-m3)
  R1b  + BM25 혼합 (글자 2-gram, RRF)
  R1c  + 리랭커(bge-reranker-v2-m3)   <- 「강한 RAG」
조 단위로 모은다 (청크 중 가장 높은 것). 벡터 색인은 본문 조의 항 단위 청크만 있다 (부칙 · 별표 · 별지는 색인에 없음 → 그런 정답은 이 방식으로 닿을 수 없다).
RAG는 늘 K개를 돌려주므로 「영향 없음」을 말하지 못한다. 후보 상한은 지식 그래프 비교와 같다.
"""
import argparse
import collections
import glob
import json
import math
import os
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("HF_HUB_OFFLINE", "1")

from regcheck.impact_eval import Truth  # noqa: E402

K = ROOT / "eval/impact_label_kit/산출"
V = ROOT / "out/vector/BAAI__bge-m3"


def jl(p):
    return [json.loads(l) for l in Path(p).read_text(encoding="utf-8").splitlines() if l.strip()]


def grams(s, n=2):
    s = re.sub(r"[^0-9A-Za-z가-힣]", "", s)
    return [s[i:i + n] for i in range(len(s) - n + 1)]


class BM25:
    def __init__(self, docs, k1=1.5, b=0.75):
        self.k1, self.b = k1, b
        self.tf = [collections.Counter(grams(d)) for d in docs]
        self.len = np.array([sum(t.values()) for t in self.tf], dtype=float)
        self.avg = self.len.mean()
        df = collections.Counter()
        for t in self.tf:
            df.update(t.keys())
        n = len(docs)
        self.idf = {g: math.log(1 + (n - c + 0.5) / (c + 0.5)) for g, c in df.items()}

    def scores(self, query):
        q = collections.Counter(grams(query))
        out = np.zeros(len(self.tf))
        for g in q:
            if g not in self.idf:
                continue
            for i, t in enumerate(self.tf):
                f = t.get(g)
                if f:
                    out[i] += self.idf[g] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.len[i] / self.avg))
        return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="개발")
    ap.add_argument("--yes-open-sealed", action="store_true")
    ap.add_argument("--pool", type=int, default=50, help="리랭크 전 후보 청크 수")
    a = ap.parse_args()
    if a.split == "봉인" and not a.yes_open_sealed:
        sys.exit("봉인 분할은 한 번만 연다.")
    import torch
    from sentence_transformers import CrossEncoder, SentenceTransformer

    regs = {r["reg_id"]: r for r in (json.loads(Path(p).read_text(encoding="utf-8")) for p in glob.glob(str(ROOT / "out/parsed/[0-9]*.json")))}
    nodes = {r["id"]: {k: v for k, v in r.items() if k != "id"} for r in jl(ROOT / "out/kg/nodes.jsonl")}
    T = Truth(nodes, regs)
    emb = np.load(V / "embeddings.npy"); emb = emb / np.linalg.norm(emb, axis=1, keepdims=True)
    chunks = jl(V / "chunks.jsonl")
    art = np.array([c["article_id"] for c in chunks])
    bm = BM25([c["text"] for c in chunks])
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    enc = SentenceTransformer("BAAI/bge-m3", device=dev)
    rer = CrossEncoder("BAAI/bge-reranker-v2-m3", device=dev, max_length=512)
    art_text = {x["id"]: x["norm"] for r in regs.values() for x in r["articles"]}
    item_text = {}
    for r in regs.values():
        for x in r["articles"]:
            for it in x["items"]:
                item_text[(x["id"], it["path"][0])] = it["norm"]

    def to_articles(order, exclude, cap):
        seen, out = set(), []
        for j in order:
            x = art[j]
            if x not in seen and x not in exclude:
                seen.add(x); out.append(x)
            if len(out) >= cap:
                break
        return out

    ch = {r["id"]: r for r in jl(K / "changes.jsonl")}; fin = {r["id"]: r for r in jl(K / "labels_final.jsonl")}
    caps = (10, 20)
    rec = {c: collections.defaultdict(lambda: [0, 0, 0]) for c in caps}  # [적중, 후보수, 적중(본문만)]
    tot = [0, 0]
    for i in sorted(ch):
        c = ch[i]; s = c["수정"]
        if c["분할"] != a.split or not fin[i]["영향"]:
            continue
        st = T.start_of(s)
        body = item_text.get((st, s.get("항"))) if s.get("항") else None
        qtext = f"{nodes[st].get('title', '')}\n{(body or art_text.get(st, ''))[:600]}\n{s['개정문']}"
        qa = enc.encode([qtext], normalize_embeddings=True)[0]
        dense = emb @ qa
        d_rank = np.argsort(-dense)
        b_sc = bm.scores(s["개정문"] + " " + nodes[st].get("title", "") + " " + (body or "")[:300])
        b_rank = np.argsort(-b_sc)
        rrf = np.zeros(len(chunks))
        for r_, j in enumerate(d_rank[:200]):
            rrf[j] += 1 / (60 + r_)
        for r_, j in enumerate(b_rank[:200]):
            rrf[j] += 1 / (60 + r_)
        h_rank = np.argsort(-rrf)
        pool = [j for j in h_rank[: a.pool * 2] if art[j] != st][: a.pool]
        sc = rer.predict([(qtext[:1200], chunks[j]["text"]) for j in pool], batch_size=16, show_progress_bar=False)
        rr = [pool[k] for k in np.argsort(-np.asarray(sc))]
        # R0: 수정한 조 청크 벡터와의 유사도
        s0 = (emb @ emb[[j for j, x in enumerate(art) if x == st]].T).max(axis=1)
        truth = {T.resolve(e) for e in fin[i]["영향"]} - {None}
        body_truth = {n for n in truth if nodes.get(n, {}).get("type") == "Article"}
        tot[0] += len(truth); tot[1] += len(body_truth)
        for cap in caps:
            lists = {"R0 수정한 조와 비슷한 조": to_articles(np.argsort(-s0), {st}, cap),
                     "R1a 개정문 질의 + 밀집": to_articles(d_rank, {st}, cap),
                     "R1b + BM25 혼합": to_articles(h_rank, {st}, cap),
                     "R1c + 리랭커 (강한 RAG)": to_articles(rr, {st}, cap)}
            for name, lst in lists.items():
                rec[cap][name][0] += len(truth & set(lst)); rec[cap][name][1] += len(lst); rec[cap][name][2] += len(body_truth & set(lst))
        print(f"{i} {s['규정']} {s['조']} [{s['종류']}] 정답 {len(truth)} (본문 조 {len(body_truth)}) | R1c 상위20 적중 {len(truth & set(to_articles(rr, {st}, 20)))}")
    for cap in caps:
        print(f"\n[후보 상한 {cap}개/문항] 정답 {tot[0]}건 (본문 조 {tot[1]}건)")
        for name, (h, l, hb) in rec[cap].items():
            print(f"  {name:26} 재현율 {h}/{tot[0]} = {h / tot[0]:.0%} | 본문 조만 {hb}/{tot[1]} = {hb / tot[1]:.0%} | 후보 {l}개 중 정답 {h} ({h / l:.0%})")


if __name__ == "__main__":
    main()
