"""규정·조 찾기(target.find_targets)를 RAG 팀의 독립 라벨 평가셋으로 잰다.

  uv run --extra db python scripts/eval_target_rag.py --file C:/Users/Admin/Downloads/answer_eval_100_tiered.jsonl [--rewrite]
입력은 질문(개정 요청이 아님)이고 정답은 RAG 팀이 단 근거 (규정, 조)다 → 「문장만 보고 어느 규정·조를 가리키는가」를 우리와 독립인 라벨로 본다.
지표: 규정 적중(필수 근거의 규정이 상위 k 후보에 있는가), 조 적중(필수 근거의 (규정, 조)가 상위 k 안에 있는가). 질문 단위와 근거 단위를 모두 낸다.
CPU로 돌려 GPU를 쓰는 서버와 함께 실행할 수 있다(느림). 출력: out/eval/target_rag.json
"""

import argparse
import collections
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c - h) / d, (c + h) / d


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--rewrite", action="store_true", help="점수가 낮을 때 로컬 LLM으로 바꿔 써 다시 찾기(느림)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--index", default="BAAI__bge-m3", help="out/vector 아래 색인 폴더. Ollama 색인(meta.json backend=ollama)이면 질문에 지시문을 붙여 Ollama로 임베딩")
    ap.add_argument("--search-k", type=int, default=0, help="리랭커에 넘길 후보 조 수 (0이면 target.SEARCH_K 그대로)")
    ap.add_argument("--device", default="cpu", help="cpu 또는 cuda (GPU를 쓰는 서버와 같이 돌릴 때는 cpu)")
    ap.add_argument("--tag", default="", help="결과 파일 이름 뒤에 붙일 이름")
    ap.add_argument("--rerank-reg", action="store_true", help="리랭커에 넘기는 조문 앞에 규정 이름을 붙인다")
    ap.add_argument("--text-chars", type=int, default=0, help="리랭커에 넘기는 조문 글자 수 (0이면 target.TEXT_CHARS 그대로 400)")
    a = ap.parse_args()
    import numpy as np
    from sentence_transformers import CrossEncoder, SentenceTransformer
    from regcheck import target
    from regcheck.target import find_targets

    if a.search_k:
        target.SEARCH_K = a.search_k
    target.RERANK_REG_NAME = a.rerank_reg
    if a.text_chars:
        target.TEXT_CHARS = a.text_chars
    regs = {r["reg_id"]: r for r in (json.loads(p.read_text(encoding="utf-8")) for p in (ROOT / "out" / "parsed").glob("[0-9]*.json"))}
    by_name = {r["name"].replace(" ", ""): r["reg_id"] for r in regs.values()}
    d = ROOT / "out" / "vector" / a.index
    index = (np.load(d / "embeddings.npy"), [json.loads(l) for l in (d / "chunks.jsonl").read_text(encoding="utf-8").splitlines()])
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8")) if (d / "meta.json").exists() else {}
    if meta.get("backend") == "ollama":
        import requests

        def embed(texts):
            r = requests.post("http://127.0.0.1:11434/api/embed", timeout=300,
                              json={"model": meta["model"], "input": [meta.get("query_instruction", "") + t for t in texts], "truncate": True})
            v = np.asarray(r.json()["embeddings"], dtype=np.float32)
            return v / np.linalg.norm(v, axis=1, keepdims=True)
    else:
        model = SentenceTransformer("BAAI/bge-m3", device=a.device)
        embed = lambda t: model.encode(t, normalize_embeddings=True)  # noqa: E731
    rr = CrossEncoder("BAAI/bge-reranker-v2-m3", device=a.device)
    rerank = lambda q, ts: rr.predict([(q, t) for t in ts], batch_size=16, show_progress_bar=False).tolist()  # noqa: E731
    rewrite = None
    if a.rewrite:
        from regcheck.nl import _post

        def rewrite(q: str) -> str:
            msg = [{"role": "system", "content": "회사 내부 규정(내규)의 조문을 찾는 검색어를 만든다. 요청을 내규 조문에 나올 법한 낱말로 바꿔 한 줄로만 쓴다. 설명 없이 검색어만."},
                   {"role": "user", "content": q}]
            return _post("qwen2.5:7b", msg, timeout=60).strip().splitlines()[0][:80]

    rows = [json.loads(l) for l in Path(a.file).read_text(encoding="utf-8").splitlines() if l.strip()]
    if a.limit:
        rows = rows[:a.limit]
    ks = (1, 3, 5, target.SEARCH_K)   # 마지막은 후보 전체(리랭크 전 천장)
    q_reg, q_art, e_reg, e_art = (collections.Counter() for _ in range(4))
    n_q = n_e = 0
    out = []
    for g in rows:
        need = []   # 필수 근거의 (규정ID, 조 라벨)
        for ev in g["근거"]:
            if ev.get("tier", "필수") != "필수":
                continue
            rid = by_name.get(ev["규정"].replace(" ", ""))
            m = re.match(r"(제\s*\d+\s*조(?:\s*의\s*\d+)?)", ev["조항"])
            if rid and m:
                need.append((rid, re.sub(r"\s+", "", m.group(1))))
        if not need:
            continue
        res = find_targets(g["q"], regs, index, embed, rerank, rewrite=rewrite, k=target.SEARCH_K)
        cands = [(c["reg_id"], re.sub(r"\s+", "", c["label"])) for c in res["candidates"]]
        n_q += 1
        for k in ks:
            top = cands[:k]
            q_reg[k] += any(r in {x[0] for x in top} for r, _ in need)
            q_art[k] += any(n in top for n in need)
        for n in need:
            n_e += 1
            for k in ks:
                e_reg[k] += n[0] in {x[0] for x in cands[:k]}
                e_art[k] += n in cands[:k]
        out.append({"id": g["id"], "need": need, "got": cands[:5], "confident": res["confident"],
                    "pool": [{"c": [c["reg_id"], re.sub(r"\s+", "", c["label"])], "s": c["score"], "v": c.get("vrank")} for c in res["candidates"]]})
    print(f"질문 {n_q}개 · 필수 근거 {n_e}개 · 색인 {a.index} · 후보 조 {target.SEARCH_K} · rewrite={'켬' if a.rewrite else '끔'}")
    for name, cnt, n in (("질문 단위·규정", q_reg, n_q), ("질문 단위·조", q_art, n_q), ("근거 단위·규정", e_reg, n_e), ("근거 단위·조", e_art, n_e)):
        print(f"  {name}: " + " · ".join(f"@{k} {cnt[k]}/{n}={cnt[k] / n:.0%} [{wilson(cnt[k], n)[0]:.0%},{wilson(cnt[k], n)[1]:.0%}]" for k in ks))
    p = ROOT / "out" / "eval"
    p.mkdir(parents=True, exist_ok=True)
    (p / f"target_rag{'_rw' if a.rewrite else ''}{('_' + a.tag) if a.tag else ''}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
