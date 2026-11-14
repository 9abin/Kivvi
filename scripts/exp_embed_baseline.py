"""임베딩 튜닝 단계 0: 기준선 재현 + 문서 모양 비교 (튜닝 없음).

  uv run --extra db python scripts/exp_embed_baseline.py [--models BAAI/bge-m3 dragonkue/BGE-m3-ko] [--forms v0 v1 v2]
가빈 검색의 후보 단계를 그대로 옮겨 「후보 안에 정답 근거(조·별표)가 얼마나 있나」를 잰다 (feature/rag-tuning-para의 search.py 기준)
  · 질의: 원질문 + 바꿔 쓴 검색어 각각 → 벡터 상위 50, BM25 상위 50 (k1=1.5, b=0.75, Kiwi 형태소, 질의에서 조사·어미·문장부호 제외)
  · 합치기: 가중 RRF(상수 60), 벡터 0.7·키워드 0.3, 바꿔 쓴 검색어 목록도 같은 가중치
  · 후보 N개(N=10·30·50·100)를 조·별표 단위로 묶어 정답 근거 재현율 (micro)
문서 모양 (임베딩 입력만 바꿈. BM25 토큰은 기본 DB 값, --bm25-form이면 같은 모양으로 다시)
  v0 현행: 본문_검색 그대로 (항 청크에는 조 제목·규정명이 없다)
  v1: 「제N조(제목) 본문」
  v2: 「규정명 > 장 > 제N조(제목)\n본문」
DB는 읽기 전용(팀 계정). 질문·정답은 평가 100문항(answer_eval_100). 결과: out/eval/embed_baseline.json, 임베딩 캐시 data/embedding/emb_*.npy
"""

import argparse
import collections
import json
import math
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from regcheck.db import load_env  # noqa: E402

DATA = ROOT / "data" / "embedding"
SKIP_TAGS = ("J", "E", "SF", "SP", "SS", "SE", "SO")
CAND, RRF_K, W_VEC, W_KEY = 50, 60, 0.7, 0.3
NS = (10, 30, 50, 100)


class BM25:
    """가빈 src/search/bm25.py와 같은 계산 (k1=1.5, b=0.75)"""

    def __init__(self, docs, k1=1.5, b=0.75):
        self.k1, self.b = k1, b
        self.ids = [d[0] for d in docs]
        self.doc_len = [len(d[1]) for d in docs]
        self.n = len(docs)
        self.avgdl = sum(self.doc_len) / self.n
        self.inv = collections.defaultdict(list)
        df = collections.Counter()
        for i, (_, toks) in enumerate(docs):
            c = collections.Counter(toks)
            for t, f in c.items():
                self.inv[t].append((i, f))
            df.update(c.keys())
        self.idf = {t: math.log(1 + (self.n - n + 0.5) / (n + 0.5)) for t, n in df.items()}

    def top(self, qtoks, k=50):
        sc = collections.defaultdict(float)
        for t in set(qtoks):
            idf = self.idf.get(t)
            if idf is None:
                continue
            for i, f in self.inv[t]:
                sc[i] += idf * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.doc_len[i] / self.avgdl))
        return [self.ids[i] for i, _ in sorted(sc.items(), key=lambda x: -x[1])[:k]]


def node_of_chunk(cid: str) -> str | None:
    m = re.match(r"^(\d+)_(.+)$", cid)
    if not m:
        return None
    doc, rest = m.groups()
    if x := re.match(r"별표(\d+)", rest):
        return f"{doc}:별표{x.group(1)}"
    if x := re.match(r"제(\d+)조(?:의(\d+))?", rest):
        return f"{doc}:{x.group(1)}" + (f"의{x.group(2)}" if x.group(2) else "")
    return None


def node_of_gold(doc: str, 조항: str) -> str | None:
    if x := re.match(r"별표\s*(\d+)", 조항):
        return f"{doc}:별표{x.group(1)}"
    if x := re.match(r"제\s*(\d+)\s*조(?:\s*의\s*(\d+))?", 조항):
        return f"{doc}:{x.group(1)}" + (f"의{x.group(2)}" if x.group(2) else "")
    return None


def load_db():
    import psycopg
    from psycopg.rows import dict_row

    team = load_env(ROOT / ".env.team")
    con = psycopg.connect(host="127.0.0.1", port=team.get("TEAM_PORT", "5432"), dbname=team["TEAM_DATABASE"], user=team["TEAM_USER"],
                          password=team["TEAM_PASSWORD"], gssencmode="disable", autocommit=True)
    con.execute("SET default_transaction_read_only = on")
    with con.cursor(row_factory=dict_row) as cur:
        rows = cur.execute("select c.chunk_id, c.doc_id, c.article, c.body_search, c.body_tokens, c.embedding::text as emb, d.title "
                           "from public.chunks c join public.documents d using (doc_id) where c.is_indexed order by c.chunk_id").fetchall()
    con.close()
    return rows


def doc_text(form: str, r: dict, art: dict | None) -> str:
    body = r["body_search"]
    if form == "v0" or art is None:
        return body
    label, title = art["label"], art["title"]
    head = f"{label}({title})" if title else label
    if body.lstrip().startswith(label):          # 조 전체 청크는 본문 첫 줄에 이미 「제N조(제목)」이 있다
        head = ""
    if form == "v1":
        return f"{head} {body}".strip()
    path = " > ".join(x for x in (r["title"], (art.get("chapter") or "").strip(), head) if x)
    return f"{path}\n{body}".strip() if path else body


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", default=["BAAI/bge-m3", "dragonkue/BGE-m3-ko"])
    ap.add_argument("--forms", nargs="+", default=["v0", "v1", "v2"])
    ap.add_argument("--gold", default=str(DATA / "answer_eval_100.jsonl"))
    ap.add_argument("--rewrite", default=str(DATA / "rewrite-cache.json"))
    ap.add_argument("--bm25-form", action="store_true", help="BM25 문서도 같은 모양으로 (기본: DB의 body_tokens)")
    a = ap.parse_args()
    from kiwipiepy import Kiwi
    from sentence_transformers import SentenceTransformer

    kiwi = Kiwi()
    rows = load_db()
    ids = [r["chunk_id"] for r in rows]
    node = [node_of_chunk(c) for c in ids]
    parsed = {}
    for p in (ROOT / "out" / "parsed").glob("[0-9]*.json"):
        reg = json.loads(p.read_text(encoding="utf-8"))
        for art in reg["articles"]:
            parsed[art["id"]] = art
    arts = [parsed.get(n) if n else None for n in node]
    print(f"색인 청크 {len(rows)} · 조·별표 단위 {len(set(n for n in node if n))} · 조 제목을 찾은 청크 {sum(1 for x in arts if x)}")

    names = {r["title"]: r["doc_id"] for r in rows}
    gold = [json.loads(l) for l in Path(a.gold).read_text(encoding="utf-8").splitlines() if l.strip()]
    rw = json.loads(Path(a.rewrite).read_text(encoding="utf-8"))
    indexed_nodes = set(n for n in node if n)
    qs = []
    for g in gold:
        need, unread = [], 0
        for ev in g["근거"]:
            n = node_of_gold(names.get(ev["규정"], "?"), ev["조항"])
            if n is None:
                unread += 1
            elif n not in need:
                need.append(n)
        if need:
            qs.append({"id": g["id"], "q": g["q"], "rw": rw.get(g["q"], ""), "need": need,
                       "need_idx": [n for n in need if n in indexed_nodes]})
    tot = sum(len(q["need"]) for q in qs)
    tot_idx = sum(len(q["need_idx"]) for q in qs)
    print(f"질문 {len(qs)} · 정답 근거 {tot}곳(조·별표) · 그중 색인에 있는 것 {tot_idx}곳 · 바꿔 쓴 검색어 있음 {sum(1 for q in qs if q['rw'])}")

    qtok = lambda t: [x.form for x in kiwi.tokenize(t) if not x.tag.startswith(SKIP_TAGS)]  # noqa: E731
    bm_v0 = BM25([(r["chunk_id"], (r["body_tokens"] or "").split()) for r in rows])
    db_vec = np.array([[float(x) for x in r["emb"].strip("[]").split(",")] for r in rows], dtype=np.float32)
    DATA.mkdir(parents=True, exist_ok=True)

    def rrf(lists):
        sc = collections.defaultdict(float)
        for hits, w in lists:
            for rk, c in enumerate(hits, 1):
                sc[c] += w / (RRF_K + rk)
        return [c for c, _ in sorted(sc.items(), key=lambda x: -x[1])]

    def recall(cands_by_q, key):
        out = {}
        for n in NS:
            hit = hit_idx = 0
            for q, cands in zip(qs, cands_by_q):
                got = {node[ids.index(c)] if False else node_of_chunk(c) for c in cands[:n]}
                hit += sum(1 for x in q["need"] if x in got)
                hit_idx += sum(1 for x in q["need_idx"] if x in got)
            out[n] = (hit / tot, hit_idx / tot_idx)
        return out

    results = {}
    for mname in a.models:
        model = SentenceTransformer(mname, device="cuda")
        model.half()
        slug = re.sub(r"[^A-Za-z0-9]+", "_", mname)
        qv = model.encode([q["q"] for q in qs], normalize_embeddings=True, convert_to_numpy=True, batch_size=32)
        rv = model.encode([q["rw"] or q["q"] for q in qs], normalize_embeddings=True, convert_to_numpy=True, batch_size=32)
        for form in a.forms:
            f = DATA / f"emb_{slug}_{form}.npy"
            if f.exists():
                E = np.load(f)
            else:
                texts = [doc_text(form, r, art) for r, art in zip(rows, arts)]
                E = model.encode(texts, normalize_embeddings=True, convert_to_numpy=True, batch_size=32, show_progress_bar=False).astype(np.float32)
                np.save(f, E)
            if mname == "BAAI/bge-m3" and form == "v0":
                cos = (E * db_vec).sum(1)
                print(f"[검증] 내 v0 임베딩 ↔ DB 벡터 코사인 평균 {cos.mean():.4f}, 최소 {cos.min():.4f} (1에 가까우면 같은 입력)")
            bm = bm_v0
            if a.bm25_form:
                texts = [doc_text(form, r, art) for r, art in zip(rows, arts)]
                bm = BM25([(i, [t.form for t in kiwi.tokenize(x)]) for i, x in zip(ids, texts)])
            top = lambda M: [[ids[j] for j in np.argsort(-(M @ E.T), axis=1)[i][:100]] for i in range(len(qs))]  # noqa: E731
            V = top(qv)
            VR = top(rv)
            K = [bm.top(qtok(q["q"]), 100) for q in qs]
            KR = [bm.top(qtok(q["rw"]), 100) if q["rw"] else [] for q in qs]
            variants = {
                "벡터(원질문)": V,
                "벡터(원질문+바꿔쓴 RRF)": [rrf([(v[:CAND], 1), (r[:CAND], 1)]) for v, r in zip(V, VR)],
                "BM25(원질문)": K,
                "하이브리드 4목록 (가빈 후보 단계)": [rrf([(v[:CAND], W_VEC), (k[:CAND], W_KEY), (r[:CAND], W_VEC), (kr[:CAND], W_KEY)])
                                           for v, k, r, kr in zip(V, K, VR, KR)],
            }
            for vname, cands in variants.items():
                results[f"{mname} | {form} | {vname}"] = recall(cands, vname)
            hy = variants["하이브리드 4목록 (가빈 후보 단계)"]   # 분석용: 질문별 후보 100개 (놓친 근거 분석에 씀)
            (DATA / f"cands_{slug}_{form}{'_b' if a.bm25_form else ''}.json").write_text(
                json.dumps({q["id"]: c[:100] for q, c in zip(qs, hy)}, ensure_ascii=False), encoding="utf-8")
        del model
        import gc
        import torch
        gc.collect()
        torch.cuda.empty_cache()

    print("\n정답 근거 재현율 (조·별표 단위, 색인에 있는 근거만 / 전체) — 후보 N개 안")
    print("모델 | 문서 모양 | 방식\t" + "\t".join(f"@{n}" for n in NS))
    for k, v in results.items():
        print(k + "\t" + "\t".join(f"{v[n][1]:.3f}/{v[n][0]:.3f}" for n in NS))
    out = ROOT / "out" / "eval" / "embed_baseline.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"questions": len(qs), "evidence": tot, "evidence_indexed": tot_idx,
                               "results": {k: {str(n): list(v[n]) for n in NS} for k, v in results.items()}}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"→ {out}")


if __name__ == "__main__":
    main()
