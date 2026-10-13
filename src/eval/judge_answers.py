"""190문항 답변 채점 (LLM 채점, config.JUDGE_MODEL, temperature 0). 어느 색인의 답인지 채점 모델에 알리지 않는다.

실행 (저장소 루트에서): uv run python -m src.eval.judge_answers <tag> [<tag> ...]
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


def criteria(g):
    ev = g.get("근거") or []
    ev = ev if isinstance(ev, list) else ast.literal_eval(ev)
    quotes = [f"- {e.get('규정')} {e.get('조항')}: {e.get('인용')}" for e in ev]
    return (f"[질문] {g['q']}\n[답 가능 여부] {g.get('답가능여부')}\n"
            f"[정답 요지]\n" + "\n".join(f"{i}. {p}" for i, p in enumerate(g.get("정답요지") or [], 1)) +
            "\n[금지 내용]\n" + ("\n".join(f"- {x}" for x in g.get("금지내용") or []) or "- 없음") +
            "\n[규정 근거 인용]\n" + ("\n".join(quotes) or "- 없음"))


def judge(model, g, rec):
    answer = rec.get("answer") or f"(실행 오류: {rec.get('error')})"
    msgs = [("system", PROMPT), ("human", criteria(g) + f"\n\n[시스템 답변]\n{answer}")]
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


def main(tags):
    gold = {g["id"]: g for g in (json.loads(l) for l in open("eval/answer_eval.jsonl", encoding="utf-8") if l.strip())}
    model = ChatOpenAI(model=JUDGE_MODEL, temperature=0)
    for tag in tags:
        runs = [json.loads(l) for l in open(f"eval/answer_runs/{tag}/runs.jsonl", encoding="utf-8") if l.strip()]
        with ThreadPoolExecutor(8) as ex:
            results = list(ex.map(lambda r: judge(model, gold[r["id"]], r), runs))
        with open(f"eval/answer_runs/{tag}/judged.jsonl", "w", encoding="utf-8") as f:
            for r, j in zip(runs, results):
                f.write(json.dumps({"id": r["id"], "mode": r.get("mode"), **j}, ensure_ascii=False) + "\n")
        counts = {k: sum(1 for j in results if j.get("판정") == k) for k in ("정답", "부분", "오답", "답못함", "채점실패")}
        print(tag, "채점", len(results), counts)


if __name__ == "__main__":
    main(sys.argv[1:])
