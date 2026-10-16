"""k별 Luna 선택 호출 시간을 질문 하나씩 차례로 잰다 (scripts/exp_listwise_topk.py와 같은 프롬프트·후보).

사용(프로젝트 루트에서, PG_*로 색인 DB를 고른다):
  python scripts/exp_listwise_time_k.py --n 20
BGE 16개 조 → k개 선택, RRF 30개 조 → k개 선택(Luna 단독)을 k=5·8·10으로 질문마다 한 번씩 호출한다.
"""
import argparse
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
from src.search import glossary  # noqa: E402
from src.search.search import Searcher, group_by_article  # noqa: E402

PROMPT = """한국벤처투자 내부 규정 검색 결과에서 직원 질문에 답하는 데 필요한 조문을 정확히 {k}개 고른다.
- 질문에 직접 답하는 조문을 앞에 두고, 답의 조건·예외·정의·기준표를 알려 주는 조문도 포함한다.
- 같은 규정의 비슷한 조문만 몰아 고르지 말고, 질문에 꼭 필요한 것만 고른다.
- 후보 번호만 JSON으로 답한다: {{"순서": [번호, 번호, ...]}}

질문: {q}

후보:
{cands}"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    args = ap.parse_args()
    pre = pickle.load(open(os.path.join("eval", "runs", "bakeoff_pool_cache.pkl"), "rb"))[:args.n]
    s = Searcher()
    s.glossary_filter = True
    llm = ChatOpenAI(model=config.JUDGE_MODEL, temperature=0, timeout=120, max_retries=2)

    def line(i, h):
        r = s.row_info[h["chunk_id"]]
        title = s.documents.get(r["doc_id"], {}).get("title") or r["doc_id"]
        return f"[{i}] {title} {r['article']} | " + re.sub(r"\s+", " ", r["body_search"] or "")[:220]

    def arts(q, hits):
        hits, _ = glossary.filter_candidates(q, hits, s.glossary)
        return group_by_article(hits)

    secs = {}
    for i, p in enumerate(pre, 1):
        q = p["g"]["q"]
        bge = arts(q, s.rerank(q, [dict(h) for h in p["pool"]]))[:16]
        rrf = arts(q, [dict(h) for h in p["pool"]])[:30]
        for name, cands in (("BGE 16개 → Luna", bge), ("Luna 단독(30개)", rrf)):
            text_base = "\n".join(line(j, h) for j, h in enumerate(cands, 1))
            for k in (5, 8, 10):
                t = time.time()
                llm.invoke(PROMPT.format(k=k, q=q, cands=text_base))
                secs.setdefault((name, k), []).append(time.time() - t)
        if i % 5 == 0:
            print(f"  {i}/{len(pre)}", flush=True)
    for (name, k), v in secs.items():
        v2 = sorted(v)
        print(f"{name:16s} k={k:2d} | 평균 {st.mean(v):.1f}초 중앙값 {st.median(v):.1f}초 90퍼센타일 {v2[int(len(v2) * 0.9) - 1]:.1f}초 최대 {v2[-1]:.1f}초 ({len(v)}번)")
    s.close()


if __name__ == "__main__":
    main()
