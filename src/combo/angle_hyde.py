"""다각도 HyDE: 질문 하나에 가상 조문을 갈래별로 여러 개 쓴다 (rewrite.prompt = "hyde3" / "both3").

기존 HyDE는 가상 조문을 한 줄만 쓰고, 검색어 한 줄과 같은 각도로 쓰는 일이 잦다. 여러 가지를 묻는 질문(행위가 둘 이상, 일상어와 규정 용어가 다른 경우)은
규정 문장이 질문과 다른 낱말을 써서 한 각도로는 닿지 않는 조가 생긴다. 갈래마다 다른 낱말로 쓰게 해서 후보가 넓어지게 한다.
예시는 평가 질문과 겹치지 않는 가상 지침(차량)으로 쓴다.
"""
import json
import re

ANGLE_HYDE_PROMPT = """직원이 사내 규정에 대해 물은 질문이다. 이 질문에 답하려면 규정의 서로 다른 조문을 봐야 할 수 있다.
질문이 묻는 것을 서로 다른 행위·상황·제도로 나눠 최대 3개 갈래를 만들고, 갈래마다 그 답이 될 규정 조문을 규정 문체로 1~2문장 쓴다.
- 갈래마다 다른 낱말로 쓴다. 같은 낱말을 갈래끼리 되풀이하지 않는다. 질문의 일상어는 규정 용어와, 그 상위·하위 말로 바꿔 쓴다.
- 질문이 한 가지만 묻는다면 갈래는 1~2개여도 된다. 억지로 늘리지 않는다.
- 조 번호, 규정 이름, 구체적 숫자·금액·일수는 지어내지 않는다 ("일정 기간", "정하는 바에 따라"처럼 쓴다).
- 출력은 JSON 한 줄이다: {"갈래": ["조문 문장", "조문 문장"]}

예시 질문: 회사 차를 빌려서 주말에 개인 일에 써도 돼요? 기름값은 누가 내요?
예시 답: {"갈래": ["업무용 차량은 업무 외의 목적으로 사용할 수 없으며, 부득이한 경우 차량관리부서장의 승인을 받아야 한다.", "차량 운행에 드는 유류비와 통행료는 정하는 바에 따라 이용 부서가 부담한다."]}"""


def parse_angles(raw, limit=3):
    """모델 출력에서 갈래 문장 목록을 뽑는다. JSON이 깨졌고 JSON처럼 보이지 않는 글이면 첫 줄 하나를 쓴다 (기존 HyDE와 같다)."""
    m = re.search(r"\{.*\}", raw or "", re.S)
    items = []
    if m:
        try:
            items = json.loads(m.group(0)).get("갈래") or []
        except ValueError:
            items = []
    out = list(dict.fromkeys(re.sub(r"\s+", " ", x).strip() for x in items if isinstance(x, str) and x.strip()))[:limit]
    if not out and not (raw or "").lstrip().startswith("{"):
        lines = (raw or "").strip().strip('"').splitlines()
        out = [lines[0].strip()] if lines and lines[0].strip() else []
    return out
