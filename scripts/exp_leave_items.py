"""휴직 층 시험 항목(eval/leave_items/changes.jsonl): 층을 끈 경우와 켠 경우의 후보를 뽑아 검수용으로 저장한다 (RAG 없이 그래프 · 문구 경로만).

출력: out/leave_items/<id>.txt (후보 목록 + 층에서만 나온 후보 표시), 표준출력에 요약.
"""

import glob
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")

from regcheck.impact_view import load_context, run  # noqa: E402

TOPIC = "pay" if "--topic=pay" in sys.argv else ("disc" if "--topic=disc" in sys.argv else "leave")   # leave: 휴직 층(eval/leave_items), pay: 보수 · 수당 층(eval/pay_items)
_DIR = {"pay": "pay_items", "disc": "disc_items", "leave": "leave_items"}[TOPIC]
ITEMS = ROOT / "eval" / _DIR / "changes.jsonl"
OUT = ROOT / "out" / _DIR


def make_layer(unit_text):
    if TOPIC == "disc":
        from regcheck.disc_layer import DiscLayer
        return DiscLayer(unit_text)
    if TOPIC == "pay":
        from regcheck.pay_layer import PayLayer
        return PayLayer(unit_text)
    from regcheck.leave_layer import LeaveLayer
    return LeaveLayer(unit_text)


def main():
    regs = {r["reg_id"]: r for r in (json.loads(Path(p).read_text(encoding="utf-8")) for p in glob.glob(str(ROOT / "out/parsed/[0-9]*.json")))}
    ctx = load_context(ROOT, regs)
    by_name = {r["name"]: k for k, r in regs.items()}
    lab = lambda u: f"{regs[u.split(':')[0]]['name']} {ctx.nodes.get(u, {}).get('label') or u.split(':', 1)[1]}"
    out = OUT
    out.mkdir(parents=True, exist_ok=True)
    for line in ITEMS.read_text(encoding="utf-8").splitlines():
        it = json.loads(line)
        m = it["수정"]
        off = run(ctx, regs, by_name[m["규정"]], m["개정문"], it["id"], leave=None)
        on = run(ctx, regs, by_name[m["규정"]], m["개정문"], it["id"], leave=make_layer(ctx.unit_text))
        if on.get("error"):
            print(it["id"], "오류:", on["error"])
            continue
        a_off = {c.id: c for c in off["view"]["all"]}
        a_on = {c.id: c for c in on["view"]["all"]}
        only = [c for u, c in a_on.items() if u not in a_off]
        lines = [f"{it['id']} {m['규정']} {m['조']} {m['항']}항 {on['cq'].kind}: {m['개정문']}", f"끔 {len(a_off)} / 켬 {len(a_on)} / 층에서만 {len(only)}", ""]
        for c in a_on.values():
            lines.append(f"{'★' if c.id not in a_off else ' '} [{c.tier}] {lab(c.id)}  {','.join(c.paths)}")
        (out / f"{it['id']}.txt").write_text("\n".join(lines), encoding="utf-8")
        print(it["id"], on["cq"].kind, "끔", len(a_off), "켬", len(a_on), "층에서만", len(only), dict(Counter(c.tier for c in only)))


if __name__ == "__main__":
    main()


def build_pools():
    """라벨러용 후보 묶음: 층을 켠 후보 + 휴직 조 전체. out/leave_items/pool_<id>.md"""
    regs = {r["reg_id"]: r for r in (json.loads(Path(p).read_text(encoding="utf-8")) for p in glob.glob(str(ROOT / "out/parsed/[0-9]*.json")))}
    ctx = load_context(ROOT, regs)
    by_name = {r["name"]: k for k, r in regs.items()}
    leave_units = set(make_layer(ctx.unit_text).assign)
    for line in ITEMS.read_text(encoding="utf-8").splitlines():
        it = json.loads(line)
        m = it["수정"]
        on = run(ctx, regs, by_name[m["규정"]], m["개정문"], it["id"], leave=make_layer(ctx.unit_text))
        ids = {c.id for c in on["view"]["all"]} | leave_units
        ids -= set(on["cq"].starts)
        rows = [f"# {it['id']}  {m['규정']} {m['조']} {m['항']}항\n\n개정문: {m['개정문']}\n\n바뀐 조 원문:\n{ctx.unit_text.get(on['cq'].starts[0], '')[:1200]}\n\n---\n"]
        for u in sorted(ids):
            rows.append(f"## [{u}] {regs[u.split(':')[0]]['name']}\n{ctx.unit_text.get(u, "")[:(600 if TOPIC != "leave" else 900)]}\n")
        (OUT / f"pool_{it['id']}.md").write_text("\n".join(rows), encoding="utf-8")
        print(it["id"], len(ids))


if __name__ == "__main__" and "--pools" in sys.argv:
    build_pools()
