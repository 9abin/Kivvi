"""점검 결과 정밀도 표본: 점검 결과 중 일부를 사람이 판정해 「알린 것 중 실제 문제가 몇 %인가」를 잰다 (에이전트 평가 — 정밀도).

  uv run python scripts/eval_precision.py make            # 표본 30건 → eval/agent/precision_sample.csv (엑셀로 열어 「판정」 칸 채우기)
  uv run python scripts/eval_precision.py score           # 판정한 CSV 채점 → 방법별·전체 정밀도와 95% 구간
표본: 방법(rule·semantic·law_db·history)별로 점검 결과 수에 비례해 뽑는다 (비례 층화라 전체 정밀도는 단순 비율로 계산해도 치우치지 않음).
판정 기준 (「판정」 칸에 셋 중 하나)
  맞음  담당자가 확인·수정할 가치가 있는 실제 문제 (법령 개정확인은: 바뀐 법령 내용이 이 내규 문장과 실제로 관련 있음)
  아님  문제가 아님 (오탐). 메모에 이유 한 줄
  모름  문서만으로 판단할 수 없음 (담당 부서 확인 필요)
원문(규정 문장)은 KVIC 저작물이라 이 CSV는 내부 판정용이다.
"""

import argparse
import collections
import csv
import json
import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from regcheck.review import key_baseline  # noqa: E402

CSV = ROOT / "eval" / "agent" / "precision_sample.csv"
COLS = ["번호", "판정", "메모", "유형", "방법", "규정", "위치", "인용 문구", "원문 문장", "도구 설명", "도구 제안", "인용 대상의 실제 내용", "review_key"]
METHOD = {"rule": "규칙", "semantic": "의미 비교", "law_db": "법률 DB", "history": "개정 이력"}


def jl(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def target_text(f: dict, edges: dict, arts: dict) -> str:
    """인용이 실제로 가리키는 조의 제목·첫 부분 (판정할 때 「그 조가 정말 그 내용인가」를 보게)"""
    if f["method"] == "law_db":
        return (f.get("law_text") or "")[:200]
    e = edges.get((f.get("src_id"), f.get("quote")))
    a = arts.get(e.get("target_id")) if e else None
    first = (a["items"][0]["norm"] if a and a["items"] else "")
    out = f"「{a['reg_name']}」 {a['label']}({a['title']}) {first[:180]}" if a else ""
    if f["method"] == "semantic" and f.get("suggest") and f["suggest"] != f.get("quote"):
        out += f"  ※ 도구가 더 맞다고 본 조: {f['suggest']}"
    return out


def make(n: int, seed: int) -> None:
    found = jl(ROOT / "out" / "findings" / "findings.jsonl")
    edges = {(e["src"], e["raw"]): e for e in jl(ROOT / "out" / "edges" / "edges_all.jsonl")}
    regs = [json.loads(p.read_text(encoding="utf-8")) for p in (ROOT / "out" / "parsed").glob("[0-9]*.json")]
    arts = {a["id"]: {**a, "reg_name": r["name"]} for r in regs for a in r["articles"]}
    by = collections.defaultdict(list)
    for f in found:
        by[f["method"]].append(f)
    total = len(found)
    quota = {m: max(1, round(n * len(v) / total)) for m, v in by.items()}
    while sum(quota.values()) > n:
        quota[max(quota, key=quota.get)] -= 1
    while sum(quota.values()) < n:
        quota[max(by, key=lambda m: len(by[m]) / quota[m])] += 1
    rng = random.Random(seed)
    pick = []
    for m in sorted(by):
        pick += rng.sample(by[m], quota[m])
    rng.shuffle(pick)   # 방법별로 몰려 있으면 판정이 앞 결과에 끌려가서 섞는다
    CSV.parent.mkdir(parents=True, exist_ok=True)
    with CSV.open("w", encoding="utf-8-sig", newline="") as fp:   # 엑셀에서 한글이 깨지지 않게 BOM
        w = csv.writer(fp)
        w.writerow(COLS)
        for i, f in enumerate(pick, 1):
            place = f"{f.get('unit', '')} {f.get('article', '')}" + (f" 제{f['para']}항" if str(f.get("para") or "0") != "0" else "")
            w.writerow([i, "", "", f["type"].replace("_", " "), METHOD.get(f["method"], f["method"]), f["reg_name"], place.strip(),
                        f.get("quote", ""), (f.get("context") or "").replace("\n", " "), f.get("note", ""), f.get("suggest", ""),
                        target_text(f, edges, arts), key_baseline(f)])
    print(f"표본 {len(pick)}건 (점검 결과 {total}건 중, 방법별 {dict(quota)}) → {CSV}")
    print("엑셀로 열어 「판정」 칸에 맞음 / 아님 / 모름을 적고 저장한 뒤: uv run python scripts/eval_precision.py score")


def wilson(k: int, n: int) -> tuple[float, float]:
    if not n:
        return 0.0, 0.0
    z, p = 1.96, k / n
    c = (p + z * z / (2 * n)) / (1 + z * z / n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, c - h), min(1.0, c + h)


def score() -> None:
    with CSV.open(encoding="utf-8-sig") as fp:
        rows = list(csv.DictReader(fp))
    bad = [r["번호"] for r in rows if r["판정"].strip() not in ("맞음", "아님", "모름")]
    if bad:
        print(f"판정이 비었거나 맞음/아님/모름이 아닌 행: {', '.join(bad)}")
    res = {}
    groups = collections.defaultdict(list)
    for r in rows:
        groups[r["방법"]].append(r["판정"].strip())
        groups["전체"].append(r["판정"].strip())
    print("방법\t표본\t맞음\t아님\t모름\t정밀도(맞음/(맞음+아님))\t95% 구간")
    for g in [*sorted(k for k in groups if k != "전체"), "전체"]:
        v = groups[g]
        k, no, unk = v.count("맞음"), v.count("아님"), v.count("모름")
        lo, hi = wilson(k, k + no)
        prec = k / (k + no) if k + no else 0
        res[g] = {"표본": len(v), "맞음": k, "아님": no, "모름": unk, "정밀도": round(prec, 3), "구간": [round(lo, 3), round(hi, 3)]}
        print(f"{g}\t{len(v)}\t{k}\t{no}\t{unk}\t{prec:.2f}\t{lo:.2f}~{hi:.2f}")
    out = ROOT / "out" / "eval" / "precision_result.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"→ {out}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["make", "score"])
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--seed", type=int, default=14)
    a = ap.parse_args()
    if a.cmd == "make":
        if CSV.exists() and any(r.get("판정") for r in csv.DictReader(CSV.open(encoding="utf-8-sig"))):
            sys.exit(f"이미 판정한 표본이 있습니다: {CSV} (덮어쓰지 않음)")
        make(a.n, a.seed)
    else:
        score()


if __name__ == "__main__":
    main()
