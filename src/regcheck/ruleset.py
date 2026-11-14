"""규칙 추론: 규칙 레지스트리(data/manual/rules.json) + 사실 표 → 「의심」 관계(엣지).

  load_rules()                          레지스트리 읽기 (사용 중인 규칙만 돌림)
  infer(regs, rels, rules)              엣지 목록. 관련 조항 표(relations.py Relation)와 같은 칸 + rule_id · via · priority · target_context
  annotate(edges, regs, model)          luna가 엣지마다 한 줄 설명·의도된 차이 가능성·확인할 것을 덧붙임 (AI 의견, 판정 아님)

사실 = 인용 표(rels) · 기한 표 · 이동 표 · 정의 표. 규칙 = 레지스트리에 적힌 것. 추론기는 규칙의 조건이 맞는 사실 쌍을 찾아 잇는다.
엣지는 상태가 늘 「의심」(검증 상태), 신뢰도 「후보」다. 사람이 확인해야 「확인됨」(AGENTS §3). 원문은 고치지 않는다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import consistency, legal_basis, moves

ROOT = Path(__file__).resolve().parents[2]
RULES = ROOT / "data" / "manual" / "rules.json"


def load_rules(path: Path = RULES, only: set[str] | None = None) -> list[dict]:
    rules = json.loads(path.read_text(encoding="utf-8"))["규칙"]
    return [r for r in rules if r.get("사용", True) and (only is None or r["id"] in only)]


def _art(regs: dict, reg_id: str, label: str) -> dict | None:
    reg = regs.get(reg_id)
    return next((a for a in reg["articles"] if a["label"] == label), None) if reg else None


def _src_item(a: dict, para: int, context: str) -> str:
    """조 안에서 근거 문장이 든 항·호의 ID (없으면 항 번호가 같은 첫 항, 그것도 없으면 조 전체)."""
    ctx = context[:40]
    for it in a["items"]:
        if ctx and ctx in it["norm"]:
            return it["id"]
    for it in a["items"]:
        if it["path"][0] == para:
            return it["id"]
    return a["id"] + "-0-0-0"


def _edge(rule: dict, n: int, regs: dict, *, src: str, target: str, target_type: str = "article", evidence: str, context: str,
          target_context: str, note: str, via: str = "") -> dict:
    return {"id": f"{rule['id']}-{n:04d}", "src": src, "src_article": src.rsplit("-", 3)[0] if re.search(r"-\d+-\d+-\d+$", src) else src,
            "src_kind": "본문", "target": target, "target_type": target_type, "kind": rule["관계"], "evidence": evidence, "context": context,
            "source": "rule", "confidence": "후보", "verification": "의심", "check_status": "ok", "note": note, "src_version": "", "target_version": "",
            "run": "", "rule_id": rule["id"], "directed": rule.get("방향") == "단방향", "via": via, "priority": rule["우선순위"], "target_context": target_context}


def _label(regs: dict, aid: str) -> str:
    rid = aid.split(":")[0]
    a = next((x for x in regs[rid]["articles"] if x["id"] == aid), None) if rid in regs else None
    return f"{regs[rid]['name']} {a['label'] if a else aid}" if rid in regs else aid


# ── 규칙 구현 ──────────────────────────────────────────────────────────────────

def dl01(rule: dict, regs: dict, rels: list[dict], ctx: dict) -> list[dict]:
    out = []
    for r, mode, mode_n, same in consistency.deadline_outliers(ctx["deadlines"]):
        a = _art(regs, r["reg_id"], r["article"])
        if not a:
            continue
        src = _src_item(a, r["para"], r["context"])
        seen = set()
        for m in same:    # 다수 쪽에서 규정이 다른 조문 최대 3곳
            b = _art(regs, m["reg_id"], m["article"])
            if not b or b["id"] in seen or m["reg_id"] == r["reg_id"]:
                continue
            seen.add(b["id"])
            out.append(_edge(rule, len(out), regs, src=src, target=b["id"], evidence=f"{r['n']}{r['unit']}", context=r["context"], target_context=m["context"],
                             note=f"{r['kind']} 기한이 이 조는 {r['n']}{r['unit']}, 연결된 조는 {mode[0]}{mode[1]} (같은 값인 곳 {mode_n}곳)"))
            if len(seen) >= 3:
                break
    return out


def def01(rule: dict, regs: dict, rels: list[dict], ctx: dict) -> list[dict]:
    """정의가 둘 이상의 규정에 있는 용어에서, 정의 쌍마다 유사도를 따로 본다 (셋 이상이면 한 쌍의 차이가 다른 쌍을 끌어내리지 않게).
    한쪽 정의가 다른 쪽 규정의 이름을 대며 가리키는 경우(「임직원행동강령 제2조제3호 각 목…」)는 뜻이 다른 게 아니라서 뺀다."""
    out = []
    for d in ctx["definitions"]:
        first = d["defs"][0]
        for other in d["defs"][1:]:
            if other["reg_id"] == first["reg_id"] or first["reg_name"] in other["text"] or other["reg_name"] in first["text"]:
                continue
            sim = consistency.def_similarity(other["text"], first["text"])
            if sim > rule.get("유사도_이하", 0.7):
                continue
            out.append(_edge(rule, len(out), regs, src=other["article_id"] + "-0-0-0", target=first["article_id"], evidence=d["term"], context=other["text"],
                             target_context=first["text"], note=f"「{d['term']}」 정의의 문구 유사도 {sim:.2f} (정의가 {len(d['defs'])}개 규정에 있음)"))
    return out


def move01(rule: dict, regs: dict, rels: list[dict], ctx: dict) -> list[dict]:
    out = []
    for f in ctx["moved"]:
        if not f.get("new_id"):
            continue
        new_art = f["new_id"]
        tgt_ctx = next((a["items"][0]["norm"] for a in regs[f["new_id"].split(":")[0]]["articles"] if a["id"] == new_art and a["items"]), "")
        pr = dict(rule, 우선순위=f["priority"])
        out.append(_edge(pr, len(out), regs, src=f["src"], target=new_art, evidence=f["quote"], context=f["context"], target_context=tgt_ctx,
                         note=f["reason"], via=f["target_id"]))
    return out


def chain_edges(rule: dict, regs: dict, rels: list[dict], ctx: dict) -> list[dict]:
    cites = [r for r in rels if r["kind"] == "인용" and r["target_type"] == "article" and r["src_kind"] == "본문" and r["src_article"] != r["target"]]
    by_src: dict[str, list[dict]] = {}
    direct = set()
    for r in cites:
        by_src.setdefault(r["src_article"], []).append(r)
        direct.add((r["src_article"], r["target"]))
    out, seen = [], set()
    for ab in cites:
        for bc in by_src.get(ab["target"], []):
            a, b, c = ab["src_article"], ab["target"], bc["target"]
            if c in (a, b) or (a, c) in direct or (a, c) in seen:
                continue
            seen.add((a, c))
            out.append(_edge(rule, len(out), regs, src=ab["src"], target=c, evidence=ab["evidence"], context=ab["context"], target_context=bc["context"],
                             note=f"{_label(regs, a)}가 {_label(regs, b)}를 인용하고, 그 조가 {_label(regs, c)}를 인용함", via=b))
    return out


def count01(rule: dict, regs: dict, rels: list[dict], ctx: dict) -> list[dict]:
    out = []
    for d in ctx["counts"]:
        a = _art(regs, d["a"].split(":")[0], d["a_label"])
        if not a:
            continue
        note = f"같은 제목 「{d['title']}」 조의 횟수 한도가 {d['a_reg']}는 {d['a_counts']}, {d['b_reg']}는 {d['b_counts']}. 확인: 적용 대상 · 횟수의 의미 · 시행 시점 · 예외"
        laws = []
        con = ctx.get("lawcon")
        if con is not None:
            laws = legal_basis.law_counts(con, d["title"], d["a_ctx"])
        ca, cb = legal_basis.first_count(d["a_ctx"]), legal_basis.first_count(d["b_ctx"])
        r7 = legal_basis.apply_article7({"name": d["a_reg"], "revised": d.get("a_revised"), "count": ca}, {"name": d["b_reg"], "revised": d.get("b_revised"), "count": cb}, laws)
        hints = ["내규관리규정 제7조 적용(제안, 내규담당부서 확인 전): " + r7["text"]]
        if laws:
            hints += [h for h in (legal_basis.floor_hint(d["a_reg"], ca, laws), legal_basis.floor_hint(d["b_reg"], cb, laws)) if h]
        out.append(_edge(rule, len(out), regs, src=_src_item(a, 0, d["a_ctx"]), target=d["b"], evidence="/".join(f"{n}회" for n in d["a_counts"]), context=d["a_ctx"], target_context=d["b_ctx"],
                         note=note + " | 적용 결과(제안, 확정 아님): " + " | ".join(hints)))
    return out


def auth01(rule: dict, regs: dict, rels: list[dict], ctx: dict) -> list[dict]:
    out = []
    for d in ctx["authority"]:
        a = _art(regs, d["a"].split(":")[0], d["a_label"])
        if not a:
            continue
        r7 = legal_basis.apply_article7({"name": d["a_reg"], "revised": regs[d["a"].split(":")[0]].get("revised"), "count": None}, {"name": d["b_reg"], "revised": regs[d["b"].split(":")[0]].get("revised"), "count": None}, [])
        out.append(_edge(rule, len(out), regs, src=d["a_item"], target=d["b"], evidence=f"{d['a_body']} {d['a_act']}", context=d["a_ctx"], target_context=d["b_ctx"],
                         note=f"같은 사안({', '.join(d['shared'][:4])})에 {d['a_reg']}는 {d['a_body']}의 {d['a_act']}, {d['b_reg']}는 {d['b_body']}의 {d['b_act']}. 확인: 사안이 같은지 · 단계(일부는 보고·협의일 수 있음) · 적용 대상 | 제7조 적용(제안): {r7['text']}"))
    return out


def quorum01(rule: dict, regs: dict, rels: list[dict], ctx: dict) -> list[dict]:
    out = []
    for d in ctx["quorum"]:
        a = _art(regs, d["a"].split(":")[0], d["a_label"])
        if not a:
            continue
        r7 = legal_basis.apply_article7({"name": d["a_reg"], "revised": regs[d["a"].split(":")[0]].get("revised"), "count": None}, {"name": d["b_reg"], "revised": regs[d["b"].split(":")[0]].get("revised"), "count": None}, [])
        out.append(_edge(rule, len(out), regs, src=_src_item(a, 0, d["a_ctx"]), target=d["b"], evidence="/".join(d["a_quorum"]), context=d["a_ctx"], target_context=d["b_ctx"],
                         note=f"{d['body']} 의결 정족수가 {d['a_reg']}는 {d['a_quorum']}, {d['b_reg']}는 {d['b_quorum']}. 확인: 일반 정족수와 특례(제척 · 기피 등)의 구분 · 같은 안건인지 | 제7조 적용(제안): {r7['text']}"))
    return out


IMPL = {"AUTH-01": auth01, "QUORUM-01": quorum01, "COUNT-01": count01, "DL-01": dl01, "DEF-01": def01, "MOVE-01": move01, "CHAIN-01": chain_edges}


def _law_connection():
    """법률 DB가 있으면 읽기 전용 연결 (없으면 법령 기준 힌트만 뺀다)."""
    import sqlite3
    path = ROOT / "data" / "law_rag.sqlite3"
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) if path.exists() else None


def infer(regs: dict[str, dict], rels: list[dict], rules: list[dict] | None = None) -> list[dict]:
    """사용 중인 엣지 산출 규칙을 돌려 「의심」 엣지를 만든다. 카드만 내는 규칙(산출=카드)은 scripts/check_consistency.py가 맡는다."""
    rules = load_rules() if rules is None else rules
    want = {r["id"] for r in rules if r["산출"] == "엣지"}
    ctx: dict = {}
    if "DL-01" in want:
        ctx["deadlines"] = consistency.deadlines(regs)
    if "DEF-01" in want:
        ctx["definitions"] = consistency.definition_diffs(regs)
    if "AUTH-01" in want:
        ctx["authority"] = consistency.authority_diffs(regs)
    if "QUORUM-01" in want:
        ctx["quorum"] = consistency.quorum_diffs(regs)
    if "COUNT-01" in want:
        ctx["counts"] = consistency.count_diffs(regs)
        ctx["lawcon"] = _law_connection()
    if "MOVE-01" in want:
        mv = moves.parse_moves(regs)
        ctx["moved"] = moves.stale_by_moves(regs, mv, rels)
    out: list[dict] = []
    for rule in rules:
        if rule["id"] in want and rule["id"] in IMPL:
            out += IMPL[rule["id"]](rule, regs, rels, ctx)
    return out


# ── luna 설명 (AI 의견) ──────────────────────────────────────────────────────────

SYS = """너는 한국 공공기관 내규 점검 보조자다. 규칙이 찾은 확인 후보 한 건에 대해 JSON 한 개만 출력한다.
필드:
- summary: 무엇이 어긋나 보이는지 한 문장 (아래 두 문장에 있는 내용만)
- intent: 의도된 차이일 가능성. "높음" | "낮음" | "판단 불가" 중 하나
- check: 담당자가 확인할 것 한 문장
법적 판단을 하지 않는다. 문장에 없는 사실을 지어내지 않는다. 확신이 없으면 intent는 "판단 불가"."""


def annotate(edges: list[dict], regs: dict, model: str = "gpt-5.6-luna", cache: Path | None = None, limit: int | None = None) -> dict[str, dict]:
    """엣지마다 AI 의견을 만든다. 결과는 {엣지 ID: {summary, intent, check, model}}. 캐시가 있으면 다시 부르지 않는다.
    보내는 것은 공개 규정(ALIO) 문장과 규칙 설명뿐 (AGENTS §4)."""
    from .nl import api_chat, _post, is_api_model
    cache = cache or ROOT / "out" / "rules" / "ai_notes.json"
    notes: dict[str, dict] = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}
    todo = [e for e in edges if e["id"] not in notes][:limit]
    for e in todo:
        user = (f"규칙: {e['rule_id']} ({e['kind']}) — {e['note']}\n"
                f"[문장 A] {_label(regs, e['src_article'])}: {e['context']}\n"
                f"[문장 B] {_label(regs, e['target'])}: {e.get('target_context', '')}")
        msgs = [{"role": "system", "content": SYS}, {"role": "user", "content": user}]
        try:
            raw = api_chat(model, msgs, 120, 400) if is_api_model(model) else _post(model, msgs)
            m = re.search(r"\{.*\}", raw, re.S)
            js = json.loads(m.group(0)) if m else {}
            if js.get("intent") not in ("높음", "낮음", "판단 불가"):
                js["intent"] = "판단 불가"
            notes[e["id"]] = {"summary": str(js.get("summary", ""))[:200], "intent": js["intent"], "check": str(js.get("check", ""))[:200], "model": model}
        except Exception as ex:   # 한 건 실패로 전체를 멈추지 않음. 빈 의견으로 두고 다음 실행에서 다시 시도되게 캐시하지 않는다
            print("  AI 의견 실패", e["id"], str(ex)[:80])
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(notes, ensure_ascii=False, indent=1), encoding="utf-8")
    return notes
