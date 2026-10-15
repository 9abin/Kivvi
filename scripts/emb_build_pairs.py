"""임베딩 튜닝 학습 쌍 만들기: 합성 질문(pairs_v3_good) + 어려운 오답 (docs/09 §6).

  uv run --extra db python scripts/emb_build_pairs.py
입력: data/embedding/pairs_v3_good.jsonl (gen_questions.py qc가 내보낸, 자동 검사를 통과한 질문), 공유 DB(읽기 전용), 기본 BGE-m3 임베딩 캐시
오답 고르기
  1. 질문을 기본 BGE-m3로 검색한 상위 30개 청크 중, 정답과 같은 조(·별표)의 청크는 뺀다 (같은 조의 다른 항은 정답 후보라 오답이 아님)
  2. 같은 조의 청크는 하나만 (조 단위로 다양하게)
  3. false negative 방지: 후보가 사실 질문의 답일 수 있으면 학습을 망친다. bge-reranker-v2-m3로 (질문, 정답)과 (질문, 후보)를 같은 입력 모양으로 채점해,
     후보 점수가 정답 점수보다 낮거나(−0.05 이상) 아예 낮은 것(0.10 미만)만 오답으로 쓴다.
     (처음에는 앞 조건만 썼더니 간접 질문처럼 정답 점수가 낮은 질문의 38%가 오답을 못 받았다 → 뒤 조건을 추가)
  4. 남은 것 중 기본 순위가 높은 것(가장 헷갈리는 것)부터 최대 4개
분할: 규정(문서) 단위로 약 10%를 검증으로 뗀다 (같은 규정의 청크가 학습·검증에 섞이지 않게).
출력: data/embedding/pairs_v3_train.jsonl, 통계는 화면과 data/embedding/pairs_v3_train.stats.json
"""

import collections
import json
import random
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import exp_embed_baseline as E  # noqa: E402

DATA = ROOT / "data" / "embedding"
POOL, MAX_NEG, MARGIN, ABS_LOW, VAL_FRAC = 30, 4, 0.05, 0.10, 0.10
SCORES = DATA / "pairs_v3_rerank_scores.json"      # reranker 점수 캐시 (규칙만 바꿀 때 다시 채점하지 않으려고)


def rerank_text(r):
    return f"[{r['title']} {r['article']}] {r['body_search']}"       # 가빈 search.rerank_text와 같은 모양 (장 정보 없음)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    from sentence_transformers import CrossEncoder, SentenceTransformer

    rows = E.load_db()
    ids = [r["chunk_id"] for r in rows]
    pos_of = {c: i for i, c in enumerate(ids)}
    node = [E.node_of_chunk(c) for c in ids]
    emb = np.load(DATA / "emb_BAAI_bge_m3_v0.npy")
    pairs = [json.loads(l) for l in (DATA / "pairs_v3_good.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    print(f"질문 {len(pairs)}개")

    model = SentenceTransformer("BAAI/bge-m3", device="cuda")
    model.half()
    Q = model.encode([p["q"] for p in pairs], normalize_embeddings=True, convert_to_numpy=True, batch_size=64)
    S = Q @ emb.T
    del model
    import gc
    import torch
    gc.collect()
    torch.cuda.empty_cache()

    cand = []
    for k, p in enumerate(pairs):
        order = np.argsort(-S[k])[:200]
        seen, lst = {p["node"]}, []
        for j in order:
            n = node[j]
            if n in seen:
                continue
            seen.add(n)
            lst.append(int(j))
            if len(lst) >= POOL:
                break
        cand.append(lst)

    sc = collections.defaultdict(dict)
    if SCORES.exists() and json.loads(SCORES.read_text(encoding="utf-8")).get("n") == len(pairs):
        for k, d in json.loads(SCORES.read_text(encoding="utf-8"))["scores"].items():
            sc[int(k)] = {int(j): v for j, v in d.items()}
        print("reranker 점수 캐시 사용")
    else:
        ce = CrossEncoder("BAAI/bge-reranker-v2-m3", device="cuda", model_kwargs={"dtype": "float16"})
        texts = [rerank_text(r) for r in rows]
        jobs, owners = [], []
        for k, p in enumerate(pairs):
            pj = pos_of[p["chunk_id"]]
            for j in [pj] + cand[k]:
                jobs.append((p["q"], texts[j]))
                owners.append((k, j))
        print(f"reranker 채점 {len(jobs):,}쌍 …")
        scores = ce.predict(jobs, batch_size=128, show_progress_bar=False)
        for (k, j), s in zip(owners, scores):
            sc[k][j] = float(s)
        SCORES.write_text(json.dumps({"n": len(pairs), "scores": {str(k): {str(j): v for j, v in d.items()} for k, d in sc.items()}}), encoding="utf-8")
        del ce
        gc.collect()
        torch.cuda.empty_cache()

    regs = sorted({p["chunk_id"].split("_")[0] for p in pairs})
    rng = random.Random(11)
    val_regs = set(rng.sample(regs, max(1, round(len(regs) * VAL_FRAC))))
    out, drop_fn, kept, no_neg = [], 0, 0, 0
    for k, p in enumerate(pairs):
        pj = pos_of[p["chunk_id"]]
        ps = sc[k][pj]
        ok = []
        for j in cand[k]:
            if sc[k][j] <= ps - MARGIN or sc[k][j] < ABS_LOW:
                ok.append(j)
            else:
                drop_fn += 1
        negs = ok[:MAX_NEG]
        kept += len(negs)
        no_neg += not negs
        out.append({"q": p["q"], "form": p["form"], "node": p["node"], "chunk_id": p["chunk_id"], "pos_score": round(ps, 3),
                    "negs": [ids[j] for j in negs], "neg_scores": [round(sc[k][j], 3) for j in negs],
                    "split": "val" if p["chunk_id"].split("_")[0] in val_regs else "train"})
    path = DATA / "pairs_v3_train.jsonl"
    path.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in out), encoding="utf-8")
    tr = [x for x in out if x["split"] == "train"]
    stats = {"questions": len(out), "train": len(tr), "val": len(out) - len(tr), "val_regs": sorted(val_regs),
             "false_negative_guard_dropped": drop_fn, "candidates_total": sum(len(c) for c in cand),
             "negs_kept": kept, "questions_without_negs": no_neg,
             "by_form": dict(collections.Counter(x["form"] for x in tr)),
             "pos_score_mean": round(float(np.mean([x["pos_score"] for x in out])), 3)}
    (DATA / "pairs_v3_train.stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False, indent=1))
    print(f"→ {path}")


if __name__ == "__main__":
    main()
