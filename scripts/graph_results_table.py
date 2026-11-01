"""그래프 RAG 실험 결과를 평가셋별 비교 표(Markdown)로 뽑는다. 결과 파일(results_*_<태그>.json)만 읽는다.

  python scripts/graph_results_table.py reports/graph-results-20261029.md

지표 (모두 결과 파일 final["8"]에서 계산):
  찾음   nodes ∪ graph_added ∪ struct_added ∪ input_nodes ∪ onto.added 중 정답 조 (LAW: 키 제외). 예산을 보지 않은 값
  입력   input_core: 답변 단계와 같은 방식으로 글자 예산 안에 조립한 입력 중 정답 조
  전부   정답 조를 모두 찾은(또는 모두 입력에 넣은) 문항 수
  법령   law.found / law.input (법률 평가셋, tier 필수)
"""
import glob
import json
import os
import statistics
import sys

SETS = [("r3", "eval/cases_run_v3/cases_v3_20261028_r3"), ("v4", "eval/cases_run_v4/cases_v4_20261028"),
        ("법률 개발용", "eval/cases_run_law/cases_law_20261029")]
# (행 이름, 평가셋별 태그). 태그가 없는 평가셋은 표에서 "—"
ROWS = [
    ("기존 검색 + 인용 확장", {"r3": "onto_base", "v4": "onto_base", "법률 개발용": "lw_base_2"}),
    ("+ 구조 그래프 (인용·별표·위임·이웃 양방향)", {"r3": "dir_base_all", "v4": "dir_base_all"}),
    ("+ 온톨로지·위임·법령·HyDE (확정 조합, 예전 예산 조립)", {"r3": "cfgcheck", "v4": "cfgcheck", "법률 개발용": "cfgcheck"}),
    ("  예산: 관련성 순, 8,000자", {k: "bg_rel" for k, _ in SETS}),
    ("  예산: 관련성 순, 12,000자 (그래프 몫 3,000)", {k: "bg_t12k" for k, _ in SETS}),
    ("  예산: 관련성 순, 16,000자 (그래프 몫 4,000)", {k: "bg_t16k" for k, _ in SETS}),
    ("  예산: 선 종류 우선, 8,000자", {k: "kd_8k" for k, _ in SETS}),
    ("  예산: 선 종류 우선, 8,000자 (그래프 몫 3,000)", {k: "kd_8k_s3" for k, _ in SETS}),
    ("  예산: 선 종류 우선, 12,000자 (그래프 몫 3,000)", {k: "kd_12k" for k, _ in SETS}),
    ("  예산: 선 종류 우선, 16,000자 (그래프 몫 4,000)", {k: "kd_16k" for k, _ in SETS}),
]
BASE, BEST = 0, 2   # 문항별 비교: 첫 행과 확정 조합 행


def load(d, tag):
    ps = [p for p in glob.glob(os.path.join(d, f"results_*_{tag}.json")) if p.endswith(f"_{tag}.json")]
    return {x["id"]: x for x in json.load(open(ps[0], encoding="utf-8"))["questions"]} if ps else None


def found(z):
    u = set(z["nodes"]) | set(z.get("graph_added", [])) | set(z.get("struct_added", [])) | set(z["input_nodes"]) | set((z.get("onto") or {}).get("added", []))
    return {n for n in u if not n.startswith("LAW:")}


def stats(qs):
    r = {"n": len(qs), "tot": 0, "f": 0, "fa": 0, "i": 0, "ia": 0, "lt": 0, "lf": 0, "li": 0, "nf": [], "ni": [], "ch": []}
    for x in qs.values():
        z, core = x["final"]["8"], set(x["stage_nodes"]["core"])
        f = found(z)
        r["tot"] += len(core)
        r["f"] += len(core & f)
        r["fa"] += core <= f
        r["i"] += z["input_core"]
        r["ia"] += z["input_all_core"]
        r["nf"].append(len(f))
        r["ni"].append(z["input_articles"])
        r["ch"].append(z["input_chars"])
        law = z.get("law") or {}
        r["lt"] += len(law.get("gold", []))
        r["lf"] += len(law.get("found", []))
        r["li"] += len(law.get("input", []))
    return r


def main(out):
    lines = []
    for name, d in SETS:
        law = name.startswith("법률")
        head = "| 구성 | 찾음 | 찾음 전부 | **입력** | 입력 전부 | " + ("법령 찾음 | **법령 입력** | " if law else "") + "질문당 찾은 조 | 질문당 입력 조 | 입력 글자 |"
        lines += [f"### {name}", "", head, "|" + "---|" * (head.count("|") - 1)]
        for label, tags in ROWS:
            qs = load(d, tags[name]) if tags.get(name) else None
            if not qs:
                lines.append(f"| {label} | " + " | ".join(["—"] * (head.count("|") - 2)) + " |")
                continue
            s = stats(qs)
            cells = [f"{s['f']}/{s['tot']}", f"{s['fa']}/{s['n']}", f"**{s['i']}**", f"{s['ia']}/{s['n']}"]
            if law:
                cells += [f"{s['lf']}/{s['lt']}", f"**{s['li']}**/{s['lt']}"]
            cells += [f"{statistics.mean(s['nf']):.1f}", f"{statistics.mean(s['ni']):.1f}", f"{statistics.mean(s['ch']):,.0f}"]
            lines.append(f"| {label} | " + " | ".join(cells) + " |")
        a, b = load(d, ROWS[BASE][1].get(name, "")), load(d, ROWS[BEST][1].get(name, ""))
        if a and b:
            lines += ["", f"문항별 차이 ({ROWS[BASE][0]} → 확정 조합, 정답 조 기준. 찾음 / 입력):", ""]
            diff = []
            for i in b:
                core = set(b[i]["stage_nodes"]["core"])
                fa, fb = core & found(a[i]["final"]["8"]), core & found(b[i]["final"]["8"])
                ia, ib = core & set(a[i]["final"]["8"]["input_nodes"]), core & set(b[i]["final"]["8"]["input_nodes"])
                if fa != fb or ia != ib:
                    fmt = lambda plus, minus: " ".join([f"+{x}" for x in sorted(plus)] + [f"−{x}" for x in sorted(minus)]) or "같음"  # noqa: E731
                    diff.append(f"| {i} | {fmt(fb - fa, fa - fb)} | {fmt(ib - ia, ia - ib)} |")
            lines += ["| 문항 | 찾음 | 입력 |", "|---|---|---|"] + (diff or ["| (차이 없음) | | |"])
        lines.append("")
    open(out, "w", encoding="utf-8").write("\n".join(lines))
    print("저장:", out)


if __name__ == "__main__":
    main(sys.argv[1])
