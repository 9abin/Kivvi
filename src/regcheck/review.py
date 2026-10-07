"""검토 결정 기록 (finding_log).

사람의 결정은 다시 만들 수 없으므로 생성물(out/)이 아니라 `eval/review_log.jsonl`에 덧붙이기만 한다 (git에 남김).
결과 ID(F00012)는 다시 돌리면 바뀌므로, 위치·문구·종류로 만든 고정 열쇠로 결정을 찾는다.
지우거나 고치지 않는다. 결정을 바꾸면 새 줄이 쌓이고 마지막 줄이 현재 상태다.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOG = Path(os.environ.get("REGCHECK_REVIEW_LOG", ROOT / "eval" / "review_log.jsonl"))  # 시험용으로 바꿀 수 있음

# 대상별 선택지 (02-specs finding 상태를 PoC 범위로 줄임). 첫 값이 기본 상태
STATES = {
    "baseline": ["신규", "오류 확인", "오탐", "판단 불가", "보류"],
    "row": ["신규", "수락", "거절", "보류"],                 # 대비표 행
    "impact": ["신규", "반영 필요", "문제없음", "오탐", "보류"],  # 변경안 영향 항목
}


def _h(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:12]


def key_baseline(f: dict) -> str:
    return "B:" + _h(f["reg_id"], f["unit"], f["article"], f["para"], f["quote"], f["type"])


def key_row(change_id: str, row: dict) -> str:
    return f"R:{change_id}:" + _h(row.get("origin") or "", row["new_label"], json.dumps(row["new"], ensure_ascii=False))


def key_impact(change_id: str, it: dict) -> str:
    return f"I:{change_id}:" + _h(it["category"], it["reg_id"], it.get("unit"), it.get("article"), it.get("para"), it.get("quote"))


class ReviewLog:
    def __init__(self, path: Path | None = None):
        self.path = path = Path(path or LOG)
        self.rows: list[dict] = []
        if path.exists():
            self.rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]

    def state(self, key: str, kind: str) -> str:
        last = next((r for r in reversed(self.rows) if r["key"] == key), None)
        return last["state"] if last else STATES[kind][0]

    def history(self, key: str) -> list[dict]:
        return [r for r in self.rows if r["key"] == key]

    def decide(self, key: str, kind: str, state: str, reviewer: str, comment: str = "", ref: dict | None = None) -> dict:
        if state not in STATES[kind]:
            raise ValueError(f"{kind}에 없는 상태: {state}")
        if not reviewer.strip():
            raise ValueError("검토자 이름이 필요합니다")
        rec = {"key": key, "kind": kind, "prev": self.state(key, kind), "state": state, "reviewer": reviewer.strip(),
               "at": datetime.now().isoformat(timespec="seconds"), "comment": comment.strip(), "ref": ref or {}}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.rows.append(rec)
        return rec
