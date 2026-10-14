"""이미 만든 답변(eval/answer_runs/<tag>/runs.jsonl)에 RAGAS 스타일 지표를 매긴다. 답변을 새로 만들지 않는다.

지표 정의와 프롬프트는 src/eval/ragas_style.py 그대로 (채점 모델 config.JUDGE_MODEL):
  faithfulness(충실도)    : 답변 문장 중 실제로 넘긴 근거에 있는 문장의 비율 (모드가 answer일 때만)
  answer_relevancy(관련성): 답변이 질문의 요지를 다뤘는가 (0~1)
  context_recall(문맥재현): 정답 근거 조문의 핵심 내용이 넘긴 근거 안에 있었는가 (0~1, 검색 정답이 있는 문항만)

넘긴 근거 본문은 runs.jsonl의 entries(문서명, 조항)로 out/chunks.jsonl에서 찾는다. 외부 법령 근거는 본문이 없어 뺀다.
정답 근거는 검색 정답 파일(scripts/build_190_retrieval.py로 만든 것)의 청크 본문이다.

실행 (저장소 루트에서):
  uv run python -m src.eval.ragas_from_runs article_luna_100 --retrieval eval/questions_100_retrieval.jsonl
결과: eval/answer_runs/<tag>/ragas.jsonl, 요약 출력
"""
import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv

from config import BASE_DIR, JUDGE_MODEL, OUT_DIR

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv()
os.environ["LANGCHAIN_TRACING_V2"] = "false"

from langchain_openai import ChatOpenAI  # noqa: E402

from src.eval.ragas_style import answer_relevancy, context_recall, faithfulness  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tag")
    ap.add_argument("--retrieval", default=os.path.join(BASE_DIR, "eval", "questions_100_retrieval.jsonl"))
    ap.add_argument("--questions", default=os.path.join(BASE_DIR, "eval", "answer_eval_100.jsonl"))
    args = ap.parse_args()

    chunks = [json.loads(line) for line in open(os.path.join(OUT_DIR, "chunks.jsonl"), encoding="utf-8") if line.strip()]
    by_label = {(c["문서명"], c["조항"]): c["본문"] for c in chunks}
    by_id = {c["id"]: c["본문"] for c in chunks}
    questions = {q["id"]: q for q in (json.loads(line) for line in open(args.questions, encoding="utf-8") if line.strip())}
    gold = {g["id"]: g["정답"] for g in (json.loads(line) for line in open(args.retrieval, encoding="utf-8") if line.strip())}
    run_dir = os.path.join(BASE_DIR, "eval", "answer_runs", args.tag)
    runs = [json.loads(line) for line in open(os.path.join(run_dir, "runs.jsonl"), encoding="utf-8") if line.strip()]
    model = ChatOpenAI(model=JUDGE_MODEL, temperature=0)

    def score(r):
        q = questions[r["id"]]
        bodies = [by_label[(e["문서명"], e["article"])] for e in r.get("entries", []) if (e["문서명"], e["article"]) in by_label]
        context = "\n\n".join(bodies) or "(근거 없음)"
        gold_text = "\n\n".join(by_id[c] for c in gold.get(r["id"], []) if c in by_id)
        return {
            "id": r["id"], "mode": r.get("mode"),
            "faithfulness": faithfulness(model, r["answer"], context) if r.get("mode") == "answer" else None,
            "answer_relevancy": answer_relevancy(model, q["q"], r["answer"]),
            "context_recall": context_recall(model, gold_text, context) if gold_text else None,
        }

    with ThreadPoolExecutor(8) as ex:
        rows = list(ex.map(score, [r for r in runs if "answer" in r]))
    with open(os.path.join(run_dir, "ragas.jsonl"), "w", encoding="utf-8") as f:
        f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)

    print(f"{args.tag}: 질문 {len(rows)}개 (채점 모델 {JUDGE_MODEL})")
    for key, label in [("faithfulness", "충실도"), ("answer_relevancy", "관련성"), ("context_recall", "문맥재현")]:
        vals = [r[key] for r in rows if r[key] is not None]
        print(f"  {label}({key}): {sum(vals) / len(vals):.3f} (n={len(vals)})" if vals else f"  {label}: 값 없음")


if __name__ == "__main__":
    main()
