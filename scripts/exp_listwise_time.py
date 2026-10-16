"""리스트와이즈 리랭킹 호출 시간(질문 하나씩 차례로)과 같은 입력의 결과 흔들림을 잰다.

사용(프로젝트 루트에서, PG_*로 색인 DB를 고른다):
  python scripts/exp_listwise_time.py --n 30
전부 교체(후보 30개, RRF 순서)와 보완(BGE 상위 16개)을 질문마다 두 번씩 호출해 초와 앞 8개 선택 일치율을 낸다.
"""
import argparse
import json
import os
import pickle
import re
import statistics as st
import sys
import time

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")
from dotenv import load_dotenv  # noqa: E402

load_dotenv()
os.environ["LANGCHAIN_TRACING_V2"] = "false"

import config  # noqa: E402
from langchain_openai import ChatOpenAI  # noqa: E402
from src.search.search import Searcher  # noqa: E402

PROMPT = """한국벤처투자 내부 규정 검색 결과를 다시 줄 세운다.
직원 질문에 답하려면 읽어야 하는 조문을 중요한 순서대로 최대 {n}개 고른다.
- 질문에 직접 답하는 조문을 앞에 두고, 답의 조건·예외·정의·기준표를 알려 주는 조문도 포함한다.
- 같은 규정의 비슷한 조문만 몰아 고르지 말고, 질문에 꼭 필요한 것만 고른다.
- 후보 번호만 JSON으로 답한다: {{"순서": [번호, 번호, ...]}}

질문: {q}

후보:
{cands}"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30)
    args = ap.parse_args()
    pre = pickle.load(open(os.path.join("eval", "runs", "bakeoff_pool_cache.pkl"), "rb"))[:args.n]
    s = Searcher()
    llm = ChatOpenAI(model=config.JUDGE_MODEL, temperature=0, timeout=120, max_retries=2)

    def line(i, h):
        r = s.row_info[h["chunk_id"]]
        title = s.documents.get(r["doc_id"], {}).get("title") or r["doc_id"]
        return f"[{i}] {title} {r['article']} | " + re.sub(r"\s+", " ", r["body_search"] or "")[:220]

    def call(q, cands):
        text = PROMPT.format(n=12, q=q, cands="\n".join(line(i, h) for i, h in enumerate(cands, 1)))
        t = time.time()
        out = llm.invoke(text).content
        sec = time.time() - t
        m = re.search(r"\{.*\}", out, re.S)
        try:
            ids = [int(x) for x in json.loads(m.group(0))["순서"]]
        except Exception:  # noqa: BLE001
            ids = []
        return sec, [cands[i - 1]["chunk_id"] for i in ids if 1 <= i <= len(cands)], len(text)

    res = {"전부 교체": [], "보완(16개)": []}
    for i, p in enumerate(pre, 1):
        q = p["g"]["q"]
        bge = s.rerank(q, [dict(h) for h in p["pool"]])
        for name, cands in (("전부 교체", p["pool"]), ("보완(16개)", bge[:16])):
            a, b = call(q, cands), call(q, cands)
            res[name].append((a[0], b[0], a[1][:8] == b[1][:8], len(set(a[1][:8]) & set(b[1][:8])), a[2]))
        if i % 10 == 0:
            print(f"  {i}/{len(pre)}", flush=True)
    for name, rows in res.items():
        secs = sorted(x for r in rows for x in r[:2])
        print(f"{name}: 호출 {len(secs)}번 | 평균 {st.mean(secs):.1f}초 중앙값 {st.median(secs):.1f}초 "
              f"90퍼센타일 {secs[int(len(secs) * 0.9) - 1]:.1f}초 최대 {secs[-1]:.1f}초 | 입력 평균 {st.mean(r[4] for r in rows):.0f}자 | "
              f"같은 입력 두 번: 앞 8개 순서까지 같음 {sum(r[2] for r in rows)}/{len(rows)}, "
              f"앞 8개 집합 평균 겹침 {st.mean(r[3] for r in rows):.1f}/8")
    s.close()


if __name__ == "__main__":
    main()
