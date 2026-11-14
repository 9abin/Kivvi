"""중간 개념 풀어쓰기(bridge) 시험: LLM이 「이 질문과 함께 확인해야 할 주제」를 쓰고, 그것으로 다시 검색하면 놓친 근거가 잡히나.

  uv run --extra db python scripts/exp_bridge.py [--model qwen3.5:9b]
개발용 94문항(근거 126곳)으로 후보 30개 안 재현율을 기준선(현행 하이브리드)과 비교한다. 후보 수는 같다 (공정 비교).
  기준선: 원질문 + 바꿔쓴 검색어 (벡터·BM25 4목록 RRF, 리포트 40)
  bridge: 위에 LLM이 풀어 쓴 「함께 확인할 주제」 3~4개를 벡터·BM25 목록으로 더함 (가중치 낮게)
LLM 호출 결과는 data/embedding/bridge-cache-{모델}.json에 둔다.
커피 예시 유형(「멀지만 필수」 근거: 원질문 벡터 순위가 30 밖)만 따로 센다.
"""

import argparse
import collections
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import exp_embed_baseline as E  # noqa: E402

DATA = ROOT / "data" / "embedding"
PROMPT = ("회사 내부 규정(내규)에서 답이나 판단의 근거가 되는 조문을 찾으려 한다. 아래 질문에 답하려면 질문에 직접 나오지 않더라도 "
          "함께 확인해야 하는 내규 주제(조건·제한·예외·절차·정의·관련 제도)를 3~4개 쓴다. 한 줄에 하나, 내규 조문에 나올 법한 낱말로, 설명 없이 주제만.")


def ask(model: str, q: str) -> list[str]:
    from regcheck.nl import _post
    out = _post(model, [{"role": "system", "content": PROMPT}, {"role": "user", "content": q}], timeout=120)
    lines = [l.strip(" -*•·0123456789.)").strip() for l in out.splitlines() if l.strip()]
    return [l for l in lines if 2 <= len(l) <= 80][:4]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen3.5:9b")
    ap.add_argument("--w", type=float, default=0.5, help="bridge 목록 가중치 (원질문 목록은 0.7·0.3)")
    a = ap.parse_args()
    from kiwipiepy import Kiwi
    from sentence_transformers import SentenceTransformer

    kiwi = Kiwi()
    rows = E.load_db()
    ids = [r["chunk_id"] for r in rows]
    names = {r["title"]: r["doc_id"] for r in rows}
    indexed = {E.node_of_chunk(c) for c in ids if E.node_of_chunk(c)}
    E_doc = np.load(DATA / "emb_BAAI_bge_m3_v0.npy")
    bm = E.BM25([(r["chunk_id"], (r["body_tokens"] or "").split()) for r in rows])
    qtok = lambda t: [x.form for x in kiwi.tokenize(t) if not x.tag.startswith(E.SKIP_TAGS)]  # noqa: E731
    model = SentenceTransformer("BAAI/bge-m3", device="cuda")
    model.half()

    def rrf(lists):
        sc = collections.defaultdict(float)
        for hits, w in lists:
            for rk, c in enumerate(hits, 1):
                sc[c] += w / (E.RRF_K + rk)
        return [c for c, _ in sorted(sc.items(), key=lambda x: -x[1])]

    def vec(t):
        S = model.encode([t], normalize_embeddings=True)[0] @ E_doc.T
        return [ids[j] for j in np.argsort(-S)[:100]]

    cache_path = DATA / f"bridge-cache-{a.model.replace(':', '_')}.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    gold = [json.loads(l) for l in (DATA / "answer_eval_100.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    rw = json.loads((DATA / "rewrite-cache.json").read_text(encoding="utf-8"))
    qs = []
    for g in gold:
        need = []
        for ev in g["근거"]:
            n = E.node_of_gold(names.get(ev["규정"], "?"), ev["조항"])
            if n and n in indexed and n not in need:
                need.append(n)
        if need:
            qs.append({"q": g["q"], "rw": rw.get(g["q"], ""), "need": need})
    for i, q in enumerate(qs):
        if q["q"] not in cache:
            cache[q["q"]] = ask(a.model, q["q"])
            if i % 10 == 9:
                cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
                print(f"  LLM {i + 1}/{len(qs)}", flush=True)
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")

    C = E.CAND
    base, brid, orig_vec, only = [], [], [], []
    for q in qs:
        v0, k0 = vec(q["q"]), bm.top(qtok(q["q"]), 100)
        v1 = vec(q["rw"] or q["q"]); k1 = bm.top(qtok(q["rw"]), 100) if q["rw"] else []
        lists = [(v0[:C], .7), (k0[:C], .3), (v1[:C], .7), (k1[:C], .3)]
        base.append(rrf(lists))
        extra = []
        for b in cache[q["q"]]:
            extra += [(vec(b)[:C], a.w * .7), (bm.top(qtok(b), 100)[:C], a.w * .3)]
        brid.append(rrf(lists + extra))
        only.append(rrf(extra) if extra else [])      # bridge 목록만 (별도 가설 목록용)
        orig_vec.append(v0)
    nodes = lambda cs, n: {E.node_of_chunk(c) for c in cs[:n]}  # noqa: E731
    tot = sum(len(q["need"]) for q in qs)
    print(f"\n질문 {len(qs)} · 근거 {tot}곳 · 모델 {a.model} · bridge 가중치 {a.w}")
    print("방식\t" + "\t".join(f"@{n}" for n in (10, 30, 50)))
    for name, cs in (("기준선(현행 하이브리드)", base), ("+ bridge(LLM이 풀어 쓴 주제)", brid)):
        print(name + "\t" + "\t".join(f"{sum(n in nodes(c, k) for q, c in zip(qs, cs) for n in q['need']) / tot:.3f}" for k in (10, 30, 50)))
    print("\n별도 가설 목록: 기준선 후보 30개는 그대로 두고, bridge 목록만의 상위 k개를 따로 덧붙임 (후보 30 + k)")
    base30 = sum(n in nodes(base[i], 30) for i, q in enumerate(qs) for n in q["need"])
    for k in (3, 5, 10):
        got = sum(n in (nodes(base[i], 30) | {E.node_of_chunk(c) for c in only[i][:k]}) for i, q in enumerate(qs) for n in q["need"])
        print(f"  30 + {k:2d}\t{got / tot:.3f}  (+{got - base30}곳)")
    far = [(i, n) for i, q in enumerate(qs) for n in q["need"] if n not in nodes(orig_vec[i], 30)]   # 원질문 벡터로는 30 밖
    gained = [(i, n) for i, n in far if n in nodes(brid[i], 30) and n not in nodes(base[i], 30)]
    lost = [(i, n) for i, q in enumerate(qs) for n in q["need"] if n in nodes(base[i], 30) and n not in nodes(brid[i], 30)]
    print(f"\n원질문 벡터 검색으로는 30 밖인 근거 {len(far)}곳 중 기준선 후보 30 안 {sum(n in nodes(base[i], 30) for i, n in far)}곳")
    print(f"bridge로 새로 후보 30 안에 든 근거 {len(gained)}곳 · 기준선에 있다가 밀려난 근거 {len(lost)}곳")
    inv = {}
    for r in rows:
        n = E.node_of_chunk(r["chunk_id"])
        if n and n not in inv:
            inv[n] = f"{r['title']} {r['chunk_id'].split('_', 1)[1]}"
    for i, n in gained[:12]:
        print(f"  + 「{qs[i]['q'][:34]}」 → {inv.get(n, n)} | bridge: {' / '.join(cache[qs[i]['q']][:3])[:90]}")
    for i, n in lost[:6]:
        print(f"  - 「{qs[i]['q'][:34]}」 → {inv.get(n, n)} 밀려남")


if __name__ == "__main__":
    main()
