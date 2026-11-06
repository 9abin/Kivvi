"""온톨로지 관계 종류별 통과 가중치를 맞춰 다홉 탐색의 recall을 올리는 시험.
  uv run python scripts/exp_ontology_walk.py
관계 종류(온톨로지)를 14개 묶음으로 보고, 묶음마다 가중치(0 · 0.5 · 1 · 2)와 허브 감쇠(γ), 홉 감쇠(α)를 정한다.
점수 = W·e + α·W²·e + α²·W³·e (W = Σ 가중치 × 정규화한 묶음 인접행렬). 후보 = 그래프 상위 + 의미 검색으로 총 K개를 채움.
정하는 데이터: 동반 개정 벤치마크의 개발 구간(2024 이전). 시험 구간(2024 이후)은 마지막에 한 번만 본다.
"""
import collections
import itertools
import random
import sys
from pathlib import Path

import numpy as np
import scipy.sparse as sp

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts")); sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")
import eval_coamend as ec  # noqa: E402

GROUPS = {
    "CITES": ["CITES"], "CITES_REG": ["CITES_REG"], "CITES_LAW": ["CITES_LAW"], "TERM": ["USES_TERM", "DEFINES"],
    "MENTIONS_TITLE": ["MENTIONS_TITLE"], "MENTIONS_STEM": ["MENTIONS_STEM"], "MENTIONS_FORM": ["MENTIONS_FORM"], "SHARED_ATTACH": ["SHARED_ATTACH"],
    "BODY": ["MENTIONS_BODY"], "ORG": ["MENTIONS_ORG"], "SAME_TITLE": ["SAME_TITLE"], "SAME_VALUE": ["SAME_VALUE"],
    "RULE": ["VALUE_DIFF", "DEF_DIFF", "STALE_CITE"], "SUPPLEMENTS": ["SUPPLEMENTS"],
}
K = 50
GRAPH_SHARE = 25   # 후보 K개 중 그래프에서 가져오는 수 (나머지는 의미 검색)


def main():
    vec, regs, nodes, edges = ec.load()
    cl = ec.clusters(regs)
    idx = {n: i for i, n in enumerate(nodes)}
    N = len(idx)
    ART = np.array([nodes[n]["type"] in ("Article", "Supplement", "Annex", "Form") for n in nodes])
    REG = np.array([(nodes[n].get("reg") or n) if nodes[n]["type"] != "Regulation" else n for n in nodes])
    mats = {}
    for g, types in GROUPS.items():
        r, c = [], []
        for e in edges:
            if e["type"] in types and e["src"] in idx and e["dst"] in idx:
                r += [idx[e["src"]], idx[e["dst"]]]; c += [idx[e["dst"]], idx[e["src"]]]
        A = sp.csr_matrix((np.ones(len(r)), (r, c)), shape=(N, N)); A.data[:] = 1
        mats[g] = A
    deg = {g: np.asarray(A.sum(1)).ravel() for g, A in mats.items()}
    keys = [k for k in vec if k in idx and ART[idx[k]]]
    M = np.stack([vec[k] for k in keys])
    # 시작 조와 정답
    starts = {"dev": [], "test": []}
    for d, v in cl.items():
        sp_ = "dev" if d < ec.SPLIT else "test"
        for s in v:
            truth = {x for x in v if ec.reg_of(x) != ec.reg_of(s)}
            if truth and s in idx and s in vec:
                order = [keys[i] for i in np.argsort(-(M @ vec[s])) if keys[i] != s][:100]
                starts[sp_].append((s, truth, order))
    print("시작 조", {k: len(v) for k, v in starts.items()})

    def build(w, gamma):
        W = sp.csr_matrix((N, N))
        for g, A in mats.items():
            if w.get(g, 0) <= 0:
                continue
            dd = np.maximum(deg[g], 1.0) ** (-gamma)
            W = W + w[g] * sp.diags(dd) @ A @ sp.diags(dd)
        return W

    def evaluate(w, gamma, alpha, split, k=K, share=GRAPH_SHARE, detail=False):
        W = build(w, gamma)
        S = starts[split]
        E = sp.csr_matrix((np.ones(len(S)), ([idx[s] for s, _, _ in S], range(len(S)))), shape=(N, len(S)))
        V1 = W @ E; V2 = W @ V1; V3 = W @ V2
        Sc = (V1 + alpha * V2 + alpha ** 2 * V3).toarray()
        hit = tot = 0
        for j, (s, truth, order) in enumerate(S):
            sc = Sc[:, j].copy()
            sc[~ART] = 0; sc[REG == REG[idx[s]]] = 0
            top = np.argsort(-sc)[:share]
            g_list = [list(nodes)[i] for i in top if sc[i] > 0]
            cand = list(dict.fromkeys(g_list + order))[:k] if len(g_list) else order[:k]
            # 그래프가 모자라면 의미 검색으로 총 k개를 채운다 (위 슬라이스가 이미 그렇게 한다)
            tot += len(truth); hit += len(truth & set(cand))
        return hit / max(tot, 1)

    names = list(GROUPS)
    uniform = {g: 1.0 for g in names}
    print("\n[기준선]")
    sem = lambda split: np.mean([len(t & set(o[:K])) / len(t) for _, t, o in starts[split]])  # noqa: E731
    print(f"  의미 검색만 {K}개: 개발 {evaluate({}, 0, 0, 'dev'):.1%} / 시험 {evaluate({}, 0, 0, 'test'):.1%}")
    for gamma in (0.0, 0.5, 1.0):
        print(f"  전 관계 가중치 1, 허브 감쇠 γ={gamma}, 홉 감쇠 0.5: 개발 {evaluate(uniform, gamma, 0.5, 'dev'):.1%} / 시험 {evaluate(uniform, gamma, 0.5, 'test'):.1%}")
    # 이 벤치마크로 맞추지 않은 독립 근거: 다른 평가(AI 라벨 묶음)에서 잰 경로별 정밀도를 가중치로 쓴다 (graph_budget · 리포트 80)
    prec = {"CITES": 1.0, "TERM": 0.6, "SAME_TITLE": 0.67, "SAME_VALUE": 0.22, "MENTIONS_TITLE": 0.14, "MENTIONS_STEM": 0.14, "CITES_REG": 0.04, "ORG": 0.04,
            "SHARED_ATTACH": 0.3, "MENTIONS_FORM": 0.3, "BODY": 0.1, "RULE": 0.5, "SUPPLEMENTS": 0.5, "CITES_LAW": 0.5}
    print("\n[정밀도 가중치(다른 평가에서 잰 값, 이 벤치마크로 맞추지 않음)]")
    for gamma in (0.0, 0.5, 1.0):
        print(f"  γ={gamma}: 개발 {evaluate(prec, gamma, 0.5, 'dev'):.1%} / 시험 {evaluate(prec, gamma, 0.5, 'test'):.1%}")
    # 개발 구간에서 좌표 상승
    best = dict(uniform); bg, ba = 0.5, 0.5
    cur = evaluate(best, bg, ba, "dev")
    for it in range(3):
        improved = False
        for g in names:
            for val in (0, 0.5, 1, 2):
                if val == best[g]:
                    continue
                trial = dict(best); trial[g] = val
                sc = evaluate(trial, bg, ba, "dev")
                if sc > cur + 1e-9:
                    best, cur, improved = trial, sc, True
        for gam in (0.0, 0.5, 1.0):
            sc = evaluate(best, gam, ba, "dev")
            if sc > cur + 1e-9:
                bg, cur, improved = gam, sc, True
        for al in (0.2, 0.5, 1.0):
            sc = evaluate(best, bg, al, "dev")
            if sc > cur + 1e-9:
                ba, cur, improved = al, sc, True
        if not improved:
            break
    print(f"\n[개발 구간에서 맞춘 값] γ={bg}, α={ba}")
    print("  가중치:", {g: best[g] for g in names})
    print(f"  개발 {cur:.1%} (맞춘 구간이라 낙관적)")
    print(f"  시험(한 번만): {evaluate(best, bg, ba, 'test'):.1%}  | 같은 시험에서 의미 검색만 {evaluate({}, 0, 0, 'test'):.1%}, 전 관계 1: {evaluate(uniform, 0.5, 0.5, 'test'):.1%}")
    # 거꾸로: 시험 구간에서 맞춰 개발에서 확인 (안정성)
    print("\n[안정성: 값이 구간에 의존하는가]")
    for k2 in (30, 50, 100):
        print(f"  K={k2}: 맞춘 값 시험 {evaluate(best, bg, ba, 'test', k=k2, share=k2//2):.1%} / 의미 검색만 시험 {evaluate({}, 0, 0, 'test', k=k2, share=k2//2):.1%}")


if __name__ == "__main__":
    main()
