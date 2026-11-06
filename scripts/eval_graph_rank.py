"""전통 그래프 재정렬 비교: 시작 조에서 그래프 구조로 후보 순서를 다시 매기면 어떤가 (후보 집합과 단계는 그대로, 순서만).
  uv run python scripts/eval_graph_rank.py
- 개인화 PageRank(PPR): 시작 조에서 되돌아오는 확률로 퍼뜨림. 간선은 관계 종류별 가중치(kg.EXPAND_WEIGHTS)를 쓰고 양방향, 허브는 차수로 눌러 준다.
- 홉 거리: 시작 조에서 그 관계들로 몇 걸음에 닿는가. 닿지 않으면 먼 것으로 본다.
- 융합(RRF): 현재 순서와 그래프 순서를 순위 역수로 합친다.
후보 · 단계 · 정답은 out/rank_ctx_cache.pkl(63문항)과 out/rank_holdout_cache.pkl(새 문항 4차 봉인)에서 읽고, 시작 조는 out/graph_starts.pkl(없으면 만든다)에서 읽는다.
판단 모델 점수가 있으면(out/decide_scores*.json) 같은 표에 나란히 둔다.
"""
import collections
import glob
import json
import math
import os
import pickle
import random
import sys
from pathlib import Path

import numpy as np
import scipy.sparse as sp

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "scripts"))
sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
from regcheck.kg import EXPAND_WEIGHTS  # noqa: E402

T = {"확정급": 0, "확인 권장": 1, "참고": 2}
STARTS = ROOT / "out/graph_starts.pkl"


def build_starts():
    """(묶음, 문항) -> 시작 조 목록. 후보 수집과 같은 경로로 만든다 (eval_rank_ctx.py · eval_decide_holdout.py와 같은 순서)."""
    import eval_collect_dev as ecd
    import eval_recall_all as era
    from regcheck.impact_view import load_context, run
    st = {}
    for tag, kit, sp_ in (("A개발", ecd.K, "개발"), ("A1차", ecd.K, "봉인"), ("A2차", ROOT / "eval/impact_label_kit_r2/산출", "봉인"),
                          ("A3차", ROOT / "eval/impact_label_kit_r3/산출", "봉인"), ("A4차", ROOT / "eval/impact_label_kit_r4/산출", "봉인")):
        ecd.K = kit
        pos, _, _, _ = ecd.build(sp_)
        for r in pos:
            st[(tag, r["id"])] = list(r["cq"].starts or [])
    regs = {r["reg_id"]: r for r in (json.loads(Path(p).read_text(encoding="utf-8")) for p in glob.glob(str(ROOT / "out/parsed/[0-9]*.json")))}
    ctx = load_context(ROOT, regs); by = {r["name"]: k for k, r in regs.items()}; rag = era.build_rag(ctx)
    for tag, d, o, sfx in (("B새봉인", "eval/new_items", "out/new_items", ""), ("C휴직", "eval/leave_items", "out/leave_items", ""), ("C보수", "eval/pay_items", "out/pay_items", "_v2")):
        for i, (m, ys) in era.labels_for(d, o, sfx).items():
            if not ys:
                continue
            r = run(ctx, regs, by[m["규정"]], m["개정문"], i, rag=rag)
            if r["cq"].kind != "분석 실패":
                st[(tag, i)] = list(r["cq"].starts or [])
    pickle.dump(st, open(STARTS, "wb"))
    return st


def jl(p):
    return [json.loads(l) for l in Path(p).read_text(encoding="utf-8").splitlines() if l.strip()]


def graph_matrices():
    nodes = {r["id"]: r for r in jl(ROOT / "out/kg/nodes.jsonl")}
    idx = {n: i for i, n in enumerate(nodes)}
    r_, c_, v_ = [], [], []
    for e in jl(ROOT / "out/kg/edges.jsonl"):
        w = EXPAND_WEIGHTS.get(e["type"])
        if w and e["src"] in idx and e["dst"] in idx:
            a, b = idx[e["src"]], idx[e["dst"]]
            r_ += [a, b]; c_ += [b, a]; v_ += [w, w]
    N = len(idx)
    A = sp.csr_matrix((v_, (r_, c_)), shape=(N, N)); A.data = np.minimum(A.data, 1.0) if False else A.data
    deg = np.asarray(A.sum(1)).ravel()
    d = 1.0 / np.sqrt(np.maximum(deg, 1e-9))
    Wn = sp.diags(d) @ A @ sp.diags(d)           # 허브를 눌러 준 대칭 정규화
    Au = (A > 0).astype(np.float32).tocsr()      # 홉 거리용 (가중치 무시)
    return idx, Wn.tocsr(), Au


def ppr(Wn, seeds, alpha=0.3, iters=30):
    e = np.zeros(Wn.shape[0]); e[seeds] = 1.0 / max(len(seeds), 1)
    p = e.copy()
    for _ in range(iters):
        p = alpha * e + (1 - alpha) * (Wn @ p)
    return p


def hops(Au, seeds, maxh=5):
    d = np.full(Au.shape[0], 99); d[seeds] = 0
    fr = np.zeros(Au.shape[0], dtype=np.float32); fr[seeds] = 1
    seen = fr > 0
    for h in range(1, maxh + 1):
        nf = (Au @ fr) > 0
        new = nf & ~seen
        d[new] = h; seen |= new
        fr = new.astype(np.float32)
        if not new.any():
            break
    return d


def metrics_rows(P, V):
    n = sum(P[i]["n"] for i in V)
    return dict(mrr=np.mean([P[i]["mrr"] for i in V]), nd=np.mean([P[i]["nd"] for i in V]),
                h5=sum(P[i]["h5"] for i in V) / n, h10=sum(P[i]["h10"] for i in V) / n, h20=sum(P[i]["h20"] for i in V) / n, n=n)


def per_item(items, orders):
    out = {}
    for k, it in enumerate(items):
        truth = it[6]; o = orders[k]
        pos = sorted(i + 1 for i, c in enumerate(o) if c in truth)
        n = len(truth)
        dcg = sum(1 / math.log2(p + 1) for p in pos if p <= 20); idcg = sum(1 / math.log2(j + 2) for j in range(min(n, 20)))
        out[k] = dict(n=n, mrr=1 / pos[0] if pos else 0, h5=sum(p <= 5 for p in pos), h10=sum(p <= 10 for p in pos), h20=sum(p <= 20 for p in pos), nd=dcg / idcg if idcg else 0)
    return out


def evaluate(title, items, feats, dscore):
    V = [k for k, it in enumerate(items) if it[6] & {c for c, _, _ in it[5]}]
    modes = {}
    rank = lambda xs, rev: {c: i for i, c in enumerate(sorted(xs, key=lambda c: (-xs[c] if rev else xs[c])))}   # noqa: E731
    for name in ("현재", "PPR만", "단계 안 PPR", "홉 거리만", "단계 안 홉 거리", "단계 안 PPR+현재 융합", "융합(현재+PPR, 단계 무시)"):
        modes[name] = []
    if dscore is not None:
        modes["판단 모델: 단계 안 4단계 점수"] = []
        modes["단계 안 판단 모델+PPR 융합"] = []
    for k, it in enumerate(items):
        cands = it[5]; ids = [c for c, _, _ in cands]
        pos = {c: i for i, c in enumerate(ids)}
        f = feats.get(k)
        tier = {c: T[t] for c, t, _ in cands}
        if f is None:
            for m in modes:
                modes[m].append(ids)
            continue
        pp, hp = f["ppr"], f["hop"]
        modes["현재"].append(ids)
        modes["PPR만"].append(sorted(ids, key=lambda c: (-pp[c], pos[c])))
        modes["단계 안 PPR"].append(sorted(ids, key=lambda c: (tier[c], -pp[c], pos[c])))
        modes["홉 거리만"].append(sorted(ids, key=lambda c: (hp[c], pos[c])))
        modes["단계 안 홉 거리"].append(sorted(ids, key=lambda c: (tier[c], hp[c], pos[c])))
        rp = rank(pp, True)
        fus = {c: 1 / (60 + pos[c]) + 1 / (60 + rp[c]) for c in ids}
        modes["단계 안 PPR+현재 융합"].append(sorted(ids, key=lambda c: (tier[c], -fus[c])))
        modes["융합(현재+PPR, 단계 무시)"].append(sorted(ids, key=lambda c: -fus[c]))
        if dscore is not None:
            lv = {c: dscore[f"{k}|{c}"]["lv"] for c in ids}
            modes["판단 모델: 단계 안 4단계 점수"].append(sorted(ids, key=lambda c: (tier[c], -lv[c])))
            rl = rank(lv, True)
            fu2 = {c: 1 / (60 + rl[c]) + 1 / (60 + rp[c]) for c in ids}
            modes["단계 안 판단 모델+PPR 융합"].append(sorted(ids, key=lambda c: (tier[c], -fu2[c])))
    P = {m: per_item(items, o) for m, o in modes.items()}
    print(f"\n[{title}] 문항 {len(items)} (정답이 후보에 있는 문항 {len(V)}) 정답 {metrics_rows(P['현재'], V)['n']}")
    base = metrics_rows(P["현재"], V)
    rng = random.Random(0)
    for m in modes:
        a = metrics_rows(P[m], V)
        line = f"  {m:28s} @5 {a['h5']:.0%} @10 {a['h10']:.0%} @20 {a['h20']:.0%} | MRR {a['mrr']:.3f} nDCG {a['nd']:.3f}"
        if m != "현재":
            d = {k: [] for k in ("mrr", "h10", "h20")}
            for _ in range(1000):
                ix = [V[rng.randrange(len(V))] for _ in V]
                x, y = metrics_rows(P[m], ix), metrics_rows(P["현재"], ix)
                for k in d:
                    d[k].append(x[k] - y[k])
            w = sum(P[m][i]["mrr"] > P["현재"][i]["mrr"] for i in V); l = sum(P[m][i]["mrr"] < P["현재"][i]["mrr"] for i in V)
            line += f" | MRR {a['mrr']-base['mrr']:+.3f} [{np.percentile(d['mrr'],2.5):+.2f},{np.percentile(d['mrr'],97.5):+.2f}] @10 {a['h10']-base['h10']:+.1%} [{np.percentile(d['h10'],2.5):+.2f},{np.percentile(d['h10'],97.5):+.2f}] 승{w}/패{l}"
        print(line)
    # 종류별 (단계 안 PPR 대 현재)
    kinds = collections.defaultdict(list)
    for k in V:
        kinds[items[k][4]].append(k)
    print("  종류별 MRR 현재 → 단계 안 PPR → 단계 안 홉 거리" + (" → 판단 모델" if dscore is not None else ""))
    for kd, ks in sorted(kinds.items()):
        cols = ["현재", "단계 안 PPR", "단계 안 홉 거리"] + (["판단 모델: 단계 안 4단계 점수"] if dscore is not None else [])
        print(f"    {kd:10s} 문항 {len(ks):2d} 정답 {metrics_rows(P['현재'], ks)['n']:3d} | " + " → ".join(f"{metrics_rows(P[c], ks)['mrr']:.2f}" for c in cols))
    return P


def main():
    st = pickle.load(open(STARTS, "rb")) if STARTS.exists() else build_starts()
    idx, Wn, Au = graph_matrices()
    nodes = list(idx)
    for title, cache, sc in (("63문항", "out/rank_ctx_cache.pkl", "out/decide_scores.json"), ("새 문항 4차 봉인", "out/rank_holdout_cache.pkl", "out/decide_scores_holdout.json")):
        items, _, _ = pickle.load(open(ROOT / cache, "rb"))
        dscore = json.loads((ROOT / sc).read_text(encoding="utf-8")) if (ROOT / sc).exists() else None
        feats = {}; miss = 0
        for k, it in enumerate(items):
            seeds = [idx[s] for s in st.get((it[0], it[1]), []) if s in idx]
            if not seeds:
                miss += 1
                continue
            p = ppr(Wn, seeds); h = hops(Au, seeds)
            feats[k] = dict(ppr={c: float(p[idx[c]]) if c in idx else 0.0 for c, _, _ in it[5]}, hop={c: int(h[idx[c]]) if c in idx else 99 for c, _, _ in it[5]})
        print(f"\n시작 조를 못 찾은 문항 {miss}개 (이 문항은 현재 순서를 그대로 둠)")
        evaluate(title, items, feats, dscore)


if __name__ == "__main__":
    main()
