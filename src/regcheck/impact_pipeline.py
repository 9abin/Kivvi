"""영향 후보 파이프라인: 수집 -> state -> 출처별 거르기 · 순위 -> 근거 -> 답변 (리포트 93).

  out = run_pipeline(ctx, regs, reg_id, amendment, cid, rag=rag, graph=graph,
                     lv=lv, luna=luna_call(), writer=None)   # 기본 n_rag=50, n_graph=10(위임 · 언급 연결만), orders=2 (261111 val)
  out["states"]  # 후보마다 CandState (출처 · 경로 · 각 단계 결과 · 기록)
  out["rule"]    # 보이는 규칙 후보 (수집 순서 그대로)
  out["other"]   # 보이는 그래프 · RAG 후보 (luna 순서)

출처별로 할 일이 고정이다.
| 출처        | 거르기 · 순위                         | 근거                                   | 답변 (writer)                   |
| rule        | 판단 모델 점수(lv) 거르기, 순서 그대로 | 코드: 원인(경로 · 바뀐 말) + 결과(대상 조) + 원문 인용 | 원인 · 결과를 읽고 근거 문장을 쓴다 |
| graph · rag | luna 목록 리랭킹 (고른 것만 남김). 입력은 RAG가 찾은 후보 상위 50(그래프도 찾은 것 먼저) + 그래프만 찾은 후보 중 위임 · 언급 연결 상위 10 | luna가 고를 때 함께 쓴 근거 (원문 대조 통과분만)       | 검증된 luna 근거를 다듬어 쓴다      |

- 판단 모델은 별도 환경에서 돈다(scripts/decide_score_pairs.py). lv는 {후보 id: 점수} 또는 그 dict를 돌려주는 함수(입력 {id: 글}).
- luna · writer를 주지 않으면 그 단계는 건너뛰고 state에 「건너뜀」을 남긴다(후보를 숨기지 않는다).
- 판정하지 않는다. 결정은 사람이 한다.
"""

from __future__ import annotations

from typing import Callable

from .cand_state import GRAPH_ONLY_EDGES, GRAPH_PREFIXES, RAG_PATHS, CandState, build_states, by_route, found_by
from .evidence import card
from .final_view import RULE_TH, decide_inputs
from .luna_rerank import change_block, rank_list


def _has(s: CandState, names=(), prefixes=()) -> bool:
    return any(p in names or (prefixes and p.startswith(prefixes)) for p in s.paths)


def _graph_edge_ok(s: CandState, edges=GRAPH_ONLY_EDGES) -> bool:
    for p in s.paths:
        b = p.split(":")[0]
        for pre in GRAPH_PREFIXES:
            if b.startswith(pre) and b[len(pre):] in edges:
                return True
    return False


def pick_for_luna(states: list[CandState], n_rag: int | None = 50, n_graph: int | None = 10) -> list[CandState]:
    """luna에 넘길 후보 (261111 val · 최종 100 검색 단계로 정함).
    - RAG가 찾은 후보: 도메인 그래프도 함께 찾은 것을 먼저, 그 안은 수집 순서. 상위 n_rag개.
    - 도메인 그래프만 찾은 후보: 위임 · 언급 연결(GRAPH_ONLY_EDGES)로 찾은 것만, 수집 순서 상위 n_graph개.
      이름 없는 위임 · 준용(「회사의 복무 관련 규정을 준용」)은 규칙 그래프 · RAG가 못 찾는다. 다른 연결은 정답 0이라 넣지 않는다.
    넘기지 않는 후보는 「잘림」으로 숨긴다."""
    lun = sorted(by_route(states, "luna"), key=lambda s: s.order)
    rag = sorted([s for s in lun if _has(s, names=RAG_PATHS)], key=lambda s: (not _has(s, prefixes=GRAPH_PREFIXES), s.order))
    graph_only = [s for s in lun if not _has(s, names=RAG_PATHS) and _graph_edge_ok(s)]
    picked = (rag if n_rag is None else rag[:n_rag]) + (graph_only if n_graph is None else graph_only[:n_graph])
    keep = {id(s) for s in picked}
    for s in lun:
        if id(s) not in keep:
            s.rerank = {"by": "cut", "kept": False}
            if _has(s, names=RAG_PATHS):
                why = f"RAG 후보 상위 {n_rag}개 밖"
            elif _graph_edge_ok(s):
                why = f"도메인 그래프만 찾음 (위임 · 언급 연결 상위 {n_graph}개 밖)"
            else:
                why = "도메인 그래프만 찾음 (위임 · 언급 연결 아님)"
            s.log.append("리랭킹 전 잘림: " + why)
    return picked


# 파이프라인용 판단 모델 문턱. 화면(final_view, 리포트 92)의 RULE_TH 0.5와 따로 둔다.
# 261111 val: 0.5 -> 0.7에서 규칙 쪽 정답 19/20 그대로, 문항당 3.9 -> 3.3개 (최종 100: 48 -> 47, 3.3 -> 2.8개)
DECIDE_TH = 0.7


def _decide_bypass(s: CandState, kind: str) -> bool:
    """판단 모델을 거치지 않고 남기는 규칙 후보: 정의 변경에서 「정의가 바뀌는 용어를 씀」(USES_TERM).
    261111: 판단 모델이 이 후보의 정답에 0.09~0.28을 줬다(val 1 · 최종 100 3). 통과시키면 목록은 거의 그대로(6.6 · 5.1 -> 6.6 · 5.2개),
    상위 10 재현율 val 84 -> 87%, 최종 100 86 -> 90%."""
    return kind == "정의 변경" and any(p.split(":")[0] == "USES_TERM" for p in s.paths)


def apply_decide(states: list[CandState], lv: dict[str, float] | None, th: float = RULE_TH, kind: str = "") -> None:
    """규칙 후보: 확정급과 _decide_bypass는 항상 남기고, 나머지는 판단 모델 점수 th 이상만. 점수가 없으면 남긴다(리포트 92).
    남긴 후보의 순위는 수집 순서 그대로. 판단 모델 점수 순으로 놓으면 val 상위 3 재현율이 47% -> 39%로 낮아졌다(261111):
    수집 순서에 경로의 확실성(확정급 먼저)이 이미 들어 있다."""
    rank = 0
    for s in sorted(by_route(states, "decide"), key=lambda s: s.order):
        sc = None if lv is None else lv.get(s.id)
        bypass = _decide_bypass(s, kind)
        kept = s.tier == "확정급" or bypass or sc is None or sc >= th
        s.rerank = {"by": "decide_model" if sc is not None else ("tier" if s.tier == "확정급" else "none"), "score": sc, "kept": kept}
        if bypass and sc is not None and sc < th:
            s.rerank["by"] = "bypass_uses_term"
        if kept:
            s.rerank["rank"] = rank
            rank += 1
        s.log.append(f"판단 모델: 점수 {sc if sc is None else round(sc, 2)} -> {'남김' if kept else '숨김'}")


def luna_items(states: list[CandState], nodes: dict, unit_text: dict[str, str]) -> list[dict]:
    def name(i: str) -> str:
        nd = nodes.get(i, {})
        reg = nodes.get(nd.get("reg", ""), {}).get("name", "")
        return f"{reg} {nd.get('label', i)}({nd.get('title', '')})".strip()
    return [{"id": s.id, "name": name(s.id), "found_by": found_by(s.paths), "text": unit_text.get(s.id, "")} for s in states]


def apply_luna(states: list[CandState], res: dict) -> None:
    picks, order = res["picks"], {cid: k for k, cid in enumerate(res["order"])}
    for s in states:
        if s.id in order:
            p = picks[s.id]
            s.rerank = {"by": "luna", "kept": True, "rank": order[s.id], "relation": p["relation"]}
            s.evidence = {"by": "luna", "relation": p["relation"],
                          "quote": p["candidate_quote"] if p["candidate_quote_ok"] else "", "quote_verified": p["candidate_quote_ok"],
                          "source_quote": p["source_quote"] if p["source_quote_ok"] else "", "source_quote_verified": p["source_quote_ok"],
                          "reason": p["reason"]}
            s.log.append(f"luna: {order[s.id] + 1}번째로 고름 ({p['relation']}), 후보 인용 {'일치' if p['candidate_quote_ok'] else '불일치(버림)'}")
        else:
            s.rerank = {"by": "luna", "kept": False}
            s.log.append("luna: 고르지 않음")


def rule_evidence(states: list[CandState], cq, nodes: dict, unit_text: dict[str, str]) -> None:
    """규칙 후보의 원인 · 결과 · 원문 인용을 코드로 만든다 (evidence.card 재사용, 리포트 74)."""
    from .collect import Candidate

    start = [f"{nodes.get(st, {}).get('label', st)}({nodes.get(st, {}).get('title', '')})" for st in cq.starts]
    changed = ", ".join(f"{a} -> {b}" for a, b in zip(cq.old_values, cq.new_values)) or ", ".join(cq.removed + cq.added)[:200]
    for s in states:
        if s.route != "decide" or not s.rerank.get("kept"):
            continue
        cd = card(Candidate(s.id, 0.0, list(s.paths), s.tier), cq, nodes, unit_text)
        s.evidence = {"by": "code",
                      "cause": {"kind": cq.kind, "start": start, "changed": changed, "paths": found_by(s.paths)},
                      "effect": cd["target"],
                      "quote": cd["quote"] or "", "quote_verified": bool(cd["quote"]),
                      "source_quote": cd["source_quote"] or "",
                      "target_text": "" if cd["quote"] else unit_text.get(s.id, "")[:400],   # 인용이 없으면 답변 모델이 조문 앞부분을 보고 쓴다
                      "replacement": cd["replacement"]}
        s.log.append("근거: 코드 (" + ("원문 인용 있음" if cd["quote"] else "원문 인용 없음") + ")")


def final_list(states: list[CandState]) -> dict:
    rule = sorted([s for s in states if s.route == "decide" and s.rerank.get("kept")], key=lambda s: s.rerank.get("rank", s.order))
    other = sorted([s for s in states if s.route == "luna" and s.rerank.get("kept")], key=lambda s: (s.rerank.get("rank", 10**6), s.order))
    return {"rule": rule, "other": other, "hidden": len(states) - len(rule) - len(other)}


def run_pipeline(ctx, regs: dict, reg_id: str, amendment: str, cid: str, rag=None, graph=None,
                 lv: dict | Callable[[dict], dict] | None = None, luna: Callable | None = None, writer: Callable | None = None,
                 n_rag: int | None = 50, n_graph: int | None = 10, orders: int = 2, seed: int = 0, rule_th: float = DECIDE_TH) -> dict:
    from .impact_view import run

    res = run(ctx, regs, reg_id, amendment, cid, rag=rag, graph=graph)
    cq = res["cq"]
    if res["error"]:
        return {"cq": cq, "states": [], "rule": [], "other": [], "hidden": 0, "error": res["error"], "usage": []}
    states = build_states(res["view"]["all"])
    start = cq.starts[0] if cq.starts else None
    change_text = change_block(regs[reg_id], amendment, start)

    # 1. 규칙 후보: 판단 모델
    if callable(lv):
        rule_c = [_cand(s) for s in by_route(states, "decide")]
        lv = lv(decide_inputs(rule_c, cq.kind, amendment, ctx.unit_text.get(start, ""), ctx.unit_text))
    apply_decide(states, lv, rule_th, cq.kind)
    rule_evidence(states, cq, ctx.nodes, ctx.unit_text)

    # 2. 그래프 · RAG 후보: luna 목록 리랭킹 + 근거
    usage: list[dict] = []
    picked = pick_for_luna(states, n_rag, n_graph)
    if luna is not None and picked:
        out = rank_list(change_text, luna_items(picked, ctx.nodes, ctx.unit_text), luna, orders=orders, seed=seed)
        apply_luna(picked, out)
        usage = out["usage"]
    else:
        for s in picked:
            s.rerank = {"by": "skipped", "kept": True, "rank": s.order}
            s.log.append("luna 건너뜀: 검색 순서 그대로 보임")

    view = final_list(states)
    # 3. 답변
    if writer is not None:
        from .answer_writer import write_answers
        write_answers(view["rule"] + view["other"], amendment, change_text, ctx.nodes, writer)
    return {"cq": cq, "states": states, **view, "error": "", "usage": usage, "change_text": change_text}


def _cand(s: CandState):
    from .collect import Candidate
    return Candidate(s.id, 0.0, list(s.paths), s.tier)

