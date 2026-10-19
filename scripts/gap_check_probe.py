"""답변 뒤 누락 검사(self-ask)의 게이트 측정: 되묻는 질문으로 다시 찾으면 놓친 정답 근거가 올라오는가.

답변 평가 없이 검색만 돌린다 (검색_설계.md 13절 예정).
1. 질문을 지금 방식으로 검색해 상위 5개 조를 얻는다 (바꿔 쓴 검색어는 eval/runs/multi-cache.jsonl의 것).
2. GPT에게 질문 + 질문자 + 상위 5개 조(제목·첫 줄) + 버린 후보 6~30위의 제목을 주고
   "답이 달라질 조건·예외·같이 알아야 할 것"을 규정 용어 질문 최대 3개로 만들게 한다.
3. 되묻는 질문마다 검색해 상위 3개 조를 얻는다.
4. 원래 상위 5개에 없던 정답 근거 묶음이 되묻기 검색 상위 3개(또는 5개)에 올라오면 "건짐"으로 센다.

실행 (저장소 루트, 추천 구성 환경 변수): uv run python -m scripts.gap_check_probe [--limit N] [--out eval/runs/gap_probe.json]
"""
import argparse
import json
import os
import re
import sys
import time

from dotenv import load_dotenv

from config import BASE_DIR
from src.answer.generate import Generator
from src.search.search import Searcher, _article_base

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv()

CACHE = os.path.join(BASE_DIR, "eval", "runs", "multi-cache.jsonl")
QUESTIONS = os.path.join(BASE_DIR, "eval", "questions_100_retrieval.jsonl")
ANSWER_SET = os.path.join(BASE_DIR, "eval", "answer_eval_100.jsonl")

PROBE_PROMPT = """너는 사내 규정 질의응답의 보조 검사기다. 직원의 질문과 지금 찾은 근거 조문, 그리고 검색이 버린 다른 후보 조문의 제목이 주어진다.
답변이 달라지거나 조건이 붙을 수 있는데 지금 근거에는 없는 사항을 찾기 위해, 규정에서 다시 찾아볼 **되묻는 질문**을 만든다.

규칙:
- 질문자가 같이 알아야 할 조건·예외·기한·승인·다른 제도(예: 건강검진 질문이면 "검진 받는 시간은 공가 처리되나", 출장 질문이면 "휴일 이동 시간은 근무로 인정되나")를 생각한다.
- 버린 후보 제목에 질문과 관련된 조건(기밀, 승인, 예외, 제한)이 보이면 그걸 되묻는다.
- 이미 찾은 근거에 답이 있는 것은 되묻지 않는다.
- 질문자 정보가 있고 질문이 본인에 대한 것이면 질문자의 신분(공무직, 임원 등)에 맞는 규정을 되묻는다. 질문이 다른 사람에 대한 것이면 질문자 정보를 쓰지 않는다.
- 되묻는 질문은 규정 원문에 쓰일 법한 용어로, 한 문장씩, 최대 3개. 없으면 빈 목록.
출력은 JSON 하나만: {{"질문": ["...", "..."]}}"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default=os.path.join(BASE_DIR, "eval", "runs", "gap_probe.json"))
    args = ap.parse_args()

    cache = {r["q"]: r for r in map(json.loads, open(CACHE, encoding="utf-8"))} if os.path.exists(CACHE) else {}
    askers = {r["q"]: r.get("질문자") for r in map(json.loads, open(ANSWER_SET, encoding="utf-8"))}
    questions = [json.loads(l) for l in open(QUESTIONS, encoding="utf-8") if l.strip()]
    if args.limit:
        questions = questions[:args.limit]
    s, g = Searcher(), Generator()
    out, t0 = [], time.time()
    tot_missed = tot_hit3 = tot_hit5 = tot_new = tot_fq = 0
    for i, q in enumerate(questions, 1):
        text = q["q"]
        groups = q.get("정답묶음") or [[x] for x in q.get("정답", [])]
        hits = s.search(text, k=5, rewritten=cache.get(text, {}).get("rewritten"), expand_cites=False,
                        expand_siblings=False)
        top5 = [f"{h['doc_id']}_{_article_base(h['article'])}" for h in hits]
        found = {a for a in top5}
        missed = [gset for gset in groups if not any(x in found for x in gset)]
        # 버린 후보 6~30위 (조 단위, 제목만)
        seen, discarded = set(top5), []
        for h in s.last_reranked:
            a = f"{h['doc_id']}_{_article_base(h['article'])}"
            if a in seen:
                continue
            seen.add(a)
            title = s.documents.get(h["doc_id"], {}).get("title") or h["doc_id"]
            first = re.sub(r"\s+", " ", h["body"])[:70]
            discarded.append(f"- {title} {_article_base(h['article'])}: {first}")
            if len(discarded) >= 25:
                break
        evidence = "\n".join(f"- {h['문서명']} {h['article']}: {re.sub(r'\s+', ' ', h['body'])[:160]}" for h in hits)
        user = f"질문: {text}\n"
        if askers.get(text):
            user += f"질문자: {askers[text]}\n"
        user += f"\n지금 찾은 근거:\n{evidence}\n\n검색이 버린 후보 제목:\n" + "\n".join(discarded)
        raw = g._ask(PROBE_PROMPT, user)
        m = re.search(r"\{.*\}", raw, re.S)
        try:
            fqs = [x for x in json.loads(m.group(0)).get("질문", []) if isinstance(x, str) and x.strip()][:3] if m else []
        except json.JSONDecodeError:
            fqs = []
        probe = []
        for fq in fqs:
            r = s.search(fq, k=5, expand_cites=False, expand_siblings=False)
            ids = [f"{h['doc_id']}_{_article_base(h['article'])}" for h in r]
            probe.append({"되묻기": fq, "상위5": ids})
        hit3 = [gset for gset in missed if any(x in p["상위5"][:3] for p in probe for x in gset)]
        hit5 = [gset for gset in missed if any(x in p["상위5"] for p in probe for x in gset)]
        new = {x for p in probe for x in p["상위5"][:3]} - set(top5)
        tot_missed += len(missed); tot_hit3 += len(hit3); tot_hit5 += len(hit5); tot_new += len(new); tot_fq += len(fqs)
        out.append({"id": q.get("id"), "q": text, "상위5": top5, "놓친정답": missed, "되묻기": probe,
                    "건짐3": hit3, "건짐5": hit5, "새조문3": sorted(new)})
        print(f"{i}/{len(questions)} 놓침 {len(missed)} 건짐 {len(hit3)}/{len(hit5)} 되묻기 {len(fqs)} | {text[:30]}", flush=True)
    s.close()
    json.dump(out, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    n = len(out)
    print(f"\n문항 {n}, 되묻기 {tot_fq}개 ({tot_fq/n:.1f}/문항), {time.time()-t0:.0f}초")
    print(f"원래 상위 5개가 놓친 정답 근거 {tot_missed}묶음 중 되묻기 검색 상위 3개에 {tot_hit3}개, 상위 5개에 {tot_hit5}개 올라옴")
    print(f"되묻기 상위 3개 중 원래 근거에 없던 새 조문 {tot_new}개 ({tot_new/n:.1f}/문항) — 건짐 대비 잡음 비율 {tot_new/max(tot_hit3,1):.1f}배")
    print(f"저장: {args.out}")


if __name__ == "__main__":
    main()
