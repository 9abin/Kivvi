"""조 → 연관 조 표 초안 만들기 (검색_설계.md 12.5, TOPIC_MODE="article").

주제어 사전(data/manual/topics.json)으로 조문마다 어느 주제를 담는지 표를 만든 뒤 뒤집어서,
"이 조와 같은 주제를 담은 다른 조"를 연관 조로 적는다. 사람이 검토하는 자동 초안이다.

- 등급: 연관 조의 제목(제N조(…))에 주제어가 있으면 "핵심", 본문에만 있으면 "참고". 검색기는 핵심을 먼저 붙인다.
- 범위어(링크=false)는 쓰지 않는다. 주제어 사전의 '함께' 조문은 그 주제를 담은 모든 조의 연관에 "핵심"으로 들어간다.
- 같은 규정 안의 조도 연관에 넣는다 (복무규정 제21조 ↔ 제22·23조). 같은 조는 넣지 않는다.

출력
- data/manual/related_articles.json: 검색기가 읽는 표 (조 → [{"대상", "주제", "등급"}])
- data/manual/related_articles_candidates.csv: 검토용 표 (출발 조, 주제, 연관 조, 등급, 제목, 채택 칸)

실행 (저장소 루트에서): uv run python -m scripts.build_related_articles [--max-per-topic N] [--broad M]
  --max-per-topic N: 한 주제에서 조마다 넣을 연관 조 수 상한 (핵심 먼저). 기본 8. 0 = 전부.
  --broad M: 조문이 M개를 넘는 넓은 주제(보수·징계·예산 등)는 제목에 주제어가 있는 조(핵심)만 연관으로 넣는다. 기본 60. 0 = 끔.
  상한 없이 돌리면(--max-per-topic 0 --broad 0) 연관 26만 개, 조당 중앙값 127개가 나와 검토도 예산도 불가능하다 (2026.10.15).
"""
import argparse
import csv
import json
import os
import re
import sys
from collections import defaultdict

from config import MANUAL_DIR, OUT_DIR, RELATED_ARTICLES
from src.search import topics

sys.stdout.reconfigure(encoding="utf-8")
CSV_OUT = os.path.join(MANUAL_DIR, "related_articles_candidates.csv")
_TITLE = re.compile(r"^제\d+조(?:의\d+)?\((.+?)\)")


def load_articles():
    rows = []
    with open(os.path.join(OUT_DIR, "chunks.jsonl"), encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if r.get("상태") != "정상" or r["id"].endswith("_부칙") or not r.get("조항", "").startswith("제"):
                continue
            body = r.get("본문_검색") or r.get("본문", "")
            m = _TITLE.match(r.get("본문", ""))
            rows.append({"chunk_id": r["id"], "doc_id": r["고유번호"], "문서명": r["문서명"], "article": r["조항"],
                         "body_search": body, "제목": m.group(1) if m else ""})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-per-topic", type=int, default=8)
    ap.add_argument("--broad", type=int, default=60)
    args = ap.parse_args()

    table = topics.load()
    rows = load_articles()
    by_id = {r["chunk_id"]: r for r in rows}
    topic_chunks, chunk_topics = topics.build_index(rows, table)
    pinned = {name: [p["대상"] for p in spec.get("함께", [])] for name, spec in table.items()}

    related = defaultdict(list)
    broad = {name for name, members in topic_chunks.items() if args.broad and len(members) > args.broad}
    for name, members in topic_chunks.items():
        ranked = topics.rank_candidates(members, chunk_topics, [name], len(members))
        if name in broad:
            ranked = [c for c in ranked if chunk_topics[c][name][0]]  # 넓은 주제: 제목에 주제어가 있는 조만
        for src in members:
            picks = []
            for p in pinned.get(name, []):
                if p != src and p in by_id:
                    picks.append({"대상": p, "주제": name, "등급": "핵심", "이유": next(
                        x.get("이유", "") for x in table[name]["함께"] if x["대상"] == p)})
            for tgt in ranked:
                if tgt == src or any(x["대상"] == tgt for x in picks):
                    continue
                grade = "핵심" if chunk_topics[tgt][name][0] else "참고"
                picks.append({"대상": tgt, "주제": name, "등급": grade})
                if args.max_per_topic and len(picks) >= args.max_per_topic:
                    break
            related[src].extend(picks)

    out = {"_설명": "조 → 연관 조 표 (자동 초안, scripts/build_related_articles.py). 같은 주제어를 담은 조를 연관으로 적었다. "
                   "등급 핵심 = 연관 조 제목에 주제어가 있음 / 참고 = 본문에만 있음. 사람이 related_articles_candidates.csv로 "
                   "검토해 뺄 것은 빼고 이유를 적는다. 검색기(TOPIC_MODE=article)는 찾은 조마다 이 표를 조회만 한다."}
    out.update({k: v for k, v in sorted(related.items())})
    with open(RELATED_ARTICLES, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    with open(CSV_OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["출발 조", "출발 규정·조항", "출발 제목", "주제", "연관 조", "연관 규정·조항", "연관 제목", "등급", "채택(O/X)", "메모"])
        for src, links in sorted(related.items()):
            s = by_id[src]
            for x in links:
                t = by_id[x["대상"]]
                w.writerow([src, f"{s['문서명']} {s['article']}", s["제목"], x["주제"], x["대상"],
                            f"{t['문서명']} {t['article']}", t["제목"], x["등급"], "", x.get("이유", "")])

    n_links = sum(len(v) for v in related.values())
    sizes = sorted(len(v) for v in related.values())
    print(f"조 {len(rows)}개 중 연관이 있는 조 {len(related)}개, 연관 {n_links}개 "
          f"(조당 중앙값 {sizes[len(sizes)//2]}, 최대 {sizes[-1]}, 핵심 {sum(1 for v in related.values() for x in v if x['등급']=='핵심')})")
    print(f"넓은 주제(조문 {args.broad}개 초과, 핵심만 연결) {len(broad)}개: {', '.join(sorted(broad))}")
    print(f"저장: {RELATED_ARTICLES}, {CSV_OUT}")


if __name__ == "__main__":
    main()
