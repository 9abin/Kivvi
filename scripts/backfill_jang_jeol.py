"""이미 만든 out/parsed/*.json에 조마다 장 · 절 머리줄(jang, jeol)만 채워 넣는다 (261111, 리포트 93).

  uv run python scripts/backfill_jang_jeol.py

다른 칸은 건드리지 않는다. 원문에서 다시 읽은 조와 id · label이 같은 조에만 넣고, 다르면 그 규정은 건너뛰고 보고한다.
(파서는 이제 장과 절을 따로 기록한다. 예전 chapter 칸은 「바로 앞 머리줄」이라 절 아래 조는 장을 잃었다:
 정보보안관리지침 제19조 chapter = 「제1절 …」, 장 = 제3장.)
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from regcheck.build import build  # noqa: E402


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    src_root = ROOT / "data" / "한국벤처투자_내부규정"
    done = skipped = filled = 0
    for folder in sorted(p for p in src_root.iterdir() if p.is_dir() and "_" in p.name):
        reg_id = folder.name.rsplit("_", 1)[1]
        out = ROOT / "out" / "parsed" / f"{reg_id}.json"
        if not out.exists():
            continue
        cur = json.loads(out.read_text(encoding="utf-8"))
        try:
            new = build(folder)
        except Exception as e:  # noqa: BLE001
            print(f"건너뜀 {folder.name}: 다시 읽기 실패 {e!r}")
            skipped += 1
            continue
        by_id = {a["id"]: a for a in new["articles"]}
        if [a["id"] for a in cur["articles"]] != [a["id"] for a in new["articles"]] or any(
                by_id[a["id"]]["label"] != a["label"] for a in cur["articles"]):
            print(f"건너뜀 {folder.name}: 조 목록이 다름 (지금 {len(cur['articles'])} / 다시 읽음 {len(new['articles'])})")
            skipped += 1
            continue
        for a in cur["articles"]:
            a["jang"], a["jeol"] = by_id[a["id"]].get("jang", ""), by_id[a["id"]].get("jeol", "")
            filled += bool(a["jang"] or a["jeol"])
        out.write_text(json.dumps(cur, ensure_ascii=False, indent=1), encoding="utf-8")
        done += 1
    print(f"채움 {done}개 규정 (장 · 절 있는 조 {filled}), 건너뜀 {skipped}")


if __name__ == "__main__":
    main()
