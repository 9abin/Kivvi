"""실험: 확정급 후보에서 한 번 더 퍼뜨리면(2라운드) 새로 잡히는 정답이 있는가 (영향 점검 평가셋, 개발 분할만).
  uv run python scripts/exp_second_round_dev.py
1라운드 후보 수집(collect) 결과는 그대로 두고, 확정급 후보를 새 시작점으로 삼아 한 단계 더 따라간 후보만 따로 센다.
  A  확정급 후보를 인용하는 조 (CITES 거슬러감)
  B  A + 확정급 후보가 인용하는 조 (CITES 따라감)
수정 종류별로 새 후보 수 · 새 정답 수 · 정밀도를 낸다. collect.py는 바꾸지 않는다.
"""
import collections
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "scripts"))
sys.stdout.reconfigure(encoding="utf-8")
from eval_collect_dev import build, jl  # noqa: E402
from regcheck.kg import KG  # noqa: E402

HUB = 40


def main():
    pos, neg, _, nodes = build("개발")
    kg = KG(nodes, jl(ROOT / "out/kg/edges.jsonl"))
    stat = collections.defaultdict(lambda: collections.Counter())
    for variant in ("A", "B"):
        for r in pos + neg:
            pool = {c.id for c in r["cands"]} | set(r["cq"].starts)
            seeds = [c.id for c in r["cands"] if c.tier == "확정급"]
            new = set()
            for s in seeds:
                ins = [x["src"] for x in kg.inb.get(("CITES", s), [])]
                outs = [x["dst"] for x in kg.out.get(("CITES", s), [])] if variant == "B" else []
                for n in ins + outs:
                    if n in nodes and nodes[n].get("type") in ("Article", "Supplement", "Annex", "Form") and n not in pool:
                        new.add(n)
            if len(new) > 0:
                pass
            k = (variant, r["kind"])
            stat[k]["문항"] += 1
            stat[k]["새 후보"] += len(new)
            stat[k]["새 정답"] += len(new & r["truth"])
            stat[k]["정답 중 풀 밖"] += len(r["truth"] - pool)
            stat[k]["정답 중 2라운드로 닿음"] += len((r["truth"] - pool) & new)
    for variant in ("A", "B"):
        print(f"\n[{variant}] 수정 종류별: 새 후보 / 새 정답 / (풀 밖 정답 중 닿은 수)")
        t = collections.Counter()
        for (v, kind), c in sorted(stat.items()):
            if v != variant:
                continue
            t.update(c)
            print(f"  {kind:10} 문항 {c['문항']} | 새 후보 {c['새 후보']:3d} | 새 정답 {c['새 정답']:2d} ({c['새 정답']/max(c['새 후보'],1):.0%}) | 풀 밖 정답 {c['정답 중 풀 밖']} 중 {c['정답 중 2라운드로 닿음']}")
        print(f"  합계: 새 후보 {t['새 후보']}, 새 정답 {t['새 정답']} ({t['새 정답']/max(t['새 후보'],1):.0%}), 풀 밖 정답 {t['정답 중 풀 밖']} 중 {t['정답 중 2라운드로 닿음']}")


if __name__ == "__main__":
    main()
