"""정답 근거가 어느 단계에서 빠졌는지 나눈다 (검색 → 리랭크 → 근거 조립 → 답변).

근거 묶음(정답 조문 하나, 별표 조각 여럿이면 한 묶음)마다 다음 중 하나로 분류한다:
  넘어감          : 답변 모델에 넘어간 근거(runs.jsonl entries)에 있음 → 답이 빠뜨렸다면 "넘어갔는데 안 씀"
  조립에서_빠짐   : 리랭크 상위 5개 조 안에 있었는데 답변 근거에는 없음 (글자 상한, 용어집·표 범위 필터, 모드)
  리랭커가_밀어냄 : 리랭커가 읽은 30개 안에 있었는데 5위 밖
  합침_30위밖     : 후보(벡터·BM25·바꿔 쓴 검색어 결과)에는 있었는데 RRF 순위가 30위 밖이라 리랭커가 못 읽음
  후보에도_없음   : 어느 후보 목록에도 없음
  색인에_없음     : 평가셋 근거가 부칙·별지 등 색인 대상이 아님 (검색 정답에서 빠진 근거)
  외부_법령       : 평가셋 근거가 내부규정이 아님

입력: rank_trace(단계별 목록을 남긴 것), 답변 실행 runs.jsonl, 답변 평가셋, 검색 정답 파일, (선택) 사람·에이전트 검토 결과.
검색 단계 재현은 rank_trace의 바꿔 쓴 검색어(캐시)를 쓰므로 실제 답변 실행과 후보가 조금 다를 수 있다.
"넘어감"은 답변 실행 기록으로 판단해서 정확하다.

실행 (저장소 루트에서):
  uv run python -m src.eval.loss_breakdown --trace eval/runs/rank_trace_100_team_stages.json --tag team_s2b_hl_fast_luna_100
      [--review 검토결과.json]   # [{"id", "내판정", "원인", ...}] (선택)
"""
import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict

from config import BASE_DIR
from src.chunk.articles import article_id
from src.store.pgstore import PgStore

sys.stdout.reconfigure(encoding="utf-8")

STAGES = ["넘어감", "조립에서_빠짐", "리랭커가_밀어냄", "합침_30위밖", "후보에도_없음", "색인에_없음", "외부_법령"]


def label_map():
    """(문서명, 조항 표기) → 조 ID. 답변 근거 목록의 '문서명 조항'을 조 ID로 바꾸는 데 쓴다 (지금 PG_DATABASE 기준)."""
    store = PgStore()
    titles = {d["doc_id"]: d["title"] for d in store.load_documents()}
    with store.conn.cursor() as cur:
        cur.execute("SELECT chunk_id, doc_id, article FROM chunks")
        out = {}
        for cid, doc, art in cur.fetchall():
            aid = article_id(cid)
            out[(titles.get(doc), art)] = aid
            out[(titles.get(doc), (art or "").split(" ")[0])] = aid
    store.close()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trace", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--questions", default=os.path.join(BASE_DIR, "eval", "answer_eval_100.jsonl"))
    ap.add_argument("--retrieval", default=os.path.join(BASE_DIR, "eval", "questions_100_retrieval.jsonl"))
    ap.add_argument("--review", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    trace = {r["q"]: r for r in json.load(open(args.trace, encoding="utf-8"))}
    runs = {json.loads(l)["id"]: json.loads(l) for l in open(os.path.join(BASE_DIR, "eval", "answer_runs", args.tag, "runs.jsonl"), encoding="utf-8")}
    gold = {json.loads(l)["id"]: json.loads(l) for l in open(args.retrieval, encoding="utf-8")}
    review = {r["id"]: r for r in json.load(open(args.review, encoding="utf-8"))} if args.review else {}
    labels = label_map()

    rows = []
    for q in (json.loads(l) for l in open(args.questions, encoding="utf-8") if l.strip()):
        groups = [set(g) for g in gold.get(q["id"], {}).get("정답묶음", [])]
        groups = [{article_id(c) for c in g} for g in groups]
        t = trace.get(q["q"], {})
        # runs.jsonl의 entries는 "문서명", "article" 칸으로 저장돼 있다
        passed = {labels.get((e["문서명"], e["article"])) or labels.get((e["문서명"], (e["article"] or "").split(" ")[0]))
                  for e in runs.get(q["id"], {}).get("entries", [])} - {None}
        top30 = t.get("리랭크_순위", [])
        top5 = top30[:5]
        cands, fused30 = set(t.get("후보", [])), set(t.get("합침_순위", [])[:30])
        status = []
        for g in groups:
            if g & passed:
                s = "넘어감"
            elif g & set(top5):
                s = "조립에서_빠짐"
            elif g & set(top30):
                s = "리랭커가_밀어냄"
            elif g & cands:
                s = "합침_30위밖" if not g & fused30 else "리랭커가_밀어냄"
            else:
                s = "후보에도_없음"
            status.append({"조": sorted(g)[0], "단계": s})
        # 검색 정답에서 빠진 평가셋 근거 (부칙·별지, 외부 법령)
        for e in q.get("근거") or []:
            label = e.get("조항") or ""
            if re.match(r"^(부칙|별지)", label):
                status.append({"조": f"{e.get('규정')} {label}", "단계": "색인에_없음"})
        rv = review.get(q["id"], {})
        rows.append({"id": q["id"], "q": q["q"], "근거": status, "검토판정": rv.get("내판정"), "검토원인": rv.get("원인")})

    out = args.out or os.path.join(BASE_DIR, "eval", "runs", f"loss_{args.tag}.json")
    json.dump(rows, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    every = Counter(s["단계"] for r in rows for s in r["근거"])
    print(f"근거 묶음 {sum(every.values())}개, 단계별:")
    for k in STAGES:
        print(f"  {k:<14}{every.get(k, 0):>4}")
    if review:
        bad = [r for r in rows if r["검토판정"] and r["검토판정"] != "정답"]
        print(f"\n검토 판정이 정답이 아닌 {len(bad)}문항: 못 넘어간 근거 중 가장 앞 단계에서 빠진 것 기준 "
              "(모두 넘어갔으면 '넘어갔는데_안_씀')")
        worst = Counter()
        cross = defaultdict(Counter)
        for r in bad:
            miss = [s["단계"] for s in r["근거"] if s["단계"] != "넘어감"]
            key = max(miss, key=STAGES.index) if miss else "넘어갔는데_안_씀"
            worst[key] += 1
            cross[r["검토원인"]][key] += 1
        for k, v in worst.most_common():
            print(f"  {k:<16}{v:>4}")
        print("\n검토 원인 × 빠진 단계")
        for c, v in cross.items():
            print(f"  {c}: {dict(v)}")
    print("저장:", out)


if __name__ == "__main__":
    main()
