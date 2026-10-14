"""에이전트 평가 ① 오류 주입 — 규칙 밖 유형: 「법령과 다른 숫자」 (예: 출산휴가 90일 → 60일).

  uv run python scripts/eval_inject_number.py [--n 40] [--seed 1]
법령을 인용하는 내규 항 중, 인용한 조(조가 없으면 그 법령 전체)에 같은 숫자·단위(90일, 3회 …)가 있는 항을 골라 숫자만 바꾼다.
두 경로를 본다
  기준선 점검   이런 오류를 잡는 규칙이 없다 → 0%가 정상 (한계를 숫자로 남김)
  개정안 작성   담당자가 그 숫자를 바꾸는 개정안을 냈을 때, 법령 대조 카드(app/web_law: related → compare)가
              「현행 값은 법령 조문에 있으나 개정안 값은 다름」을 띄우는가
원래 값이 법령 값과 같은지(같은 것을 세는지)는 web_law의 문맥 비교(0.15)를 그대로 쓴다.
출력: out/eval/inject_number_s{seed}.jsonl, 화면 표.
"""

import argparse
import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "app"))

import web_law  # noqa: E402
from regcheck import runinfo  # noqa: E402
from regcheck.lawdb import DEFAULT as LAW_DB, LawDB  # noqa: E402

FLAG = "현행 값은 법령 조문에 있으나 개정안 값은 다름"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    regs = {r["reg_id"]: r for r in (json.loads(p.read_text(encoding="utf-8")) for p in (ROOT / "out" / "parsed").glob("[0-9]*.json"))}
    items = {it["id"]: it for r in regs.values() for x in r["articles"] for it in x["items"]}
    edges = [json.loads(l) for l in (ROOT / "out" / "edges" / "edges_all.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    db = LawDB(LAW_DB)
    run = runinfo.start("eval_inject_number", [ROOT / "out" / "parsed", ROOT / "out" / "edges" / "edges_all.jsonl", LAW_DB], params=vars(a))
    cands, seen = [], set()
    for e in edges:
        if e["status"] not in ("law_pending", "external_rule") or e["src_kind"] != "본문" or e["src"] not in items or e["src"] in seen:
            continue
        law = db.find(e["target_doc"])
        if not law:
            continue
        key, u = db.article(law, e["raw"]) if e["target_type"] == "article" else (None, None)
        old = items[e["src"]]["norm"]
        # 인용한 조가 있으면 그 조, 없으면(법령만 인용) 법령 전체에서 같은 숫자·단위를 찾는다
        law_nums = web_law.numbers(" ".join(web_law._paras(u)) if u else " ".join(p for x in law["조문"].values() for p in web_law._paras(x)))
        for n, unit in web_law._ordered(old):
            if n in law_nums.get(unit, []):
                seen.add(e["src"])
                cands.append({"src": e["src"], "law": law["title"], "key": key, "num": n, "unit": unit, "old": old})
                break
    rng = random.Random(a.seed)
    rng.shuffle(cands)
    res = []
    for c in cands[: a.n]:
        law = db.find(c["law"])
        val = c["num"]
        new_val = str(int(float(val)) * 2) if "." not in val else str(float(val) * 2)
        pat = re.compile(r"(?<![\d.])" + re.escape(val) + r"(\s*" + re.escape(c["unit"]) + ")")
        new = pat.sub(lambda m: new_val + m.group(1), c["old"], count=1)
        focus, units = web_law.focus_of(c["old"], new)
        rel = web_law.related(law, c["old"], k=2, prefer=c["key"] or None, focus=focus, units=units)
        rows = web_law.compare([x["text"] for x in rel], c["old"], new)
        flagged = any(r["note"] == FLAG for r in rows)
        res.append({**{k: c[k] for k in ("src", "law", "key", "num", "unit")}, "new": new_val, "flagged": flagged,
                    "related": [x["article"] for x in rel], "notes": [r["note"] for r in rows], "context": c["old"][:120]})
    out = ROOT / "out" / "eval" / f"inject_number_s{a.seed}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in res), encoding="utf-8")
    n, hit = len(res), sum(x["flagged"] for x in res)
    print(f"규칙 밖 오류 주입: 법령과 다른 숫자 (시드 {a.seed})")
    print(f"  후보: 법령 조문과 같은 숫자·단위가 있는 내규 항 {len(cands)}개 → 주입 {n}개 (숫자를 두 배로)")
    print("  기준선 점검: 0% (이 오류를 잡는 규칙 없음)")
    print(f"  개정안 법령 대조 카드: {hit}/{n} ({hit / max(n, 1):.0%}) 경고")
    for x in res:
        if not x["flagged"]:
            print(f"  [놓침] {x['law']} {('제' + x['key'] + '조') if x['key'] else '(법령만 인용)'} {x['num']}{x['unit']}→{x['new']}{x['unit']} · 고른 법조문 {x['related']} · {x['context'][:60]}")
    runinfo.finish(run, [out], {"후보": len(cands), "주입": n, "법령 대조 경고": hit})


if __name__ == "__main__":
    main()
