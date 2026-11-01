"""새 선 종류(DELEGATES_TO, SAME_REG_NEAR, 의미형)를 기준선 결과 위에 얹었을 때의 효과.

기본 = CITES 정방향 + CITES_ANNEX 양방향. 거기에 선 종류를 하나씩 더하거나 모두 더해 비교한다.
검색은 다시 돌리지 않는다. SAME_REG_NEAR는 리랭크 상위 8개 조에서만 출발한다.

  set -a; . ./.env; set +a
  python scripts/graph_kinds_eval.py <results_*.json> <emb_*.ids.json>
"""
import re
import statistics
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "scripts")
from graph_expand_eval import indexed_nodes  # noqa: E402
from src.combo import graph_expand as G  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")

def top_nodes(x, n):
    """리랭크 순위의 실제 상위 n개 노드 (stage_nodes의 rerank8은 정답만 담고 있어 시작 조로 쓰면 안 된다)."""
    out = []
    for r in x["rerank_order"]:
        m = re.match(r"(\d+)_(제\d+조(?:의\d+)?|별표\d+(?:의\d+)?)", r["cid"])
        k = f"{m[1]}:{m[2]}" if m else None
        if k and k not in out:
            out.append(k)
        if len(out) == n:
            break
    return set(out)


BASE = {"CITES": "fwd", "CITES_ANNEX": "both"}
SEM = ("SAME_MATTER", "SPECIAL_OF", "PARALLEL", "EXCEPTION_OF", "SHARED_CRITERIA")


def main(results_path, ids_path):
    import json
    idx = indexed_nodes(ids_path)
    G.KINDS = G.KINDS + SEM
    edges = G.load_edges_all() if hasattr(G, "load_edges_all") else G.load_edges()
    qs = json.load(open(results_path, encoding="utf-8"))["questions"]
    qs = list(qs.values()) if isinstance(qs, dict) else qs
    combos = {
        "기본(인용)": {},
        "+위임 정방향": {"DELEGATES_TO": "fwd"},
        "+위임 양방향": {"DELEGATES_TO": "both"},
        "+근접 조": {"SAME_REG_NEAR": "both"},
        "+의미형 전부": {k: "both" for k in SEM},
        "인용+위임(정)+근접": {"DELEGATES_TO": "fwd", "SAME_REG_NEAR": "both"},
        "전부": {"DELEGATES_TO": "both", "SAME_REG_NEAR": "both", **{k: "both" for k in SEM}},
    }
    print(f"질문 {len(qs)}개, 선 {len(edges)}개 | 전부 찾은 질문 / 정답 조 회수 / 추가 조 평균 / 최종 조 평균")
    for name, extra in combos.items():
        pol = {**BASE, **extra}
        ex = G.Expander(edges, idx, pol)
        ok = got = tot = 0
        adds, sizes = [], []
        gold_by_kind, add_by_kind = {}, {}
        for x in qs:
            core, fin = set(x["stage_nodes"]["core"]), set(x["final"]["8"]["nodes"])
            near = top_nodes(x, 8)
            det = ex.expand_detail(fin, near)
            full = fin | set(det)
            ok += core <= full
            got += len(core & full)
            tot += len(core)
            adds.append(len(det))
            sizes.append(len(full))
            for n, ks in det.items():
                for k in ks:
                    add_by_kind[k] = add_by_kind.get(k, 0) + 1
                    gold_by_kind[k] = gold_by_kind.get(k, 0) + (n in core and n not in fin)
        kinds = ", ".join(f"{k}:{gold_by_kind[k]}/{add_by_kind[k]}" for k in sorted(add_by_kind))
        print(f"{name:14} -> {ok:2}/{len(qs)} / {got}/{tot} ({got / tot:.0%}) / {statistics.mean(adds):4.1f} / {statistics.mean(sizes):4.1f}  | 새 정답/더한 수: {kinds}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
