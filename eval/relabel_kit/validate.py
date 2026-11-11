"""산출물 검사 (v2: 관계 · 찾은방법 · 경유 · 탐색 기록 포함). 사용법:
  python validate.py labels 산출/labels_B1.jsonl 산출/changes.jsonl
읽기만 하고 아무것도 고치지 않는다. 오류가 있으면 목록을 출력하고 종료 코드 1.
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REG = HERE / "규정"

RELS = {"인용", "준용·위임", "같은 사항", "정의 용어", "전제", "2단계"}
WAYS = {"번호 · 제목 검색", "낱말 검색", "다른 표현 검색", "같은 규정 통독", "위임 추적", "관련 규정 통독", "2단계 추적"}


def norm(s):
    return re.sub(r"\s+", "", s or "")


def load_regs():
    return {p.stem: p.read_text(encoding="utf-8") for p in REG.glob("*.txt")}


def articles(text):
    """조 라벨 집합: 줄 맨 앞의 「제N조」「제N조의M」"""
    return {m.group(1).replace(" ", "") for m in re.finditer(r"^(제\s*\d+\s*조(?:의\s*\d+)?)", text, re.M)}


def jl(p):
    return [json.loads(l) for l in Path(p).read_text(encoding="utf-8").splitlines() if l.strip()]


def unit_ok(regs, arts, texts, g, c):
    if g not in regs:
        return False
    c = norm(c)
    if c.startswith(("별표", "별지")):
        return f"[{c}" in texts[g]
    return c in arts[g]


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
        if not (r.get("변경_요약") or "").strip():
            errs.append(f"{i}: 변경_요약 없음")
        if len(r.get("다른표현") or []) < 3:
            errs.append(f"{i}: 다른표현은 3개 이상")
        read = r.get("읽은_규정") or []
        if len(read) < 2:
            errs.append(f"{i}: 읽은_규정은 2개 이상 (수정된 규정 포함)")
        if s.get("규정") in regs and s.get("규정") not in read:
            errs.append(f"{i}: 읽은_규정에 수정된 규정({s.get('규정')})이 없음 (같은 규정 통독)")
        for g in read:
            if g not in regs:
                errs.append(f"{i}: 읽은_규정 이름 오류 {g}")
        if not (r.get("검토_검색어") or []):
            errs.append(f"{i}: 검토_검색어 없음")
        for e in r.get("영향") or []:
            g, c = e.get("규정"), e.get("조")
            tag = f"{i}: {g} {c}"
            if g not in regs:
                errs.append(f"{i}: 규정 이름 오류 {g}")
                continue
            if not unit_ok(regs, arts, texts, g, c):
                errs.append(f"{tag}가 없음")
            if g == s.get("규정") and norm(c) == norm(s.get("조")):
                errs.append(f"{tag}: 수정된 조 자체는 영향 목록에서 뺀다")
            q = norm(e.get("근거문장"))
            if not q or q not in texts[g]:
                errs.append(f"{tag}: 근거문장이 원문과 글자 그대로 일치하지 않음")
            if e.get("확신") not in ("높음", "중간", "낮음"):
                errs.append(f"{tag}: 확신은 높음·중간·낮음 중 하나")
            if not (e.get("이유") or "").strip():
                errs.append(f"{tag}: 이유 없음")
            if e.get("관계") not in RELS:
                errs.append(f"{tag}: 관계는 {sorted(RELS)} 중 하나")
            ways = e.get("찾은방법") or []
            if not ways or any(w not in WAYS for w in ways):
                errs.append(f"{tag}: 찾은방법은 {sorted(WAYS)} 중에서 하나 이상")
            if e.get("관계") == "2단계":
                via = (e.get("경유") or "").strip()
                m = re.match(r"^(.+?)\s+((?:제\s*\d+\s*조(?:의\s*\d+)?)|별표\s*\d+)$", via)
                if not m or not unit_ok(regs, arts, texts, m.group(1), m.group(2)):
                    errs.append(f"{tag}: 2단계면 경유에 「규정 조」를 적는다 (예: 인사규정 제31조)")
            if "라벨러" in e:  # 역할 C 산출물
                if len(e["라벨러"]) == 1 or e.get("확신") == "낮음":
                    if e.get("결정") != "인정":
                        errs.append(f"{tag}: 한쪽만 냈거나 확신 낮음이면 결정은 「인정」 (제외는 제외목록으로)")
                    if len((e.get("결정이유") or "").strip()) < 15:
                        errs.append(f"{tag}: 결정이유에 그 조가 수정 내용에 어떻게 닿는지 쓴다")
        for e in r.get("제외목록") or []:
            if len((e.get("결정이유") or "").strip()) < 15:
                errs.append(f"{i}: 제외목록 {e.get('규정')} {e.get('조')}: 결정이유 없음")
        if not r.get("영향") and not (r.get("영향없음_이유") or "").strip():
            errs.append(f"{i}: 영향이 비었으면 영향없음_이유를 쓴다")
    for i in ch:
        if i not in seen:
            errs.append(f"{i}: 라벨 누락")
    return errs


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) == 4 and sys.argv[1] == "labels":
        errs = check_labels(sys.argv[2], sys.argv[3])
    else:
        sys.exit(__doc__)
    print("\n".join(errs) if errs else "오류 없음")
    sys.exit(1 if errs else 0)
