"""그래프로 더한 조를 답변 입력에 넣는다: 조 노드 키(23970:제25조)를 검색 결과와 같은 모양의 항목(조 전체 한 덩어리)으로 만든다.

assemble은 검색 결과 -> 인용 -> 주제(related) 순으로 글자 수 예산 안에서 채우므로, 여기서 만든 항목은 related로 붙여 예산이 남을 때만 들어간다.
"""
import re

from src.search.search import _order_key

NODE = re.compile(r"(\d+):(제\d+조(?:의\d+)?|별표\d+(?:의\d+)?)")


def graph_entries(searcher, nodes):
    out = []
    for n in sorted(nodes):
        m = NODE.fullmatch(n)
        if not m:
            continue
        doc, base = m.groups()
        rows = sorted(searcher.store.find_article(doc, base), key=lambda r: _order_key(r["article"]))
        if not rows:
            continue
        item = searcher._entry(rows[0])
        item.update({"chunk_id": f"{doc}_{base}", "article": base, "body": "\n".join(r["body"] for r in rows),
                     "match": "그래프", "siblings": [], "cited": []})
        out.append(item)
    return out
