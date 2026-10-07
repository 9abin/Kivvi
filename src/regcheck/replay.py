"""과거 개정 재현: 현행 규정의 개정 표시로 그날 이전 구조를 되돌려 만들고, 그날의 개정문을 재구성한다.

  [본조신설 D] 이고 「종전 제X조에서 이동」이 없음 → 그날 새로 생긴 조 (이전 구조에서 뺌, 개정문에서는 신설)
  「종전 제X조에서 이동 … D」                      → 그날 제X조에서 옮겨 옴 (이전 구조에서는 제X조)
  <개정 D> 내용 개정                                → 옛 문안이 원문에 없어 재현하지 않음 (결과에 밝힘)
조문 ID는 번호 기준이므로(관련 조항 표도 번호로 해석) 되돌린 구조는 옛 번호로 다시 ID를 붙인다.
"""

from __future__ import annotations

import copy
import re

from .agent import _eul, _ro
from .amend import marker


def _key(label: str) -> tuple[int, int]:
    m = re.match(r"제\s*(\d+)\s*조(?:\s*의\s*(\d+))?", label)
    return int(m.group(1)), int(m.group(2) or 0)


def _date_pat(date: str) -> str:
    y, m, d = (int(x) for x in re.findall(r"\d+", date)[:3])
    return rf"{y}\s*\.\s*0?{m}\s*\.\s*0?{d}"


def reconstruct(reg: dict, date: str) -> tuple[dict, str, dict]:
    """(그날 이전 규정, 그날 개정문, 설명) — 번호 이동·신설만 재현"""
    dp = _date_pat(date)
    moved, inserted, content = {}, [], []
    for a in reg["articles"]:
        text = a["text"] + " " + " ".join(a.get("amend_notes", []))
        m = re.search(rf"종전\s*제\s*(\d+)\s*조(?:\s*의\s*(\d+))?에서\s*이동[^\]]*?{dp}", text)
        if m:
            moved[a["id"]] = f"제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "")
        elif re.search(rf"본조신설\s*{dp}", text):
            inserted.append(a)
        elif re.search(rf"개정\s*{dp}", text):
            content.append(a["label"])
    prior = copy.deepcopy(reg)
    keep = []
    for a in prior["articles"]:
        if any(a["id"] == x["id"] for x in inserted):
            continue
        if a["id"] in moved:
            a["label"] = moved[a["id"]]
        no = re.match(r"제(\d+)조(?:의(\d+))?", a["label"])
        aid = f"{reg['reg_id']}:{no.group(1)}" + (f"의{no.group(2)}" if no.group(2) else "")
        a["id"] = aid
        for it in a["items"]:
            p, i, s = it["path"]
            it["id"] = f"{aid}-{p}-{i}-{s}"
        keep.append(a)
    keep.sort(key=lambda a: _key(a["label"]))
    prior["articles"] = keep

    # 개정문: 번호 이동은 큰 번호부터 (빈 자리로 옮기도록), 그다음 신설
    lines = []
    cur_by_id = {a["id"]: a for a in reg["articles"]}
    for aid, old in sorted(moved.items(), key=lambda kv: _key(kv[1]), reverse=True):   # 가지 번호까지 큰 것부터 (제8조의2 → 제8조의3 먼저)
        new = cur_by_id[aid]["label"]
        lines.append(f"{old}{_eul(old)} {new}{_ro(new)} 한다.")
    for a in inserted:
        after_move = sorted([_key(cur_by_id[k]["label"]) if k in moved else _key(x["label"])
                             for x, k in ((x, next((kk for kk, v in moved.items() if v == x["label"]), None)) for x in keep)])
        prev_key = max((k for k in after_move if k < _key(a["label"])), default=None)
        prev = {"label": f"제{prev_key[0]}조" + (f"의{prev_key[1]}" if prev_key[1] else "")} if prev_key else None
        lines.append((f"{prev['label']}의 다음에 " if prev else "") + f"{a['label']}를 다음과 같이 신설한다.")
        body = []
        for k, it in enumerate(a["items"]):
            t = re.sub(r"\s*[<\[][^<>\[\]]*(?:신설|개정|이동)[^<>\[\]]*[>\]]", "", marker(it["path"]) + it["norm"]).strip()
            body.append((f"{a['label']}({a['title']}) " if k == 0 else "") + t)
        lines += body
    note = {"date": date, "moved": {cur_by_id[k]["label"]: v for k, v in moved.items()},
            "inserted": [a["label"] for a in inserted], "content_not_replayed": content}
    return prior, "\n".join(lines), note
