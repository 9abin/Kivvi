"""도메인 그래프 후보: 한석희 ontology-search의 scoped 그래프(문단 범위 제한 전파)로 후보를 더한다 (리포트 92 · 93).

  g = GraphSource.load(root)                      # ontology-search가 없으면 None
  hits = g.candidates(reg, amendment, starts)     # [(조 id, 점수 0~1, 관계)], 관계 = 마지막 간선 종류
  add_graph(cands, hits)                          # collect.Candidate 목록에 경로 GRAPH_<관계>로 합친다

- 기본 검색(impact_view.run)에는 graph 인자를 줄 때만 들어간다. 기본값은 끔.
- 그래프 후보는 state에서 source="graph"가 되어 luna가 거른다(규칙 경로가 함께 있으면 rule).
- 평가 때 쓴 scratchpad onto_swap.py와 같은 방식: 시작 조 · 바뀐 항(seed) · 바뀐 글(focus)을 넣고 scoped 순위를 그대로 점수로 바꾼다.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from .amend import AmendError, apply, parse_amendment

_compact = lambda t: re.sub(r"\s+", "", t or "")  # noqa: E731


def _label_code(label: str) -> str:
    t = _compact(label)
    a = re.fullmatch(r"제(\d+)조(?:의(\d+))?", t)
    if a:
        return a[1] + ("의" + a[2] if a[2] else "")
    b = re.fullmatch(r"(별표|별지)(?:제)?(\d+)(?:호)?(?:서식)?", t)
    if b:
        return b[1] + ("제" + b[2] + "호서식" if b[1] == "별지" else b[2])
    return t


def seeds_for(reg: dict, amendment: str, starts: set[str]) -> tuple[dict, dict]:
    """시작 조마다 바뀐 항 id 목록(seed)과 바뀐 항 글(focus). 개정문을 적용하지 못하면 빈 값."""
    seeds: dict[str, list[str]] = {}
    focus: dict[str, str] = {}
    try:
        new = apply(reg, parse_amendment(amendment))[0]
    except AmendError:
        return seeds, focus
    newl = {a["label"]: a for a in new["articles"]}
    for a in reg["articles"]:
        if a["id"] not in starts:
            continue
        b = newl.get(a["label"])
        gone = b is None or b.get("deleted")
        nb = {} if gone else {it["id"]: it.get("norm") for it in (b.get("items") or [])}
        ch = [it for it in a.get("items") or [] if not it.get("deleted") and (gone or nb.get(it["id"]) != it.get("norm"))]
        seeds[a["id"]] = [it["id"] for it in ch]
        focus[a["id"]] = "\n".join(it.get("norm") or "" for it in ch)
    return seeds, focus


class GraphSource:
    def __init__(self, graph, corpus: dict):
        self.graph, self.corpus = graph, corpus

    @classmethod
    def load(cls, root: Path) -> "GraphSource | None":
        base = Path(root) / "ontology-search"
        data = base / "data"
        if not (data / "ontology.db").exists():
            return None
        if str(base) not in sys.path:
            sys.path.insert(0, str(base))
        from ontology_search import DomainGraph, ScopedPropagatingGraph

        rj = lambda p: [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]  # noqa: E731
        corpus = {r["id"]: r for r in rj(data / "corpus.jsonl")}
        graph = ScopedPropagatingGraph(corpus, DomainGraph(data / "ontology.db"), rj(data / "references.jsonl"), _label_code, _compact)
        return cls(graph, corpus)

    def candidates(self, reg: dict, amendment: str, starts: list[str], kind: str = "") -> list[tuple[str, float, str]]:
        from ontology_search import Limits

        seeds, focus = seeds_for(reg, amendment, set(starts))
        best: dict[str, tuple[float, str]] = {}
        for st in starts:
            if st not in self.corpus:
                continue
            payload = self.graph.initial_payload(st, seeds.get(st, []), {"종류": kind, "개정문": amendment}, focus.get(st) or self.corpus[st]["text"])
            ranking, paths, _ = self.graph.rank(st, payload, {}, Limits())
            for i, aid in enumerate(ranking):
                if aid == st:
                    continue
                rel = (paths.get(aid) or [[{}]])[0][-1].get("relation", "x")
                sc = max(0.05, 1.0 - 0.01 * i)   # 순위를 점수로 (0~1)
                if aid not in best or sc > best[aid][0]:
                    best[aid] = (sc, rel)
        return sorted(((a, s, r) for a, (s, r) in best.items()), key=lambda x: -x[1])


def add_graph(cands: list, hits: list[tuple[str, float, str]], known: set[str] | None = None) -> list:
    """그래프 후보를 수집 목록 뒤에 합친다. 이미 있는 후보는 경로만 더하고 순서는 그대로 둔다."""
    from .collect import Candidate, _tier

    by_id = {c.id: c for c in cands}
    out = list(cands)
    for aid, sc, rel in hits:
        if known is not None and aid not in known:
            continue
        path = f"GRAPH_{rel}"
        c = by_id.get(aid)
        if c is None:
            c = Candidate(aid, 0.1 * sc)
            out.append(c)
            by_id[aid] = c
        if path not in c.paths:
            c.paths.append(path)
            c.tier = _tier(c.paths)
    return out
