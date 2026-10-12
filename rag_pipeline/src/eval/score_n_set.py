"""N 평가셋(80문항, 끔/켬 160회) 1차 판정·집계. scoring_rules_v5_1.md를 그대로 따른다.

1차 판정은 LLM이 한다(이 스크립트). 사람 확인은 오답·부분·보류 전수, 끔/켬 등급
불일치 문항 전수, 정답의 무작위 20%에 대해 별도로 하고, 그 결과가 최종 라벨이다
(scoring_rules_v5_1.md 7절). 이 스크립트는 그 사람 확인용 파일(끔/켬 조건을 가린
것)과, 사람이 확인한 뒤 병합하는 도구까지 만든다.

판정 단위는 gold의 `필수_하위질문`이다(각 하위질문: 충족/부분/미응답/오류, 규칙 1절).
문항 등급은 규칙 2절 순서로 코드가 합친다(LLM이 등급을 직접 매기지 않는다):
  1. 오답   : 하위질문 중 하나라도 '오류'이거나, 답변 전체에 금지내용 위반·전역 오류가 있다
  2. 보류   : 오답이 아니고, 모든 하위질문이 '미응답'이다
  3. 정답   : 오답·보류가 아니고, 모든 하위질문이 '충족'이다
  4. 부분   : 그 외 (적어도 하나는 충족/부분, 오류 없음)
  5. 실행오류: run 기록에 error가 있으면 LLM 판정 자체를 하지 않는다 (규칙 5절)

근거 확보율은 LLM이 아니라 코드로 계산한다(규칙 4절 마지막 줄: "검색 결과가 아니라
entries 기준"): gold `핵심근거`의 (규정, 조항)과 실행 기록 `entries`의 (문서명, article)을
정확히 맞춰서 센다. 다른 조문 경로로 같은 내용을 지지하는 경우는 사람 확인 때 반영한다
(코드로는 과대추정하지 않는다).

실행 (rag_pipeline에서):
  uv run python -m src.eval.score_n_set judge  --rundir eval/n_set/runs/<폴더> [--dry-run] [--limit N]
  uv run python -m src.eval.score_n_set review --rundir eval/n_set/runs/<폴더>   # 사람 검토 파일(끔/켬 가림) 생성
  uv run python -m src.eval.score_n_set merge  --rundir eval/n_set/runs/<폴더>   # 사람 확인 결과 병합 + 최종 집계
"""
import argparse
import hashlib
import json
import math
import os
import random
import sys

from dotenv import load_dotenv

from config import BASE_DIR, OPENAI_MODEL
from langchain_openai import ChatOpenAI

load_dotenv()
sys.stdout.reconfigure(encoding="utf-8")

GOLD_PATH = os.path.join(BASE_DIR, "eval", "n_set", "n_gold_80_v5_1.jsonl")
RULES_PATH = os.path.join(BASE_DIR, "eval", "n_set", "scoring_rules_v5_1.md")

SUBQ_LABELS = ("충족", "부분", "미응답", "오류")

JUDGE_SYSTEM_TEMPLATE = """너는 사내 규정 질의응답 시스템의 답변을 '채점 규칙'대로 판정하는 평가자다.
아래 채점 규칙을 그대로 따른다. 규칙에 없는 기준을 새로 만들지 않는다.

[채점 규칙 전문]
{rules}

[네가 할 일]
- `필수_하위질문` 각각에 대해 "충족" / "부분" / "미응답" / "오류" 중 하나를 고른다.
  - "오류": 그 하위질문에 대한 답에 금지내용 위반, 잘못된 결론·조건·수치·단위, 대상 치환,
    결론과 본문의 모순 중 하나라도 있다.
  - "확인되지 않음"이 정답인 하위질문에서, 답변이 (제도가 없다고 단정하지 않고) 확인되지
    않는다고 말했으면 "충족"이다. "없다/금지된다"로 확대했으면 "오류"다.
  - 답변이 그 하위질문과 관련된 내용을 전혀 안 다뤘으면 "미응답"이다.
  - `부분답_예시`/`보류_예시`가 gold에 있으면 그 경계 판단의 기준으로 참고한다.
- 하위질문 하나에 매이지 않는, 답변 전반(보충 내용 포함)의 오류를 "전역_오류"에 따로 적는다
  (예: 핵심은 맞았는데 뒤에 틀린 보충을 붙인 경우).
- `금지내용` 목록 각각이 답변에서 위반됐는지 확인해 "금지내용_위반"에 적는다. 위반이 없으면 빈 배열.
- `적용배제근거`에 있는 조문의 내용을 이 사안에 적용해서 결론을 냈으면 전역_오류에 적는다.
- 채점 대상은 "표시된 답"(system_answer)이다. withheld(보류 전 원답)는 참고만 하고 채점하지 않는다.

[출력]
JSON 객체 하나만 쓴다. 다른 텍스트는 쓰지 않는다. 형식:
{{"하위질문_판정": {{"<하위질문 원문>": {{"판정": "충족|부분|미응답|오류", "이유": "한두 문장"}}}},
  "전역_오류": [{{"유형": "결론오류|조건오류|수치오류|단위오류|대상치환|모순|기타", "설명": "..."}}],
  "금지내용_위반": [{{"문구": "<금지내용 중 위반된 문구 그대로>", "근거": "답변에서 위반을 보여주는 부분"}}]}}
"""


def load_rules():
    return open(RULES_PATH, encoding="utf-8").read()


def load_gold(path=GOLD_PATH):
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    return {r["id"]: r for r in rows}


def load_runs(rundir):
    path = os.path.join(rundir, "runs.jsonl")
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


GOLD_FIELDS_FOR_JUDGE = [
    "q", "질문자", "유형", "필수_하위질문", "핵심", "언급시검사", "보충",
    "금지내용", "허용되는_정보부족_답변", "부분답_예시", "보류_예시",
    "적용배제근거", "표현기준", "적용판단",
]


def build_user_message(gold, rec):
    g = {k: gold.get(k) for k in GOLD_FIELDS_FOR_JUDGE if gold.get(k)}
    sys_info = {
        "system_answer": rec.get("answer"),
        "withheld_참고용": rec.get("withheld"),
        "mode": rec.get("mode"),
        "entries_사용된_근거": rec.get("entries"),
        "warnings": rec.get("warnings"),
    }
    return ("[gold]\n" + json.dumps(g, ensure_ascii=False, indent=1) +
            "\n\n[system]\n" + json.dumps(sys_info, ensure_ascii=False, indent=1))


def _ask_json(model, system, user):
    text = model.invoke([("system", system), ("human", user)]).content.strip()
    try:
        raw = json.loads(text)
    except (ValueError, TypeError):
        return None, text
    return (raw if isinstance(raw, dict) else None), text


def judge_item(model, rules_text, gold, rec):
    system = JUDGE_SYSTEM_TEMPLATE.format(rules=rules_text)
    user = build_user_message(gold, rec)
    raw, text = _ask_json(model, system, user)
    if raw is None:
        return {"하위질문_판정": {}, "전역_오류": [{"유형": "기타", "설명": f"판정 응답 해석 실패: {text[:200]}"}],
                "금지내용_위반": [], "_판정오류": True}
    raw.setdefault("하위질문_판정", {})
    raw.setdefault("전역_오류", [])
    raw.setdefault("금지내용_위반", [])
    return raw


def fake_judge_item(gold, rec):
    """--dry-run 전용: API를 호출하지 않고 구조만 검증한다 (실제 판정 아님)."""
    subs = gold.get("필수_하위질문", [])
    h = int(hashlib.sha256((rec["run_id"]).encode()).hexdigest(), 16)
    labels = {}
    for i, s in enumerate(subs):
        labels[s] = {"판정": SUBQ_LABELS[(h + i) % 4], "이유": "[dry-run] 가짜 판정"}
    return {"하위질문_판정": labels, "전역_오류": [], "금지내용_위반": []}


def compute_grade(verdict):
    subs = verdict.get("하위질문_판정", {})
    has_violation = bool(verdict.get("전역_오류")) or bool(verdict.get("금지내용_위반"))
    has_subq_error = any(v.get("판정") == "오류" for v in subs.values())
    statuses = [v.get("판정") for v in subs.values()]
    if not statuses:
        return "판정불가"
    if has_violation or has_subq_error:
        return "오답"
    if all(s == "미응답" for s in statuses):
        return "보류"
    if all(s == "충족" for s in statuses):
        return "정답"
    return "부분"


def evidence_coverage(gold, rec):
    required = {(h.get("규정"), h.get("조항")) for h in gold.get("핵심근거", [])}
    if not required:
        return None
    used = {(e.get("문서명"), e.get("article")) for e in rec.get("entries", [])}
    covered = required & used
    return {"필요": len(required), "확보": len(covered), "미확보_목록": sorted(required - used)}


def classify_rule_firing(rec):
    if rec.get("glossary_excluded"):
        return "제외_발동"
    if (rec.get("search_hint") or "").strip():
        return "검색_확장만"
    return "둘다_미작동"


def cmd_judge(args):
    gold = load_gold(args.gold)
    runs = load_runs(args.rundir)
    if args.limit:
        runs = runs[:args.limit]
    rules_text = load_rules()
    model = None if args.dry_run else ChatOpenAI(model=args.model, temperature=0)

    outdir = args.outdir or os.path.join(args.rundir, "scored")
    os.makedirs(outdir, exist_ok=True)
    out_path = os.path.join(outdir, "judged.jsonl")
    done_ids = set()
    if os.path.exists(out_path) and not args.overwrite:
        done_ids = {json.loads(l)["run_id"] for l in open(out_path, encoding="utf-8")}

    with open(out_path, "a" if done_ids else "w", encoding="utf-8") as out:
        for rec in runs:
            if rec["run_id"] in done_ids:
                continue
            g = gold[rec["id"]]
            if "error" in rec:
                row = {"run_id": rec["run_id"], "id": rec["id"], "arm": rec["arm"], "glossary": rec["glossary"],
                       "grade": "실행오류", "verdict": None, "coverage": None,
                       "rule_firing": None, "error": rec["error"]}
            else:
                verdict = fake_judge_item(g, rec) if args.dry_run else judge_item(model, rules_text, g, rec)
                grade = compute_grade(verdict)
                row = {"run_id": rec["run_id"], "id": rec["id"], "arm": rec["arm"], "glossary": rec["glossary"],
                       "유형": g.get("유형"), "확정구분": g.get("확정구분"),
                       "grade": grade, "verdict": verdict, "coverage": evidence_coverage(g, rec),
                       "rule_firing": classify_rule_firing(rec)}
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            out.flush()
            print(rec["run_id"], row["grade"])

    print("DONE", out_path)
    sample_and_save(outdir, gold, args.seed)


def sample_and_save(outdir, gold, seed):
    """정답 20%(조건별, 올림) 표본의 seed·대상 ID를 판정 직후 저장한다 (규칙 7절)."""
    rows = [json.loads(l) for l in open(os.path.join(outdir, "judged.jsonl"), encoding="utf-8")]
    sample = {}
    for arm in ("off", "on"):
        ids = sorted(r["run_id"] for r in rows if r["arm"] == arm and r["grade"] == "정답")
        n = math.ceil(len(ids) * 0.2)
        rnd = random.Random(seed)
        sample[arm] = sorted(rnd.sample(ids, n)) if n else []
    path = os.path.join(outdir, "sample_manifest.json")
    json.dump({"seed": seed, "sampled_correct_run_ids": sample}, open(path, "w", encoding="utf-8"),
               ensure_ascii=False, indent=1)
    print("표본 저장:", path)


def cmd_review(args):
    outdir = args.outdir or os.path.join(args.rundir, "scored")
    gold = load_gold(args.gold)
    rows = [json.loads(l) for l in open(os.path.join(outdir, "judged.jsonl"), encoding="utf-8")]
    runs_by_id = {r["run_id"]: r for r in load_runs(args.rundir)}
    sample = json.load(open(os.path.join(outdir, "sample_manifest.json"), encoding="utf-8"))
    sampled_ids = set(sample["sampled_correct_run_ids"]["off"]) | set(sample["sampled_correct_run_ids"]["on"])

    by_question = {}
    for r in rows:
        by_question.setdefault(r["id"], {})[r["arm"]] = r
    mismatched = {qid for qid, arms in by_question.items()
                  if "off" in arms and "on" in arms and arms["off"]["grade"] != arms["on"]["grade"]}

    targets = []
    for r in rows:
        need = (r["grade"] in ("오답", "부분", "보류") or r["id"] in mismatched or r["run_id"] in sampled_ids)
        if need:
            targets.append(r)

    review_rows, mapping = [], {}
    for r in targets:
        rid = hashlib.sha256((r["run_id"] + "|" + str(args.seed)).encode()).hexdigest()[:12]
        mapping[rid] = {"run_id": r["run_id"], "id": r["id"], "arm": r["arm"], "glossary": r["glossary"]}
        rec = runs_by_id[r["run_id"]]
        g = gold[r["id"]]
        review_rows.append({
            "review_id": rid,
            "질문": g["q"],
            "필수_하위질문": g.get("필수_하위질문"),
            "핵심": g.get("핵심"), "언급시검사": g.get("언급시검사"), "보충": g.get("보충"),
            "금지내용": g.get("금지내용"), "허용되는_정보부족_답변": g.get("허용되는_정보부족_답변"),
            "표시된_답변": rec.get("answer"),
            "LLM_하위질문_판정": (r.get("verdict") or {}).get("하위질문_판정"),
            "LLM_전역_오류": (r.get("verdict") or {}).get("전역_오류"),
            "LLM_금지내용_위반": (r.get("verdict") or {}).get("금지내용_위반"),
            "LLM_등급": r["grade"],
            "사람_등급": None, "사람_메모": None,
        })
    random.Random(args.seed).shuffle(review_rows)  # 순서로 끔/켬을 추측하지 못하게 섞는다

    rpath = os.path.join(outdir, "review.jsonl")
    with open(rpath, "w", encoding="utf-8") as f:
        for row in review_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    mpath = os.path.join(outdir, "review_mapping.json")
    json.dump(mapping, open(mpath, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"검토 대상 {len(review_rows)}건 -> {rpath}")
    print(f"(비공개) 끔/켬 매핑 -> {mpath}  — 검토자에게 주지 않는다")


def cmd_merge(args):
    """사람이 review.jsonl에 '사람_등급'을 채운 뒤, 최종 라벨을 judged.jsonl에 병합하고 집계표를 만든다."""
    outdir = args.outdir or os.path.join(args.rundir, "scored")
    gold = load_gold(args.gold)
    rows = [json.loads(l) for l in open(os.path.join(outdir, "judged.jsonl"), encoding="utf-8")]
    mapping = json.load(open(os.path.join(outdir, "review_mapping.json"), encoding="utf-8"))
    reviewed = [json.loads(l) for l in open(os.path.join(outdir, "review.jsonl"), encoding="utf-8")]
    human, ai_review = {}, {}
    for rv in reviewed:
        m = mapping[rv["review_id"]]
        if rv.get("사람_등급"):
            human[m["run_id"]] = rv["사람_등급"]
        elif rv.get("AI_재검토_등급"):  # 사람 확인 전 2차 LLM 재검토 (review_ai_report.md). 사람보다 아래 등급.
            ai_review[m["run_id"]] = rv["AI_재검토_등급"]

    for r in rows:
        if r["run_id"] in human:
            r["final_grade"], r["label_source"] = human[r["run_id"]], "확인 후 라벨"
        elif r["run_id"] in ai_review:
            r["final_grade"], r["label_source"] = ai_review[r["run_id"]], "AI 재검토 라벨(사람 미확인)"
        else:
            r["final_grade"], r["label_source"] = r["grade"], "LLM 잠정 라벨"

    final_path = os.path.join(outdir, "final.jsonl")
    with open(final_path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    summarize(rows, gold, outdir)
    print("DONE", final_path)


def summarize(rows, gold, outdir):
    def bucket(pred):
        return [r for r in rows if pred(r)]

    confirmed_ids = {qid for qid, g in gold.items() if g.get("확정구분") == "확정후보"}
    main = bucket(lambda r: r["id"] in confirmed_ids)
    unconfirmed = bucket(lambda r: r["id"] not in confirmed_ids)

    def grade_counts(subset):
        c = {}
        for r in subset:
            c[r["final_grade"]] = c.get(r["final_grade"], 0) + 1
        return c

    by_type_arm = {}
    for r in main:
        key = (r.get("유형"), r["arm"])
        by_type_arm.setdefault(key, {})
        by_type_arm[key][r["final_grade"]] = by_type_arm[key].get(r["final_grade"], 0) + 1

    by_rule_firing = {}
    for r in main:
        key = r.get("rule_firing")
        by_rule_firing.setdefault(key, {})
        by_rule_firing[key][r["final_grade"]] = by_rule_firing[key].get(r["final_grade"], 0) + 1

    # 끔/켬 공통 분모(양쪽 초회 실행 성공) 비교, 정상 문항 퇴행(끔 정답/부분 -> 켬 더 나쁨)
    by_q = {}
    for r in main:
        by_q.setdefault(r["id"], {})[r["arm"]] = r
    order = {"정답": 3, "부분": 2, "보류": 1, "오답": 0}
    paired, regressions, missing_pair = [], [], []
    for qid, arms in by_q.items():
        if "off" not in arms or "on" not in arms:
            missing_pair.append(qid)
            continue
        if arms["off"]["grade"] == "실행오류" or arms["on"]["grade"] == "실행오류":
            missing_pair.append(qid)
            continue
        paired.append(qid)
        off_g, on_g = arms["off"]["final_grade"], arms["on"]["final_grade"]
        if off_g in ("정답", "부분") and order.get(on_g, -1) < order.get(off_g, -1):
            regressions.append({"id": qid, "off": off_g, "on": on_g})

    summary = {
        "확정후보_전체_등급": grade_counts(main),
        "미확정_전체_등급": grade_counts(unconfirmed),
        "유형x끔켬": {f"{k[0]}|{k[1]}": v for k, v in by_type_arm.items()},
        "규칙발동별_등급": by_rule_firing,
        "끔켬_공통분모": len(paired),
        "짝_누락_또는_실행오류": missing_pair,
        "정상문항_퇴행": regressions,
        "표시답_정답률_분모": {
            "off": len(bucket(lambda r: r["id"] in confirmed_ids and r["arm"] == "off" and r["grade"] != "실행오류")),
            "on": len(bucket(lambda r: r["id"] in confirmed_ids and r["arm"] == "on" and r["grade"] != "실행오류")),
        },
    }
    path = os.path.join(outdir, "summary.json")
    json.dump(summary, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("집계 저장:", path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["judge", "review", "merge"])
    ap.add_argument("--rundir", required=True)
    ap.add_argument("--gold", default=GOLD_PATH)
    ap.add_argument("--outdir", default=None)
    ap.add_argument("--model", default=OPENAI_MODEL)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true", help="API를 호출하지 않고 가짜 판정으로 구조만 검증한다")
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()
    {"judge": cmd_judge, "review": cmd_review, "merge": cmd_merge}[args.cmd](args)


if __name__ == "__main__":
    main()
