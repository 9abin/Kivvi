"""김가빈 corrections.json(사람 판정 보정 155건) → 정답 원장 형식.

  uv run python scripts/import_corrections.py 경로/corrections.json --show   # 키 구조만 확인
  uv run python scripts/import_corrections.py 경로/corrections.json          # eval/ledger/corrections.jsonl 생성

파일 형식을 아직 모르므로 흔한 키 이름을 순서대로 찾는다. 못 찾으면 무엇이 비었는지 알려 주고 멈춘다.
가빈 청크 ID 형식: 고유번호_제N조[의K][_M항]  (예: 23234_제5조_2항, 23234_부칙)
주의: 가빈 청크 ID의 항 번호는 중복 항을 고친 '검색용 번호'일 수 있다 (노사협의회 제9조 두 번째 ② → 3항).
     원장에는 원문 표기를 남겨야 하므로 인용 원문(quote)으로 위치를 다시 확인한다.
"""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "eval" / "ledger" / "corrections.jsonl"

KEYS = {
    "chunk_id": ["chunk_id", "청크ID", "청크_id", "id", "위치"],
    "kind": ["type", "종류", "kind", "category", "유형"],
    "before": ["before", "원문", "orig", "from", "잘못"],
    "after": ["after", "정정", "fixed", "to", "수정", "target", "대상"],
    "basis": ["reason", "근거", "note", "설명", "memo"],
    "judge": ["judge", "판정자", "reviewer"],
    "date": ["date", "판정일", "updated"],
}
# 가빈 종류 → 원장 종류
KIND_MAP = {"오탈자": "오탈자", "인용 정정": "인용_대상불일치", "인용정정": "인용_대상불일치",
            "근거 없음": "인용_대상없음", "근거없음": "인용_대상없음", "항번호 중복": "항번호_중복", "항번호중복": "항번호_중복"}
RE_CHUNK = re.compile(r"^(?P<reg>\d+)_(?:(?P<add>부칙)|제(?P<jo>\d+)조(?:의(?P<br>\d+))?)(?:_(?P<hang>\d+)항)?$")


def pick(row: dict, field: str):
    for k in KEYS[field]:
        if k in row and row[k] not in (None, ""):
            return row[k]
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args()
    data = json.loads(Path(args.path).read_text(encoding="utf-8"))
    rows = data if isinstance(data, list) else next(v for v in data.values() if isinstance(v, list))
    if args.show:
        print(f"{len(rows)}건, 첫 항목 키: {list(rows[0].keys())}")
        print(json.dumps(rows[0], ensure_ascii=False, indent=1)[:800])
        return
    out, bad = [], []
    for i, row in enumerate(rows):
        cid, kind = pick(row, "chunk_id"), pick(row, "kind")
        m = RE_CHUNK.match(str(cid or ""))
        if not m or not kind:
            bad.append((i, cid, kind))
            continue
        art = "부칙" if m["add"] else f"제{m['jo']}조" + (f"의{m['br']}" if m["br"] else "")
        out.append({
            "id": f"K{i + 1:03d}", "reg_id": m["reg"], "unit": "부칙" if m["add"] else "본문",
            "article": art, "para": int(m["hang"] or 0), "quote": str(pick(row, "before") or ""),
            "type": KIND_MAP.get(str(kind).strip(), str(kind).strip()), "handling": "고칠 수 있음",
            "answer": str(pick(row, "after") or ""), "basis": str(pick(row, "basis") or ""),
            "source": "김가빈 corrections.json", "judge": pick(row, "judge") or "김가빈", "date": pick(row, "date") or "",
            "link": {"gabin": cid},
        })
    if bad:
        print(f"위치·종류를 읽지 못한 항목 {len(bad)}건 (KEYS·RE_CHUNK를 파일 형식에 맞게 고칠 것): {bad[:5]}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in out) + "\n", encoding="utf-8")
    print(f"{len(out)}건 → {OUT}")


if __name__ == "__main__":
    main()
