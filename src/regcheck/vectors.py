"""조문 벡터 색인.

청크 = 본문 조의 항 하나(호·목 포함). 항 구분이 없는 조는 조 전체가 한 청크.
짧은 항(중앙 77자)에 맥락을 주려고 앞에 「규정명 > 제N조(제목)」을 붙인다.
부칙·별지는 넣지 않는다 (02-specs 별지 처리, 부칙은 옛 번호가 많음).

벡터 원본은 out/vector/{model}/embeddings.npy 로 따로 저장한다. 저장소(Chroma → pgvector)를 바꿔도 다시 계산하지 않기 위함.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # numpy·torch는 벡터 기능에서만 필요 (uv sync --extra vector)
    import numpy as np

DEFAULT_MODEL = "BAAI/bge-m3"


def build_chunks(regs: list[dict]) -> list[dict]:
    chunks = []
    for r in regs:
        for a in r["articles"]:
            if a["deleted"]:
                continue
            groups: dict[int, list[str]] = {}
            for it in a["items"]:
                groups.setdefault(it["path"][0], []).append(it["norm"])
            head = f"{r['name']} > {a['label']}" + (f"({a['title']})" if a["title"] else "")
            for hang, texts in groups.items():
                body = " ".join(t for t in texts if t)
                if not body:
                    continue
                chunks.append({
                    "id": f"{a['id']}#{hang}",
                    "text": f"{head}\n{body}",
                    "reg_id": r["reg_id"],
                    "reg_name": r["name"],
                    "hierarchy": r["hierarchy"],
                    "article_id": a["id"],
                    "label": a["label"],
                    "title": a["title"],
                    "chapter": a["chapter"],
                    "hang": hang,
                    "revised": r["revised"],
                })
    return chunks


def load_model(name: str = DEFAULT_MODEL):
    import torch
    from sentence_transformers import SentenceTransformer

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = SentenceTransformer(name, device=device)
    if device == "cuda":
        model.half()
    return model


def embed(model, texts: list[str], batch_size: int = 32) -> np.ndarray:
    import numpy as np

    return model.encode(texts, batch_size=batch_size, normalize_embeddings=True, show_progress_bar=False).astype(
        np.float32
    )


def save(out: Path, chunks: list[dict], vecs: np.ndarray, model_name: str) -> None:
    import numpy as np

    out.mkdir(parents=True, exist_ok=True)
    np.save(out / "embeddings.npy", vecs)
    with (out / "chunks.jsonl").open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    (out / "meta.json").write_text(
        json.dumps({"model": model_name, "count": len(chunks), "dim": int(vecs.shape[1])}, ensure_ascii=False),
        encoding="utf-8",
    )


def to_chroma(out: Path, chunks: list[dict], vecs: np.ndarray, collection: str = "articles_hang"):
    import chromadb

    client = chromadb.PersistentClient(path=str(out / "chroma"))
    try:
        client.delete_collection(collection)
    except Exception:
        pass
    col = client.create_collection(collection, metadata={"hnsw:space": "cosine"})
    keys = ("reg_id", "reg_name", "hierarchy", "article_id", "label", "title", "chapter", "hang", "revised")
    for i in range(0, len(chunks), 1000):
        part = chunks[i : i + 1000]
        col.add(
            ids=[c["id"] for c in part],
            embeddings=vecs[i : i + len(part)].tolist(),
            documents=[c["text"] for c in part],
            metadatas=[{k: c[k] for k in keys} for c in part],
        )
    return col


class Index:
    """저장된 벡터로 바로 검색 (작은 규모라 행렬곱으로 충분, Chroma와 결과 동일)."""

    def __init__(self, out: Path):
        import numpy as np

        self.vecs = np.load(out / "embeddings.npy")
        self.chunks = [json.loads(l) for l in (out / "chunks.jsonl").read_text(encoding="utf-8").splitlines()]
        self.model_name = json.loads((out / "meta.json").read_text(encoding="utf-8"))["model"]

    def search(self, qvec: np.ndarray, reg_id: str | None = None, k: int = 5) -> list[tuple[float, dict]]:
        import numpy as np

        scores = self.vecs @ qvec
        idx = [i for i in np.argsort(-scores) if reg_id is None or self.chunks[i]["reg_id"] == reg_id]
        return [(float(scores[i]), self.chunks[i]) for i in idx[:k]]

    def rank_of(self, qvec: np.ndarray, reg_id: str, article_id: str) -> tuple[int, float, float]:
        """대상 규정 안에서 인용된 조의 순위(1부터), 그 조의 최고 점수, 1위 점수."""
        hits = self.search(qvec, reg_id, k=10_000)
        top = hits[0][0] if hits else 0.0
        for rank, (s, c) in enumerate(hits, 1):
            if c["article_id"] == article_id:
                return rank, s, top
        return 0, 0.0, top
