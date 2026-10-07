"""답변 생성 (LangChain). 검색 근거를 받아 OpenAI 모델로 답하고, 인용·충돌·문장 근거를 검사한다.

모델 호출은 LangChain(`ChatOpenAI`)으로 한다. `.env`에 LANGCHAIN_API_KEY가 있으면 LangSmith로 자동 추적된다.
검색기는 LangChain으로 바꾸지 않는다. 정확 조회, 가중 RRF, 리랭킹, 필터, 확장 같은 고유 로직이 많아서
프레임워크에 맞추면 복잡해지기 때문이다 (`src/search/search.py`, `답변_설계.md` 9절).

규칙 (SYSTEM_PROMPT에 넣고, 결과에서 한 번 더 확인한다)
  - 제공된 근거에서만 답한다. 근거에 없는 내용은 쓰지 않는다.
  - 문장마다 근거 번호 [n]을 붙인다.
  - 부칙은 과거 개정 경과 조항, 삭제 조문은 삭제된 조항으로 명시한다. 현행 근거로 쓰지 않는다.
  - 정정안내가 있으면 답변 끝에 함께 쓴다.
  - 근거끼리 내용이 다르면 판정하지 않는다. 충돌 여부는 별도 호출로 찾고, 화면에서 두 조문을 나란히 보여준다.
  - 외부 법령은 근거가 아니다.

점수 구간(리랭커 점수 기준)은 pipeline.py에서 정한다. 이 파일은 모델 호출과 검사만 담당한다.
"""
import json
import os
import re

from dotenv import load_dotenv
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from config import OPENAI_MODEL

load_dotenv()

# LangSmith 추적: .env에 LANGCHAIN_API_KEY가 있으면 켠다. 없으면 평소처럼 추적 없이 동작한다.
if os.environ.get("LANGCHAIN_API_KEY") and not os.environ.get("LANGCHAIN_TRACING_V2"):
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ.setdefault("LANGCHAIN_PROJECT", "kvic-internal-regulations")

SYSTEM_PROMPT = """너는 한국벤처투자 내부규정을 설명하는 도우미다. 아래 규칙과 출력 형식을 반드시 지킨다.

[규칙]
1. 제공된 근거([1], [2] …)에 적힌 내용만 사용한다. 근거에 없는 내용은 추측하지 않는다.
2. 문장마다 근거 번호를 [1]처럼 붙인다. 여러 근거면 [1][2]처럼 붙인다.
3. 근거의 규정 이름, 조, 항은 표기를 그대로 쓴다. 새로 만들지 않는다.
4. 근거끼리 내용이 달라도 어느 쪽이 맞는지 판정하지 않는다. 각 근거의 내용을 번호와 함께 적는다.
5. 근거가 질문의 핵심에 답하지 못하면, 답할 수 있는 부분만 말하고 나머지는 "찾을 수 없었습니다"라고 쓴다.
6. 근거에 없는 일반 조언이나 예측을 덧붙이지 않는다. 예: "논의가 필요합니다", "달라질 수 있습니다" 같은 문장은 근거에 적혀 있을 때만 쓴다.
7. 근거는 번호순으로 현행 조문이 먼저 나온다. 현행 근거로 먼저 답하고, 부칙이나 삭제 근거는 답의 근거로 쓰지 않는다.
8. 부칙·삭제 여부와 정정 안내는 답변에 쓰지 않아도 된다. 시스템이 따로 붙인다.
9. 날짜는 근거 본문에 적힌 날짜만 쓴다. "개정" 표기는 문서 전체의 개정일이지 조문의 시행일이나 삭제일이 아니다.

[출력 형식]
- 첫 문장: 결론을 바로 문장으로 쓴다 (한두 문장, 근거 번호 포함). "결론:" 같은 머리말을 붙이지 않는다.
- 이어서 세부 내용을 불릿으로 쓴다. 최대 5개. 각 불릿 끝에 [n].
- 길이: 기본 3~5문장. 조문 원문을 길게 옮기지 않는다."""

CONFLICT_PROMPT = """아래 근거 중에서, 같은 질문에 대해 서로 다른 답(수치, 기준, 결론)을 주는 조문 쌍만 찾는다.

충돌이 아닌 경우 (찾지 않는다):
- 같은 단어(예: "감사")를 쓰지만 서로 다른 것을 규정하는 조문. 예: 하나는 감사의 "종류"를 나열하고, 다른 하나는 감사의 "직무"나 "업무"를 나열하면 충돌이 아니다. 둘 다 "감사"에 관한 것일 뿐, 같은 질문에 다른 답을 주는 것이 아니다.
- 한쪽이 다른 쪽을 보충하거나 구체화하는 경우.
- 적용 대상이나 상황이 다른 경우 (예: 하나는 정규직, 하나는 계약직 기준).

충돌인 경우 (찾는다):
- "지급률은 70%다"와 "지급률은 80%다"처럼 같은 항목에 다른 수치나 기준을 말하는 경우.

- 출력은 JSON 배열만 쓴다. 예: [[1, 3]]
- 확신이 없으면 넣지 않는다. 쌍이 없으면 []만 쓴다.
- 설명은 쓰지 않는다."""

VERIFY_PROMPT = """질문에 대한 답변과, 답변이 사용한 근거가 주어진다. 답변의 불릿을 하나씩 보고,
그 불릿이 인용한 근거 번호의 본문에 실제로 있는 내용인지 확인한다.

- 근거 본문에 없는 내용(추측, 일반론, 과장)을 담은 불릿이 있으면, 그 불릿 문장을 그대로 JSON 배열에 담는다.
- 결론 문장도 같은 기준으로 본다.
- 근거와 맞으면 포함하지 않는다. 모두 맞으면 []만 출력한다.
- 설명은 쓰지 않는다."""

_CITE = re.compile(r"\[(\d+)\]")


def format_context(entries):
    """근거 목록을 번호를 붙인 텍스트로 만든다. 번호는 답변의 [n]과 맞는다."""
    blocks = []
    for i, e in enumerate(entries, start=1):
        # 개정표기는 모델에게 주지 않는다. 문서 개정일을 조문 날짜로 잘못 읽는 일이 있었다 (화면에는 표시).
        head = f"[{i}] {e['문서명']} {e['article']} | 조문상태: {e['조문상태']} | 위계: {e.get('위계') or '-'}"
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


def order_entries(entries):
    """현행 조문을 먼저, 부칙·삭제를 뒤로 보낸다. 같은 그룹 안에서는 검색 순서를 유지한다."""
    return sorted(entries, key=lambda e: 0 if e["조문상태"] == "현행" else 1)


def state_notes(entries, cited):
    """부칙·삭제 근거를 인용했을 때 붙이는 참고 문구. 모델이 쓰지 않고 코드가 만든다."""
    notes = []
    for n in cited:
        if 1 <= n <= len(entries):
            e = entries[n - 1]
            if e["조문상태"] == "부칙":
                notes.append(f"※ 참고: [{n}]은 부칙 조항이라 현행 기준이 아닙니다.")
            elif e["조문상태"] == "삭제":
                notes.append(f"※ 참고: [{n}]은 삭제된 조항입니다.")
    return notes


def revision_notes(entries, cited):
    """인용한 근거 중 정정안내가 있는 것을 답변 끝에 붙인다."""
    notes = []
    for n in cited:
        if 1 <= n <= len(entries) and entries[n - 1].get("정정안내"):
            notes.append(f"※ 정정 안내 [{n}]: " + " / ".join(entries[n - 1]["정정안내"]))
    return notes


def check(answer, n_entries):
    """답변의 인용을 검사한다. 없는 번호를 쓰거나 인용이 빠지면 경고를 돌려준다."""
    warnings = []
    cited = sorted({int(x) for x in _CITE.findall(answer)})
    bad = [n for n in cited if n < 1 or n > n_entries]
    if bad:
        warnings.append(f"근거에 없는 번호를 인용함: {bad}")
    if not cited:
        warnings.append("인용 번호가 없는 답변")
    return cited, warnings


def _parse_json_list(text):
    try:
        raw = json.loads(text.strip())
    except (ValueError, TypeError):
        return None
    return raw if isinstance(raw, list) else None


def parse_pairs(text, n_entries):
    """모델이 돌려준 충돌 쌍을 검사한다.

    반환: (쌍 목록, ok). ok=False는 응답을 믿을 수 없다는 뜻이다 — JSON 배열이 아니거나, 배열 안에
    "정수 두 개짜리 목록"이 아닌 항목이 있거나, 존재하지 않는 근거 번호나 자기 자신과의 쌍처럼
    값 자체가 말이 안 되는 항목이 있을 때다. 이런 항목은 모델이 넘겨준 근거 목록([1]..[n])을
    혼동했다는 신호이므로, 조용히 버리고 ok=True(정상 통과)로 돌려주면 안 된다. 형식이 깨진
    경우와 똑같이 응답 전체를 믿지 않는다.
    """
    raw = _parse_json_list(text)
    if raw is None:
        return [], False
    pairs = []
    for p in raw:
        if not (isinstance(p, list) and len(p) == 2
               and all(isinstance(x, int) and not isinstance(x, bool) for x in p)):
            return [], False  # 형식이 깨진 항목
        a, b = sorted(p)
        if not (1 <= a < b <= n_entries):
            return [], False  # 존재하지 않는 근거 번호이거나 자기 자신과의 쌍
        pairs.append((a, b))
    return sorted(set(pairs)), True


def parse_unsupported(text):
    """모델이 돌려준 '근거 밖 문장' 목록을 검사한다. 반환은 parse_pairs와 같은 (목록, ok) 형태다.

    배열 안의 모든 항목이 문자열이어야 한다. 문자열이 아닌 항목이 섞여 있으면(예: [123]) 형식이
    깨진 것으로 보고 ok=False를 돌려준다.
    """
    raw = _parse_json_list(text)
    if raw is None:
        return [], False
    out = []
    for s in raw:
        if not isinstance(s, str):
            return [], False
        if s.strip():
            out.append(s)
    return out, True


class Generator:
    def __init__(self, model=OPENAI_MODEL):
        self.model = ChatOpenAI(model=model, temperature=0)
        self.model_name = model

    def _ask(self, system, user):
        prompt = ChatPromptTemplate.from_messages([("system", system), ("human", "{input}")])
        chain = prompt | self.model
        return chain.invoke({"input": user}).content.strip()

    def answer(self, question, entries):
        user = f"질문: {question}\n\n근거:\n{format_context(entries)}"
        text = self._ask(SYSTEM_PROMPT, user)
        cited, warnings = check(text, len(entries))
        return {"answer": text, "cited": cited, "warnings": warnings, "model": self.model_name}

    def conflicts(self, question, entries):
        """근거 중 서로 충돌하는 조문 쌍을 찾는다. 근거가 2개 미만이면 호출하지 않는다.

        반환: (쌍 목록, ok). 응답 형식이 틀리면 한 번 더 물어보고, 그래도 안 되면 ok=False로
        돌려준다. 호출 쪽(pipeline.py)은 ok=False일 때 "충돌 확인 실패"를 사용자에게 경고해야 한다.
        쌍이 없는 것으로 조용히 넘기면 실제로 충돌이 있어도 놓친다.
        """
        if len(entries) < 2:
            return [], True
        user = f"질문: {question}\n\n근거:\n{format_context(entries)}"
        pairs, ok = parse_pairs(self._ask(CONFLICT_PROMPT, user), len(entries))
        if not ok:
            pairs, ok = parse_pairs(self._ask(CONFLICT_PROMPT, user), len(entries))
        return pairs, ok

    def verify(self, question, answer, entries):
        """답변의 각 불릿이 인용한 근거 본문과 실제로 맞는지 확인한다.

        반환: (근거 밖 문장 목록, ok). ok=False면 모델 응답을 해석하지 못한 것이므로,
        "검사를 통과했다"가 아니라 "검사를 못 했다"로 다뤄야 한다.
        """
        if not entries:
            return [], True
        user = f"질문: {question}\n\n답변:\n{answer}\n\n근거:\n{format_context(entries)}"
        unsupported, ok = parse_unsupported(self._ask(VERIFY_PROMPT, user))
        if not ok:
            unsupported, ok = parse_unsupported(self._ask(VERIFY_PROMPT, user))
        return unsupported, ok
