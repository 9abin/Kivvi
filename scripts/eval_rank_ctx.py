"""변경을 이해한 질의로 단계 안 재정렬: 개정 종류 · 바뀐 내용에 맞춰 「어떤 조가 영향받는가」를 설명하는 질의를 만들어 재정렬 모델에 준다.
  uv run python scripts/eval_rank_ctx.py
비교: 현재 순서 / 일반 질의(개정문 + 수정 전 문구)로 단계 안 재정렬 / 변경 이해 질의로 단계 안 재정렬. 후보 집합과 단계는 그대로.
"""
import glob
import json
import os
import pickle
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "scripts"))
sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
import eval_collect_dev as ecd  # noqa: E402
import eval_rank as er  # noqa: E402
import eval_recall_all as era  # noqa: E402
from regcheck.collect import TIER_ORDER  # noqa: E402
from regcheck.impact_view import load_context, run  # noqa: E402
from regcheck.review_list import split_view  # noqa: E402


def change_query(cq, amend: str, start_text: str, title: str) -> str:
    """개정 종류별로 영향받는 조가 어떤 모습인지 설명하는 질의."""
    rem = " / ".join(cq.removed[:2])[:160]
    add = " / ".join(cq.added[:2])[:160]
    k = cq.kind
    if k == "값 변경":
        ov = ", ".join(cq.old_values[:3]); nv = ", ".join(cq.new_values[:3])
        return f"「{title}」에서 {ov or rem}(이)던 기준이 {nv or add}(으)로 바뀐다. 같은 사안의 같은 기준({ov or rem})을 따로 정하거나 인용하거나 전제하는 조항.\n{start_text[:300]}"
    if k == "정의 변경":
        terms = ", ".join(cq.terms[:3])
        return f"용어 「{terms}」의 정의가 바뀐다({rem} → {add}). 이 용어를 쓰거나 이 정의에 기대는 조항.\n{start_text[:300]}"
    if k == "삭제":
        return f"「{title}」({(cq.units or [''])[0]})가 삭제된다. 삭제된 조를 인용하거나 그 내용에 기대거나 같은 사안을 다루는 조항.\n{start_text[:300]}"
    if k == "번호 이동":
        return f"조 번호가 바뀐다({', '.join(cq.units[:3])}). 이 조들을 번호로 인용하는 조항."
    if k == "위임·법령 변경":
        return f"위임 또는 법령 관련 내용이 바뀐다({rem} → {add}). 이 위임 근거나 법령 · 지침을 인용하거나 그에 따라 정한 조항.\n{start_text[:300]}"
    if k == "표현 수정":
        return f"표현이 바뀐다({rem} → {add}). 같은 말(이름 · 용어)을 쓰는 조항.\n{start_text[:200]}"
    return f"{amend}\n{start_text[:400]}"


def main():
    items = []   # (set, id, generic_q, change_q, kind, cands, truth)
    R = ROOT
    for tag, kit, sp in (("A개발", ecd.K, "개발"), ("A1차", ecd.K, "봉인"), ("A2차", R / "eval/impact_label_kit_r2/산출", "봉인"), ("A3차", R / "eval/impact_label_kit_r3/산출", "봉인")):
        ecd.K = kit
        pos, _, ut, _ = ecd.build(sp)
        for r in pos:
            tx = r["tx"]; cq = r["cq"]
            gq = f"{tx.get('amend','')}\n{tx.get('title','')}\n{tx.get('old','')}"[:900]
            cqs = change_query(cq, tx.get("amend", ""), tx.get("old", ""), tx.get("title", ""))[:900]
            v = split_view(r["cands"])
            items.append((tag, r["id"], gq, cqs, cq.kind, [(c.id, c.tier, ut.get(c.id, "")[:700]) for c in v["all"]], set(r["truth"])))
    regs = {r["reg_id"]: r for r in (json.loads(Path(p).read_text(encoding="utf-8")) for p in glob.glob(str(ROOT / "out/parsed/[0-9]*.json")))}
    ctx = load_context(ROOT, regs); by = {r["name"]: k for k, r in regs.items()}; rag = era.build_rag(ctx)
    for tag, d, o, sfx in (("B새봉인", "eval/new_items", "out/new_items", ""), ("C휴직", "eval/leave_items", "out/leave_items", ""), ("C보수", "eval/pay_items", "out/pay_items", "_v2")):
        for i, (m, ys) in era.labels_for(d, o, sfx).items():
            if not ys:
                continue
            r = run(ctx, regs, by[m["규정"]], m["개정문"], i, rag=rag)
            if r["cq"].kind == "분석 실패":
                continue
            st = (r["cq"].starts or [""])[0]; a0 = next((x for x in regs[st.split(":")[0]]["articles"] if x["id"] == st), {})
            gq = f"{m['개정문']}\n{ctx.unit_text.get(st, '')[:600]}"[:900]
            cqs = change_query(r["cq"], m["개정문"], ctx.unit_text.get(st, "")[:600], a0.get("title") or "")[:900]
            items.append((tag, i, gq, cqs, r["cq"].kind, [(c.id, c.tier, ctx.unit_text.get(c.id, "")[:700]) for c in r["view"]["all"]], ys))
    from sentence_transformers import CrossEncoder
    ce = CrossEncoder("BAAI/bge-reranker-v2-m3", device="cuda", max_length=512, model_kwargs={"dtype": "float16"})
    SG, SC = {}, {}
    for k, (tag, iid, gq, cqs, kind, cands, truth) in enumerate(items):
        if cands:
            SG[k] = {c: float(s) for (c, _, _), s in zip(cands, ce.predict([(gq, t) for _, _, t in cands], batch_size=16, show_progress_bar=False))}
            SC[k] = {c: float(s) for (c, _, _), s in zip(cands, ce.predict([(cqs, t) for _, _, t in cands], batch_size=16, show_progress_bar=False))}
    pickle.dump((items, SG, SC), open(ROOT / "out/rank_ctx_cache.pkl", "wb"))
    return items, SG, SC


if __name__ == "__main__":
    items, SG, SC = main()
    flat = [(it[0], it[1], it[2], it[5], it[6]) for it in items]
    idx = {(it[0], it[1]): k for k, it in enumerate(items)}
    def mk(S, mode):
        def f(iid, cands):
            k = next(kk for kk, it in enumerate(items) if it[1] == iid and [c for c, _, _ in it[5]] == [c for c, _, _ in cands])
            s = S[k]
            if mode == "cur":
                return [c for c, _, _ in cands]
            return [c for c, _, _ in sorted(cands, key=lambda x: (TIER_ORDER[x[1]], -s[x[0]]))]
        return f
    print("문항", len(items), "정답", sum(len(x[4]) for x in flat))
    er.show("현재(단계 먼저 · 점수순)", er.metrics(mk(SG, "cur"), flat))
    er.show("일반 질의로 단계 안 재정렬", er.metrics(mk(SG, "tier"), flat))
    er.show("변경 이해 질의로 단계 안 재정렬", er.metrics(mk(SC, "tier"), flat))
    print("\n개정 종류별 (정답 수 · MRR · @10 · @20): 현재 / 일반 질의 / 변경 이해 질의")
    kinds = sorted({it[4] for it in items})
    for kd in kinds:
        sub = [(it[0], it[1], it[2], it[5], it[6]) for it in items if it[4] == kd]
        if not sub:
            continue
        a, b, c = er.metrics(mk(SG, "cur"), sub), er.metrics(mk(SG, "tier"), sub), er.metrics(mk(SC, "tier"), sub)
        print(f"  {kd:10s} 문항 {len(sub):2d} 정답 {a['tot']:3d} | 현재 MRR {a['mrr']:.3f} @10 {a['h10']/a['tot']:.0%} @20 {a['h20']/a['tot']:.0%} | 일반 {b['mrr']:.3f} {b['h10']/b['tot']:.0%} {b['h20']/b['tot']:.0%} | 변경 이해 {c['mrr']:.3f} {c['h10']/c['tot']:.0%} {c['h20']/c['tot']:.0%}")
