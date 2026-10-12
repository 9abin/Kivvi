"""전처리 파이프라인 실행 진입점.

실행 (rag_pipeline 폴더에서):
  python run.py            # 추출 → 청킹 → 참조 해석 → 산출물 저장
  python run.py extract    # 추출만
"""
import csv
import io
import json
import os
import sys

from config import LAW_DB, OUT_DIR
from src.chunk.articles import DUP_NUMBERING, build_chunks
from src.citations.law_lookup import resolve_external
from src.citations.refs import resolve_citations, review_old_references
from src.corrections import store as corrections
from src.corrections.store import validate, check_pairs
from src.corrections.apply import apply_search_text, apply_reference_corrections, apply_none_findings
from src.extract import extract_all
from src.preprocess.metadata import build_meta

sys.stdout.reconfigure(encoding="utf-8")


def write_outputs(rows, aliases, stat):
    os.makedirs(OUT_DIR, exist_ok=True)
    review = [[r["id"], r["문서명"], c["원문"], c.get("대상") or "", c["상태"]]
              for r in rows for c in r["cites"] if c["상태"] not in ("정상연결", "외부", "외부연결")]
    with io.open(os.path.join(OUT_DIR, "chunks.jsonl"), "w", encoding="utf-8") as f:
        f.write("\n".join(json.dumps(r, ensure_ascii=False) for r in rows))
    with io.open(os.path.join(OUT_DIR, "aliases.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps(aliases, ensure_ascii=False, indent=1))
    with io.open(os.path.join(OUT_DIR, "cite_review.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["출발청크", "출발문서", "인용원문", "추정대상", "상태"])
        w.writerows(review)
    with io.open(os.path.join(OUT_DIR, "항번호_정정목록.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["고유번호", "항 시작 문구", "원문 항번호", "검색용 항번호", "상태"])
        w.writerows([[a, b, c, d, "보정 후보(원문 확인 필요)"] for a, b, c, d in DUP_NUMBERING])
    dups = [r["id"] for r in rows if "_중복" in r["id"]]
    print("청크:", len(rows), "| 문서:", len({r["고유번호"] for r in rows}), "| 약칭사전 문서:", len(aliases))
    print("참조:", dict(stat))
    print("중복 ID:", len(dups), "| 검토 목록:", len(review), "| 항 번호 정정 후보:", len(DUP_NUMBERING))


def main(argv):
    step = argv[1] if len(argv) > 1 else "all"
    report = extract_all()
    print("추출:", len(report), "건, 실패:", sum(1 for r in report if r[2] != "ok"))
    if step == "extract":
        return
    meta = build_meta()
    texts = {idx: io.open(os.path.join(OUT_DIR, "text", idx + ".txt"), encoding="utf-8").read() for idx in meta}
    rows = build_chunks(meta, texts)
    rows, aliases, stat = resolve_citations(rows, {idx: m["문서명"] for idx, m in meta.items()})
    if os.path.exists(LAW_DB):
        rows, n_ext = resolve_external(rows, LAW_DB)
        print("외부 법령 인용 연결:", n_ext, "건")
    rows, applied = apply_search_text(rows, corrections.load())  # 확정된 오탈자만 검색 텍스트에 반영
    print("확정 오탈자 정정 적용:", len(applied), "건")
    rows, ref_applied = apply_reference_corrections(rows, corrections.load())
    print("확정 참조 정정 적용:", len(ref_applied), "건")
    rows, n_none = apply_none_findings(rows, corrections.load())
    print("근거 없음(확인 완료) 표시:", n_none, "건")
    rows, n_cand = review_old_references(rows, {idx: m["문서명"] for idx, m in meta.items()})
    print("옛 규정 참조 연결 후보:", n_cand, "건")
    # 보정 검증: 대상 조문과 치환 글자가 실제로 있는지 확인 (없으면 경고만 출력)
    chunk_ids = {r["id"] for r in rows}
    doc_texts = {}
    for r in rows:
        doc_texts[r["고유번호"]] = doc_texts.get(r["고유번호"], "") + r["본문"]
    cor = corrections.load()
    for cid, target in validate(cor, chunk_ids):
        print(f"경고: 보정 {cid}의 대상 조문 없음 → {target}")
    for w in check_pairs(cor, doc_texts):
        print("경고:", w)
    write_outputs(rows, aliases, stat)


if __name__ == "__main__":
    main(sys.argv)
