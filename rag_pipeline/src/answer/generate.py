"""답변 생성: 검색 근거를 받아 OpenAI 모델로 답하고, 인용과 근거 규칙을 검사한다.

규칙 (SYSTEM_PROMPT에 넣고, 결과에서 한 번 더 확인한다)
  - 제공된 근거에서만 답한다. 근거에 없는 내용은 쓰지 않는다.
  - 근거가 질문에 답하지 못하면 "제공된 규정에서 확인되지 않습니다"라고 쓰고 담당 부서 확인을 안내한다.
  - 문장마다 근거 번호 [n]을 붙인다.
  - 부칙은 과거 개정 경과 조항, 삭제 조문은 삭제된 조항으로 명시한다. 현행 근거로 쓰지 않는다.
  - 정정안내가 있으면 답변에 함께 쓴다.
  - 근거끼리 내용이 다르면 둘 다 보여주고, 어느 쪽이 맞는지 판정하지 않는다.
  - 외부 법령은 근거가 아니다.
"""
import os
import re

from dotenv import load_dotenv
from openai import OpenAI

from config import OPENAI_MODEL

load_dotenv()

NO_ANSWER = "제공된 규정에서 확인되지 않습니다"

SYSTEM_PROMPT = """너는 한국벤처투자 내부규정을 설명하는 도우미다. 아래 규칙을 반드시 지킨다.

1. 제공된 근거([1], [2] …)에 적힌 내용만 사용한다. 근거에 없는 내용은 추측하지 않는다.
2. 근거가 질문에 답하지 못하면 "제공된 규정에서 확인되지 않습니다."라고 쓰고, 담당 부서에 확인하라고 안내한다.
3. 답변의 각 문장 끝에 근거 번호를 [1]처럼 붙인다. 여러 근거면 [1][2]처럼 붙인다.
4. 조문상태가 "부칙"인 근거는 "과거 개정 경과를 정한 부칙 조항이라 현행 기준이 아닙니다"라고 밝힌다.
5. 조문상태가 "삭제"인 근거는 "삭제된 조항입니다"라고 밝히고, 그 내용을 현행 기준으로 말하지 않는다.
6. 근거에 "정정안내"가 있으면 답변 끝에 "※ 정정 안내: ..."로 함께 적는다.
7. 근거끼리 내용이 다르면 각각을 근거 번호와 함께 보여주고, 어느 쪽이 맞는지 판정하지 않는다.
8. 규정 이름, 조, 항은 근거의 표기를 그대로 쓴다. 새로 만들지 않는다.
9. 한국어로, 간결하게 답한다."""

_CITE = re.compile(r"\[(\d+)\]")


def format_context(entries):
    """근거 목록을 번호를 붙인 텍스트로 만든다. 번호는 답변의 [n]과 맞는다."""
    blocks = []
    for i, e in enumerate(entries, start=1):
        head = f"[{i}] {e['문서명']} {e['article']} | 조문상태: {e['조문상태']} | 개정: {e.get('개정표기') or '-'}"
        lines = [head, e["body"]]
        if e.get("정정안내"):
            lines.append("정정안내: " + " / ".join(e["정정안내"]))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def flatten(assembled):
    """assemble() 결과를 번호 순서대로 한 줄씩 펼친다. 같은 청크는 한 번만 나온다."""
    out = []
    for g in assembled["groups"]:
        for it in g["items"]:
            out.append(it)
    return out


def check(answer, n_entries):
    """답변의 인용을 검사한다. 없는 번호를 쓰거나 인용이 빠지면 경고를 돌려준다."""
    warnings = []
    cited = sorted({int(x) for x in _CITE.findall(answer)})
    bad = [n for n in cited if n < 1 or n > n_entries]
    if bad:
        warnings.append(f"근거에 없는 번호를 인용함: {bad}")
    if NO_ANSWER not in answer and not cited:
        warnings.append("인용 번호가 없는 답변")
    return cited, warnings


class Generator:
    def __init__(self, model=OPENAI_MODEL):
        self.client = OpenAI()
        self.model = model

    def answer(self, question, entries):
        context = format_context(entries) if entries else "(근거 없음)"
        user = f"질문: {question}\n\n근거:\n{context}"
        resp = self.client.responses.create(
            model=self.model,
            instructions=SYSTEM_PROMPT,
            input=user,
        )
        text = resp.output_text.strip()
        cited, warnings = check(text, len(entries))
        return {
            "answer": text,
            "cited": cited,
            "warnings": warnings,
            "model": self.model,
        }
