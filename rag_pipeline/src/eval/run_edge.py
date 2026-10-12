"""답 없음·애매함 질문 평가. eval/questions_v2_edge.jsonl을 실제 답변 파이프라인(Assistant)으로 돌린다.

검색 Hit@k로는 잴 수 없는 것을 본다: "모른다고 답해야 할 때 실제로 모른다고 하는가?"

기대값
  없음 : 규정에 전혀 없는 질문. mode가 none이면 성공, answer/partial이면 추측했을 위험이 있다.
  부분 : 조문은 있지만 구체적인 수치가 표·부칙처럼 색인되지 않은 곳에만 있는 질문.
         mode가 none이나 partial(관련 조문만 제시)이면 적절하다. answer인데 숫자를
         만들어 냈다면 추측(할루시네이션)일 가능성이 크므로 직접 읽어서 확인해야 한다.

실행 (rag_pipeline 폴더에서):
  uv run python -m src.eval.run_edge
"""
import json
import os
import sys

from config import BASE_DIR
from src.answer.pipeline import Assistant

sys.stdout.reconfigure(encoding="utf-8")

QUESTIONS = os.path.join(BASE_DIR, "eval", "questions_v2_edge.jsonl")


def load():
    with open(QUESTIONS, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def main():
    questions = load()
    a = Assistant()
    ok = 0
    for q in questions:
        r = a.ask(q["q"])
        # "없음" 기대는 none만 적절하다. "부분" 기대는 none이나 partial(추측 없이 관련 조문만 제시) 모두 적절하다.
        acceptable = {"없음": ("none",), "부분": ("none", "partial")}[q["기대"]]
        verdict = "적절" if r["mode"] in acceptable else "확인 필요(추측 위험)"
        if r["mode"] in acceptable:
            ok += 1
        score = f"{r['top_score']:.3f}" if r["top_score"] is not None else "-"
        print(f"\n[{verdict}] {q['q']}")
        print(f"  기대: {q['기대']}  실제 모드: {r['mode']}  점수: {score}")
        print(f"  메모: {q['메모']}")
        print(f"  답변: {r['answer'][:150]}".replace("\n", " "))
    a.close()
    print(f"\n적절하게 처리: {ok}/{len(questions)}")


if __name__ == "__main__":
    main()
