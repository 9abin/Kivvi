"""영향 점검 평가셋(리포트 68)의 정답 항목 `(규정 이름, 조)`를 그래프 노드 ID로 바꾼다. 평가 스크립트들이 같이 쓴다.

  Truth(nodes, regs).resolve(e)   라벨 한 건 -> 노드 ID (본문 조 · 부칙의 조 · 별표 · 서식). 못 풀면 None
  Truth(nodes, regs).start_of(s)  수정 위치 -> 조 노드 ID
부칙 적용례는 근거 문장이 본문 조에 없고 같은 규정 부칙에만 있으면 그 부칙의 조로 푼다.
"""

from __future__ import annotations

import re
from collections import defaultdict


def norm(s: str | None) -> str:
    return re.sub(r"\s+", "", s or "")


class Truth:
    def __init__(self, nodes: dict[str, dict], regs: dict[str, dict]):
        self.nodes = nodes
        self.by_name = {r["name"]: rid for rid, r in regs.items()}
        self.art_text = {x["id"]: x["norm"] for r in regs.values() for x in r["articles"]}
        self.sup_text = {x["id"]: x["norm"] for r in regs.values() for ad in (r.get("addenda") or []) for x in (ad.get("articles") or [])}
        self.sup_by_reg: dict[str, list[str]] = defaultdict(list)
        for k, v in nodes.items():
            if v["type"] == "Supplement":
                self.sup_by_reg[v["reg"]].append(k)

    @staticmethod
    def art_id(rid: str, label: str) -> str | None:
        c = norm(label)
        m = re.match(r"제(\d+)조(?:의(\d+))?$", c) or re.match(r"제(\d+)-(\d+)조$", c)  # 「제12-1조」는 「제12조의1」과 같은 조
        return f"{rid}:{m.group(1)}" + (f"의{m.group(2)}" if m.group(2) else "") if m else None

    def start_of(self, s: dict) -> str | None:
        rid = self.by_name.get(s["규정"])
        return self.art_id(rid, s["조"]) if rid else None

    def resolve(self, e: dict) -> str | None:
        rid = self.by_name.get(e["규정"])
        if not rid:
            return None
        c = norm(e["조"])
        nid = self.art_id(rid, c)
        if nid:
            q = norm(e.get("근거문장"))
            if nid in self.nodes and q and q not in norm(self.art_text.get(nid, "")):
                for sid in self.sup_by_reg.get(rid, []):
                    if q in norm(self.sup_text.get(sid, "")):
                        return sid
            return nid if nid in self.nodes else None
        if re.match(r"별(표|지)\d*$|별지제", c):  # 번호 없는 별표(규정에 별표가 하나뿐인 경우)도 푼다
            nid = f"{rid}:{c}"
            return nid if nid in self.nodes else None
        return None
