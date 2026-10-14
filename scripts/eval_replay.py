"""에이전트 평가 ② 과거 재현: 이 회사의 실제 개정(조 신설·번호 이동) 전부를 되돌려 에이전트에 넣고, 정답과 비교한다.

  uv run python scripts/eval_replay.py
사건: src/regcheck/replay.events (개정 표시로 재현할 수 있는 날). 정답: eval/agent/replay_gold.json (사람 확인된 것만)
채점
  함께 개정    다른 규정에서 고칠 곳 — 정답 재현율, 정답에 없는 제안(오탐) 수
  개정안 자체  그 개정안에 들어 있던 문제를 에이전트가 「남은 깨짐」으로 알렸는가
  교차 확인    번호 이동 사건마다, 파서를 거치지 않은 원문 문자열 검색(「규정명 … 제N조」)으로 다른 규정의 옛 번호 인용을 찾아
              에이전트 제안과 비교 (관련 조항 표와 같은 데이터로 정답을 만들면 순환이라 따로 확인)
출력: out/eval/replay_result.json, 화면 표
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from regcheck import runinfo  # noqa: E402
from regcheck.agent import build_package  # noqa: E402
from regcheck.amend import build_changeset  # noqa: E402
from regcheck.lawdb import DEFAULT as LAW_DB, LawDB  # noqa: E402
from regcheck.replay import events, reconstruct  # noqa: E402


def jl(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def text_search(regs: dict, reg: dict, old_labels: list[str]) -> list[tuple[str, str, str]]:
    """다른 규정 원문에서 「(이 규정 이름) … 제N조」 (N = 옮겨진 옛 번호). 문장 안 30자 이내"""
    hits = []
    for r in regs.values():
        if r["reg_id"] == reg["reg_id"]:
            continue
        units = [(a["label"], it["norm"]) for a in r["articles"] for it in a["items"]] + [(ap["id"].split(":")[1], ap["text"]) for ap in r["appendices"]]
        for label, t in units:
            for old in old_labels:
                n = re.match(r"제(\d+)조(?:의(\d+))?", old)
                pat = re.escape(reg["name"]) + r"[^.。\n]{0,30}?제\s*" + n.group(1) + r"\s*조" + (r"\s*의\s*" + n.group(2) if n.group(2) else r"(?!\s*의\s*\d)")
                if re.search(pat, t):
                    hits.append((r["name"], label, old))
    return hits


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    regs = {r["reg_id"]: r for r in (json.loads(p.read_text(encoding="utf-8")) for p in (ROOT / "out" / "parsed").glob("[0-9]*.json"))}
    rels = jl(ROOT / "out" / "relations" / "relations.jsonl")
    edges = jl(ROOT / "out" / "edges" / "edges_all.jsonl")
    gold_path = ROOT / "eval" / "agent" / "replay_gold.json"
    gold = {k: v for k, v in json.loads(gold_path.read_text(encoding="utf-8")).items() if not k.startswith("_")}
    run = runinfo.start("eval_replay", [ROOT / "out" / "parsed", ROOT / "out" / "relations" / "relations.jsonl", gold_path, LAW_DB])
    db = LawDB(LAW_DB) if LAW_DB.exists() else None

    rows, tp, fn, fp, amend_hit, amend_n = [], 0, 0, 0, 0, 0
    for reg in sorted(regs.values(), key=lambda r: r["name"]):
        for d in events(reg):
            key = f"{reg['name']} {d}"
            prior, text, note = reconstruct(reg, d)
            cs, _ = build_changeset(prior, text, "replay", key)
            pkg = build_package(cs, {**regs, reg["reg_id"]: prior}, rels, edges, lawdb=db)
            props = [(c["reg_name"], line) for c in pkg["companion"] for line in c["amendment"].splitlines() if line.strip()]
            g = gold.get(key, {"companion": [], "in_amendment": []})
            got = []
            for x in g["companion"]:
                ok = any(name == x["reg"] and line.startswith(x["article"]) and f'"{x["old"]}"' in line and f'"{x["new"]}"' in line
                         for name, line in props)
                got.append(ok)
            tp += sum(got)
            fn += len(got) - sum(got)
            extra = [p for p in props if not any(p[0] == x["reg"] and p[1].startswith(x["article"]) for x in g["companion"])]
            fp += len(extra)
            breaks = pkg["remaining_breaks"]
            amend = []
            for x in g["in_amendment"]:
                hit = any(b["article"] == x["article"] and x["quote"] in (b.get("quote") or "") for b in breaks)
                amend.append({**x, "에이전트 알림": hit})
            amend_n += sum(1 for x in g["in_amendment"] if x["status"] == "확인됨")
            amend_hit += sum(1 for x in amend if x["status"] == "확인됨" and x["에이전트 알림"])
            search = text_search(regs, reg, list(note["moved"].values())) if note["moved"] else []
            rows.append({"event": key, "moved": note["moved"], "inserted": note["inserted"], "proposals": props,
                         "gold_companion": g["companion"], "gold_found": got, "extra": extra, "in_amendment": amend,
                         "remaining_breaks": [(b.get("unit"), b["article"], b.get("quote")) for b in breaks],
                         "text_search": search})

    out = ROOT / "out" / "eval" / "replay_result.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    moved = [r for r in rows if r["moved"]]
    print(f"과거 재현: 사건 {len(rows)}개 (번호 이동 {len(moved)}, 신설만 {len(rows) - len(moved)})")
    print(f"  함께 개정: 정답 {tp + fn}건 중 {tp}건 제안, 정답에 없는 제안 {fp}건")
    print(f"  개정안 자체 문제(확인됨): {amend_n}건 중 {amend_hit}건 알림")
    for r in moved:
        print(f"  [이동] {r['event']} {r['moved']} → 제안 {r['proposals'] or '없음'} / 원문 검색 {r['text_search'] or '없음'}")
    for r in rows:
        if r["extra"] or r["in_amendment"] or not all(r["gold_found"]):
            print(f"  {r['event']}: 놓침 {[x for x, ok in zip(r['gold_companion'], r['gold_found']) if not ok] or '-'} 오탐 {r['extra'] or '-'} "
                  f"개정안 자체 {[(x['article'], x['type'], x['status'], '알림' if x['에이전트 알림'] else '못 알림') for x in r['in_amendment']] or '-'}")
    runinfo.finish(run, [out], {"사건": len(rows), "번호 이동": len(moved), "함께 개정 정답": tp + fn, "제안 일치": tp, "오탐": fp,
                                "개정안 자체 문제(확인됨)": amend_n, "알림": amend_hit})


if __name__ == "__main__":
    main()
