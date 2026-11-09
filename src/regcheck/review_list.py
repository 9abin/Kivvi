"""검토 목록 나누기: 후보 수집 결과를 기본 화면과 전체 후보로 가른다 (리포트 78 부록).

  split_view(cands, rag_k=30, graph_n=20) -> {"default": [...], "all": [...], "hidden": n}

  기본 화면 = 의미 검색 상위 rag_k개 + RAG가 못 가져온 그래프 후보 중 「쓸모 있는 경로」를 가진 것 상위 graph_n개
  tier_open=True면 확정급 전부와 소음 경로가 아닌 경로를 가진 확인 권장 전부를 개수 상한 없이 더 펼친다 (정답 수가 문항마다 다르므로 단계로 노출을 정한다).
  전체     = 후보 수집 결과 전부 (소음 경로 후보 포함). 숨기는 것이 아니라 접어 둔 것이며 개수를 표시한다.
쓸모 없는 경로는 4개 평가 묶음(개발 · 1차 봉인 · 2차 · 3차)에서 RAG30이 못 가져온 그래프 후보의 정답률이 5% 안팎이던 다섯 경로다.
같은 경로가 묶음마다 남기도 하고 빠지기도 했으므로 경로 목록은 잠정이다 (표본이 작음).
"""

from __future__ import annotations

from .collect import Candidate

RAG_PATHS = ("RAG", "RAG_NEW")
NOISE_PATHS = ("CITE_MENTION", "CITES_REG", "CO_MENTION", "MENTIONS_ORG", "PHRASE", "HOP2", "HOP3")   # HOP: 고재현 모드(다홉 확장)는 기본 화면 밖


def _graph_paths(c: Candidate) -> set[str]:
    return {p.split(":")[0] for p in c.paths if p.split(":")[0] not in RAG_PATHS}


def split_view(cands: list[Candidate], rag_k: int = 30, graph_n: int = 20, tier_open: bool = False) -> dict:
    rag = sorted((c for c in cands if any(p in RAG_PATHS for p in c.paths)), key=lambda c: -c.score)[:rag_k]
    rag_ids = {c.id for c in rag}
    graph = [c for c in cands if c.id not in rag_ids and (_graph_paths(c) - set(NOISE_PATHS))][:graph_n]   # cands는 단계 먼저 · 점수순
    keep = rag_ids | {c.id for c in graph}
    if tier_open:   # 개수 상한과 무관하게 확정급 전부 + 쓸모 있는 경로를 가진 확인 권장 전부를 펼친다 (참고 · 소음 경로뿐인 후보만 상한으로 접는다)
        keep |= {c.id for c in cands if c.tier == "확정급" or (c.tier == "확인 권장" and (_graph_paths(c) - set(NOISE_PATHS)))}
    default = [c for c in cands if c.id in keep]   # 원래 순서(단계 먼저 · 점수순)를 지킨다
    return {"default": default, "all": list(cands), "hidden": len(cands) - len(default)}
