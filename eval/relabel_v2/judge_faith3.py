"""답변 충실도 판정 3판 (261113, 리포트 101 뒤). 판정 모델이 답변을 주장으로 바꿔 쓰다 없는 주장을 만들던 문제(faith2: Solar 「문제 있음」 28개 중 18개 오판)를 막는다.

  python eval/relabel_v2/judge_faith3.py judge <bm|test> <luna 답변 실행.jsonl> <규칙 답변 실행.jsonl> <출력.jsonl> <모델> [판정표.csv]
  python eval/relabel_v2/judge_faith3.py agree <판정표.csv> <출력.jsonl> [...]      # 판정표(사람 · Claude 판정)와 일치도
  python eval/relabel_v2/judge_faith3.py report <출력.jsonl>

- 코드가 답변을 문장으로 자른다. 판정 모델은 문장을 고쳐 쓰지 않고 문장 번호마다 판정만 붙인다.
- 판정: 권고뿐 | 문제없음 | 근거 없는 해석 | 틀린 사실 (사람 판정표와 같은 기준).
- 근거를 코드로 확인한다: 문제없음 · 근거 없는 해석의 「원문 구절」은 원문에 글자 그대로(띄어쓰기 무시) 있어야 하고,
  근거 없는 해석 · 틀린 사실의 「문제 부분」은 그 답변 문장 안에 있어야 한다. 어긋나면 판정을 「확인 실패」로 두고 집계에서 따로 센다.
- 원문만 준다(개정문 · 개정 전후 조문 · 후보 조문, luna 이유 · 찾은 경로 없음). 후보 조문은 3,000자까지, 넘으면 근거 문장 주변 + 「잘림」 표시.
- 모델: solar-… (SOLAR_REASONING=high면 추론 모드) 또는 gpt-…(답변을 쓴 gpt-5.6-luna와 다른 세대 권장).
- 판정 기준 예시는 가상 예시다(검증용 판정표 50개를 예시로 쓰지 않는다).
"""
import csv
import glob
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "eval/relabel_v2")]
sys.stdout.reconfigure(encoding="utf-8")
from judge_solar import context as load_ctx, jl, n, run_jobs  # noqa: E402

LABELS = ("권고뿐", "문제없음", "근거 없는 해석", "틀린 사실")
WORST = {"권고뿐": 0, "문제없음": 1, "확인 실패": 2, "근거 없는 해석": 3, "틀린 사실": 4}
CAND_MAX = 3000

PROMPT = """너는 규정 개정 검토 답변의 사실 확인자다. 아래 [원문]만 보고 [답변 문장] 하나하나를 판정한다. 상식이나 외부 지식은 쓰지 않는다.
답변 문장을 고쳐 쓰거나, 문장에 없는 새 주장을 만들지 않는다.

판정 (문장마다 하나)
- 권고뿐: 사실 내용 없이 「~를 확인해 볼 만하다」 같은 제안만 있다.
- 문제없음: 문장의 사실 내용(무엇이 바뀌는지, 어느 조가 무엇을 정하는지)이 모두 원문에 있다. 말을 바꿔 써도 뜻이 같으면 된다.
- 근거 없는 해석: 각 사실은 원문에 있지만, 원문에 없는 관계 · 영향을 **사실처럼 단정**한다(「A와 B가 이어진다」 「A 때문에 B가 달라진다」 「A가 B에 영향을 미친다」). 원문에서 바로 따라 나오는 결과(삭제된 조가 정하던 근거가 없어진다 등)는 문제없음이다.
- 틀린 사실: 원문과 다르거나 원문에 없는 사실(조 번호 · 내용 · 이름 · 숫자)이 있다.
「A이므로 B를 확인해 볼 만하다」 「이에 따라 B를 확인해 볼 만하다」처럼 확인을 권하는 이유로 개정과 후보를 잇는 것은 이 답변의 목적이라 해석이 아니다. 이런 문장은 A와 B 안의 사실(B가 무엇을 정하는지 등)만 판정한다. 사실이 없으면 권고뿐.
한 문장에 여러 문제가 있으면 더 나쁜 쪽(틀린 사실 > 근거 없는 해석)으로.

적을 것
- 문제없음: 「원문 구절」에 근거가 되는 원문 부분을 글자 그대로 옮긴다(10~80자).
- 근거 없는 해석: 「원문 구절」에 근거 원문 부분, 「문제 부분」에 답변 문장에서 원문에 없는 연결 부분을 글자 그대로.
- 틀린 사실: 「문제 부분」에 답변 문장의 틀린 부분을 글자 그대로, 「이유」에 한 문장.
- 권고뿐: 나머지는 빈 문자열.

예) 원문: 「제5조 ① 회의비는 매월 25일에 지급한다.」 개정: 「25일」을 「20일」로.
  문장 「회의비 지급일이 매월 25일에서 20일로 바뀐다.」 → 문제없음, 원문 구절 「회의비는 매월 25일에 지급한다」
  문장 「제5조는 출장비 지급일도 정한다.」 → 틀린 사실, 문제 부분 「출장비 지급일도 정한다」
  문장 「지급일이 바뀌므로 제5조의 지급 절차를 확인해 볼 만하다.」 → 문제없음(확인을 권하는 연결), 원문 구절 「회의비는 매월 25일에 지급한다」
  문장 「지급일 변경으로 제9조의 예산 집행 일정이 앞당겨진다.」 → 근거 없는 해석(원문에 없는 영향을 단정), 문제 부분 「예산 집행 일정이 앞당겨진다」

[원문]
{context}

[답변 문장]
{sentences}

JSON 한 줄만 출력한다: {{"판정": [{{"문장": 번호, "판정": "권고뿐|문제없음|근거 없는 해석|틀린 사실", "원문 구절": "...", "문제 부분": "...", "이유": "..."}}]}}"""


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.?!])\s+|\n+", text.strip())
    return [p.strip() for p in parts if len(n(p)) >= 4]


def call(model: str, prompt: str):
    from regcheck.nl import api_chat
    for _ in range(3):
        try:
            out = api_chat(model, [{"role": "user", "content": prompt}], timeout=180, max_tokens=3000)
        except Exception:  # noqa: BLE001
            continue
        m = re.search(r"\{.*\}", out, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    return None


def check(item: dict, sent: str, source: str) -> str:
    """판정 하나를 코드로 확인해 최종 판정을 낸다."""
    lab = item.get("판정")
    if lab not in LABELS:
        return "확인 실패"
    quote, part = n(item.get("원문 구절")), n(item.get("문제 부분"))
    if lab == "문제없음" and not (len(quote) >= 4 and quote in n(source)):
        return "확인 실패"
    if lab in ("근거 없는 해석", "틀린 사실") and not (len(part) >= 2 and part in n(sent)):
        return "확인 실패"
    return lab


def build_jobs(d, luna_run, rule_run, keys=None):
    from regcheck.change_query import analyze
    from regcheck.final_view import cand_window
    from regcheck.luna_rerank import change_block
    ctx = load_ctx()
    regs = {r["reg_id"]: r for r in (json.loads(Path(p).read_text(encoding="utf-8")) for p in glob.glob(str(ROOT / "out/parsed/[0-9]*.json")))}
    by = {r["name"]: k for k, r in regs.items()}
    ch = {m["id"]: m for m in jl(ROOT / f"eval/relabel_v2/{d}/changes.jsonl")}
    change, jobs = {}, []
    for route, f, key in (("luna", luna_run, "other"), ("decide", rule_run, "rule")):
        for r in jl(f):
            for st in r[key]:
                k = f"{r['q']}|{st['id']}|{route}"
                a = (st.get("answer") or {}).get("text") or ""
                if not a or (keys and k not in keys):
                    continue
                if r["q"] not in change:
                    s0 = ch[r["q"].split(":")[1]]["수정"]; reg = regs[by[s0["규정"]]]
                    cq = analyze(reg, s0["개정문"], r["q"].split(":")[1])
                    change[r["q"]] = change_block(reg, s0["개정문"], cq.starts[0] if cq.starts else None, max_old=3000)
                nd = ctx.nodes.get(st["id"], {})
                name = f"{ctx.nodes.get(nd.get('reg', ''), {}).get('name', '')} {nd.get('label', st['id'])}({nd.get('title', '')})"
                full = ctx.unit_text.get(st["id"], "")
                quote = (st.get("evidence") or {}).get("quote") or ""
                text = cand_window(full, [quote] if quote else [], n=CAND_MAX)
                jobs.append({"key": k, "q": r["q"], "id": st["id"], "route": route, "answer": a, "sents": sentences(a),
                             "truncated": len(full) > CAND_MAX,
                             "context": f"[개정]\n{change[r['q']]}\n\n[확인 대상 조] {name}{' (앞뒤 잘림)' if len(full) > CAND_MAX else ''}\n{text}"})
    return jobs


def judge(d, luna_run, rule_run, out, model, sheet=None):
    keys = {row["키"] for row in csv.DictReader(open(sheet, encoding="utf-8-sig"))} if sheet else None
    jobs = build_jobs(d, luna_run, rule_run, keys)

    def fn(j):
        sents = "\n".join(f"{i}. {s}" for i, s in enumerate(j["sents"], 1))
        o = call(model, PROMPT.format(context=j["context"], sentences=sents)) or {}
        items = {it.get("문장"): it for it in o.get("판정") or [] if isinstance(it, dict)}
        res = []
        for i, s in enumerate(j["sents"], 1):
            it = items.get(i) or items.get(str(i)) or {}
            res.append({"문장": s, "판정 원본": it.get("판정", ""), "최종": check(it, s, j["context"]) if it else "확인 실패",
                        "원문 구절": it.get("원문 구절", ""), "문제 부분": it.get("문제 부분", ""), "이유": it.get("이유", "")})
        labs = [r["최종"] for r in res]
        return {"model": model, "ok": bool(o), "sentence_judgments": res,
                "answer_label": max(labs, key=lambda x: WORST[x]) if labs else "확인 실패"}
    run_jobs(jobs, fn, out)


def to3(lab: str) -> str:
    """판정표와 비교용 세 갈래. 권고뿐 · 문제없음은 문제없음으로."""
    return {"권고뿐": "문제없음", "문제없음": "문제없음"}.get(lab, lab)


def agree(sheet, *outs):
    ref = {row["키"]: row["Claude 판정"] if not row.get("사람 확인(맞음/틀림)", "").startswith("틀") else row.get("사람 메모", "")
           for row in csv.DictReader(open(sheet, encoding="utf-8-sig"))}
    for f in outs:
        R = {x["key"]: x for x in jl(f)}
        pairs = [(ref[k], to3(R[k]["answer_label"])) for k in ref if k in R]
        conf = Counter(pairs)
        acc = sum(a == b for a, b in pairs) / max(len(pairs), 1)
        prob_ref = [p for p in pairs if p[0] != "문제없음"]
        caught = sum(p[1] in ("근거 없는 해석", "틀린 사실") for p in prob_ref)
        clean = [p for p in pairs if p[0] == "문제없음"]
        false_alarm = sum(p[1] in ("근거 없는 해석", "틀린 사실") for p in clean)
        wrong_ref = [p for p in pairs if p[0] == "틀린 사실"]
        unsure = sum(p[1] == "확인 실패" for p in pairs)
        model = next(iter(R.values()))["model"] if R else "?"
        print(f"\n## {Path(f).name} ({model}) 비교 {len(pairs)}")
        print(f"  세 갈래 일치 {acc:.2f} | 문제 잡음 {caught}/{len(prob_ref)} | 틀린 사실 잡음 {sum(p[1] == '틀린 사실' for p in wrong_ref)}/{len(wrong_ref)} "
              f"| 문제없음을 문제로(오판) {false_alarm}/{len(clean)} | 확인 실패 {unsure}")
        print("  (기준, 판정): " + ", ".join(f"{a}→{b} {c}" for (a, b), c in sorted(conf.items())))


def report(f):
    F = jl(f)
    for route in ("luna", "decide", None):
        xs = [x for x in F if route is None or x["route"] == route]
        c = Counter(x["answer_label"] for x in xs); t = len(xs) or 1
        sj = [s for x in xs for s in x["sentence_judgments"]]
        fact = [s for s in sj if s["최종"] in ("문제없음", "근거 없는 해석", "틀린 사실")]
        name = {"luna": "luna 답변", "decide": "규칙 답변", None: "전체"}[route]
        print(f"- {name} {len(xs)}개: 틀린 사실 {c['틀린 사실'] / t:.3f}, 근거 없는 해석 {c['근거 없는 해석'] / t:.3f}, 확인 실패 {c['확인 실패'] / t:.3f} "
              f"| 문장 단위 문제없음 {sum(s['최종'] == '문제없음' for s in fact) / max(len(fact), 1):.3f} (사실 문장 {len(fact)})")


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "judge":
        judge(*sys.argv[2:8])
    elif cmd == "agree":
        agree(*sys.argv[2:])
    elif cmd == "report":
        report(sys.argv[2])
    else:
        sys.exit(__doc__)
