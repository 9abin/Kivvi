"""순위 평가: 후보 집합은 그대로 두고 순서만 바꿔 정답이 얼마나 위로 오는지 잰다 (A 4묶음 + B 새 봉인 + C 휴직 · 보수).
  uv run python scripts/eval_rank.py
지표: 후보 안에 든 정답 중 상위 5 · 10 · 20 안에 드는 비율, 문항별 첫 정답 순위의 MRR, nDCG@20, 정답 평균 순위.
순서: 현재(단계 먼저 · 점수순) / 재정렬 모델 점수 / 단계 안에서만 재정렬 / 단계 가산 + 모델 점수.
정답은 AI 라벨(현업 확인 전). 모델은 개정문과 수정 전 문구를 질의로, 후보 조 본문을 문서로 본다.
"""
import collections
import glob
import json
import math
import os
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "scripts"))
sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
import eval_collect_dev as ecd  # noqa: E402
import eval_recall_all as era  # noqa: E402
from regcheck.collect import TIER_ORDER  # noqa: E402
from regcheck.impact_view import load_context, run  # noqa: E402
from regcheck.review_list import split_view  # noqa: E402

MODELS = {"bge-reranker-v2-m3": "BAAI/bge-reranker-v2-m3", "bge-reranker-v2-m3-ko": "dragonkue/bge-reranker-v2-m3-ko"}


def gather():
    """[(set, id, query, [(cand_id, tier, text)], truth)]  후보는 현재 순서."""
    items = []
    R = ROOT
    for tag, kit, sp in (("A개발", ecd.K, "개발"), ("A1차", ecd.K, "봉인"), ("A2차", R / "eval/impact_label_kit_r2/산출", "봉인"), ("A3차", R / "eval/impact_label_kit_r3/산출", "봉인")):
        ecd.K = kit
        pos, _, ut, _ = ecd.build(sp)
        for r in pos:
            tx = r["tx"]
            q = f"{tx.get('amend','')}\n{tx.get('title','')}\n{tx.get('old','')}"[:900]
            v = split_view(r["cands"])
            items.append((tag, r["id"], q, [(c.id, c.tier, ut.get(c.id, "")[:700]) for c in v["all"]], set(r["truth"])))
    regs = {r["reg_id"]: r for r in (json.loads(Path(p).read_text(encoding="utf-8")) for p in glob.glob(str(ROOT / "out/parsed/[0-9]*.json")))}
    ctx = load_context(ROOT, regs); by = {r["name"]: k for k, r in regs.items()}; rag = era.build_rag(ctx)
    for tag, d, o, sfx in (("B새봉인", "eval/new_items", "out/new_items", ""), ("C휴직", "eval/leave_items", "out/leave_items", ""), ("C보수", "eval/pay_items", "out/pay_items", "_v2")):
        for i, (m, ys) in era.labels_for(d, o, sfx).items():
            if not ys:
                continue
            r = run(ctx, regs, by[m["규정"]], m["개정문"], i, rag=rag)
            if r["cq"].kind == "분석 실패":
                continue
            st = (r["cq"].starts or [""])[0]
            q = f"{m['개정문']}\n{ctx.unit_text.get(st, '')[:600]}"[:900]
            items.append((tag, i, q, [(c.id, c.tier, ctx.unit_text.get(c.id, "")[:700]) for c in r["view"]["all"]], ys))
    return items


def metrics(order_fn, items):
    tot = hit = 0; h = collections.Counter(); mrr = []; ndcg = []; ranks = []
    for tag, iid, q, cands, truth in items:
        order = order_fn(iid, cands)
        ids = [c for c in order]
        t_in = truth & set(ids)
        tot += len(truth); hit += len(t_in)
        for n in (5, 10, 20):
            h[n] += len(truth & set(ids[:n]))
        pos = [i + 1 for i, c in enumerate(ids) if c in truth]
        if pos:
            mrr.append(1 / pos[0]); ranks += pos
        dcg = sum(1 / math.log2(p + 1) for p in pos if p <= 20)
        idcg = sum(1 / math.log2(k + 2) for k in range(min(len(truth), 20)))
        ndcg.append(dcg / idcg if idcg else 0)
    return dict(tot=tot, hit=hit, h5=h[5], h10=h[10], h20=h[20], mrr=float(np.mean(mrr)), ndcg=float(np.mean(ndcg)), mean_rank=float(np.mean(ranks)), median_rank=float(np.median(ranks)))


def show(name, m):
    print(f"  {name:34s} @5 {m['h5']/m['tot']:.0%}  @10 {m['h10']/m['tot']:.0%}  @20 {m['h20']/m['tot']:.0%} (후보 안 {m['hit']/m['tot']:.0%} 중) | MRR {m['mrr']:.3f} | nDCG@20 {m['ndcg']:.3f} | 정답 순위 중앙값 {m['median_rank']:.0f} 평균 {m['mean_rank']:.1f}")


def main():
    items = gather()
    print("문항", len(items), "정답", sum(len(x[4]) for x in items))
    cur = lambda iid, cands: [c for c, _, _ in cands]  # noqa: E731
    base = metrics(cur, items)
    show("현재(단계 먼저 · 점수순)", base)
    from sentence_transformers import CrossEncoder
    for nm, mid in MODELS.items():
        ce = CrossEncoder(mid, device="cuda", max_length=512, model_kwargs={"dtype": "float16"})
        score = {}
        for tag, iid, q, cands, truth in items:
            if cands:
                sc = ce.predict([(q, t) for _, _, t in cands], batch_size=16, show_progress_bar=False)
                score[(tag, iid)] = {c: float(s) for (c, _, _), s in zip(cands, sc)}
        key = {(it[0], it[1]): it for it in items}
        def by_model(iid, cands, tag=None): ...
        # 항목별로 점수를 쓰려면 태그가 필요해서 래퍼를 만든다
        def make(mode):
            def f(iid, cands):
                tag = next(t for (t, i) in score if i == iid and score[(t, i)].keys() >= {c for c, _, _ in cands})
                s = score[(tag, iid)]
                if mode == "model":
                    return [c for c, _, _ in sorted(cands, key=lambda x: -s[x[0]])]
                if mode == "tier":
                    return [c for c, _, _ in sorted(cands, key=lambda x: (TIER_ORDER[x[1]], -s[x[0]]))]
                vals = np.array(list(s.values())); z = {c: (s[c] - vals.mean()) / (vals.std() + 1e-9) for c in s}
                return [c for c, _, _ in sorted(cands, key=lambda x: -(z[x[0]] - 1.0 * TIER_ORDER[x[1]]))]
            return f
        print(f"\n[{nm}]")
        show("모델 점수만", metrics(make("model"), items))
        show("단계 먼저 · 단계 안에서 모델", metrics(make("tier"), items))
        show("모델 z점수 − 1.0×단계", metrics(make("blend"), items))
        del ce


if __name__ == "__main__":
    main()
