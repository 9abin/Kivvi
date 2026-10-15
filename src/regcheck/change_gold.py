"""변경안 골든셋: 「조항을 추가·삭제·수정했을 때 생기는 문제를 잡는가」 (에이전트 평가의 최종 목표).

문항 하나 = 개정문 하나 + 잡아야 할 문제(expected) + 잡으면 안 되는 곳(not_affected).
  DEL    인용받는 조 삭제 — 정답 = 원문 문자열 검색으로 찾은 인용 위치 (관계 표와 따로 만든 정답. 관계 표로 만들면 순환)
  DELN   아무도 인용하지 않는 조 삭제 — 깨짐이 없어야 함
  P-*    기존 항 끝에 문장을 덧붙이는 개정에 문제를 하나 심음 — 정답 = 심은 위치·유형
           없는조 / 삭제조 / 규정명오기 → 「깨짐」(재검사)   옛법령명 / 법령명오기 → 「법령명」   오타 → 「오타」
           정상인용(존재하는 조) → 아무것도 걸리면 안 됨
  NUMC   다른 규정 같은 제목 조와 같던 숫자를 바꿈 → 상대 조에 「새 충돌」
  NUML   인용한 법령 조문과 같던 숫자를 바꿈 → 「법령 수치」 (법령 대조 카드: app/web_law)
  CLEAN  인용·숫자 없는 표현만 다듬음 (「하여야 한다」→「해야 한다」) → 높은 우선순위(깨짐·새 충돌)가 없어야 함
  REAL   실제 사례 (시나리오 S01~S03)
  INS    조 신설 + 뒤 조 번호 밀림 — 정답 = 밀리는 조를 가리키는 인용 (원문 문자열 검색). 같은 규정은 깨짐, 다른 규정은 확인 필요(review)
  INSN   아무도 인용하지 않는 조 앞에 신설 — 깨짐이 없어야 함
  MIX    한 개정안에 문제 셋(없는 조 인용 + 옛 법령명 + 오타)을 함께 심음 — 서로 가려지지 않는지
  P-오타(확장)  오타 종류 5가지(자리바꿈·모음·받침빠짐·맞춤법·반복)로 50개까지
시드로 만들고, 번호가 짝수면 개발용·홀수면 시험용 (규칙을 고칠 때 시험용 결과로 점수를 내지 않는다).
"""

from __future__ import annotations

import random
import re

from .lawdb import RENAMED

RE_HANG = re.compile(r"[가-힣]")
HISTORY = re.compile(r"[\[<][^\[\]<>]*(?:이동|개정|신설|삭제|전문개정)[^\[\]<>]*[\]>]")
MAJOR = ("breaks", "new_conflicts", "law_name", "typo")   # 문제없는 문항에서 나오면 안 되는 경보


def _jo(label: str) -> int | None:
    m = re.fullmatch(r"제(\d+)조", label)
    return int(m.group(1)) if m else None


def _items(reg: dict):
    for a in reg["articles"]:
        if a["deleted"]:
            continue
        for it in a["items"]:
            if it["path"][1:] == [0, 0]:
                yield a, it


def _unit(a: dict, it: dict) -> str:
    p = it["path"][0]
    return a["label"] + (f"제{p}항" if p else "")


def _tail(it: dict) -> str | None:
    """항 끝의 고유한 꼬리 (개정문 「중 "A"를」의 A). 마침표로 끝나는 마지막 8~14자가 항 안에서 한 번만 나와야 함"""
    t = it["norm"].rstrip()
    if not t.endswith("."):
        return None
    for n in (8, 10, 12, 14):
        s = t[-n:]
        if '"' not in s and t.count(s) == 1:
            return s
    return None


def _units(r: dict):
    """(조 또는 별표·별지 이름, 항, 문장) — 본문 항과 별표·별지 (별지 서식도 「제N조」를 인용한다: G122)"""
    from .normalize import normalize
    for a, it in _items(r):
        yield a["label"], it["path"][0], it["norm"]
    for ap in r.get("appendices", []):
        text = normalize(ap["text"])
        # 계약서형 별지의 「제N조」는 그 계약서의 자기 조문이라 규정 본문을 가리키지 않는다. 인용 해석기(citations)가 같은 기준으로 보류하므로 정답에서도 뺀다
        if re.search(r"^\s*제\s*1\s*조\s*\(", text, re.M) and "계약" in text[:400]:
            continue
        yield ap["id"].split(":", 1)[1], 0, text


def text_citers(regs: dict, reg: dict, label: str) -> list[dict]:
    """원문 문자열 검색으로 「이 규정 제N조」를 인용하는 곳 (파서·관계 표를 거치지 않음).
    다른 규정: 「규정명 … 제N조」(30자 안). 같은 규정: 앞 20자 안에 다른 문서 이름(「」·규정·지침·법)이 없는 「제N조」. 본문과 별표·별지를 본다"""
    n = _jo(label)
    out = []
    pat_n = r"제\s*" + str(n) + r"\s*조(?!\s*의\s*\d)"
    for r in regs.values():
        for where, para, raw in _units(r):
            t = HISTORY.sub(" ", raw)   # 「[제25조에서 이동]」 같은 개정 이력 표시는 인용이 아님 (처음 만든 정답에서 4건 잘못 셈)
            if r["reg_id"] == reg["reg_id"]:
                if where == label:
                    continue
                for m in re.finditer(pat_n, t):
                    pre = re.sub(r"(?:본|이)\s*(?:지침|규정|규칙|강령|내규)", "", t[max(0, m.start() - 20): m.start()])   # 「본 지침 제36조」는 같은 규정 인용 (G122)
                    if not re.search(r"[」]|규정|지침|규칙|법률|법\s|법$|령\s|정관|강령|(?<![가-힣])영\s*$|(?<![가-힣])영\s", pre):   # 「영 제3조」 = 시행령 (261014 골든셋 G108)
                        out.append({"reg_id": r["reg_id"], "article": where, "para": para})
                        break
            elif re.search(re.escape(reg["name"]) + r"[^.\n]{0,30}?" + pat_n, t):
                out.append({"reg_id": r["reg_id"], "article": where, "para": para})
    return out


def _typo(word: str) -> str:
    return word[0] + word[2] + word[1] + word[3:]   # 둘째·셋째 글자 자리 바꿈 (휴직기간 → 휴기직간)


def make(regs: dict, edges: list[dict], db, seed: int = 1, per: int = 8) -> list[dict]:
    rng = random.Random(seed)
    out: list[dict] = []

    def add(kind: str, reg: dict, amendment: str, expected: dict, note: str, not_affected=None):
        out.append({"id": f"G{len(out) + 1:03d}", "kind": kind, "reg_id": reg["reg_id"], "reg_name": reg["name"],
                    "amendment": amendment, "expected": expected, "not_affected": not_affected or [], "note": note})

    regl = list(regs.values())
    # ── 삭제 ────────────────────────────────────────
    dels, deln = [], []
    for r in regl:
        for a in r["articles"]:
            if a["deleted"] or not _jo(a["label"]):
                continue
            cit = text_citers(regs, r, a["label"])
            (dels if cit else deln).append((r, a, cit))
    rng.shuffle(dels)
    rng.shuffle(deln)
    for r, a, cit in dels[:15]:
        add("DEL", r, f"{a['label']}를 삭제한다.", {"breaks": cit}, f"{a['label']}({a['title']}) 삭제 — 원문 검색 인용 {len(cit)}곳")
    for r, a, _ in deln[:5]:
        add("DELN", r, f"{a['label']}를 삭제한다.", {}, f"{a['label']}({a['title']}) 삭제 — 원문에 인용 없음")

    # ── 문장 덧붙이기 + 문제 심기 ─────────────────────────
    slots = [(r, a, it, _tail(it)) for r in regl for a, it in _items(r)]
    slots = [s for s in slots if s[3] and len(s[2]["norm"]) < 400]
    rng.shuffle(slots)
    used: set[str] = set()

    def take(pred=lambda s: True):
        for s in slots:
            if s[2]["id"] not in used and pred(s):
                used.add(s[2]["id"])
                return s
        return None

    renamed = list(RENAMED.items())
    cited_laws = sorted({e["target_doc"] for e in edges if e["status"] == "law_pending" and len((e["target_doc"] or "").replace(" ", "")) >= 9
                         and db.find(e["target_doc"]) and e["target_doc"] == db.find(e["target_doc"])["title"]})
    for kind in ("없는조", "삭제조", "규정명오기", "옛법령명", "법령명오기", "오타", "정상인용"):
        for _ in range(per):
            pred = (lambda s: any(x["deleted"] and _jo(x["label"]) for x in s[0]["articles"])) if kind == "삭제조" else (lambda s: True)
            s = take(pred)
            if not s:
                break
            r, a, it, tail = s
            loc = {"reg_id": r["reg_id"], "article": a["label"], "para": it["path"][0]}
            nums = sorted(n for n in (_jo(x["label"]) for x in r["articles"] if not x["deleted"]) if n)
            if kind == "오타":
                words = [w for w in re.findall(r"[가-힣]{4,}", it["norm"]) if it["norm"].count(w) == 1]
                if not words:
                    continue
                w = rng.choice(words)
                add("P-오타", r, f'{_unit(a, it)} 중 "{w}"를 "{_typo(w)}"로 한다.', {"typo": [loc]}, f"「{w}」→「{_typo(w)}」")
                continue
            if kind == "없는조":
                sent, exp = f"다만, 세부 사항은 제{max(nums) + 9}조에 따른다.", {"breaks": [loc]}
            elif kind == "삭제조":
                d = rng.choice([x["label"] for x in r["articles"] if x["deleted"] and _jo(x["label"])])
                sent, exp = f"다만, 세부 사항은 {d}에 따른다.", {"breaks": [loc]}
            elif kind == "규정명오기":
                other = rng.choice([x for x in regl if x["reg_id"] != r["reg_id"] and x["name"].endswith(("규정", "지침"))])
                from .inject import misname
                k = rng.choice([x["label"] for x in other["articles"] if not x["deleted"] and _jo(x["label"])])
                sent, exp = f"다만, {misname(other['name'])} {k}에 따른 경우는 예외로 한다.", {"breaks": [loc]}
            elif kind == "옛법령명":
                old, _new = rng.choice(renamed)
                sent, exp = f"다만, 「{old}」에 따른 경우는 예외로 한다.", {"law_name": [loc]}
            elif kind == "법령명오기":
                name = rng.choice(cited_laws)
                i = len(name) // 2 + (1 if name[len(name) // 2] == " " else 0)
                sent, exp = f"다만, 「{name[:i] + name[i + 1:]}」에 따른 경우는 예외로 한다.", {"law_name": [loc]}
            else:   # 정상인용: 같은 규정의 존재하는 다른 조
                k = rng.choice([n for n in nums if f"제{n}조" != a["label"]] or nums)
                sent, exp = f"다만, 세부 사항은 제{k}조에 따른다.", {}
            add("P-" + kind, r, f'{_unit(a, it)} 중 "{tail}"를 "{tail} {sent}"로 한다.', exp, sent,
                not_affected=[loc] if kind == "정상인용" else None)

    # ── 다른 규정 같은 제목 조와 같던 숫자를 바꿈 ───────────────
    RE_NU = re.compile(r"(?<![\d.])(\d+)\s*(일|개월|년|회|시간|%|원|만원|세|명)")
    pairs = []
    by_title: dict[str, list] = {}
    for r in regl:
        for a in r["articles"]:
            if not a["deleted"] and a["title"] and len(a["title"]) >= 3:
                by_title.setdefault(a["title"], []).append((r, a))
    for title, lst in by_title.items():
        for r1, a1 in lst:
            for r2, a2 in lst:
                if r1["reg_id"] == r2["reg_id"]:
                    continue
                for it in a1["items"]:
                    if it["path"][1:] != [0, 0]:
                        continue
                    for m in RE_NU.finditer(it["norm"]):
                        tok = m.group(0)
                        hit = next((x for x in a2["items"] if tok in x["norm"]), None)
                        if hit and it["norm"].count(tok) == 1:
                            pairs.append((r1, a1, it, tok, m, r2, a2, hit))
                            break
    rng.shuffle(pairs)
    seen_items = set()
    for r1, a1, it, tok, m, r2, a2, hit in pairs:
        if it["id"] in seen_items or len([o for o in out if o["kind"] == "NUMC"]) >= 10:
            continue
        seen_items.add(it["id"])
        new = str(int(m.group(1)) + 1) + tok[len(m.group(1)):]
        add("NUMC", r1, f'{_unit(a1, it)} 중 "{tok}"을 "{new}"으로 한다.',
            {"new_conflicts": [{"reg_id": r2["reg_id"], "article": a2["label"], "para": hit["path"][0]}]},
            f"{tok}→{new} · 같은 제목 「{a1['title']}」 {r2['name']} {a2['label']}에 같은 값")

    # ── 인용한 법령 조문과 같던 숫자를 바꿈 ───────────────────
    items = {it["id"]: (r, a, it) for r in regl for a, it in _items(r)}
    numl = []
    for e in edges:
        if e["status"] != "law_pending" or e["src"] not in items or e["target_type"] != "article":
            continue
        law = db.find(e["target_doc"])
        key, u = db.article(law, e["raw"]) if law else (None, None)
        if not u:
            continue
        r, a, it = items[e["src"]]
        law_txt = " ".join(x.get("항내용", "") for x in (u.get("항") if isinstance(u.get("항"), list) else [u.get("항")] if u.get("항") else []) if isinstance(x, dict)) or u.get("조문내용", "")
        for mm in RE_NU.finditer(it["norm"]):
            if mm.group(0) in law_txt and it["norm"].count(mm.group(0)) == 1:
                numl.append((r, a, it, mm, law["title"], key))
                break
    seen_items = set()
    for r, a, it, mm, lt, key in numl:
        if it["id"] in seen_items:
            continue
        seen_items.add(it["id"])
        tok = mm.group(0)
        new = str(int(mm.group(1)) * 2) + tok[len(mm.group(1)):]
        add("NUML", r, f'{_unit(a, it)} 중 "{tok}"을 "{new}"으로 한다.',
            {"law_num": [{"reg_id": r["reg_id"], "article": a["label"], "para": it["path"][0]}]}, f"{tok}→{new} · 「{lt}」 제{key}조에 같은 값")

    # ── 표현만 다듬기 ───────────────────────────────────
    clean = [(r, a, it) for r in regl for a, it in _items(r) if it["norm"].count("하여야 한다") == 1 and not re.search(r"제\s*\d+\s*조|\d", it["norm"])]
    rng.shuffle(clean)
    for r, a, it in clean[:15]:
        add("CLEAN", r, f'{_unit(a, it)} 중 "하여야 한다"를 "해야 한다"로 한다.', {}, "표현만 다듬음",
            not_affected=[{"reg_id": r["reg_id"], "article": a["label"], "para": it["path"][0]}])
    return out


def make_extra(regs: dict, edges: list[dict], db, seed: int, start: int, n_ins: int = 15, n_insn: int = 5, n_mix: int = 15, n_typo: int = 42) -> list[dict]:
    """v0 106문항 뒤에 덧붙이는 문항 (기존 번호가 바뀌지 않게 별도 난수로)"""
    from .typo_gen import mutate
    rng = random.Random(seed + 1000)
    out: list[dict] = []

    def add(kind, reg, amendment, expected, note, not_affected=None):
        out.append({"id": f"G{start + len(out):03d}", "kind": kind, "reg_id": reg["reg_id"], "reg_name": reg["name"],
                    "amendment": amendment, "expected": expected, "not_affected": not_affected or [], "note": note})

    regl = list(regs.values())
    # ── INS / INSN: 조를 끼워 넣고 뒤 번호를 한 칸씩 민다 ───────────────
    cands = []
    for r in regl:
        labs = [a["label"] for a in r["articles"]]
        nums = [_jo(l) for l in labs]
        if None in nums or nums != list(range(1, len(nums) + 1)) or len(nums) < 8:
            continue   # 가지 번호가 섞였거나 번호가 이어지지 않는 규정은 제외 (밀기 개정문이 단순하지 않음)
        per_label = {m: text_citers(regs, r, f"제{m}조") for m in range(2, len(nums) + 1)}   # 조마다 한 번만 (인용 검색이 느림)
        for k in range(2, len(nums)):          # 제k조 자리에 신설 (제k-1조 다음), 제k~M조를 한 칸씩 뒤로
            cit = [{**c, "_cited": f"제{m}조"} for m in range(k, len(nums) + 1) for c in per_label[m]]
            cands.append((r, k, len(nums), cit))
    with_cit = [c for c in cands if c[3]]
    without = [c for c in cands if not c[3]]
    rng.shuffle(with_cit)
    rng.shuffle(without)

    def ins_sentence(r, k, m):
        return (f"제{k}조부터 제{m}조까지를 각각 제{k + 1}조부터 제{m + 1}조까지로 하고, 제{k - 1}조의 다음에 제{k}조를 다음과 같이 신설한다.\n"
                f"제{k}조(업무 협조) ① 회사는 업무 수행에 필요한 경우 관련 부서의 협조를 요청할 수 있다.")
    seen = set()
    for r, k, m, cit in with_cit:
        if r["reg_id"] in seen and len(seen) < 12:
            continue                              # 규정이 여러 개 섞이게
        seen.add(r["reg_id"])
        same = [{k2: v for k2, v in c.items() if k2 != "_cited"} for c in cit if c["reg_id"] == r["reg_id"]]
        other = [{k2: v for k2, v in c.items() if k2 != "_cited"} for c in cit if c["reg_id"] != r["reg_id"]]
        exp = {}
        if same:
            exp["breaks"] = same
        if other:
            exp["review"] = other
        add("INS", r, ins_sentence(r, k, m), exp, f"제{k}조 신설, 제{k}~{m}조 한 칸씩 밀림 — 원문 검색 인용 같은 규정 {len(same)}곳·다른 규정 {len(other)}곳")
        if len([o for o in out if o["kind"] == "INS"]) >= n_ins:
            break
    for r, k, m, _ in without[:n_insn]:
        add("INSN", r, ins_sentence(r, k, m), {}, f"제{k}조 신설, 뒤 조를 인용하는 곳 없음")

    # ── MIX: 한 개정안에 문제 셋 ─────────────────────────────
    slots = [(r, a, it, _tail(it)) for r in regl for a, it in _items(r)]
    slots = [x for x in slots if x[3] and len(x[2]["norm"]) < 400]
    by_reg: dict[str, list] = {}
    for x in slots:
        by_reg.setdefault(x[0]["reg_id"], []).append(x)
    regs_ok = [rid for rid, v in by_reg.items() if len({(a["label"]) for _, a, _, _ in v}) >= 3]
    rng.shuffle(regs_ok)
    renamed = list(RENAMED.items())
    k = 0
    for rid in regs_ok:
        if k >= n_mix:
            break
        pool = by_reg[rid][:]
        rng.shuffle(pool)
        picks, arts = [], set()
        for x in pool:
            if x[1]["label"] not in arts:
                picks.append(x)
                arts.add(x[1]["label"])
            if len(picks) == 3:
                break
        if len(picks) < 3:
            continue
        r = picks[0][0]
        nums = sorted(n for n in (_jo(x["label"]) for x in r["articles"] if not x["deleted"]) if n)
        (_, a1, i1, t1), (_, a2, i2, t2), (_, a3, i3, _t3) = picks
        old = rng.choice(renamed)[0]
        words = [w for w in re.findall(r"[가-힣]{4,}", i3["norm"]) if i3["norm"].count(w) == 1]
        if not words:
            continue
        m = mutate(rng.choice(("자리바꿈", "모음", "받침빠짐")), i3["norm"], rng)
        if not m:
            continue
        loc = lambda a, it: {"reg_id": r["reg_id"], "article": a["label"], "para": it["path"][0]}   # noqa: E731
        amend = "\n".join([
            f'{_unit(a1, i1)} 중 "{t1}"를 "{t1} 다만, 세부 사항은 제{max(nums) + 9}조에 따른다."로 한다.',
            f'{_unit(a2, i2)} 중 "{t2}"를 "{t2} 다만, 「{old}」에 따른 경우는 예외로 한다."로 한다.',
            f'{_unit(a3, i3)} 중 "{m[1]}"를 "{m[2]}"로 한다.'])
        add("MIX", r, amend, {"breaks": [loc(a1, i1)], "law_name": [loc(a2, i2)], "typo": [loc(a3, i3)]}, f"없는 조 + 「{old}」 + 오타 「{m[1]}」→「{m[2]}」")
        k += 1

    # ── 오타 확장 ───────────────────────────────────────
    sents = [(r, a, it) for r in regl for a, it in _items(r) if 30 <= len(it["norm"]) <= 300]
    rng.shuffle(sents)
    kinds = ("자리바꿈", "모음", "받침빠짐", "맞춤법", "반복")
    j = 0
    for r, a, it in sents:
        if j >= n_typo:
            break
        m = mutate(kinds[j % 5], it["norm"], rng)
        if not m or "\"" in m[1] + m[2]:
            continue
        add("P-오타", r, f'{_unit(a, it)} 중 "{m[1]}"를 "{m[2]}"로 한다.', {"typo": [{"reg_id": r["reg_id"], "article": a["label"], "para": it["path"][0]}]},
            f"{kinds[j % 5]} 「{m[1]}」→「{m[2]}」")
        j += 1
    return out


def hits(found: list[dict], exp: dict, cat: str) -> bool:
    """기대 위치 하나를 탐지했나. 위치 = 규정 + 조 (+ 항이 둘 다 있으면 항). 새 충돌은 상대 조의 어느 항과 비교했든 조 단위로 본다"""
    def same(f):
        return f["reg_id"] == exp["reg_id"] and f.get("article") == exp["article"] and (
            cat == "new_conflicts" or not exp.get("para") or not f.get("para") or int(f["para"]) == int(exp["para"]))
    want = {"breaks": ("breaks",), "new_conflicts": ("new_conflicts",), "law_name": ("law_name",), "typo": ("typo",),
            "law_num": ("law_num",), "review": ("review", "breaks")}[cat]   # 다른 규정의 인용은 「확인 필요」(깨짐으로 알려도 맞음)
    return any(f["category"] in want and same(f) for f in found)
