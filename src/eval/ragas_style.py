"""RAGAS 스타일 지표. ragas 패키지는 설치하지 않고, 핵심 지표 정의만 우리 프롬프트로 구현한다.

이유 (답변_설계.md 9절과 같은 판단): ragas는 LangChain/임베딩 의존성이 추가로 필요하고,
faithfulness는 이미 Generator.verify()·judge.py와 상당 부분 겹친다. 패키지를 통째로 들이는
대신 지표 정의만 빌려서 우리 데이터·프롬프트에 맞게 가볍게 구현한다.

구현한 지표 (0~1)
  faithfulness(충실도)   : 답변의 각 주장이 실제로 사용된 근거에서 나왔는지 (judge.py의 근거충실과 비슷하지만 0/1이 아니라 비율)
  answer_relevancy(관련성): 답변이 질문에 실제로 답했는지 (근거가 맞고 틀리고를 떠나, 질문의 요지를 다뤘는가)
  context_recall(문맥재현): 정답 근거(조 전체)의 핵심 내용이 검색된 근거 안에 들어 있었는가

실행 (프로젝트 루트에서):
  uv run python -m src.eval.ragas_style                              # 블라인드 세트(v2+v3, 기본값)
  uv run python -m src.eval.ragas_style --questions eval/questions.jsonl --limit 20
"""
import argparse
import json
import os

import sys

from config import BASE_DIR, OPENAI_MODEL
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from src.answer.pipeline import Assistant
from src.store.pgstore import PgStore

sys.stdout.reconfigure(encoding="utf-8")

FAITH_PROMPT = """답변을 문장 단위로 나눈다고 생각하고, 각 문장이 '실제로 사용된 근거'에
있는 내용에서 나온 것인지 따진다.

출력은 JSON 객체 하나: {{"총문장": N, "근거에_있는_문장": M}}
문장 수를 세기 애매하면 의미 단위(불릿 하나)로 세도 된다. 설명은 쓰지 않는다."""

RELEVANCY_PROMPT = """질문과 답변이 주어진다. 답변이 질문에서 실제로 묻는 것에 답했는지
0~1 사이 점수로 평가한다 (내용이 맞고 틀리고는 보지 않는다. 질문의 요지를 다뤘는지만 본다).
"확인되지 않습니다"처럼 모른다고 솔직히 답한 경우도 질문을 회피하지 않았으므로 관련성은 높게(0.8~1.0) 준다.
완전히 동문서답이면 0에 가깝게 준다.

출력은 JSON 객체 하나: {{"관련성": 0.0~1.0, "이유": "한 문장"}}"""

RECALL_PROMPT = """'정답 근거'(규정 조문 전체)와 '실제로 검색된 근거'가 주어진다.
정답 근거의 핵심 내용이 실제로 검색된 근거 안에 들어 있었는지 0~1 사이로 평가한다.
(시스템이 그 내용을 답변에 썼는지는 보지 않는다. 검색 단계에서 그 내용 자체를 가져왔는지만 본다.)

출력은 JSON 객체 하나: {{"문맥재현": 0.0~1.0, "이유": "한 문장"}}"""


def _ask_json(model, system, user):
    prompt = ChatPromptTemplate.from_messages([("system", system), ("human", "{input}")])
    try:
        raw = json.loads((prompt | model).invoke({"input": user}).content.strip())
        return raw if isinstance(raw, dict) else None
    except (ValueError, TypeError):
        return None


def faithfulness(model, answer, used_context):
    r = _ask_json(model, FAITH_PROMPT, f"답변:\n{answer}\n\n실제로 사용된 근거:\n{used_context}")
    if not r or not r.get("총문장"):
        return None
    return min(1.0, r.get("근거에_있는_문장", 0) / r["총문장"])


def answer_relevancy(model, question, answer):
    r = _ask_json(model, RELEVANCY_PROMPT, f"질문: {question}\n\n답변:\n{answer}")
    return (r or {}).get("관련성")


def context_recall(model, gold_text, retrieved_context):
    r = _ask_json(model, RECALL_PROMPT,
                  f"정답 근거:\n{gold_text}\n\n실제로 검색된 근거:\n{retrieved_context}")
    return (r or {}).get("문맥재현")


def load_questions(paths):
    rows = []
    for p in paths:
        with open(p, encoding="utf-8") as f:
            rows.extend(json.loads(line) for line in f if line.strip())
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", nargs="+", default=[
        os.path.join(BASE_DIR, "eval", "questions_v2.jsonl"),
        os.path.join(BASE_DIR, "eval", "questions_v3.jsonl"),
    ], help="질문 파일 경로 (여러 개 가능). 생략하면 블라인드 세트(v2+v3)")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    questions = load_questions(args.questions)
    if args.limit:
        questions = questions[:args.limit]

    store = PgStore()
    assistant = Assistant()
    model = ChatOpenAI(model=OPENAI_MODEL, temperature=0)

    rows = []
    for q in questions:
        result = assistant.ask(q["q"])
        used_context = "\n\n".join(e["body"] for e in result["entries"]) or "(근거 없음)"

        gold_rows = store.get(q["정답"])
        seen = {r["chunk_id"] for r in gold_rows}
        extra = []
        for r in gold_rows:
            for sib in store.siblings(r["doc_id"], r["article"], exclude_chunk_id=r["chunk_id"]):
                if sib["chunk_id"] not in seen:
                    seen.add(sib["chunk_id"])
                    extra.append(sib)
        gold_text = "\n\n".join(r["body"] for r in gold_rows + extra)

        faith = faithfulness(model, result["answer"], used_context) if result["mode"] == "answer" else None
        rel = answer_relevancy(model, q["q"], result["answer"])
        rec = context_recall(model, gold_text, used_context)

        rows.append({"q": q["q"], "mode": result["mode"], "faithfulness": faith,
                    "answer_relevancy": rel, "context_recall": rec})
        print(f"[{q['q']}]")
        print(f"  모드: {result['mode']}  충실도: {faith}  관련성: {rel}  문맥재현: {rec}")

    assistant.close()
    store.close()

    def avg(key):
        vals = [r[key] for r in rows if r[key] is not None]
        return (sum(vals) / len(vals), len(vals)) if vals else (None, 0)

    print(f"\n=== 요약 (질문 {len(rows)}개) ===")
    for key, label in [("faithfulness", "충실도(faithfulness)"),
                       ("answer_relevancy", "관련성(answer relevancy)"),
                       ("context_recall", "문맥재현(context recall)")]:
        v, n = avg(key)
        print(f"  {label}: {v:.2f} (n={n})" if v is not None else f"  {label}: 측정값 없음")


if __name__ == "__main__":
    main()
