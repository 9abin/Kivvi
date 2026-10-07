"""김가빈 corrections.json(사람 판단 보정 155건) → 정답 원장 형식 (eval/ledger/corrections.jsonl).

  uv run python scripts/import_corrections.py data/external/gabin/corrections.json [--commit 7d0677f]
출처: github.com/9abin/kvic-internal-regulations-review  rag_pipeline/data/manual/corrections.json

실제 키: id, 고유번호, 문서, 조항(자유 서술), 종류, 범위, 원문, 정정안, 대상ID, 근거, 상태 (+ 치환전·치환후)
원칙
- 위치는 원문 기준: 조항 서술에서 조·항을 읽는다. 여러 조를 적은 항목은 조마다 한 행(group으로 묶음)
- 위치가 없는 메모(「(인용 관계 메모)」, 「…인용 전반」)는 원장에 넣지 않고 따로 센다
- 「(맞음)」 확인 건은 type=정상_확인으로 넣는다: 우리가 이 위치를 오류로 표시하면 오탐 (score.py)
- 오탈자는 그대로 넣고 score.py가 범위 밖으로 따로 센다 (평가 포함 여부 미정)
- 점검 코드는 이 파일을 읽지 않는다 (정답 누수 방지). 채점만 쓴다
"""

import argparse
import collections
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "eval" / "ledger" / "corrections.jsonl"
CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"

TYPE = {
    "오탈자": "오탈자",
    "참조 오류": "인용_대상불일치",
    "인용 오류(조번호 불일치)": "인용_대상불일치",
    "인용 오류(계획)": "인용_대상불일치",
    "근거 조문 없음": "인용_대상없음",
    "옛 규정 참조": "인용_대상없음",
    "항 번호 중복(검색용 정정)": "항번호_중복",
    "조번호 중복": "조번호_이상",
}
RE_ART = re.compile(r"제\s*(\d+)\s*조(?:\s*의\s*(\d+))?((?:\s*제\s*\d+\s*항|\s*[①-⑳])?)")
RE_CITE = re.compile(r"제\s*\d+\s*조(?:\s*의\s*\d+)?(?:\s*제?\s*\d+\s*항)?(?:\s*제?\s*\d+\s*호)?|제\s*\d+\s*장")


def locations(jo: str) -> list[tuple[str, int]]:
    """조항 서술 → [(조, 항)]. '제23조④, 제24조①' → [('제23조',4), ('제24조',1)], '부칙 ⑤항' → [('',5)]"""
    out = []
    for m in RE_ART.finditer(jo):
        label = f"제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "")
        tail = m.group(3) or ""
        para = 0
        if mm := re.search(r"(\d+)\s*항", tail):
            para = int(mm.group(1))
        elif mm := re.search(r"[①-⑳]", tail):
            para = CIRCLED.index(mm.group(0)) + 1
        if (label, para) not in out:   # '제12조(벌점) / 제12조(그 밖의 사항)'은 한 위치
            out.append((label, para))
    # '제4조·제6조·제9조' 처럼 조만 나열된 경우 위 정규식이 모두 잡는다
    if not out and (mm := re.search(r"([①-⑳])\s*항?", jo)):
        out.append(("", CIRCLED.index(mm.group(1)) + 1))
    return out


def unit_of(jo: str) -> str:
    return "부칙" if "부칙" in jo else "별지" if "별지" in jo else "별표" if "별표" in jo else "본문"


def quote_of(c: dict, typ: str) -> str:
    if typ in ("항번호_중복", "조번호_이상", "오탈자"):
        return "" if typ != "오탈자" else c["원문"]
    m = RE_CITE.search(c["원문"])
    return re.sub(r"\s+", "", m.group(0)) if m else c["원문"]


def answer_of(c: dict) -> str:
    if "맞음" in c["정정안"] or c["정정안"].startswith("없음") or c["종류"] == "옛 규정 참조":
        return ""
    m = RE_CITE.search(c["정정안"])
    return re.sub(r"\s+", "", m.group(0)) if m else ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--commit", default="", help="가빈 저장소 커밋 (출처 기록)")
    args = ap.parse_args()
    data = json.loads(Path(args.path).read_text(encoding="utf-8"))
    rows, notes, unknown = [], [], []
    for c in data:
        ok = "맞음" in c["정정안"]
        typ = "정상_확인" if ok else TYPE.get(c["종류"])
        locs = locations(c["조항"])
        if not locs or typ is None:
            (notes if not locs else unknown).append(c)
            continue
        unit = unit_of(c["조항"])
        ans = answer_of(c)
        handling = ("판단 불가" if typ == "인용_대상없음" and not ans else
                    "고칠 수 있음" if ans or typ in ("항번호_중복", "조번호_이상", "오탈자") else "판단 가능")
        expected = ({"show": "정상"} if ok else {"show": "수정 제안", "fix": ans} if ans else
                    {"show": "대상 없음 표시" if typ == "인용_대상없음" else "위치 표시"})
        for k, (label, para) in enumerate(locs):
            rows.append({
                "id": c["id"] + (f"-{k + 1}" if len(locs) > 1 else ""), "reg_id": c["고유번호"], "unit": unit,
                "article": label, "para": para, "quote": quote_of(c, typ), "type": typ, "handling": handling,
                "answer": ans, "expected": expected, "basis": c["근거"], "source": "김가빈 corrections.json"
                + (f" ({args.commit})" if args.commit else ""), "judge": "김가빈", "date": "2026-10-07",
                "status": c["상태"], "link": {"gabin": c["id"], "대상ID": c.get("대상ID", ""), "종류": c["종류"]},
                **({"group": c["id"]} if len(locs) > 1 else {}),
            })
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    print(f"원장 {len(rows)}행 ← 보정 {len(data)}건 → {OUT.relative_to(ROOT)}")
    print("  종류:", dict(collections.Counter(r["type"] for r in rows)))
    print(f"  위치 없는 메모 {len(notes)}건 (원장 제외):", [f"{c['id']} {c['문서']} {c['조항']}" for c in notes])
    if unknown:
        print(f"  종류 대응 없음 {len(unknown)}건:", [f"{c['id']} {c['종류']}" for c in unknown])
    for r in rows:
        if r["type"] != "오탈자":
            print(f"   {r['id']:7} {r['type']:10} {r['reg_id']} {r['unit']} {r['article'] or '-'} {r['para'] or ''} 「{r['quote']}」 → {r['answer'] or r['expected']['show']}")


if __name__ == "__main__":
    sys.exit(main())
