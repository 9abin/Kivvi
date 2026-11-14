"""휴직 · 보수 · 징계 시험 항목의 AI 라벨을 사람이 검수할 수 있는 시트로 묶는다 (로컬 전용: 규정 본문이 들어간다).

검수 시트에는 AI 판정을 숨기고(눈가림), 정답지(AI 초안)를 따로 둔다. 검수 대상은 AI의 '예' · '불확실' 전부와, 'AI 아니오'에서 무작위로 고른 대조 표본.
출력: out/review_sheet/검수시트.csv (검수자용), 정답지_AI초안.csv (대조용)
"""

import csv
import glob
import json
import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")

from regcheck.impact_view import load_context  # noqa: E402

CONTROL_N = 40
SEED = 20261105
GROUPS = [("휴직", "eval/leave_items", "out/leave_items", ""), ("보수", "eval/pay_items", "out/pay_items", "_v2"), ("징계", "eval/disc_items", "out/disc_items", "")]


def load_labels(i, od, suf):
    p = ROOT / od / f"labels_{i}{suf}.json"
    if not p.exists():
        p = ROOT / od / f"labels_{i}.json"
    out = {x["unit"]: x for x in json.loads(p.read_text(encoding="utf-8"))}
    pe = ROOT / "out/pool_extra" / f"labels_{i}.json"
    if pe.exists():
        for x in json.loads(pe.read_text(encoding="utf-8")):
            out.setdefault(x["unit"], x)
    return out


def main():
    regs = {r["reg_id"]: r for r in (json.loads(Path(p).read_text(encoding="utf-8")) for p in glob.glob(str(ROOT / "out/parsed/[0-9]*.json")))}
    ctx = load_context(ROOT, regs)
    name = lambda u: regs[u.split(":")[0]]["name"]
    label = lambda u: ctx.nodes.get(u, {}).get("label") or u.split(":", 1)[1]
    rows, controls = [], []
    for _, ed, od, suf in GROUPS:
        for line in (ROOT / ed / "changes.jsonl").read_text(encoding="utf-8").splitlines():
            it = json.loads(line)
            i, m = it["id"], it["수정"]
            for u, x in load_labels(i, od, suf).items():
                row = {"항목": i, "개정": f"{m['규정']} {m['조']} {m['항'] or ''}항 | {m['개정문']}", "후보": f"{name(u)} {label(u)}", "uid": u, "후보 본문": (ctx.unit_text.get(u, "")[:500]).replace("\n", " "),
                       "AI 판정": x["label"], "AI 근거 인용": x.get("quote", ""), "AI 이유": x.get("why", "")}
                (rows if x["label"] in ("예", "불확실") else controls).append(row)
    random.Random(SEED).shuffle(controls)
    rows += [dict(c, **{"구분": "대조"}) for c in controls[:CONTROL_N]]
    for r in rows:
        r.setdefault("구분", r["AI 판정"])
    random.Random(SEED + 1).shuffle(rows)
    out = ROOT / "out/review_sheet"
    out.mkdir(parents=True, exist_ok=True)
    blind = ["번호", "항목", "개정", "후보", "후보 본문", "검수 판정(예/아니오/판단불가)", "원본에 이미 있던 불일치인가(예/아니오)", "근거·메모"]
    key = ["번호", "항목", "단위ID", "후보", "구분", "AI 판정", "AI 근거 인용", "AI 이유"]
    with (out / "검수시트.csv").open("w", encoding="utf-8-sig", newline="") as f, (out / "정답지_AI초안.csv").open("w", encoding="utf-8-sig", newline="") as g:
        w, k = csv.writer(f), csv.writer(g)
        w.writerow(blind); k.writerow(key)
        for n, r in enumerate(rows, 1):
            w.writerow([n, r["항목"], r["개정"], r["후보"], r["후보 본문"], "", "", ""])
            k.writerow([n, r["항목"], r["uid"], r["후보"], r["구분"], r["AI 판정"], r["AI 근거 인용"], r["AI 이유"]])
    from collections import Counter
    print("행", len(rows), dict(Counter(r["구분"] for r in rows)))


if __name__ == "__main__" and "--rules" not in sys.argv:
    main()


def build_rule_sheet():
    """기존 규칙 후보(기한 · 정의 불일치 24개) + 횟수 한도 불일치(COUNT-01)를 현업이 확인할 시트로. 판정이 아니라 확인 요청이다."""
    regs = {r["reg_id"]: r for r in (json.loads(Path(p).read_text(encoding="utf-8")) for p in glob.glob(str(ROOT / "out/parsed/[0-9]*.json")))}
    ctx = load_context(ROOT, regs)
    name = lambda u: regs[u.split(":")[0]]["name"] + " " + (ctx.nodes.get(u, {}).get("label") or u.split(":", 1)[1])
    text = lambda u: (ctx.unit_text.get(u, "")[:400]).replace("\n", " ")
    rows = []
    edges = [json.loads(line) for line in (ROOT / "out/kg/edges.jsonl").read_text(encoding="utf-8").splitlines()]
    seen = set()
    for e in edges:
        if e["type"] in ("VALUE_DIFF", "DEF_DIFF"):
            k = (e["type"], *sorted((e["src"], e["dst"])))
            if k in seen:
                continue
            seen.add(k)
            rows.append(("기한 불일치(DL-01)" if e["type"] == "VALUE_DIFF" else "정의 불일치(DEF-01)", e["src"], e["dst"], str(e.get("evidence") or e.get("note") or e.get("title") or "")))
    from regcheck import ruleset
    for e in ruleset.infer(regs, [], [r for r in ruleset.load_rules() if r["id"] in ("COUNT-01", "AUTH-01")]):
        label = "횟수 한도 불일치(COUNT-01)" if e["rule_id"] == "COUNT-01" else "승인·의결 주체 차이(AUTH-01)"
        rows.append((label, e["src_article"], e["target"], e["note"]))   # 위계 · 법령 기준 힌트 포함
    out = ROOT / "out/review_sheet"
    out.mkdir(parents=True, exist_ok=True)
    with (out / "규칙후보_검수시트.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["번호", "규칙", "조 A", "조 A 본문", "조 B", "조 B 본문", "규칙이 본 사유", "검수 판정(실제 불일치/의도된 차이/오탐/판단불가)", "원본에 이미 있던 문제인가", "근거·메모"])
        for n, (rule, a, b, why) in enumerate(rows, 1):
            w.writerow([n, rule, name(a), text(a), name(b), text(b), why, "", "", ""])
    print("규칙 후보 시트 행", len(rows))


if __name__ == "__main__" and "--rules" in sys.argv:
    build_rule_sheet()
