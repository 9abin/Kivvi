"""최종 화면: 수집한 후보를 사람에게 보일 만큼 줄인다 (리포트 92).

  states = decide_inputs(cands, kind, amendment, start_text, unit_text)   # 판단 모델에 줄 입력 {후보 id: 글}
  lv = {후보 id: 0~3 점수}                                               # 판단 모델 환경에서 계산 (scripts/decide_score_pairs.py)
  final_view(cands, lv) -> {"rule": [...], "rag": [...], "hidden": n}

- 규칙 후보(RAG 말고 다른 경로가 하나라도 있는 후보): 확정급은 항상 보인다. 나머지는 판단 모델 점수가 RULE_TH 이상일 때만 보인다.
  순서는 수집 순서(단계 먼저 · 점수순) 그대로. 근거는 규칙 경로(원인)와 대상 조문(결과)이다.
- RAG만 찾은 후보: 의미 검색 순위와 판단 모델 순위를 반씩 섞어 정렬하고 상위 RAG_N개만 보인다.
- 점수가 없는 후보는 숨기지 않는다(판단 모델을 못 돌린 경우 수집 결과 그대로).
임계값 · 개수는 개발 세트(val 30 + 4도메인)로 미리 정한 기준에 따라 고르고 최종 100건으로 한 번 확인했다. 정답은 AI 초안이다.
판단 모델은 걸러내기와 순서에만 쓴다. 영향 여부를 판정하지 않으며 결정은 사람이 한다.
"""

from __future__ import annotations

RULE_TH = 0.5
RAG_N = 15   # 미리 정한 규칙으로 개발 세트에서 고른 값 (리포트 92)
RAG_PATHS = ("RAG", "RAG_NEW")

# 판단 모델 질문 (실험 리포트 92와 같은 문구)
INSTR = {"값 변경": "바뀌는 값(옛 값 → 새 값)을 이 조문이 같은 사안에 대해 따로 정하거나, 인용하거나, 전제하고 있는가?",
         "정의 변경": "정의가 바뀌는 용어를 이 조문이 쓰고, 바뀌는 부분에 실제로 기대고 있는가?",
         "삭제": "삭제되는 조(항)를 이 조문이 인용하거나 그 내용에 기대고 있는가?",
         "표현 수정": "바뀌는 이름이나 말을 이 조문이 그대로 쓰고 있는가?",
         "번호 이동": "번호가 바뀌는 조를 이 조문이 번호로 인용하고 있는가?",
         "위임·법령 변경": "바뀌는 위임 · 법령 내용에 이 조문이 근거하거나 이를 인용하고 있는가?",
         "내용 변경": "바뀌는 내용에 이 조문이 기대고 있거나 같은 사안을 정하고 있는가?"}
LEVEL_Q = "이 개정이 확인 대상 조문에 얼마나 닿는가?"
LEVELS = ["전혀 닿지 않는다", "같은 주제일 뿐이다", "닿을 가능성이 있다", "확실히 닿는다"]


def is_rule(c) -> bool:
    return any(p.split(":")[0] not in RAG_PATHS for p in c.paths)


def decide_state(kind: str, amendment: str, start_text: str, cand_text: str) -> str:
    gq = f"{amendment}\n{start_text[:600]}"[:700]
    return f"[개정 종류] {kind}\n[개정 내용]\n{gq}\n\n[확인 대상 조문]\n{cand_text[:700]}"


def decide_inputs(cands: list, kind: str, amendment: str, start_text: str, unit_text: dict) -> dict[str, str]:
    """판단 모델이 점수를 매길 후보: 확정급이 아닌 규칙 후보 전부 + RAG만 찾은 후보 전부."""
    return {c.id: decide_state(kind, amendment, start_text, unit_text.get(c.id, ""))
            for c in cands if not (is_rule(c) and c.tier == "확정급")}


def final_view(cands: list, lv: dict[str, float], rule_th: float = RULE_TH, rag_n: int = RAG_N) -> dict:
    rule = [c for c in cands if is_rule(c) and (c.tier == "확정급" or lv.get(c.id, rule_th) >= rule_th)]
    rag_only = [c for c in cands if not is_rule(c)]   # cands 순서 = 의미 검색 순위
    pos = {c.id: i for i, c in enumerate(rag_only)}
    rk = {c.id: i for i, c in enumerate(sorted(rag_only, key=lambda c: (-lv.get(c.id, 0.0), pos[c.id])))}
    rag = sorted(rag_only, key=lambda c: (0.5 * pos[c.id] + 0.5 * rk[c.id], pos[c.id]))[:rag_n]
    return {"rule": rule, "rag": rag, "hidden": len(cands) - len(rule) - len(rag)}
