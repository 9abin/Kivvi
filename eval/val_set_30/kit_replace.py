"""분포 보정: 라벨 결과 분포가 목표 모양에서 크게 어긋나 일부 문항의 개정 조를 다시 뽑는다.
kit_sample.py 의 후보·제외·종류 조건을 그대로 쓰고(import 대신 exec), 이미 쓴 조와 1차에서 교체된 조도 뺀다. 규정별 최대 2건은 최종 30건 기준."""
import json, collections, random, sys, runpy
sys.stdout.reconfigure(encoding="utf-8")
SEED = 20261111
sys.argv = ["kit_sample.py", "1"]
import io, contextlib
with contextlib.redirect_stdout(io.StringIO()):
    g = runpy.run_path("kit_sample.py")   # pool, ok 만 쓰고 sample.json 은 아래에서 복원한다
R1 = json.load(open("sample_round1.json", encoding="utf-8"))
json.dump(R1, open("sample.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
pool, ok = g["pool"], g["ok"]
items = R1["items"]
# 교체할 슬롯 -> (새 종류, 도메인 여부)
REPL = {"V15": ("번호 이동", True), "V24": ("정의 변경", True),
        "V08": ("정의 변경", False), "V12": ("번호 이동", False), "V05": ("삭제", False),
        "V27": ("삭제", False), "V03": ("위임·법령 변경", False), "V25": ("위임·법령 변경", False)}
random.seed(SEED)
keep = [o for o in items if o["id"] not in REPL]
taken = {o["aid"] for o in items}
per_reg = collections.Counter(o["reg_id"] for o in keep)
out = {o["id"]: o for o in items}
dropped = {}
for sid, (kind, is_dom) in REPL.items():
    cand = [p for p in pool if p["domain"] == is_dom and p["aid"] not in taken and ok(kind, p) and per_reg[p["reg_id"]] < 2]
    p = random.choice(cand)
    taken.add(p["aid"]); per_reg[p["reg_id"]] += 1
    dropped[sid] = {"aid": out[sid]["aid"], "reg": out[sid]["reg"], "label": out[sid]["label"], "종류": out[sid]["종류"]}
    out[sid] = {"종류": kind, **p, "id": sid, "분할": "개발"}
    print(sid, "D" if is_dom else "-", kind, p["reg"], p["label"], p["title"])
res = {"seed": R1["seed"], "replace_seed": SEED, "items": [out[k] for k in sorted(out)]}
json.dump(res, open("sample.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
json.dump(dropped, open("replaced_round1.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("도메인", sum(o["domain"] for o in res["items"]), "| 규정 수", len({o["reg_id"] for o in res["items"]}), "| 종류", dict(collections.Counter(o["종류"] for o in res["items"])))
