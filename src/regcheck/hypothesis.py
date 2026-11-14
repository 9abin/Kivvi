"""가설 카드: 조항을 고칠 때 LLM이 「함께 확인해야 할 주제」를 추론하고, 그 주제로 내규를 검색해 사람에게 보여 준다.

  concerns(post, reg_name, art_label, art_title, diff_text)   LLM → [{topic, why}] (최대 3)
  hypotheses(...)                                              주제마다 내규 검색(임베딩 + reranker) → 가설 카드 (change_check 카드 형식)

원칙 (261015 팀원 논의):
  · 이것은 근거가 아니라 모델의 가설이다. 인과를 단정하지 않고 「~가 바뀌면 ~도 확인할 만함」 정도로만 보여 준다 (근거 종류: 모델 추정)
  · 이미 다른 경로(인용·같은 제목·정의 용어·의미 이웃)로 찾은 조문은 가설로 다시 내지 않는다
  · 우선순위는 낮음. 카드는 두 조문의 원문을 함께 보여 주고 판단은 담당자가 한다 (도메인 판단은 현업)
  · LLM이 만든 주제는 내규 용어와 어긋나기 쉬워(리포트 49) 주제 낱말과 바뀐 문장을 함께 검색어로 쓴다
"""

from __future__ import annotations

import json
import re

from .relocate import article_text

PROMPT = ("회사 내부 규정(내규)의 조문 하나를 고친다. 이 변경 때문에 같은 내규나 다른 내규에서 함께 확인해야 할 주제를 최대 3개 추론한다.\n"
          "각 주제는 JSON 한 줄: {\"topic\": \"내규 조문에 나올 법한 낱말 2~6개\", \"why\": \"~가 바뀌면 ~도 확인할 만함, 한 문장\"}\n"
          "추측이 약하면 적게 쓴다. 이 조문 자체나 이미 인용으로 이어진 조문은 쓰지 않는다. JSON 줄만 출력, 설명 없이.")
MIN_SCORE = 0.3
MAX_TOPICS = 3
SEARCH_K = 25


def concerns(post, reg_name: str, art_label: str, art_title: str, diff_text: str) -> list[dict]:
    """post(messages) → 문자열 (로컬 LLM). 실패하면 빈 목록 (가설은 보조 기능이라 점검을 막지 않는다)."""
    user = f"내규: {reg_name}\n조문: {art_label}({art_title})\n변경 내용:\n{diff_text[:700]}"
    try:
        raw = post([{"role": "system", "content": PROMPT}, {"role": "user", "content": user}])
    except Exception:
        return []
    out = []
    for line in raw.splitlines():
        m = re.search(r"\{.*\}", line)
        if not m:
            continue
        try:
            d = json.loads(m.group(0))
        except ValueError:
            continue
        topic, why = str(d.get("topic", "")).strip(), str(d.get("why", "")).strip()
        if 2 <= len(topic) <= 60 and why:
            out.append({"topic": topic, "why": why[:160]})
    return out[:MAX_TOPICS]


def hypotheses(post, regs: dict, reg: dict, old_art: dict, new_art: dict | None, diff_text: str, index, embed, rerank,
               skip: set[tuple[str, str]]) -> list[dict]:
    """skip: 이미 다른 경로로 낸 (규정ID, 조 라벨) 쌍. 바뀐 조 자신도 뺀다."""
    import numpy as np

    cs = concerns(post, reg["name"], old_art["label"], old_art["title"], diff_text)
    if not cs:
        return []
    vecs, chunks = index
    by_id = {a["id"]: (r, a) for r in regs.values() for a in r["articles"] if not a.get("deleted")}
    out, used = [], set(skip) | {(reg["reg_id"], old_art["label"])}
    for c in cs:
        query = f"{c['topic']} {diff_text[:150]}"
        q = embed([query])[0]
        cand: dict[str, tuple[dict, dict]] = {}
        for i in np.argsort(-(vecs @ q)):
            aid = chunks[i]["article_id"]
            if aid in by_id and aid not in cand and (by_id[aid][0]["reg_id"], by_id[aid][1]["label"]) not in used:
                cand[aid] = by_id[aid]
                if len(cand) >= SEARCH_K:
                    break
        if not cand:
            continue
        ids = list(cand)
        scores = rerank(f"{c['topic']} {c['why']}", [article_text(cand[i][1], 400) for i in ids])
        best = max(zip(scores, ids), key=lambda x: x[0])
        if best[0] < MIN_SCORE:
            continue
        r2, a2 = cand[best[1]]
        used.add((r2["reg_id"], a2["label"]))
        out.append({"category": "review", "reg_id": r2["reg_id"], "reg_name": r2["name"], "unit": "본문", "article": a2["label"], "para": 0,
                    "quote": a2["title"] or a2["label"], "context": " ".join(it["norm"] for it in a2["items"])[:400], "path": "가설(모델 추정)",
                    "target": f"{reg['name']} {old_art['label']}", "priority": "낮음", "handling": "판단 가능",
                    "reason": f"모델의 가설 — {c['why']} (검색 주제: {c['topic']}, 관련도 {best[0]:.2f}). 근거가 아니라 추정이므로 원문을 보고 판단"})
    return out
