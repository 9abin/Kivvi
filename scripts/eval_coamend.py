"""동반 개정 벤치마크: 같은 날 둘 이상 규정에서 함께 개정된 조(2~8개 조의 작은 묶음)를 정답으로, 한쪽 조를 질의로 다른 규정의 짝을 후보에 올리는지 잰다.
  uv run python scripts/eval_coamend.py
한계: 개정문 없이 조 본문만 쓰는 어려운 조건이고, 같은 날 개정이 모두 내용상 연결된 것은 아니다(일괄 개정 포함). 그래서 절대 수준이 아니라 방법 간 비교와 기준선 기록용이다.
날짜순 분할: 2024-01-01 이전 날짜(개발) / 이후(시험). 사람이 확인한 정답이 아니다.
"""
import collections
import glob
import json
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "scripts"))
sys.stdout.reconfigure(encoding="utf-8")
import eval_collect_dev as ecd  # noqa: E402

SPLIT = "2024-01-01"
HUB = 60
D = re.compile(r"((?:19|20)\d{2})\s*\.\s*(\d{1,2})\s*\.\s*(\d{1,2})")
reg_of = lambda u: u.split(":")[0]  # noqa: E731


def load():
    V = ecd.V
    emb = np.load(V / "embeddings.npy"); emb = emb / np.linalg.norm(emb, axis=1, keepdims=True)
    chunks = ecd.jl(V / "chunks.jsonl")
    ext = np.load(ROOT / "out/kg/ext_emb.npy"); ext_ids = json.loads((ROOT / "out/kg/ext_ids.json").read_text(encoding="utf-8"))
    ids = [c["article_id"] for c in chunks] + ext_ids; E = np.vstack([emb, ext])
    acc = collections.defaultdict(list)
    for j, i in enumerate(ids):
        acc[i].append(j)
    vec = {i: E[js].mean(0) for i, js in acc.items()}
    vec = {i: v / np.linalg.norm(v) for i, v in vec.items()}
    regs = {r["reg_id"]: r for r in (json.loads(Path(p).read_text(encoding="utf-8")) for p in glob.glob(str(ROOT / "out/parsed/[0-9]*.json")))}
    nodes = {n["id"]: n for n in ecd.jl(ROOT / "out/kg/nodes.jsonl")}
    edges = ecd.jl(ROOT / "out/kg/edges.jsonl")
    return vec, regs, nodes, edges


def clusters(regs):
    ad = collections.defaultdict(set)
    for r in regs.values():
        for a in r["articles"]:
            if a.get("deleted"):
                continue
            notes = list(a.get("amend_notes") or []) + [m for it in a["items"] for m in (it.get("amend_marks") or [])]
            for n in notes:
                for m in D.finditer(str(n)):
                    ad[f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"].add(a["id"])
    return {d: v for d, v in ad.items() if 2 <= len(v) <= 8 and len({reg_of(x) for x in v}) >= 2}


TYPES = {"CITES", "SAME_TITLE", "SAME_VALUE", "MENTIONS_TITLE", "VALUE_DIFF", "STALE_CITE"}


def neighbors(edges):
    nb = collections.defaultdict(set)
    for e in edges:
        if e["type"] in TYPES:
            nb[e["src"]].add(e["dst"]); nb[e["dst"]].add(e["src"])
    return nb


def reach(nb, nodes, s, hops):
    seen, fr = {s: 0}, {s}
    for h in range(1, hops + 1):
        nxt = set()
        for u in fr:
            if len(nb[u]) > HUB and h > 1:   # 허브는 2단계부터 번지지 않게
                continue
            for w in nb[u]:
                if w not in seen:
                    seen[w] = h; nxt.add(w)
        fr = nxt
    seen.pop(s)
    return {k: h for k, h in seen.items() if nodes.get(k, {}).get("type") in ("Article", "Supplement", "Annex", "Form") and reg_of(k) != reg_of(s)}


def main():
    vec, regs, nodes, edges = load()
    cl = clusters(regs); nb = neighbors(edges)
    keys = [k for k in vec if ":" in k]; M = np.stack([vec[k] for k in keys])
    Ks = (10, 30, 50, 100)
    for tag, sel in (("개발(2024 이전)", lambda d: d < SPLIT), ("시험(2024 이후)", lambda d: d >= SPLIT), ("전체", lambda d: True)):
        res, n, size = collections.defaultdict(collections.Counter), 0, collections.defaultdict(list)
        for d, v in cl.items():
            if not sel(d):
                continue
            for s in v:
                truth = {x for x in v if reg_of(x) != reg_of(s)}
                if not truth or s not in vec:
                    continue
                order = [keys[i] for i in np.argsort(-(M @ vec[s])) if keys[i] != s]
                R = {h: reach(nb, nodes, s, h) for h in (1, 2, 3)}
                rank = {h: sorted(R[h], key=lambda k: (R[h][k], -float(vec[k] @ vec[s])) if k in vec else (R[h][k], 0)) for h in R}
                for K in Ks:
                    lists = {"의미 K": order[:K], "그래프 3홉 K": rank[3][:K], "의미 K + 그래프 3홉 K": list(dict.fromkeys(order[:K] + rank[3][:K]))}
                    for m, L in lists.items():
                        size[(m, K)].append(len(L))
                    for t in truth:
                        for m, L in lists.items():
                            res[m][K] += t in set(L)
                for t in truth:
                    n += 1
                    for h in (1, 2, 3):
                        res[f"그래프 {h}홉 전체(상한 없음)"][0] += t in R[h]
                        size[(f"그래프 {h}홉 전체(상한 없음)", 0)].append(len(R[h]))
        print(f"\n## {tag}: 쌍 {n}")
        for m in ("의미 K", "그래프 3홉 K", "의미 K + 그래프 3홉 K"):
            print(f"  {m:24s}", "  ".join(f"@{K} {res[m][K]/max(n,1):.0%}(후보 {np.mean(size[(m,K)]):.0f})" for K in Ks))
        for h in (1, 2, 3):
            m = f"그래프 {h}홉 전체(상한 없음)"
            print(f"  {m:24s} recall {res[m][0]/max(n,1):.1%} (후보 평균 {np.mean(size[(m,0)]):.0f})")


if __name__ == "__main__":
    main()
