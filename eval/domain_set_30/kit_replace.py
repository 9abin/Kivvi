"""4도메인(인사·보수·휴가·휴직) 평가셋 30건: 개정할 조를 난수로 뽑는다. 개정문은 뽑힌 조 본문만 읽고 따로 쓴다.
사용: python kit_sample.py [시드]   (eval/domain_set_30 에서)
- 시작 조는 모두 도메인 DB에서 개념에 '정의'/'근거'로 연결된 조(약한 '언급'만인 조 제외)
- 정답(영향 조)은 도메인 밖도 포함, 규정별 최대 3건
- 제외: PR11 24건, PR12 개정 항목, 기존 4도메인 30건, val셋 30건+교체분, 벤치마크 100건
"""
import json, glob, sqlite3, collections, re, random, sys
from pathlib import Path
getattr(sys.stdout, "reconfigure", lambda **k: None)(encoding="utf-8")
SEED = 20261111
random.seed(SEED)
H = Path(__file__).resolve().parent
PARSED = "C:/Users/Admin/Downloads/kvic_pr11/out/parsed/[0-9]*.json"
norm = lambda s: re.sub(r"\s+", "", s or "")
regs = {}
for p in glob.glob(PARSED):
    r = json.loads(Path(p).read_text(encoding="utf-8")); regs[r["reg_id"]] = r
name2id = {norm(r["name"]): k for k, r in regs.items()}


def aid(rid, label):
    m = re.match(r"제(\d+)조(?:의(\d+))?", norm(label))
    return f"{rid}:{m.group(1)}" + (f"의{m.group(2)}" if m.group(2) else "") if m else None


# ---- 제외 목록 ----
used = collections.defaultdict(set)  # 조 id -> 출처
for f, tag in [("pr11_I01_I24_changes", "PR11"), ("pr11_r2_changes", "PR11_r2"), ("pr11_r3_changes", "PR11_r3"),
               ("disc_items_changes", "징계층"), ("dom30_changes", "기존4도메인30")]:
    for l in (H / "_src" / f"{f}.jsonl").read_text(encoding="utf-8").splitlines():
        s = json.loads(l)["수정"]
        rid = name2id.get(norm(s["규정"]))
        a = aid(rid, s["조"]) if rid else None
        if a: used[a].add(tag)
_va = json.loads((H / "_src" / "val_used_articles.json").read_text(encoding="utf-8"))["articles"]
_va += [v["aid"] for v in json.loads((H / "_src" / "val_replaced_round1.json").read_text(encoding="utf-8")).values()]
for a in _va: used[a].add("val30")
used_ids_extra = json.loads((H / "_src" / "used_articles_benchmark.json").read_text(encoding="utf-8"))["articles"]
for a in used_ids_extra: used[a].add("벤치마크100")
for a, tags in json.loads((H / "_src" / "excluded_articles.json").read_text(encoding="utf-8")).items(): used[a].add("기제외목록")
for l in (H / "_src" / "pr12_inputs.jsonl").read_text(encoding="utf-8").splitlines():
    for o in json.loads(l)["amendment_ops"]:
        for k in ("logical_item_id", "logical_unit_id", "logical_article_id", "new_id"):
            m = re.match(r"(\d+):(\d+(?:의\d+)?)(?:-|$)", o.get(k, ""))
            if m: used[f"{m.group(1)}:{m.group(2)}"].add("PR12")

# ---- 도메인 후보 ----
c = sqlite3.connect(H / "_src" / "domain_ontology.reviewed.db")
dom = {a for (a,) in c.execute("select distinct i.article_id from edge_item_concept e join item i on i.id=e.item_id where e.rel in ('정의','근거')")}

_cur = json.loads((H / "sample.json").read_text(encoding="utf-8"))["items"]
_rm = set(json.loads((H / "_round2_removed.json").read_text(encoding="utf-8")))
for o in _cur: used[o["aid"]].add("sample1")
pool = []
for rid, r in regs.items():
    n_art = len([x for x in r["articles"] if not x.get("deleted")])
    for x in r["articles"]:
        if x.get("deleted") or x["id"] in used: continue
        pool.append({"aid": x["id"], "reg_id": rid, "reg": r["name"], "label": x["label"], "title": x["title"],
                     "text": x["norm"], "n_art": n_art, "domain": x["id"] in dom})
print("제외 조", len(used), "| 후보 조", len(pool), "| 도메인 후보", sum(p["domain"] for p in pool))

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


# 종류 배분(합 30): 지수 모양이 나오도록 흔한 값 변경·표현·고립을 많이, 큰 영향(번호 이동·정의 변경)은 소수.
PLAN = {True: [("번호 이동", 2), ("정의 변경", 1), ("삭제", 1), ("위임·법령 변경", 1), ("값 변경", 1)]}
out, taken, per_reg = [], set(), collections.Counter(o["reg_id"] for o in _cur if o["id"] not in _rm)
for is_dom, plan in PLAN.items():
    cand = [p for p in pool if p["domain"] == is_dom]
    for kind, k in plan:
        pick = [p for p in cand if ok(kind, p) and p["aid"] not in taken]
        random.shuffle(pick)
        got = 0
        for p in pick:
            if per_reg[p["reg_id"]] >= 3: continue
            taken.add(p["aid"]); per_reg[p["reg_id"]] += 1; got += 1
            out.append({"종류": kind, **p})
            if got == k: break
        if got < k: print("부족", is_dom, kind, got, "/", k)
# 번호를 V01.. 로 매기되 도메인/비도메인이 섞이도록 난수 순서로
random.shuffle(out)
for i, o in enumerate(out, 1): o.update({"id": f"N{i:02d}", "분할": "봉인"})
json.dump({"seed": SEED, "items": out}, open(H / "sample_round2.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
json.dump({k: sorted(v) for k, v in sorted(used.items())}, open(H / "_excl_tmp.json", "w", encoding="utf-8"), ensure_ascii=False, indent=0)
print(len(out), "건 | 도메인", sum(o["domain"] for o in out), "| 종류", dict(collections.Counter(o["종류"] for o in out)))
print("규정 수", len({o["reg_id"] for o in out}), "| 겹침 검사", [o["aid"] for o in out if o["aid"] in used])
for o in out: print(o["id"], "D" if o["domain"] else "-", o["종류"], o["reg"], o["label"], o["title"])
