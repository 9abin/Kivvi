"""조합 실험 결과를 합쳐 가이드 6 · 8절의 산출물을 만든다. 결과 폴더(여러 사람 것)를 모두 넘기면 된다.

  python scripts/combo_report.py eval/combo/answer_eval_100_tiered [다른 사람 결과 폴더 ...] --out eval/combo/report

만드는 것 (--out 폴더):
  summary.csv        조합별 요약: 주지표 [구간], 부지표, 검색 합계 시간 p50 · p95, GPU · RAM 피크
  stage_time.csv     조합 × 단계(바꿔 쓰기 · 후보 · 리랭크 · 선택 · 확장) 시간 p50
  loss_breakdown.csv 필수 근거를 못 얻은 이유: 후보 밖 / 리랭크에서 k 밖 / 선택에서 버림 / 확장 후 누락 (+ 색인 없음)
  factor_effects.csv 요인별 효과: 다른 요인은 같고 한 요인만 다른 조합끼리 짝지은 차이 [구간], McNemar p
  scatter.svg        가로 시간 · 세로 주지표 산점도 (파레토 조합 표시)
  report.md          위 내용을 한 장으로, 해시가 다른 결과 경고 포함

주지표: 최종 근거 필수 Recall at k=8. 같은 설정을 반복(--tag)한 결과가 있으면 변동 크기를 함께 적고, 그보다 작은 차이는 동률로 본다.
"""
import argparse
import csv
import glob
import json
import math
import os
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")
from src.combo import metrics as M  # noqa: E402

K = "8"
FACTORS = ("E", "R", "L", "S")


def load(dirs):
    runs = []
    for d in dirs:
        for p in glob.glob(os.path.join(d, "results_*.json")):
            r = json.load(open(p, encoding="utf-8"))
            r["_path"] = p
            runs.append(r)
    return runs


def key(r):
    m = r["meta"]
    return (m["E"], m["R"], m["L"], m["S"])


def label(k):
    return f"{k[0]} / {k[1]} / {k[2]} / S{k[3]}"


def pairs_of(r):
    return {q["id"]: (q["final"][K]["core"], q["n_core"]) for q in r["questions"]}


def all_core_of(r):
    return {q["id"]: q["final"][K]["all_core"] for q in r["questions"] if q["n_core"]}


def mcnemar_p(a, b):
    """같은 질문의 이진 결과(전부 확보 여부) 두 개. 불일치 쌍만으로 정확 이항 검정(양쪽)."""
    ids = [i for i in a if i in b]
    n10 = sum(1 for i in ids if a[i] and not b[i])
    n01 = sum(1 for i in ids if b[i] and not a[i])
    n = n10 + n01
    if n == 0:
        return 1.0, n10, n01
    p = sum(math.comb(n, i) for i in range(0, min(n10, n01) + 1)) / 2 ** n * 2
    return min(1.0, p), n10, n01


def pareto(points):
    """(시간, 값) 중 더 빠르면서 더 높은 점이 없는 것."""
    out = set()
    for i, (t, v) in enumerate(points):
        if not any((t2 <= t and v2 >= v) and (t2 < t or v2 > v) for j, (t2, v2) in enumerate(points) if j != i):
            out.add(i)
    return out


def svg(rows, path):
    pts = [(r["time"], r["value"], r["label"]) for r in rows if r["time"] is not None and r["value"] is not None]
    if not pts:
        return
    W, H, L, B, T, R = 860, 520, 70, 60, 30, 30
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    x0, x1 = 0, max(xs) * 1.1 or 1
    y0, y1 = max(0, min(ys) - 0.05), min(1.0, max(ys) + 0.03)
    sx = lambda x: L + (x - x0) / (x1 - x0) * (W - L - R)  # noqa: E731
    sy = lambda y: H - B - (y - y0) / (y1 - y0) * (H - B - T)  # noqa: E731
    par = pareto([(p[0], p[1]) for p in pts])
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" font-family="sans-serif" font-size="11">',
         f'<rect width="{W}" height="{H}" fill="white"/>',
         f'<line x1="{L}" y1="{H - B}" x2="{W - R}" y2="{H - B}" stroke="#444"/><line x1="{L}" y1="{T}" x2="{L}" y2="{H - B}" stroke="#444"/>',
         f'<text x="{W / 2}" y="{H - 15}" text-anchor="middle">검색 합계 시간 p50 (초, k=8)</text>',
         f'<text x="18" y="{H / 2}" transform="rotate(-90 18 {H / 2})" text-anchor="middle">최종 근거 필수 Recall@8</text>']
    for i in range(6):
        v = y0 + (y1 - y0) * i / 5
        o.append(f'<line x1="{L}" y1="{sy(v):.1f}" x2="{W - R}" y2="{sy(v):.1f}" stroke="#eee"/><text x="{L - 6}" y="{sy(v) + 4:.1f}" text-anchor="end">{v:.2f}</text>')
        t = x0 + (x1 - x0) * i / 5
        o.append(f'<text x="{sx(t):.1f}" y="{H - B + 16}" text-anchor="middle">{t:.1f}</text>')
    for i, (t, v, lab) in enumerate(pts):
        c = "#d62728" if i in par else "#1f77b4"
        o.append(f'<circle cx="{sx(t):.1f}" cy="{sy(v):.1f}" r="{6 if i in par else 4}" fill="{c}"/>')
        o.append(f'<text x="{sx(t) + 8:.1f}" y="{sy(v) - 6:.1f}" fill="{c}">{lab}</text>')
    o.append(f'<text x="{W - R}" y="{T - 10}" text-anchor="end" fill="#d62728">빨강 = 파레토(더 빠르면서 더 높은 조합이 없음)</text></svg>')
    open(path, "w", encoding="utf-8").write("\n".join(o))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dirs", nargs="+")
    ap.add_argument("--out", default="eval/combo/report")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    runs = load(args.dirs)
    if not runs:
        sys.exit("results_*.json 을 찾지 못했습니다.")
    warns = []
    for field in ("eval_hash", "prompt_hash"):
        vals = {r["meta"].get(field) for r in runs}
        if len(vals) > 1:
            warns.append(f"{field}가 서로 다른 결과가 섞여 있습니다: {sorted(map(str, vals))}. 이런 결과끼리는 직접 비교하지 않습니다.")
    commits = {r["meta"].get("commit") for r in runs}
    if len(commits) > 1:
        warns.append(f"코드 커밋이 서로 다릅니다: {sorted(map(str, commits))}")

    by = {}
    for r in sorted(runs, key=lambda r: r["meta"]["time"]):
        by.setdefault(key(r), []).append(r)
    # 기준 결과 = 태그 없는 실행 중 가장 최근 것(같은 설정의 --stage final 재실행은 최신 결과를 쓴다).
    # 태그를 붙인 실행(--tag r2 등)은 변동 크기를 보는 반복으로만 쓴다.
    best, reps = {}, {}
    for k, v in by.items():
        main_runs = [r for r in v if not r["meta"].get("run_tag")]
        best[k] = main_runs[-1] if main_runs else v[0]
        tagged = [r for r in v if r["meta"].get("run_tag")]
        if tagged:
            reps[k] = [best[k]] + tagged

    rows, trows, lrows = [], [], []
    for k, r in sorted(best.items()):
        a, T = r["agg"], r["agg"]["time"]
        f = a["final"][f"core@{K}"]
        rows.append({"조합": label(k), "E": k[0], "R": k[1], "L": k[2], "S": k[3], "질문": a["n_questions"], "필수노드": a["n_core_nodes"],
                     "제외(색인없음)": json.dumps(a["excluded"], ensure_ascii=False), "주지표_최종필수Recall@8": round(f["value"], 4),
                     "구간": "" if not f["ci"] else f"{f['ci'][0]:.3f}~{f['ci'][1]:.3f}",
                     "전부확보": f"{a['final'][f'all_core@{K}']['hits']}/{a['final'][f'all_core@{K}']['total']}",
                     "Hit1": f"{a['rerank']['hit1_core']['hits']}/{a['rerank']['hit1_core']['total']}", "MRR": round(a["rerank"]["mrr_core"], 3),
                     "보조Recall@8": round(a["final"][f"aux@{K}"]["value"], 3) if a["final"][f"aux@{K}"]["value"] is not None else "",
                     "검색합계_p50": T[f"search_total@{K}"]["p50"], "검색합계_p95": T[f"search_total@{K}"]["p95"],
                     "GPU피크_GB": max([x for x in T["gpu_peak_gb"].values() if x], default=""),
                     "RAM피크_GB": max([x for x in T["ram_peak_gb"].values() if x], default=""),
                     "오프로드": json.dumps(T["offload"], ensure_ascii=False) if T["offload"] else "", "환경": T["env"]})
        trows.append({"조합": label(k), "바꿔쓰기": T["rewrite"]["p50"], "후보": T["cands"]["p50"], "리랭크": T["rerank"]["p50"],
                      "선택": T[f"select@{K}"]["p50"], "확장": T[f"expand@{K}"]["p50"], "검색합계": T[f"search_total@{K}"]["p50"],
                      "적재_임베딩": T["load_sec"]["embedding"], "적재_리랭커": T["load_sec"]["reranker"],
                      "LLM첫호출_바꿔쓰기": T["load_sec"]["llm_first_call_rewrite"], "LLM첫호출_선택": T["load_sec"]["llm_first_call_select"]})
        cnt = {"유지(최종 근거에 있음)": 0, "후보 밖": 0, "리랭크에서 k 밖": 0, "선택에서 버림": 0, "확장 후 누락": 0}
        for q in r["questions"]:
            sn = q["stage_nodes"]
            for node in sn["core"]:
                if sn["final8"] is not None and node in sn["final8"]:
                    cnt["유지(최종 근거에 있음)"] += 1
                elif node not in sn["cand30"]:
                    cnt["후보 밖"] += 1
                elif k[3] == 0:
                    cnt["리랭크에서 k 밖" if node not in sn["rerank8"] else "확장 후 누락"] += 1
                elif node not in sn["rerank16"]:
                    cnt["리랭크에서 k 밖"] += 1
                else:
                    cnt["선택에서 버림" if node not in (sn["sel8"] or []) else "확장 후 누락"] += 1
        lrows.append({"조합": label(k), **cnt, "색인 없음(제외)": sum(a["excluded"].values())})

    def write_csv(name, data):
        if not data:
            return
        with open(os.path.join(args.out, name), "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(data[0]))
            w.writeheader()
            w.writerows(data)

    write_csv("summary.csv", rows)
    write_csv("stage_time.csv", trows)
    write_csv("loss_breakdown.csv", lrows)

    # 요인별 효과: 한 요인만 다른 조합 쌍 (b - a), 같은 질문끼리 짝지어 부트스트랩 구간 + McNemar
    eff = []
    keys = sorted(best)
    for i, ka in enumerate(keys):
        for kb in keys[i + 1:]:
            diff = [f for f in range(4) if ka[f] != kb[f]]
            if len(diff) != 1:
                continue
            f = diff[0]
            ra, rb = best[ka], best[kb]
            ids = sorted(set(pairs_of(ra)) & set(pairs_of(rb)))
            pa, pb = [pairs_of(ra)[x] for x in ids], [pairs_of(rb)[x] for x in ids]
            d = M.paired_diff(pb, pa)
            p, n10, n01 = mcnemar_p(all_core_of(ra), all_core_of(rb))
            eff.append({"요인": FACTORS[f], "비교": f"{ka[f]} → {kb[f]}", "나머지 고정": " | ".join(str(ka[j]) for j in range(4) if j != f),
                        "차이(주지표)": round(d[0], 4) if d else "", "구간": f"{d[1]:.3f}~{d[2]:.3f}" if d else "",
                        "구간이 0을 포함": "예(동률 처리)" if d and d[1] <= 0 <= d[2] else "아니오",
                        "McNemar p(전부확보)": round(p, 3), "A만 성공": n10, "B만 성공": n01})
    # 리랭커 대신 LLM 선택: (R0 + 선택 켬) 대 (R1 + 선택 끔)
    for e in sorted({k[0] for k in keys}):
        for l in sorted({k[2] for k in keys}):
            ka, kb = (e, "none", l, 1), (e, "bge-reranker-v2-m3", l, 0)
            if ka in best and kb in best:
                ra, rb = best[ka], best[kb]
                ids = sorted(set(pairs_of(ra)) & set(pairs_of(rb)))
                d = M.paired_diff([pairs_of(ra)[x] for x in ids], [pairs_of(rb)[x] for x in ids])
                p, n10, n01 = mcnemar_p(all_core_of(ra), all_core_of(rb))
                eff.append({"요인": "LLM이 리랭커를 대신하는가", "비교": "(R0 + 선택 켬) − (R1 + 선택 끔)", "나머지 고정": f"{e} | {l}",
                            "차이(주지표)": round(d[0], 4) if d else "", "구간": f"{d[1]:.3f}~{d[2]:.3f}" if d else "",
                            "구간이 0을 포함": "예(동률 처리)" if d and d[1] <= 0 <= d[2] else "아니오", "McNemar p(전부확보)": round(p, 3),
                            "A만 성공": n10, "B만 성공": n01})
    write_csv("factor_effects.csv", eff)

    scatter = [{"time": best[k]["agg"]["time"][f"search_total@{K}"]["p50"], "value": best[k]["agg"]["final"][f"core@{K}"]["value"],
                "label": label(k)} for k in keys]
    svg(scatter, os.path.join(args.out, "scatter.svg"))
    par = pareto([(r["time"], r["value"]) for r in scatter if r["time"] is not None and r["value"] is not None])

    md = ["# 조합 실험 결과", ""]
    md += [f"> 경고: {w}" for w in warns] + ([""] if warns else [])
    md += [f"결과 {len(runs)}개, 조합 {len(best)}개. 주지표 = 최종 근거 필수 Recall@8 (95% 구간은 질문 단위 부트스트랩).", ""]
    if reps:
        md.append("## 같은 설정 반복 (변동 크기)")
        for k, v in reps.items():
            vals = [r["agg"]["final"][f"core@{K}"]["value"] for r in v]
            md.append(f"- {label(k)}: {len(v)}회, 값 {[round(x, 3) for x in vals]}, 폭 {max(vals) - min(vals):.3f} → 이보다 작은 차이는 동률로 본다")
        md.append("")
    md += ["## 조합별 요약", "", "| 조합 | 주지표 [구간] | 전부확보 | Hit@1 | 검색합계 p50 / p95 (초) | GPU 피크 | RAM 피크 |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        md.append(f"| {r['조합']} | {r['주지표_최종필수Recall@8']} [{r['구간']}] | {r['전부확보']} | {r['Hit1']} | {r['검색합계_p50']} / {r['검색합계_p95']} | {r['GPU피크_GB']} | {r['RAM피크_GB']} |")
    md += ["", "## 파레토(더 빠르면서 더 높은 조합이 없는 것)", ""]
    md += [f"- {scatter[i]['label']}" for i in sorted(par)]
    md += ["", "## 요인별 효과 (구간이 0을 포함하면 동률로 보고 현행 E1 · R1을 유지)", "", "| 요인 | 비교 | 나머지 | 차이 [구간] | 동률? |", "|---|---|---|---|---|"]
    for e in eff:
        md.append(f"| {e['요인']} | {e['비교']} | {e['나머지 고정']} | {e['차이(주지표)']} [{e['구간']}] | {e['구간이 0을 포함']} |")
    md += ["", "세부는 summary.csv, stage_time.csv, loss_breakdown.csv, factor_effects.csv, scatter.svg 참고."]
    open(os.path.join(args.out, "report.md"), "w", encoding="utf-8").write("\n".join(md))
    print(f"조합 {len(best)}개 보고서 작성: {args.out}")
    for w in warns:
        print("경고:", w)


if __name__ == "__main__":
    main()
