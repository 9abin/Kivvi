"""한석희의 인사 · 보수 · 휴가 · 휴직 120문항에서 우리 후보 수집이 정답 쌍의 짝을 후보에 올리는지 잰다 (1단계: 후보 찾기).
  uv run python scripts/eval_four_domain.py <gold.jsonl> [--rag]
- 문항의 개정 항목(scripts/four_domain_parse.py → out/four_domain_ops.json)마다 개정문을 만들어 후보 수집(impact_view.run)에 넣는다.
- 정답: 문항의 expected_delta(new · resolved) 문제의 logical_pair(조 id 쌍). 한쪽이 같은 문항에서 개정된 조이면, 다른 쪽(짝)이 그 개정 항목의 후보에 있는지 센다.
- 후보 수집은 우리 코퍼스(out/parsed)를 쓰고, 문항의 before_overlay(가상 개정 전 상태)는 쓰지 않는다. 정답은 한석희 브랜치의 AI 초안이다.
"""
import collections
import glob
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "scripts"))
sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("HF_HUB_OFFLINE", "1")


def amend_text(op):
    art = op["article"].split(":")[1]
    label = op["label"]
    if op["type"] == "move":
        n = re.sub(r"\D", "", label) if label.startswith("제") else None
        t = op["to"]
        if label.startswith("제") and t.startswith("제"):
            return f"{label}부터 {label}까지를 각각 {t}부터 {t}까지로 한다."
        return f"{label}를 {t}로 한다."
    parts = op["id"].split(":")[1].split("-")
    ho, hang = (int(parts[2]) if len(parts) > 2 else 0), (int(parts[1]) if len(parts) > 1 else 0)
    where = label if label.startswith("제") else label.replace("별지제", "별지 제").replace("호서식", "호 서식").replace("호 서식", "호 서식")
    if label.startswith("제"):
        if hang:
            where += f"제{hang}항"
        if ho:
            where += f"제{ho}호"
    return f'{where} 중 "{op["old"]}"를 "{op["new"]}"로 한다.'


def main():
    from regcheck.impact_view import load_context, run
    gold = {json.loads(l)["id"]: json.loads(l) for l in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines() if l.strip()}
    ops = json.loads((ROOT / "out/four_domain_ops.json").read_text(encoding="utf-8"))
    regs = {r["reg_id"]: r for r in (json.loads(Path(p).read_text(encoding="utf-8")) for p in glob.glob(str(ROOT / "out/parsed/[0-9]*.json")))}
    ctx = load_context(ROOT, regs)
    rag = None
    if "--rag" in sys.argv:
        import eval_recall_all as era
        rag = era.build_rag(ctx)
    cache = {}
    stat = collections.Counter(); fails = []
    for q, items in ops.items():
        for k, op in enumerate(items):
            reg = op["article"].split(":")[0]
            txt = amend_text(op)
            try:
                r = run(ctx, regs, reg, txt, f"{q}-{k}", rag=rag)
            except Exception as e:   # noqa: BLE001
                stat["오류"] += 1; fails.append((q, k, txt, repr(e)[:80])); cache[(q, k)] = None; continue
            if r["cq"].kind == "분석 실패":
                stat["분석 실패"] += 1; fails.append((q, k, txt, r["cq"].error[:80])); cache[(q, k)] = None; continue
            stat["분석 성공"] += 1
            cache[(q, k)] = dict(kind=r["cq"].kind, all={c.id for c in r["view"]["all"]}, dflt={c.id for c in r["view"]["default"]}, starts=set(r["cq"].starts or []))
    print("개정 항목", sum(len(v) for v in ops.values()), dict(stat))
    print("분석 실패 예:", fails[:5])
    res = collections.defaultdict(lambda: collections.Counter())
    miss = []
    sizes = []
    for q, g in gold.items():
        touched = {}   # 조 id -> [(k)]
        for k, op in enumerate(ops[q]):
            touched.setdefault(op["article"], []).append(k)
        for state in ("new", "resolved"):
            for iid in g["expected_delta"][state]:
                iss = (g["after_issues"] if state == "new" else g["before_issues"])[iid]
                a, b = [x.split("-")[0] for x in iss["logical_pair"]]
                kind = iss["kind"]
                for me, partner in ((a, b), (b, a)):
                    if me in touched:
                        ks = [k for k in touched[me] if cache.get((q, k))]
                        inall = any(partner in cache[(q, k)]["all"] for k in ks)
                        indef = any(partner in cache[(q, k)]["dflt"] for k in ks)
                        res[(state, kind)]["쌍"] += 1; res[(state, kind)]["후보 안"] += inall; res[(state, kind)]["기본화면 안"] += indef
                        res[(state, kind)]["분석 실패"] += (not ks)
                        if not inall:
                            miss.append((q, state, kind, me, partner))
                        break
                else:
                    res[(state, kind)]["개정된 조 없음"] += 1
    for q in gold:
        for k in range(len(ops[q])):
            if cache.get((q, k)):
                sizes.append((len(cache[(q, k)]["all"]), len(cache[(q, k)]["dflt"])))
    print(f"\n개정 항목당 후보 평균: 전체 {sum(a for a,_ in sizes)/len(sizes):.0f}, 기본 화면 {sum(b for _,b in sizes)/len(sizes):.0f}")
    print(f"{'상태/종류':28s} {'쌍':>4s} {'후보 안':>7s} {'기본화면':>8s} {'분석실패':>6s}")
    for key in sorted(res):
        c = res[key]
        print(f"{key[0]+' / '+key[1]:28s} {c['쌍']:4d} {c['후보 안']:4d} ({c['후보 안']/max(c['쌍'],1):.0%}) {c['기본화면 안']:4d} ({c['기본화면 안']/max(c['쌍'],1):.0%}) {c['분석 실패']:4d}   개정된 조 없음 {c['개정된 조 없음']}")
    tot = collections.Counter()
    for c in res.values():
        tot.update(c)
    print(f"{'합계':28s} {tot['쌍']:4d} {tot['후보 안']:4d} ({tot['후보 안']/max(tot['쌍'],1):.0%}) {tot['기본화면 안']:4d} ({tot['기본화면 안']/max(tot['쌍'],1):.0%})")
    print("\n놓친 쌍 표본:", miss[:12])
    json.dump({"cache": {f"{q}-{k}": (None if v is None else {"kind": v["kind"], "all": sorted(v["all"]), "dflt": sorted(v["dflt"])}) for (q, k), v in cache.items()}}, open(ROOT / "out/four_domain_stage1.json", "w", encoding="utf-8"), ensure_ascii=False)


if __name__ == "__main__":
    main()
