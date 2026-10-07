"""개정안 작성 에이전트 (규칙 루프): 개정문 하나 → 연쇄 수정까지 담은 개정 패키지.

  적용(사본) → 변경안 점검 → 같은 규정 안 깨진 인용을 고치는 개정문 문장 추가 → 다시 적용·점검 → 깨짐 0 또는 더 못 고칠 때까지
  다른 규정의 인용 → ① 그 규정의 함께 개정안(적용·대비표까지 검증) ② 개정안 부칙 「다른 내규와의 관계」 초안 — 고르는 것은 사람
  고칠 수 없는 것(삭제된 조를 근거로 든 곳 등) → 판단 필요 질의서

LLM을 쓰지 않는다. 문장은 개정문 형식 그대로 만들고, 우리 적용기가 읽지 못하면 멈춘다 (03 에이전트 구조).
결과는 참고 초안이다. 원문·규정 DB는 바꾸지 않는다.
"""

from __future__ import annotations

from collections import defaultdict

from .amend import AmendError, build_changeset
from .change_check import run as run_check

MAX_ROUNDS = 4


def _has_batchim(ch: str) -> bool:
    if not ch:
        return False
    code = ord(ch[-1]) - 0xAC00
    if 0 <= code <= 11171:
        return code % 28 != 0
    return ch[-1] in "136780"   # 숫자: 일·삼·육·칠·팔·영(공)은 받침


def _eul(word: str) -> str:
    return "을" if _has_batchim(word) else "를"


def _ro(word: str) -> str:
    if not word:
        return "로"
    code = ord(word[-1]) - 0xAC00
    if 0 <= code <= 11171 and code % 28 not in (0, 8):   # 받침이 있고 ㄹ이 아니면 「으로」
        return "으로"
    return "로"


def _unit_label(label: str, para: int) -> str:
    return label + (f"제{para}항" if para else "")


def fix_sentence(unit: str, pairs: list[tuple[str, str]]) -> str:
    """「제3조제3항 중 "제7조제1항"을 "제8조제1항"으로 한다.」 (여러 쌍은 쉼표로, 동시에 적용됨)"""
    parts = [f'"{old}"{_eul(old)} "{new}"{_ro(new)}' for old, new in pairs]
    return f"{unit} 중 " + ", ".join(parts) + " 한다."


def _label_now(origin_label: str, src_article_id: str, label_map: dict) -> str | None:
    """번호가 옮겨진 뒤 그 조의 지금 번호 (삭제됐으면 None)."""
    if src_article_id in label_map:
        return label_map[src_article_id]
    return origin_label


def build_package(cs: dict, regs: dict, rels: list[dict], edges: list[dict], lawdb=None, vector=None) -> dict:
    reg = regs[cs["reg_id"]]
    base_text = cs["amendment"].rstrip()
    added: list[str] = []
    fixed: set[tuple[str, str]] = set()
    rounds = []
    found: list[dict] = []
    cur = cs
    for rnd in range(1, MAX_ROUNDS + 1):
        text = base_text + ("\n" + "\n".join(added) if added else "")
        try:
            cur, _ = build_changeset(reg, text, cs["id"], cs.get("title", ""))
        except AmendError as e:   # 우리가 만든 문장을 적용기가 못 읽으면 그 라운드 문장을 버리고 멈춤
            rounds.append({"round": rnd, "error": str(e)})
            added = added[: -len(rounds[-2]["added"])] if len(rounds) > 1 and rounds[-2].get("added") else added
            break
        cur["fixed"] = sorted(fixed)
        found = run_check(cur, regs, rels, edges, vector=vector, lawdb=lawdb)
        same_breaks = [f for f in found if f["category"] == "breaks" and f["reg_id"] == reg["reg_id"] and f.get("suggest")]
        rec = {"round": rnd, "breaks": sum(f["category"] == "breaks" for f in found), "fixable": len(same_breaks), "added": []}
        rounds.append(rec)
        if not same_breaks:
            break
        # 출발 조문(지금 번호)·항별로 묶어 한 문장에
        by_unit: dict[tuple[str, int], list[tuple[str, str, str]]] = defaultdict(list)
        for f in same_breaks:
            src_article = f["src"].split("-")[0]
            now = _label_now(f["article"], src_article, cur["label_map"])
            if not now:
                continue
            by_unit[(now, f.get("para") or 0)].append((f["quote"], f["suggest"], f["src"]))
        for (label, para), items in by_unit.items():
            pairs = list(dict.fromkeys((q, s) for q, s, _ in items if q != s))
            if not pairs:
                continue
            sent = fix_sentence(_unit_label(label, para), pairs)
            if sent in added:
                continue
            added.append(sent)
            rec["added"].append(sent)
            fixed.update((src, q) for q, _, src in items)
        if not rec["added"]:
            break

    # 다른 규정: 함께 개정안 (규정별) + 부칙 관계 조항 초안
    others: dict[str, list[dict]] = defaultdict(list)
    for f in found:
        if f["category"] == "review" and f["path"] == "인용받음" and f.get("suggest") and f["reg_id"] != reg["reg_id"]:
            others[f["reg_id"]].append(f)
    companion = []
    for rid, items in others.items():
        by_unit = defaultdict(list)
        manual = []
        for f in items:
            if f.get("unit") in ("별표", "별지", "부칙"):   # 개정문 적용기가 다루지 않는 단위 → 사람이 직접
                manual.append({"where": f"{f.get('unit')} {f['article']}", "quote": f["quote"], "suggest": f["suggest"]})
                continue
            by_unit[(f["article"], f.get("para") or 0)].append((f["quote"], f["suggest"]))
        sents = [fix_sentence(_unit_label(lab, para), list(dict.fromkeys(p for p in pairs if p[0] != p[1])))
                 for (lab, para), pairs in by_unit.items() if any(p[0] != p[1] for p in pairs)]
        entry = {"reg_id": rid, "reg_name": regs[rid]["name"], "amendment": "\n".join(sents), "status": "ok", "manual": manual}
        if sents:
            try:
                ocs, _ = build_changeset(regs[rid], entry["amendment"], f"{cs['id']}-{rid}")
                entry["rows"] = ocs["rows"]
            except AmendError as e:
                entry["status"] = f"적용 못함: {e}"
        else:
            entry["status"] = "수작업만"
        companion.append(entry)
    moved = {k: v for k, v in cur["label_map"].items() if v}
    addendum = ""
    if others and moved:
        addendum = (f"제2조(다른 내규와의 관계) 이 규정 시행 당시 다른 내규에서 종전의 「{reg['name']}」 또는 그 조문을 인용한 경우에 "
                    f"이 규정 중 그에 해당하는 조문이 있을 때에는 종전의 조문을 갈음하여 이 규정의 해당 조문을 인용한 것으로 본다.")

    questions = []
    for f in found:
        if f["category"] == "breaks" and not f.get("suggest"):
            questions.append({"where": f"{f['reg_name']} {f['article']}" + (f" {f['para']}항" if f.get("para") else ""),
                              "quote": f.get("quote", ""), "question": f"{f['reason']} — 이 조항을 어떻게 고칠지(다른 근거로 바꾸기·삭제·유지) 결정이 필요합니다."})
        elif f["category"] == "undeterminable":
            questions.append({"where": f"{f['reg_name']} {f['article']}", "quote": "", "question": f["reason"]})
        elif f["category"] == "new_conflicts":
            questions.append({"where": f"{f['reg_name']} {f['article']}" + (f" {f['para']}항" if f.get("para") else ""),
                              "quote": f.get("quote", ""), "question": f"{f['reason']} — 두 규정 중 어느 값을 따를지 결정이 필요합니다."})

    remaining = [f for f in found if f["category"] == "breaks"]
    return {
        "id": cs["id"], "reg_id": reg["reg_id"], "reg_name": reg["name"], "request": cs.get("title", ""),
        "amendment_input": base_text, "amendment_final": base_text + ("\n" + "\n".join(added) if added else ""),
        "added_sentences": added, "rounds": rounds,
        "breaks_before": rounds[0].get("breaks") if rounds else None, "breaks_after": len(remaining),
        "rows": cur["rows"], "label_map": cur["label_map"],
        "companion": companion, "addendum_draft": addendum, "questions": questions,
        "law_check": [f for f in found if f["category"] == "law_check"],
        "review": [f for f in found if f["category"] in ("review", "resolved")],
        "remaining_breaks": remaining,
        "fixed": sorted(fixed),
    }
