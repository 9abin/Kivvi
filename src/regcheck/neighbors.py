"""의미 이웃: 다른 규정에서 같은 주제로 보이는 조문 (인용·제목·용어로 이어지지 않아도). 조문 대 조문 벡터 유사도를 미리 계산해 둔다.

  build(index_dir)  → {조문ID: [{"article_id", "score"}]}   항 벡터(out/vector/{모델}) 쌍의 최댓값을 조 단위로 모음
  save(nb) / load()  out/relations/neighbors.json

규칙 (261015 점수 구간별 표본을 읽고 정함, 리포트 44):
  · 같은 규정은 뺀다 (같은 규정 안 영향은 인용·정의 용어가 맡음)
  · 「목적」·「시행일」처럼 모든 규정에 있는 흔한 제목의 조는 양쪽 다 뺀다 (서로 다 비슷해 보임)
  · 점수 MIN_SCORE 이상, 조마다 MAX_PER 개까지 (0.80 이상은 대부분 같은 주제, 0.72 아래는 잡음이 많음)
  · gap: 다음으로 비슷한 조문과의 점수 차. 절대 점수(바닥)와 함께 본다 — 벡터 검색은 「없음」을 돌려주지 않으므로 바닥은 필요하고, 상대 간격으로 걸러낸다
판정이 아니라 「같은 주제로 보이는 조문」 후보다. 변경안 점검에서 낮은 우선순위로만 쓴다.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INDEX = ROOT / "out" / "vector" / "BAAI__bge-m3"
OUT = ROOT / "out" / "relations" / "neighbors.json"
MIN_SCORE = 0.78
MAX_PER = 3


def build(index_dir: Path = DEFAULT_INDEX, min_score: float = MIN_SCORE, max_per: int = MAX_PER) -> dict[str, list[dict]]:
    import numpy as np

    from .change_check import GENERIC_TITLES

    vecs = np.load(index_dir / "embeddings.npy")
    chunks = [json.loads(l) for l in (index_dir / "chunks.jsonl").read_text(encoding="utf-8").splitlines()]
    reg = np.array([c["reg_id"] for c in chunks])
    keep = np.array([(c.get("title") or "") not in GENERIC_TITLES for c in chunks])
    S = vecs @ vecs.T
    S[reg[:, None] == reg[None, :]] = -1.0
    S[~keep, :] = -1.0
    S[:, ~keep] = -1.0
    best: dict[str, dict[str, float]] = defaultdict(dict)
    for i in np.where(keep)[0]:
        for j in np.argsort(-S[i])[:40]:
            s = float(S[i, j])
            if s < -0.5:
                break
            a, b = chunks[i]["article_id"], chunks[j]["article_id"]
            best[a][b] = max(best[a].get(b, 0.0), s)
    out: dict[str, list[dict]] = {}
    for a, v in best.items():
        ranked = sorted(v.items(), key=lambda x: -x[1])
        rows = []
        for r, (b, s) in enumerate(ranked[:max_per]):
            if s < min_score:
                break
            # gap = 이 이웃이 다음으로 비슷한 조문보다 얼마나 높은가. 간격이 작으면(비슷한 이웃이 여럿) 같은 주제라도 한쪽을
            # 고칠 때 다른 쪽을 봐야 하는 관계일 가능성이 낮다 (261015 판정 30쌍: 0.03 이상이면 정밀도 57% → 75%, 리포트 48)
            nxt = ranked[r + 1][1] if r + 1 < len(ranked) else 0.0
            rows.append({"article_id": b, "score": round(s, 3), "gap": round(s - nxt, 3)})
        if rows:
            out[a] = rows
    return out


def save(nb: dict, path: Path = OUT) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(nb, ensure_ascii=False), encoding="utf-8")


def load(path: Path = OUT) -> dict[str, list[dict]] | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
