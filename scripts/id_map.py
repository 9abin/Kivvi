"""우리 조문 ID ↔ 김가빈 챗봇 chunk_id 대응표 (챗봇 통합용 공통 키 확인).

  uv run python scripts/id_map.py 가빈_chunks.jsonl
입력: 가빈 저장소 rag_pipeline/out/chunks.jsonl (고유번호·id·조항·상태). 본문·본문_검색·cites는 읽지 않는다.
출력: out/id_map/chunk_map.tsv (우리 항 단위 키 → chunk_id), out/id_map/mismatch.tsv (한쪽에만 있는 키와 유형)
가빈 chunk_id 규칙: {고유번호}_제N조[의M][_K항], 부칙은 {고유번호}_부칙[...]
"""

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def our_keys() -> dict[str, dict]:
    """가빈 형식으로 만든 키 → 우리 조 정보. 항이 하나뿐인 조는 두 형식(접미어 있음·없음)을 모두 후보로."""
    out = {}
    for p in sorted((ROOT / "out" / "parsed").glob("[0-9]*.json")):
        r = json.loads(p.read_text(encoding="utf-8"))
        for a in r["articles"]:
            hangs = sorted({it["path"][0] for it in a["items"]}) or [0]
            for h in hangs:
                key = f"{r['reg_id']}_{a['label']}" + (f"_{h}항" if h else "")
                out[key] = {"reg_id": r["reg_id"], "article_id": a["id"], "hang": h, "single": hangs == [1] or hangs == [0],
                            "deleted": bool(a.get("deleted"))}
    return out


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    g = {}
    for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
        c = json.loads(line)
        g[c["id"]] = {"reg_id": c["고유번호"], "article": c.get("조항"), "status": c.get("상태")}
    ours = our_keys()
    rows, miss = [], []
    used = set()
    for key, o in ours.items():
        if key in g:
            rows.append((o["article_id"], o["hang"], key, "같음"))
            used.add(key)
            continue
        art_key = key.rsplit("_", 1)[0] if key.endswith("항") else key
        if o["single"] and key.endswith("_1항") and art_key in g:   # 항이 하나뿐인 조: 우리는 「_1항」, 가빈은 조 전체
            rows.append((o["article_id"], o["hang"], art_key, "단일 항 조"))
            used.add(art_key)
        elif not key.endswith("항") and key + "_1항" in g:          # 반대: 우리는 조 전체, 가빈은 「_1항」
            rows.append((o["article_id"], o["hang"], key + "_1항", "단일 항 조"))
            used.add(key + "_1항")
        elif key.endswith("항") and art_key in g:                  # 가빈이 이 조를 항으로 나누지 않음 → 조 청크에 여러 항을 대응
            rows.append((o["article_id"], o["hang"], art_key, "가빈 조 단위"))
            used.add(art_key)
        else:
            miss.append((key, "우리만", "삭제 조" if o["deleted"] else "항 번호 차이"))
    our_articles = {k.rsplit("_", 1)[0] if k.endswith("항") else k for k in ours}
    for key, c in g.items():
        if key in used:
            continue
        art_key = key.rsplit("_", 1)[0] if key.endswith("항") else key
        kind = ("부칙" if "_부칙" in key else "삭제 조" if c["status"] == "삭제" else "조문 없는 문서" if not c["article"]
                else "같은 조, 항 나눔 차이" if art_key in our_articles else "항 번호 차이")
        if kind != "부칙":
            miss.append((key, "가빈만", kind))
    out = ROOT / "out" / "id_map"
    out.mkdir(parents=True, exist_ok=True)
    (out / "chunk_map.tsv").write_text("our_article_id\thang\tchunk_id\tmatch\n" + "\n".join("\t".join(map(str, r)) for r in rows) + "\n",
                                       encoding="utf-8")
    (out / "mismatch.tsv").write_text("key\tside\tkind\n" + "\n".join("\t".join(r) for r in sorted(miss)) + "\n", encoding="utf-8")
    m = Counter(r[3] for r in rows)
    print(f"우리 항 키 {len(ours)}개 중 대응 {len(rows)} ({len(rows) / len(ours):.1%}): {dict(m)}")
    print(f"남은 것 {len(miss)}: {dict(Counter((s, k) for _, s, k in miss))}")
    print(f"→ {out}")


if __name__ == "__main__":
    main()
