"""호(번호 항목) 단위 검색 표현을 추가했을 때 후보와 최종 Recall이 어떻게 변하는지 본다 (정답 근거 Recall만 본다).

사용(프로젝트 루트에서, PG_*로 색인 DB를 고른다):
  python scripts/exp_ho.py --questions eval/answer_eval_100.jsonl --repeat 2

기존 항 색인은 그대로 둔다. 호가 2개 이상인 항을 호마다 잘라 "검색용 표현"을 만든다(DB는 건드리지 않고 메모리에서만).
  검색용 표현 = [문서명 조항(조 제목)] 항의 앞머리(조건·처리 문장) + 제N호 본문
호 표현으로 벡터·키워드 검색을 따로 하고, 결과는 부모 항 청크로 되돌려 기존 4개 목록에 더해 같은 가중 RRF로 합친다.
그 뒤(후보 30개, BGE 리랭크, 같은 조 묶기, 근거 조립)는 기존과 같다. 평가 분모와 근거 매칭 단위(규정, 조)도 같다.
"""
import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor

import numpy as np

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")
from dotenv import load_dotenv  # noqa: E402

load_dotenv()
os.environ["LANGCHAIN_TRACING_V2"] = "false"

import config  # noqa: E402
import src.search.search as ss  # noqa: E402
from langchain_openai import ChatOpenAI  # noqa: E402
from src.search import glossary  # noqa: E402
from src.search.bm25 import BM25  # noqa: E402
from src.search.search import RERANK_TOP, Searcher, group_by_article  # noqa: E402

TITLE = re.compile(r"제\d+조(?:의\d+)?\(([^)]+)\)")
KS = (8, 10, 16)
CANDIDATES = 50


def key(doc, art):
    m = re.match(r"제(\d+)조(?:의(\d+))?", art or "")
    if m:
        return f"{doc}:제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "")
    return f"{doc}:" + re.sub(r"\s+", "", art or "")


def split_ho(body):
    """항 본문에서 호를 나눈다: (앞머리, [(호 번호, 호 본문), ...]). 1부터 차례로 이어지는 번호가 2개 이상일 때만 호로 본다."""
    marks = [(m.start(1), int(m.group(1))) for m in re.finditer(r"(?:^|\s)(\d{1,2})\.\s", body)]
    seq, expect = [], 1
    for pos, n in marks:
        if n == expect:
            seq.append((pos, n))
            expect += 1
    if len(seq) < 2:
        return body, []
    head = body[:seq[0][0]].strip()
    items = []
    for i, (pos, n) in enumerate(seq):
        end = seq[i + 1][0] if i + 1 < len(seq) else len(body)
        items.append((n, body[pos:end].strip()))
    return head, items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", default="eval/answer_eval_100.jsonl")
    ap.add_argument("--rewrite-cache", default=os.path.join("eval", "runs", "rewrite-cache.json"))
    ap.add_argument("--repeat", type=int, default=2)
    ap.add_argument("--out", default="exp_ho.json")
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
    art_title = {}
    for cid, r in s.row_info.items():
        m = TITLE.search((r["body"] or "")[:200])
        if m:
            art_title.setdefault(ckey[cid], m.group(1))

    # --- 호 단위 검색 표현 (메모리) ---
    ho_parent, ho_text = [], []
    for cid, r in s.row_info.items():
        body = (r["body_search"] or "").strip()
        head, items = split_ho(body)
        if not items:
            continue
        doc = s.documents.get(r["doc_id"], {}).get("title") or r["doc_id"]
        t = art_title.get(ckey[cid])
        art = re.match(r"제\d+조(?:의\d+)?", r["article"] or "")
        label = f"{doc} {art.group(0) if art else r['article']}{f'({t})' if t else ''}"
        lead = re.sub(r"\s+", " ", head)[-160:]
        for n, item in items:
            ho_parent.append(cid)
            ho_text.append(f"[{label}] {lead} 제{n}호 {re.sub(chr(10), ' ', item)}")
    print(f"호 단위 검색 표현 {len(ho_text)}개 (부모 항 {len(set(ho_parent))}개)", flush=True)
    E = s.model.encode(ho_text, normalize_embeddings=True, batch_size=32, show_progress_bar=False)
    tokens = [s._query_tokens(x) for x in ho_text]
    bm = BM25([(i, tk) for i, tk in enumerate(tokens)])

    def ho_lists(text):
        qv = s.model.encode([text], normalize_embeddings=True, show_progress_bar=False)[0]
        top = np.argsort(-(E @ qv))[:CANDIDATES]
        vec = list(dict.fromkeys(ho_parent[i] for i in top))
        key_ = list(dict.fromkeys(ho_parent[i] for i, _ in bm.top(s._query_tokens(text), k=CANDIDATES)))
        mk = lambda ids: [{"chunk_id": c, **s.row_info[c]} for c in ids]  # noqa: E731
        return mk(vec), mk(key_)

    data = []
    for g in gold:
        q = g["q"]
        exact = s.exact_hits(q)
        taken = {h["chunk_id"] for h in exact}
        hint = " ".join(x for x in (rew.get(q, ""), glossary.signal_terms(q, s.glossary)) if x)
        base = [("벡터", s.vector_hits(q, None), wv), ("키워드", s.bm25_hits(q, None), wk),
                ("벡터(바꿔쓴)", s.vector_hits(hint, None) if hint else [], wv),
                ("키워드(바꿔쓴)", s.bm25_hits(hint, None) if hint else [], wk)]
        hv, hk = ho_lists(q)
        hvh, hkh = ho_lists(hint) if hint else ([], [])
        ho = [("호 벡터", hv, wv), ("호 키워드", hk, wk), ("호 벡터(바꿔쓴)", hvh, wv), ("호 키워드(바꿔쓴)", hkh, wk)]
        want = {key(t2d.get(e["규정"]), e["조항"]) for e in g["근거"]}
        data.append({"g": g, "exact": exact, "taken": taken, "base": base, "ho": ho, "want": want})
    print("후보 목록 준비 완료", flush=True)

    def pool(d, with_ho):
        lists = d["base"] + (d["ho"] if with_ho else [])
        f = s.fuse(lists[0][1], [], lists[0][2], 0.0, lists[1:])
        return [h for h in f if h["chunk_id"] not in d["taken"]]

    need = sum(len(d["want"]) for d in data)

    def recall(chosen_per_q, k):
        got = full_ = 0
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
        return got / need, full_

    results = {}
    base_pool_keys = None
    for name, with_ho in (("기존 (항 색인)", False), ("호 표현 추가", True)):
        pools = [pool(d, with_ho)[:RERANK_TOP] for d in data]
        keys = [{ckey[h["chunk_id"]] for h in p} for p in pools]
        in_pool = sum(len(d["want"] & k_) for d, k_ in zip(data, keys))
        if base_pool_keys is None:
            base_pool_keys = keys
        new = sum(len((d["want"] & k_) - b) for d, k_, b in zip(data, keys, base_pool_keys))
        lost = sum(len((d["want"] & b) - k_) for d, k_, b in zip(data, keys, base_pool_keys))
        print(f"\n[{name}] 후보 30개 안 정답 {in_pool}/{need} | 기존보다 새로 들어온 정답 {new}, 빠진 정답 {lost}", flush=True)
        orders = []
        for d, p in zip(data, pools):
            ranked = s.rerank(d["g"]["q"], [dict(h) for h in p])
            ranked, _ = glossary.filter_candidates(d["g"]["q"], ranked, s.glossary)
            orders.append(group_by_article(ranked))
        cells = {k: recall(orders, k) for k in KS}
        runs = []
        for _ in range(args.repeat):
            with ThreadPoolExecutor(8) as ex:
                chosen = list(ex.map(lambda a: s._llm_select(a[0]["g"]["q"], a[1][:16], 10), zip(data, orders)))
            runs.append(recall(chosen, 10))
        avg = sum(r[0] for r in runs) / len(runs)
        results[name] = {"후보안정답": in_pool, "새로": new, "빠짐": lost, "BGE": cells, "Luna10": avg, "Luna전부": sum(r[1] for r in runs) / len(runs)}
        print(f"  BGE만 " + " ".join(f"k{k} {r:.3f}({f})" for k, (r, f) in cells.items())
              + f" | BGE 16 → Luna 10 평균 {avg:.3f} (회차 {', '.join(f'{r[0]:.3f}' for r in runs)})", flush=True)
        json.dump(results, open(os.path.join("eval", "runs", args.out), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    s.close()


if __name__ == "__main__":
    main()
