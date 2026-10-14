"""바꿔쓰기 변형을 질문마다 한 번씩 만들어 캐시 파일로 저장한다 (scripts/exp_retrieval_variants.py의 --rewrite-cache 입력).

사용(프로젝트 루트에서):
  python scripts/gen_rewrites.py --questions eval/answer_eval_100.jsonl --style terms --out eval/runs/rewrite-terms-only.json
  python scripts/gen_rewrites.py --questions eval/answer_eval_100.jsonl --style none  --out eval/runs/rewrite-none.json

style
  terms : 구어체를 규정 용어로 바꾸기만 한다. 주제어는 덧붙이지 않는다(현재 REWRITE_PROMPT는 주제어 2~4개를 덧붙인다).
  none  : 바꿔쓰기 없음(빈 문자열). 원질문과 용어집 신호어만으로 후보를 찾는다.
"""
import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")
from dotenv import load_dotenv  # noqa: E402

load_dotenv()
os.environ["LANGCHAIN_TRACING_V2"] = "false"

TERMS_PROMPT = """직원이 사내 규정 검색창에 구어체로 쓴 질문을, 규정 문서에 실제로 쓰이는 공식 용어로 바꿔 검색어 한 줄로 만든다.
- 줄임말, 속어, 외래어, 일상어를 규정에서 쓰는 정식 용어로 바꾼다.
- 질문에 없는 주제어, 사실, 수치를 덧붙이지 않는다. 답을 쓰지 않는다.
- 출력은 검색어 한 줄만 쓴다. 설명, 따옴표, 번호를 쓰지 않는다."""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", required=True)
    ap.add_argument("--style", choices=["terms", "none"], required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    qs = [json.loads(line)["q"] for line in open(args.questions, encoding="utf-8") if line.strip()]
    if args.style == "none":
        cache = {q: "" for q in qs}
    else:
        from src.answer.generate import Generator
        gen = Generator()

        def one(q):
            text = gen._ask(TERMS_PROMPT, q).strip().strip('"').splitlines()
            return q, (text[0].strip() if text else "")

        with ThreadPoolExecutor(8) as ex:
            cache = dict(ex.map(one, qs))
    json.dump(cache, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"저장: {args.out} ({len(cache)}개)")


if __name__ == "__main__":
    main()
