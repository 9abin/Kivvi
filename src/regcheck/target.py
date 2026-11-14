"""개정 위치 찾기: 「출장비 기준을 올리고 싶어」처럼 규정·조를 모르는 요청 → 고칠 후보 조문 (사람이 고른다).

  find_targets(text, regs, index, embed, rerank, rewrite=None, k=3)
    → {"candidates": [{reg_id, reg_name, article_id, label, title, score, how}], "query", "rewritten", "confident"}

  1. 요청에 규정 이름이 있으면 그 규정의 조만, 없으면 90개 규정 전체 벡터 검색 상위 SEARCH_K개 조
  2. reranker로 요청과 조문을 다시 비교해 순위
  3. 1위 점수가 MIN_SCORE 아래면 rewrite(요청)으로 규정 용어에 가깝게 바꿔 한 번 더 찾고 더 나은 쪽을 쓴다 (재검색)
  4. 그래도 낮으면 confident=False → 화면은 「후보가 확실하지 않음, 규정이나 조를 적어 주세요」
위치를 잘못 고르면 그 뒤가 다 틀리므로 후보는 제시만 하고 고르는 것은 사람이다 (근거 종류: 모델 추정).
"""

from __future__ import annotations

from .relocate import article_text

SEARCH_K = 25       # 화면 응답 시간 (8GB GPU를 채팅·로컬 LLM과 나눠 씀, 261015 측정 22초 → 줄임)
TEXT_CHARS = 800    # 리랭커에 넘기는 조문 글자 수. 400 → 800: Qwen3-4B 색인과 함께 조 @3 62% → 69% (리포트 55), 요청당 0.19 → 0.28초
MIN_SCORE = 0.3
RERANK_REG_NAME = False   # 리랭커에 넘기는 조문 앞에 규정 이름을 붙일지 (시험 중, scripts/eval_target_rag.py --rerank-reg)
# 순서 매기기 (리포트 56): 리랭커 순위와 임베딩 순위를 같은 무게의 RRF로 섞고, 요청이 말하지 않은 대상 전용 규정은 뒤로.
# 리랭커만일 때 조 @1 38/90 → 49/90 (+12.2%p, 95% 구간 [+4.4, +21.1]), @3 62 → 67. 30B로 다시 매기기는 효과 없음(@3 62 → 62, 3초)
FUSE_VECTOR = 1.0   # 임베딩 순위의 무게 (0이면 리랭커 순서만). 데이터에 맞춰 고르지 않은 표준값(같은 무게)
RRF_K = 60
# 규정 이름에 이 낱말이 있으면 그 대상 전용 규정. 요청에 대응 낱말이 하나도 없으면 후보를 뒤로 민다 (「결혼 특별휴가」 → 해외 파견 지침 특별휴가가 1위이던 문제)
AUDIENCE = {"해외": ("해외", "외국", "국외", "파견", "현지", "주재", "미국", "중국", "일본", "베트남", "싱가포르"), "파견": ("파견", "해외", "주재"),
            "공무직": ("공무직",), "임원": ("임원", "이사", "감사", "대표이사", "상임"), "계약직": ("계약직", "전문계약", "기간제")}


def _other_audience(reg_name: str, query: str) -> bool:
    groups = [g for g in AUDIENCE if g in reg_name]
    return bool(groups) and not any(w in query for g in groups for w in AUDIENCE[g])


def _named_reg(text: str, regs: dict) -> dict | None:
    """요청에 적힌 규정 (정확한 이름, 없으면 하나로 좁혀지는 비슷한 이름 — 「법인카드 지침」, 리포트 56)."""
    from .nl import find_regulation
    return find_regulation(regs, text)


def _search(query: str, regs: dict, index, embed, rerank, reg: dict | None, k: int) -> list[dict]:
    import numpy as np

    arts: dict[str, tuple[dict, dict]] = {}
    if reg:
        arts = {a["id"]: (reg, a) for a in reg["articles"] if not a.get("deleted")}
    else:
        vecs, chunks = index
        q = embed([query])[0]
        by_id = {a["id"]: (r, a) for r in regs.values() for a in r["articles"] if not a.get("deleted")}
        for i in np.argsort(-(vecs @ q)):
            aid = chunks[i]["article_id"]
            if aid in by_id and aid not in arts:
                arts[aid] = by_id[aid]
                if len(arts) >= SEARCH_K:
                    break
    if not arts:
        return []
    ids = list(arts)
    texts = [article_text(arts[i][1], TEXT_CHARS) for i in ids]
    if RERANK_REG_NAME:
        texts = [f"{arts[i][0]['name']} {t}" for i, t in zip(ids, texts)]
    scores = [float(x) for x in rerank(query, texts)]
    rrank = {aid: n for n, aid in enumerate(sorted(ids, key=lambda i: -scores[ids.index(i)]), 1)}
    fuse = 0.0 if reg else FUSE_VECTOR   # 규정을 지정했으면 ids가 조 순서라 임베딩 순위가 없다
    key = {aid: 1 / (RRF_K + rrank[aid]) + fuse / (RRF_K + n) for n, aid in enumerate(ids, 1)}
    order = sorted(ids, key=lambda i: -key[i])
    if not reg:
        order = [i for i in order if not _other_audience(arts[i][0]["name"], query)] + [i for i in order if _other_audience(arts[i][0]["name"], query)]
    out = []
    for aid in order[:k]:
        r, a = arts[aid]
        out.append({"reg_id": r["reg_id"], "reg_name": r["name"], "article_id": aid, "label": a["label"], "title": a["title"],
                    "score": round(scores[ids.index(aid)], 3), "vrank": ids.index(aid) + 1})   # score: 리랭커 점수, vrank: 임베딩 검색 순위
    return out


def _best(cands: list[dict]) -> float:
    """후보 중 가장 높은 리랭커 점수 (순서를 섞었으므로 1위의 점수가 아니라 최댓값으로 확실함을 판단)."""
    return max((c["score"] for c in cands), default=0.0)


def find_targets(text: str, regs: dict, index, embed, rerank, rewrite=None, k: int = 3) -> dict:
    reg = _named_reg(text, regs)
    cands = _search(text, regs, index, embed, rerank, reg, k)
    for c in cands:
        c["how"] = "요청 그대로 검색" + (f" (「{reg['name']}」 안)" if reg else "")
    rewritten = ""
    if rewrite and (not cands or _best(cands) < MIN_SCORE):
        try:
            rewritten = (rewrite(text) or "").strip()
        except Exception:   # 바꿔 쓰기가 실패해도 처음 결과는 보여 준다
            rewritten = ""
        if rewritten and rewritten != text:
            # 바꿔 쓴 검색어만 쓰면 원래 낱말을 잃을 수 있다 (「출장 숙박비」 → 「출장비 기준 조정」에서 숙박이 빠짐, 261015) → 둘을 합쳐 찾는다
            again = _search(f"{text} {rewritten}", regs, index, embed, rerank, reg, k)
            if again and (not cands or _best(again) > _best(cands)):
                cands = again
                for c in cands:
                    c["how"] = f"규정 용어로 바꿔 다시 검색: 「{rewritten}」"
    return {"candidates": cands, "query": text, "rewritten": rewritten,
            "confident": bool(cands) and _best(cands[:3]) >= MIN_SCORE}
