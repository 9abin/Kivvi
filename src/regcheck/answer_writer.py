"""최종 답변: 보이는 후보마다 「왜 열어 봐야 하는지」를 LLM이 2~3문장으로 쓴다 (리포트 93).

  write_answers(states, amendment, change_text, nodes, writer)   # writer(prompt) -> 문장
  writer = make_writer("gpt-5.6-luna" 또는 로컬 모델 이름)

출처별로 입력이 다르다 (state.evidence.by).
- code (규칙 후보): 원인(수정 종류 · 고쳐지는 조 · 바뀌는 말 · 찾은 경로)과 결과(대상 조 · 원문 인용)를 읽고 근거를 쓴다.
- luna (그래프 · RAG): luna가 고를 때 남긴 근거 중 원문 대조를 통과한 구절과 이유만 다듬는다. 새 사실을 더하지 않는다.
검사(explain.valid): 200자 안팎 · 단정 표현 없음 · 입력에 없는 조 번호 · 숫자 없음. 통과 못 하면 answer.text를 비우고 화면은 근거 칸을 그대로 쓴다.
판정하지 않는다. 결정은 사람이 한다.
"""

from __future__ import annotations

from typing import Callable

from .explain import valid

COMMON_RULES = """규칙
- 위 내용에 있는 낱말과 숫자만 쓴다. 조 번호·숫자를 새로 만들지 않는다.
- 판정하지 않는다. 위법·반드시·문제가 있다 같은 단정은 쓰지 않는다. 「~를 확인해 볼 만하다」 정도로 쓴다.
- 법적 판단을 하지 않는다. 한국어 2~3문장, 200자 이내. 문장만 쓰고 머리말·목록·따옴표 장식은 쓰지 않는다."""

RULE_PROMPT = """내부규정 개정 때문에 확인 대상으로 걸린 조문이 있다. 담당자가 이 조문을 왜 열어 봐야 하는지 쓴다.

[개정]
{change}

[원인] 수정 종류: {kind} / 고쳐지는 조: {start} / 바뀌는 말: {changed}
[찾은 경로] {paths}
[결과] 확인 대상: {effect}
[확인 대상의 해당 문장] {quote}

원인(무엇이 바뀌나)에서 결과(확인 대상이 그것과 어떻게 이어지나) 순서로 쓴다. 해당 문장이 「(없음)」이면 찾은 경로를 근거로 쓰고, 문장이 없다는 말은 쓰지 않는다.
""" + COMMON_RULES

OTHER_PROMPT = """내부규정 개정 때문에 확인 대상으로 걸린 조문이 있다. 아래는 검토 보조 모델이 이 조문을 고르며 남긴 근거다. 담당자가 읽기 쉽게 다듬는다. 새 사실을 더하지 않는다.

[개정]
{change}

[확인 대상] {effect}
[관계] {relation}
[개정 쪽 구절] {source_quote}
[확인 대상 구절] {quote}
[이유] {reason}
[찾은 경로] {paths}

원인(개정에서 무엇이 바뀌나)에서 결과(확인 대상의 무엇이 영향을 받나) 순서로 쓴다. 「(없음)」인 칸은 언급하지 않는다.
""" + COMMON_RULES


def _effect(s, nodes: dict) -> str:
    nd = nodes.get(s.id, {})
    reg = nodes.get(nd.get("reg", ""), {}).get("name", "")
    return f"{reg} {nd.get('label', s.id)}({nd.get('title', '')})".strip()


def prompt_for(s, amendment: str, change_text: str, nodes: dict) -> str:
    ev = s.evidence or {}
    from .cand_state import found_by
    change = change_text[:900] or amendment[:900]
    if ev.get("by") == "code":
        c = ev.get("cause", {})
        return RULE_PROMPT.format(change=change, kind=c.get("kind", ""), start=", ".join(c.get("start") or []) or "(없음)",
                                  changed=c.get("changed") or "(없음)", paths=", ".join(c.get("paths") or found_by(s.paths)),
                                  effect=ev.get("effect") or _effect(s, nodes), quote=(ev.get("quote") or "(없음)")[:400])
    return OTHER_PROMPT.format(change=change, effect=_effect(s, nodes), relation=ev.get("relation") or "(없음)",
                               source_quote=(ev.get("source_quote") or "(없음)")[:300], quote=(ev.get("quote") or "(없음)")[:400],
                               reason=ev.get("reason") or "(없음)", paths=", ".join(found_by(s.paths)))


def write_answers(states: list, amendment: str, change_text: str, nodes: dict, writer: Callable[[str], str]) -> None:
    for s in states:
        p = prompt_for(s, amendment, change_text, nodes)
        try:
            text = (writer(p) or "").strip()
        except Exception as e:  # noqa: BLE001
            s.answer = {"by": "writer", "text": "", "checked": False, "error": repr(e)[:200]}
            s.log.append("답변: 호출 실패")
            continue
        ok = valid(text, p)
        s.answer = {"by": "writer", "text": text if ok else "", "checked": ok, "raw": text}
        s.log.append("답변: " + ("검사 통과" if ok else "검사 실패(버림)"))


def make_writer(model: str) -> Callable[[str], str]:
    from .nl import _post
    return lambda prompt: _post(model, [{"role": "user", "content": prompt}], timeout=120)
