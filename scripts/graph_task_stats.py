"""두 실험 결과를 작업(주제) 단위로 비교한다: 작업마다 이긴·진·같은 수와 부호 검정.

같은 작업의 세 변형(Q-1~Q-3)은 정답 조가 같아 서로 독립이 아니다. 정답 조 개수를 문항마다 더하면 이득이 부풀어 보이므로,
작업마다 세 문항의 정답 조 수를 합쳐 한 번의 승패로 센다.

  python scripts/graph_task_stats.py <결과 폴더> <기준 태그> <비교 태그> [<비교 태그> ...] [--metric input|found]
  예: python scripts/graph_task_stats.py eval/cases_run_v3/cases_v3_20261028_r3 dir_base_all cfgcheck bg_t16k
"""
import glob
import json
import math
import os
import sys

sys.path.insert(0, "scripts")
sys.stdout.reconfigure(encoding="utf-8")
from graph_results_table import found  # noqa: E402


def load(d, tag):
    ps = [p for p in glob.glob(os.path.join(d, f"results_*_{tag}.json")) if p.endswith(f"_{tag}.json")]
    if not ps:
        raise SystemExit(f"결과 없음: {d} {tag}")
    return {x["id"]: x for x in json.load(open(ps[0], encoding="utf-8"))["questions"]}


def score(x, metric):
    z, core = x["final"]["8"], set(x["stage_nodes"]["core"])
    return z["input_core"] if metric == "input" else len(core & found(z))


def sign_test(wins, losses):
    """두쪽 부호 검정 p값 (같은 것은 뺀다)."""
    n = wins + losses
    if n == 0:
        return 1.0
    k = min(wins, losses)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)


def task_of(qid):
    return qid.rsplit("-Q", 1)[0]


def main(d, base, others, metric):
    a = load(d, base)
    for tag in others:
        b = load(d, tag)
        tasks = {}
        for i in a:
            t = task_of(i)
            sa, sb = tasks.get(t, (0, 0))
            tasks[t] = (sa + score(a[i], metric), sb + score(b[i], metric))
        w = sum(1 for x, y in tasks.values() if y > x)
        l_ = sum(1 for x, y in tasks.values() if y < x)
        tie = len(tasks) - w - l_
        detail = ", ".join(f"{t} {y - x:+d}" for t, (x, y) in sorted(tasks.items()) if y != x)
        print(f"{os.path.basename(d)} {metric} {base} -> {tag}: 작업 {len(tasks)}개 중 {w}승 {l_}패 {tie}무, "
              f"부호 검정 p={sign_test(w, l_):.3f} | {detail or '차이 없음'}")


if __name__ == "__main__":
    args = [x for x in sys.argv[1:] if not x.startswith("--metric")]
    m = "found" if "--metric=found" in sys.argv or ("--metric" in sys.argv and "found" in sys.argv) else "input"
    args = [x for x in args if x not in ("found", "input")]
    main(args[0], args[1], args[2:], m)
