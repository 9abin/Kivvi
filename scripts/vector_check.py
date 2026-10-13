"""벡터로 '대상 불일치' 후보를 찾는 실험 (의미 검사 1단계, LLM 없음).

인용 문맥(엣지 context)을 질의로, 인용 대상 규정 안의 조문을 검색한다.
인용된 조가 상위에 없고 다른 조가 훨씬 가까우면 '대상 불일치 후보'로 본다. 판정이 아니라 후보 제시다.

  uv run python scripts/vector_check.py [--model BAAI/bge-m3]
먼저 extract_edges.py(21건)와 build_vectors.py 를 실행한다.
"""

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from build_vectors import out_dir  # noqa: E402
from regcheck.vectors import DEFAULT_MODEL, Index, embed, load_model  # noqa: E402

GOLD = {c["src"] + "|" + c["raw"]: c["id"] for c in json.loads((ROOT / "eval" / "week1_citations.json").read_text(encoding="utf-8"))["cases"] if c["expect"] == "semantic"}


def query_text(e: dict) -> str:
    # 인용 표현 자체(조 번호)는 의미가 없으므로 지우고, 앞뒤 문맥만 쓴다
    return re.sub(r"\s+", " ", e["context"].replace(e["raw"], " ")).strip()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--rank", type=int, default=3, help="인용된 조가 이 순위 밖이면 후보")
    ap.add_argument("--gap", type=float, default=0.05, help="1위와 인용된 조의 점수 차가 이 이상이면 후보")
    args = ap.parse_args()
    index = Index(out_dir(args.model))
    model = load_model(args.model)
    edges = [json.loads(l) for l in (ROOT / "out" / "edges" / "edges.jsonl").read_text(encoding="utf-8").splitlines()]
    n_articles = {}
    for c in index.chunks:
        n_articles.setdefault(c["reg_id"], set()).add(c["article_id"])

    targets = [e for e in edges if e["status"] in ("ok", "title_mismatch") and e["target_type"] in ("article", "chapter")
               and e["target_reg"] and e["src_kind"] != "부칙" and len(n_articles.get(e["target_reg"], ())) >= 5]
    qv = embed(model, [query_text(e) for e in targets])
    rows = []
    for e, q in zip(targets, qv):
        # 인용하는 조문 자신은 질의 문맥과 당연히 가장 비슷하므로 후보에서 뺀다
        src_article = e["src"].rsplit("-", 3)[0]
        hits = [(s, c) for s, c in index.search(q, e["target_reg"], k=10_000) if c["article_id"] != src_article]
        top_s, top_c = hits[0]
        if e["target_type"] == "chapter":
            jang = e["target_id"].rsplit("장", 1)[1]
            rank = next((i for i, (s, c) in enumerate(hits, 1) if re.match(rf"제\s*{jang}\s*장", c["chapter"])), 0)
            cited_s = hits[rank - 1][0] if rank else 0.0
        else:
            seen, rank, cited_s = [], 0, 0.0
            for s, c in hits:  # 순위는 조 단위로 센다 (같은 조의 여러 항은 한 번)
                if c["article_id"] in seen:
                    continue
                seen.append(c["article_id"])
                if c["article_id"] == e["target_id"]:
                    rank, cited_s = len(seen), s
                    break
        flag = rank > args.rank and (top_s - cited_s) >= args.gap
        key = e["src"] + "|" + e["raw"]
        gold = next((g for k, g in GOLD.items() if k.split("|")[0] == e["src"] and k.split("|")[1] in e["raw"]), "")
        top3 = []
        for s, c in hits:
            if c["article_id"] not in [t["article_id"] for t in top3]:
                top3.append(c)
            if len(top3) == 3:
                break
        rows.append((gold, flag, rank, cited_s, top_s, e, top_c, top3))

    # 2단계(scripts/semantic_check.py) 입력: 검사한 인용 전체와 1단계 후보 여부
    sem = ROOT / "out" / "semantic"
    sem.mkdir(parents=True, exist_ok=True)
    with (sem / "stage1.jsonl").open("w", encoding="utf-8") as f:
        for gold, flag, rank, cs, ts, e, tc, t3 in rows:
            f.write(json.dumps({"gold": gold, "flag": flag, "rank": rank, "cited_score": cs, "top_score": ts,
                                "edge": e, "top3": [c["article_id"] for c in t3]}, ensure_ascii=False) + "\n")

    print(f"검사한 인용 {len(rows)}개 (대상 규정 조문 5개 이상, 부칙 제외), 기준: 순위>{args.rank} 그리고 점수차≥{args.gap}\n")
    print("■ 1주차 의미 검사 대상")
    for gold, flag, rank, cs, ts, e, tc, t3 in sorted((r for r in rows if r[0]), key=lambda r: r[0]):
        print(f"  {gold:4} {'후보' if flag else '통과':4} 인용 {e['raw']:8} 순위 {rank:3}  점수 {cs:.3f} / 1위 {ts:.3f}")
        print("       상위 3: " + ", ".join(f"{c['label']}({c['title']})" + (f" [{c['chapter']}]" if e['target_type'] == 'chapter' else '') for c in t3))
    flagged = [r for r in rows if r[1] and not r[0]]
    ranks = [r[2] for r in rows if not r[0] and r[2]]
    print(f"\n■ 나머지 인용 {len(rows) - sum(1 for r in rows if r[0])}개: 인용된 조 순위 중앙값 {statistics.median(ranks):.0f}, 1위 비율 {sum(1 for x in ranks if x == 1) / len(ranks):.0%}")
    print(f"  후보로 걸린 것 {len(flagged)}개 (사람 확인 필요)")
    for gold, flag, rank, cs, ts, e, tc, t3 in sorted(flagged, key=lambda r: r[3] - r[4])[:15]:
        print(f"  {e['src']:22} 인용 {e['target_doc'] or '(자기 규정)'} {e['raw']:10} 순위 {rank:3} {cs:.3f}/{ts:.3f} → 1위 {tc['label']}({tc['title']})")
        print(f"      …{query_text(e)[:90]}")


if __name__ == "__main__":
    main()
