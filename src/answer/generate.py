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

from config import HIGHLIGHT_HITS, OPENAI_MODEL

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
5. 근거 어디에도 질문의 핵심(가능 여부, 수치, 기준, 절차)에 답하는 내용이 없을 때만 "제공된 규정에서 확인되지 않습니다. 담당 부서에 확인해 주세요."라고 쓴다.
   - 핵심에 답하는 내용이 하나라도 있으면 이 문장을 쓰지 않는다. 일부만 확인되면 확인된 부분만 쓰고, 빠진 부분은 "~는 근거에 없습니다"처럼 그 내용만 짚는다.
   - 이 문장은 결론에만 쓴다. 세부 불릿에는 쓰지 않는다.
   - 질문과 가장 가까운 근거가 있으면 이어서 "관련 조문:" 한 줄로 [n]과 규정명·조·항, 그 조문이 무엇을 정하는지 한 문장을 알려 준다. 그 조문이 질문에 답하는 것처럼 쓰지 않는다.
   - "찾을 수 없었습니다" 한 줄만 쓰고 끝내지 않는다.
6. 근거에 없는 일반 조언이나 예측을 덧붙이지 않는다. 예: "논의가 필요합니다", "달라질 수 있습니다" 같은 문장은 근거에 적혀 있을 때만 쓴다.
7. 근거는 번호순으로 현행 조문이 먼저 나온다. 현행 근거로 먼저 답하고, 부칙이나 삭제 근거는 현행 기준의 근거로 쓰지 않는다.
   - 조문상태가 '삭제'인 근거는 "그 항이 삭제되었다"는 사실의 근거로는 인용할 수 있다. 예: "○○에 관한 제N조 제M항은 삭제되었습니다 [n]." 삭제 날짜는 본문의 <삭제 날짜>에 적힌 것만 쓴다.
   - 삭제된 항의 원래 내용은 알 수 없다. 삭제 표기만으로 과거 내용을 추측하지 않고, 삭제됐다는 이유로 "현재 기준이 없다"고 결론짓지 않는다. 다른 근거에 현행 기준이 없으면 "확인되지 않는다"고 쓴다.
8. 부칙·삭제 여부와 정정 안내는 답변에 쓰지 않아도 된다. 시스템이 따로 붙인다. 단, 질문이 어떤 기준이 아직 있는지·삭제됐는지를 묻는다면 삭제 사실을 쓴다.
9. 날짜는 근거 본문에 적힌 날짜만 쓴다. "개정" 표기는 문서 전체의 개정일이지 조문의 시행일이나 삭제일이 아니다.
10. 근거를 쓰기 전에, 그 근거가 질문과 **같은 대상·같은 상황**을 다루는지 먼저 확인한다.
   - 단어가 같아도 대상이 다르면(예: 질문은 "직원"인데 근거는 "임원"·"단시간근로자"·"외부 기관" 대상, 또는 질문은 "직원에게 지급"인데 근거는 "외부에 제공 금지") 그 근거로 질문에 답하지 않는다.
   - 단어가 같아도 다루는 행위·절차가 다르면(예: 질문은 "징계 기준"인데 근거는 "근무평정(인사평가) 기준") 그 근거로 결론을 내리지 않는다.
   - 대상·상황이 맞는 근거가 없으면, 그 근거를 "관련은 있지만 질문에 직접 답하지는 않는다"고만 언급하고, 핵심 결론은 "제공된 규정에서 확인되지 않습니다"로 쓴다(규칙 5).
   - 다른 대상에 대한 규정만 있을 때, 그걸 일반화해서 "~는 금지됩니다/~는 보장됩니다"처럼 질문 대상 전체에 대한 결론으로 쓰지 않는다.
   - 반대로 근거가 질문과 같은 대상·상황을 다루면 이 규칙을 이유로 거절하지 않는다(규칙 11).
11. 근거 중 하나라도 질문에 직접 답하는 내용(수치, 기준, 가능 여부, 절차)이 있으면 반드시 그 내용으로 답한다. 이 경우 "확인되지 않습니다"로 끝내지 않는다.
   - 질문에 이미 들어 있는 말(연도, 선배·동료가 한 말, 옛 제도에 대한 기억)이 근거와 다르면 거절하지 말고 근거 기준으로 바로잡는다. 예: "근거에는 ~로 되어 있어 말씀하신 내용과 다릅니다 [n]."
   - 시행 시점이 근거에 없어 확인할 수 없으면, 현행 규정 내용은 답하고 시행 시점만 확인되지 않는다고 쓴다.
12. 표(별표)가 근거일 때는 질문의 대상(직급, 상황)에 해당하는 행만 골라 값과 단위를 그대로 쓴다. 표의 열 구분이 불분명해 값을 확정할 수 없으면 그 값을 단정하지 않고 확인되지 않는다고 쓴다.
13. 질문하는 사람이 어떤 집단(예: 신입, 수습 중인 직원)에 속하는지 먼저 생각한다. 근거에 그 집단이 다른 집단에 "포함된다/산입된다/준용한다"고 적혀 있으면, 그 다른 집단에 대한 근거의 기준(일수, 금액, 조건)을 질문한 사람에게도 그대로 적용한다. 이어서 쓴 두 근거는 번호를 모두 단다.
14. 질문이 "~는 안 되는 거 맞죠?", "~없다던데 진짜예요?"처럼 어떤 상태를 단정하면, 근거의 기준을 먼저 적고 그 기준에 비추어 단정이 맞는지 결론에 쓴다. 단정이 근거의 기준과 다르면 질문이 틀렸다고 쓰고 근거의 기준을 알려 준다. 이때 "규정에서 확인되지 않는다"고 쓰지 않는다.
15. 질문에 단정이 둘 이상 들어 있으면("~인 거 맞죠? 그럼 ~인 건가요?") 각 단정을 따로 근거의 기준에 비추어 본다. 첫 단정이 틀렸으면 그 위에 세운 다음 단정도 틀린 것이다. 결론은 "맞지 않습니다"로 시작해 근거의 기준(일수, 금액, 조건)을 먼저 쓰고, 질문자가 깔고 있던 상태를 그대로 되풀이하지 않는다.
16. 질문이 여러 항목을 동시에 물었으면(예: "월급이랑 복지포인트, 출산축하금은 각각 어떻게 되나요") 근거에 있는 항목을 하나도 빠뜨리지 않고 전부 답한다. 한 항목만 자세히 답하고 나머지 항목을 통째로 빼놓지 않는다.
17. 근거에 적힌 수치나 결론에 적용 조건·한도·예외(예: "1회로 한정", "구간별로 다름", "~인 경우에만")가 붙어 있으면, 그 수치만 떼어서 쓰지 않고 조건도 같이 쓴다. 조건을 빼면 다른 뜻이 된다.

[출력 형식] 아래 구역을 이 순서와 이 머리말 그대로 쓴다. 화면에는 시스템이 결론을 맨 위로 옮겨 보여 준다.
세부:
- 질문의 표현은 무시하고, 근거에서 질문과 관련된 내용만 불릿으로 쓴다. 최대 5개. 질문이 여러 항목을 물었으면(규칙 16) 항목당 최소 1개씩 쓰고 전체는 8개까지 쓸 수 있다. 각 불릿 끝에 [n]. 근거에 적힌 그대로 쓴다. 쓸 내용이 없으면 "없음"만 쓴다.
결론: 한두 문장. 반드시 위 세부 불릿을 다 쓴 다음에, 불릿과 모순되지 않게 쓴다.
   - 질문이 "~맞죠?"처럼 어떤 주장의 확인을 구하면, 첫머리에서 불릿에 비추어 "맞습니다/맞지 않습니다/근거로는 확인되지 않습니다" 중 하나를 밝히고 그 이유를 [n]으로 단다.
   - 질문이 기대하는 답이더라도 세부 불릿과 다르면 불릿을 따른다. 질문에 맞장구치지 않는다.
   - 결론이 근거로 질문에 답했다면 "확인되지 않습니다. 담당 부서에 확인해 주세요" 문구를 덧붙이지 않는다. 그 문구는 규칙 5의 경우에만, 결론 전체를 대신해서 쓴다.
관련 조문: 규칙 5에서 요구할 때만 쓴다. 아니면 이 구역을 쓰지 않는다.
- 길이: 기본 3~5문장. 조문 원문을 길게 옮기지 않는다."""

CONSISTENCY_PROMPT = """질문과 답변이 주어진다. 답변은 '결론 문장'과 '세부 불릿'으로 되어 있다.
결론 문장이 세부 불릿의 내용과 모순되는지 판정한다.

모순인 경우:
- 불릿은 "수습기간은 계속근로기간에 산입된다"인데 결론은 "수습 중에는 연차가 발생하지 않는다"처럼 결론이 불릿과 반대인 경우.
- 결론은 질문의 전제를 "맞다"고 하는데 불릿은 그 전제가 틀렸음을 보여 주는 경우, 또는 그 반대.
- 결론이 "임원도 팀장도 가능"인데 불릿은 "팀장 이하는 불가"처럼 결론이 불릿의 일부를 부정하는 경우.

모순이 아닌 경우:
- 결론이 불릿을 요약하거나 좁혀서 쓴 경우.
- 근거로는 확인되지 않는다고 밝히고, 불릿이 관련 내용만 나열하는 경우.

출력은 JSON 한 줄만 쓴다: {{"모순": true}} 또는 {{"모순": false}}. 설명은 쓰지 않는다."""

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
- 근거와 의미가 반대인 문장도 포함한다. 특히 근거가 "~해서는 아니 된다/금지한다"인데 답변이 "~할 수 있다/허용된다"로 바뀌었거나, 근거의 조건("다만", "한하여", 금액 상한, 예외)을 빼서 범위를 넓힌 문장이 그렇다.
- 답변의 숫자(금액, 일수, 비율, 기간)와 단위가 근거 본문과 정확히 같은지 본다. 근거의 표에서 다른 직급·등급 행의 값을 가져왔거나, 근거에 없는 단위(원, 달러 등)를 붙인 문장도 포함한다.
- 결론 문장도 같은 기준으로 본다.
- "~는 제공된 규정에서 확인되지 않는다/근거에 없다"고 말하는 문장은 근거에 없는 내용을 지어낸 것이 아니므로 포함하지 않는다. 단, 근거에 실제로 그 내용이 있는데 없다고 쓴 문장은 포함한다.
- 근거와 맞으면 포함하지 않는다. 모두 맞으면 []만 출력한다.
- 설명은 쓰지 않는다."""

REWRITE_PROMPT = """직원이 사내 규정 검색창에 구어체로 쓴 질문을, 규정 문서에 실제로 쓰이는 공식 용어로 바꿔 검색어 한 줄로 만든다.
- 줄임말, 속어, 외래어, 일상어를 규정에서 쓰는 정식 용어로 바꾼다.
- 질문의 상황이 규정에서 어떤 주제(제도, 절차, 금지·허용 사항)로 다뤄질지 떠올려, 그 주제어 2~4개를 덧붙인다.
- 질문에 없는 사실이나 수치를 만들어 넣지 않는다. 답을 쓰지 않는다.
- 출력은 검색어 한 줄만 쓴다. 설명, 따옴표, 번호를 쓰지 않는다."""

# 질문 나누기 (Azure AI Search agentic retrieval의 query planning과 같은 방식). REWRITE_PROMPT를 대신한다:
# 하위 질문마다 검색어를 같이 만들므로 모델 호출 수는 그대로다.
PLAN_PROMPT = """직원이 사내 규정 검색창에 쓴 질문을 검색 계획으로 바꾼다.

1. 질문이 서로 다른 것을 두 가지 이상 묻는지 본다 (예: "사원증 재발급은 어디서 하고, 회의실 예약은 어떻게 하나요").
   - 그렇다면 묻는 것마다 하위 질문을 하나씩 만든다. 최대 3개.
   - 하위 질문마다 원래 질문의 대상과 상황을 모두 넣어서, 혼자 읽어도 같은 뜻이 되게 쓴다.
   - 한 가지만 묻는다면 하위 질문은 1개다. 나눌지 애매하면 나누지 않는다.
   - 다음은 나누지 않는다:
     · 한 제도의 여러 면 (예: "사원증 재발급은 며칠 걸리고 비용이 드나요")
     · 상황 설명이나 덧붙인 말 (예: "사원증 재발급 돼요? 어제 잃어버렸는데", "사무용품 개인 돈으로 사도 되나요? 나중에 청구해야 해요?")
     · 둘 중 하나를 고르는 질문 (예: "노트북 교체는 부서에서 신청해요 아니면 제가 직접 해요?")
2. 하위 질문마다 "검색어"를 만든다: 줄임말·속어·외래어·일상어를 규정에서 쓰는 정식 용어로 바꾸고,
   규정에서 어떤 주제(제도, 절차, 금지·허용 사항)로 다뤄질지 떠올려 그 주제어 2~4개를 덧붙인 한 줄.
3. 질문에 규정 이름(예: "복무규정", "여비규정")이 적혀 있으면 그 이름을 해당 하위 질문에도 그대로 남긴다.
- 질문에 없는 사실이나 수치를 만들어 넣지 않는다. 답을 쓰지 않는다.
- 출력은 JSON 배열만 쓴다. 설명을 쓰지 않는다.
  예: [{{"질문": "사원증 재발급은 어디서 하나요?", "검색어": "출입증 재발급 신청 절차 분실"}}]"""

MAX_SUBQUERIES = 3

# config.HIGHLIGHT_HITS일 때: 근거 머리에 역할을 적고(ROLE_LABEL) 질문 뒤에 HIT_NOTE를 붙인다. 기본 프롬프트는 그대로다.
# 인용으로 따라온 조문이 검색으로 찾은 조문과 같은 모양이라, 인용이 많으면 핵심 조문이 묻혔다.
HIT_NOTE = ("[참고] 근거마다 '역할'이 적혀 있다. '질문으로 찾은 조문'을 먼저 보고 답한다. 그 본문에서 ▶로 시작하는 항이 "
            "검색이 질문과 가장 가깝다고 찾은 항이다. 같은 조의 다른 항(조건, 예외, 단서)도 함께 확인한다. "
            "'인용된 조문(참고)'은 찾은 조문이 가리키는 다른 조문이다. 찾은 조문을 보충하거나 그 조문이 '~에 따른다'고 "
            "넘긴 내용을 확인할 때만 쓰고, 질문에 직접 답하는 근거로 먼저 쓰지 않는다. ▶ 표시는 답변에 옮겨 쓰지 않는다.")
ROLE_LABEL = {"검색": "질문으로 찾은 조문", "정확조회": "질문으로 찾은 조문", "인용": "인용된 조문(참고)",
              "형제항": "같은 조 다른 항"}

_CITE = re.compile(r"\[(\d+)\]")


def format_context(entries):
    """근거 목록을 번호를 붙인 텍스트로 만든다. 번호는 답변의 [n]과 맞는다."""
    blocks = []
    for i, e in enumerate(entries, start=1):
        # 개정표기는 모델에게 주지 않는다. 문서 개정일을 조문 날짜로 잘못 읽는 일이 있었다 (화면에는 표시).
        head = f"[{i}] {e['문서명']} {e['article']} | 조문상태: {e['조문상태']} | 위계: {e.get('위계') or '-'}"
        if HIGHLIGHT_HITS and e.get("역할") in ROLE_LABEL:
            head += f" | 역할: {ROLE_LABEL[e['역할']]}"
            if e["역할"] == "인용" and e.get("인용원문"):
                head += f" ('{e['인용원문']}'이 가리킴)"
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


_GENERIC_FALLBACK = re.compile(r"\s*(?:제공된 규정에서 )?확인되지 않습니다\.\s*담당 부서에 확인해 주세요\.?\s*$")
_SECTION = re.compile(r"^(전제|세부|결론|관련 조문)[ \t]*:[ \t]*", re.M)


def assemble_sections(text):
    """모델이 쓴 전제·세부·결론·관련 조문 구역을 화면 순서(결론, 세부 불릿, 관련 조문)로 다시 조립한다.
    '결론' 구역을 못 찾으면 모델 응답을 그대로 돌려준다(형식을 안 지켜도 화면이 깨지지 않게)."""
    marks = list(_SECTION.finditer(text))
    if not any(m.group(1) == "결론" for m in marks):
        return text
    parts = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        parts.setdefault(m.group(1), text[m.end():end].strip())
    conclusion = parts["결론"]
    # 모델이 답을 이미 쓴 결론 끝에 일반 문구를 습관처럼 덧붙이는 일이 있어(실측 29건 중 4건) 코드로 뗀다.
    # 결론이 그 문구뿐이면(근거에 답이 없는 경우) 그대로 둔다.
    trimmed = _GENERIC_FALLBACK.sub("", conclusion).strip()
    if trimmed != conclusion and _CITE.search(trimmed):
        conclusion = trimmed
    out = [conclusion]
    detail = parts.get("세부", "")
    if detail and detail.strip(" -") != "없음":
        out.append(detail)
    if parts.get("관련 조문"):
        out.append("관련 조문: " + parts["관련 조문"])
    return "\n\n".join(p for p in out if p)


def has_bullets(text):
    return bool(re.search(r"^\s*[-•]\s+\S", text, re.M))


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
    # 모델이 ```json 울타리로 감싸거나 닫는 ]를 빠뜨리는 일이 있어(실측) 그만큼만 바로잡는다.
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    if text.startswith("[") and not text.endswith("]"):
        text += "]"
    try:
        raw = json.loads(text)
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


def parse_plan(text, question):
    """PLAN_PROMPT 응답을 하위 질문 목록 [{"질문", "검색어"}, ...]으로 바꾼다.
    하위 질문이 하나면 "질문"은 원래 질문 글자로 둔다: 나누지 않는 질문은 리랭킹까지 지금과 똑같이 검색된다.
    응답을 해석하지 못하면 원래 질문 하나(검색어 없음)로 돌려준다."""
    fallback = [{"질문": question, "검색어": None}]
    raw = _parse_json_list(text or "")
    if raw is None:
        return fallback
    plan = []
    for r in raw:
        if isinstance(r, dict) and str(r.get("질문") or "").strip():
            plan.append({"질문": str(r["질문"]).strip(),
                         "검색어": str(r.get("검색어") or "").strip() or None})
    if not plan:
        return fallback
    plan = plan[:MAX_SUBQUERIES]
    if len(plan) == 1:
        plan[0]["질문"] = question
    return plan


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

    def _ask_stream(self, system, user, on_token):
        """_ask와 같지만 모델이 쓰는 글자를 생기는 대로 on_token(조각)으로 넘긴다 (화면 스트리밍). 반환은 전체 글."""
        prompt = ChatPromptTemplate.from_messages([("system", system), ("human", "{input}")])
        parts = []
        for chunk in (prompt | self.model).stream({"input": user}):
            if chunk.content:
                parts.append(chunk.content)
                on_token(chunk.content)
        return "".join(parts).strip()

    def rewrite_query(self, question):
        """질문을 규정 용어로 바꾼 검색어. 실패하거나 비면 None(원래 질문만으로 검색한다)."""
        text = self._ask(REWRITE_PROMPT, question).strip().strip('"').splitlines()
        return text[0].strip() if text and text[0].strip() else None

    def plan_queries(self, question):
        """질문을 하위 질문으로 나누고 하위 질문마다 검색어를 만든다 (parse_plan). rewrite_query를 대신하는 한 번의 호출."""
        return parse_plan(self._ask(PLAN_PROMPT, question), question)

    def contradicts(self, question, answer):
        """결론 문장이 세부 불릿과 모순되는지. 응답을 해석하지 못하면 모순 없음으로 본다(재작성은 보조 수단이다)."""
        raw = self._ask(CONSISTENCY_PROMPT, f"질문: {question}\n\n답변:\n{answer}")
        m = re.search(r'"모순"\s*:\s*(true|false)', raw)
        return bool(m and m.group(1) == "true")

    def answer(self, question, entries, on_token=None):
        """on_token을 주면 첫 답변을 스트리밍으로 받는다 (글이 생기는 대로 on_token에 넘김). 결과는 같다.
        결론이 불릿과 모순되어 다시 쓰는 경우, 다시 쓴 답은 스트리밍하지 않는다 (화면은 최종 answer로 바꿔 보여 줘야 한다)."""
        context = format_context(entries)
        user = f"질문: {question}\n\n근거:\n{context}"
        if HIGHLIGHT_HITS:
            user += "\n\n" + HIT_NOTE
        raw = self._ask_stream(SYSTEM_PROMPT, user, on_token) if on_token else self._ask(SYSTEM_PROMPT, user)
        text = assemble_sections(raw)
        retried = False
        if has_bullets(text) and self.contradicts(question, text):
            retry = (user + "\n\n[주의] 직전 답변은 결론이 세부 불릿과 모순되었다. 근거 본문과 세부 불릿을 기준으로 "
                     "결론을 다시 써서 같은 형식으로 답한다.\n직전 답변:\n" + text)
            text = assemble_sections(self._ask(SYSTEM_PROMPT, retry))
            retried = True
        cited, warnings = check(text, len(entries))
        if retried:
            warnings = warnings + ["결론이 세부 내용과 모순되어 한 번 다시 작성함"]
        return {"answer": text, "cited": cited, "warnings": warnings, "model": self.model_name, "retried": retried}

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
