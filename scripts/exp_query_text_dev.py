"""실험: RAG 탐색의 질의를 무엇으로 만들 것인가 — 원래 조항 / 지워진 말 / 바뀐 뒤 문구 / 변경분만 / 개정문 (영향 점검 평가셋 68, 개발 분할 기본).

  uv run python scripts/exp_query_text_dev.py [--splits 개발]
질의 변형 (검색은 모두 같다: 밀집 bge-m3 + BM25 혼합 RRF, 본문 조 + 부칙·별표·서식 임베딩, 후보 상한 같음):
  A 원래 조항   조 제목 + 수정되는 항(없으면 조)의 현행 원문
  B 지워진 말   조 제목 + 바뀌기 전 문구
  C 바뀐 뒤     조 제목 + 개정 후 해당 항 원문
  D 변경분만    조 제목 + 지워진 말 + 새 말
  E 개정문      개정문 문장만
  F 현행 + 개정  A + C를 이어 붙임 (둘 다)
  G 후보 합집합  A와 C의 결과를 따로 구해 합침 (각각 상위 k/2)
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

from regcheck.amend import apply, parse_amendment  # noqa: E402
from regcheck.change_query import _art_text, analyze  # noqa: E402
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
        self.inv = collections.defaultdict(list)
        for i, t in enumerate(self.tf):
            for g, f in t.items():
                self.inv[g].append((i, f))

    def scores(self, query):
        out = np.zeros(len(self.tf))
        for g in set(grams(query)):
            for i, f in self.inv.get(g, ()):
                out[i] += self.idf[g] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.len[i] / self.avg))
        return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--splits", default="개발")
    a = ap.parse_args()
    from sentence_transformers import SentenceTransformer
    regs = {r["reg_id"]: r for r in (json.loads(Path(p).read_text(encoding="utf-8")) for p in glob.glob(str(ROOT / "out/parsed/[0-9]*.json")))}
    nodes = {r["id"]: {k: v for k, v in r.items() if k != "id"} for r in jl(ROOT / "out/kg/nodes.jsonl")}
    T = Truth(nodes, regs)
    emb = np.load(V / "embeddings.npy"); emb = emb / np.linalg.norm(emb, axis=1, keepdims=True)
    chunks = jl(V / "chunks.jsonl")
    ext = np.load(ROOT / "out/kg/ext_emb.npy"); ext_ids = json.loads((ROOT / "out/kg/ext_ids.json").read_text(encoding="utf-8"))
    ids = [c["article_id"] for c in chunks] + ext_ids
    E = np.vstack([emb, ext])
    texts = [c["text"] for c in chunks]
    for r in regs.values():
        for ad in r.get("addenda") or []:
            for x in ad.get("articles") or []:
                if x["id"] in ext_ids:
                    pass
    ext_text = {}
    for r in regs.values():
        for ad in r.get("addenda") or []:
            for x in ad.get("articles") or []:
                ext_text[x["id"]] = x["norm"]
        for ap_ in r.get("appendices") or []:
            ext_text[ap_["id"]] = (ap_.get("title") or "") + "\n" + (ap_.get("text") or "")
    texts += [ext_text.get(k, "") for k in ext_ids]
    bm = BM25(texts)
    enc = SentenceTransformer("BAAI/bge-m3", device="cuda")
    enc.max_seq_length = 512

    def rank(q, exclude, k):
        qa = enc.encode([q[:1200]], normalize_embeddings=True)[0]
        d = np.argsort(-(E @ qa))[:200]
        b = np.argsort(-bm.scores(q))[:200]
        rrf = collections.defaultdict(float)
        for r_, j in enumerate(d):
            rrf[j] += 1 / (60 + r_)
        for r_, j in enumerate(b):
            rrf[j] += 1 / (60 + r_)
        seen, out = set(), []
        for j in sorted(rrf, key=lambda j: -rrf[j]):
            x = ids[j]
            if x not in seen and x not in exclude:
                seen.add(x); out.append(x)
            if len(out) >= k:
                break
        return out

    ch = {r["id"]: r for r in jl(K / "changes.jsonl")}; fin = {r["id"]: r for r in jl(K / "labels_final.jsonl")}
    names = ["A 원래 조항", "B 지워진 말", "C 바뀐 뒤", "D 변경분만", "E 개정문", "F 현행+개정", "G 현행·개정 각각 합침"]
    H = {c: collections.Counter() for c in (10, 20)}; tot = 0; per = collections.defaultdict(dict)
    for i in sorted(ch):
        c = ch[i]; s = c["수정"]
        if c["분할"] not in a.splits.split(",") or not fin[i]["영향"]:
            continue
        st = T.start_of(s)
        if not st:
            continue
        rid = T.by_name[s["규정"]]
        q = analyze(regs[rid], s["개정문"], i)
        if q.kind == "분석 실패":
            continue
        new_reg, _ = apply(regs[rid], parse_amendment(s["개정문"]))
        old_a = next(x for x in regs[rid]["articles"] if x["id"] == st)
        new_a = next((x for x in new_reg["articles"] if x["label"] == old_a["label"]), old_a)
        title = old_a.get("title") or ""
        item_old = next((it["norm"] for it in old_a["items"] if s.get("항") and it["path"][0] == s["항"]), None)
        item_new = next((it.get("norm") or it.get("text") for it in new_a["items"] if s.get("항") and it["path"][0] == s["항"]), None)
        qa = f"{title}\n{(item_old or _art_text(old_a))[:600]}"
        qc = f"{title}\n{(item_new or _art_text(new_a))[:600]}"
        queries = {"A 원래 조항": qa, "B 지워진 말": f"{title}\n" + " ".join(q.removed)[:400], "C 바뀐 뒤": qc,
                   "D 변경분만": f"{title}\n" + " ".join(q.removed + q.added)[:400], "E 개정문": s["개정문"], "F 현행+개정": qa + "\n" + qc}
        truth = {T.resolve(e) for e in fin[i]["영향"]} - {None}
        tot += len(truth)
        for cap in (10, 20):
            res = {n: rank(qs, {st}, cap) for n, qs in queries.items()}
            ra, rc = rank(qa, {st}, cap), rank(qc, {st}, cap)
            res["G 현행·개정 각각 합침"] = list(dict.fromkeys([x for p in zip(ra, rc) for x in p]))[:cap]
            for n in names:
                H[cap][n] += len(truth & set(res[n]))
                if cap == 20:
                    per[n][i] = f"{len(truth & set(res[n]))}/{len(truth)}"
    print(f"[정답 {tot}건, 분할 {a.splits}] 후보 상한 10 / 20 재현율")
    for n in names:
        print(f"  {n:22} {H[10][n]/tot:>4.0%} / {H[20][n]/tot:>4.0%}   문항별@20 " + " ".join(f"{k}:{v}" for k, v in per[n].items()))


if __name__ == "__main__":
    main()
