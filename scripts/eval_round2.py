"""2차 봉인 평가: 동결한 후보 수집 구성을 새 문항(I25~I36)에서 한 번 잰다 (리포트 75).
  uv run python scripts/eval_round2.py --yes-open-sealed
읽는 것: eval/impact_label_kit_r2/산출/{changes,labels_final}.jsonl (사람 확인 전 AI 라벨 초안)
비교: RAG만 (후보 중 의미 검색 경로 순서) / 그래프+문구만 (RAG 경로 없는 후보) / 합침(수집 결과 그대로).
동결 대상 파일(collect.py 등)은 바꾸지 않는다. 라벨 위치만 eval_collect_dev.K를 바꿔서 읽는다.
"""
import argparse
import collections
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "scripts"))
sys.stdout.reconfigure(encoding="utf-8")
import eval_collect_dev as ecd  # noqa: E402
from combo.metrics import paired_diff_cluster  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes-open-sealed", action="store_true")
    ap.add_argument("--kit", default="eval/impact_label_kit_r2/산출")
    ap.add_argument("--tag", default="2차 봉인")
    a = ap.parse_args()
    if not a.yes_open_sealed:
        sys.exit("2차 봉인 문항은 한 번만 연다: --yes-open-sealed")
    ecd.K = ROOT / a.kit
    pos, neg, unit_text, nodes = ecd.build("봉인")
    tot = sum(len(r["truth"]) for r in pos)
    print(f"[{a.tag}] 영향 있는 문항 {len(pos)}개 (정답 {tot}건), 영향 없음 문항 {len(neg)}개")

    def lists(r):
        comb = r["cands"]
        rag = [c for c in comb if "RAG" in c.paths]
        graph = [c for c in comb if any(p != "RAG" for p in c.paths)]
        return {"RAG만": rag, "그래프+문구": graph, "합침": comb}

    for cap in (10, 20):
        row = {}
        for name in ("RAG만", "그래프+문구", "합침"):
            h = sum(len(r["truth"] & {c.id for c in lists(r)[name][:cap]}) for r in pos)
            row[name] = h
        print(f"  후보 {cap}개 안 정답: " + " | ".join(f"{k} {v}/{tot} ({v/tot:.0%})" for k, v in row.items()))
    pairs = [(len(r["truth"] & {c.id for c in lists(r)["합침"][:20]}), len(r["truth"]), len(r["truth"] & {c.id for c in lists(r)["RAG만"][:20]}), len(r["truth"])) for r in pos]
    d = paired_diff_cluster(pairs)
    win = sum(1 for p in pairs if p[0] > p[2]); lose = sum(1 for p in pairs if p[0] < p[2]); tie = len(pairs) - win - lose
    if d:
        print(f"  합침 − RAG만 (@20, 문항 단위 bootstrap): {d[0]:+.0%} [{d[1]:+.0%}, {d[2]:+.0%}] | 이긴 {win} 진 {lose} 같음 {tie}")
    print("  단계별 후보 중 정답:")
    for tier in ("확정급", "확인 권장", "참고"):
        n = sum(1 for r in pos for c in r["cands"] if c.tier == tier); h = sum(1 for r in pos for c in r["cands"] if c.tier == tier and c.id in r["truth"])
        print(f"    {tier}: {h}/{n} ({h/max(n, 1):.0%})")
    strong = sum(len(r["truth"] & {c.id for c in r["cands"] if c.tier == "확정급"}) for r in pos)
    sn = sum(1 for r in pos for c in r["cands"] if c.tier == "확정급")
    rev = sum(len(r["truth"] & {c.id for c in r["cands"] if c.tier != "참고"}) for r in pos)
    rn = sum(1 for r in pos for c in r["cands"] if c.tier != "참고")
    print(f"  확정급 {sn}개 읽으면 정답 {strong}/{tot} ({strong/tot:.0%}) | 확정급+권장 {rn}개 읽으면 {rev}/{tot} ({rev/tot:.0%})")
    rk = []
    for r in pos:
        p = {c.id: k + 1 for k, c in enumerate(r["cands"])}
        rk += [p.get(n, math.inf) for n in r["truth"]]
    fin = [x for x in rk if x != math.inf]
    print(f"  도달 재현율 {len(fin)}/{tot} ({len(fin)/tot:.0%}) | k@100% {max(fin) if fin else '-'}")
    # 경로별 정밀도 (가장 강한 경로 기준은 단계로 대신하고, 경로 이름별 후보/정답)
    pc = collections.Counter(); ph = collections.Counter()
    for r in pos + neg:
        for c in r["cands"]:
            for p in set(x.split(":")[0] for x in c.paths):
                pc[p] += 1
                ph[p] += c.id in r["truth"]
    print("  경로별(후보에 한 번이라도 들어간 경로) 후보/정답: " + ", ".join(f"{p} {ph[p]}/{pc[p]}" for p, _ in pc.most_common()))
    print("\n  문항별 @20 (정답 중 후보 20개 안): ")
    for r in pos:
        print(f"    {r['id']} [{r['kind']}]: 합침 {len(r['truth'] & {c.id for c in r['cands'][:20]})}/{len(r['truth'])}, RAG만 {len(r['truth'] & {c.id for c in lists(r)['RAG만'][:20]})}, 풀 안 {len(r['truth'] & {c.id for c in r['cands']})}")
    print("  영향 없음 문항: " + "; ".join(f"{r['id']}[{r['kind']}] 후보 {len(r['cands'])} (확정급 {sum(c.tier == '확정급' for c in r['cands'])}, 권장 {sum(c.tier == '확인 권장' for c in r['cands'])})" for r in neg))
    unresolved = []
    return pos, neg


if __name__ == "__main__":
    main()
