"""조문마다 직원이 물을 법한 구어체 예상 질문을 Luna로 만든다 (doc2query). 조문 본문만 입력으로 쓰고 평가 질문·정답은 쓰지 않는다.

사용(프로젝트 루트에서, PG_*로 색인 DB를 고른다):
  python scripts/gen_doc2query.py --out eval/runs/doc2query_questions.json

단위: 같은 규정의 같은 조(별표는 번호별)에 속한 색인 청크를 합친 것. 결과: {"규정id:조키": {"규정": ..., "조": ..., "질문": [...]}}
"""
import argparse
import collections
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")
from dotenv import load_dotenv  # noqa: E402

load_dotenv()
os.environ["LANGCHAIN_TRACING_V2"] = "false"

import psycopg  # noqa: E402
from langchain_openai import ChatOpenAI  # noqa: E402

import config  # noqa: E402

TITLE = re.compile(r"제\d+조(?:의\d+)?\(([^)]+)\)")
PROMPT = """너는 한국벤처투자 직원이 사내 규정 검색창에 쓸 법한 질문을 만든다.
아래 조문 {n}개 각각에 대해, 이 조문을 읽어야 답할 수 있는 질문을 6개씩 만든다.
- 규정을 잘 모르는 직원이 말하듯 구어체로 쓴다 (예: "법카 영수증 잃어버렸는데 어떻게 해요?").
- 질문마다 상황이나 쓰는 말을 다르게 한다. 규정 용어를 그대로 쓰는 질문은 2개 이하로 하고, 나머지는 일상어, 줄임말, 구체적인 사례로 쓴다.
- 이 조문의 내용으로 답할 수 있어야 한다. 조 번호나 규정 이름을 질문에 쓰지 않는다. 조문에 없는 사실을 지어내지 않는다.
- 출력은 JSON 하나만: {{"결과": [{{"번호": 1, "질문": ["...", ...]}}, ...]}}

{units}"""


def key(doc, art):
    m = re.match(r"\s*제\s*(\d+)\s*조(?:\s*의\s*(\d+))?", art or "")
    if m:
        return f"{doc}:제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "")
    m = re.match(r"\s*(별표\s*\d+(?:\s*의\s*\d+)?)", art or "")
    return f"{doc}:" + (re.sub(r"\s+", "", m.group(1)) if m else re.sub(r"\s+", "", art or ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join("eval", "runs", "doc2query_questions.json"))
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    c = psycopg.connect(host=os.environ["PG_HOST"], port=os.environ["PG_PORT"], dbname=os.environ["PG_DATABASE"],
                        user=os.environ["PG_USER"], password=os.environ["PG_PASSWORD"], connect_timeout=5)
    cur = c.cursor()
    cur.execute("select d.title, c.doc_id, c.chunk_id, c.article, c.body_search from chunks c join documents d using(doc_id) where c.is_indexed order by c.chunk_id")
    units = collections.OrderedDict()
    for title, doc, cid, art, body in cur.fetchall():
        k_ = key(doc, art)
        u = units.setdefault(k_, {"규정": title, "조": re.sub(r"\s+", "", (re.match(r"\s*(제\s*\d+\s*조(?:\s*의\s*\d+)?|별표\s*\d+(?:\s*의\s*\d+)?)", art or "") or [art or ""])[0]), "제목": "", "본문": []})
        m = TITLE.search((body or "")[:200])
        if m and not u["제목"]:
            u["제목"] = m.group(1)
        u["본문"].append((body or "").strip())
    items = list(units.items())
    if args.limit:
        items = items[:args.limit]
    done = json.load(open(args.out, encoding="utf-8")) if os.path.exists(args.out) else {}
    todo = [(k_, u) for k_, u in items if k_ not in done]
    print(f"조문 단위 {len(items)}개, 이미 만든 것 {len(done)}개, 남은 것 {len(todo)}개", flush=True)
    llm = ChatOpenAI(model=config.JUDGE_MODEL, timeout=180, max_retries=2)

    def run(batch):
        text = "\n\n".join(f"[{i}] {u['규정']} {u['조']}" + (f"({u['제목']})" if u["제목"] else "") + "\n" + "\n".join(u["본문"])[:2000]
                           for i, (k_, u) in enumerate(batch, 1))
        for _ in range(2):
            try:
                out = llm.invoke(PROMPT.format(n=len(batch), units=text)).content
                res = json.loads(re.search(r"\{.*\}", out, re.S).group(0))["결과"]
                got = {int(r["번호"]): [q.strip() for q in r["질문"] if isinstance(q, str) and q.strip()] for r in res}
                if all(got.get(i) for i in range(1, len(batch) + 1)):
                    return [(k_, {"규정": u["규정"], "조": u["조"], "질문": got[i]}) for i, (k_, u) in enumerate(batch, 1)]
            except Exception:  # noqa: BLE001
                pass
        return []

    batches = [todo[i:i + args.batch] for i in range(0, len(todo), args.batch)]
    fail = 0
    with ThreadPoolExecutor(8) as ex:
        for n, res in enumerate(ex.map(run, batches), 1):
            if not res:
                fail += 1
            for k_, v in res:
                done[k_] = v
            if n % 25 == 0 or n == len(batches):
                json.dump(done, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
                print(f"  {n}/{len(batches)} 묶음 (실패 {fail}), 질문 {sum(len(v['질문']) for v in done.values())}개", flush=True)
    json.dump(done, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    miss = [k_ for k_, _ in items if k_ not in done]
    print(f"완료: 조문 {len(done)}개, 질문 {sum(len(v['질문']) for v in done.values())}개, 못 만든 조문 {len(miss)}개")
    c.close()


if __name__ == "__main__":
    main()
