"""기관별 내부규정 구조 · 인용 점검 지표 (KVIC와 다른 공공기관을 같은 코드로 비교).

  uv run python scripts/compare_institutions.py 한국벤처투자=data/한국벤처투자_내부규정 창업진흥원=data/others/창업진흥원 ...
각 기관 폴더를 파싱(out/others/{기관}/parsed)하고, 그 기관 규정만으로 인용을 해석한다(regcheck.citations).
지표: 규정 · 조 수, 인용(같은 규정 · 다른 규정 · 법령), 다른 규정을 인용하는 규정 비율, 인용 상태별 건수(대상 조 없음 · 삭제된 조 · 제목 불일치 · 규정명 다름 · 공개본에 없는 규정).
주의: 파서는 KVIC 원문에 맞춰 다듬었다. 다른 기관은 서식 차이로 파싱이 틀려 「문제」가 부풀 수 있으므로 표본을 사람이 확인해야 한다(--sample).
결과: out/others/compare.json, 화면 표
"""

import argparse
import collections
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from regcheck.build import build_all  # noqa: E402
from regcheck.citations import STATUS, Registry, extract_edges  # noqa: E402

PROBLEM = ["missing_article", "missing_item", "deleted_target", "title_mismatch", "name_mismatch", "unknown_regulation"]


def measure(name: str, root: Path) -> tuple[dict, list[dict]]:
    out = ROOT / "out" / "others" / name / "parsed"
    regs = build_all(root, out)
    registry = Registry(regs)
    edges = []
    for r in regs:
        for e in extract_edges(r, registry):
            d = e.to_dict()
            d["src_reg"], d["src_name"] = r["reg_id"], r["name"]
            edges.append(d)
    reg_edges = [d for d in edges if d["target_type"] != "law" and d["status"] not in ("law_pending", "external_rule", "addendum_history")]
    same = [d for d in reg_edges if d.get("target_reg") == d["src_reg"]]
    other = [d for d in reg_edges if d.get("target_reg") != d["src_reg"]]
    citing_other = {d["src_reg"] for d in other}
    st = collections.Counter(d["status"] for d in edges)
    m = {
        "기관": name, "규정": len(regs), "본문 조": sum(len(r["articles"]) for r in regs),
        "인용 전체": len(edges), "같은 규정 인용": len(same), "다른 규정 인용": len(other),
        "법령 인용": st["law_pending"] + st["external_rule"],
        "다른 규정을 인용하는 규정": f"{len(citing_other)}/{len(regs)}",
        **{STATUS[k]: st[k] for k in PROBLEM},
        "점검 대상 합계": sum(st[k] for k in PROBLEM),
        "다른 규정 인용 중 문제 비율": round(sum(1 for d in other if d["status"] in PROBLEM) / max(len(other), 1), 3),
    }
    return m, edges


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("inst", nargs="+", help="기관명=원문 폴더")
    ap.add_argument("--sample", type=int, default=0, help="기관마다 문제 인용 표본 n개를 출력 (사람 확인용)")
    a = ap.parse_args()
    rows, samples = [], {}
    for spec in a.inst:
        name, _, path = spec.partition("=")
        m, edges = measure(name, ROOT / path)
        rows.append(m)
        probs = [d for d in edges if d["status"] in PROBLEM]
        random.seed(0)
        samples[name] = random.sample(probs, min(a.sample, len(probs))) if a.sample else []
    keys = list(rows[0])
    for k in keys:
        print(f"{k:28} | " + " | ".join(str(r[k]) for r in rows))
    for name, ss in samples.items():
        if ss:
            print(f"\n## {name} 문제 인용 표본")
            for d in ss:
                print(f"- [{STATUS[d['status']]}] {d['src_name']} {d.get('src', '')} → 「{d.get('raw', '')}」 (대상 {d.get('target_doc') or ''} {d.get('target_label') or ''})"
                      f"\n    문맥: {(d.get('context') or '')[:110]}")
    p = ROOT / "out" / "others"
    p.mkdir(parents=True, exist_ok=True)
    (p / "compare.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
