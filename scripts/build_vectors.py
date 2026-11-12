"""조문 벡터 색인을 만든다 (90건 전체).

  uv sync --extra vector
  uv run python scripts/build_vectors.py [--model BAAI/bge-m3]
결과: out/vector/{모델 이름}/ embeddings.npy, chunks.jsonl, meta.json, chroma/
"""

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from regcheck.vectors import DEFAULT_MODEL, OllamaEncoder, build_chunks, embed, load_model, save, to_chroma  # noqa: E402


def out_dir(model: str) -> Path:
    return ROOT / "out" / "vector" / model.replace("/", "__")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--ollama", metavar="MODEL", help="로컬 Ollama 모델로 NumPy 색인을 만든다")
    args = ap.parse_args()
    regs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((ROOT / "out" / "parsed").glob("[0-9]*.json"))]
    chunks = build_chunks(regs)
    t = time.time()
    model = OllamaEncoder(args.ollama) if args.ollama else load_model(args.model)
    vecs = embed(model, [c["text"] for c in chunks])
    out = out_dir(args.model)
    if args.ollama:
        save(out, chunks, vecs, args.ollama, backend="ollama", query_instruction="")
    else:
        save(out, chunks, vecs, args.model)
        to_chroma(out, chunks, vecs)
    print(f"{len(regs)}건, 청크 {len(chunks)}개, 차원 {vecs.shape[1]}, {time.time() - t:.0f}초 → {out}")


if __name__ == "__main__":
    main()
