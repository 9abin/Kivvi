"""규정 원문을 조문 단위 JSON으로 만든다.

  uv run python scripts/parse_regs.py            # 담당 21건
  uv run python scripts/parse_regs.py --all      # 90건
결과: out/parsed/{규정ID}.json, out/parsed/_summary.tsv
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from regcheck.build import build_all  # noqa: E402

DATA = ROOT / "data" / "한국벤처투자_내부규정"
OUT = ROOT / "out" / "parsed"

# 1주차 담당 범위: 폴더 가나다순 「직장 내 괴롭힘 예방 운영지침」~「휴직자 복무관리 지침」
ASSIGNED_FIRST, ASSIGNED_LAST = "직장 내 괴롭힘 예방 운영지침", "휴직자 복무관리 지침"


def assigned_names() -> list[str]:
    names = sorted(p.name.rsplit("_", 1)[0] for p in DATA.iterdir() if p.is_dir())
    return names[names.index(ASSIGNED_FIRST) : names.index(ASSIGNED_LAST) + 1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()
    names = None if args.all else assigned_names()
    regs = build_all(DATA, OUT, names)
    rows = ["규정ID\t규정명\t위계\t개정\t조\t마지막조\t삭제조\t부칙\t별표\t별지\t번호이상	머리말불일치"]
    for r in regs:
        arts = r["articles"]
        nums = [(int(a["label"][1:].split("조")[0]), a["label"]) for a in arts]
        gaps = [f"{a[1]}→{b[1]}" for a, b in zip(nums, nums[1:]) if b[0] not in (a[0], a[0] + 1)]
        rows.append(
            "\t".join(
                map(str, [
                    r["reg_id"], r["name"], r["hierarchy"], r["revised"], len(arts),
                    arts[-1]["label"] if arts else "-", sum(a["deleted"] for a in arts), len(r["addenda"]),
                    sum(x["kind"] == "별표" for x in r["appendices"]),
                    sum(x["kind"] == "별지" for x in r["appendices"]), ",".join(gaps),
                    ",".join(r["header_mismatch"]),
                ])
            )
        )
    (OUT / "_summary.tsv").write_text("\n".join(rows), encoding="utf-8")
    print("\n".join(rows))
    print(f"\n{len(regs)}건, 본문 조 {sum(len(r['articles']) for r in regs)}개 → {OUT}")


if __name__ == "__main__":
    main()
