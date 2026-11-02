"""조합 실험 결과(results_*.json) 둘을 질문·작업별로 비교한다 (평가셋 설계 리포트 66).

  python scripts/compare_runs.py <기준 results.json> <비교 results.json> [--cand @50] [--k 8,10] [--csv out.csv]

- 같은 질문 id끼리 짝짓는다. id가 "T01-Q-1"이면 작업 T01로 묶는다 (세 변형은 같은 정답을 공유).
- 층별로 필수 정답 조 수를 비교한다: 후보(--cand, 상위 N개 항 안) / 입력 / 찾음.
- 합계만이 아니라 작업별 +/− 표와 이긴·진 작업 수를 낸다. 구간은 작업 단위 부트스트랩.
- 입력 층은 입력에 든 조 중 필수 정답 비율(슬롯 정확도)과 입력 글자 수를 같이 낸다 (입력이 늘어서 오른 것인지 보려고).

두 가지 결과 형식을 읽는다 (final[k]에 input_core가 있으면 그래프 형식).
  · 조합 실험(src.combo): 입력 = final[k].core, 글자 = final[k].chars
  · 그래프 파일럿: 입력 = final[k].input_core (그래프로 넓힌 뒤의 LLM 입력), 글자 = final[k].input_chars.
    "찾음@k" 층도 낸다 = 필수 정답 조 중 기본 검색·그래프 확장·구조 확장·온톨로지 검색 어느 경로로든 후보에 든 것 (입력 예산 전의 상한).
    찾음 층은 두 결과가 모두 그래프 형식일 때만 낸다.
"""
import argparse
import csv
import json
import re
import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")
from src.combo import metrics as M  # noqa: E402


def task_of(qid):
    m = re.match(r"^(.+?)-Q-\d+$", qid)
    return m.group(1) if m else qid


def load(path):
    d = json.load(open(path, encoding="utf-8"))
    return {q["id"]: q for q in d["questions"]}


def is_graph(f):
    return "input_core" in f


def found_nodes(f):
    """그래프 형식: 어느 경로로든 후보에 든 조·별표 (법령 노드 제외)."""
    u = set(f.get("nodes") or []) | set(f.get("graph_added") or []) | set(f.get("struct_added") or []) | set(f.get("input_nodes") or [])
    u |= set((f.get("onto") or {}).get("added") or [])
    return {n for n in u if not n.startswith("LAW:")}


def layers(q, cand_key, ks):
    """질문 하나의 층별 값: 이름 -> (맞은 필수 조 수, 전체 필수 조 수, 입력 조 수, 입력 글자 수)."""
    n = q["n_core"]
    out = {f"후보{cand_key}": (q["cand"].get(cand_key, 0), n, None, None)}
    core = set((q.get("stage_nodes") or {}).get("core") or [])
    for k in ks:
        f = (q.get("final") or {}).get(str(k))
        if not f:
            out[f"입력@{k}"] = (0, n, None, None)
            continue
        if is_graph(f):
            out[f"찾음@{k}"] = (len(core & found_nodes(f)), n, None, None)
            out[f"입력@{k}"] = (f["input_core"], n, len(f["input_nodes"]), f.get("input_chars"))
        else:
            out[f"입력@{k}"] = (f["core"], n, len(f["nodes"]), f.get("chars"))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("base")
    ap.add_argument("other")
    ap.add_argument("--cand", default="@50")
    ap.add_argument("--k", default="8")
    ap.add_argument("--csv", default="")
    args = ap.parse_args()
    ks = [int(x) for x in args.k.split(",")]
    A, B = load(args.base), load(args.other)
    ids = [i for i in A if i in B]
    miss = [i for i in A if i not in B] + [i for i in B if i not in A]
    print(f"짝 {len(ids)}문항 (한쪽에만 있는 id {len(miss)}개: {miss[:5]})")
    rows = []  # 질문별
    for i in ids:
        la, lb = layers(A[i], args.cand, ks), layers(B[i], args.cand, ks)
        for name in la:
            if name not in lb:
                continue
            rows.append({"id": i, "작업": task_of(i), "층": name, "전체": la[name][1], "기준": la[name][0], "비교": lb[name][0],
                         "기준입력수": la[name][2], "비교입력수": lb[name][2], "기준글자": la[name][3], "비교글자": lb[name][3]})
    if not rows:
        sys.exit("비교할 층이 없습니다.")
    for name in dict.fromkeys(r["층"] for r in rows):
        rs = [r for r in rows if r["층"] == name]
        tasks = {}
        for r in rs:
            t = tasks.setdefault(r["작업"], [0, 0, 0])
            t[0] += r["기준"]
            t[1] += r["비교"]
            t[2] += r["전체"]
        print(f"\n== {name} ==")
        print(f"{'작업':8} {'필수':>4} {'기준':>4} {'비교':>4} {'차':>4}")
        for t, (a, b, n) in sorted(tasks.items()):
            print(f"{t:8} {n:4d} {a:4d} {b:4d} {b - a:+4d}")
        win = sum(1 for a, b, _ in tasks.values() if b > a)
        lose = sum(1 for a, b, _ in tasks.values() if b < a)
        qwin = sum(1 for r in rs if r["비교"] > r["기준"])
        qlose = sum(1 for r in rs if r["비교"] < r["기준"])
        tot_a, tot_b, tot_n = (sum(v[i] for v in tasks.values()) for i in range(3))
        pd = M.paired_diff_cluster([(b, n, a, n) for a, b, n in tasks.values()])
        print(f"합계 기준 {tot_a}/{tot_n}, 비교 {tot_b}/{tot_n} | 작업 {len(tasks)}개 중 비교 우세 {win}, 열세 {lose}, 같음 {len(tasks) - win - lose}"
              f" | 질문 우세 {qwin}, 열세 {qlose}")
        if pd:
            print(f"비교-기준 recall 차 {pd[0]:+.3f} (95% 작업 단위 부트스트랩 [{pd[1]:+.3f}, {pd[2]:+.3f}])"
                  + ("  → 구간이 0을 포함: 차이 확인 안 됨" if pd[1] <= 0 <= pd[2] else ""))
        if name.startswith("입력"):
            for lab, key, ckey in (("기준", "기준입력수", "기준글자"), ("비교", "비교입력수", "비교글자")):
                ins = sum(r[key] or 0 for r in rs)
                hit = sum(r[lab] for r in rs)
                chars = sum(r[ckey] or 0 for r in rs)
                avg = f", 질문당 평균 {chars / len(rs):,.0f}자" if chars else ""
                print(f"  {lab} 입력 조 {ins}개 중 필수 정답 {hit}개 (슬롯 정확도 {hit / ins:.3f}){avg}" if ins else f"  {lab} 입력 조 없음")
            ca, cb = (sum(r[k] or 0 for r in rs) for k in ("기준글자", "비교글자"))
            if ca and cb:
                print(f"  입력 글자 비: 비교/기준 = {cb / ca:.2f}배" + ("  ← 입력이 늘어난 만큼의 이득일 수 있음" if cb > ca * 1.1 and tot_b > tot_a else ""))
    if args.csv:
        with open(args.csv, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        print(f"\n질문별 표 저장: {args.csv}")


if __name__ == "__main__":
    main()
