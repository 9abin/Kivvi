"""답변 방식 비교(소규모): 같은 검색 근거를 고정하고 A / A′ / B / C로 답변해 채점한다.

사용(프로젝트 루트에서, PG_*로 색인 DB를 고른다):
  python scripts/exp_answer_struct.py --questions eval/answer_eval_100.jsonl

문항 선정(실행 전에 정한 규칙, 아래 규칙을 바꾸지 않는다): 이전 실행(k8grp_luna_100)에서
  부분 판정이면서 정답 근거를 전부 확보한 문항 중 id 순으로 16개 + 정답 판정 id 순 4개 + 오답·답못함 판정 id 순 4개.
  부분 중심으로 골랐으므로 개선 가능성을 찾는 개발용 표본이다. 여기서 오른 비율을 전체 성능으로 해석하지 않는다.
검색: 문항마다 한 번만 한다. BGE 순서, 최종 k=10, Luna 선택 없음, 기존 8,000자 근거 예산. 근거 본문 전체를 eval/answer_runs/fixed_evidence_24.json에 저장하고
  네 조건이 모두 이 파일을 쓴다(근거의 내용·순서·번호가 같다).
조건
  A   현재 답변 생성 방식(생성 단계만): 질문 + 근거 → 답변. 숫자 가드, 응답 모드 결정, 근거 문장 검사는 어느 조건에도 없어 과거 전체 파이프라인의 정답 수·시간과 직접 비교하지 않는다
  A′  A + 답변 지침(조건·예외 포함, 판단에 필요한 사실이 없으면 확정하지 않고 무엇이 부족한지 설명, 근거에 없는 기준을 만들지 않음)
  B   체크리스트(사실, 규칙, 조건·예외, 확인 필요 사실) 추출 → A′ 지침으로 답변
  C   근거 그래프(B와 같은 항목 + 규칙·조건 연결 + 조건별 적용 상태) 추출 → 코드 검증 → A′ 지침으로 답변
답변 모델, 온도, 출력 형식(SYSTEM_PROMPT), 결론 모순 재작성은 모두 같다. 근거 문장 검사·충돌 확인 호출은 어느 조건에서도 하지 않는다.
평가 정답 요지·금지 내용·정답 근거는 추출·답변 입력에 쓰지 않는다. 채점은 기존 judge_answers(Luna)를 쓴다.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")
from dotenv import load_dotenv  # noqa: E402

load_dotenv()
os.environ["LANGCHAIN_TRACING_V2"] = "false"

from src.answer.generate import (SYSTEM_PROMPT, Generator, assemble_sections, flatten, format_context,  # noqa: E402
                                 has_bullets, order_entries)
from src.answer.guard import filter_tables_by_scope  # noqa: E402
from src.search.glossary import filter_by_glossary  # noqa: E402

BASE_TAG = "k8grp_luna_100"
K = 10

GUIDE = """[답변 지침]
1. 규칙의 적용 조건과 예외가 근거에 있으면 빠뜨리지 말고 답에 포함한다.
2. 질문에 적힌 사실만으로 조건 충족 여부를 판단할 수 있는지 구분한다. 결론을 바꾸는 사실이 질문에 없으면 확정하지 말고, 근거가 정한 내용까지 답한 뒤 무엇이 확인되지 않아 확정할 수 없는지 한 문장으로 쓴다.
3. 근거에 없는 기준, 의무, 절차를 만들지 않는다. 확인 안내는 적용 판단에 필요한 정보가 없을 때만 권고로 쓴다.
4. 질문에 없는 사실을 가정하지 않는다."""

CHECK_PROMPT = """직원 질문과 번호가 붙은 근거가 주어진다. 답변을 쓰기 전에 아래 항목만 정리한다. 답을 쓰지 않는다.
출력은 JSON 하나만 쓴다.
{"질문의_사실": ["질문에 직접 적힌 상황만"],
 "규칙": [{"내용": "근거가 정한 허용, 금지, 지급, 처리 기준", "근거": [번호], "인용": "근거 본문에서 그대로 옮긴 문장"}],
 "조건_예외": [{"종류": "조건 또는 예외", "내용": "...", "근거": [번호], "인용": "근거 본문에서 그대로 옮긴 문장"}],
 "확인_필요_사실": ["결론을 바꾸지만 질문에 없는 사실"]}
- 근거에 없는 내용을 만들지 않는다. 질문의 사실과 규정의 내용을 섞지 않는다."""

GRAPH_PROMPT = """직원 질문과 번호가 붙은 근거가 주어진다. 답변을 쓰기 전에 사실, 규칙, 조건·예외와 그 연결을 정리한다. 답을 쓰지 않는다.
출력은 JSON 하나만 쓴다.
{"질문의_사실": [{"id": "F1", "내용": "질문에 직접 적힌 상황만"}],
 "규칙": [{"id": "R1", "내용": "근거가 정한 허용, 금지, 지급, 처리 기준", "근거": [번호], "인용": "근거 본문에서 그대로 옮긴 문장"}],
 "조건_예외": [{"id": "C1", "종류": "조건 또는 예외", "내용": "...", "근거": [번호], "인용": "근거 본문에서 그대로 옮긴 문장", "대상규칙": "R1"}],
 "적용_상태": [{"조건": "C1", "상태": "충족, 불충족, 미확인 중 하나", "사실": ["F1"], "이유": "질문의 어떤 사실로 판단했는지, 없으면 무엇이 없는지"}],
 "답변_항목": [{"유형": "확정, 조건부, 확인불가 중 하나", "내용": "...", "규칙": ["R1"], "조건": ["C1"]}]}
- 근거에 없는 내용을 만들지 않는다. 질문의 사실과 규정의 내용을 섞지 않는다.
- 질문의 사실만으로 조건을 판단할 수 없으면 상태는 반드시 미확인이다. 미확인을 충족으로 쓰지 않는다."""


def esc(prompt):
    """Generator._ask는 system 문장을 LangChain 템플릿으로 읽으므로 JSON 예시의 중괄호를 이스케이프한다."""
    return prompt.replace("{", "{{").replace("}", "}}")


def norm(x):
    return re.sub(r"\s+", "", x or "")


def parse_json(text):
    m = re.search(r"\{.*\}", text, re.S)
    try:
        return json.loads(m.group(0)) if m else None
    except Exception:  # noqa: BLE001
        return None


def validate_graph(g, entries):
    """코드로 확인할 수 있는 것만 확인한다: id와 연결이 존재하는가, 근거 번호가 유효한가, 인용 문장이 원문에 있는가, 미확인을 충족으로 쓰지 않았는가."""
    issues = []
    ids = {x.get("id") for k in ("질문의_사실", "규칙", "조건_예외") for x in g.get(k, []) if isinstance(x, dict)}
    bodies = [norm(e["body"]) for e in entries]
    for k in ("규칙", "조건_예외"):
        for j, x in enumerate(g.get(k, [])):
            name = x.get("id") or f"{k}{j + 1}"
            cited = x.get("근거", [])
            valid = [n for n in cited if isinstance(n, int) and 1 <= n <= len(entries)]
            if not cited or len(valid) != len(cited):
                issues.append(f"{name} 근거 번호 이상: {cited}")
            q = norm(x.get("인용"))
            if not q:
                issues.append(f"{name} 인용 문장 없음")
            elif not any(q in bodies[n - 1] for n in valid):
                issues.append(f"{name} 인용 문장 전체가 지정한 근거 본문에 없음")
    for x in g.get("조건_예외", []):
        if "id" in x and x.get("대상규칙") not in ids:
            issues.append(f"{x.get('id')} 대상규칙 없음")
    for x in g.get("적용_상태", []):
        if x.get("조건") not in ids:
            issues.append(f"적용_상태의 조건 {x.get('조건')} 없음")
        if x.get("상태") not in ("충족", "불충족", "미확인"):
            issues.append(f"적용_상태 값 {x.get('상태')} 이상")
        if x.get("상태") == "충족" and not x.get("사실"):
            issues.append(f"{x.get('조건')} 충족인데 근거가 되는 사실이 없음")
    return issues


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", default="eval/answer_eval_100.jsonl")
    ap.add_argument("--only", default="", help="이 조건만 (쉼표 구분: A,Ap,B,C)")
    ap.add_argument("--base-tag", default=BASE_TAG, help="문항 선정에 쓰는 이전 실행(판정 파일이 있는 태그)")
    ap.add_argument("--all", action="store_true", help="선정 규칙 대신 평가셋 전체 문항을 쓴다(A와 A′ 전체 비교용)")
    ap.add_argument("--suffix", default="", help="결과 폴더와 고정 근거 파일 이름 뒤에 붙일 말(평가셋별 구분)")
    ap.add_argument("--limit", type=int, default=0, help="선정 문항 앞에서 N개만 (구현 점검용)")
    args = ap.parse_args()

    gold = {g["id"]: g for g in (json.loads(l) for l in open(args.questions, encoding="utf-8") if l.strip())}
    base = lambda f: {json.loads(l)["id"]: json.loads(l) for l in open(f"eval/answer_runs/{args.base_tag}/{f}", encoding="utf-8") if l.strip()}  # noqa: E731
    runs, judged = base("runs.jsonl"), base("judged.jsonl")

    def art_base(a):
        m = re.search(r"제\s*\d+\s*조(?:\s*의\s*\d+)?|별표\s*\d+(?:의\s*\d+)?", a or "")
        return re.sub(r"\s+", "", m.group(0)) if m else re.sub(r"\s+", "", a or "")

    def covered(i):
        used = {(e["문서명"], art_base(e["article"])) for e in runs[i]["entries"]}
        want = [(e["규정"], art_base(e["조항"])) for e in gold[i]["근거"] if "부칙" not in e["조항"] and e.get("tier", "필수") == "필수"]
        return all(w in used for w in want)

    ids = sorted(gold)
    pick = ([i for i in ids if judged[i]["판정"] == "부분" and covered(i)][:16]
            + [i for i in ids if judged[i]["판정"] == "정답"][:4]
            + [i for i in ids if judged[i]["판정"] in ("오답", "답못함")][:4])
    if args.all:
        pick = ids
    if args.limit:
        pick = pick[:args.limit]
    print("선정 문항:", ", ".join(pick), flush=True)

    # --- 검색 근거 고정 ---
    fixed_path = os.path.join("eval", "answer_runs", f"fixed_evidence_24{args.suffix}.json")
    if os.path.exists(fixed_path) and not args.limit:
        fixed = json.load(open(fixed_path, encoding="utf-8"))
    else:
        from src.answer.pipeline import Assistant
        asst = Assistant(parallel=False)
        fixed = {}
        for i in pick:
            q = gold[i]["q"]
            results = asst._retrieve(q, K, None, {})
            entries = order_entries(flatten(asst.searcher.assemble(results)))
            entries, _ = filter_tables_by_scope(q, entries)
            entries, _ = filter_by_glossary(q, entries, asst.searcher.glossary)
            scored = [r["rerank"] for r in results if r.get("rerank") is not None]
            ctx = format_context(entries)
            fixed[i] = {"q": q, "entries": entries, "top_score": max(scored) if scored else None,
                        "설정": {"k": K, "luna_select": False, "rerank": "BGE", "budget": 8000},
                        "해시": hashlib.sha256(ctx.encode("utf-8")).hexdigest()[:16], "글자수": len(ctx)}
        asst.close()
        if not args.limit:
            json.dump(fixed, open(fixed_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"고정 근거 {len(fixed)}문항, 평균 {sum(v['글자수'] for v in fixed.values()) / len(fixed):.0f}자", flush=True)

    gen = Generator()

    def answer_with(question, entries, extra):
        user = f"질문: {question}\n\n근거:\n{format_context(entries)}" + (f"\n\n{extra}" if extra else "")
        text = assemble_sections(gen._ask(SYSTEM_PROMPT, user))
        if has_bullets(text) and gen.contradicts(question, text):
            text = assemble_sections(gen._ask(SYSTEM_PROMPT, user + "\n\n[주의] 직전 답변은 결론이 세부 불릿과 모순되었다. 근거 본문과 세부 불릿을 기준으로 "
                                              "결론을 다시 써서 같은 형식으로 답한다.\n직전 답변:\n" + text))
        return text

    def run_one(cond, i):
        q, entries = fixed[i]["q"], fixed[i]["entries"]
        ctx = f"질문: {q}\n\n근거:\n{format_context(entries)}"
        rec = {"id": i, "mode": "answer", "withheld": None, "warnings": [], "glossary_excluded": [], "plan": None,
               "entries": [{"문서명": e["문서명"], "article": e["article"], "상태": e["조문상태"]} for e in entries],
               "top_score": fixed[i]["top_score"], "해시": fixed[i]["해시"], "fallback": False, "issues": []}
        t0 = time.time()
        extra = None
        if cond in ("A", "A2"):  # A2는 A를 한 번 더 생성한 것(생성 흔들림 측정용)
            extra = ""
        elif cond == "Ap":
            extra = GUIDE
        else:
            try:
                raw = gen._ask(esc(CHECK_PROMPT if cond == "B" else GRAPH_PROMPT), ctx)
                rec["struct_raw"] = raw
                g = parse_json(raw)
                if not isinstance(g, dict):
                    raise ValueError("JSON을 해석하지 못함")
                rec["struct"] = g
                rec["issues"] = validate_graph(g, entries)
                if rec["issues"]:
                    raise ValueError("검증 실패: " + "; ".join(rec["issues"][:3]))
                label = "체크리스트" if cond == "B" else "근거 그래프"
                extra = f"[질문별 {label}]\n{json.dumps(g, ensure_ascii=False, indent=1)}\n\n{GUIDE}\n위 {label}는 근거를 읽은 정리일 뿐이다. 근거 원문과 다르면 원문을 따른다."
            except Exception as e:  # noqa: BLE001
                rec["fallback"] = True
                rec["fallback_reason"] = repr(e)[:160]
                extra = GUIDE
        rec["sec_struct"] = round(time.time() - t0, 1)
        t1 = time.time()
        rec["answer"] = answer_with(q, entries, extra)
        rec["sec_answer"] = round(time.time() - t1, 1)
        rec["sec"] = round(time.time() - t0, 1)
        return rec

    conds = [c for c in ("A", "A2", "Ap", "B", "C") if not args.only or c in args.only.split(",")]
    tags = {}
    for cond in conds:
        tag = f"fixed_{cond}{args.suffix}" + ("_pilot" if args.limit else "")
        outdir = os.path.join("eval", "answer_runs", tag)
        os.makedirs(outdir, exist_ok=True)
        runs_path = os.path.join(outdir, "runs.jsonl")
        done = [json.loads(l) for l in open(runs_path, encoding="utf-8") if l.strip()] if os.path.exists(runs_path) else []
        if [r["id"] for r in done] == pick:  # 이전 실행이 끝까지 만든 조건은 다시 만들지 않는다(중단 후 이어서 실행)
            recs = done
            print(f"[{cond}] 이전 실행 결과를 재사용", flush=True)
        else:
            with ThreadPoolExecutor(4) as ex:
                recs = list(ex.map(lambda i: run_one(cond, i), pick))
            with open(runs_path, "w", encoding="utf-8") as f:
                for r in recs:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
        tags[cond] = (tag, recs)
        fb = sum(1 for r in recs if r["fallback"])
        print(f"[{cond}] 생성 완료 | 구조 단계 평균 {sum(r['sec_struct'] for r in recs) / len(recs):.1f}초, 답변 평균 {sum(r['sec_answer'] for r in recs) / len(recs):.1f}초, "
              f"전체 평균 {sum(r['sec'] for r in recs) / len(recs):.1f}초 | A′로 대체(구조 생성·해석·검증 실패) {fb}/{len(recs)}", flush=True)

    from src.eval import judge_answers
    judge_answers.main([t for t, _ in tags.values()], args.questions)
    res = {}
    for cond, (tag, _) in tags.items():
        res[cond] = {json.loads(l)["id"]: json.loads(l) for l in open(f"eval/answer_runs/{tag}/judged.jsonl", encoding="utf-8")}
    print("\n문항별 판정 (이전 k8 실행 → A → A′ → B → C)")
    for i in pick:
        print(f"  {i} {judged[i]['판정']} → " + " → ".join(res[c][i]["판정"] if c in res else "-" for c in ("A", "A2", "Ap", "B", "C")))
    for c in res:
        if c == "A" or "A" not in res:
            continue
        up = sum(1 for i in pick if res["A"][i]["판정"] == "부분" and res[c][i]["판정"] == "정답")
        down = sum(1 for i in pick if res["A"][i]["판정"] == "정답" and res[c][i]["판정"] != "정답")
        wrong = sum(1 for i in pick if res["A"][i]["판정"] != "오답" and res[c][i]["판정"] == "오답")
        print(f"A 대비 {c}: 부분→정답 {up}, 정답→악화 {down}, 새 오답 {wrong}")


if __name__ == "__main__":
    main()
