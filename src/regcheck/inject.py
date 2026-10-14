"""오류 주입: 정상 조문에 알려진 오류를 일부러 넣고, 탐지(기준선 점검)가 그 위치에서 그 유형을 찾는지 본다 (에이전트 평가 ①).

정답이 넣은 위치·유형으로 기계적으로 정해지므로, 우리가 만든 정답 원장(43/46)과 달리 규칙을 만든 사람의 판단이 들어가지 않는다.
대신 실제 오류보다 쉬울 수 있다 (한 곳에 한 오류, 문장은 그대로). 실제 사례 채점과 함께 보고한다.

유형 (기대 탐지 유형)
  없는조     내규 조 인용 번호를 그 규정에 없는 번호로          → 인용_대상없음
  삭제조     내규 조 인용을 그 규정의 삭제된 조로                → 근거소멸
  규정명오기 인용한 규정 이름을 흔한 방식으로 틀림 (인사규정→인사관리규정) → 규정명_불일치 / 규정명_미확인
  옛법령명   법령 이름을 법률 DB의 이전 이름으로                 → 법령명_변경
  법령명오기 법령 이름에서 가운데 글자 하나를 뺌                 → 법령명_오기
  법령조없음 법령 조 번호를 그 법령에 없는 번호로                → 법령_조문없음
  항번호중복 한 조의 항 번호를 앞 항과 같게 (파싱 뒤 단계에 넣음)  → 항번호_중복
"""

from __future__ import annotations

import copy
import random
import re

from .citations import extract_edges
from .findings import duplicate_articles, duplicate_paragraphs, from_edges
from .lawdb import RENAMED, law_changes, norm
from .review import key_baseline

EXPECT = {
    "없는조": {"인용_대상없음"}, "삭제조": {"근거소멸"}, "규정명오기": {"규정명_불일치", "규정명_미확인"},
    "옛법령명": {"법령명_변경"}, "법령명오기": {"법령명_오기"}, "법령조없음": {"법령_조문없음"}, "항번호중복": {"항번호_중복"},
}
RE_JO = re.compile(r"제\s*(\d+)\s*조")


def _items(reg: dict) -> dict[str, tuple[dict, dict]]:
    return {it["id"]: (a, it) for a in reg["articles"] for it in a["items"]}


def _replace_once(it: dict, a: dict, old: str, new: str) -> bool:
    """항목 원문·정규화문·조 전체 문안에서 old가 정확히 한 번 나올 때만 바꾼다 (어디를 바꿨는지 모호하지 않게)"""
    if it["text"].count(old) != 1 or it["norm"].count(old) != 1:
        return False
    if old.endswith("조") and re.search(re.escape(old) + r"\s*의\s*\d", it["norm"]):   # 「제22조의 2」의 앞부분만 바꾸면 다른 오류가 됨
        return False
    for obj, keys in ((it, ("text", "norm")), (a, ("text", "norm"))):
        for k in keys:
            obj[k] = obj[k].replace(old, new, 1)
    return True


SUFFIX = ("규정", "지침", "규칙", "세칙", "기준", "정관")


def misname(name: str) -> str:
    """규정 이름 오기: 끝말(규정·지침…)은 두고 앞부분을 실제로 흔한 방식으로 틀리게 — 「관리」를 덧붙이거나(인사규정→인사관리규정)
    이미 있으면 뺀다(정보보안관리지침→정보보안지침)"""
    suf = next((x for x in SUFFIX if name.endswith(x)), "")
    stem = name[: len(name) - len(suf)]
    return (stem.replace("관리", "", 1) if "관리" in stem else stem + "관리") + suf


def _jo_numbers(reg: dict) -> set[int]:
    return {int(m.group(1)) for a in reg["articles"] if (m := RE_JO.match(a["label"]))}


def candidates(regs: dict, edges: list[dict], db) -> dict[str, list[dict]]:
    """유형별 주입 후보 (아직 바꾸지 않음). 본문 항목의 인용만 쓴다"""
    items = {rid: _items(r) for rid, r in regs.items()}
    old_names: dict[str, str] = {}   # 지금 이름(정규화) → 옛 이름(정규화)
    raw_old = {norm(k): k for k in RENAMED}   # 옛 이름 표기 (정규화 → 원래 표기)
    if db is not None:
        for old, new in db.previous_names.items():
            old_names.setdefault(norm(new), old)
    out: dict[str, list[dict]] = {k: [] for k in EXPECT}
    for e in edges:
        if e["src_kind"] != "본문" or e["src_reg"] not in items or e["src"] not in items[e["src_reg"]]:
            continue
        a, it = items[e["src_reg"]][e["src"]]
        base = {"reg_id": e["src_reg"], "src_id": e["src"], "article": a["label"], "para": it["path"][0], "context": e["context"]}
        m = RE_JO.match(e["raw"] or "")
        if e["status"] == "ok" and e["target_type"] == "article" and m and e.get("target_reg") in regs:
            tgt = regs[e["target_reg"]]
            jo = m.group(0)
            out["없는조"].append({**base, "old": jo, "new": f"제{max(_jo_numbers(tgt)) + 7}조", "target_reg": tgt["reg_id"]})
            dels = [x["label"] for x in tgt["articles"] if x["deleted"] and RE_JO.fullmatch(x["label"])]
            dels = [d for d in dels if not (tgt["reg_id"] == e["src_reg"] and d == a["label"])]   # 자기 조를 가리키게 되면 뺀다
            if dels:
                out["삭제조"].append({**base, "old": jo, "new": dels[0], "target_reg": tgt["reg_id"]})
            name = e.get("target_doc") or ""
            if len(name) >= 4 and name in it["text"]:
                out["규정명오기"].append({**base, "old": name, "new": misname(name)})
        if e["status"] in ("law_pending", "external_rule") and db is not None:
            name = e.get("target_doc") or ""
            law = db.find(name)
            if not law or name not in it["text"]:
                continue
            old = old_names.get(norm(law["title"]))
            if old and norm(name) == norm(law["title"]):
                old_raw = raw_old.get(old) or next((l["이전법령명"] for l in db.laws.values() if norm(l["이전법령명"] or "") == old), "")
                if old_raw:
                    out["옛법령명"].append({**base, "old": name, "new": old_raw})
            core = name.replace(" ", "")
            if len(core) >= 10 and norm(name) == norm(law["title"]):
                i = len(name) // 2
                while name[i] == " ":
                    i += 1
                out["법령명오기"].append({**base, "old": name, "new": name[:i] + name[i + 1:]})
            if m and e["target_type"] == "article":
                nums = [int(k.split("의")[0]) for k in law["조문"]]
                if nums:
                    out["법령조없음"].append({**base, "old": m.group(0), "new": f"제{max(nums) + 50}조", "law": law["title"]})
    for rid, r in regs.items():
        for a in r["articles"]:
            paras = [it for it in a["items"] if it["path"][0] and it["path"][1:] == [0, 0] and len(it["text"]) >= 12]
            if len(paras) >= 3 and [p["path"][0] for p in paras] == list(range(1, len(paras) + 1)):
                p = paras[2]
                out["항번호중복"].append({"reg_id": rid, "src_id": p["id"], "article": a["label"], "para": 2, "context": p["text"][:80],
                                     "old": "③", "new": "②"})
    return out


def sample(cands: dict[str, list[dict]], n: int, seed: int, regs: dict) -> list[dict]:
    """유형마다 n개, 한 항목에는 하나만. 바꿀 곳이 모호한 후보(주입 실패)는 건너뛴다"""
    rng = random.Random(seed)
    used, out = set(), []
    for typ in EXPECT:
        pool = cands[typ][:]
        rng.shuffle(pool)
        k = 0
        for c in pool:
            if c["src_id"] in used or apply(regs[c["reg_id"]], {"type": typ, **c}) is None:
                continue
            used.add(c["src_id"])
            out.append({"id": f"J{seed}-{typ}-{k:02d}", "type": typ, **c})
            k += 1
            if k == n:
                break
    return out


def apply(reg: dict, inj: dict) -> dict | None:
    """주입한 규정 사본. 바꿀 곳이 모호하면 None"""
    r = copy.deepcopy(reg)
    a, it = _items(r)[inj["src_id"]]
    if inj["type"] == "항번호중복":
        it["path"] = [inj["para"], *it["path"][1:]]
        return r
    return r if _replace_once(it, a, inj["old"], inj["new"]) else None


class Detector:
    """규정 하나에 대한 기준선 점검 (build_findings와 같은 규칙: 인용 엣지·항 번호·조 번호·법률 DB). 의미 검사·개정 이력은 빠짐"""

    def __init__(self, regs: dict, registry, db, aliases: dict):
        self.regs, self.registry, self.db, self.aliases = regs, registry, db, aliases
        self._base: dict[str, set[str]] = {}

    def findings(self, reg: dict) -> list[dict]:
        edges = []
        for e in extract_edges(reg, self.registry):
            d = e.to_dict()
            d["src_reg"], d["src_name"] = reg["reg_id"], reg["name"]
            edges.append(d)
        one = {reg["reg_id"]: reg}
        found = from_edges(edges) + duplicate_paragraphs(one) + duplicate_articles(one)
        if self.db is not None:
            found += law_changes(one, edges, self.db, self.aliases)[0]
        return found

    def base(self, rid: str) -> set[str]:
        if rid not in self._base:
            self._base[rid] = {key_baseline(f) for f in self.findings(self.regs[rid])}
        return self._base[rid]

    def check(self, inj: dict) -> dict:
        reg = apply(self.regs[inj["reg_id"]], inj)
        if reg is None:
            return {**inj, "result": "주입 실패"}
        base = self.base(inj["reg_id"])
        new = [f for f in self.findings(reg) if key_baseline(f) not in base]
        here = [f for f in new if f["article"] == inj["article"] and f.get("unit") == "본문"
                and (not f.get("para") or not inj["para"] or f["para"] == inj["para"])]
        hit = [f for f in here if f["type"] in EXPECT[inj["type"]]]
        result = "탐지" if hit else ("다른 유형으로 탐지" if here else "놓침")
        return {**inj, "result": result, "found_types": sorted({f["type"] for f in here}),
                "side_effects": len(new) - len(here), "note": (hit or here or [{}])[0].get("note", "")}
