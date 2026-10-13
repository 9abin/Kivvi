"""자연어 요청 → 개정문 성공률 (로컬 LLM). 요청 8개는 내가 쓴 것이라 독립 평가가 아니다.

  uv run python scripts/nl_eval.py --model qwen2.5:7b
지표: 적용 성공(적용기가 읽고 적용함), 시도 횟수, 대상 조 맞음, 의도 맞음(기대 문구·삭제·신설). 출력: out/nl_eval/{model}.json
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from regcheck import runinfo  # noqa: E402
from regcheck.nl import draft  # noqa: E402


def extra_changes(req: dict, res: dict) -> list[str]:
    """요청하지 않은 조의 내용 변경 (번호 이동만은 제외). LLM이 덤으로 고친 곳."""
    if not res.get("ok"):
        return []
    return [c["old_label"] for c in res["changeset"]["changes"]
            if c["origin"] and c["old_label"] != req["article"] and any(op["op"] != "renumber" for op in c["ops"])]


def judge(req: dict, res: dict) -> tuple[bool, bool]:
    """(대상 조 맞음, 의도 맞음)"""
    if not res.get("ok"):
        return False, False
    ch = res["changeset"]["changes"]
    exp = req["expect"]
    new = res["new_reg"]
    target = any(c["old_label"] == req["article"] or c["new_label"] == req["article"] for c in ch)
    art = next((a for a in new["articles"] if (a.get("_old_label") or a["label"]) == req["article"]), None)
    text = (art["title"] + " " + " ".join(it["norm"] for it in art["items"])) if art else ""
    ok = True
    if "contains" in exp:
        ok &= exp["contains"].replace(" ", "") in text.replace(" ", "")
    if "not_contains" in exp:
        ok &= exp["not_contains"] not in text
    if exp.get("deleted"):
        ok &= bool(art and art.get("deleted"))
    if "inserted" in exp:
        ok &= any(not c["origin"] and c["new_label"] == exp["inserted"] for c in ch)
        target = ok
    if "moved" in exp:
        ok &= res["changeset"]["label_map"].get(exp["moved"][0]) == exp["moved"][1]
    return target, bool(ok)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen2.5:7b")
    args = ap.parse_args()
    regs = {r["reg_id"]: r for r in (json.loads(p.read_text(encoding="utf-8")) for p in (ROOT / "out" / "parsed").glob("[0-9]*.json"))}
    reqs = [json.loads(l) for l in (ROOT / "eval" / "nl_requests.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    run = runinfo.start("nl_eval", [ROOT / "eval" / "nl_requests.jsonl"], models={"llm": args.model})
    rows = []
    for q in reqs:
        t0 = time.time()
        try:
            res = draft(regs, q["request"], model=args.model)
        except Exception as e:   # 모델 연결 실패 등
            res = {"ok": False, "tries": [{"output": "", "error": f"호출 실패: {e}"}]}
        target, intent = judge(q, res)
        rows.append({"id": q["id"], "request": q["request"], "ok": res.get("ok", False), "tries": len(res.get("tries", [])),
                     "target": target, "intent": intent, "extra": extra_changes(q, res), "amendment": res.get("amendment", ""),
                     "errors": [t["error"] for t in res.get("tries", []) if t["error"]], "candidates": res.get("candidates"),
                     "by": "규칙" if any(t.get("by") == "rule" for t in res.get("tries", [])) else "LLM",
                     "seconds": round(time.time() - t0, 1)})
        r = rows[-1]
        print(f"{q['id']} 적용 {'O' if r['ok'] else 'X'} 시도 {r['tries']} 대상 {'O' if target else 'X'} 의도 {'O' if intent else 'X'} "
              f"[{r['by']}] {'요청 밖 ' + ','.join(r['extra']) + ' ' if r['extra'] else ''}{r['seconds']}초 | {(r['amendment'] or (r['errors'][-1] if r['errors'] else ''))[:110]}")
    n = len(rows)
    summ = {"적용 성공": sum(r["ok"] for r in rows), "첫 시도 성공": sum(r["ok"] and r["tries"] == 1 for r in rows),
            "대상 맞음": sum(r["target"] for r in rows), "의도 맞음": sum(r["intent"] for r in rows),
            "의도 맞음·요청 밖 변경 없음": sum(r["intent"] and not r["extra"] for r in rows),
            "그중 규칙으로 작성": sum(r["by"] == "규칙" and r["ok"] for r in rows), "요청": n}
    out = ROOT / "out" / "nl_eval"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{re.sub(r'[^\w.-]', '_', args.model)}.json"
    path.write_text(json.dumps({"model": args.model, "summary": summ, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    runinfo.finish(run, [path], summ)
    print(summ, "→", path)


if __name__ == "__main__":
    main()
