"""기존 벡터 색인(out/vector/BAAI__bge-m3)과 같은 청크를 Ollama 임베딩 모델로 다시 임베딩한다.

  uv run python scripts/build_vectors_ollama.py --model qwen3-embedding:4b-q8_0
결과: out/vector/{모델 이름}/ embeddings.npy, chunks.jsonl(원본 복사), meta.json
문서 쪽에는 지시문을 붙이지 않는다(Qwen3-Embedding 권장: 질의에만 지시문). 질의 지시문은 meta.json의 query_instruction에 적어 두고,
쓰는 쪽(scripts/eval_target_rag.py --embed 등)이 붙인다. 주소는 127.0.0.1 (localhost는 윈도우에서 호출마다 약 2초 지연, 리포트 53).
"""

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import requests

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "out" / "vector" / "BAAI__bge-m3"
QUERY_INSTRUCTION = "Instruct: Given a request to amend internal regulations, retrieve the regulation article that should be amended\nQuery: "


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen3-embedding:4b-q8_0")
    ap.add_argument("--url", default="http://127.0.0.1:11434")
    ap.add_argument("--batch", type=int, default=16)
    a = ap.parse_args()
    out = ROOT / "out" / "vector" / a.model.replace(":", "-").replace("/", "__")
    out.mkdir(parents=True, exist_ok=True)
    lines = (SRC / "chunks.jsonl").read_text(encoding="utf-8").splitlines()
    chunks = [json.loads(l) for l in lines]
    vecs, t = [], time.time()
    for i in range(0, len(chunks), a.batch):
        texts = [c["text"] for c in chunks[i:i + a.batch]]
        r = requests.post(f"{a.url}/api/embed", json={"model": a.model, "input": texts, "truncate": True}, timeout=600)
        r.raise_for_status()
        vecs.extend(r.json()["embeddings"])
        if (i // a.batch) % 25 == 0:
            print(f"  {i + len(texts)}/{len(chunks)} ({time.time() - t:.0f}초)", flush=True)
    v = np.asarray(vecs, dtype=np.float32)
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    np.save(out / "embeddings.npy", v)
    (out / "chunks.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "meta.json").write_text(json.dumps({"model": a.model, "backend": "ollama", "dim": int(v.shape[1]), "n": len(chunks),
                                               "source_chunks": str(SRC.relative_to(ROOT)), "query_instruction": QUERY_INSTRUCTION,
                                               "sec": round(time.time() - t, 1)}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"완료: {out} ({len(chunks)}개, {v.shape[1]}차원, {time.time() - t:.0f}초)")


if __name__ == "__main__":
    main()
