"""Offline validation of this folder; write evidence to validation/."""
import ast
import collections
import hashlib
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

from ontology_search import DomainGraph, ScopedPropagatingGraph
from ontology_search.propagating_graph import walk_events
from run import compact, label_code, read_jsonl

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "validation"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    OUT.mkdir(exist_ok=True)
    report = {"date": "2026-11-10", "python": sys.version, "checks": {}, "diagnostics": {}}
    checks = report["checks"]
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    checks["manifest_hashes"] = all(sha(ROOT / entry["destination"]) == entry["packaged_sha256"] for entry in manifest["files"])
    checks["additional_manifest_hashes"] = all(sha(ROOT / entry["path"]) == entry["sha256"] for entry in manifest["additional_files"])
    code = [p for p in ROOT.rglob("*.py") if ".verify-" not in str(p)]
    for path in code:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    checks["python_syntax"] = True
    report["python_files_checked"] = len(code)
    rows = read_jsonl(ROOT / "data/corpus.jsonl")
    corpus = {r["id"]: r for r in rows}
    checks["unique_article_ids"] = len(corpus) == len(rows)
    item_ids = [i["logical_id"] for r in rows for i in r["items"]]
    checks["unique_item_ids"] = len(item_ids) == len(set(item_ids))
    item_counts = collections.Counter(item_ids)
    duplicates = {key: value for key, value in item_counts.items() if value > 1}
    report["duplicate_items"] = {"ids": duplicates, "distinct_duplicated_ids": len(duplicates), "extra_rows": sum(n - 1 for n in duplicates.values())}
    con = sqlite3.connect((ROOT / "data/ontology.db").as_uri() + "?mode=ro", uri=True)
    checks["sqlite_integrity"] = con.execute("pragma integrity_check").fetchall() == [("ok",)]
    checks["sqlite_foreign_keys"] = not con.execute("pragma foreign_key_check").fetchall()
    report["data"] = {"articles": len(rows), "items": len(item_ids), "regulations": len({r["reg_id"] for r in rows})}
    schema = DomainGraph()
    report["graph"] = schema.stats
    by_item = {i["logical_id"]: i for r in rows for i in r["items"]}
    binding_counts = collections.Counter()
    for logical, text in con.execute("select b.logical_item_id,i.text from source_binding b join item i on i.id=b.item_id"):
        item = by_item.get(logical)
        if item is None:
            binding_counts["missing_item_id"] += 1
        elif compact(item["text"]) == compact(text):
            binding_counts["equal_text_after_whitespace_removal"] += 1
        else:
            binding_counts["different_text_after_whitespace_removal"] += 1
    con.close()
    report["binding_audit"] = dict(binding_counts)
    references = read_jsonl(ROOT / "data/references.jsonl")
    reference_counts = collections.Counter()
    for row in references:
        source = row["고유번호"] + ":" + label_code(row["조항"])
        for cite in row["cites"]:
            reference_counts["total"] += 1
            rid, label = cite["대상"].split("_", 1)
            target = rid + ":" + label_code(label)
            if source not in corpus:
                reference_counts["missing_source"] += 1
            if target not in corpus:
                reference_counts["missing_target"] += 1
            if source in corpus and target in corpus and source != target:
                reference_counts["resolvable_nonself"] += 1
            quote = compact(cite.get("원문", ""))
            if source in corpus and quote and quote in compact(corpus[source]["text"]):
                reference_counts["quote_found_in_source"] += 1
            else:
                reference_counts["quote_not_verified"] += 1
    report["citation_audit"] = dict(reference_counts)

    with tempfile.TemporaryDirectory(prefix=".verify-", dir=ROOT) as folder:
        env = dict(os.environ, TEMP=folder, TMP=folder, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        env.pop("PYTHONPATH", None)
        tests = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", ".", "-v"], cwd=ROOT, env=env, capture_output=True, encoding="utf-8", timeout=30)
        (OUT / "unittest.log").write_text(tests.stdout + tests.stderr, encoding="utf-8")
        checks["regression_suite"] = tests.returncode == 0
        def cli(*args):
            return subprocess.run([sys.executable, str(ROOT / "run.py"), *args], cwd=folder, env=env, capture_output=True, encoding="utf-8", timeout=30)

        examples = {}
        for name, args in {
            "domain": ["--anchor", "24024:30", "--limit", "3"],
            "scoped": ["--anchor", "24024:30", "--method", "scoped", "--amendment", '제30조제1항 중 "만 8세 이하"를 "만 9세 이하"로 한다.', "--limit", "3"],
            "scoped_seed": ["--anchor", "24024:30", "--seed", "24024:30-1-0-0", "--method", "scoped", "--amendment", '제30조제1항 중 "만 8세 이하"를 "만 9세 이하"로 한다.', "--limit", "3"],
        }.items():
            first, second = cli(*args), cli(*args)
            checks[name + "_cli"] = first.returncode == 0
            checks[name + "_deterministic"] = first.returncode == second.returncode == 0 and first.stdout == second.stdout
            if first.returncode == 0:
                result = json.loads(first.stdout)
                ids = [r["id"] for r in result["results"]]
                checks[name + "_result_shape"] = len(ids) <= 3 and len(ids) == len(set(ids)) and "24024:30" not in ids and all(i in corpus for i in ids)
                examples[name] = result
        report["examples"] = {name: {"candidate_count": r["candidate_count"], "first_ids": [x["id"] for x in r["results"]]} for name, r in examples.items()}
        (OUT / "examples.json").write_text(json.dumps(examples, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for name, args in {
            "missing_anchor": [],
            "unknown_anchor": ["--anchor", "NOT_FOUND"],
            "unknown_seed": ["--anchor", "24024:30", "--seed", "NOT_FOUND"],
            "zero_limit": ["--anchor", "24024:30", "--limit", "0"],
            "missing_amendment": ["--anchor", "24024:30", "--method", "scoped"],
        }.items():
            result = cli(*args)
            checks["reject_" + name] = result.returncode == 2 and "Traceback" not in result.stderr
        if importlib.util.find_spec("numpy"):
            result = cli("--anchor", "24024:30", "--lexical", "--limit", "3")
            checks["lexical_cli"] = result.returncode == 0
            report["lexical_execution"] = "executed"
        else:
            result = cli("--anchor", "24024:30", "--lexical")
            checks["missing_numpy_message"] = result.returncode == 2 and "numpy" in result.stderr and "Traceback" not in result.stderr
            report["lexical_execution"] = "not executed: numpy absent; dependency error handling checked"

    # Diagnostics reproduce existing behavior; they do not declare it correct.
    ranking, _ = schema.rank(corpus, [corpus["10770:4"]], "", {}, ["10770:4-1-0-0"])
    report["diagnostics"]["explicit_seed_fallback"] = {"anchor": "10770:4", "seed": "10770:4-1-0-0", "direct_active_edges": sum(e["active"] and e["logical_item_id"] == "10770:4-1-0-0" for e in schema.edges), "candidate_count": len(ranking)}
    def edge(target, gate, weight=1):
        return dict(target=target, kind="fixture", weight=weight, impact=gate, reason="diagnostic")
    adjacency = {"root": [edge("A", True, 2), edge("B", True)], "A": [edge("X", False)], "B": [edge("X", True)], "X": [edge("D", True)]}
    scores, _, _ = walk_events("root", {}, lambda n, p, d: adjacency.get(n, []))
    report["diagnostics"]["terminal_before_expandable"] = {"candidate_ids": sorted(scores), "downstream_D_missing": "D" not in scores}
    graph = ScopedPropagatingGraph(corpus, schema, references, label_code, compact)
    amendment = {"종류": "값 변경", "개정문": '제30조제1항 중 "만 8세 이하"를 "만 9세 이하"로 한다.'}
    focus = corpus["24024:30"]["text"]
    payload = graph.initial_payload("24024:30", [], amendment, focus)
    seeded = graph.initial_payload("24024:30", ["24024:30-1-0-0"], amendment, focus)
    report["diagnostics"]["scoped_without_seed"] = {"concepts_without_seed": payload["concepts"], "concepts_with_seed": seeded["concepts"]}
    report["data_hashes"] = {p.name: sha(p) for p in sorted((ROOT / "data").iterdir()) if p.is_file()}
    checks["data_unchanged_after_execution"] = all(sha(ROOT / e["destination"]) == e["packaged_sha256"] for e in manifest["files"] if e["destination"].startswith("data/"))
    report["passed"] = sum(checks.values())
    report["failed"] = len(checks) - report["passed"]
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
