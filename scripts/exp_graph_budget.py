"""실험: RAG를 30개로 고정하고, 그래프 후보를 효율 순으로 N개만 더하면 재현율이 얼마나 오르는가.
  uv run python scripts/exp_graph_budget.py
평가 묶음 4개(개발 · 1차 봉인 · 2차 · 3차)를 하나씩 시험용으로 빼고, 나머지 셋에서 경로별 효율(후보 수 · 정답 수 · RAG30이 못 가져온 정답 수)을 배워 시험용 묶음에서 확인한다.
  현재 순서  collect의 순서(단계 먼저, 점수순)
  효율 순서  경로 정밀도(평활: (정답+0.5)/(후보+2))의 최대값 순
RAG30 = RAG · RAG_NEW 경로 후보를 의미 검색 점수 순으로 30개.
"""
import collections
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "scripts"))
sys.stdout.reconfigure(encoding="utf-8")
import eval_collect_dev as ecd  # noqa: E402

K1 = ecd.K
SETS = (("개발", K1, "개발"), ("1차봉인", K1, "봉인"), ("2차", ROOT / "eval/impact_label_kit_r2/산출", "봉인"), ("3차", ROOT / "eval/impact_label_kit_r3/산출", "봉인"))
RAGP = ("RAG", "RAG_NEW")
data = {}
for tag, kit, sp in SETS:
    ecd.K = kit
    pos, neg, _, _ = ecd.build(sp)
    rows = []
    for r in pos + neg:
        rag = sorted([c for c in r["cands"] if any(p in RAGP for p in c.paths)], key=lambda c: -c.score)[:30]
        rag_ids = {c.id for c in rag}
        graph = [c for c in r["cands"] if any(p.split(":")[0] not in RAGP for p in c.paths) and c.id not in rag_ids]   # RAG30이 못 가져온 그래프 · 문구 후보
        rows.append({"id": r["id"], "kind": r["kind"], "truth": r["truth"], "rag": rag_ids, "graph": graph, "pos": bool(r["truth"])})
    data[tag] = rows
    tot = sum(len(r["truth"]) for r in rows)
    h = sum(len(r["truth"] & r["rag"]) for r in rows)
    ga = sum(len(r["truth"] & {c.id for c in r["graph"]}) for r in rows)
    print(f"[{tag}] 정답 {tot} | RAG30이 가져온 정답 {h} ({h/max(tot,1):.0%}) | 그래프가 더 가져올 수 있는 정답 {ga} (후보 {sum(len(r['graph']) for r in rows)}개)")


def paths_of(c):
    return {p.split(":")[0] for p in c.paths if p.split(":")[0] not in RAGP}


print("\n[경로별 효율] 전체 4묶음 합 (RAG30이 못 가져온 그래프 후보만): 후보 / 정답")
n = collections.Counter(); h = collections.Counter()
for rows in data.values():
    for r in rows:
        for c in r["graph"]:
            for p in paths_of(c):
                n[p] += 1; h[p] += c.id in r["truth"]
for p, _ in n.most_common():
    print(f"  {p:16} {h[p]:3d}/{n[p]:4d} ({h[p]/n[p]:.0%})")

print("\n[시험용 묶음별 재현율] RAG30 + 그래프 상위 N (효율은 나머지 3묶음에서 학습). 현재 순서와 비교")
for test in data:
    n = collections.Counter(); h = collections.Counter()
    for tag, rows in data.items():
        if tag == test:
            continue
        for r in rows:
            for c in r["graph"]:
                for p in paths_of(c):
                    n[p] += 1; h[p] += c.id in r["truth"]
    prec = {p: (h[p] + 0.5) / (n[p] + 2) for p in n}
    rows = [r for r in data[test] if r["pos"]]
    tot = sum(len(r["truth"]) for r in rows)
    out = [f"[{test}] 정답 {tot}"]
    for N in (0, 5, 10, 20, 40):
        a = b = 0
        for r in rows:
            cur = r["graph"][:N]
            eff = sorted(r["graph"], key=lambda c: -max((prec.get(p, 0.05) for p in paths_of(c)), default=0))[:N]
            a += len(r["truth"] & (r["rag"] | {c.id for c in cur}))
            b += len(r["truth"] & (r["rag"] | {c.id for c in eff}))
        out.append(f"N={N}: 현재 {a}/{tot} ({a/tot:.0%}) 효율 {b}/{tot} ({b/tot:.0%})")
    print("  " + " | ".join(out))

print("\n[가지치기] 시험용 묶음을 뺀 나머지에서 정밀도 25% 미만인 경로만 가진 그래프 후보를 버린다. RAG30 + 남은 그래프 후보 전부")
for test in data:
    n = collections.Counter(); h = collections.Counter()
    for tag, rows in data.items():
        if tag == test:
            continue
        for r in rows:
            for c in r["graph"]:
                for p in paths_of(c):
                    n[p] += 1; h[p] += c.id in r["truth"]
    good = {p for p in n if (h[p] + 0.5) / (n[p] + 2) >= 0.25}
    rows = [r for r in data[test] if r["pos"]]
    tot = sum(len(r["truth"]) for r in rows)
    all_n = sum(len(r["graph"]) for r in rows); keep_n = 0; a = b = 0
    for r in rows:
        kept = [c for c in r["graph"] if paths_of(c) & good]
        keep_n += len(kept)
        a += len(r["truth"] & (r["rag"] | {c.id for c in r["graph"]}))
        b += len(r["truth"] & (r["rag"] | {c.id for c in kept}))
    neg_all = sum(len(r["graph"]) for r in data[test] if not r["pos"]); neg_keep = sum(len([c for c in r["graph"] if paths_of(c) & good]) for r in data[test] if not r["pos"])
    print(f"  [{test}] 남긴 경로 {sorted(good)}\n      그래프 후보 {all_n}→{keep_n}개 | 재현율(RAG30+그래프 전부) {a}/{tot} ({a/tot:.0%}) → {b}/{tot} ({b/tot:.0%}) | 영향 없음 문항 그래프 후보 {neg_all}→{neg_keep}")
