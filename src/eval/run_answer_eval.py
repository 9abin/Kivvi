"""eval/answer_eval.jsonl(190문항)을 현재 설정으로 한 번씩 실행해 답변을 저장한다. 채점은 하지 않는다.

사용(rag_pipeline에서): uv run python -m src.eval.run_answer_eval --tag base [--limit N]
결과: eval/answer_runs/<tag>/runs.jsonl (이어 실행 가능: 이미 있는 id는 건너뛴다)
"""
import argparse
import json
import os
import sys
import time

from dotenv import load_dotenv

from config import BASE_DIR, TOP_K

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv()
os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ["LANGSMITH_TRACING"] = "false"

GOLD = os.path.join(BASE_DIR, "eval", "answer_eval.jsonl")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--k", type=int, default=TOP_K, help="검색에서 가져오는 청크 수 (기본: config.TOP_K)")
    ap.add_argument("--questions", default=GOLD, help="질문·정답 파일 (기본: eval/answer_eval.jsonl)")
    ap.add_argument("--no-plan", action="store_true", help="질문 나누기를 끄고 실행 (전후 비교용)")
    ap.add_argument("--serial", action="store_true", help="모델 호출을 동시에 하지 않고 순서대로 실행 (전후 시간 비교용)")
    ap.add_argument("--defer-checks", action="store_true", help="답을 먼저 받고 근거 검사·충돌 확인은 뒤에 끝낸다 (sec_answer가 답이 나온 시간)")
    args = ap.parse_args()

    gold = [json.loads(l) for l in open(args.questions, encoding="utf-8") if l.strip()]
    if args.limit:
        gold = gold[:args.limit]
    outdir = os.path.join(BASE_DIR, "eval", "answer_runs", args.tag)
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, "runs.jsonl")
    done = set()
    if os.path.exists(path):
        done = {json.loads(l)["id"] for l in open(path, encoding="utf-8")}

    from src.answer.pipeline import Assistant
    assistant = Assistant(use_plan=not args.no_plan, parallel=not args.serial, defer_checks=args.defer_checks)
    for g in gold:
        if g["id"] in done:
            continue
        t = time.time()
        try:
            r = assistant.ask(g["q"], k=args.k)
            sec_answer = round(time.time() - t, 1)  # 답 본문이 나온 시간 (defer_checks가 아니면 검사까지 끝난 시간)
            assistant.finish(r)
            rec = {"id": g["id"], "mode": r["mode"], "answer": r["answer"], "withheld": r.get("withheld"),
                   "warnings": r.get("warnings", []), "glossary_excluded": r.get("glossary_excluded", []),
                   "entries": [{"문서명": e["문서명"], "article": e["article"], "상태": e["조문상태"]} for e in r["entries"]],
                   "top_score": r.get("top_score"), "plan": r.get("plan"), "timing": r.get("timing"), "sec_answer": sec_answer}
        except Exception as e:
            rec = {"id": g["id"], "error": repr(e)[:300]}
        rec["sec"] = round(time.time() - t, 1)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(g["id"], rec.get("mode", "ERROR"), rec["sec"], flush=True)
    assistant.close()


if __name__ == "__main__":
    main()
