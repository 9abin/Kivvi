"""색인에 없는 단위(부칙 · 별표 · 별지 서식)의 임베딩을 만든다 (bge-m3, 단위당 하나, 앞 512 토큰). 영향 점검의 의미 이웃이 이 단위에도 닿게 한다.

  uv run python scripts/build_ext_emb.py        # out/kg/ext_emb.npy, ext_ids.json
입력: out/kg/nodes.jsonl, out/parsed. 모델: BAAI/bge-m3 (HF 캐시, 오프라인)
"""
import glob
import json
import os
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("HF_HUB_OFFLINE", "1")


def main():
    from sentence_transformers import SentenceTransformer
    nodes = {json.loads(l)["id"]: json.loads(l) for l in (ROOT / "out/kg/nodes.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()}
    regs = {r["reg_id"]: r for r in (json.loads(Path(p).read_text(encoding="utf-8")) for p in glob.glob(str(ROOT / "out/parsed/[0-9]*.json")))}
    text = {}
    for r in regs.values():
        for ad in r.get("addenda") or []:
            for x in ad.get("articles") or []:
                text[x["id"]] = x["norm"]
        for ap in r.get("appendices") or []:
            text[ap["id"]] = (ap.get("title") or "") + "\n" + (ap.get("text") or "")
    ids = [k for k in text if k in nodes]
    texts = [f"{regs[nodes[k]['reg']]['name']} > {nodes[k].get('label', '')} {re.sub(chr(10) + '+', chr(10), text[k])}"[:1500] for k in ids]
    m = SentenceTransformer("BAAI/bge-m3", device="cuda")
    m.max_seq_length = 512
    emb = m.encode(texts, batch_size=16, normalize_embeddings=True, show_progress_bar=False)
    out = ROOT / "out/kg"
    np.save(out / "ext_emb.npy", emb.astype(np.float32))
    (out / "ext_ids.json").write_text(json.dumps(ids, ensure_ascii=False), encoding="utf-8")
    print(len(ids), "단위 임베딩 저장", emb.shape)


if __name__ == "__main__":
    main()
