"""판정 모델(Solar)로 재는 지표 (리포트 94). 답변 · 리랭킹을 luna가 하므로 판정은 다른 회사 모델로 한다.

  python eval/relabel_v2/judge_solar.py adjudicate <bm|val> <실행 결과.jsonl> <출력.jsonl>
  python eval/relabel_v2/judge_solar.py faith      <bm|val> <실행 결과.jsonl> <출력.jsonl>
  python eval/relabel_v2/judge_solar.py report     <bm|val> <실행 결과.jsonl> <판정.jsonl> <충실도.jsonl>
  python eval/relabel_v2/judge_solar.py faith2     <bm|test> <luna 답변 실행.jsonl> <규칙 답변 실행.jsonl> <출력.jsonl>
  python eval/relabel_v2/judge_solar.py report2    <충실도2.jsonl> [권고 속 사실.jsonl]
  python eval/relabel_v2/judge_solar.py faith2b    <충실도2.jsonl> <출력.jsonl>   # 「권고」로 분류된 문장 속 사실 전제를 따로 판정

adjudicate (판정 보완, pooling): 보여 준 후보마다 라벨 지시서(v2.1)와 같은 기준으로 「함께 살펴볼 조인가」를 판정한다.
  정답(gold)도 판정해서 판정 모델이 라벨과 얼마나 맞는지(정답에 「예」라고 한 비율)를 함께 본다.
  근거문장은 후보 원문과 글자 대조(띄어쓰기 · 가운뎃점 무시)를 통과해야 「예」로 센다.
faith (RAGAS Faithfulness 방식): 답변을 주장 문장으로 나누고, 답변 모델이 본 입력(answer_writer.prompt_for)으로
  뒷받침되는지 문장마다 판정한다. Faithfulness = 뒷받침되는 문장 / 전체 문장. Hallucination rate = 뒷받침 안 되는 문장이 하나라도 있는 답변 비율.
faith2 (261113, 리포트 96 지적 1 · 2 반영): 판정 근거를 원문만으로 한다(개정문 · 개정 전 조문 · 바뀌는 부분 + 후보 조 원문).
  luna 이유 · 관계 판단 · 찾은 경로 이름은 넣지 않는다. 판정 모델이 주장마다 「사실 / 권고」를 직접 표시하고 사실만 센다
  (이전 faith는 권고 어미 문장을 정규식으로 통째로 빼서 그 안의 사실도 빠졌다).
Answer Relevancy(RAGAS)는 재지 않는다: 질문이 「이 개정 때문에 이 조를 왜 봐야 하나」로 고정이라 질문 생성 · 유사도 방식이 의미가 없다.
"""
import json
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")

MODEL = "solar-pro4"
_P = str.maketrans({"ㆍ": "·", "․": "·", "‧": "·", "∙": "·", "“": '"', "”": '"', "‘": "'", "’": "'"})
n = lambda s: re.sub(r"\s+", "", (s or "").translate(_P))  # noqa: E731

ADJ = """너는 내부규정 개정의 영향을 검토하는 담당자다. 법적 판단은 하지 않는다.
아래 개정 때문에 [후보 조]를 담당자가 함께 살펴보거나 고쳐야 하는지 판정하라.

살펴볼 조 (아래 관계 중 하나가 원문에서 확인될 때만)
- 인용: 수정되는(삭제·이동되는) 조·항을 번호나 제목으로 가리킨다
- 준용·위임: 수정되는 규정·조를 준용하거나 사항을 맡기거나 맡는다. 규정 이름 없이 「회사의 ○○ 관련 규정」처럼 가리켜도, 대상이 수정된 내용에 닿으면 포함
- 같은 사항: 수정된 것과 같은 사항(값·기간·대상·주체·명칭·절차)을 따로 정해 수정 후 어긋나거나 함께 맞춰야 할 수 있다. 다른 낱말로 쓰여 있어도 포함
- 정의 용어: 정의가 바뀌는 용어를 쓰고 바뀐 부분이 이 조의 뜻에 영향을 준다
- 전제: 삭제·변경되는 절차·직무·서식·조직·용어를 전제로 동작한다. 인용 문구가 없어도 포함
- 2단계: 위 관계로 영향받는 조를 다시 인용·준용해서 수정 내용이 이어진다
살펴보지 않을 조: 주제만 비슷하고 위 관계가 확인되지 않는 조, 수정된 조 자체

[개정]
{change}

[후보 조] {name}
{text}

JSON 한 줄만 출력한다: {{"살펴볼조": true 또는 false, "관계": "인용|준용·위임|같은 사항|정의 용어|전제|2단계|", "근거문장": "후보 조 원문에서 글자 그대로 옮긴 부분(없으면 빈 문자열)", "이유": "한 문장"}}"""

FAITH = """아래 [입력]만 보고, [답변]에 담긴 사실 주장이 [입력]으로 뒷받침되는지 판정하라. 상식이나 외부 지식으로 판단하지 않는다.
1) [답변]에서 사실 주장만 뽑아 하나씩 짧은 문장으로 쓴다. 「~를 확인해 볼 만하다」 「~를 함께 살펴볼 필요가 있다」 같은 권고 부분은 떼어 내고, 그 안의 사실 내용(무엇이 바뀌는지, 후보 조가 무엇을 정하는지, 둘이 어떻게 이어지는지)만 주장으로 남긴다.
2) 각 주장이 [입력]에 같은 내용으로 있거나(말을 바꿔 써도 됨) 입력에서 바로 따라 나오면 true, 입력에 없는 내용(새 사실 · 새 숫자 · 다른 조 번호 · 입력과 다른 관계)이 들어 있으면 false.

예) 입력: 개정 후 "연 4회", 후보 구절 "매 반기 말일까지 제출" / 답변: "보고가 연 4회로 바뀌어 반기별 제출 시기와 함께 확인해 볼 만하다."
→ 주장: ["보고 횟수가 연 4회로 바뀐다"(true), "후보 조는 반기별로 제출하게 한다"(true)]

[입력]
{context}

[답변]
{answer}

JSON 한 줄만 출력한다: {{"주장": [{{"문장": "...", "뒷받침": true 또는 false}}]}}"""


FAITH2 = """아래 [원문]만 보고, [답변]의 사실 주장이 [원문]으로 뒷받침되는지 판정하라. 상식이나 외부 지식으로 판단하지 않는다.
1) [답변]을 짧은 주장으로 나눈다. 주장마다 종류를 붙인다.
   - 사실: 무엇이 바뀌는지, 후보 조가 무엇을 정하거나 쓰는지, 둘이 어떻게 이어지는지 같은 내용
   - 권고: 「~를 확인해 볼 만하다」 「함께 살펴볼 필요가 있다」 같은 제안 부분
   한 문장에 둘이 섞이면 나눈다. 예) "제5조가 숙박비를 공제 대상으로 정해 함께 확인해 볼 만하다" → 사실 "제5조는 숙박비를 공제 대상으로 정한다" + 권고 "함께 확인해 볼 만하다"
2) 사실 주장만 판정한다. [원문]에 같은 내용이 있거나(말을 바꿔 써도 됨) 원문에서 바로 따라 나오면 true, 원문에 없는 내용(새 사실 · 새 숫자 · 다른 조 번호 · 원문과 다른 관계)이 들어 있으면 false. 권고의 뒷받침은 null.

[원문]
{context}

[답변]
{answer}

JSON 한 줄만 출력한다: {{"주장": [{{"문장": "...", "종류": "사실" 또는 "권고", "뒷받침": true 또는 false 또는 null}}]}}"""


FAITH2B = """아래 [권고 문장]들은 「~를 확인해 볼 만하다」 같은 제안이지만, 그 안에 사실 전제(조 번호, 후보 조가 무엇을 정하는지, 무엇이 바뀌는지, 둘의 관계)가 들어 있을 수 있다.
각 문장에서 사실 전제만 뽑아 짧은 주장으로 쓰고, [원문]으로 뒷받침되는지 판정하라. 사실 전제가 없는 순수 제안이면 아무것도 내지 않는다.
판정 기준: [원문]에 같은 내용이 있거나(말을 바꿔 써도 됨) 원문에서 바로 따라 나오면 true, 원문에 없는 내용이 들어 있으면 false. 상식이나 외부 지식으로 판단하지 않는다.

[원문]
{context}

[권고 문장]
{recs}

JSON 한 줄만 출력한다: {{"주장": [{{"문장": "...", "뒷받침": true 또는 false}}]}}"""


def jl(p):
    return [json.loads(l) for l in Path(p).read_text(encoding="utf-8").splitlines() if l.strip()]


def call(prompt):
    from regcheck.nl import api_chat
    for _ in range(3):
        out = api_chat(MODEL, [{"role": "user", "content": prompt}], timeout=120, max_tokens=1500)
        m = re.search(r"\{.*\}", out, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    return None


def context():
    import glob
    from regcheck.impact_view import load_context
    regs = {r["reg_id"]: r for r in (json.loads(Path(p).read_text(encoding="utf-8")) for p in glob.glob(str(ROOT / "out/parsed/[0-9]*.json")))}
    return load_context(ROOT, regs)


def run_jobs(jobs, fn, out):
    out = Path(out)
    done = {json.loads(l)["key"] for l in out.read_text(encoding="utf-8").splitlines() if l.strip()} if out.exists() else set()
    todo = [j for j in jobs if j["key"] not in done]
    print("할 판정", len(todo), "/", len(jobs), flush=True)
    lock = threading.Lock()

    def one(j):
        rec = {**j, **fn(j)}
        with lock, out.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    with ThreadPoolExecutor(6) as ex:
        list(ex.map(one, todo))


def gold(d):
    return {f"{d}:{r['id']}": {e["id"] for e in r["영향"] if e.get("id")} for r in jl(ROOT / f"eval/relabel_v2/{d}/gold.jsonl")}


def adjudicate(d, run, out):
    ctx = context(); G = gold(d)
    jobs = []
    for r in jl(run):
        for s in r["rule"] + r["other"]:
            nd = ctx.nodes.get(s["id"], {})
            name = f"{ctx.nodes.get(nd.get('reg', ''), {}).get('name', '')} {nd.get('label', s['id'])}({nd.get('title', '')})"
            jobs.append({"key": f"{r['q']}|{s['id']}", "q": r["q"], "id": s["id"], "gold": s["id"] in G[r["q"]],
                         "change": r["change_text"][:2000], "name": name, "text": ctx.unit_text.get(s["id"], "")[:1800]})

    def fn(j):
        o = call(ADJ.format(change=j["change"], name=j["name"], text=j["text"])) or {}
        quote_ok = bool(o.get("근거문장")) and len(n(o.get("근거문장"))) >= 4 and n(o.get("근거문장")) in n(j["text"])
        return {"judge": o, "yes": bool(o.get("살펴볼조")) and quote_ok, "quote_ok": quote_ok}
    run_jobs(jobs, fn, out)


def faith(d, run, out):
    ctx = context()
    from regcheck.answer_writer import prompt_for
    jobs = []
    for r in jl(run):
        for s in r["rule"] + r["other"]:
            a = (s.get("answer") or {}).get("text") or ""
            if not a:
                continue
            st = SimpleNamespace(id=s["id"], paths=s["paths"], evidence=s.get("evidence") or {})
            p = prompt_for(st, "", r["change_text"], ctx.nodes)
            body = p.split("\n원인(")[0]   # 답변 모델이 본 입력 칸들만 (뒤의 작성 지시 · 규칙은 뺀다)
            body = body[body.find("[개정]"):] if "[개정]" in body else body   # 맨 앞 작성 지시 줄도 뺀다
            jobs.append({"key": f"{r['q']}|{s['id']}", "q": r["q"], "id": s["id"], "route": s["route"], "context": body, "answer": a})

    def fn(j):
        o = call(FAITH.format(context=j["context"], answer=j["answer"])) or {}
        cl = [c for c in o.get("주장") or [] if isinstance(c, dict)]
        return {"claims": cl, "ok": bool(o)}
    run_jobs(jobs, fn, out)


def report(d, run, adj_f, faith_f):
    R = {r["q"]: r for r in jl(run)}; G = gold(d)
    A = {x["key"]: x for x in jl(adj_f)}
    shown = sum(len(r["rule"]) + len(r["other"]) for r in R.values())
    g_yes = [A[k]["yes"] for k in A if A[k]["gold"]]
    ng = [A[k] for k in A if not A[k]["gold"]]
    ng_yes = sum(x["yes"] for x in ng)
    gold_shown = len(g_yes)
    print(f"## {d} 판정 보완 (판정 {len(A)}/{shown})")
    print(f"- 판정 모델이 라벨 정답에 「예」: {sum(g_yes)}/{gold_shown} ({sum(g_yes)/max(gold_shown,1):.3f})  ← 판정 모델의 라벨 재현")
    print(f"- 라벨에 없는 후보 중 판정 모델 「예」: {ng_yes}/{len(ng)} ({ng_yes/max(len(ng),1):.3f})")
    print(f"- 정밀도: 라벨 기준 {gold_shown/shown:.3f} → 판정 보완(라벨 정답 + 판정 「예」) {(gold_shown+ng_yes)/shown:.3f}")
    # 정밀도 두 가지 관련도: ① 라벨 정답만 ② 라벨 정답 + 판정 「예」(판정 보완)
    def rel(q, i, pooled):
        return i in G[q] or (pooled and A.get(f"{q}|{i}", {}).get("yes", False))
    for pooled, nm_ in ((False, "① 라벨만"), (True, "② 라벨 + 판정 「예」")):
        p5 = cp = 0.0; nq = 0; hit = tot = 0
        for q, r in R.items():
            L = [s["id"] for s in r["rule"] + r["other"]]
            if not L:
                continue
            v = [1 if rel(q, i, pooled) else 0 for i in L]
            nq += 1; hit += sum(v); tot += len(v)
            p5 += sum(v[:5]) / min(5, len(v))
            # RAGAS Context Precision: Σ_k (Precision@k × v_k) / (관련 있는 후보 수), 관련 없으면 0
            rs = sum(v)
            cp += sum((sum(v[:k + 1]) / (k + 1)) * v[k] for k in range(len(v))) / rs if rs else 0.0
        print(f"- 정밀도 {nm_}: 목록 전체 {hit/max(tot,1):.3f}, Precision@5 {p5/max(nq,1):.3f}, RAGAS Context Precision 식 {cp/max(nq,1):.3f} (목록이 빈 문항 제외 {nq}문항)")
    zero = [q for q in R if not G[q]]
    print(f"- 정답 없는 문항 {len(zero)}개: 빈 목록 {sum(1 for q in zero if not (R[q]['rule'] or R[q]['other']))}개, 문항당 보인 후보 {sum(len(R[q]['rule'])+len(R[q]['other']) for q in zero)/max(len(zero),1):.1f}, 그중 판정 「예」 {sum(A[k]['yes'] for k in A if A[k]['q'] in zero)}개")
    F = jl(faith_f)
    by = {}
    rec = re.compile(r"(볼\s*만하다|필요가\s*있다|필요하다|살펴볼|확인해\s*볼|확인할\s*필요|검토할\s*필요)[^가-힣]*$")
    dropped = 0
    for x in F:
        cl0 = x.get("claims") or []
        cl = [c for c in cl0 if not rec.search((c.get("문장") or "").strip())]   # 권고 문장은 사실 주장이 아니다 (판정 모델이 남긴 경우)
        dropped += len(cl0) - len(cl)
        if not cl:
            continue
        b = by.setdefault(x["route"], [0, 0, 0, 0])
        sup = sum(1 for c in cl if c.get("뒷받침") is True)
        b[0] += sup; b[1] += len(cl); b[2] += 1; b[3] += sup < len(cl)
    print(f"## {d} 답변 Faithfulness (RAGAS 방식, 판정 {MODEL}; 권고 문장 {dropped}개는 주장에서 뺌)")
    for k, (s_, t, na, bad) in by.items():
        print(f"- {'규칙' if k == 'decide' else 'luna'} 후보 답변 {na}개: Faithfulness {s_/max(t,1):.3f} (주장 {t}), Hallucination rate {bad/max(na,1):.3f}")
    s_ = sum(b[0] for b in by.values()); t = sum(b[1] for b in by.values()); na = sum(b[2] for b in by.values()); bad = sum(b[3] for b in by.values())
    print(f"- 전체 {na}개: Faithfulness {s_/max(t,1):.3f}, Hallucination rate {bad/max(na,1):.3f}")


def faith2(d, luna_run, rule_run, out):
    """luna 답변은 luna_run의 other, 규칙 답변은 rule_run의 rule(근거 보강 후 다시 쓴 것)을 판정한다."""
    import glob
    from regcheck.change_query import analyze
    from regcheck.final_view import cand_window
    from regcheck.luna_rerank import change_block
    ctx = context()
    regs = {r["reg_id"]: r for r in (json.loads(Path(p).read_text(encoding="utf-8")) for p in glob.glob(str(ROOT / "out/parsed/[0-9]*.json")))}
    by = {r["name"]: k for k, r in regs.items()}
    ch = {m["id"]: m for m in jl(ROOT / f"eval/relabel_v2/{d}/changes.jsonl")}
    change = {}
    for q in {r["q"] for r in jl(luna_run)}:
        s0 = ch[q.split(":")[1]]["수정"]; reg = regs[by[s0["규정"]]]
        cq = analyze(reg, s0["개정문"], q.split(":")[1])
        change[q] = change_block(reg, s0["개정문"], cq.starts[0] if cq.starts else None)   # 지금 코드로(번호 이동 설명 버그 수정 반영)
    jobs = []
    for route, f, key in (("luna", luna_run, "other"), ("decide", rule_run, "rule")):
        for r in jl(f):
            for st in r[key]:
                a = (st.get("answer") or {}).get("text") or ""
                if not a:
                    continue
                nd = ctx.nodes.get(st["id"], {})
                name = f"{ctx.nodes.get(nd.get('reg', ''), {}).get('name', '')} {nd.get('label', st['id'])}({nd.get('title', '')})"
                quote = (st.get("evidence") or {}).get("quote") or ""
                text = cand_window(ctx.unit_text.get(st["id"], ""), [quote] if quote else [], n=1500)
                jobs.append({"key": f"{r['q']}|{st['id']}|{route}", "q": r["q"], "id": st["id"], "route": route, "answer": a,
                             "context": f"[개정]\n{change[r['q']]}\n\n[확인 대상 조] {name}\n{text}"})

    def fn(j):
        o = call(FAITH2.format(context=j["context"], answer=j["answer"])) or {}
        return {"claims": [c for c in o.get("주장") or [] if isinstance(c, dict)], "ok": bool(o)}
    run_jobs(jobs, fn, out)


def report2(f, fb=None):
    F = jl(f)
    if fb:   # 권고 문장 속 사실 전제를 사실 주장으로 더한다
        B = {x["key"]: x for x in jl(fb)}
        for x in F:
            x["claims"] = x["claims"] + [{**c, "종류": "사실"} for c in B.get(x["key"], {}).get("claims", [])]
    for route in ("luna", "decide", None):
        xs = [x for x in F if route is None or x["route"] == route]
        facts = [[c for c in x["claims"] if c.get("종류") == "사실"] for x in xs]
        with_fact = [fs for fs in facts if fs]
        sup = sum(c.get("뒷받침") is True for fs in facts for c in fs); tot = sum(len(fs) for fs in facts)
        bad = sum(any(c.get("뒷받침") is not True for c in fs) for fs in with_fact)
        name = {"luna": "luna 답변", "decide": "규칙 답변", None: "전체"}[route]
        print(f"- {name} {len(xs)}개 (판정 실패 {sum(not x['ok'] for x in xs)}, 사실 주장 없는 답변 {len(xs) - len(with_fact)}): "
              f"Faithfulness {sup / max(tot, 1):.3f} (사실 주장 {tot}), Hallucination rate {bad / max(len(with_fact), 1):.3f}")


def faith2b(f, out):
    jobs = [{"key": x["key"], "route": x["route"], "context": x["context"],
             "recs": [c["문장"] for c in x["claims"] if c.get("종류") == "권고" and c.get("문장")]} for x in jl(f)]
    jobs = [j for j in jobs if j["recs"]]

    def fn(j):
        o = call(FAITH2B.format(context=j["context"], recs="\n".join(f"- {r}" for r in j["recs"]))) or {}
        return {"claims": [c for c in o.get("주장") or [] if isinstance(c, dict)], "ok": bool(o)}
    run_jobs(jobs, fn, out)


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "adjudicate":
        adjudicate(*sys.argv[2:5])
    elif cmd == "faith":
        faith(*sys.argv[2:5])
    elif cmd == "report":
        report(*sys.argv[2:6])
    elif cmd == "faith2":
        faith2(*sys.argv[2:6])
    elif cmd == "report2":
        report2(*sys.argv[2:4])
    elif cmd == "faith2b":
        faith2b(*sys.argv[2:4])
    else:
        sys.exit(__doc__)
