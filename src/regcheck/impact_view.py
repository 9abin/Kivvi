"""검토 화면용 영향 후보: 개정문 -> 변경 분석 -> 후보 수집 -> 기본 화면 / 전체 후보 -> 근거 카드 (리포트 74 · 78).

  ctx = load_context(root, regs)                 # out/kg가 없으면 None
  res = run(ctx, regs, reg_id, amendment, cid, rag=None)
  res["cq"], res["view"]["default" | "all" | "hidden"], res["cards"], res["error"]

화면(app/review_app.py)이 부르는 얇은 층이다. 후보를 확정하거나 충돌을 판정하지 않는다. 결정은 사람이 한다.
의미 검색(rag)은 선택: 없으면 그래프 · 문구 후보만 나온다. 부칙 · 별표 · 서식의 임베딩은 쓰지 않는다(본문 조 청크만).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .amend import AmendError, apply, parse_amendment
from .change_query import _art_text, analyze
from .collect import collect
from .evidence import card
from .kg import KG
from .review_list import split_view


@dataclass
class ImpactContext:
    nodes: dict
    kg: KG
    unit_text: dict[str, str]
    ntext: dict[str, str]

    @classmethod
    def from_parts(cls, regs: dict, nodes: dict, edge_rows: list[dict]) -> "ImpactContext":
        unit_text: dict[str, str] = {}
        for r in regs.values():
            for x in r["articles"]:
                if not x.get("deleted"):
                    unit_text[x["id"]] = x["norm"]
            for ad in r.get("addenda") or []:
                for x in ad.get("articles") or []:
                    unit_text[x["id"]] = x["norm"]
            for ap in r.get("appendices") or []:
                unit_text[ap["id"]] = (ap.get("title") or "") + "\n" + (ap.get("text") or "")
        ntext = {k: re.sub(r"\s+", "", v) for k, v in unit_text.items()}
        return cls(nodes, KG(nodes, edge_rows), unit_text, ntext)

    def find(self, term: str) -> list[str]:
        t = re.sub(r"\s+", "", term)
        return [k for k, v in self.ntext.items() if t in v]


def _jl(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def load_context(root: Path, regs: dict) -> ImpactContext | None:
    nodes_p, edges_p = root / "out" / "kg" / "nodes.jsonl", root / "out" / "kg" / "edges.jsonl"
    if not (nodes_p.exists() and edges_p.exists()):
        return None
    nodes = {r["id"]: {k: v for k, v in r.items() if k != "id"} for r in _jl(nodes_p)}
    return ImpactContext.from_parts(regs, nodes, _jl(edges_p))


def make_rag(model, index) -> Callable[[str, int], list[str]]:
    """본문 조 청크 임베딩(regcheck.vectors.Index)으로 의미 검색. 조 단위로 중복을 없앤다."""
    def rag(q: str, k: int) -> list[str]:
        qvec = model.encode([q[:1200]], normalize_embeddings=True)[0]
        seen, out = set(), []
        for _, chunk in index.search(qvec, None, k=max(k * 4, 40)):
            a = chunk["article_id"]
            if a not in seen:
                seen.add(a)
                out.append(a)
            if len(out) >= k:
                break
        return out
    return rag


def _clause_query(old_a, texts: dict, amendment: str) -> str:
    """개정문의 따옴표 안 옛 문구가 들어 있는 항(없으면 조 원문)에 개정문을 이어 붙인 글."""
    q = re.findall(r'"([^"]+)"', amendment)
    old = re.sub(r"\s+", "", q[0]) if q else ""
    base = texts["old"]
    if old and old_a:
        hit = [it["text"] for it in (old_a.get("items") or []) if old in re.sub(r"\s+", "", it.get("text", ""))]
        if hit:
            base = min(hit, key=len)
    return f"{base} {amendment}"[:1500]


def _fuse_clause(all_cands: list, rag_fn, old_a, texts: dict, amendment: str, w: float, k: int = 50) -> list:
    """기존 후보 순서와 항 질의 의미 검색 순위를 순위 합치기(RRF)로 섞는다. 새로 들어오는 후보의 경로 이름은 기존 의미 검색과 같은 RAG."""
    from .collect import Candidate, _tier
    lane = rag_fn(_clause_query(old_a, texts, amendment), k)
    by_id = {c.id: c for c in all_cands}
    sc = {c.id: 1 / (60 + r) for r, c in enumerate(all_cands)}
    for r, a in enumerate(lane):
        sc[a] = sc.get(a, 0.0) + w / (60 + r)
        if a not in by_id:
            c = Candidate(a, 0.2 - 0.001 * r)
            c.paths.append("RAG")
            c.tier = _tier(c.paths)
            by_id[a] = c
    return [by_id[a] for a in sorted(sc, key=lambda a: -sc[a])]


def run(ctx: ImpactContext, regs: dict, reg_id: str, amendment: str, cid: str, rag: Callable[[str, int], list[str]] | None = None,
        rag_k: int = 30, graph_n: int = 20, leave=False, tier_open: bool = True) -> dict:
    cq = analyze(regs[reg_id], amendment, cid)
    if cq.kind == "분석 실패":
        return {"cq": cq, "view": {"default": [], "all": [], "hidden": 0}, "cards": [], "error": cq.error or "개정문을 읽지 못함"}
    start = cq.starts[0] if cq.starts else None
    old_a = next((x for x in regs[reg_id]["articles"] if x["id"] == start), None)
    try:
        new_reg = apply(regs[reg_id], parse_amendment(amendment))[0]
    except AmendError:   # 별지 서식 · 별표를 고치는 개정문은 조 단위 적용기가 못 읽는다: 대상 본문은 서식 본문을 쓴다
        new_reg = regs[reg_id]
    new_a = next((x for x in new_reg["articles"] if old_a and x["label"] == old_a["label"]), old_a)
    appx = "" if old_a else ctx.unit_text.get(start, "")[:600]
    texts = {"title": (old_a or {}).get("title") or "", "old": _art_text(old_a)[:600] if old_a else appx, "new": _art_text(new_a)[:600] if new_a else appx, "amend": amendment}
    exclude = set() if cq.kind == "번호 이동" else set(cq.starts)

    def rag_fn(q: str, k: int) -> list[str]:
        return [a for a in (rag(q, k + len(exclude)) if rag else []) if a not in exclude][:k]

    layer = leave   # 주제 층(TopicLayer) 또는 목록. True면 휴직 층 (이전 호출과 호환)
    if leave is True:
        from .leave_layer import LeaveLayer
        layer = LeaveLayer(ctx.unit_text)
    elif not leave:
        layer = None
    cands = collect(ctx.kg, cq, ctx.find, rag_fn, texts, rag_k=rag_k, leave=layer)
    view = split_view(cands, rag_k=rag_k, graph_n=graph_n, tier_open=tier_open)
    import os as _os
    _w = float(_os.environ.get("CLAUSE_RRF") or 0)   # 시험용: 개정된 항 질의 의미 검색을 순위 합치기로 더한다 (기본 꺼짐)
    if _w > 0 and rag:
        view["all"] = _fuse_clause(view["all"], rag_fn, old_a, texts, amendment, _w)
    cards = [card(c, cq, ctx.nodes, ctx.unit_text) for c in view["default"]]
    return {"cq": cq, "view": view, "cards": cards, "error": ""}
