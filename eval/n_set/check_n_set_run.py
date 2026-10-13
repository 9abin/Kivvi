import json, os, sys, collections, hashlib
HERE = os.path.dirname(os.path.abspath(__file__))
d = sys.argv[1]
mf = json.load(open(d + "/run_manifest.json", encoding="utf-8"))
rs = [json.loads(l) for l in open(d + "/runs.jsonl", encoding="utf-8")]
fz = json.load(open(os.path.join(HERE, "freeze_manifest.json"), encoding="utf-8"))
ok = True
def chk(name, cond, extra=""):
    global ok; ok &= bool(cond); print(("PASS " if cond else "FAIL ") + name, extra)
chk("160건", len(rs) == 160, len(rs))
chk("고유 run_id", len({r["run_id"] for r in rs}) == 160)
by = collections.defaultdict(dict)
for r in rs: by[r["id"]][r["arm"]] = r
chk("80문항 모두 끔/켬 쌍", len(by) == 80 and all(set(v) == {"off", "on"} for v in by.values()))
bad = [i for i, v in by.items() if (int(i[1:]) % 2 == 1) != (v["off"]["seq"] < v["on"]["seq"])]
chk("홀수 끔→켬, 짝수 켬→끔", not bad, bad[:3])
chk("쌍이 연속 실행(seq 차 1)", all(abs(v["off"]["seq"] - v["on"]["seq"]) == 1 for v in by.values()))
chk("seq 1..160 연속", sorted(r["seq"] for r in rs) == list(range(1, 161)))
diffs = collections.Counter()
for r in rs:
    base = {k: v for k, v in r["settings"].items() if k != "use_glossary"}
    diffs[json.dumps(base, sort_keys=True)] += 1
chk("끔/켬 설정 차이는 use_glossary뿐(나머지 설정 동일)", len(diffs) == 1 and all(r["settings"]["use_glossary"] == (r["arm"] == "on") for r in rs) and all(r["glossary"] == (r["arm"] == "on") for r in rs))
chk("모든 기록의 manifest_sha 동일", len({r["manifest_sha"] for r in rs}) == 1 and rs[0]["manifest_sha"] == mf["manifest_sha"])
chk("gold 해시 = 고정 manifest", mf["gold"]["sha256"] == fz["files"]["n_gold_80_v5_1.jsonl"])
chk("규칙 해시 = 고정 manifest", mf["scoring_rules"]["sha256"] == fz["files"]["scoring_rules_v5_1.md"])
need = ["code_and_data_files", "prompts", "db", "runner", "settings_common", "package_versions"]
chk("manifest 항목", all(k in mf for k in need), [k for k in need if k not in mf])
chk("DB 내용 해시 포함", all(k in mf["db"] for k in ("documents_sha256", "chunks_content_sha256", "embeddings_sha256", "index_version")))
chk("프롬프트 해시 5종", len(mf["prompts"]) >= 5, list(mf["prompts"]))
errs = [r["id"] + r["arm"] for r in rs if "error" in r]
chk("오류 기록 경로(모의 실패 1건만)", errs == ["N015on"], errs)
print("ALL PASS" if ok else "SOME FAIL")
