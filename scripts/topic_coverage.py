"""주제어 사전(data/manual/topics.json) 점검: 주제마다 몇 개 규정·몇 개 조문에 걸쳐 있는지 센다.

사전을 다듬을 때 쓴다. 조문 수가 수백 개면 범위어(링크=false)로 돌리고, 0이면 terms가 규정 원문에 없는 말이다.
기본은 out/chunks.jsonl(조 단위, 부칙·삭제 제외)을 읽고, --db를 주면 지금 색인(PG_DATABASE, 항 단위일 수 있음)을 읽는다.

실행 (저장소 루트에서): uv run python -m scripts.topic_coverage [--db] [--examples N]
"""
import argparse
import json
import sys
from collections import defaultdict

from config import OUT_DIR
from src.search import topics

sys.stdout.reconfigure(encoding="utf-8")


def load_rows(use_db):
    if use_db:
        from src.store.pgstore import PgStore
        store = PgStore()
        titles = {d["doc_id"]: d["title"] for d in store.load_documents()}
        rows = [{**r, "문서명": titles.get(r["doc_id"], r["doc_id"])} for r in store.load_indexed_rows()]
        store.close()
        return rows
    rows = []
    with open(f"{OUT_DIR}/chunks.jsonl", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if r.get("상태") == "정상" and not r["id"].endswith("_부칙"):
                rows.append({"chunk_id": r["id"], "doc_id": r["고유번호"], "문서명": r["문서명"],
                             "article": r.get("조항", ""), "body_search": r.get("본문_검색") or r.get("본문", "")})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", action="store_true", help="out/chunks.jsonl 대신 지금 색인(PG_DATABASE)을 읽는다")
    ap.add_argument("--examples", type=int, default=3, help="주제마다 보여 줄 예시 조문 수")
    args = ap.parse_args()

    table = topics.load()
    rows = load_rows(args.db)
    by_id = {r["chunk_id"]: r for r in rows}
    # 범위어도 세어 보여 주기 위해 링크 여부와 상관없이 전부 센다
    counting = {name: {**spec, "링크": True} for name, spec in table.items()}
    topic_chunks, chunk_topics = topics.build_index(rows, counting)

    print(f"행 {len(rows)}개, 규정 {len({r['doc_id'] for r in rows})}개, 주제 {len(table)}개 "
          f"(링크 {sum(1 for s in table.values() if s['링크'])}개)\n")
    print(f"{'주제':<14}{'묶음':<10}{'링크':<4}{'규정':>4}{'조문':>5}{'제목':>5}  예시")
    stats = []
    for name, spec in table.items():
        ids = topic_chunks.get(name, [])
        regs = defaultdict(int)
        for cid in ids:
            regs[by_id[cid]["doc_id"]] += 1
        titled = [cid for cid in ids if chunk_topics[cid][name][0]]
        stats.append((name, spec, len(regs), len(ids), titled))
    for name, spec, n_regs, n_chunks, titled in sorted(stats, key=lambda s: -s[3]):
        ex = " / ".join(f"{by_id[c]['문서명']} {by_id[c]['article']}" for c in titled[:args.examples])
        flag = "O" if spec["링크"] else "X"
        print(f"{name:<14}{spec['묶음']:<10}{flag:<4}{n_regs:>4}{n_chunks:>5}{len(titled):>5}  {ex}")
    empty = [name for name, _, _, n, _ in stats if n == 0]
    if empty:
        print("\n조문이 0개인 주제 (terms가 원문에 없음):", ", ".join(empty))


if __name__ == "__main__":
    main()
