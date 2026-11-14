"""조합 실험 답변 결과(answers_*.json)와 평가셋을 합쳐 답변 검토 페이지(HTML 한 파일)를 만든다.

사용(MINI_CNS_rag worktree에서):
  python ../MINI_CNS/scripts/answer_review_page.py <answers.json> <평가셋.jsonl> <출력.html> [--label 이름]
페이지는 질문별 답변 · 채점 · 정답 근거 검색 여부를 보여 주고, 질문마다 메모를 남길 수 있다(Artifact db 기능).
"""
import argparse
import json
import re

BASE = re.compile(r"^\s*(제\s*\d+\s*조(?:\s*의\s*\d+)?|별표\s*\d*|별지\s*\d*|부칙)")


def base(article):
    m = BASE.match(article or "")
    return re.sub(r"\s+", "", m.group(1)) if m else (article or "").strip()


def build(answers_path, gold_path, label):
    A = json.load(open(answers_path, encoding="utf-8"))
    gold = {g["id"]: g for g in (json.loads(l) for l in open(gold_path, encoding="utf-8") if l.strip())}
    rows = []
    for r in A["questions"]:
        g = gold[r["id"]]
        ents = [{"doc": e["문서명"], "art": e["article"]} for e in (r.get("entries") or [])]
        used = {e["doc"] + "|" + base(e["art"]) for e in ents}
        eq = {e["규정"] + "|" + base(e["조항"]) for e in g.get("동등근거") or []}
        j = r.get("judge") or {}
        rows.append({
            "id": g["id"], "q": g["q"], "type": g["유형"], "able": g["답가능여부"], "who": g.get("질문자", ""), "intent": g.get("의도", ""),
            "check": g.get("현업확인", ""), "checkWhy": g.get("현업확인_사유", ""), "law": g.get("법령기준", ""),
            "points": g["정답요지"], "sub": g.get("정답요지_보조") or [], "forbid": g.get("금지내용") or [],
            "gold": [{"doc": e["규정"], "art": e["조항"], "quote": e.get("인용", ""), "tier": e.get("tier", "필수"),
                      "hit": e["규정"] + "|" + base(e["조항"]) in used} for e in g["근거"]],
            "equiv": [{"doc": e["규정"], "art": e["조항"], "note": e.get("메모", ""), "hit": e["규정"] + "|" + base(e["조항"]) in used}
                      for e in g.get("동등근거") or []],
            "answer": r.get("answer") or "", "mode": r.get("mode"), "error": r.get("error"), "warnings": r.get("warnings") or [],
            "entries": ents, "goldKeys": sorted({e["규정"] + "|" + base(e["조항"]) for e in g["근거"]} | eq),
            "sec": r.get("sec_total"), "verdict": r.get("label") or "채점실패", "reason": j.get("이유", ""),
            "met": j.get("요지_충족") or [], "subMet": j.get("보조_충족") or [], "violated": bool(j.get("금지_위반")),
        })
    m = A["meta"]
    info = {"label": label, "combo": m.get("combo", ""), "answer_model": m.get("answer_model"), "judge_model": m.get("judge_model"),
            "k": m.get("k"), "time": m.get("time", ""), "eval_hash": m.get("eval_hash", "")}
    return rows, info, A["agg"]


def base_js():
    return BASE.pattern


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("answers")
    ap.add_argument("gold")
    ap.add_argument("out")
    ap.add_argument("--label", default="최고 조합")
    ap.add_argument("--template", default=None, help="HTML 틀 (기본: 이 스크립트 옆 answer_review_page.html)")
    a = ap.parse_args()
    rows, info, agg = build(a.answers, a.gold, a.label)
    tpl_path = a.template or __file__.replace(".py", ".html")
    tpl = open(tpl_path, encoding="utf-8").read()
    data = json.dumps({"rows": rows, "info": info, "agg": agg}, ensure_ascii=False).replace("</", "<\\/")
    open(a.out, "w", encoding="utf-8").write(tpl.replace("/*__DATA__*/null", data))
    print(f"{len(rows)}문항 → {a.out}")


if __name__ == "__main__":
    main()
