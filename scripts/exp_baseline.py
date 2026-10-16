"""평가셋 기준선 측정: 현재 검색 설정의 검색 지표 + 장 정보 추가 + BGE 상위 16개 → Luna 선택 (정답 근거 Recall).

사용(프로젝트 루트에서, PG_*로 색인 DB를 고른다):
  python scripts/exp_baseline.py --questions ../한국벤처투자_내부규정_최신개정본/eval/answer_eval_100_new.jsonl --repeat 2

현재 설정 = 후보(원질문+바꿔쓴 검색어, 벡터+BM25 가중 RRF) 상위 30개 → BGE 리랭크(원질문) → 용어집 필터 → 같은 조 묶기.
보고: 전체 후보와 상위 30개 후보 안의 정답, BGE만 Recall(k=5·8·10·12·16)과 Hit·MRR, 장 정보를 채운 BGE 입력, BGE 상위 16개 조 → Luna k개(8·10).
정답 분모는 (규정, 조) 단위로 한 질문 안에서 같은 조를 한 번만 센다.
"""
import argparse
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

import config  # noqa: E402
import src.search.search as ss  # noqa: E402
from langchain_openai import ChatOpenAI  # noqa: E402
from src.search import glossary  # noqa: E402
from src.search.search import RERANK_TOP, Searcher, group_by_article  # noqa: E402

KS = (5, 8, 10, 12, 16)


def key(doc, art):
    m = re.match(r"제(\d+)조(?:의(\d+))?", art or "")
    if m:
        return f"{doc}:제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "")
    return f"{doc}:" + re.sub(r"\s+", "", art or "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", required=True)
    ap.add_argument("--rewrite-cache", default=os.path.join("eval", "runs", "rewrite-cache.json"))
    ap.add_argument("--chunks", default=os.path.join("out", "chunks.jsonl"), help="장 정보가 있는 조 단위 청크 파일")
    ap.add_argument("--repeat", type=int, default=2)
    ap.add_argument("--out", default="exp_baseline.json")
    args = ap.parse_args()

    gold = [json.loads(line) for line in open(args.questions, encoding="utf-8") if line.strip()]
    rew = json.load(open(args.rewrite_cache, encoding="utf-8"))
    s = Searcher()
    s.glossary_filter = True
    s._select_llm = ChatOpenAI(model=config.JUDGE_MODEL, temperature=0, timeout=120, max_retries=2)
    t2d = {v["title"]: d for d, v in s.documents.items()}
    ckey = {cid: key(r["doc_id"], r.get("article")) for cid, r in s.row_info.items()}
    wv, wk = config.HYBRID_VECTOR_WEIGHT, config.HYBRID_KEYWORD_WEIGHT
    ss.CITE_WHOLE_ARTICLE = True
    ss.SIBLING_LIMIT = 3000
    sec_map = {}
    for line in open(args.chunks, encoding="utf-8"):
        if line.strip():
            r = json.loads(line)
            if r.get("장"):
                sec_map[key(r["고유번호"], r["조항"])] = r["장"]

    def set_sections(on):
        for c, r in s.row_info.items():
            r["section"] = sec_map.get(ckey[c]) if on else None

    set_sections(False)
    data = []
    for g in gold:
        q = g["q"]
        exact = s.exact_hits(q)
        taken = {h["chunk_id"] for h in exact}
        hint = " ".join(x for x in (rew.get(q, ""), glossary.signal_terms(q, s.glossary)) if x)
        vec, keyw = s.vector_hits(q, None), s.bm25_hits(q, None)
        extra = (("벡터(바꿔쓴)", s.vector_hits(hint, None), wv), ("키워드(바꿔쓴)", s.bm25_hits(hint, None), wk)) if hint else ()
        full = [h for h in s.fuse(vec, keyw, wv, wk, extra) if h["chunk_id"] not in taken]
        want = {key(t2d.get(e["규정"]), e["조항"]) for e in g["근거"]}
        core = {key(t2d.get(e["규정"]), e["조항"]) for e in g["근거"] if e.get("tier", "필수") == "필수"}
        data.append({"g": g, "exact": exact, "full": full, "a30": full[:RERANK_TOP], "want": want, "core": core})
    need = sum(len(d["want"]) for d in data)
    need_core = sum(len(d["core"]) for d in data)
    if need_core != need:
        print(f"필수 근거 {need_core}개, 보조 근거 {need - need_core}개 (tier 필드 기준, (규정, 조) 단위)", flush=True)
    n_q = sum(1 for d in data if d["want"])
    print(f"질문 {len(data)}개, 정답 근거 (규정, 조) {need}개", flush=True)
    keys = lambda hits: {ckey[h["chunk_id"]] for h in hits}  # noqa: E731
    print(f"전체 후보(자르기 전)에 정답 {sum(len(d['want'] & keys(d['full'])) for d in data)}/{need}, 상위 30개 안 정답 {sum(len(d['want'] & keys(d['a30'])) for d in data)}/{need}", flush=True)

    def order_of(sections):
        set_sections(sections)
        out = []
        for d in data:
            ranked = s.rerank(d["g"]["q"], [dict(h) for h in d["a30"]])
            ranked, _ = glossary.filter_candidates(d["g"]["q"], ranked, s.glossary)
            out.append(group_by_article(ranked))
        set_sections(False)
        return out

    def recall(chosen_per_q, k):
        got = full_ = got_c = full_c = 0
        for d, chosen in zip(data, chosen_per_q):
            res = []
            for h in (d["exact"] + chosen)[:k]:
                it = s._entry(h, status=h.get("status"))
                it.update({"score": h.get("score"), "rerank": h.get("rerank"), "match": h.get("match", "정확조회"),
                           "siblings": s._siblings_within_limit(h), "cited": s._cited(h)})
                res.append(it)
            asm = s.assemble(res)
            used = {key(it["doc_id"], it["article"]) for grp in asm["groups"] for it in grp["items"]}
            got += len(d["want"] & used)
            full_ += d["want"] <= used
            got_c += len(d["core"] & used)
            full_c += d["core"] <= used
        if need_core != need:  # tier 필드가 있는 평가셋이면 필수 근거 기준 값도 같이 기록한다
            recall.core[k] = (got_c / need_core, full_c)
        return got / need, full_

    recall.core = {}

    def hit_mrr(orders):
        firsts = []
        for d, order in zip(data, orders):
            if d["want"]:
                pos = [i for i, h in enumerate(order, 1) if ckey[h["chunk_id"]] in d["want"]]
                firsts.append(pos[0] if pos else None)
        return [sum(1 for f in firsts if f and f <= t) / len(firsts) for t in (1, 3, 5, 10)], sum(1 / f for f in firsts if f) / len(firsts)

    results = {}
    for name, sec in (("BGE만 (현재)", False), ("BGE만 + 장 정보", True)):
        orders = order_of(sec)
        recall.core = {}
        cells = {k: recall(orders, k) for k in KS}
        core_cells = dict(recall.core)
        hm = hit_mrr(orders)
        results[name] = {"Recall": cells, "필수만": core_cells, "hit": hm[0], "mrr": hm[1]}
        if core_cells:
            print(f"[{name}] 필수 근거만 Recall " + " ".join(f"k{k} {r:.3f}({f}/{n_q})" for k, (r, f) in core_cells.items()), flush=True)
        print(f"\n[{name}] Recall " + " ".join(f"k{k} {r:.3f}({f}/{n_q})" for k, (r, f) in cells.items())
              + "\n  Hit@1/3/5/10 " + "/".join(f"{x:.2f}" for x in hm[0]) + f" MRR {hm[1]:.3f}", flush=True)
        if not sec:
            base_orders = orders
    for k in (8, 10):
        runs = []
        core_runs = []
        for _ in range(args.repeat):
            with ThreadPoolExecutor(8) as ex:
                chosen = list(ex.map(lambda a: s._llm_select(a[0]["g"]["q"], a[1][:16], k), zip(data, base_orders)))
            recall.core = {}
            runs.append((recall(chosen, k), hit_mrr(chosen)))
            if recall.core:
                core_runs.append(recall.core[k])
        if core_runs:
            print(f"  (필수 근거만 Recall 평균 {sum(r for r, _ in core_runs) / len(core_runs):.3f}, 전부 확보 {sum(f for _, f in core_runs) / len(core_runs):.1f}/{n_q})", flush=True)
        avg = sum(r[0][0] for r in runs) / len(runs)
        full_ = sum(r[0][1] for r in runs) / len(runs)
        results[f"Luna k{k}"] = {"평균": avg, "회차": [r[0][0] for r in runs], "전부": full_, "hit": runs[0][1][0], "mrr": runs[0][1][1]}
        print(f"[BGE 상위 16개 조 → Luna {k:2d}개] Recall 평균 {avg:.3f} (회차 {', '.join(f'{r[0][0]:.3f}' for r in runs)}) 전부 확보 {full_:.1f}/{n_q}"
              f" | Hit@1/3/5/10 " + "/".join(f"{x:.2f}" for x in runs[0][1][0]) + f" MRR {runs[0][1][1]:.3f}", flush=True)
    json.dump(results, open(os.path.join("eval", "runs", args.out), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    s.close()


if __name__ == "__main__":
    main()
