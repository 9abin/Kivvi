"""법령명 일괄 정비 (고도화 7): 점검 결과의 옛 법령명·법령명 오기 → 규정별 개정문.

  mapping(findings)   법령명_변경·법령명_오기 결과 → [{old, new, kind, where}] (옛 이름 → 지금 이름 / 오기 → 정식 이름)
  plan(regs, rows)    명칭 일괄 변경(rename.build)에 그 표를 넣어 규정별 개정문·대비표, 확인 필요, 수작업(별표·별지·부칙)
같은 옛 이름이 다른 규정에도 있으면 함께 바뀐다 (한 번 반입한 법령명 변경을 90개 규정에 한 번에).
근거는 법률 DB(지금 이름·이전 이름)이고, 법적 판단이 아니라 문안 정비 초안이다 (AGENTS §3).
"""

from __future__ import annotations

from .rename import build

KIND = {"법령명_변경": "옛 이름", "법령명_오기": "오기"}


def mapping(findings: list[dict]) -> list[dict]:
    rows: dict[str, dict] = {}
    for f in findings:
        if f["type"] not in KIND or not f.get("suggest") or not f.get("quote") or f["quote"] == f["suggest"]:
            continue
        r = rows.setdefault(f["quote"], {"old": f["quote"], "new": f["suggest"], "kind": KIND[f["type"]], "where": []})
        place = f"{f.get('reg_name') or f['reg_id']} {f.get('article') or ''}".strip()
        if place not in r["where"]:
            r["where"].append(place)
    return sorted(rows.values(), key=lambda r: (r["kind"], r["old"]))


def plan(regs: dict[str, dict], rows: list[dict]) -> dict:
    """rename.build 결과 + 이름 표. 원문에서 찾지 못한 옛 이름(점검 결과의 표기가 원문과 다른 경우)은 따로 표시"""
    if not rows:
        return {"mapping": [], "packages": [], "check": [], "manual": [], "total": 0, "auto": 0, "regs_touched": 0, "not_found": []}
    p = build(regs, {r["old"]: r["new"] for r in rows})
    seen = {h["old"] for h in p["check"] + p["manual"]} | {old for pk in p["packages"] for old in {r["old"] for r in rows} if old in pk["amendment"]}
    p["mapping"] = rows
    p["not_found"] = [r["old"] for r in rows if r["old"] not in seen]
    return p
