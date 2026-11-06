"""판단 모델(Kanana-2-v-decide-3b-2611)로 후보 순위를 매기고 순위 지표를 비교한다.
  <별도 가상환경 python> scripts/eval_decide.py <모델 폴더> [후보 캐시] [점수 파일]   (모델 카드가 지정한 torch 2.10 · transformers 5.5.3 환경)
입력: 기본 out/rank_ctx_cache.pkl (eval_rank_ctx.py가 만든 63문항 후보 · 질의 캐시). 새 문항은 eval_decide_holdout.py가 만든 out/rank_holdout_cache.pkl.
출력: 점수 파일(기본 out/decide_scores.json), 표준출력에 지표.
모델은 후보 문장을 만들지 않고 「이 개정이 확인 대상 조문에 닿는가」의 확률(noul)과 4단계 점수(score)만 낸다. 후보 집합과 단계는 그대로 두고 순서만 바꾼다.
"""
import json
import math
import pickle
import sys
import time
from pathlib import Path

import numpy as np
import torch
from transformers import AutoModel

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")
TIER = {"확정급": 0, "확인 권장": 1, "참고": 2}
INSTR = {
    "값 변경": "바뀌는 값(옛 값 → 새 값)을 이 조문이 같은 사안에 대해 따로 정하거나, 인용하거나, 전제하고 있는가?",
    "정의 변경": "정의가 바뀌는 용어를 이 조문이 쓰고, 바뀌는 부분에 실제로 기대고 있는가?",
    "삭제": "삭제되는 조(항)를 이 조문이 인용하거나 그 내용에 기대고 있는가?",
    "표현 수정": "바뀌는 이름이나 말을 이 조문이 그대로 쓰고 있는가?",
    "번호 이동": "번호가 바뀌는 조를 이 조문이 번호로 인용하고 있는가?",
    "위임·법령 변경": "바뀌는 위임 · 법령 내용에 이 조문이 근거하거나 이를 인용하고 있는가?",
    "내용 변경": "바뀌는 내용에 이 조문이 기대고 있거나 같은 사안을 정하고 있는가?",
}
LEVELS = ["전혀 닿지 않는다", "같은 주제일 뿐이다", "닿을 가능성이 있다", "확실히 닿는다"]


def metrics(order_fn, items):
    tot = hit = 0; h = {5: 0, 10: 0, 20: 0}; mrr = []; nd = []; ranks = []
    for k, (tag, iid, gq, cqs, kind, cands, truth) in enumerate(items):
        o = order_fn(k, cands); ids = [c for c in o]
        tot += len(truth); hit += len(truth & set(ids))
        for n in h:
            h[n] += len(truth & set(ids[:n]))
        pos = [i + 1 for i, c in enumerate(ids) if c in truth]
        if pos:
            mrr.append(1 / pos[0]); ranks += pos
        dcg = sum(1 / math.log2(p + 1) for p in pos if p <= 20)
        idcg = sum(1 / math.log2(j + 2) for j in range(min(len(truth), 20)))
        nd.append(dcg / idcg if idcg else 0)
    return dict(tot=tot, h5=h[5], h10=h[10], h20=h[20], mrr=float(np.mean(mrr)), ndcg=float(np.mean(nd)), med=float(np.median(ranks)))


def show(name, m):
    print(f"  {name:36s} @5 {m['h5']/m['tot']:.0%}  @10 {m['h10']/m['tot']:.0%}  @20 {m['h20']/m['tot']:.0%} | MRR {m['mrr']:.3f} | nDCG@20 {m['ndcg']:.3f} | 정답 순위 중앙값 {m['med']:.0f}")


def main():
    model_dir = sys.argv[1]
    cache = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "out/rank_ctx_cache.pkl"
    items, SG, SC = pickle.load(open(cache, "rb"))
    model = AutoModel.from_pretrained(model_dir, trust_remote_code=True, dtype=torch.bfloat16).to("cuda").eval()
    outf = Path(sys.argv[3]) if len(sys.argv) > 3 else ROOT / "out/decide_scores.json"
    S = json.loads(outf.read_text(encoding="utf-8")) if outf.exists() else {}
    t0 = time.time(); n = 0
    for k, (tag, iid, gq, cqs, kind, cands, truth) in enumerate(items):
        for cid, tier, text in cands:
            key = f"{k}|{cid}"
            if key in S:
                continue
            state = f"[개정 종류] {kind}\n[개정 내용]\n{gq[:700]}\n\n[확인 대상 조문]\n{text[:700]}"
            r = model.system_one(state=state, questions={
                "affected": {"type": "noul", "instructions": INSTR.get(kind, "개정 내용이 이 조문에 닿는가?")},
                "level": {"type": "score", "instructions": "이 개정이 확인 대상 조문에 얼마나 닿는가?", "criteria": LEVELS}})
            S[key] = {"p": r["answers"]["affected"]["noul"], "lv": r["answers"]["level"]["score"]}
            n += 1
        outf.write_text(json.dumps(S, ensure_ascii=False), encoding="utf-8")
        print(f"문항 {k+1}/{len(items)} ({iid}) 누적 {n}쌍, {time.time()-t0:.0f}초", flush=True)
    key_of = lambda k, cid: S[f"{k}|{cid}"]  # noqa: E731

    def mk(mode):
        def f(k, cands):
            if mode == "cur":
                return [c for c, _, _ in cands]
            sc = {c: key_of(k, c) for c, _, _ in cands}
            if mode == "p":
                return [c for c, _, _ in sorted(cands, key=lambda x: -sc[x[0]]["p"])]
            if mode == "lv":
                return [c for c, _, _ in sorted(cands, key=lambda x: -sc[x[0]]["lv"])]
            if mode == "tier_p":
                return [c for c, _, _ in sorted(cands, key=lambda x: (TIER[x[1]], -sc[x[0]]["p"]))]
            if mode == "tier_lv":
                return [c for c, _, _ in sorted(cands, key=lambda x: (TIER[x[1]], -sc[x[0]]["lv"]))]
            if mode == "tier_mix":   # 단계 안에서 현재 순위와 확률 순위를 반씩
                pos = {c: i for i, (c, _, _) in enumerate(cands)}
                rk = {c: i for i, c in enumerate(sorted(sc, key=lambda y: -sc[y]["p"]))}
                return [c for c, _, _ in sorted(cands, key=lambda x: (TIER[x[1]], 0.5 * pos[x[0]] + 0.5 * rk[x[0]]))]
        return f
    print("\n문항", len(items), "정답", sum(len(it[6]) for it in items))
    for nm, mode in (("현재(단계 먼저 · 점수순)", "cur"), ("판단 모델 확률만", "p"), ("판단 모델 4단계 점수만", "lv"), ("단계 먼저 · 단계 안에서 확률", "tier_p"),
                     ("단계 먼저 · 단계 안에서 4단계 점수", "tier_lv"), ("단계 안에서 현재와 확률 반반", "tier_mix")):
        show(nm, metrics(mk(mode), items))
    # 같은 문항 묶음별
    sets = {}
    for k, it in enumerate(items):
        sets.setdefault(it[0], []).append(k)
    print("\n묶음별 MRR: 현재 → 확률만 → 단계 안 확률")
    for tag, ks in sets.items():
        sub = [items[k] for k in ks]
        def sw(f):
            return lambda i, c, f=f: f(ks[[x for x in range(len(sub))][i]] if False else ks[i], c)
        m0 = metrics(lambda i, c: mk("cur")(ks[i], c), sub); m1 = metrics(lambda i, c: mk("p")(ks[i], c), sub); m2 = metrics(lambda i, c: mk("tier_p")(ks[i], c), sub)
        print(f"  {tag:6s} 정답 {m0['tot']:3d} | {m0['mrr']:.3f} → {m1['mrr']:.3f} → {m2['mrr']:.3f} | @10 {m0['h10']/m0['tot']:.0%} → {m1['h10']/m1['tot']:.0%} → {m2['h10']/m2['tot']:.0%}")


if __name__ == "__main__":
    main()
