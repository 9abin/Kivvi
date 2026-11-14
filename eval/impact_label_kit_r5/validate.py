"""산출물 검사. 사용법:
  python validate.py changes 산출/changes.jsonl
  python validate.py labels  산출/labels_B1.jsonl 산출/changes.jsonl
읽기만 하고 아무것도 고치지 않는다. 오류가 있으면 목록을 출력하고 종료 코드 1.
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REG = HERE / "규정"


def norm(s):
    return re.sub(r"\s+", "", s or "")


def load_regs():
    regs = {}
    for p in REG.glob("*.txt"):
        t = p.read_text(encoding="utf-8")
        regs[p.stem] = t
    return regs


def articles(text):
    """조 라벨 집합: 줄 맨 앞의 「제N조」「제N조의M」"""
    return {m.group(1).replace(" ", "") for m in re.finditer(r"^(제\s*\d+\s*조(?:의\s*\d+)?)", text, re.M)}


def jl(p):
    return [json.loads(l) for l in Path(p).read_text(encoding="utf-8").splitlines() if l.strip()]


KINDS = {"값 변경", "삭제", "번호 이동", "정의 변경", "위임·법령 변경", "표현 수정", "고립 수정"}


def check_changes(path):
    errs, regs = [], load_regs()
    rows = jl(path)
    ids = [r.get("id") for r in rows]
    if len(set(ids)) != len(ids):
        errs.append("id 중복")
    cnt = {}
    for r in rows:
        i = r.get("id")
        s = r.get("수정") or {}
        if r.get("분할") not in ("개발", "봉인", "새봉인2"):
            errs.append(f"{i}: 분할은 개발 또는 봉인")
        if s.get("종류") not in KINDS:
            errs.append(f"{i}: 종류 값 오류 {s.get('종류')}")
        if s.get("종류") != "위임·법령 변경" or s.get("규정") in regs:
            if s.get("규정") not in regs:
                errs.append(f"{i}: 규정 이름이 규정목록에 없음 {s.get('규정')}")
            elif norm(s.get("조")) not in {norm(a) for a in articles(regs[s["규정"]])}:
                errs.append(f"{i}: {s.get('규정')}에 {s.get('조')}가 없음")
        if not (s.get("개정문") or "").strip():
            errs.append(f"{i}: 개정문 없음")
        cnt[(r.get("분할"), s.get("종류"))] = cnt.get((r.get("분할"), s.get("종류")), 0) + 1
    print("문항", len(rows), "| 분할·종류별", dict(sorted(cnt.items(), key=lambda x: str(x[0]))))
    return errs


def check_labels(path, changes_path):
    errs, regs = [], load_regs()
    arts = {k: {norm(a) for a in articles(v)} for k, v in regs.items()}
    texts = {k: norm(v) for k, v in regs.items()}
    ch = {r["id"]: r for r in jl(changes_path)}
    seen = set()
    for r in jl(path):
        i = r.get("id")
        seen.add(i)
        if i not in ch:
            errs.append(f"{i}: changes.jsonl에 없는 id")
            continue
        s = ch[i]["수정"]
        for e in r.get("영향") or []:
            g, c = e.get("규정"), norm(e.get("조"))
            if g not in regs:
                errs.append(f"{i}: 규정 이름 오류 {g}")
                continue
            if c.startswith(("별표", "별지")):
                if f"[{c}" not in texts[g] and f"[{c[:2]}{c[2:]}" not in texts[g]:
                    errs.append(f"{i}: {g}에 {e.get('조')}가 없음")
            elif c not in arts[g]:
                errs.append(f"{i}: {g}에 {e.get('조')}가 없음")
            if g == s.get("규정") and c == norm(s.get("조")):
                errs.append(f"{i}: 수정된 조 자체는 영향 목록에서 뺀다 ({g} {e.get('조')})")
            q = norm(e.get("근거문장"))
            if not q or q not in texts[g]:
                errs.append(f"{i}: {g} {e.get('조')} 근거문장이 원문과 글자 그대로 일치하지 않음")
            if e.get("확신") not in ("높음", "중간", "낮음"):
                errs.append(f"{i}: 확신은 높음·중간·낮음 중 하나")
            if not (e.get("이유") or "").strip():
                errs.append(f"{i}: 이유 없음")
        if not r.get("영향") and not (r.get("영향없음_이유") or "").strip():
            errs.append(f"{i}: 영향이 비었으면 영향없음_이유를 쓴다")
    for i in ch:
        if i not in seen:
            errs.append(f"{i}: 라벨 누락")
    return errs


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "changes" and len(sys.argv) == 3:
        errs = check_changes(sys.argv[2])
    elif mode == "labels" and len(sys.argv) == 4:
        errs = check_labels(sys.argv[2], sys.argv[3])
    else:
        sys.exit(__doc__)
    print("\n".join(errs) if errs else "오류 없음")
    sys.exit(1 if errs else 0)
