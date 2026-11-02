"""후보 수집: 변경 질의(ChangeQuery)로 그래프 경로 · 문구 검색 · 의미 검색을 돌려 영향 후보를 하나의 순위 목록으로 합친다.

  collect(kg, cq, find, rag, texts) -> [Candidate]   (점수 순, 단계 · 근거 경로 포함)

설계 (리포트 72, 대화 정리)
  · 시작점은 수정 전 조항이다. 그래프와 색인이 수정 전 상태를 나타내고, 영향은 수정 전 내용에 기대던 곳이 깨지는 것이기 때문이다.
  · 수정 후의 새 말은 정의 · 위임 · 값 변경에서 보조 질의로 더한다.
  · 의미 검색(RAG)의 질의는 수정 종류별로 다르다: 삭제는 지워진 말, 정의 · 위임은 새 말 + 개정문, 값 변경은 현행 + 개정, 그 밖은 현행.
  · 후보마다 근거 경로를 남긴다. 단계는 경로별로 잰 정밀도로 나눈다: 확정급(인용받음 · 이동 후 미반영 · 용어 사용) / 확인 권장 / 참고.
  · 결과는 확인 후보이고 충돌 판정이 아니다. 후보를 지우지 않고 순서와 단계만 바꾼다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .change_query import ChangeQuery
from .kg import KG

# 경로 -> 단계 (개발 + 봉인 12문항에서 잰 후보 정밀도: 인용받음 97%, 용어 사용 88%, 제목 언급 39%, 제목 줄기 14%, 조직 이름 3%)
TIER_STRONG = {"CITES_in", "STALE_CITE", "USES_TERM", "CITES_REG:제목어"}
TIER_REVIEW = {"CO_CITE", "CITE_MENTION", "CO_MENTION", "MENTIONS_TITLE", "SAME_TITLE:값다름", "SAME_TITLE", "TERM_CORE", "PHRASE", "VALUE_DIFF", "DEF_DIFF"}
# 탐색 중 전이 규칙: 수정 종류별로 허용하는 2단계 조합 (관계 · 방향, 관계 · 방향, 점수, 경로 이름). out = 따라감, in = 거슬러감.
# 개발 분할에서 잰 2단계 조합 정밀도로 고른 것만 둔다 (번호 이동 CITES>CITES< 83%, 위임 · 법령 MENTIONS_TITLE>MENTIONS_TITLE< 26%).
TRANSITIVE = {
    "번호 이동": [("CITES", "out", "CITES", "in", 0.55, "CO_CITE"), ("CITES", "out", "MENTIONS_TITLE", "in", 0.5, "CITE_MENTION")],
    "위임·법령 변경": [("MENTIONS_TITLE", "out", "MENTIONS_TITLE", "in", 0.4, "CO_MENTION")],
}
HUB_MAX = 60
TIER_ORDER = {"확정급": 0, "확인 권장": 1, "참고": 2}   # 단계 먼저, 같은 단계 안에서 점수순
PHRASE_DF_MAX = 25        # 여러 낱말로 된 새 말
PHRASE_ONE_DF_MAX = 8     # 낱말 하나(담당자 · 연락처 같은 일반어)는 더 엄격하게
CORE_DF_MAX = 40


@dataclass
class Candidate:
    id: str
    score: float
    paths: list[str] = field(default_factory=list)
    tier: str = "참고"


def _tier(paths: list[str]) -> str:
    if any(p in TIER_STRONG for p in paths):
        return "확정급"
    if any(p in TIER_REVIEW or p.startswith("SAME_TITLE") for p in paths):
        return "확인 권장"
    return "참고"


def collect(kg: KG, cq: ChangeQuery, find: Callable[[str], list[str]], rag: Callable[[str, int], list[str]], texts: dict[str, str], rag_k: int = 40) -> list[Candidate]:
    """kg: 조항 관계 그래프. cq: 변경 분석 결과. find(말): 그 말이 든 단위 목록(띄어쓰기 무시 부분 문자열).
    rag(질의, k): 의미 검색 결과. texts: {"old": 수정 전 해당 조(항) 원문, "new": 수정 후 원문, "amend": 개정문, "title": 조 제목}."""
    if cq.kind in ("분석 실패", "표현 수정"):
        return []
    cand: dict[str, Candidate] = {}

    def add(node: str, score: float, path: str) -> None:
        if node in cq.starts or node not in kg.nodes or kg.nodes[node].get("deleted"):
            return
        c = cand.setdefault(node, Candidate(node, 0.0))
        c.score = max(c.score, score)
        if path not in c.paths:
            c.paths.append(path)

    # 1) 그래프 경로 (수정 종류에 맞는 관계). 정의 변경이면 바뀐 용어만 따라간다
    kind = cq.kind
    term_ids = [t for t in (f"TERM:{cq.reg_id}:{x}" for x in cq.terms) if t in kg.nodes] if kind == "정의 변경" else None
    starts = cq.starts or []
    if len(starts) > 1:
        res = kg.impact_many(starts, kind, term_hint=term_ids)
    elif starts:
        res = kg.impact(starts[0], kind, term_ids)
    else:
        res = {"candidates": {}, "reg_level": {}}
    for n, c in res["candidates"].items():
        for p in c["paths"]:
            add(n, c["score"], p)
    for n, c in res["reg_level"].items():
        if not n.startswith("REG:"):
            add(n, c["score"], "CITES_REG")
    # 1b) 전이 규칙: 수정 종류에 허용된 2단계 조합만
    for r1, d1, r2, d2, sc, name in TRANSITIVE.get(kind, []):
        for st in starts:
            for m in (x["dst"] if d1 == "out" else x["src"] for x in (kg.out if d1 == "out" else kg.inb).get((r1, st), [])):
                rows = (kg.out if d2 == "out" else kg.inb).get((r2, m), [])
                if len(rows) > HUB_MAX:
                    continue
                for x in rows:
                    add(x["dst"] if d2 == "out" else x["src"], sc, name)
    # 2) 용어의 핵심 부분 일치 (고객확인의무 -> 고객확인)
    if kind == "정의 변경":
        for core in cq.term_cores:
            hits = find(core)
            if 0 < len(hits) <= CORE_DF_MAX:
                for n in hits:
                    add(n, 0.75, "TERM_CORE")
    # 3) 새 말 검색 (정의 · 위임 · 값 · 그 밖 변경의 보조 질의)
    if kind in ("정의 변경", "위임·법령 변경", "값 변경", "내용 변경"):
        for ph in cq.phrases:
            if len(ph.replace(" ", "")) < 5:
                continue
            hits = find(ph)
            if 0 < len(hits) <= (PHRASE_DF_MAX if " " in ph else PHRASE_ONE_DF_MAX):
                for n in hits:
                    add(n, 0.5 + 0.1 * (" " in ph), "PHRASE")
    # 4) 의미 검색 질의: 수정 종류별
    t = texts
    if kind == "삭제":
        q = f"{t['title']}\n" + " ".join(cq.removed)
    elif kind in ("정의 변경", "위임·법령 변경"):
        q = f"{t['title']}\n" + " ".join(cq.added) + "\n" + t["amend"]
    elif kind == "값 변경":
        q = f"{t['title']}\n{t['old']}\n{t['new']}"
    else:   # 번호 이동 · 내용 변경: 수정 전 조항
        q = f"{t['title']}\n{t['old']}"
    for r, n in enumerate(rag(q, rag_k)):
        add(n, 0.2 - 0.001 * r, "RAG")
    for c in cand.values():
        c.tier = _tier(c.paths)
    return sorted(cand.values(), key=lambda c: (TIER_ORDER[c.tier], -len({p.split(":")[0] for p in c.paths}), -c.score))
