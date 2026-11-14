"""Run the extracted search core without the original repository."""
import argparse
import json
import re
import sys
from pathlib import Path

from ontology_search import DomainGraph, Limits, ScopedPropagatingGraph

DATA = Path(__file__).resolve().parent / "data"


def read_jsonl(path):
    with path.open(encoding="utf-8") as source:
        return [json.loads(line) for line in source if line.strip()]


def compact(text):
    return re.sub(r"\s+", "", text or "")


def label_code(label):
    text = compact(label)
    article = re.fullmatch(r"제(\d+)조(?:의(\d+))?", text)
    if article:
        return article[1] + ("의" + article[2] if article[2] else "")
    annex = re.fullmatch(r"(별표|별지)(?:제)?(\d+)(?:호)?(?:서식)?", text)
    if annex:
        suffix = "제" + annex[2] + "호서식" if annex[1] == "별지" else annex[2]
        return annex[1] + suffix
    return text


def main():
    parser = argparse.ArgumentParser(description="온톨로지 영향 후보 검색 (오류 판정 아님)")
    parser.add_argument("--anchor", required=True, help="출발 조 ID, 예: 24024:30")
    parser.add_argument("--seed", nargs="*", default=[], help="수정 항·호의 item ID")
    parser.add_argument("--method", choices=["domain", "scoped"], default="domain")
    parser.add_argument("--query", default="", help="변경 주제 또는 검색어")
    parser.add_argument("--amendment", default="", help="scoped 방식의 개정문")
    parser.add_argument("--kind", default="값 변경", help="변경 종류")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--lexical", action="store_true", help="BM25 점수로 그래프 동점 정렬 (numpy 필요)")
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit은 1 이상이어야 합니다.")
    if args.method == "scoped" and not args.amendment.strip():
        parser.error("scoped 방식에는 --amendment가 필요합니다.")

    corpus = {row["id"]: row for row in read_jsonl(DATA / "corpus.jsonl")}
    if args.anchor not in corpus:
        parser.error(f"코퍼스에 없는 출발 조 ID: {args.anchor}")
    anchor = corpus[args.anchor]
    item_ids = {item.get("logical_id", item["id"]) for item in anchor.get("items", [])}
    unknown = set(args.seed) - item_ids
    if unknown:
        parser.error(f"출발 조에 없는 item ID: {', '.join(sorted(unknown))}")

    selected = [item for item in anchor.get("items", [])
                if item.get("logical_id", item["id"]) in args.seed]
    focus = "\n".join(item["text"] for item in selected) or anchor["text"]
    query = args.query or focus
    lexical = {}
    if args.lexical:
        try:
            from ontology_search.bm25 import BM25
        except ModuleNotFoundError as error:
            if error.name != "numpy":
                raise
            parser.error('BM25 옵션에는 numpy가 필요합니다: python -m pip install "numpy>=1.26"')
        rows = list(corpus.values())
        bm25 = BM25([row["document"] + " " + row["label"] + " " + row["title"] + "\n" + row["text"] for row in rows])
        lexical = dict(zip(corpus, bm25.scores(query).tolist()))

    schema = DomainGraph(DATA / "ontology.db")
    stats = schema.stats
    if args.method == "domain":
        ranking, paths = schema.rank(corpus, [anchor], query, lexical, args.seed)
    else:
        graph = ScopedPropagatingGraph(corpus, schema, read_jsonl(DATA / "references.jsonl"), label_code, compact)
        payload = graph.initial_payload(args.anchor, args.seed, {"종류": args.kind, "개정문": args.amendment}, focus)
        ranking, paths, stats = graph.rank(args.anchor, payload, lexical, Limits())

    result = {
        "anchor": args.anchor,
        "method": args.method,
        "candidate_count": len(ranking),
        "results": [{"id": aid, "document": corpus[aid]["document"], "label": corpus[aid]["label"],
                     "paths": paths.get(aid, [])} for aid in ranking[:args.limit]],
        "stats": stats,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
