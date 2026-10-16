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

from config import BASE_DIR

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv()
os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ["LANGSMITH_TRACING"] = "false"

GOLD = os.path.join(BASE_DIR, "eval", "answer_eval.jsonl")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--no-plan", action="store_true", help="질문 나누기를 끄고 실행 (전후 비교용)")
    ap.add_argument("--plan", action="store_true", help="config와 상관없이 질문 나누기를 켜고 실행 (전후 비교용)")
    ap.add_argument("--questions", default=GOLD, help="답변 평가셋 (예: eval/answer_eval_100.jsonl)")
    ap.add_argument("--asker", action="store_true", help="평가셋의 '질문자' 칸을 로그인한 질문자 정보처럼 답변 모델에 넘긴다")
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
    from config import QUERY_PLAN
    assistant = Assistant(use_plan=True if args.plan else (False if args.no_plan else QUERY_PLAN))
    for g in gold:
        if g["id"] in done:
            continue
        t = time.time()
        try:
            # 화면처럼 스트리밍으로 받고, 답을 먼저 받은 뒤 뒤에서 하는 검사를 기다린다 (시간을 나눠 기록하려고)
            r = assistant.ask(g["q"], on_token=lambda piece: None, wait_checks=False,
                              asker=g.get("질문자") if args.asker else None)
            timing = dict(r.get("timing") or {})
            r = assistant.finish(r)
            timing = r.get("timing") or timing
            rec = {"id": g["id"], "mode": r["mode"], "answer": r["answer"], "withheld": r.get("withheld"),
                   "warnings": r.get("warnings", []), "glossary_excluded": r.get("glossary_excluded", []),
                   "entries": [{"문서명": e["문서명"], "article": e["article"], "상태": e["조문상태"], "역할": e.get("역할"), "match": e.get("match")} for e in r["entries"]],
                   "top_score": r.get("top_score"), "plan": r.get("plan"), "retried": r.get("retried"), "gap": r.get("gap"),
                   # 질문부터 초: 첫 글자(스트리밍) / 답 완성(사용자가 답을 다 받음) / 근거 검사까지 끝
                   "t_first_token": _round(timing.get("first_token")), "t_answer": _round(timing.get("answer")),
                   "t_checks": _round(timing.get("checks"))}
        except Exception as e:
            rec = {"id": g["id"], "error": repr(e)[:300]}
        rec["sec"] = round(time.time() - t, 1)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(g["id"], rec.get("mode", "ERROR"), rec["sec"], flush=True)
    assistant.close()


def _round(x):
    return None if x is None else round(x, 1)


if __name__ == "__main__":
    main()
