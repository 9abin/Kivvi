"""판단 모델 정렬이 새 문항에서도 유지되는지 보기 위한 후보 집합 만들기 (4차 봉인 I49~I60).
  uv run python scripts/eval_decide_holdout.py
순서 비교 63문항(eval_rank_ctx.py)에 들어 있지 않고, 판단 모델 프롬프트를 이 문항으로 조정한 적이 없다. 출력: out/rank_holdout_cache.pkl (eval_rank_ctx와 같은 모양).
점수 매기기는 eval_decide.py <모델 폴더> out/rank_holdout_cache.pkl out/decide_scores_holdout.json
"""
import os
import pickle
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "scripts"))
sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
import eval_collect_dev as ecd  # noqa: E402
from eval_rank_ctx import change_query  # noqa: E402
from regcheck.review_list import split_view  # noqa: E402

ecd.K = ROOT / "eval/impact_label_kit_r4/산출"
pos, neg, ut, _ = ecd.build("봉인")
items = []
for r in pos:
    tx = r["tx"]; cq = r["cq"]
    gq = f"{tx.get('amend','')}\n{tx.get('title','')}\n{tx.get('old','')}"[:900]
    cqs = change_query(cq, tx.get("amend", ""), tx.get("old", ""), tx.get("title", ""))[:900]
    v = split_view(r["cands"])
    items.append(("A4차", r["id"], gq, cqs, cq.kind, [(c.id, c.tier, ut.get(c.id, "")[:700]) for c in v["all"]], set(r["truth"])))
pickle.dump((items, {}, {}), open(ROOT / "out/rank_holdout_cache.pkl", "wb"))
print("문항", len(items), "정답", sum(len(i[6]) for i in items), "후보", sum(len(i[5]) for i in items), "영향 없음 문항", len(neg))
print("정답이 후보에 있는 문항", sum(1 for i in items if i[6] & {c for c, _, _ in i[5]}))
print("종류", {k: sum(1 for i in items if i[4] == k) for k in sorted({i[4] for i in items})})
