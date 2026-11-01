"""그래프 확장 점검: 기준선 결과의 최종 조에서 인용 선을 따라가면 정답 조를 얼마나 더 찾고 후보가 얼마나 늘어나는가.

검색은 다시 돌리지 않고 기준선 결과 JSON만 읽는다. 선 종류별 방향 조합(4 x 4 = 16가지)을 전부 표로 낸다.
최종 조 목록은 규정·조 번호순이라 순위가 없으므로 시작 조를 자르지 않고 전부 쓴다.

  set -a; . ./.env; set +a
  python scripts/graph_expand_eval.py <results_*.json> <emb_*.ids.json>
"""
import itertools
import json
import re
import statistics
import sys

sys.path.insert(0, ".")
from src.combo.graph_expand import Expander, load_edges  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")


def indexed_nodes(ids_path):
    """색인에 있는 노드. 청크 ID 23492_제3조_1항 -> 23492:제3조."""
    out = set()
    for cid in json.load(open(ids_path, encoding="utf-8")):
        m = re.match(r"(\d+)_(제\d+조(?:의\d+)?|별표\d+(?:의\d+)?)", cid)
        if m:
            out.add(f"{m[1]}:{m[2]}")
    return out


def main(results_path, ids_path):
    idx, edges = indexed_nodes(ids_path), load_edges()
    qs = json.load(open(results_path, encoding="utf-8"))["questions"]
    qs = list(qs.values()) if isinstance(qs, dict) else qs
    print(f"선 {len(edges)}개, 색인 노드 {len(idx)}개, 질문 {len(qs)}개")
    print("CITES(조->조) / CITES_ANNEX(조->별표) 방향 -> 전부 찾은 질문 / 정답 조 회수 / 추가 조 평균(최대) / 최종 조 평균")
    for pc, pa in itertools.product(("off", "fwd", "bwd", "both"), repeat=2):
        ex = Expander(edges, idx, {"CITES": pc, "CITES_ANNEX": pa})
        ok = got = tot = 0
        adds, sizes = [], []
        for x in qs:
            core, fin = set(x["stage_nodes"]["core"]), set(x["final"]["8"]["nodes"])
            add = ex.expand(fin)
            full = fin | add
            ok += core <= full
            got += len(core & full)
            tot += len(core)
            adds.append(len(add))
            sizes.append(len(full))
        print(f"{pc:4} / {pa:4} -> {ok:2}/{len(qs)} / {got}/{tot} ({got / tot:.0%}) / {statistics.mean(adds):4.1f} ({max(adds):2}) / {statistics.mean(sizes):4.1f}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
