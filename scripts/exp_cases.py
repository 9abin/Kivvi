"""사례 단계 시험: 질문과 비슷한 사례의 연결 조문을 후보에 더한다 (정답 근거 Recall만 본다, LLM 호출 없음).

사용(프로젝트 루트에서, PG_*로 색인 DB를 고른다):
  python scripts/exp_cases.py --questions eval/answer_eval_100.jsonl

사례 라이브러리: eval/case_library_20261015.json (공개 징계·감사 사례 16건, 연결은 (규정, 조)). 평가 정답은 연결에 쓰지 않았다.
방식(실행 전에 정한 설정, 평가를 보고 바꾸지 않는다): 질문과 사례 '상황' 문장의 BGE-M3 코사인 유사도 상위 3개 중 0.55 이상인 사례를 고르고,
  그 사례가 연결한 조의 항들(최대 8개)을 다섯 번째 목록으로 가중 RRF에 더한다(가중치 = 벡터 가중치 0.7). 그 뒤는 기존과 같다.
보고: 전체 100문항 변화(후보 안 정답, BGE만 Recall), 사례가 작동한 질문 수, 사례가 연결한 조에 속한 정답(연결 가능 정답)과 그 밖의 정답을 나눠 본 변화.
"""
import argparse
import json
import os
import re
import sys

import numpy as np

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")
from dotenv import load_dotenv  # noqa: E402

load_dotenv()
os.environ["LANGCHAIN_TRACING_V2"] = "false"

import config  # noqa: E402
import src.search.search as ss  # noqa: E402
from src.search import glossary  # noqa: E402
from src.search.search import RERANK_TOP, Searcher, group_by_article  # noqa: E402

TOP_CASES = 3
MIN_SIM = 0.55
MAX_CHUNKS = 8
KS = (8, 10, 16)


def key(doc, art):
    m = re.match(r"제(\d+)조(?:의(\d+))?", art or "")
    if m:
        return f"{doc}:제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "")
    return f"{doc}:" + re.sub(r"\s+", "", art or "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", default="eval/answer_eval_100.jsonl")
    ap.add_argument("--rewrite-cache", default=os.path.join("eval", "runs", "rewrite-cache.json"))
    ap.add_argument("--library", default=os.path.join("eval", "case_library_20261015.json"))
    ap.add_argument("--out", default="exp_cases.json")
    args = ap.parse_args()

    gold = [json.loads(line) for line in open(args.questions, encoding="utf-8") if line.strip()]
    rew = json.load(open(args.rewrite_cache, encoding="utf-8"))
    lib = json.load(open(args.library, encoding="utf-8"))["사례"]
    s = Searcher()
    s.glossary_filter = True
    t2d = {v["title"]: d for d, v in s.documents.items()}
    ckey = {cid: key(r["doc_id"], r.get("article")) for cid, r in s.row_info.items()}
    by_key = {}
    for cid, k_ in ckey.items():
        by_key.setdefault(k_, []).append(cid)
    wv, wk = config.HYBRID_VECTOR_WEIGHT, config.HYBRID_KEYWORD_WEIGHT
    ss.CITE_WHOLE_ARTICLE = True
    ss.SIBLING_LIMIT = 3000

    for c in lib:
        c["keys"] = [key(t2d[r], j) for r, j in c["연결"] if r in t2d]
        c["chunks"] = [cid for k_ in c["keys"] for cid in by_key.get(k_, [])]
        assert c["chunks"], f"{c['id']} 연결 조를 DB에서 찾지 못함"
    linked_keys = {k_ for c in lib for k_ in c["keys"]}
    CE = s.model.encode([c["상황"] for c in lib], normalize_embeddings=True, show_progress_bar=False)
    print(f"사례 {len(lib)}건, 연결된 조 {len(linked_keys)}개", flush=True)

    data = []
    for g in gold:
        q = g["q"]
        exact = s.exact_hits(q)
        taken = {h["chunk_id"] for h in exact}
        hint = " ".join(x for x in (rew.get(q, ""), glossary.signal_terms(q, s.glossary)) if x)
        base = [("벡터", s.vector_hits(q, None), wv), ("키워드", s.bm25_hits(q, None), wk),
                ("벡터(바꿔쓴)", s.vector_hits(hint, None) if hint else [], wv),
                ("키워드(바꿔쓴)", s.bm25_hits(hint, None) if hint else [], wk)]
        qv = s.model.encode([q], normalize_embeddings=True, show_progress_bar=False)[0]
        sims = CE @ qv
        order = [i for i in np.argsort(-sims)[:TOP_CASES] if sims[i] >= MIN_SIM]
        chunks = list(dict.fromkeys(cid for i in order for cid in lib[i]["chunks"]))[:MAX_CHUNKS]
        case_list = [("사례", [{"chunk_id": c, **s.row_info[c]} for c in chunks], wv)] if chunks else []
        want = {key(t2d.get(e["규정"]), e["조항"]) for e in g["근거"]}
        data.append({"g": g, "exact": exact, "taken": taken, "base": base, "case": case_list,
                     "matched": [(lib[i]["id"], float(sims[i])) for i in order], "want": want})
    triggered = [d for d in data if d["matched"]]
    print(f"사례가 작동한 질문 {len(triggered)}/100 (상위 {TOP_CASES}개, 유사도 {MIN_SIM} 이상)", flush=True)
    for d in triggered:
        print(f"  {d['g']['id']} {d['g']['q'][:40]} → " + ", ".join(f"{i}({v:.2f})" for i, v in d["matched"]), flush=True)

    def pool(d, with_cases):
        lists = d["base"] + (d["case"] if with_cases else [])
        f = s.fuse(lists[0][1], [], lists[0][2], 0.0, lists[1:])
        return [h for h in f if h["chunk_id"] not in d["taken"]][:RERANK_TOP]

    linkable = lambda d: {w for w in d["want"] if w in linked_keys}  # noqa: E731
    need = sum(len(d["want"]) for d in data)
    n_link = sum(len(linkable(d)) for d in data)
    print(f"정답 근거 {need}개 중 사례가 연결한 조에 속한 것(연결 가능 정답) {n_link}개, 해당 질문 {sum(1 for d in data if linkable(d))}개", flush=True)

    res = {}
    for name, with_cases in (("사례 없음 (기준)", False), ("사례 단계 추가", True)):
        pools = [pool(d, with_cases) for d in data]
        in_pool = [d["want"] & {ckey[h["chunk_id"]] for h in p} for d, p in zip(data, pools)]
        orders = []
        for d, p in zip(data, pools):
            ranked = s.rerank(d["g"]["q"], [dict(h) for h in p])
            ranked, _ = glossary.filter_candidates(d["g"]["q"], ranked, s.glossary)
            orders.append(group_by_article(ranked))

        def used_sets(k):
            out = []
            for d, order in zip(data, orders):
                r = []
                for h in (d["exact"] + order)[:k]:
                    it = s._entry(h, status=h.get("status"))
                    it.update({"score": h.get("score"), "rerank": h.get("rerank"), "match": h.get("match", "정확조회"),
                               "siblings": s._siblings_within_limit(h), "cited": s._cited(h)})
                    r.append(it)
                asm = s.assemble(r)
                out.append({key(it["doc_id"], it["article"]) for grp in asm["groups"] for it in grp["items"]})
            return out

        res[name] = {"후보": in_pool, **{k: used_sets(k) for k in KS}}

    base, new = res["사례 없음 (기준)"], res["사례 단계 추가"]
    cnt = lambda sets: sum(len(w & u) for (w, u) in sets)  # noqa: E731
    print("\n=== 전체 100문항 ===")
    print(f"후보 30개 안 정답: 기준 {sum(len(x) for x in base['후보'])} → 사례 {sum(len(x) for x in new['후보'])} (/{need})")
    for k in KS:
        b = sum(len(d["want"] & u) for d, u in zip(data, base[k]))
        n = sum(len(d["want"] & u) for d, u in zip(data, new[k]))
        bf = sum(1 for d, u in zip(data, base[k]) if d["want"] <= u)
        nf = sum(1 for d, u in zip(data, new[k]) if d["want"] <= u)
        print(f"BGE만 k={k:2d} 최종 근거 Recall: 기준 {b / need:.3f}({bf}) → 사례 {n / need:.3f}({nf})")

    print("\n=== 정답을 둘로 나눠서 (k=10) ===")
    for label, pick in (("연결 가능 정답(사례가 연결한 조)", lambda d, w: w in linked_keys), ("그 밖의 정답", lambda d, w: w not in linked_keys)):
        tot = sum(1 for d in data for w in d["want"] if pick(d, w))
        b = sum(1 for d, u in zip(data, base[10]) for w in d["want"] if pick(d, w) and w in u)
        n = sum(1 for d, u in zip(data, new[10]) for w in d["want"] if pick(d, w) and w in u)
        gain = sum(1 for d, ub, un in zip(data, base[10], new[10]) for w in d["want"] if pick(d, w) and w in un and w not in ub)
        lost = sum(1 for d, ub, un in zip(data, base[10], new[10]) for w in d["want"] if pick(d, w) and w in ub and w not in un)
        print(f"{label}: {tot}개 중 최종 근거에 든 것 기준 {b} → 사례 {n} (새로 들어온 {gain}, 빠진 {lost})")
    trig_ids = {d["g"]["id"] for d in triggered}
    for label, sel in (("사례가 작동한 질문", lambda d: d["g"]["id"] in trig_ids), ("작동하지 않은 질문", lambda d: d["g"]["id"] not in trig_ids)):
        idx = [i for i, d in enumerate(data) if sel(d)]
        tot = sum(len(data[i]["want"]) for i in idx)
        b = sum(len(data[i]["want"] & base[10][i]) for i in idx)
        n = sum(len(data[i]["want"] & new[10][i]) for i in idx)
        print(f"{label} {len(idx)}개: 정답 {tot}개 중 k=10 최종 근거에 든 것 기준 {b} → 사례 {n}")
    print("\n사례가 작동한 질문별 변화 (k=10, 정답 조 중 근거에 든 개수 기준 → 사례)")
    for i, d in enumerate(data):
        if d["matched"]:
            print(f"  {d['g']['id']} 정답 {len(d['want'])}개: {len(d['want'] & base[10][i])} → {len(d['want'] & new[10][i])}")
    s.close()


if __name__ == "__main__":
    main()
