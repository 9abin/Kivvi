"""한석희 120문항 2단계: 개정 항목 + 후보(1단계) + 인용 그래프에서 새 문제 · 해소된 문제를 규칙으로 판정한다 (LLM 없음).
  uv run python scripts/judge_four_domain.py <gold.jsonl> <문항 폴더(cases/ 가 있는 곳)> <base_corpus.jsonl> [--ctx6] [--no-t2]

입력: out/four_domain_ops.json(개정 항목), out/four_domain_stage1.json(항목별 후보), out/kg(인용 그래프), 문항별 before/after overlay(개정 전후 상태 본문).
판정은 정답(gold)의 관계 정의(20개 family)를 쓰지 않는다. 일반 규칙 두 가지다.
 (1) 값 불일치: 숫자를 바꾸는 개정 항목마다 후보 단위에서 「같은 문맥의 숫자」를 찾아 개정 전후를 비교한다.
     T1: 바뀐 말 주변 글(앞 14자 + 바뀐 말 + 뒤 8자)을 숫자 자리만 비워 그대로 찾는다. T2: 같은 단위 숫자 주변(±24자)에 희귀 낱말이 겹치면 같은 사항으로 본다.
     새 문제 = 개정 전에는 같았고 개정 뒤 달라짐, 해소 = 개정 전에는 달랐고 개정 뒤 같아짐.
 (2) 깨진 인용: 조 · 별표를 이동하는 개정 항목마다 그 단위를 인용하는 조(인용 그래프, 부칙 제외)를 찾아, 같은 문항에서 인용 번호를 새 번호로 고쳤는지 본다.
     고치지 않았으면 새 문제, 개정 전 상태에서 이미 옮겨져 있는데(overlay) 같은 문항이 인용을 새 번호로 고치면 해소.
채점: 문항 단위 「알려진 새 문제 · 해소를 모두 찾음」(정답 밖 발견은 검토 대기로 따로 센다), 문제 단위 재현율 · 정밀도.
"""
import collections
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")
NUM = r"\d+(?:/\d+)?(?:\.\d+)?"
RARE_DF = 20   # 이 문서 수 이하에만 나오는 낱말을 희귀하다고 본다
sl = lambda s: re.sub(r"\s+", "", s or "")  # noqa: E731
JOSA = re.compile(r"(으로|에서|에게|까지|부터|이다|하는|하여|한다|에는|에도|의|를|을|은|는|이|가|에|로|과|와|도|만)$")


def words(t):
    out = set()
    for w in re.findall(r"[가-힣]{3,}", t or ""):
        w = JOSA.sub("", w)
        if len(w) >= 3:
            out.add(w)
    return out


def jl(p):
    return [json.loads(l) for l in Path(p).read_text(encoding="utf-8").splitlines() if l.strip()]


def mask_re(a_ctx):
    """숫자 자리를 비운 정규식 (공백 제거 본문용)."""
    parts = re.split(f"({NUM})", a_ctx)
    return "".join(f"({NUM})" if re.fullmatch(NUM, p) else re.escape(p) for p in parts)


def main():
    gold = {x["id"]: x for x in jl(sys.argv[1])}
    cases_dir = Path(sys.argv[2]); base = {r["id"]: r for r in jl(sys.argv[3])}
    use_t2 = "--no-t2" not in sys.argv
    ops = json.loads((ROOT / "out/four_domain_ops.json").read_text(encoding="utf-8"))
    st1 = json.loads((ROOT / "out/four_domain_stage1.json").read_text(encoding="utf-8"))["cache"]
    nodes = {n["id"]: n for n in jl(ROOT / "out/kg/nodes.jsonl")}
    cites_in = collections.defaultdict(set)
    for e in jl(ROOT / "out/kg/edges.jsonl"):
        if e["type"] == "CITES":
            cites_in[e["dst"]].add(e["src"])
    df = collections.Counter(w for r in base.values() for w in words(r["text"]))

    def overlay(q, which):
        p = cases_dir / "cases" / q / f"{which}_overlay.jsonl"
        return {r["id"]: r for r in jl(p)} if p.exists() else {}

    pred, summary = {}, collections.Counter()
    for q, g in gold.items():
        bo, ao = overlay(q, "before"), overlay(q, "after")
        text_b = lambda u: sl((bo.get(u) or base.get(u) or {}).get("text", ""))  # noqa: E731
        text_a = lambda u: sl((ao.get(u) or bo.get(u) or base.get(u) or {}).get("text", ""))  # noqa: E731
        changed_ops = collections.defaultdict(list)
        for op in ops[q]:
            changed_ops[op["article"]].append(op)
        issues = set()
        # (1) 값 불일치
        for k, op in enumerate(ops[q]):
            if op["type"] != "replace":
                continue
            a, b = op["old"], op["new"]
            if re.search(r"제\s*\d+\s*조|별표\s*\d+", a):   # 인용 번호 갱신은 (2)에서 본다
                continue
            an, bn = re.findall(NUM, a), re.findall(NUM, b)
            if not an or an == bn:
                continue
            U = op["article"]; ut = text_b(U); sa = sl(a)
            i = ut.find(sa)
            if i < 0:
                continue
            pos = re.search(NUM, sa)
            unit_suffix = ""   # 첫 숫자 바로 뒤의 단위 글자(일 · 개월 · 세 ...). 분수 · 숫자로 끝나면 없음
            if pos:
                m_u = re.match(r"[가-힣%]", sa[pos.end():])
                unit_suffix = m_u.group(0) if m_u else ""
            long_pat = re.escape(ut[max(0, i - 14):i]) + mask_re(sa) + re.escape(ut[i + len(sa):i + len(sa) + 8])
            short_pat = re.escape(ut[max(0, i - 6):i]) + mask_re(sa) + re.escape(ut[i + len(sa):i + len(sa) + 3])
            cand = (st1.get(f"{q}-{k}") or {}).get("all", [])
            wU = words(ut[max(0, i - 40):i + len(sa) + 40])
            overlap = lambda t, s0, e0: bool({w for w in words(t[max(0, s0 - 40):e0 + 40]) & wU if df[w] <= RARE_DF})  # noqa: E731
            for P in cand:
                if P == U or P not in base:
                    continue
                def val(textfn):
                    t = textfn(P)
                    m = re.search(long_pat, t)
                    if m:
                        return m.group(1), "T1"
                    m = re.search(short_pat, t)
                    if m and overlap(t, m.start(), m.end()):
                        return m.group(1), "T1s"
                    if not use_t2:
                        return None, None
                    for mm in re.finditer(f"({NUM}){re.escape(unit_suffix)}", t):
                        if overlap(t, mm.start(), mm.end()):
                            return mm.group(1), "T2"
                    return None, None
                vb, how_b = val(text_b); va, _ = val(text_a)
                if vb is None or va is None:
                    continue
                key = tuple(sorted((U, P)))
                if vb == an[0] and va != bn[0]:
                    issues.add(("new", key, "value_mismatch", how_b))
                elif vb != an[0] and va == bn[0]:
                    issues.add(("resolved", key, "value_mismatch", how_b))
        # (2) 깨진 인용
        for op in ops[q]:
            if op["type"] != "move":
                continue
            X, N, M = op["article"], sl(op["label"]), sl(op["to"])
            for u in cites_in.get(X, ()):
                if nodes.get(u, {}).get("type") == "Supplement":
                    continue
                ua = u.split("-")[0]
                updated = any(o["type"] == "replace" and N in sl(o["old"]) and M in sl(o["new"]) for o in changed_ops.get(ua, []))
                if not updated:
                    issues.add(("new", tuple(sorted((ua, X))), "broken_citation", "move"))
        for X, row in bo.items():   # 개정 전 상태에서 이미 옮겨진 단위를 인용하는 조를 같은 문항이 새 번호로 고치면 해소
            lab_b = sl(row.get("label", "")); lab_0 = sl((base.get(X) or {}).get("label", ""))
            if not lab_0 or lab_b == lab_0:
                continue
            for u in cites_in.get(X, ()):
                ua = u.split("-")[0]
                if any(o["type"] == "replace" and lab_0 in sl(o["old"]) and lab_b in sl(o["new"]) for o in changed_ops.get(ua, [])):
                    issues.add(("resolved", tuple(sorted((ua, X))), "broken_citation", "overlay"))
        pred[q] = issues
    # 채점
    def gold_issues(g):
        s = set()
        for state in ("new", "resolved"):
            for iid in g["expected_delta"][state]:
                iss = (g["after_issues"] if state == "new" else g["before_issues"])[iid]
                a, b = [x.split("-")[0] for x in iss["logical_pair"]]
                s.add((state, tuple(sorted((a, b))), iss["kind"]))
        return s
    tp = fp = fn = 0
    q_known = q_strict = q_lenient = 0
    by = collections.defaultdict(lambda: collections.Counter())
    for q, g in gold.items():
        G = gold_issues(g); Pq = {(s, p, k) for s, p, k, _ in pred[q]}
        # 문제 단위는 조 쌍 + 상태 + 종류가 맞아야 한다. 같은 쌍 · 다른 상태는 틀린 것으로 센다
        t = len(G & Pq); tp += t; fn += len(G - Pq); fp += len(Pq - G)
        for s, p, k in G:
            by[(g["scenario_type"], k)]["정답"] += 1; by[(g["scenario_type"], k)]["맞음"] += (s, p, k) in Pq
        extra = Pq - G
        if G:
            q_known += 1
            q_lenient += G <= Pq
            q_strict += (G == Pq)
        else:
            q_strict += (not Pq); q_lenient += 1
            by[("clean", "-")]["문항"] += 1; by[("clean", "-")]["오경보 문항"] += bool(Pq)
    print(f"문항 {len(gold)} (정답 문제 있는 문항 {q_known}, 정상 변경 {len(gold)-q_known})")
    print(f"문제 단위: 정답 {tp+fn} 중 맞춤 {tp} ({tp/max(tp+fn,1):.0%}) | 정답 밖 발견 {fp} | 정밀도 {tp/max(tp+fp,1):.0%}")
    print(f"문항 단위(한석희 척도: 알려진 문제를 모두 찾음, 정답 밖은 검토 대기): {q_lenient}/{len(gold)}")
    print(f"문항 단위(엄격: 정답 밖 발견도 없음): {q_strict}/{len(gold)}")
    print("\n[유형별]")
    for k, c in sorted(by.items()):
        if k[0] == "clean":
            print(f"  정상 변경 {c['문항']}문항 중 오경보가 있는 문항 {c['오경보 문항']}")
        else:
            print(f"  {k[0]:9s} {k[1]:16s} 정답 {c['정답']:3d} 맞음 {c['맞음']:3d} ({c['맞음']/max(c['정답'],1):.0%})")
    json.dump({q: [list(map(str, i)) for i in sorted(v, key=str)] for q, v in pred.items()}, open(ROOT / "out/four_domain_stage2.json", "w", encoding="utf-8"), ensure_ascii=False)


if __name__ == "__main__":
    main()
