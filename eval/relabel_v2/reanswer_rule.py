"""저장된 실행 결과에서 규칙 후보 답변만 새 근거(evidence.card 확장 · 프롬프트, 261112)로 다시 쓴다 (luna · 검색은 그대로). 사용: python eval/relabel_v2/reanswer_rule.py <bm|test> <입력 pipe.jsonl> <출력.jsonl>"""
import glob, json, sys, threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
ROOT = Path(__file__).resolve().parents[2]; sys.path[:0] = [str(ROOT / "src")]; sys.stdout.reconfigure(encoding="utf-8")
from regcheck.impact_view import load_context, analyze
from regcheck.collect import Candidate
from regcheck.evidence import card
from regcheck.answer_writer import prompt_for, make_writer
from regcheck.explain import valid
d, inp, outp = sys.argv[1:4]
rj = lambda p: [json.loads(l) for l in Path(p).read_text(encoding="utf-8").splitlines() if l.strip()]
regs = {r["reg_id"]: r for r in (json.loads(Path(p).read_text(encoding="utf-8")) for p in glob.glob(str(ROOT / "out/parsed/[0-9]*.json")))}
by = {r["name"]: k for k, r in regs.items()}; ctx = load_context(ROOT, regs); writer = make_writer("gpt-5.6-luna")
ch = {f"{d}:{m['id']}": m for m in rj(ROOT / f"eval/relabel_v2/{d}/changes.jsonl")}
runs = rj(inp); stat = {"n": 0, "q": 0, "sq": 0}
def one(r):
    m = ch[r["q"]]; s0 = m["수정"]; cq = analyze(regs[by[s0["규정"]]], s0["개정문"], m["id"])
    for s in r["rule"]:
        cd = card(Candidate(s["id"], 0.0, list(s["paths"]), s["tier"]), cq, ctx.nodes, ctx.unit_text)
        ev = dict(s.get("evidence") or {}); ev.update({"quote": cd["quote"] or "", "quote_verified": bool(cd["quote"]), "source_quote": cd["source_quote"] or "",
                                                      "target_text": "" if cd["quote"] else ctx.unit_text.get(s["id"], "")[:400]})
        s["evidence"] = ev
        p = prompt_for(SimpleNamespace(id=s["id"], paths=s["paths"], evidence=ev), s0["개정문"], r["change_text"], ctx.nodes)
        try: t = (writer(p) or "").strip()
        except Exception as e: t = ""; print("실패", repr(e)[:100])
        ok = valid(t, p); s["answer"] = {"by": "writer", "text": t if ok else "", "checked": ok, "raw": t}
        stat["n"] += 1; stat["q"] += bool(cd["quote"]); stat["sq"] += bool(cd["source_quote"])
    return r
with ThreadPoolExecutor(6) as ex: res = list(ex.map(one, runs))
Path(outp).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in res), encoding="utf-8")
print(stat)
