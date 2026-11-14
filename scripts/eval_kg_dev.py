"""지식 그래프의 수정 영향 후보를 영향 점검 평가셋(68)에서 잰다. 기본은 개발 분할만 쓴다.

  uv run python scripts/eval_kg_dev.py                  # 개발 분할
  uv run python scripts/eval_kg_dev.py --split 봉인 --yes-open-sealed   # 봉인은 한 번만. 규칙을 확정한 뒤에만

입력: out/kg/{nodes,edges}.jsonl (scripts/build_kg.py), out/parsed, eval/impact_label_kit/산출/{changes,labels_final}.jsonl
수정 종류는 개정문 작성자가 붙인 값을 입력으로 쓴다 (실제 서비스에서는 개정문에서 분류하거나 담당자가 고른다).
정답은 사람 검토 전 초안이다.
"""
import argparse
import collections
import glob
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")

from regcheck.impact_eval import Truth, norm  # noqa: E402
from regcheck.kg import KG, moved_range  # noqa: E402

K = ROOT / "eval" / "impact_label_kit" / "산출"


def jl(p):
    return [json.loads(l) for l in Path(p).read_text(encoding="utf-8").splitlines() if l.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="개발")
    ap.add_argument("--yes-open-sealed", action="store_true")
    ap.add_argument("--hops", type=int, default=1)
    a = ap.parse_args()
    if a.split == "봉인" and not a.yes_open_sealed:
        sys.exit("봉인 분할은 한 번만 연다. 규칙 확정 뒤에 --yes-open-sealed로 실행한다.")
    nodes = {r["id"]: r for r in jl(ROOT / "out/kg/nodes.jsonl")}
    nodes = {k: {kk: vv for kk, vv in v.items() if kk != "id"} for k, v in nodes.items()}
    kg = KG(nodes, jl(ROOT / "out/kg/edges.jsonl"))
    regs = {r["reg_id"]: r for r in (json.loads(Path(p).read_text(encoding="utf-8")) for p in glob.glob(str(ROOT / "out/parsed/[0-9]*.json")))}
    by_name = {r["name"]: rid for rid, r in regs.items()}
    item_text = {it["id"]: it["norm"] for r in regs.values() for x in r["articles"] for it in x["items"]}
    truth_map = Truth(nodes, regs)
    resolve, start_of = truth_map.resolve, truth_map.start_of

    def terms_for(start, amend):
        q = re.search(r'"([^"]+)"', amend)
        want = norm(q.group(1))[:12] if q else ""
        out = []
        for tid in kg.terms_defined_in(start):
            if want and want in norm(item_text.get(nodes[tid]["item"], "")):
                out.append(tid)
        return out or kg.terms_defined_in(start)

    ch = {r["id"]: r for r in jl(K / "changes.jsonl")}
    fin = {r["id"]: r for r in jl(K / "labels_final.jsonl")}
    tot = collections.Counter(); bypath = collections.Counter(); base = collections.Counter(); bykind = collections.defaultdict(collections.Counter)
    ctrl = []
    for i in sorted(ch):
        c = ch[i]
        if c["분할"] != a.split:
            continue
        s = c["수정"]; st = start_of(s); kind = s["종류"]
        if not st:
            print(f"{i} 시작점 해석 실패: {s['규정']} {s['조']} ({kind})")
            continue
        hint = terms_for(st, s["개정문"]) if kind == "정의 변경" else None
        starts = moved_range(s["개정문"], by_name[s["규정"]], nodes) if kind == "번호 이동" else []
        res = kg.impact_many(starts, kind) if len(starts) > 1 else kg.impact(st, kind, hint, hops=a.hops)
        cand = set(res["candidates"]); regl = set(res["reg_level"])
        inb = {r["src"] for s_ in (starts or [st]) for r in kg.inb.get(("CITES", s_), []) if r["src"] != s_}
        truth = fin[i]["영향"]
        if not truth:
            ctrl.append((i, kind, len(cand), len(regl)))
            print(f"\n{i} {s['규정']} {s['조']} [{kind}] 영향 없음 | 후보 {len(cand)} 규정단위 {len(regl)}")
            continue
        print(f"\n{i} {s['규정']} {s['조']} [{kind}] | 후보 {len(cand)} 규정단위 {len(regl)} | 정답 {len(truth)}" + (f" | 용어 {[nodes[t]['name'] for t in hint]}" if hint else ""))
        for e in truth:
            n = resolve(e)
            if n is None:
                w = "해석불가"
            elif n in cand:
                w = "후보"; bypath["+".join(sorted(res["candidates"][n]["paths"]))] += 1
            elif n in regl or (nodes.get(n, {}).get("reg") and f"REG:{nodes[n]['reg']}" in regl):
                w = "규정단위"
            else:
                w = "못 찾음"
            tot[w] += 1; bykind[kind][w] += 1; base["인용받음"] += (n in inb)
            if w in ("못 찾음",) :
                print(f"    못 찾음 [{e.get('확신')}] {e['규정']} {e['조']}")
    n_t = sum(tot.values())
    print(f"\n== {a.split} 합계: 정답 {n_t} | 후보에 듦 {tot['후보']} ({tot['후보']/n_t:.0%}) | 규정단위로만 {tot['규정단위']} | 못 찾음 {tot['못 찾음']} | 해석불가 {tot['해석불가']}")
    print(f"   비교: 인용받음(1단계)만 {base['인용받음']} ({base['인용받음']/n_t:.0%})")
    print("   종류별:", {k: dict(v) for k, v in bykind.items()})
    print("   후보에 든 경로:", dict(bypath))
    print("   영향 없음 문항 후보 수:", ctrl)


if __name__ == "__main__":
    main()
