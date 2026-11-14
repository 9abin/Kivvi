"""나눠 돌린 조합 실험 답변 결과(answers_*.json 여러 개)를 하나로 합친다. 지표(agg)는 문항 수로 가중해 다시 계산한다.

사용: python merge_answer_chunks.py <출력.json> <answers_p1.json> <answers_p2.json> ...
"""
import json
import sys


def pct(vals, p):
    vals = sorted(v for v in vals if v is not None)
    return round(vals[min(len(vals) - 1, int(round(p / 100 * (len(vals) - 1))))], 3) if vals else None


def main(out, paths):
    parts = [json.load(open(p, encoding="utf-8")) for p in paths]
    recs = [q for part in parts for q in part["questions"]]
    n = len(recs)
    labels = {x: sum(1 for r in recs if r.get("label") == x) for x in ("정답", "부분", "오답", "답못함", "채점실패")}
    fills = [sum(r["judge"]["요지_충족"]) / len(r["judge"]["요지_충족"]) for r in recs if (r.get("judge") or {}).get("요지_충족")]
    unans = sum(p["agg"]["n_unanswerable"] for p in parts)
    refused = sum((p["agg"]["refusal_accuracy"] or 0) * p["agg"]["n_unanswerable"] for p in parts)
    cites = sum(p["agg"]["citations"] for p in parts)
    cite_ok = sum((p["agg"]["citation_accuracy"] or 0) * p["agg"]["citations"] for p in parts)
    agg = {"n": n, "labels": labels, "label_rate": {k: v / n for k, v in labels.items()},
           "core_point_fill": sum(fills) / len(fills) if fills else None,
           "forbidden_violation_rate": sum(1 for r in recs if (r.get("judge") or {}).get("금지_위반")) / n,
           "refusal_accuracy": refused / unans if unans else None, "n_unanswerable": unans,
           "citation_accuracy": cite_ok / cites if cites else None, "citations": cites,
           "withheld_rate": sum(1 for r in recs if r.get("mode") != "answer") / n,
           "sec_first_token": {"p50": pct([r.get("sec_first_token") for r in recs], 50), "p95": pct([r.get("sec_first_token") for r in recs], 95)},
           "sec_answer": {"p50": pct([r.get("sec_answer") for r in recs], 50), "p95": pct([r.get("sec_answer") for r in recs], 95)},
           "errors": sum(1 for r in recs if r.get("error"))}
    meta = dict(parts[0]["meta"])
    meta["merged_from"] = paths
    json.dump({"meta": meta, "agg": agg, "questions": recs}, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"{n}문항 합침 → {out}\n  판정 {labels}, 오류 {agg['errors']}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
