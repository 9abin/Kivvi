"""오탈자 정정안에서 치환 쌍(치환전 → 치환후)을 뽑는 도구.

정정안이 '“X”로 수정' 형식인 항목은 자동으로 쌍을 만든다.
나머지는 사람이 corrections.json에 치환전·치환후를 직접 적는다.
이 도구는 쌍을 '후보'로만 만들며, 확정은 corrections.json에서 한다.
"""
import re

from src.corrections.store import load  # noqa: F401  (CLI에서 함께 사용)

QUOTED = re.compile(r"“([^”]+)”로 수정")


def derive_pairs(corrections):
    """오탈자 항목에서 (id, 고유번호, 치환전, 치환후) 후보 목록을 만든다."""
    out = []
    for x in corrections:
        if x.get("종류") != "오탈자":
            continue
        m = QUOTED.search(x.get("정정안", ""))
        before = (x.get("원문") or "").split("|")[0].strip()
        if m and before and m.group(1) != before:
            out.append((x["id"], x["고유번호"], before, m.group(1)))
    return out


if __name__ == "__main__":
    import io
    import json
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    from config import CORRECTIONS
    data = json.loads(io.open(CORRECTIONS, encoding="utf-8").read())
    pairs = derive_pairs(data)
    print(f"치환 쌍 후보 {len(pairs)}건")
    for p in pairs:
        print(" ", p)
