"""임베딩 후보 예비 측정: 가빈 후보 단계(벡터+BM25 하이브리드)에서 필수 근거 재현율을 모델별로 비교.

  uv run --extra db python scripts/exp_embed_candidates.py --models bge-m3 ollama:qwen3-embedding:0.6b-fp16 ollama:qwen3-embedding:4b-q8_0
고정: 문서 입력 v0(본문_검색 그대로), 바꿔 쓴 검색어는 저장된 캐시(rewrite-cache.json, luna가 쓴 것), BM25는 DB 토큰, 가중 RRF(0.7:0.3, k=60), 후보 N개 = 청크 N개.
질문·정답: 가빈 수정 평가셋(answer_eval_100_tiered.jsonl) — 근거마다 tier(필수/보조). 지표: 조·별표 단위 재현율(필수만, 전체).
모델 표기
  bge-m3                 기존에 계산한 문서 임베딩(data/embedding/emb_BAAI_bge_m3_v0.npy)을 쓰고 질문만 SentenceTransformer로
  ollama:<태그>          Ollama /api/embed (양자화 모델, 8GB GPU 제약). Qwen3-Embedding은 질문에만 지시문을 붙인다(모델 카드 방식)
DB는 읽기 전용. 결과: out/eval/embed_candidates_<평가셋>.json, 모델별 임베딩 캐시 data/embedding/emb_<모델>_v0.npy
"""

import argparse
import collections
import json
import math
import re
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import exp_embed_baseline as E  # noqa: E402

DATA = E.DATA
OLLAMA = "http://127.0.0.1:11434/api/embed"
INSTRUCT = "Instruct: Given a question about the internal regulations of a Korean public institution, retrieve the regulation provisions that answer the question\nQuery:"
NS = (10, 30, 50, 100)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c - h) / d, (c + h) / d


def ollama_embed(model: str, texts: list[str], batch: int = 8) -> np.ndarray:
    out = []
    for i in range(0, len(texts), batch):
        body = {"model": model, "input": texts[i:i + batch], "truncate": True, "options": {"num_ctx": 4096}}
        req = urllib.request.Request(OLLAMA, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=900) as r:
            out += json.loads(r.read().decode("utf-8"))["embeddings"]
    M = np.array(out, dtype=np.float32)
    return M / np.linalg.norm(M, axis=1, keepdims=True)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", default=["bge-m3"])
    ap.add_argument("--gold", default=str(DATA / "answer_eval_100_tiered.jsonl"))
    ap.add_argument("--rewrite", default=str(DATA / "rewrite-cache.json"))
    ap.add_argument("--no-instruct", action="store_true", help="Qwen3 질문 지시문을 붙이지 않음(민감도 확인)")
    a = ap.parse_args()
    from kiwipiepy import Kiwi

    kiwi = Kiwi()
    rows = E.load_db()
    ids = [r["chunk_id"] for r in rows]
    names = {r["title"]: r["doc_id"] for r in rows}
    indexed = {E.node_of_chunk(c) for c in ids if E.node_of_chunk(c)}
    gold = [json.loads(l) for l in Path(a.gold).read_text(encoding="utf-8").splitlines() if l.strip()]
    rw = json.loads(Path(a.rewrite).read_text(encoding="utf-8"))
    qs = []
    for g in gold:
        must, allv = [], []
        for ev in g["근거"]:
            n = E.node_of_gold(names.get(ev["규정"], "?"), ev["조항"])
            if n is None or n not in indexed:
                continue
            if n not in allv:
                allv.append(n)
            if ev.get("tier", "필수") == "필수" and n not in must:
                must.append(n)
        if allv:
            qs.append({"id": g["id"], "q": g["q"], "rw": rw.get(g["q"], ""), "must": must, "all": allv})
    n_must, n_all = sum(len(q["must"]) for q in qs), sum(len(q["all"]) for q in qs)
    print(f"질문 {len(qs)} · 필수 근거 {n_must}곳 · 전체 근거 {n_all}곳(색인에 있는 조·별표) · 바꿔 쓴 검색어 {sum(1 for q in qs if q['rw'])}")

    qtok = lambda t: [x.form for x in kiwi.tokenize(t) if not x.tag.startswith(E.SKIP_TAGS)]  # noqa: E731
    bm = E.BM25([(r["chunk_id"], (r["body_tokens"] or "").split()) for r in rows])
    K = [bm.top(qtok(q["q"]), 100) for q in qs]
    KR = [bm.top(qtok(q["rw"]), 100) if q["rw"] else [] for q in qs]

    def rrf(lists):
        sc = collections.defaultdict(float)
        for hits, w in lists:
            for rk, c in enumerate(hits, 1):
                sc[c] += w / (E.RRF_K + rk)
        return [c for c, _ in sorted(sc.items(), key=lambda x: -x[1])]

    def metrics(cands_by_q):
        res = {}
        for n in NS:
            hm = ha = 0
            per = []
            for q, cands in zip(qs, cands_by_q):
                got = {E.node_of_chunk(c) for c in cands[:n]}
                m = sum(1 for x in q["must"] if x in got)
                hm += m
                ha += sum(1 for x in q["all"] if x in got)
                per.append(m)
            res[n] = {"must": hm, "all": ha, "per_q_must": per}
        return res

    results = {}
    for mname in a.models:
        t0 = time.time()
        slug = re.sub(r"[^A-Za-z0-9]+", "_", mname)
        f = DATA / (f"emb_BAAI_bge_m3_v0.npy" if mname == "bge-m3" else f"emb_{slug}_v0.npy")
        qtexts, rtexts = [q["q"] for q in qs], [q["rw"] or q["q"] for q in qs]
        if mname == "bge-m3":
            import torch
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer("BAAI/bge-m3", device="cuda")
            model.half()
            E_doc = np.load(f)
            qv = model.encode(qtexts, normalize_embeddings=True, convert_to_numpy=True, batch_size=32)
            rv = model.encode(rtexts, normalize_embeddings=True, convert_to_numpy=True, batch_size=32)
            del model
            torch.cuda.empty_cache()
        else:
            tag = mname.removeprefix("ollama:")
            if f.exists():
                E_doc = np.load(f)
            else:
                print(f"[{mname}] 문서 {len(rows)}개 임베딩 중…")
                E_doc = ollama_embed(tag, [r["body_search"][:6000] for r in rows])
                np.save(f, E_doc)
            pre = "" if a.no_instruct else INSTRUCT
            qv = ollama_embed(tag, [pre + t for t in qtexts])
            rv = ollama_embed(tag, [pre + t for t in rtexts])
        top = lambda M: [[ids[j] for j in np.argsort(-(M[i] @ E_doc.T))[:100]] for i in range(len(qs))]  # noqa: E731
        V, VR = top(qv), top(rv)
        variants = {
            "벡터(원질문)": V,
            "하이브리드 4목록": [rrf([(v[:E.CAND], E.W_VEC), (k[:E.CAND], E.W_KEY), (r[:E.CAND], E.W_VEC), (kr[:E.CAND], E.W_KEY)])
                            for v, k, r, kr in zip(V, K, VR, KR)],
        }
        for vname, cands in variants.items():
            results[f"{mname} | {vname}"] = metrics(cands)
        print(f"[{mname}] 완료 {time.time() - t0:.0f}초")

    base_key = next((k for k in results if k.startswith("bge-m3") and "하이브리드" in k), None)
    print("\n필수 근거 재현율 (조·별표 단위, 후보 N개 안) — 점추정 [95% 구간]  / 전체 근거")
    print("모델 | 방식\t" + "\t".join(f"@{n}" for n in NS))
    for k, v in results.items():
        cells = []
        for n in NS:
            lo, hi = wilson(v[n]["must"], n_must)
            cells.append(f"{v[n]['must'] / n_must:.3f} [{lo:.2f},{hi:.2f}] / {v[n]['all'] / n_all:.3f}")
        print(k + "\t" + "\t".join(cells))
    if base_key:
        print("\n기준(bge-m3 하이브리드) 대비 필수 근거 @30: 질문 단위로 얻은 것/잃은 것")
        b = results[base_key][30]["per_q_must"]
        for k, v in results.items():
            if k == base_key or "하이브리드" not in k:
                continue
            c = v[30]["per_q_must"]
            print(f"  {k}: 얻음 {sum(max(0, x - y) for x, y in zip(c, b))} · 잃음 {sum(max(0, y - x) for x, y in zip(c, b))}")
    out = ROOT / "out" / "eval" / f"embed_candidates_{Path(a.gold).stem}.json"   # 입력 평가셋별로 저장 (개발용 결과가 시험용 실행에 덮어써진 적 있음, 261016)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"questions": len(qs), "must": n_must, "all": n_all,
                               "results": {k: {str(n): {"must": v[n]["must"], "all": v[n]["all"]} for n in NS} for k, v in results.items()}},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"→ {out}")


if __name__ == "__main__":
    main()
