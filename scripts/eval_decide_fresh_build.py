"""새 문항 5차(I61~I80, eval/impact_label_kit_r5)의 순서 비교용 후보 캐시를 만든다.
  uv run python scripts/eval_decide_fresh_build.py [changes.jsonl] [라벨 폴더] [묶음 이름] [출력 캐시]   (기본은 5차)
정답 = out/fresh_label_kit/산출/labels_<id>.json 에서 label이 「예」인 조. 후보 = 파이프라인 기본 구성의 전체 후보(단계 포함).
출력: out/rank_fresh_cache.pkl (eval_rank_ctx와 같은 모양), out/graph_starts.pkl에 (「F5차」, id) 시작 조를 더한다.
점수 매기기: eval_decide.py <모델 폴더> out/rank_fresh_cache.pkl out/decide_scores_fresh.json, 비교: eval_decide_fresh_compare.py
"""
import glob
import json
import os
import pickle
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "scripts"))
sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
import eval_recall_all as era  # noqa: E402
from eval_rank_ctx import change_query  # noqa: E402
from exp_pool_items import law_run  # noqa: E402
from regcheck.impact_view import load_context, run  # noqa: E402

TAG = sys.argv[3] if len(sys.argv) > 3 else "F5차"
CHANGES = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "eval/impact_label_kit_r5/산출/changes.jsonl"
LABELS = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "out/fresh_label_kit/산출"
CACHE = Path(sys.argv[4]) if len(sys.argv) > 4 else ROOT / "out/rank_fresh_cache.pkl"


def main():
    regs = {r["reg_id"]: r for r in (json.loads(Path(p).read_text(encoding="utf-8")) for p in glob.glob(str(ROOT / "out/parsed/[0-9]*.json")))}
    ctx = load_context(ROOT, regs); by = {r["name"]: k for k, r in regs.items()}; rag = era.build_rag(ctx)
    items, starts = [], {}
    ch = [json.loads(l) for l in CHANGES.read_text(encoding="utf-8").splitlines() if l.strip()]
    for it in ch:
        m = it["수정"]; i = it["id"]
        if not (LABELS / f"labels_{i}.json").exists():   # 개정문이 형식에 맞지 않아 분석 실패한 문항은 평가 전에 제외했다(풀이 없음)
            print(i, "라벨 없음: 제외"); continue
        lab = json.loads((LABELS / f"labels_{i}.json").read_text(encoding="utf-8"))
        ys = {x["unit"].strip("[] ") for x in lab if x["label"] == "예"}
        r = run(ctx, regs, by[m["규정"]], m["개정문"], i, rag=rag) if m["규정"] in by else law_run(ctx, m, i, lambda q, k: rag(q, k))
        cq = r["cq"]
        if cq.kind == "분석 실패":
            print(i, "분석 실패", cq.error); continue
        st = (cq.starts or [""])[0]
        ttl = ""
        if st and st.split(":")[0] in regs:
            ttl = next((x.get("title") or "" for x in regs[st.split(":")[0]]["articles"] if x["id"] == st), "")
        t = ctx.unit_text.get(st, "")[:600]
        gq = f"{m['개정문']}\n{t}"[:900]
        cqs = change_query(cq, m["개정문"], t, ttl or m["규정"])[:900]
        cands = [(c.id, c.tier, ctx.unit_text.get(c.id, "")[:700]) for c in r["view"]["all"]]
        items.append((TAG, i, gq, cqs, cq.kind, cands, ys))
        starts[(TAG, i)] = list(cq.starts or [])
        print(i, cq.kind, "후보", len(cands), "정답", len(ys), "후보 안 정답", len(ys & {c for c, _, _ in cands}))
    pickle.dump((items, {}, {}), open(CACHE, "wb"))
    sp = ROOT / "out/graph_starts.pkl"
    st_all = pickle.load(open(sp, "rb")) if sp.exists() else {}
    st_all.update(starts); pickle.dump(st_all, open(sp, "wb"))
    print("문항", len(items), "정답", sum(len(i[6]) for i in items), "후보", sum(len(i[5]) for i in items))


if __name__ == "__main__":
    main()
