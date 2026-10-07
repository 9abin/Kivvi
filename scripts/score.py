"""정답 원장 vs 탐지 결과 채점.

  uv run python scripts/score.py                       # eval/ledger/*.jsonl 전부
  uv run python scripts/score.py --ledger week1         # 하나만
출력: 종류별 재현율, 정답 대상 일치, eval/review_queue.csv (정답 목록 밖 결과 → 사람 확인 대기),
      eval/runs/{실행ID}.json (채점 기록: 코드 커밋·입력 해시·점수·원장 분포. git에 남겨 점수 변화 추적)

규칙 (리포트 261007-*-12):
- 탐지 = 같은 규정·단위·조, 인용 원문이 겹치고, 종류가 COMPATIBLE 안에 있음
- 정답 목록 밖 결과는 오탐이 아니라 '확인 대기'. 사람이 확인해 원장에 넣거나 기각한다
- 오탈자는 범위 밖으로 따로 센다
- 원장 출처의 검토 범위(문서) 안에서만 목록 밖 결과를 뽑는다
"""

import argparse
import collections
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from regcheck import runinfo  # noqa: E402
from regcheck.findings import COMPATIBLE, OUT_OF_SCOPE, same_place  # noqa: E402


def load(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def suggest_ok(gold: dict, f: dict) -> bool | None:
    if not gold.get("answer") or not f.get("suggest"):
        return None
    a, s = re.sub(r"\s", "", gold["answer"]), re.sub(r"\s", "", f["suggest"])
    return s.startswith(a) or a.startswith(s)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default="")
    args = ap.parse_args()
    files = sorted((ROOT / "eval" / "ledger").glob(f"{args.ledger or '*'}.jsonl"))
    fpath = ROOT / "out" / "findings" / "findings.jsonl"
    found = load(fpath)
    run = runinfo.start("score", [fpath, *files], params={"ledger": args.ledger or "전체",
                        "findings_runs": sorted({f["run"] for f in found if f.get("run")})})
    hier = {p.stem: json.loads(p.read_text(encoding="utf-8"))["hierarchy"] for p in (ROOT / "out" / "parsed").glob("[0-9]*.json")}
    results = {}
    matched_fids = set()
    print(f"탐지 결과 {len(found)}개\n")
    for path in files:
        gold = load(path)
        rows = []
        for g in gold:
            if g["type"] in OUT_OF_SCOPE:
                rows.append((g, "범위 밖", None, None))
                continue
            hits = [f for f in found if same_place(g, f) and f["type"] in COMPATIBLE.get(g["type"], {g["type"]})]
            for f in hits:
                matched_fids.add(f["fid"])
            best = next((f for f in hits if suggest_ok(g, f)), hits[0] if hits else None)
            high = [f for f in hits if f.get("priority", "높음") == "높음"]
            res = ("탐지" if high else "탐지(낮음)") if hits else "놓침"
            rows.append((g, res, best, suggest_ok(g, best) if best else None))
        print(f"■ {path.stem}: {len(gold)}건")
        by = collections.defaultdict(lambda: [0, 0])  # [정답 수, 탐지 수, 높음 여부...]
        for g, res, f, ok in rows:
            if res == "범위 밖":
                continue
            by[g["type"]][0] += 1
            by[g["type"]][1] += res.startswith("탐지")
            by[g["type"]].append(res == "탐지")
        for t, v in sorted(by.items()):
            print(f"   {t:12} {v[1]}/{v[0]}  (그중 우선순위 높음 {sum(v[2:])})")
        tot = sum(v[0] for v in by.values())
        hit = sum(v[1] for v in by.values())
        sug = [ok for _, res, _, ok in rows if ok is not None]
        print(f"   → 재현율 {hit}/{tot}" + (f", 정답 대상 제안 일치 {sum(sug)}/{len(sug)}" if sug else "")
              + (f", 범위 밖 {sum(1 for r in rows if r[1] == '범위 밖')}건" if any(r[1] == '범위 밖' for r in rows) else ""))
        results[path.stem] = {
            "정답": len(gold), "채점 대상": tot, "탐지": hit, "높음": sum(sum(v[2:]) for v in by.values()),
            "제안 일치": [sum(sug), len(sug)],
            "종류별": {t: {"정답": v[0], "탐지": v[1], "높음": sum(v[2:])} for t, v in sorted(by.items())},
            "항목": [{"id": g["id"], "type": g["type"], "reg_id": g["reg_id"], "article": g["article"], "quote": g["quote"],
                      "result": res, "fid": f["fid"] if f else "", "suggest_ok": ok} for g, res, f, ok in rows],
            # 원장 분포 (정답셋 대표성 점검용: 위계·단위·종류가 한쪽에 쏠렸는지)
            "분포": {k: dict(collections.Counter(fn(g) for g in gold)) for k, fn in
                     (("위계", lambda g: hier.get(g["reg_id"], "?")), ("단위", lambda g: g.get("unit", "본문")),
                      )} | {"서로 다른 규정": len({g["reg_id"] for g in gold})},
        }
        for g, res, f, ok in rows:
            if res == "놓침":
                print(f"     놓침 {g['id']:5} {g['type']:12} {g['reg_id']} {g['article']} 「{g['quote']}」 — {g['basis'][:40]}")
        print()

    # 정답 목록 밖 결과 → 확인 대기 (1주차 검토 범위인 담당 21건 안에서만)
    from parse_regs import assigned_names
    names = set(assigned_names())
    queue = [f for f in found if f["fid"] not in matched_fids and f["reg_name"] in names]
    qpath = ROOT / "eval" / "review_queue.csv"
    with qpath.open("w", encoding="utf-8-sig", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["fid", "규정", "단위", "조", "항", "인용", "종류", "방법", "설명", "문맥", "판정(오류/정상/애매/판단불가)", "메모"])
        for f in queue:
            w.writerow([f["fid"], f["reg_name"], f["unit"], f["article"], f["para"], f["quote"], f["type"], f["method"],
                        f["note"], f["context"][:100], "", ""])
    total = {k: sum(r[k] for r in results.values()) for k in ("채점 대상", "탐지", "높음")}
    rec = runinfo.finish(run, [qpath], {"원장별": results, "합계": total, "확인 대기": len(queue),
                                         "정답 연결 finding": sorted(matched_fids)}, where="eval")
    print(f"채점 기록 → {rec.relative_to(ROOT)}  (합계 재현율 {total['탐지']}/{total['채점 대상']}, 높음 {total['높음']})")
    print(f"정답 목록 밖 결과(담당 21건 범위) {len(queue)}개 → {qpath}  (사람 확인 후 원장 추가 또는 기각)")
    print("   ", dict(collections.Counter((f["type"], f["unit"]) for f in queue)))


if __name__ == "__main__":
    main()
