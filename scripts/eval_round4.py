"""4차 봉인 평가: 사전 등록한 지표(리포트 79)를 한 번에 잰다.
  uv run python scripts/eval_round4.py --yes-open-sealed --kit eval/impact_label_kit_r4/산출 --tag "4차 봉인"
지표: 1 합침 − RAG만(@20) · 2 확정급+권장 읽었을 때 · 3 확정급 정밀도 · 4 영향 없음 문항 확정급 오경보 · 5 확정급 카드 근거 문장 확보율 ·
      6 도달 재현율 · 7 기본 화면(RAG 30 + 그래프 20) 재현율과 문항당 후보 수 · 8 분석 실패 문항 수와 수정 종류 분류 일치
"""
import argparse
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "scripts"))
sys.stdout.reconfigure(encoding="utf-8")
import eval_collect_dev as ecd  # noqa: E402
from combo.metrics import paired_diff_cluster  # noqa: E402
from regcheck.evidence import card  # noqa: E402
from regcheck.review_list import split_view  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes-open-sealed", action="store_true")
    ap.add_argument("--kit", required=True)
    ap.add_argument("--tag", default="4차 봉인")
    a = ap.parse_args()
    if not a.yes_open_sealed:
        sys.exit("봉인 문항은 한 번만 연다: --yes-open-sealed")
    ecd.K = ROOT / a.kit
    pos, neg, unit_text, nodes = ecd.build("봉인")
    tot = sum(len(r["truth"]) for r in pos)
    print(f"[{a.tag}] 영향 있는 문항 {len(pos)}개 (정답 {tot}건), 영향 없음 문항 {len(neg)}개")
    rag_ids = lambda r: [c for c in r["cands"] if "RAG" in c.paths]  # noqa: E731
    hit = lambda r, lst, k: len(r["truth"] & {c.id for c in lst[:k]})  # noqa: E731
    pairs = [(hit(r, r["cands"], 20), len(r["truth"]), hit(r, rag_ids(r), 20), len(r["truth"])) for r in pos]
    comb = sum(p[0] for p in pairs); ragm = sum(p[2] for p in pairs)
    d = paired_diff_cluster(pairs)
    win = sum(p[0] > p[2] for p in pairs); lose = sum(p[0] < p[2] for p in pairs)
    print(f"1. 후보 20개 안 정답: 합침 {comb}/{tot} ({comb/tot:.0%}) RAG만 {ragm}/{tot} ({ragm/tot:.0%}) | 차이 {d[0]:+.0%} [{d[1]:+.0%}, {d[2]:+.0%}] 이긴 {win} 진 {lose} 같음 {len(pairs)-win-lose}")
    rev = sum(len(r["truth"] & {c.id for c in r["cands"] if c.tier != "참고"}) for r in pos); rn = sum(1 for r in pos for c in r["cands"] if c.tier != "참고")
    print(f"2. 확정급+권장 {rn}개 읽으면 정답 {rev}/{tot} ({rev/tot:.0%})")
    sn = [(r, c) for r in pos + neg for c in r["cands"] if c.tier == "확정급"]; sh = sum(c.id in r["truth"] for r, c in sn)
    print(f"3. 확정급 정밀도 {sh}/{len(sn)}" + (f" ({sh/len(sn):.0%})" if sn else "") + ("" if len(sn) >= 10 else " — 확정급 10개 미만: 표본 부족"))
    fp = [(r["id"], c.id) for r in neg for c in r["cands"] if c.tier == "확정급"]
    print(f"4. 영향 없음 문항 확정급 오경보 {len(fp)}건 {fp}")
    qn = sum(1 for r, c in sn if card(c, r["cq"], nodes, unit_text)["quote"])
    print(f"5. 확정급 카드 근거 문장 {qn}/{len(sn)}")
    rk = []
    for r in pos:
        p = {c.id: k + 1 for k, c in enumerate(r["cands"])}
        rk += [p.get(n, math.inf) for n in r["truth"]]
    fin = [x for x in rk if x != math.inf]
    print(f"6. 도달 재현율 {len(fin)}/{tot} ({len(fin)/tot:.0%}) | k@100% {max(fin) if fin else '-'}")
    dh = ah = dn = 0
    for r in pos:
        v = split_view(r["cands"]); dh += len(r["truth"] & {c.id for c in v["default"]}); ah += len(r["truth"] & {c.id for c in v["all"]}); dn += len(v["default"])
    negsz = [len(split_view(r["cands"])["default"]) for r in neg]
    print(f"7. 기본 화면 재현율 {dh}/{tot} ({dh/tot:.0%}) 전체 {ah}/{tot} | 문항당 후보 평균 {dn/len(pos):.0f} | 영향 없음 문항 기본 화면 후보 {negsz}")
    allr = pos + neg
    fails = [r["id"] for r in allr if r["kind"] == "분석 실패"]
    ch = {x["id"]: x["수정"]["종류"] for x in ecd.jl(ecd.K / "changes.jsonl")}
    same = sum(1 for r in allr if r["kind"] == ch[r["id"]])
    print(f"8. 분석 실패 {len(fails)}건 {fails} | 수정 종류 자동 분류가 작성자 종류와 일치 {same}/{len(allr)}")
    print("\n문항별 @20 (합침 / RAG만 / 정답 / 풀 안) 와 분류:")
    for r in pos:
        print(f"  {r['id']} 작성자 {ch[r['id']]} → 자동 {r['kind']}: {hit(r, r['cands'], 20)} / {hit(r, rag_ids(r), 20)} / {len(r['truth'])} / {len(r['truth'] & {c.id for c in r['cands']})}")
    print("영향 없음: " + "; ".join(f"{r['id']}[{r['kind']}] 후보 {len(r['cands'])} (확정급 {sum(c.tier == '확정급' for c in r['cands'])})" for r in neg))


if __name__ == "__main__":
    main()
