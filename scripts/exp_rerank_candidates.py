"""리랭커 후보 예비 측정: 같은 후보 30개를 리랭커마다 다시 줄 세워, 최종 선택 단계(상위 k개 조)에 필수 근거가 몇 개 남는지 비교.

  uv run --extra db python scripts/exp_rerank_candidates.py --models bge qwen3-0.6b [qwen3-4b] [bge-ko]
고정: 후보 = bge-m3 하이브리드 상위 30 청크(가빈 후보 단계, 바꿔 쓴 검색어는 저장 캐시), 리랭커 입력 = 가빈과 같은 「[문서명 조항] 본문_검색」, 점수 입력 = 원질문.
지표: 리랭크 순위에서 서로 다른 조·별표 상위 k개 안의 필수 근거 재현율 (k=5·8·10·12·16), 필수 근거 1위 적중(Hit@1), MRR.
(가빈 파이프라인의 형제 항 확장·인용 복구는 빼고 순위 자체만 비교하므로 절대값은 가빈 표보다 낮을 수 있다. 상대 비교용)
모델 표기: bge = BAAI/bge-reranker-v2-m3 (CrossEncoder), bge-ko = dragonkue/bge-reranker-v2-m3-ko, qwen3-0.6b / qwen3-4b = Qwen3-Reranker (모델 카드의 yes/no 확률 방식)
4b는 8GB GPU에 안 맞아 일부 층을 CPU에 둔다(느림). DB는 읽기 전용. 결과: out/eval/rerank_candidates_<평가셋>.json
"""

import argparse
import collections
import json
import math
import re
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import exp_embed_baseline as E  # noqa: E402

DATA = E.DATA
KS = (5, 8, 10, 12, 16)
TASK = "Given a question about the internal regulations of a Korean public institution, judge whether the regulation provision is needed to answer the question"
PREFIX = ('<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and the Instruct provided. '
          'Note that the answer can only be "yes" or "no".<|im_end|>\n<|im_start|>user\n')
SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c - h) / d, (c + h) / d


class QwenReranker:
    def __init__(self, name: str, offload: bool):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        self.torch = torch
        self.tok = AutoTokenizer.from_pretrained(name, padding_side="left")
        kw = {"dtype": torch.float16}
        if offload:
            kw.update(device_map="auto", max_memory={0: "6GiB", "cpu": "24GiB"})
        else:
            kw.update(device_map={"": 0})
        self.model = AutoModelForCausalLM.from_pretrained(name, **kw).eval()
        self.yes, self.no = self.tok.convert_tokens_to_ids("yes"), self.tok.convert_tokens_to_ids("no")

    def predict(self, pairs: list[tuple[str, str]], batch_size: int = 8) -> list[float]:
        torch = self.torch
        out = []
        for i in range(0, len(pairs), batch_size):
            texts = [PREFIX + f"<Instruct>: {TASK}\n<Query>: {q}\n<Document>: {d[:2500]}" + SUFFIX for q, d in pairs[i:i + batch_size]]
            enc = self.tok(texts, padding=True, truncation=True, max_length=3000, return_tensors="pt").to("cuda:0")
            with torch.no_grad():
                logits = self.model(**enc).logits[:, -1, :]
            two = torch.stack([logits[:, self.no], logits[:, self.yes]], dim=1).float()
            out += torch.softmax(two, dim=1)[:, 1].tolist()
        return out


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", default=["bge", "qwen3-0.6b"])
    ap.add_argument("--gold", default=str(DATA / "answer_eval_100_tiered.jsonl"))
    ap.add_argument("--rewrite", default=str(DATA / "rewrite-cache.json"))
    ap.add_argument("--pool", type=int, default=30)
    a = ap.parse_args()
    import torch
    from kiwipiepy import Kiwi
    from sentence_transformers import CrossEncoder, SentenceTransformer

    kiwi = Kiwi()
    rows = E.load_db()
    ids = [r["chunk_id"] for r in rows]
    by_id = {r["chunk_id"]: r for r in rows}
    names = {r["title"]: r["doc_id"] for r in rows}
    indexed = {E.node_of_chunk(c) for c in ids if E.node_of_chunk(c)}
    gold = [json.loads(l) for l in Path(a.gold).read_text(encoding="utf-8").splitlines() if l.strip()]
    rw = json.loads(Path(a.rewrite).read_text(encoding="utf-8"))
    qs = []
    for g in gold:
        must = []
        for ev in g["근거"]:
            n = E.node_of_gold(names.get(ev["규정"], "?"), ev["조항"])
            if n and n in indexed and ev.get("tier", "필수") == "필수" and n not in must:
                must.append(n)
        if must:
            qs.append({"id": g["id"], "q": g["q"], "rw": rw.get(g["q"], ""), "must": must})
    n_must = sum(len(q["must"]) for q in qs)
    print(f"질문 {len(qs)} · 필수 근거 {n_must}곳")

    # 후보 30개 (bge-m3 하이브리드, 가빈 후보 단계)
    qtok = lambda t: [x.form for x in kiwi.tokenize(t) if not x.tag.startswith(E.SKIP_TAGS)]  # noqa: E731
    bm = E.BM25([(r["chunk_id"], (r["body_tokens"] or "").split()) for r in rows])
    model = SentenceTransformer("BAAI/bge-m3", device="cuda")
    model.half()
    E_doc = np.load(DATA / "emb_BAAI_bge_m3_v0.npy")
    qv = model.encode([q["q"] for q in qs], normalize_embeddings=True, convert_to_numpy=True, batch_size=32)
    rv = model.encode([q["rw"] or q["q"] for q in qs], normalize_embeddings=True, convert_to_numpy=True, batch_size=32)
    del model
    torch.cuda.empty_cache()

    def rrf(lists):
        sc = collections.defaultdict(float)
        for hits, w in lists:
            for rk, c in enumerate(hits, 1):
                sc[c] += w / (E.RRF_K + rk)
        return [c for c, _ in sorted(sc.items(), key=lambda x: -x[1])]

    cands = []
    for i, q in enumerate(qs):
        v = [ids[j] for j in np.argsort(-(qv[i] @ E_doc.T))[:E.CAND]]
        vr = [ids[j] for j in np.argsort(-(rv[i] @ E_doc.T))[:E.CAND]]
        k = bm.top(qtok(q["q"]), E.CAND)
        kr = bm.top(qtok(q["rw"]), E.CAND) if q["rw"] else []
        cands.append(rrf([(v, E.W_VEC), (k, E.W_KEY), (vr, E.W_VEC), (kr, E.W_KEY)])[:a.pool])
    pool_hit = sum(1 for q, c in zip(qs, cands) for x in q["must"] if x in {E.node_of_chunk(y) for y in c})
    print(f"후보 {a.pool}개 안 필수 근거 {pool_hit}/{n_must} = {pool_hit / n_must:.3f} (리랭커가 올릴 수 있는 상한)")

    def evaluate(order_by_q):
        res = {k: 0 for k in KS}
        hit1 = 0
        rr = 0.0
        for q, order in zip(qs, order_by_q):
            nodes = []
            for c in order:
                n = E.node_of_chunk(c)
                if n and n not in nodes:
                    nodes.append(n)
            for k in KS:
                res[k] += sum(1 for x in q["must"] if x in nodes[:k])
            first = next((i for i, n in enumerate(nodes, 1) if n in q["must"]), None)
            hit1 += first == 1
            rr += 1 / first if first else 0
        return {"recall": res, "hit1": hit1, "mrr": rr / len(qs)}

    results = {"RRF 순서(리랭커 없음)": evaluate(cands)}
    times = {}
    for m in a.models:
        t0 = time.time()
        if m == "bge":
            rr_model = CrossEncoder("BAAI/bge-reranker-v2-m3", device="cuda")
            rr_model.model.half()
            predict = lambda pairs: rr_model.predict(pairs, batch_size=16, show_progress_bar=False).tolist()  # noqa: E731
        elif m == "bge-ko":
            rr_model = CrossEncoder("dragonkue/bge-reranker-v2-m3-ko", device="cuda")
            rr_model.model.half()
            predict = lambda pairs: rr_model.predict(pairs, batch_size=16, show_progress_bar=False).tolist()  # noqa: E731
        elif m in ("qwen3-0.6b", "qwen3-4b"):
            rr_model = QwenReranker("Qwen/Qwen3-Reranker-" + m.split("-")[1].upper().replace("B", "B"), offload=(m == "qwen3-4b"))
            predict = rr_model.predict
        else:
            raise SystemExit(f"모르는 모델: {m}")
        order = []
        for q, c in zip(qs, cands):
            pairs = [(q["q"], f"[{by_id[x]['title']} {by_id[x]['article']}] {by_id[x]['body_search']}") for x in c]
            sc = predict(pairs)
            order.append([x for _, x in sorted(zip(sc, c), key=lambda t: -t[0])])
        results[m] = evaluate(order)
        times[m] = time.time() - t0
        print(f"[{m}] 완료 {times[m]:.0f}초 (질문당 {times[m] / len(qs):.1f}초)")
        del rr_model
        import gc
        gc.collect()
        torch.cuda.empty_cache()

    print("\n필수 근거 재현율 (서로 다른 조·별표 상위 k개 안) — 점추정 [95% 구간] · Hit@1 · MRR")
    print("리랭커\t" + "\t".join(f"k={k}" for k in KS) + "\tHit@1\tMRR")
    for name, r in results.items():
        cells = []
        for k in KS:
            lo, hi = wilson(r["recall"][k], n_must)
            cells.append(f"{r['recall'][k] / n_must:.3f} [{lo:.2f},{hi:.2f}]")
        print(f"{name}\t" + "\t".join(cells) + f"\t{r['hit1']}/{len(qs)}\t{r['mrr']:.3f}")
    base = results.get("bge")
    if base:
        for name, r in results.items():
            if name in ("bge", "RRF 순서(리랭커 없음)"):
                continue
            print(f"  {name} 대 bge: k=8 {r['recall'][8] - base['recall'][8]:+d}곳, k=16 {r['recall'][16] - base['recall'][16]:+d}곳")
    out = ROOT / "out" / "eval" / f"rerank_candidates_{Path(a.gold).stem}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"questions": len(qs), "must": n_must, "pool_hit": pool_hit, "times": times,
                               "results": {k: {"recall": {str(kk): vv for kk, vv in v["recall"].items()}, "hit1": v["hit1"], "mrr": v["mrr"]}
                                           for k, v in results.items()}}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"→ {out}")


if __name__ == "__main__":
    main()
