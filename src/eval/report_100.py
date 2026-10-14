"""한 실행(tag)의 결과를 한 장으로 합쳐 보고한다. 저장된 답변(runs.jsonl)을 그대로 재사용하고 답변을 다시 만들지 않는다.

사용(프로젝트 루트에서, PG_DATABASE로 그 실행이 쓴 색인 DB를 고른다):
  python -m src.eval.report_100 --tag para_luna_100 --trace rank_trace_100_para.json

구성
  1. 검색 단계별 Hit@1/3/5/10, MRR  : rank_trace가 저장한 순위 파일(eval/runs/<trace>)에서 계산. LLM 호출 없음.
  2. 최종 근거 Recall               : 정답 근거 조문이 답변에 실제로 쓰인 근거(entries)에 들어 있는 비율. LLM 호출 없음.
  3. RAGAS 스타일 3지표             : 충실도, 관련성, 문맥재현. config.JUDGE_MODEL로 채점. eval/answer_runs/<tag>/ragas.jsonl에 쌓아 이어 하기 가능.
  4. LLM 판정                       : judge_answers가 만든 judged.jsonl의 정답/부분/오답/답못함 개수, 유형별.
  5. 답변 생성 시간                 : runs.jsonl의 sec(검색, 바꿔쓰기, 답변, 근거 검사 포함) 평균·중앙값·90퍼센타일·최대.
"""
import argparse
import collections
import json
import os
import re
import statistics as st
import sys
from concurrent.futures import ThreadPoolExecutor

sys.stdout.reconfigure(encoding="utf-8")
from dotenv import load_dotenv  # noqa: E402

load_dotenv()
os.environ["LANGCHAIN_TRACING_V2"] = "false"
from langchain_openai import ChatOpenAI  # noqa: E402

from config import BASE_DIR, JUDGE_MODEL  # noqa: E402
from src.eval.ragas_style import FAITH_PROMPT, RECALL_PROMPT, RELEVANCY_PROMPT  # noqa: E402
from src.store.pgstore import PgStore  # noqa: E402

STAGES = [("벡터", "벡터 검색"), ("키워드", "키워드 검색"), ("합침", "RRF 합침"), ("리랭크", "리랭크 (최종)")]


def jload(path):
    return [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]


def parse_json(text):
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    text = text[text.find("{"):text.rfind("}") + 1]
    try:
        out = json.loads(text)
        return out if isinstance(out, dict) else None
    except ValueError:
        return None


def ask(model, system, user):
    # ragas_style의 프롬프트는 ChatPromptTemplate용이라 중괄호가 두 겹이다. 직접 호출하므로 한 겹으로 푼다.
    system = system.replace("{{", "{").replace("}}", "}")
    for _ in range(3):
        try:
            r = parse_json(model.invoke([("system", system), ("human", user)]).content)
            if r:
                return r
        except Exception:  # noqa: BLE001
            pass
    return None


def retrieval_table(trace_path):
    rows = json.load(open(trace_path, encoding="utf-8"))
    n = len(rows)
    out = ["| 단계 | Hit@1 | Hit@3 | Hit@5 | Hit@10 | MRR |", "|---|---|---|---|---|---|"]
    for key, label in STAGES:
        v = [r[key] for r in rows]

        def hit(k):
            return sum(1 for x in v if x and x <= k) / n

        mrr = sum(1 / x for x in v if x) / n
        out.append(f"| {label} | {hit(1):.2f} | {hit(3):.2f} | {hit(5):.2f} | {hit(10):.2f} | {mrr:.3f} |")
    return f"질문 {n}개 (rank_trace)\n\n" + "\n".join(out)


def norm_article(a):
    m = re.match(r"(제\d+조(?:의\d+)?)", a or "")
    return m.group(1) if m else re.sub(r"\s+", "", a or "")


def build_context(store, title2doc, entries):
    """답변에 쓰인 근거 목록(문서명, 조항)의 본문을 DB에서 다시 읽는다. runs.jsonl에는 본문이 없다."""
    parts = []
    for e in entries:
        doc = title2doc.get(e["문서명"])
        if not doc:
            continue
        parts += [r["body"] for r in store.siblings(doc, e["article"]) if r["article"] == e["article"]]
    return "\n\n".join(parts) or "(근거 없음)"


def gold_text(store, title2doc, g):
    parts, seen = [], set()
    for ev in g.get("근거", []):
        doc = title2doc.get(ev["규정"])
        base = norm_article(ev["조항"])
        if doc and (doc, base) not in seen and not base.startswith("부칙"):
            seen.add((doc, base))
            parts += [r["body"] for r in store.siblings(doc, base)]
        parts.append(f"{ev['규정']} {ev['조항']}: {ev.get('인용', '')}")
    return "\n\n".join(parts)


def ragas_rows(args, gold, runs, store, title2doc):
    path = os.path.join(BASE_DIR, "eval", "answer_runs", args.tag, "ragas.jsonl")
    done = {r["id"]: r for r in jload(path)} if os.path.exists(path) else {}
    model = ChatOpenAI(model=JUDGE_MODEL, temperature=0)

    def one(job):
        run, g, ctx, gtext = job
        ans = run.get("answer") or ""
        f = ask(model, FAITH_PROMPT, f"답변:\n{ans}\n\n실제로 사용된 근거:\n{ctx}") if run.get("mode") == "answer" else None
        rel = ask(model, RELEVANCY_PROMPT, f"질문: {g['q']}\n\n답변:\n{ans}")
        rec = ask(model, RECALL_PROMPT, f"정답 근거:\n{gtext}\n\n실제로 검색된 근거:\n{ctx}")
        faith = min(1.0, f.get("근거에_있는_문장", 0) / f["총문장"]) if f and f.get("총문장") else None
        row = {"id": run["id"], "mode": run.get("mode"), "faithfulness": faith,
               "answer_relevancy": (rel or {}).get("관련성"), "context_recall": (rec or {}).get("문맥재현")}
        with open(path, "a", encoding="utf-8") as out:  # 문항마다 바로 저장해 중간에 끊겨도 이어 할 수 있다
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"  RAGAS {run['id']} 충실도 {faith} 관련성 {row['answer_relevancy']} 문맥재현 {row['context_recall']}", flush=True)
        return row

    todo = [r for r in runs if r["id"] not in done and "error" not in r]
    if args.limit:
        todo = todo[:args.limit]
    # DB 조회는 연결 하나를 나눠 쓰면 안 되므로 순서대로 먼저 끝내고, 모델 호출만 동시에 보낸다.
    jobs = [(r, gold[r["id"]], build_context(store, title2doc, r.get("entries", [])), gold_text(store, title2doc, gold[r["id"]]))
            for r in todo]
    with ThreadPoolExecutor(8) as ex:
        new = list(ex.map(one, jobs))
    return list(done.values()) + new


def used_recall(gold, runs):
    got = need = full = 0
    for run in runs:
        g = gold[run["id"]]
        used = {(e["문서명"], norm_article(e["article"])) for e in run.get("entries", [])}
        want = {(ev["규정"], norm_article(ev["조항"])) for ev in g.get("근거", [])}
        got += len(want & used)
        need += len(want)
        full += want <= used
    return got, need, full


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--questions", default="eval/answer_eval_100.jsonl")
    ap.add_argument("--trace", default=None, help="eval/runs/ 아래 rank_trace 결과 파일 이름")
    ap.add_argument("--limit", type=int, default=0, help="RAGAS 채점을 앞에서 N개만 (시험용)")
    ap.add_argument("--no-ragas", action="store_true")
    args = ap.parse_args()

    gold = {g["id"]: g for g in jload(args.questions)}
    d = os.path.join(BASE_DIR, "eval", "answer_runs", args.tag)
    runs = [r for r in jload(os.path.join(d, "runs.jsonl")) if r["id"] in gold]
    store = PgStore()
    with store.conn.cursor() as cur:
        cur.execute("SELECT title, doc_id FROM documents")
        title2doc = dict(cur.fetchall())

    lines = [f"# 평가 보고: {args.tag}", "",
             f"문항 {len(runs)}개, DB {os.environ.get('PG_DATABASE', '(기본)')}, 채점 모델 {JUDGE_MODEL}", ""]
    if args.trace:
        lines += ["## 1. 검색 단계별 Hit·MRR", "", retrieval_table(os.path.join(BASE_DIR, "eval", "runs", args.trace)), ""]
    got, need, full = used_recall(gold, runs)
    lines += ["## 2. 최종 근거 Recall (답변에 쓰인 근거 안에 정답 조문이 있는가)", "",
              f"정답 근거 조문 {need}개 중 {got}개 확보 (**Recall {got / need:.2f}**), 전부 확보한 문항 {full}/{len(runs)}", ""]
    if not args.no_ragas:
        rows = ragas_rows(args, gold, runs, store, title2doc)
        lines += [f"## 3. RAGAS 스타일 (0~1, 채점 모델 {JUDGE_MODEL})", "", "| 지표 | 평균 | 측정 문항 |", "|---|---|---|"]
        for key, label in (("faithfulness", "충실도 faithfulness"), ("answer_relevancy", "관련성 answer relevancy"),
                           ("context_recall", "문맥재현 context recall")):
            v = [r[key] for r in rows if r.get(key) is not None]
            lines.append(f"| {label} | {sum(v) / len(v):.2f} | {len(v)} |" if v else f"| {label} | 측정값 없음 | 0 |")
        lines.append("")
    jp = os.path.join(d, "judged.jsonl")
    if os.path.exists(jp):
        judged = {j["id"]: j for j in jload(jp) if j["id"] in gold}
        order = ["정답", "부분", "오답", "답못함", "채점실패"]
        c = collections.Counter(j["판정"] for j in judged.values())
        lines += ["## 4. LLM 판정 (judge_answers)", "", "| 구분 | 문항 | " + " | ".join(order) + " |",
                  "|---|---|" + "---|" * len(order)]
        lines.append(f"| 전체 | {len(judged)} | " + " | ".join(f"{c[k]} ({c[k] / len(judged):.0%})" for k in order) + " |")
        by = collections.defaultdict(collections.Counter)
        for i, j in judged.items():
            by[gold[i]["유형"]][j["판정"]] += 1
        for t in sorted(by):
            n = sum(by[t].values())
            lines.append(f"| {t} | {n} | " + " | ".join(f"{by[t][k]} ({by[t][k] / n:.0%})" for k in order) + " |")
        lines.append("")
    secs = sorted(r["sec"] for r in runs if "sec" in r)
    p90 = secs[min(len(secs) - 1, int(len(secs) * 0.9))]
    modes = collections.Counter(r.get("mode") for r in runs)
    lines += ["## 5. 답변 생성 시간 (질문 하나, 초)", "",
              f"평균 {st.mean(secs):.1f} / 중앙값 {st.median(secs):.1f} / 90퍼센타일 {p90:.1f} / 최대 {max(secs):.1f}"
              "  (검색, 바꿔쓰기, 답변, 근거 검사 포함)",
              f"실행 모드: {dict(modes)}", ""]
    text = "\n".join(lines)
    open(os.path.join(d, "report.md"), "w", encoding="utf-8").write(text)
    print(text)
    store.close()


if __name__ == "__main__":
    main()
