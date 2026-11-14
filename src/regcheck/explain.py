"""카드 설명: 영향 카드 하나가 「왜 확인할 만한지」를 로컬 모델이 2~3문장으로 풀어 쓴다 (요약·해석, 판정 아님).

  explain(amendment, where, quote, reason, basis, model)  → 설명 문장 또는 None
원칙 (261015 논의: 있어 보이려는 추론이 아니라 읽기 쉬운 해석만):
  · 입력은 개정안 문구, 영향받는 조문의 원문 인용문, 점검 코드가 남긴 이유뿐. 모델은 이 안에서만 말한다
  · 판정하지 않는다 (「문제가 있다/없다」, 「반드시」 금지). 확인 포인트를 말하는 데 그친다
  · 출력 검증: 입력에 없는 조 번호·숫자가 나오면 버린다 (환각 방지) → 화면은 기존 이유 문구를 그대로 둔다
"""

from __future__ import annotations

import re

from .nl import _post

PROMPT = """회사 내부 규정(내규)의 개정안과, 그 개정안 때문에 확인 대상으로 걸린 조문이 있다. 담당자가 이 조문을 왜 열어 봐야 하는지 2~3문장으로 풀어 쓴다.

[개정안]
{amendment}

[확인 대상] {where}
[그 조문의 해당 문장] {quote}
[점검에서 걸린 이유] {reason}
[발견 근거] {basis}

규칙
- 위 내용에 있는 낱말과 숫자만 쓴다. 조 번호·숫자를 새로 만들지 않는다.
- 문제가 있다거나 반드시 고쳐야 한다고 단정하지 않는다. 「~를 확인해 볼 만하다」 정도로 쓴다.
- 발견 근거가 「모델 추정」이면 근거가 약하다는 점을 한 문장에 밝힌다.
- 법적 판단을 하지 않는다. 한국어 2~3문장, 200자 이내."""

BANNED = ("반드시", "위법", "불법", "문제가 있", "문제가 없", "틀렸", "잘못되")
_NUM = re.compile(r"제\s*\d+\s*조(?:\s*의\s*\d+)?|\d+")


def valid(text: str, source: str) -> bool:
    """검증: 200자 이내·3문장 이하, 단정 표현 없음, 입력에 없는 조 번호·숫자 없음"""
    t = text.strip()
    if not t or len(t) > 260 or len(re.findall(r"[.!?]\s|[.!?]$|다\.", t)) > 4:
        return False
    if any(b in t for b in BANNED):
        return False
    src = re.sub(r"\s+", "", source)
    return all(re.sub(r"\s+", "", m) in src for m in _NUM.findall(t))


def explain(amendment: str, where: str, quote: str, reason: str, basis: str, model: str) -> str | None:
    prompt = PROMPT.format(amendment=amendment[:900], where=where, quote=(quote or "(문장 없음)")[:400], reason=reason[:300], basis=basis or "")
    out = _post(model, [{"role": "user", "content": prompt}], timeout=120).strip()
    return out if valid(out, "\n".join([amendment, where, quote or "", reason, basis or ""])) else None
