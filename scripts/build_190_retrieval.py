"""답변 평가셋(eval/answer_eval.jsonl, 190문항)의 근거를 검색 평가용 조 ID로 바꾼다 → eval/questions_190_retrieval.jsonl.

근거 표기 → 청크 ID
  "제5조", "제5조 제2항 제1호"   → {문서}_제5조
  "제12조의2 제1항"             → {문서}_제12조의2
  "별표 1", "별표 1의2", "별표 1 주3" → 그 별표의 청크 ({문서}_별표1, _별표1_02 조각, _별표_01, _별표1의2 …).
                                   조각이 여럿이면 인용문이 들어 있는 조각만 (못 찾으면 조각 전부)
  "별표"(번호 없음)              → 그 문서 별표 중 인용문이 들어 있는 조각 (3개 넘게 남으면 뺀다)
근거 하나가 여러 청크에 걸치면 "정답묶음"의 한 묶음이 된다. 묶음 안의 청크 중 하나만 찾아도 그 근거는 찾은 것이다
(src/eval/run_multi.py의 모두@k). "정답"은 모든 묶음의 청크를 합친 목록이다(기존 채점 도구용).
  부칙, 별지                     → 색인 대상이 아니라 뺀다
색인 대상(src/index/build.py is_indexed)인 청크만 정답이 된다. 근거가 하나도 안 남는 문항(규정에 없음 등)은 뺀다.

이전 변환(2026.10.13 이전 세션)은 조항을 띄어쓰기로 잘라 "별표 1"이 "별표"가 되는 바람에, 별표 근거가 빠지거나
그 문서의 별표 조각 전부가 정답이 됐다. 이 스크립트가 그걸 바로잡는다.

실행 (저장소 루트에서): uv run python -m scripts.build_190_retrieval
  다른 답변 평가셋: --src eval/answer_eval_100.jsonl --dst eval/questions_100_retrieval.jsonl
"""
import json
import os
import re
import sys

from config import BASE_DIR, OUT_DIR
from src.index.build import is_indexed

sys.stdout.reconfigure(encoding="utf-8")

SRC = os.path.join(BASE_DIR, "eval", "answer_eval.jsonl")
DST = os.path.join(BASE_DIR, "eval", "questions_190_retrieval.jsonl")

ARTICLE = re.compile(r"^(제\d+조(?:의\d+)?)")
ANNEX = re.compile(r"^별표\s*(\d+)?(?:\s*의\s*(\d+))?")


def annex_ids(doc_ids, n, m):
    """문서의 별표 청크 중 번호 n(·의 m)에 해당하는 것. 번호가 없으면 별표가 하나뿐일 때 그 청크들."""
    pattern = re.compile(r"_별표_?0*(\d+)(?:의(\d+))?(?:_\d+|_중복\d+)?$")
    out, numbers = set(), set()
    for cid in doc_ids:
        hit = pattern.search(cid)
        if not hit:
            continue
        numbers.add(hit.group(1))
        if n is None or (hit.group(1) == str(int(n)) and (hit.group(2) or None) == m):
            out.add(cid)
    if n is None and len(numbers) != 1:
        return set()
    return out


def _squash(s):
    return re.sub(r"[\s\W_]+", "", s or "")


def by_quote(found, quote, bodies):
    """별표 조각 중 인용문이 들어 있는 조각만 남긴다. 어느 조각에도 없으면 그대로 둔다."""
    q = _squash(quote)[:40]
    if len(q) < 8:
        return found
    hit = {c for c in found if q in _squash(bodies[c])}
    return hit or found


def convert(evidence, titles, by_doc, bodies):
    """근거마다 정답 청크 묶음을 만든다. 묶음 안의 청크 중 하나만 찾아도 그 근거는 찾은 것이다."""
    groups, skipped = [], []
    for e in evidence:
        doc = titles.get(re.sub(r"\s+", "", e.get("규정") or ""))
        label = (e.get("조항") or "").strip()
        if not doc:
            skipped.append(f"{e.get('규정')} {label} (내부규정 아님)")
            continue
        ids = by_doc.get(doc, set())
        if a := ARTICLE.match(label):
            cid = f"{doc}_{a.group(1)}"
            found = {c for c in ids if c == cid or c.startswith(cid + "_")}
        elif b := ANNEX.match(label):
            n, m = b.group(1), b.group(2)
            found = annex_ids(ids, n, m) if n else {c for c in ids if "_별표" in c}
            found = by_quote(found, e.get("인용"), bodies)
            if not n and len(found) > 3:  # 번호 없는 별표인데 인용문으로도 못 좁혔다
                found = set()
        else:
            found = set()
        if found:
            if sorted(found) not in groups:
                groups.append(sorted(found))
        else:
            skipped.append(f"{e.get('규정')} {label}")
    return groups, skipped


def main(src=SRC, dst=DST):
    chunks = [json.loads(line) for line in open(os.path.join(OUT_DIR, "chunks.jsonl"), encoding="utf-8") if line.strip()]
    titles = {re.sub(r"\s+", "", c["문서명"]): c["고유번호"] for c in chunks}
    by_doc, bodies = {}, {}
    for c in chunks:
        if is_indexed(c):
            by_doc.setdefault(c["고유번호"], set()).add(c["id"])
            bodies[c["id"]] = c["본문"]

    rows, report = [], []
    for q in (json.loads(line) for line in open(src, encoding="utf-8") if line.strip()):
        groups, skipped = convert(q.get("근거") or [], titles, by_doc, bodies)
        gold = sorted({c for g in groups for c in g})
        if gold:
            rows.append({"id": q["id"], "q": q["q"], "유형": "내용", "정답": gold, "정답묶음": groups})
        report.append((q["id"], gold, skipped))
    with open(dst, "w", encoding="utf-8", newline="\n") as f:
        f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    print(f"{len(rows)}문항 저장 (근거가 하나도 안 남은 문항 {len(report) - len(rows)}개 제외): {dst}")
    for qid, gold, skipped in report:
        if skipped and gold:
            print(f"  {qid} 일부 근거 제외: {', '.join(skipped)}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=SRC, help="답변 평가셋 (예: eval/answer_eval_100.jsonl)")
    ap.add_argument("--dst", default=DST)
    args = ap.parse_args()
    main(args.src, args.dst)
