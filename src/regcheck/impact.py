"""역방향 영향 범위: 고친 조(또는 별표·법령 조문)를 인용하는 곳을 인용 간선을 거꾸로 따라가 모은다.

  Index(edges, regs).impact(start, hops=2)    start = 조 ID("24024:30"), 별표 ID("10770:별표1"), 법령 조문("근로기준법 제60조")
  Index.resolve("인사규정 제30조")             이름 → 조 ID

규칙 · 코드만 쓴다 (LLM 없음). 결과는 영향 후보의 목록이고 충돌 판정이 아니다.
  · 1단계 = start를 직접 인용하는 조 (직접 영향), 2단계 이상 = 그 조를 다시 인용하는 조 (인용 전이, 참고)
  · 규정 단위 인용("인사규정이 정한 바에 따른다")은 단계와 따로 모은다. 규정 전체를 가리키므로 조 단위로 좁혀지지 않는다
  · 구조 검사에서 걸린 인용(없는 조, 제목 불일치 등)은 따로 표시한다 (대상이 틀렸을 수 있음)
  · 장 단위 인용과 부칙 이력(addendum_history)은 따라가지 않는다
"""

from __future__ import annotations

import re
from collections import defaultdict, deque

SUSPECT = {"missing_article", "missing_item", "missing_chapter", "missing_appendix", "deleted_target",
           "title_mismatch", "name_mismatch", "unknown_regulation"}
SKIP = {"addendum_history"}
LAW_STATUS = {"law_pending", "external_rule"}


def src_node(e: dict) -> str:
    """간선의 출발 노드 ID (항·호 ID를 조 ID로 올린다. 별표·별지는 그대로)."""
    return e["src"].rsplit("-", 3)[0] if e["src_kind"] in ("본문", "부칙") else e["src"]


def target_node(e: dict) -> str:
    """간선의 도착 노드 ID. 법령 조문은 「법령명 제N조」."""
    if e["status"] in LAW_STATUS:
        ref = e.get("target_ref") or {}
        return e["target_doc"] + (f" 제{ref['jo']}조" + (f"의{ref['br']}" if ref.get("br") else "") if ref.get("jo") else "")
    return e.get("target_id") or e.get("target_reg") or e.get("target_doc") or ""


class Index:
    def __init__(self, edges: list[dict], regs: dict[str, dict]):
        self.regs = regs
        self.inb: dict[str, list[dict]] = defaultdict(list)       # 도착 노드 → 간선
        self.inb_reg: dict[str, list[dict]] = defaultdict(list)   # 규정 ID → 규정 단위 인용
        self.label: dict[str, str] = {}
        self.by_name: dict[str, str] = {}
        for reg in regs.values():
            self.by_name[reg["name"]] = reg["reg_id"]
            for a in reg["articles"]:
                self.label[a["id"]] = f"{reg['name']} {a['label']}" + (f"({a['title']})" if a.get("title") else "")
        for e in edges:
            if e["status"] in SKIP or e["target_type"] == "chapter":
                continue
            if e["target_type"] == "regulation":
                self.inb_reg[e["target_id"]].append(e)
            else:
                t = target_node(e)
                if t:
                    self.inb[t].append(e)

    def name_of(self, node: str) -> str:
        if node in self.label:
            return self.label[node]
        reg_id, _, rest = node.partition(":")
        reg = self.regs.get(reg_id)
        return f"{reg['name']} {rest}" if reg and rest else node

    def resolve(self, text: str) -> str | None:
        """"인사규정 제30조", "직원채용지침 별표1", 이미 ID인 값 → 노드 ID. 법령 이름은 그대로 돌려준다 (없으면 None)."""
        text = text.strip()
        if text in self.label or text in self.inb:
            return text
        m = re.match(r"^(.+?)\s+(제\s*\d+\s*조(?:\s*의\s*\d+)?|별표\s*\d+|별지\s*\S+)$", text)
        if not m:
            return None
        name, loc = m.group(1).strip(), re.sub(r"\s+", "", m.group(2))
        reg_id = self.by_name.get(name)
        if reg_id:
            mm = re.match(r"제(\d+)조(?:의(\d+))?$", loc)
            return f"{reg_id}:{mm.group(1)}" + (f"의{mm.group(2)}" if mm.group(2) else "") if mm else f"{reg_id}:{loc}"
        law = f"{name} {loc}"
        return law if law in self.inb else None

    def impact(self, start: str, hops: int = 2) -> dict:
        """start를 인용하는 노드를 단계별로. 결과:
        {"start", "hits": [{node, name, hops, via(바로 앞 노드), edges[{src, kind, raw, context, status}]}], "reg_level": [...], "suspect": [...]}
        같은 노드는 가장 가까운 단계에 한 번만 나온다."""
        seen, hits = {start}, []
        queue = deque([(start, 0)])
        while queue:
            node, d = queue.popleft()
            if d >= hops:
                continue
            groups: dict[str, list[dict]] = defaultdict(list)
            for e in self.inb.get(node, []):
                groups[src_node(e)].append(e)
            for src, es in sorted(groups.items()):
                if src in seen:
                    continue
                seen.add(src)
                hits.append({"node": src, "name": self.name_of(src), "hops": d + 1, "via": node,
                             "edges": [_slim(e) for e in es], "suspect": all(e["status"] in SUSPECT for e in es)})
                queue.append((src, d + 1))
        reg_id = start.split(":")[0] if ":" in start else ""
        reg_level = [{"node": src_node(e), "name": self.name_of(src_node(e)), **_slim(e)} for e in self.inb_reg.get(reg_id, [])] if reg_id in self.regs else []
        return {"start": start, "name": self.name_of(start), "hits": hits, "reg_level": reg_level}


def _slim(e: dict) -> dict:
    return {"src": e["src"], "kind": e["target_type"], "raw": e["raw"], "context": e.get("context", ""), "status": e["status"]}


def summarize(res: dict) -> dict:
    """단계별 · 규정별 개수."""
    by_hop = defaultdict(int)
    regs = set()
    for h in res["hits"]:
        by_hop[h["hops"]] += 1
        regs.add(h["node"].split(":")[0])
    return {"단계별": dict(sorted(by_hop.items())), "규정 수": len(regs), "규정 단위 인용": len(res["reg_level"])}
