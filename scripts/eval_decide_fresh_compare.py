"""새 문항 5차에서 현재 순서 · 그래프 재정렬 · 판단 모델 정렬을 한 표로 비교한다 (동결한 코드와 프롬프트로 한 번).
  uv run python scripts/eval_decide_fresh_compare.py [후보 캐시] [판단 모델 점수 파일] [제목]   (기본은 5차)
eval_graph_rank.py의 평가 함수를 그대로 쓴다. 입력: out/rank_fresh_cache.pkl, out/decide_scores_fresh.json, out/graph_starts.pkl.
"""
import json
import pickle
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts")); sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")
import eval_graph_rank as g  # noqa: E402

items, _, _ = pickle.load(open(sys.argv[1] if len(sys.argv) > 1 else ROOT / "out/rank_fresh_cache.pkl", "rb"))
dscore = json.loads(Path(sys.argv[2] if len(sys.argv) > 2 else ROOT / "out/decide_scores_fresh.json").read_text(encoding="utf-8"))
st = pickle.load(open(g.STARTS, "rb"))
idx, Wn, Au = g.graph_matrices()
feats = {}; miss = 0
for k, it in enumerate(items):
    seeds = [idx[s] for s in st.get((it[0], it[1]), []) if s in idx]
    if not seeds:
        miss += 1; continue
    p = g.ppr(Wn, seeds); h = g.hops(Au, seeds)
    feats[k] = dict(ppr={c: float(p[idx[c]]) if c in idx else 0.0 for c, _, _ in it[5]}, hop={c: int(h[idx[c]]) if c in idx else 99 for c, _, _ in it[5]})
print("시작 조를 못 찾은 문항", miss)
g.evaluate(sys.argv[3] if len(sys.argv) > 3 else "새 문항 5차 (I61~I80)", items, feats, dscore)
