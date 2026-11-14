"""에이전트 평가 ① 오류 주입: 정상 조문에 오류를 넣고 탐지가 찾는지 (유형별 재현율).

  uv run python scripts/eval_inject.py                    # 시드 1, 유형마다 25개
  uv run python scripts/eval_inject.py --seed 2 --n 30    # 시험용 셋은 시드를 바꿔 새로 (규칙을 고칠 때 본 시드로 점수 내지 않음)
출력: eval/agent/inject_s{seed}.jsonl (주입 목록 = 정답), out/eval/inject_s{seed}_result.jsonl, 화면에 유형별 표
입력: out/parsed, out/edges/edges_all.jsonl, data/law_rag.sqlite3. 원문 파일은 바꾸지 않는다 (메모리 사본에 주입).
"""

import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from regcheck import runinfo  # noqa: E402
from regcheck.citations import Registry  # noqa: E402
from regcheck.inject import EXPECT, Detector, candidates, sample  # noqa: E402
from regcheck.lawdb import DEFAULT as LAW_DB, LawDB  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--n", type=int, default=25)
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    regs = {r["reg_id"]: r for r in (json.loads(p.read_text(encoding="utf-8")) for p in (ROOT / "out" / "parsed").glob("[0-9]*.json"))}
    edges = [json.loads(l) for l in (ROOT / "out" / "edges" / "edges_all.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    run = runinfo.start("eval_inject", [ROOT / "out" / "parsed", ROOT / "out" / "edges" / "edges_all.jsonl", LAW_DB], params=vars(a))
    db = LawDB(LAW_DB) if LAW_DB.exists() else None
    registry = Registry(list(regs.values()))
    cands = candidates(regs, edges, db)
    injs = sample(cands, a.n, a.seed, regs)
    gold = ROOT / "eval" / "agent" / f"inject_s{a.seed}.jsonl"
    gold.parent.mkdir(parents=True, exist_ok=True)
    gold.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in injs), encoding="utf-8")

    det = Detector(regs, registry, db, registry.law_aliases)
    res = [det.check(x) for x in injs]
    out = ROOT / "out" / "eval" / f"inject_s{a.seed}_result.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in res), encoding="utf-8")

    by = collections.defaultdict(collections.Counter)
    for r in res:
        by[r["type"]][r["result"]] += 1
    print(f"오류 주입 평가 (시드 {a.seed}, 유형마다 최대 {a.n}개)")
    print("유형\t후보\t주입\t탐지\t다른 유형\t놓침\t재현율\t기대 탐지 유형")
    tot = collections.Counter()
    summary = {}
    for typ in EXPECT:
        c = by[typ]
        n = sum(v for k, v in c.items() if k != "주입 실패")
        rec = c["탐지"] / n if n else 0
        summary[typ] = {"후보": len(cands[typ]), "주입": n, "탐지": c["탐지"], "다른 유형": c["다른 유형으로 탐지"], "놓침": c["놓침"],
                        "주입 실패": c["주입 실패"], "재현율": round(rec, 3)}
        tot.update({"주입": n, "탐지": c["탐지"]})
        print(f"{typ}\t{len(cands[typ])}\t{n}\t{c['탐지']}\t{c['다른 유형으로 탐지']}\t{c['놓침']}\t{rec:.2f}\t{'/'.join(sorted(EXPECT[typ]))}")
    print(f"전체\t\t{tot['주입']}\t{tot['탐지']}\t\t\t{tot['탐지'] / max(tot['주입'], 1):.2f}")
    side = sum(r.get("side_effects", 0) for r in res)
    print(f"주입 위치 밖에서 새로 생긴 결과: {side}건 (0이 정상)")
    for r in res:
        if r["result"] != "탐지":
            print(f"  [{r['result']}] {r['id']} {regs[r['reg_id']]['name']} {r['article']} 「{r['old']}」→「{r['new']}」 {r.get('found_types') or ''}")
    runinfo.finish(run, [gold, out], {"유형별": summary, "전체 재현율": round(tot["탐지"] / max(tot["주입"], 1), 3), "주입 밖 새 결과": side})


if __name__ == "__main__":
    main()
