"""변경 분석(regcheck.change_query)이 개정문에서 수정 종류와 질의를 제대로 뽑는지 본다 (영향 점검 평가셋 68의 개정문 24건).

  uv run python scripts/eval_change_analysis.py [--splits 개발,봉인]
정답 비교는 수정 종류 하나뿐이다 (평가셋의 `수정.종류`는 개정문 작성자가 붙인 값). 영향 정답은 쓰지 않는다.
"""
import argparse
import collections
import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")

from regcheck.change_query import analyze  # noqa: E402

K = ROOT / "eval/impact_label_kit/산출"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--splits", default="개발,봉인")
    a = ap.parse_args()
    regs = {r["reg_id"]: r for r in (json.loads(Path(p).read_text(encoding="utf-8")) for p in glob.glob(str(ROOT / "out/parsed/[0-9]*.json")))}
    by = {r["name"]: k for k, r in regs.items()}
    ch = [json.loads(l) for l in (K / "changes.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    ok = tot = 0; conf = collections.Counter()
    for c in ch:
        if c["분할"] not in a.splits.split(","):
            continue
        s = c["수정"]; rid = by.get(s["규정"])
        if not rid:
            print(f"{c['id']} [{c['분할']}] {s['규정']} {s['조']}: 규정 아님 (법령 개정) — 분석 대상 아님")
            continue
        q = analyze(regs[rid], s["개정문"], c["id"])
        tot += 1; match = q.kind == s["종류"]; ok += match; conf[(s["종류"], q.kind)] += 1
        print(f"{c['id']} [{c['분할']}] {s['규정']} {s['조']} | 작성자 {s['종류']} / 자동 {q.kind} {'✓' if match else '✗'} | {q.evidence}")
        print(f"      시작 {q.starts[:3]}{'…' if len(q.starts) > 3 else ''} | 지운 말 {[r[:20] for r in q.removed][:2]} | 새 말 {[x[:24] for x in q.added][:2]}")
        print(f"      값 {q.old_values}->{q.new_values} | 용어 {q.terms} 핵심 {q.term_cores} | 새 핵심어 {q.phrases[:6]}")
        if q.error:
            print("      오류:", q.error)
    print(f"\n수정 종류 일치 {ok}/{tot}")
    for (g, p), n in sorted(conf.items()):
        if g != p:
            print(f"  불일치: 작성자 {g} -> 자동 {p}: {n}건")


if __name__ == "__main__":
    main()
