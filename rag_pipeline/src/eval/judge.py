"""LLM-as-a-Judge: 검색이 아니라 '최종 답변이 실제로 쓸모 있는가'를 잰다.

Hit@k·MRR은 "정답 조문을 찾았는가"만 본다. 이 스크립트는 그 조문을 찾은 뒤 모델이 만든
**답변 자체**가 질문에 제대로 답했는지, 근거에 없는 내용을 지어냈는지를 별도 모델 호출로 채점한다.
근거 본문(정답 조문)은 검색 시스템을 거치지 않고 DB에서 직접 가져온다 — 채점 기준이
검색 결과에 영향받지 않게 하기 위해서다.

실행 (rag_pipeline 폴더에서):
  uv run python -m src.eval.judge                              # eval/questions_v2.jsonl + v3
  uv run python -m src.eval.judge --questions eval/questions.jsonl --limit 20
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

JUDGE_PROMPT = """너는 사내 규정 질의응답 시스템의 답변 품질을 채점하는 평가자다.
질문, 정답 근거(실제 규정 조문 원문), 시스템이 실제로 낸 답변이 주어진다.

다음 기준으로 채점한다.
- 정확성(0~5): 답변이 정답 근거의 핵심 내용을 담고 있는가. 5는 완전히 정확, 0은 완전히 틀리거나 무관함.
  질문의 일부만 다뤘으면 중간 점수를 준다.
- 근거충실(참/거짓): 답변에 정답 근거에 없는 내용(지어낸 숫자, 조건, 절차 등)이 있으면 참.
  정답 근거에 없는 내용이 전혀 없으면 거짓.
- 이유: 한두 문장으로 왜 그 점수를 줬는지.

출력은 JSON 객체 하나만 쓴다. 예: {{"정확성": 4, "근거충실_위반": false, "이유": "..."}}
설명은 이유 필드 안에만 쓰고, 다른 텍스트는 쓰지 않는다."""


def judge_one(model, question, gold_text, answer):
    prompt = ChatPromptTemplate.from_messages([("system", JUDGE_PROMPT), ("human", "{input}")])
    chain = prompt | model
    user = f"질문: {question}\n\n정답 근거:\n{gold_text}\n\n시스템 답변:\n{answer}"
    text = chain.invoke({"input": user}).content.strip()
    try:
        raw = json.loads(text)
    except (ValueError, TypeError):
        return {"정확성": None, "근거충실_위반": None, "이유": f"채점 응답 해석 실패: {text[:100]}"}
    if not isinstance(raw, dict):
        return {"정확성": None, "근거충실_위반": None, "이유": "채점 응답이 JSON 객체가 아님"}
    return raw


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
    ])
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    questions = load_questions(args.questions)
    if args.limit:
        questions = questions[:args.limit]

    store = PgStore()
    assistant = Assistant()
    judge_model = ChatOpenAI(model=OPENAI_MODEL, temperature=0)

    scores, violations = [], 0
    low = []
    for q in questions:
        # 정답 근거는 검색을 거치지 않고 DB에서 바로 가져온다 (채점 기준의 독립성 유지).
        # 정답 청크가 속한 조의 다른 항(형제 항)까지 같이 준다. 안 그러면 시스템이 합법적으로
        # 가져온 같은 조의 다른 항·관련 조문을 "근거 밖"으로 오판하는 측정 오류가 생긴다.
        gold_rows = store.get(q["정답"])
        seen = {r["chunk_id"] for r in gold_rows}
        extra = []
        for r in gold_rows:
            for sib in store.siblings(r["doc_id"], r["article"], exclude_chunk_id=r["chunk_id"]):
                if sib["chunk_id"] not in seen:
                    seen.add(sib["chunk_id"])
                    extra.append(sib)
        gold_text = "\n\n".join(r["body"] for r in gold_rows + extra)

        result = assistant.ask(q["q"])
        verdict = judge_one(judge_model, q["q"], gold_text, result["answer"])

        score = verdict.get("정확성")
        if score is not None:
            scores.append(score)
        if verdict.get("근거충실_위반"):
            violations += 1
        print(f"\n[{q['q']}]")
        print(f"  모드: {result['mode']}  정확성: {score}  근거충실 위반: {verdict.get('근거충실_위반')}")
        print(f"  이유: {verdict.get('이유')}")
        if score is not None and score <= 2:
            low.append((q["q"], score, verdict.get("이유")))

    assistant.close()
    store.close()

    print(f"\n=== 요약 (질문 {len(questions)}개) ===")
    if scores:
        print(f"평균 정확성: {sum(scores) / len(scores):.2f} / 5  (채점된 질문 {len(scores)}개)")
    print(f"근거충실 위반(지어낸 내용 의심): {violations}개")
    if low:
        print("\n낮은 점수(2점 이하):")
        for q, s, reason in low:
            print(f"  [{s}] {q}  — {reason}")


if __name__ == "__main__":
    main()
