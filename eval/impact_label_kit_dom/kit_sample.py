"""4개 도메인 평가셋 30건: 개정할 조를 난수로 뽑는다(선택 편향 방지). 개정문은 뽑힌 조 본문만 읽고 따로 쓴다."""
import json, glob, sqlite3, collections, re, random, sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
random.seed(20261109)
regs = {}
for p in glob.glob("C:/Users/Admin/Downloads/kvic_pr11/out/parsed/[0-9]*.json"):
    r = json.loads(Path(p).read_text(encoding="utf-8")); regs[r["reg_id"]] = r
c = sqlite3.connect("p12db/domain_ontology.reviewed.db")
dom = set(a for (a,) in c.execute("select distinct i.article_id from edge_item_concept e join item i on i.id=e.item_id where e.rel in ('정의','근거')"))
used = {op["article"] for v in json.load(open("p12db/four_domain_ops.json", encoding="utf-8")).values() for op in v if "article" in op}
for l in open("pr11set/changes.jsonl", encoding="utf-8"):
    s = json.loads(l)["수정"]
    m = re.match(r"제(\d+)조(?:의(\d+))?", s["조"])
    rid = next((k for k, r in regs.items() if r["name"] == s["규정"]), None)
    if rid and m: used.add(f"{rid}:{m.group(1)}" + (f"의{m.group(2)}" if m.group(2) else ""))
DEV = ["인사규정", "취업규칙", "급여규정", "직제규정", "승진지침", "성과급지급지침", "복지·후생규정", "직원채용지침", "휴직자 복무관리 지침", "복무규정", "퇴직금지급규정", "임직원교육훈련지침"]
norm = lambda s: re.sub(r"\s+", "", s)
devnames = {norm(x) for x in DEV}
pool = []
for a in sorted(dom):
    rid = a.split(":")[0]
    r = regs.get(rid)
    if not r or a in used: continue
    art = next((x for x in r["articles"] if x["id"] == a and not x.get("deleted")), None)
    if not art: continue
    pool.append({"aid": a, "reg_id": rid, "reg": r["name"], "label": art["label"], "title": art["title"], "text": art["norm"], "n_art": len([x for x in r["articles"] if not x.get("deleted")]), "dev": norm(r["name"]) in devnames})
print("후보 조", len(pool), "개발 가능", sum(p["dev"] for p in pool), "봉인 가능", sum(not p["dev"] for p in pool))
VAL = re.compile(r"\d+\s*(일|회|개월|년|%|퍼센트|원|명|시간|분의\s*\d+|배)")
DEL_BAD = re.compile(r"^제[12]조|목적|정의")
def ok(kind, p):
    t, lab = p["text"], p["label"]
    if kind == "값 변경": return bool(VAL.search(t)) and len(t) < 700
    if kind == "삭제": return not re.search(r"목적|정의|적용범위", p["title"]) and len(t) < 600
    if kind == "번호 이동": return not re.search(r"목적|정의", p["title"]) and p["n_art"] >= 6
    if kind == "정의 변경": return "정의" in p["title"] and "라 함은" in t or ("정의" in p["title"] and "말한다" in t)
    if kind == "위임·법령 변경": return bool(re.search(r"(로|으로) 정한다|에 따라|법\s*제\d+조|시행령|시행규칙", t)) and len(t) < 700
    if kind == "표현 수정": return 80 < len(t) < 400
    if kind == "고립 수정": return 80 < len(t) < 400
PLAN = {"개발": [("값 변경", 3), ("삭제", 2), ("번호 이동", 2), ("정의 변경", 2), ("위임·법령 변경", 1), ("표현 수정", 1), ("고립 수정", 1)],
        "봉인": [("값 변경", 5), ("삭제", 2), ("번호 이동", 2), ("정의 변경", 3), ("위임·법령 변경", 3), ("표현 수정", 1), ("고립 수정", 2)]}
out, taken, per_reg = [], set(), collections.Counter()
n = 0
for split, plan in PLAN.items():
    cand = [p for p in pool if p["dev"] == (split == "개발")]
    for kind, k in plan:
        pick = [p for p in cand if ok(kind, p) and p["aid"] not in taken]
        random.shuffle(pick)
        got = 0
        for p in pick:
            if per_reg[(split, p["reg_id"])] >= (2 if split == "봉인" else 3): continue
            n += 1; taken.add(p["aid"]); per_reg[(split, p["reg_id"])] += 1; got += 1
            out.append({"id": f"D{n:02d}", "분할": split, "종류": kind, **p})
            if got == k: break
        if got < k: print("부족", split, kind, got, "/", k)
json.dump(out, open("kit_dom/sample.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(len(out), collections.Counter((o["분할"], o["종류"]) for o in out))

# D06은 '제9조의2'처럼 조의 번호가 섞인 조라 번호 이동에 맞지 않아, 같은 조건에서 조 번호가 단순한 조로 다시 뽑는다(별도 시드)
random.seed(20261110)
cand = [p for p in pool if p["dev"] and p["aid"] not in taken and "의" not in p["label"] and not re.search(r"목적|정의", p["title"]) and p["n_art"] >= 6 and per_reg[("개발", p["reg_id"])] < 3]
random.shuffle(cand)
new = cand[0]
for o in out:
    if o["id"] == "D06":
        o.update({"aid": new["aid"], "reg_id": new["reg_id"], "reg": new["reg"], "label": new["label"], "title": new["title"], "text": new["text"], "n_art": new["n_art"]})
        print("D06 교체:", new["reg"], new["label"], new["title"])
        print(new["text"][:500])
json.dump(out, open("kit_dom/sample.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
# 번호 이동 4건의 이동 범위(해당 규정의 마지막 단순 조 번호)
for o in out:
    if o["종류"] == "번호 이동":
        labs = [int(m.group(1)) for x in regs[o["reg_id"]]["articles"] if not x.get("deleted") for m in [re.fullmatch(r"제(\d+)조", norm(x["label"]))] if m]
        print(o["id"], o["reg"], o["label"], "마지막 단순 조", max(labs), "조 수", len(labs))
