"""변경안 점검: 개정 초안(changeset) 하나가 다른 조항에 주는 영향을 찾는다.

전수 비교 대신 미리 만든 관련 조항 표를 조회하고(02-specs 「후보 수집 → 정밀 점검」), 후보에만 규칙을 적용한다.
결과는 판정이 아니라 담당자 검토용 표시다. 충돌·해소는 양쪽 조문을 보여 줄 뿐 자동 판정하지 않는다 (AGENTS §3).

발견 경로
  인용받음     바뀐 조를 조 번호로 인용하는 곳 (관련 조항 표 역방향)
  문서 인용    바뀐 규정을 문서 단위로 인용하면서 바뀐 조의 제목어가 문맥에 있는 곳
  같은 제목    다른 규정에서 제목이 같은 조 (메타데이터)
  의미 유사    벡터 상위 k (보조, 우선순위 낮음)
  재검사       바뀐 조 자체의 인용을 개정안 기준으로 다시 해석
분류: breaks(깨짐) / new_conflicts(새 충돌 후보) / resolved(해소 후보) / review(확인 필요) / law_check(법령 대조) / undeterminable(판단 불가)
"""

from __future__ import annotations

import copy
import re

from .findings import locate

RE_NUM = re.compile(r"(\d+(?:\.\d+)?)\s*(회|일|년|개월|주|시간|원|만원|억원|%|퍼센트|명|인|배|세|급|호봉)")
GENERIC_TITLES = {"목적", "정의", "적용범위", "시행일", "기타", "준용", "위임", "경과조치", "다른 규정과의 관계"}


_CORPUS: str | None = None   # 오타 확인용 90개 규정 본문 (처음 한 번 읽음)


def _nums(text: str) -> set[str]:
    return {a + b for a, b in RE_NUM.findall(text)}


def _sorted_nums(s: set) -> list[str]:
    return sorted(s, key=lambda x: (re.sub(r"[\d.]", "", x), float(re.match(r"[\d.]+", x).group(0))))


def _changed_items(old_art: dict, new_art: dict | None) -> list[dict]:
    """바뀐 항·호만 전체 문안으로 (표시·값 비교용). [{path, marker, old, new}]"""
    if not new_art:
        return []
    from .amend import marker
    o = {tuple(it["path"]): it["norm"] for it in old_art["items"]}
    n = {tuple(it["path"]): it["norm"] for it in new_art["items"]}
    return [{"path": list(p), "marker": marker(list(p)).strip(), "old": o.get(p, ""), "new": n.get(p, "")}
            for p in sorted(set(o) | set(n)) if o.get(p) != n.get(p)]


def _jacc(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def _art_by_label(reg: dict, label: str) -> dict | None:
    return next((a for a in reg["articles"] if a["label"] == label), None)


def _item_text(art: dict, para: int | None = None) -> str:
    its = [it for it in art["items"] if para is None or it["path"][0] in (para, 0)]
    return " ".join(it["norm"] for it in its)


def _src_text(regs: dict, src: str) -> str:
    rid = src.split(":")[0]
    r = regs.get(rid)
    if not r:
        return ""
    for coll in (r["articles"], *[ad["articles"] for ad in r["addenda"]]):
        for a in coll:
            for it in a["items"]:
                if it["id"] == src:
                    return it["norm"]
    return ""


def _item(cat: str, loc: dict, regs: dict, **kw) -> dict:
    return {"category": cat, **loc, "reg_name": regs[loc["reg_id"]]["name"] if loc["reg_id"] in regs else loc["reg_id"], **kw}


def _renumber_ref(evidence: str, old_label: str, new_label: str | None) -> str:
    if not new_label:
        return ""
    m = re.match(r"제\s*(\d+)\s*조(?:\s*의\s*(\d+))?", old_label)
    pat = rf"제\s*{m.group(1)}\s*조" + (rf"\s*의\s*{m.group(2)}" if m.group(2) else r"(?!\s*의\s*\d)")
    return re.sub(pat, new_label, evidence, count=1)


def _fixed_in_draft(new_reg: dict | None, src: str, evidence: str, suggest: str) -> bool:
    """개정안 사본에서 그 인용이 이미 새 번호로 고쳐졌나 (연쇄 수정 루프가 넣은 문장 반영 여부)."""
    if not new_reg or not suggest:
        return False
    for a in new_reg["articles"]:
        for it in a["items"]:
            if it["id"] == src:
                return suggest in it["norm"] and not re.search(_strict(evidence), it["norm"])
    return False


def _only_reference_fix(c: dict) -> bool:
    """그 조의 변경이 번호 이동과 「"제N조…"를 "제M조…"로」 같은 인용 번호 치환뿐인가."""
    ops = [op for op in c["ops"] if op["op"] != "renumber"]
    ref = re.compile(r"^제\s*\d+\s*(?:조|장)")
    return bool(ops) and all(op["op"] == "replace" and ref.match(op.get("old", "")) and ref.match(op.get("new", "")) for op in ops)


def _strict(phrase: str) -> str:
    return r"(?<!\d)" + re.escape(phrase) + r"(?!\d|\s*의\s*\d)"


def check(cs: dict, regs: dict[str, dict], rels: list[dict], baseline_edges: list[dict] | None = None,
          new_reg: dict | None = None, registry_factory=None, vector=None, k: int = 5, lawdb=None, spell_model: str | None = None) -> list[dict]:
    """cs: out/changes/{id}/changeset.json, regs: 원문 파싱 결과(규정ID → 레코드), rels: 관련 조항 표.
    new_reg/registry_factory를 주면 바뀐 조의 인용을 다시 해석한다. vector=(model, Index)면 의미 유사 후보를 더한다."""
    rid = cs["reg_id"]
    reg = regs[rid]
    out: list[dict] = []
    seen: set[tuple] = set()

    def add(it: dict) -> None:
        key = (it["category"], it["reg_id"], it.get("unit"), it.get("article"), it.get("para"), it.get("quote"))
        if key not in seen:
            seen.add(key)
            out.append(it)

    changed = {c["origin"]: c for c in cs["changes"] if c["origin"]}
    fixed = {tuple(x) for x in cs.get("fixed", [])}   # 연쇄 수정 루프가 고친 (출발 단위, 인용 문구)
    lmap = cs["label_map"]                         # origin → 새 번호 (None = 삭제)
    content_changed = {o: c for o, c in changed.items() if any(op["op"] != "renumber" for op in c["ops"])}
    inserted_labels = {c["new_label"] for c in cs["changes"] if not c["origin"]}
    moved_here = {c["new_label"]: c for c in cs["changes"] if c["origin"] and c["old_label"] != c["new_label"]}

    # 1) 인용받음: 삭제·번호 변경·내용 변경된 조를 가리키는 인용
    for r in rels:
        tgt = r.get("target") or ""
        if r["target_type"] != "article" or tgt not in changed:
            continue
        c = changed[tgt]
        loc = locate(r["src"])
        same = r["src"].split(":")[0] == rid
        base = dict(quote=r["evidence"], context=r["context"], path="인용받음", src=r["src"], target=f"{reg['name']} {c['old_label']}")
        if r["src_kind"] == "부칙":
            add(_item("info", loc, regs, reason="부칙의 옛 조문 언급 (경과조치·이력, 오류 제외 원칙)", handling="참고", priority="낮음", **base))
            continue
        if tgt in lmap and lmap[tgt] is None:
            add(_item("breaks", loc, regs, reason=f"삭제된 {c['old_label']}를 근거로 듦 → 근거 소멸", handling="판단 불가",
                      priority="높음", **base))
        elif tgt in lmap:
            new = lmap[tgt]
            now = (f" 지금 {c['old_label']}는 신설 조문을 가리킴" if c["old_label"] in inserted_labels
                   else f" 지금 {c['old_label']}는 종전 다른 조문({moved_here[c['old_label']]['old_label']})을 가리킴"
                   if c["old_label"] in moved_here else "")
            if same:
                sug = _renumber_ref(r["evidence"], c["old_label"], new)
                if (r["src"], r["evidence"]) in fixed or _fixed_in_draft(new_reg, r["src"], r["evidence"], sug):
                    add(_item("info", loc, regs, reason=f"개정안에서 함께 고침 ({r['evidence']} → {sug})", handling="참고",
                              priority="낮음", suggest=sug, **{**base, "path": "연쇄 수정 반영"}))
                    continue
                add(_item("breaks", loc, regs, reason=f"같은 규정 안 인용. {c['old_label']} → {new}로 옮겨짐.{now} 개정문에 함께 고쳐야 함",
                          handling="고칠 수 있음", priority="높음", suggest=sug, **base))
            else:
                add(_item("review", loc, regs, reason=f"다른 규정의 인용. {c['old_label']} → {new}. 개정안 부칙에 「다른 내규와의 관계」"
                          "(종전 조문 인용을 새 조문 인용으로 본다)가 있으면 표기 정비, 없으면 깨짐", handling="판단 가능", priority="중간",
                          suggest=_renumber_ref(r["evidence"], c["old_label"], new), **base))
        else:
            add(_item("review", loc, regs, reason=f"바뀐 {c['old_label']}을 인용. 바뀐 내용과 관계있는지 확인", handling="판단 가능",
                      priority="중간", **base))

    # 2) 문서 인용: 규정 전체를 인용하는 문맥에 바뀐 조의 제목어가 있는 곳
    titles = {o: reg_art["title"] for o in content_changed
              if (reg_art := next((a for a in reg["articles"] if a["id"] == o), None)) and reg_art["title"]}
    for r in rels:
        if r["target_type"] != "regulation" or r.get("target") != rid or r["src_kind"] == "부칙":
            continue
        for o, t in titles.items():
            if t not in GENERIC_TITLES and t in r["context"]:
                add(_item("review", locate(r["src"]), regs, quote=r["evidence"], context=r["context"], path="문서 인용", src=r["src"],
                          target=f"{reg['name']} {changed[o]['old_label']}",
                          reason=f"「{reg['name']}」에 문서 단위로 맡긴 조항이고 문맥에 「{t}」가 있음", handling="판단 가능", priority="중간"))

    # 3) 같은 제목 + 값 비교, 4) 의미 유사
    new_arts = {a.get("_origin") or a["id"]: a for a in (new_reg or {}).get("articles", [])}
    for o, c in content_changed.items():
        if _only_reference_fix(c):
            continue   # 인용 번호만 고친 조(연쇄 수정)는 내용이 바뀐 게 아니므로 같은 주제·법령 대조 대상이 아님
        old_art = next(a for a in reg["articles"] if a["id"] == o)
        new_art = new_arts.get(o)
        deleted = o in lmap and lmap[o] is None
        old_txt = _item_text(old_art)
        new_txt = "" if deleted or not new_art else _item_text(new_art)
        cands: dict[str, tuple[dict, str, str]] = {}   # 조문ID → (조문, 경로, 규정ID)
        if old_art["title"] and old_art["title"] not in GENERIC_TITLES:
            for orid, r2 in regs.items():
                if orid == rid:
                    continue
                for a in r2["articles"]:
                    if not a["deleted"] and a["title"] and (a["title"] == old_art["title"] or
                                                           (len(old_art["title"]) >= 3 and old_art["title"] in a["title"])):
                        cands[a["id"]] = (a, "같은 제목", orid)
        if vector:
            model, index = vector
            from .vectors import embed
            # 조 전체(긴 목록)를 넣으면 주제가 흐려짐 → 제목+첫 문장, 제목만 두 질의를 합친다 (S02 「당연해직」 1~2위)
            lead = old_art["items"][0]["norm"] if old_art["items"] else ""
            qs = embed(model, [f"{reg['name']} > {old_art['label']}({old_art['title']})\n{lead}", old_art["title"] or lead])
            merged: dict[str, tuple[float, dict]] = {}
            for q in qs:
                for sc_, ch in index.search(q, k=k + 15):
                    if ch["article_id"] not in merged or merged[ch["article_id"]][0] < sc_:
                        merged[ch["article_id"]] = (sc_, ch)
            for score, ch in sorted(merged.values(), key=lambda x: -x[0]):
                if ch["article_id"] == o or ch["reg_id"] == rid or ch["article_id"] in cands:
                    continue
                a = next((x for x in regs[ch["reg_id"]]["articles"] if x["id"] == ch["article_id"]), None)
                if a:
                    cands[a["id"]] = (a, f"의미 유사 ({score:.2f})", ch["reg_id"])
                if sum(1 for v in cands.values() if v[1].startswith("의미")) >= k:
                    break
        for aid, (a, path, orid) in cands.items():
            loc = {"reg_id": orid, "unit": "본문", "article": a["label"], "para": 0}
            low = path.startswith("의미")
            # 값 비교: 상대 조의 항 중 숫자가 가장 많이 겹치는 항과 비교
            if not deleted and new_txt and not low:   # 값 비교는 같은 제목 후보에만 (의미 유사는 주제가 달라 오경보)
                # 바뀐 항만 비교 (조 전체 숫자를 섞으면 「총 3회」→「최대 3회」처럼 자리만 바뀐 값이 사라져 보임)
                diff = _changed_items(old_art, new_art)
                O = set().union(*(_nums(d["old"]) for d in diff)) if diff else _nums(old_txt)
                N = set().union(*(_nums(d["new"]) for d in diff)) if diff else _nums(new_txt)
                if O != N:
                    best = max(({"p": p, "C": _nums(_item_text(a, p)), "t": _item_text(a, p)}
                                for p in sorted({it["path"][0] for it in a["items"]})),
                               key=lambda x: max(_jacc(O, x["C"]), _jacc(N, x["C"])), default=None)
                    if best and best["C"] and (O - N or N - O):
                        before, after = _jacc(O, best["C"]), _jacc(N, best["C"])
                        if after != before:
                            cat = "resolved" if after > before else "new_conflicts"
                            pairs = [f"「{op['old']}」→「{op['new']}」" for op in c["ops"] if op["op"] == "replace" and not op.get("title")]
                            add(_item(cat, {**loc, "para": best["p"]}, regs, quote=", ".join(sorted(best["C"] & (O | N))) or a["title"],
                                      context=best["t"], path=f"{path} + 값 비교",
                                      target=f"{reg['name']} {c['old_label']}",
                                      reason=("값이 같아짐 (충돌 해소 후보)" if cat == "resolved" else "같던 값이 달라짐 (새 충돌 후보)")
                                      + (f": 개정 {', '.join(pairs)}" if pairs else "")
                                      + f" / 바뀐 항의 값 현행 {_sorted_nums(O)} → 개정안 {_sorted_nums(N)}, 상대 {_sorted_nums(best['C'])}",
                                      handling="판단 가능", priority="낮음" if low else "중간",
                                      counterpart={"items": diff}))
                            continue
            reason = (f"삭제된 조와 같은 주제 — 이 조가 삭제된 조에 기대는지 확인" if deleted
                      else f"같은 주제 조문 — 바뀐 내용과 맞는지 확인")
            add(_item("review", loc, regs, quote=a["title"] or a["label"], context=_item_text(a), path=path,
                      target=f"{reg['name']} {c['old_label']}", reason=reason, handling="판단 가능" if not deleted else "판단 불가",
                      priority="낮음" if low else "중간"))

        # 법령 대조 ②: 같은 제목의 법령 조문 (법률 DB) — 인용이 없어도 같은 주제의 법정 기준과 대조 (S01 육아휴직 → 남녀고용평등법 제19조)
        if lawdb is not None and not deleted and old_art["title"] not in GENERIC_TITLES:
            for law, lkey, u in lawdb.same_title(old_art["title"], limit=3):
                no, _, br = lkey.partition("의")
                add({"category": "law_check", "reg_id": rid, "reg_name": reg["name"], "unit": "본문", "article": c["old_label"],
                     "para": 0, "quote": f"{law['title']} 제{no}조" + (f"의{br}" if br else ""), "context": u.get("조문내용", "")[:400],
                     "path": "같은 제목 법령", "target": law["title"], "priority": "중간", "handling": "판단 가능", "law": law["title"],
                     "reason": f"바뀐 조와 제목이 같은 법령 조문 「{law['title']}」 제{no}조{'의' + br if br else ''}({u.get('조문제목')}) — 개정안이 법정 기준과 맞는지 대조"})
        # 법령 대조 ①: 바뀐 조가 인용하는 법령 (삭제된 조는 대조할 문안이 없음)
        for r in rels:
            if not deleted and r["src_article"] == o and r["target_type"] == "law":
                add(_item("law_check", locate(r["src"]), regs, quote=r["evidence"], context=r["context"], path="나가는 인용", src=r["src"],
                          target=r["target"], reason="바뀐 조가 인용하는 법령 — 개정안이 법령과 맞는지 대조 (법률 DB 연결 후)",
                          handling="판단 가능", priority="중간"))
        if deleted:
            add({"category": "undeterminable", "reg_id": rid, "reg_name": reg["name"], "unit": "본문", "article": c["old_label"],
                 "para": 0, "quote": "", "path": "삭제", "target": "", "priority": "높음", "handling": "판단 불가",
                 "reason": "삭제 사유와 이관 여부(다른 조로 옮기는지, 제도를 없애는지)는 회사 결정. 위 근거 소멸 조항의 처리가 여기에 달림"})

    # 5) 재검사: 바뀐 조 자체의 인용을 개정안 기준으로 다시 해석
    if new_reg is not None and registry_factory is not None and baseline_edges is not None:
        from .citations import extract_edges
        nr = _reidentify(new_reg)
        registry = registry_factory(nr)
        fresh = [e.to_dict() for e in extract_edges(nr, registry)]
        targets = {(changed[o]["new_label"]) for o in content_changed if lmap.get(o, "") is not None}
        targets |= inserted_labels
        live = {o for o in content_changed if lmap.get(o, "") is not None}   # 삭제된 조의 이슈는 함께 사라질 뿐 해소가 아님
        old_bad = {(e["raw"], e["status"]) for e in baseline_edges if e["src_reg"] == rid
                   and e["src"].split("-")[0] in live and e["status"] not in ("ok", "law_pending", "external_rule")}
        for e in fresh:
            lab = _label_of(nr, e["src"])
            if lab not in targets or e["status"] in ("ok", "law_pending", "external_rule"):
                continue
            if (e["raw"], e["status"]) in old_bad:
                continue
            add({"category": "breaks", "reg_id": rid, "reg_name": reg["name"], "unit": e["src_kind"], "article": lab,
                 "para": int(e["src"].split("-")[1]) if "-" in e["src"] else 0, "quote": e["raw"], "context": e["context"],
                 "path": "재검사", "target": e["target_doc"] or reg["name"], "priority": "높음", "handling": "고칠 수 있음",
                 "reason": f"개정안의 인용이 해석되지 않음 ({e['status']}) {e.get('note', '')}".strip()})
        # 바뀐 조의 법령명 (옛 이름·오기). 원문에 원래 있던 것은 빼고 개정안이 새로 들인 것만 (261014 변경안 골든셋 0/16)
        if lawdb is not None:
            from .lawdb import law_changes
            mine = [e for e in fresh if _label_of(nr, e["src"]) in targets]
            had = {(e["target_doc"]) for e in baseline_edges if e["src_reg"] == rid and e["src"].split("-")[0] in live}
            for f in law_changes({rid: nr}, mine, lawdb, registry.law_aliases)[0]:
                if f["type"] in ("법령명_변경", "법령명_오기") and f["quote"] not in had:
                    add({"category": "breaks", "reg_id": rid, "reg_name": reg["name"], "unit": f["unit"], "article": f["article"],
                         "para": f["para"], "quote": f["quote"], "context": f.get("context", ""), "path": "재검사(법령명)",
                         "target": f.get("suggest", ""), "priority": "높음", "handling": "고칠 수 있음", "suggest": f.get("suggest", ""),
                         "reason": ("옛 법령명 — " if f["type"] == "법령명_변경" else "법령명 오기 의심 — ") + f["note"]})
        new_bad = {(e["raw"], e["status"]) for e in fresh if _label_of(nr, e["src"]) in targets}
        for raw, st in old_bad - new_bad:
            add({"category": "resolved", "reg_id": rid, "reg_name": reg["name"], "unit": "본문", "article": "", "para": 0,
                 "quote": raw, "path": "재검사", "target": "", "priority": "중간", "handling": "판단 가능",
                 "reason": f"기존 인용 문제({st})가 개정안에서 없어짐"})
    # 6) 오타·맞춤법: 바뀐 문장만 로컬 LLM에 넣고 확인 규칙을 통과한 지적 중 개정안이 새로 들인 것만 (regcheck.spell)
    if spell_model and new_reg is not None:
        from .spell import check as spell_check, corpus_text
        global _CORPUS
        if _CORPUS is None:
            _CORPUS = corpus_text()
        new_by_origin = {a.get("_origin") or a["id"]: a for a in new_reg["articles"]}
        pairs = [(next(a for a in reg["articles"] if a["id"] == o), new_by_origin.get(o)) for o in content_changed if lmap.get(o, "") is not None]
        pairs += [(None, a) for a in new_reg["articles"] if a["label"] in inserted_labels and not a.get("_origin")]
        for old_art, new_art in pairs:
            if not new_art:
                continue
            items = _changed_items(old_art, new_art) if old_art else [{"path": it["path"], "old": "", "new": it["norm"]} for it in new_art["items"]]
            for d in items:
                if not d["new"]:
                    continue
                for e in spell_check(d["new"], spell_model, _CORPUS):
                    if e["wrong"] in d["old"]:
                        continue   # 원래 있던 표기 (개정안 탓이 아님)
                    add({"category": "review", "reg_id": rid, "reg_name": reg["name"], "unit": "본문", "article": new_art["label"],
                         "para": d["path"][0], "quote": e["wrong"], "context": d["new"], "path": "오타 의심", "target": "",
                         "priority": "중간", "handling": "고칠 수 있음", "suggest": e["right"],
                         "reason": f"오타·맞춤법 의심: 「{e['wrong']}」 → 「{e['right']}」? ({e.get('reason', '')}) — 로컬 모델 지적, 확인 필요"})
    order = {"breaks": 0, "new_conflicts": 1, "resolved": 2, "review": 3, "law_check": 4, "undeterminable": 5, "info": 6}
    prio = {"높음": 0, "중간": 1, "낮음": 2}
    out.sort(key=lambda x: (order[x["category"]], prio.get(x.get("priority"), 3)))
    return out


def _reidentify(new_reg: dict) -> dict:
    """번호가 바뀐 사본의 조문ID를 새 번호로 다시 붙인다 (재검사의 대상 해석용)."""
    nr = copy.deepcopy(new_reg)
    for a in nr["articles"]:
        m = re.match(r"제(\d+)조(?:의(\d+))?", a["label"])
        aid = f"{nr['reg_id']}:{m.group(1)}" + (f"의{m.group(2)}" if m.group(2) else "")
        a["id"] = aid
        for it in a["items"]:
            p, i, s = it["path"]
            it["id"] = f"{aid}-{p}-{i}-{s}"
        a["norm"] = "\n".join(it["norm"] for it in a["items"])
    nr["articles"] = [a for a in nr["articles"] if not a.get("deleted")] + [a for a in nr["articles"] if a.get("deleted")]
    return nr


def _label_of(reg: dict, src: str) -> str:
    aid = src.split("-")[0]
    return next((a["label"] for a in reg["articles"] if a["id"] == aid), "")


def score(found: list[dict], expected: dict) -> dict:
    """시나리오 기대 결과와 대조. 같은 곳 = 규정(ID 또는 이름) + 조 (+ 항이 둘 다 있으면 항)."""
    def same(e, f):
        if e.get("reg_id") and e["reg_id"] != f["reg_id"]:
            return False
        if not e.get("reg_id") and e.get("reg_name") != f["reg_name"]:
            return False
        if e.get("article") and e["article"] != f.get("article"):
            return False
        if e.get("unit") and f.get("unit") and e["unit"] != f["unit"] and not (e.get("unit") == "별표" and f.get("unit") == "별표"):
            return False
        return not (e.get("para") and f.get("para") and e["para"] != f["para"])

    res = {}
    for cat in ("breaks", "new_conflicts", "resolved", "review", "law_check", "undeterminable"):
        exp = expected.get(cat, [])
        rows = []
        for e in exp:
            if not e.get("article"):   # 위치가 없는 기대 항목은 분류만 맞으면 탐지
                hit = [f for f in found if f["category"] == cat]
                rows.append({"expected": e, "result": "탐지" if hit else "놓침", "as": cat if hit else ""})
                continue
            hit_same = [f for f in found if f["category"] == cat and same(e, f)]
            hit_other = [f for f in found if f["category"] != cat and same(e, f)]
            rows.append({"expected": e, "result": "탐지" if hit_same else ("다른 분류" if hit_other else "놓침"),
                         "as": hit_same[0]["category"] if hit_same else (hit_other[0]["category"] if hit_other else "")})
        res[cat] = rows
    na = expected.get("not_affected", [])
    res["not_affected"] = [{"expected": e, "result": "오탐" if any(same(e, f) for f in found if f["category"] != "info") else "통과"}
                           for e in na]
    return res


def run(cs: dict, regs: dict[str, dict], rels: list[dict], edges: list[dict], vector=None, k: int = 5, lawdb=None,
        spell_model: str | None = None) -> list[dict]:
    """changeset 하나 점검 (개정안 사본을 다시 만들어 재검사까지). scripts/check_change.py와 검토 화면이 같이 쓴다."""
    from .amend import apply, parse_amendment
    from .citations import Registry

    new_reg, _ = apply(regs[cs["reg_id"]], parse_amendment(cs["amendment"]))

    def factory(nr):
        return Registry([nr if r["reg_id"] == nr["reg_id"] else r for r in regs.values()])

    return check(cs, regs, rels, baseline_edges=edges, new_reg=new_reg, registry_factory=factory, vector=vector, k=k, lawdb=lawdb,
                 spell_model=spell_model)
