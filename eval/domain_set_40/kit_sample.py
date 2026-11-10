"""4도메인(인사·보수·휴가·휴직) 기능2 평가셋 40건: 도메인별 10건씩 개정할 조를 난수로 뽑는다.
사용: python kit_sample.py [시드]   (eval/domain_set_40 에서)
- 시작 조: 도메인 DB에서 해당 도메인 개념(루트 개념 기준, 하위 유형 포함)에 '정의'/'근거'로 연결된 조. 약한 '언급'만인 조는 제외.
- 규정 한 개 상한: 도메인당 3건, 전체 5건.
- 제외: val_set_30(+교체분), benchmark_100, domain_set_30(교체 전후 모두), PR11 24건(r2·r3·징계층), PR12 개정 항목.
"""
import json, glob, sqlite3, collections, re, random, sys
from pathlib import Path
getattr(sys.stdout, "reconfigure", lambda **k: None)(encoding="utf-8")
SEED = int(sys.argv[1]) if len(sys.argv) > 1 else 20261112
random.seed(SEED)
H = Path(__file__).resolve().parent; S = H / "_src"
norm = lambda s: re.sub(r"\s+", "", s or "")
regs = {}
for p in glob.glob(str(S / "parsed" / "[0-9]*.json")):
    r = json.loads(Path(p).read_text(encoding="utf-8")); regs[r["reg_id"]] = r
name2id = {norm(r["name"]): k for k, r in regs.items()}
J = lambda p: json.loads((S / p).read_text(encoding="utf-8"))
JL = lambda p: [json.loads(l) for l in (S / p).read_text(encoding="utf-8").splitlines() if l.strip()]


def aid(rid, label):
    m = re.match(r"제(\d+)조(?:의(\d+))?", norm(label))
    return f"{rid}:{m.group(1)}" + (f"의{m.group(2)}" if m.group(2) else "") if m else None


used = collections.defaultdict(set)
for f, tag in [("pr11_I01_I24_changes", "PR11"), ("pr11_r2_changes", "PR11_r2"), ("pr11_r3_changes", "PR11_r3"),
               ("disc_items_changes", "징계층"), ("dom30_changes", "기존4도메인30"),
               ("domain30_changes", "domain_set_30"), ("domain30_changes_before_round2", "domain_set_30_교체전")]:
    for s in JL(f + ".jsonl"):
        s = s["수정"]; rid = name2id.get(norm(s["규정"])); a = aid(rid, s["조"]) if rid else None
        if a: used[a].add(tag)
for a in J("val_used_articles.json")["articles"]: used[a].add("val30")
for v in J("val_replaced_round1.json").values(): used[v["aid"]].add("val30_교체")
for a in J("used_articles_benchmark.json")["articles"]: used[a].add("벤치마크100")
for a in J("domain30_used_articles.json")["articles"]: used[a].add("domain_set_30")
for v in J("domain30_replaced_round2.json").values():
    used[v["빠진_조"]["aid"]].add("domain_set_30_교체전"); used[v["새_조"]["aid"]].add("domain_set_30")
for a in J("excluded_articles.json"): used[a].add("기제외목록")
for o in JL("pr12_inputs.jsonl"):
    for op in o["amendment_ops"]:
        for k in ("logical_item_id", "logical_unit_id", "logical_article_id", "new_id"):
            m = re.match(r"(\d+):(\d+(?:의\d+)?)(?:-|$)", op.get(k, ""))
            if m: used[f"{m.group(1)}:{m.group(2)}"].add("PR12")

# ---- 도메인별 후보 ----
c = sqlite3.connect(S / "domain_ontology.reviewed.db")
parent = dict(c.execute("select name,parent from concept"))
def root(n):
    while parent.get(n): n = parent[n]
    return n
DOMS = ["인사", "보수", "휴가", "휴직"]
dom = {d: set() for d in DOMS}
for a, con in c.execute("select distinct i.article_id, e.concept from edge_item_concept e join item i on i.id=e.item_id where e.rel in ('정의','근거')"):
    dom[root(con)].add(a)

pool = {d: [] for d in DOMS}
for rid, r in regs.items():
    n_art = len([x for x in r["articles"] if not x.get("deleted")])
    for x in r["articles"]:
        if x.get("deleted") or x["id"] in used: continue
        for d in DOMS:
            if x["id"] in dom[d]:
                pool[d].append({"aid": x["id"], "reg_id": rid, "reg": r["name"], "label": x["label"], "title": x["title"],
                                "text": x["norm"], "n_art": n_art, "domain": d})
print("제외 조", len(used), "| 도메인 후보", {d: len(v) for d, v in pool.items()})

VAL = re.compile(r"\d+\s*(일|회|개월|년|%|퍼센트|원|명|시간|분의\s*\d+|배)")
def ok(kind, p):
    t, lab, ti = p["text"], p["label"], p["title"]
    if len(re.sub(r"\s", "", t)) < 40 or re.search(r"삭제\s*$", t): return False
    if kind == "값 변경": return bool(VAL.search(t)) and len(t) < 700
    if kind == "삭제": return not re.search(r"목적|정의|적용범위|시행", ti) and len(t) < 600 and not re.match(r"제[12]조$", norm(lab))
    if kind == "번호 이동": return not re.search(r"목적|정의", ti) and p["n_art"] >= 6 and re.fullmatch(r"제\d+조", norm(lab)) and int(norm(lab)[1:-1]) >= 3
    if kind == "정의 변경": return bool(re.search(r"용어|^정의|의 정의$|^정의", ti)) or bool(re.search(r"(란|이라 함은|라 함은).{2,60}(말한다|를 말|을 말)|\"[^\"]{2,12}\"(이)?라 한다", t)) and len(t) < 700
    if kind == "위임·법령 변경": return bool(re.search(r"(로|으로) 정한다|에 따라|법\s*제\d+조|시행령|시행규칙|따로 정", t)) and len(t) < 700
    if kind in ("표현 수정", "고립 수정"): return 80 < len(t) < 400

# 도메인마다 종류가 고르게 섞이도록 배분(도메인당 10, 합 40). 정답 개수 분포는 뽑을 때 쓰지 않는다.
PLAN = {
 "인사": [("번호 이동", 1), ("정의 변경", 1), ("값 변경", 2), ("삭제", 1), ("위임·법령 변경", 1), ("표현 수정", 2), ("고립 수정", 2)],
 "보수": [("정의 변경", 1), ("값 변경", 2), ("삭제", 1), ("위임·법령 변경", 2), ("표현 수정", 2), ("고립 수정", 2)],
 "휴가": [("번호 이동", 1), ("값 변경", 2), ("삭제", 1), ("위임·법령 변경", 1), ("표현 수정", 2), ("고립 수정", 3)],
 # 휴직은 정의 변경에 맞는 조가 0개라 표현 수정 1건을 더한다.
 "휴직": [("값 변경", 2), ("삭제", 1), ("위임·법령 변경", 1), ("표현 수정", 3), ("고립 수정", 3)],
}
out, taken = [], set()
per_dom_reg, per_reg = collections.Counter(), collections.Counter()
for d in DOMS:
    for kind, k in PLAN[d]:
        pick = [p for p in pool[d] if ok(kind, p) and p["aid"] not in taken]
        random.shuffle(pick); got = 0
        for p in pick:
            if per_dom_reg[(d, p["reg_id"])] >= 3 or per_reg[p["reg_id"]] >= 5: continue
            taken.add(p["aid"]); per_dom_reg[(d, p["reg_id"])] += 1; per_reg[p["reg_id"]] += 1; got += 1
            out.append({"종류": kind, **p})
            if got == k: break
        if got < k: print("부족", d, kind, got, "/", k)
random.shuffle(out)
for i, o in enumerate(out, 1): o.update({"id": f"E{i:03d}", "분할": "봉인"})
json.dump({"seed": SEED, "items": out}, open(H / "sample.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
json.dump({k: sorted(v) for k, v in sorted(used.items())}, open(S / "excluded_articles_all.json", "w", encoding="utf-8"), ensure_ascii=False, indent=0)
print(len(out), "건 | 도메인", dict(collections.Counter(o["domain"] for o in out)), "| 종류", dict(collections.Counter(o["종류"] for o in out)))
print("규정 수", len({o["reg_id"] for o in out}), "| 겹침", [o["aid"] for o in out if o["aid"] in used])
for o in out: print(o["id"], o["domain"], o["종류"], o["reg"], o["label"], o["title"])
