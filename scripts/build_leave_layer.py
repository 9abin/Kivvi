"""휴직 개념 층 규칙 초안 (자동 초안, 사람 검토 전).

클래스 = 휴직 종류, 프로퍼티 = 속성(기간·분할·보수·승진·절차·대상·복직), 인스턴스 = 조.
조 본문에 종류 키워드와 속성 키워드가 함께 있으면 (종류, 속성) 배정을 만든다. 추론은 하지 않는다.
출력: out/leave_layer/assign.jsonl, summary.txt
"""

import glob
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

KINDS = {
    "육아휴직": ["육아휴직"],
    "질병휴직": ["질병휴직", "인병휴직"],
    "가족돌봄휴직": ["가족돌봄휴직", "가족돌봄"],
    "청원휴직": ["청원휴직"],
    "직권휴직": ["직권휴직", "명령휴직"],
    "휴직(공통)": ["휴직"],
}
ATTRS = {
    "기간": ["휴직기간", "휴직 기간", "기간", "이내", "연장"],
    "분할": ["분할", "나누어", "회에 한", "2회", "3회"],
    "보수": ["보수", "급여", "수당", "임금", "급"],
    "승진": ["승진", "경력", "근속"],
    "절차": ["신청", "승인", "사유서", "서류", "통보", "제출"],
    "대상": ["대상", "자격", "사유", "요건", "해당하는"],
    "복직": ["복직", "복귀", "신고"],
}


def norm(s):
    return re.sub(r"\s+", "", s or "")


def main():
    regs = {r["reg_id"]: r for r in (json.loads(Path(p).read_text(encoding="utf-8")) for p in glob.glob(str(ROOT / "out/parsed/[0-9]*.json")))}
    rows, per = [], Counter()
    for rid, r in regs.items():
        for a in r["articles"]:
            if a.get("deleted"):
                continue
            t = norm(a.get("norm") or "")
            if "휴직" not in t and "휴직" not in (a.get("title") or ""):
                continue
            kinds = [k for k, ws in KINDS.items() if k != "휴직(공통)" and any(w in t for w in ws)] or ["휴직(공통)"]
            attrs = [k for k, ws in ATTRS.items() if any(norm(w) in t for w in ws if len(w) > 1)]
            for k in kinds:
                for at in attrs or ["미분류"]:
                    rows.append({"id": a["id"], "reg": r.get("name"), "label": a.get("label"), "title": a.get("title"), "kind": k, "attr": at})
                    per[(k, at)] += 1
    out = ROOT / "out/leave_layer"
    out.mkdir(parents=True, exist_ok=True)
    (out / "assign.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in rows), encoding="utf-8")
    arts = {x["id"] for x in rows}
    by_reg = defaultdict(set)
    for x in rows:
        by_reg[x["reg"]].add(x["id"])
    lines = [f"휴직 관련 조 {len(arts)}개, (종류, 속성) 배정 {len(rows)}건 (자동 초안)", ""]
    lines += [f"{k:12s} {a:6s} {n}" for (k, a), n in sorted(per.items())]
    lines += ["", "규정별 조 수"] + [f"{n}: {len(s)}" for n, s in sorted(by_reg.items(), key=lambda z: -len(z[1]))]
    (out / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
