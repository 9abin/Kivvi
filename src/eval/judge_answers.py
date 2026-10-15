"""190문항 답변 채점 (LLM 채점, config.JUDGE_MODEL, temperature 0). 어느 색인의 답인지 채점 모델에 알리지 않는다.

실행 (저장소 루트에서): uv run python -m src.eval.judge_answers <tag> [<tag> ...] [--questions eval/answer_eval_100.jsonl] [--mode question|tiered]
입력: eval/answer_runs/<tag>/runs.jsonl, 정답: eval/answer_eval.jsonl (정답요지, 금지내용, 근거 인용, 답가능여부)
출력: eval/answer_runs/<tag>/judged.jsonl
"""
import ast
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor


sys.stdout.reconfigure(encoding="utf-8")
from dotenv import load_dotenv  # noqa: E402

load_dotenv()
os.environ["LANGCHAIN_TRACING_V2"] = "false"
from langchain_openai import ChatOpenAI  # noqa: E402

from config import JUDGE_MODEL  # noqa: E402

PROMPT = """너는 사내 규정 질의응답 답변을 채점한다. 질문, 채점 기준(정답 요지, 금지 내용, 규정 근거 인용, 답 가능 여부), 시스템 답변이 주어진다.

채점 규칙
- 요지_충족: 정답 요지 각 항목을 답변이 담았는지 순서대로 true/false. 표현이 달라도 뜻이 같으면 true. 숫자·기간·대상이 다르면 false.
- 금지_위반: 금지 내용 중 하나라도 답변에 들어 있으면 true. 정답 요지와 반대되는 단정(틀린 숫자, 없는 제도를 있다고 함)도 true.
- 판정: 정답 / 부분 / 오답 / 답못함 중 하나.
  - 정답: 요지를 모두(또는 핵심을 모두) 담고 금지_위반이 없다.
  - 부분: 요지 일부만 담았고 금지_위반이 없다.
  - 오답: 금지_위반이 있거나, 요지와 다른 내용으로 답했다.
  - 답못함: "확인되지 않음", "관련 내용 없음"처럼 답을 하지 않았다. 단, 답 가능 여부가 '불가'이거나 정답 요지가 "규정에서 확인되지 않음"인 질문에서 그렇게 답했으면 정답이다.
- 이유: 한 문장.
출력은 JSON 하나만: {"요지_충족": [true, false], "금지_위반": false, "판정": "부분", "이유": "..."}"""


# 질문 중심 채점 (2026.10.14, 사람 검토 기준): 정답 요지 중 질문이 직접 묻지 않은 부가 내용은 빠져도 정답이다.
# 사람 검토에서 "기밀 예외·민감정보 제외·숙박료 총합은 안 물어봤으니 too much"로 정답 처리한 기준을 옮긴 것.
PROMPT_QUESTION = """너는 사내 규정 질의응답 답변을 채점한다. 질문, 채점 기준(정답 요지, 금지 내용, 규정 근거 인용, 답 가능 여부), 시스템 답변이 주어진다.
채점은 질문 중심으로 한다: 질문자가 실제로 물어본 것에 바르게 답했는가를 본다.

채점 규칙
- 요지_필수: 정답 요지 각 항목이 질문에 답하는 데 꼭 필요한지 순서대로 true/false.
  - true(필수): 질문이 직접 묻는 것(가능 여부, 수치, 기간, 대상, 절차)의 답, 질문의 잘못된 전제를 바로잡는 내용,
    질문한 상황에 바로 적용되는 조건·예외(이것이 빠지면 질문자가 틀린 행동을 하게 되는 것).
  - false(부가): 질문이 묻지 않은 관련 정보, 질문 상황에 해당하지 않는 예외·특례, 묻지 않은 합계·절차·서식, 참고용 배경.
- 요지_충족: 정답 요지 각 항목을 답변이 담았는지 순서대로 true/false. 표현이 달라도 뜻이 같으면 true. 숫자·기간·대상이 다르면 false.
- 금지_위반: 금지 내용 중 하나라도 답변에 들어 있으면 true. 정답 요지와 반대되는 단정(틀린 숫자, 없는 제도를 있다고 함)도 true.
- 판정: 정답 / 부분 / 오답 / 답못함 중 하나.
  - 정답: 필수 요지를 모두 담고 금지_위반이 없다. 부가 요지는 빠져도 정답이다. 답변에 근거 있는 추가 정보가 있어도 감점하지 않는다.
  - 부분: 필수 요지 일부만 담았고 금지_위반이 없다.
  - 오답: 금지_위반이 있거나, 필수 요지와 다른 내용으로 답했다.
  - 답못함: "확인되지 않음", "관련 내용 없음"처럼 답을 하지 않았다. 단, 답 가능 여부가 '불가'이거나 정답 요지가 "규정에서 확인되지 않음"인 질문에서 그렇게 답했으면 정답이다.
- 이유: 한 문장.
출력은 JSON 하나만: {"요지_필수": [true, false], "요지_충족": [true, false], "금지_위반": false, "판정": "정답", "이유": "..."}"""


# 단계 채점 (2026.10.15, eval/answer_eval_100_tiered.jsonl): 사람이 정답 요지를 필수(정답요지)와 보조(정답요지_보조)로 미리 나눴다.
# question 모드는 채점 모델이 필수/부가를 스스로 가르지만, 이 모드는 사람이 가른 것을 그대로 쓴다.
# 판정은 필수 요지만으로 하고, 보조 요지는 충족 여부만 따로 기록한다(보조_충족).
PROMPT_TIERED = """너는 사내 규정 질의응답 답변을 채점한다. 질문, 채점 기준(필수 요지, 보조 요지, 금지 내용, 규정 근거 인용, 답 가능 여부), 시스템 답변이 주어진다.
필수 요지는 질문에 답하려면 반드시 들어가야 하는 내용이고, 보조 요지는 있으면 좋지만 없어도 되는 부가 내용이다(사람이 미리 나눴다).

채점 규칙
- 요지_충족: 필수 요지 각 항목을 답변이 담았는지 순서대로 true/false. 표현이 달라도 뜻이 같으면 true. 숫자·기간·대상이 다르면 false.
- 보조_충족: 보조 요지 각 항목을 답변이 담았는지 순서대로 true/false (같은 기준). 보조 요지가 없으면 빈 목록.
- 금지_위반: 금지 내용 중 하나라도 답변에 들어 있으면 true. 필수 요지와 반대되는 단정(틀린 숫자, 없는 제도를 있다고 함)도 true.
- 판정: 정답 / 부분 / 오답 / 답못함 중 하나. **보조 요지는 판정에 쓰지 않는다.**
  - 정답: 필수 요지를 모두 담고 금지_위반이 없다. 보조 요지는 빠져도 정답이다. 답변에 근거 있는 추가 정보가 있어도 감점하지 않는다.
  - 부분: 필수 요지 일부만 담았고 금지_위반이 없다.
  - 오답: 금지_위반이 있거나, 필수 요지와 다른 내용으로 답했다.
  - 답못함: "확인되지 않음", "관련 내용 없음"처럼 답을 하지 않았다. 단, 답 가능 여부가 '불가'이거나 필수 요지가 "규정에서 확인되지 않음"인 질문에서 그렇게 답했으면 정답이다.
- 이유: 한 문장.
출력은 JSON 하나만: {"요지_충족": [true, false], "보조_충족": [true], "금지_위반": false, "판정": "부분", "이유": "..."}"""


def criteria_tiered(g):
    """단계 채점용 기준 글: 정답 요지를 [필수 요지]로, 정답요지_보조를 [보조 요지]로 따로 적는다."""
    base = criteria(g).replace("[정답 요지]", "[필수 요지]", 1)
    aux = "\n".join(f"{i}. {p}" for i, p in enumerate(g.get("정답요지_보조") or [], 1)) or "- 없음"
    return base.replace("\n[금지 내용]", f"\n[보조 요지]\n{aux}\n[금지 내용]", 1)


def criteria(g):
    ev = g.get("근거") or []
    ev = ev if isinstance(ev, list) else ast.literal_eval(ev)
    quotes = [f"- {e.get('규정')} {e.get('조항')}: {e.get('인용')}" for e in ev]
    return (f"[질문] {g['q']}\n[답 가능 여부] {g.get('답가능여부')}\n"
            f"[정답 요지]\n" + "\n".join(f"{i}. {p}" for i, p in enumerate(g.get("정답요지") or [], 1)) +
            "\n[금지 내용]\n" + ("\n".join(f"- {x}" for x in g.get("금지내용") or []) or "- 없음") +
            "\n[규정 근거 인용]\n" + ("\n".join(quotes) or "- 없음"))


def judge(model, g, rec, prompt=PROMPT, crit=criteria):
    answer = rec.get("answer") or f"(실행 오류: {rec.get('error')})"
    msgs = [("system", prompt), ("human", crit(g) + f"\n\n[시스템 답변]\n{answer}")]
    for _ in range(3):
        try:
            text = model.invoke(msgs).content.strip()
            text = text[text.find("{"):text.rfind("}") + 1]
            out = json.loads(text)
            if out.get("판정") in ("정답", "부분", "오답", "답못함"):
                return out
        except Exception as e:  # noqa: BLE001
            last = repr(e)
    return {"판정": "채점실패", "이유": locals().get("last", "형식 오류")}


def main(tags, questions="eval/answer_eval.jsonl", mode="strict"):
    """mode: strict(정답 요지 전부 기준, judged.jsonl) / question(질문 중심, judged_q.jsonl)
    / tiered(사람이 나눈 필수·보조 요지, 필수만으로 판정, judged_t.jsonl — 평가셋에 정답요지_보조가 있어야 한다)"""
    prompt, out_name, crit = {"question": (PROMPT_QUESTION, "judged_q.jsonl", criteria),
                              "tiered": (PROMPT_TIERED, "judged_t.jsonl", criteria_tiered)}.get(mode, (PROMPT, "judged.jsonl", criteria))
    gold = {g["id"]: g for g in (json.loads(l) for l in open(questions, encoding="utf-8") if l.strip())}
    model = ChatOpenAI(model=JUDGE_MODEL, temperature=0)
    for tag in tags:
        runs = [json.loads(l) for l in open(f"eval/answer_runs/{tag}/runs.jsonl", encoding="utf-8") if l.strip()]
        with ThreadPoolExecutor(8) as ex:
            results = list(ex.map(lambda r: judge(model, gold[r["id"]], r, prompt, crit), runs))
        with open(f"eval/answer_runs/{tag}/{out_name}", "w", encoding="utf-8") as f:
            for r, j in zip(runs, results):
                f.write(json.dumps({"id": r["id"], "mode": r.get("mode"), **j}, ensure_ascii=False) + "\n")
        counts = {k: sum(1 for j in results if j.get("판정") == k) for k in ("정답", "부분", "오답", "답못함", "채점실패")}
        print(tag, f"채점({mode})", len(results), counts)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("tags", nargs="+")
    ap.add_argument("--questions", default="eval/answer_eval.jsonl", help="채점 기준 평가셋 (답변 실행에 쓴 파일과 같게)")
    ap.add_argument("--mode", choices=["strict", "question", "tiered"], default="strict",
                    help="question: 질문이 묻지 않은 부가 요지는 빠져도 정답 (judged_q.jsonl에 저장) / "
                         "tiered: 평가셋의 정답요지(필수)만으로 판정, 정답요지_보조는 보조_충족으로만 기록 (judged_t.jsonl)")
    args = ap.parse_args()
    main(args.tags, args.questions, args.mode)
