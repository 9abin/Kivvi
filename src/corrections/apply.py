"""확정된 오탈자 정정을 검색 텍스트와 답변 안내에 적용한다.

원칙 (2026.10.06 합의)
  - 검색 텍스트(본문_검색): 확정된 정정을 반영한 글자로 만든다.
  - 답변 인용(본문): 원문 표기를 그대로 쓰고, 정정 안내를 함께 붙인다.
  - 후보·보류 항목은 적용하지 않는다.
  - 치환 쌍(치환전·치환후)이 없는 항목은 적용할 수 없으므로 건너뛰고 보고한다.
"""
import re

from src.chunk.articles import article_id


def is_confirmed(c):
    return str(c.get("상태", "")).startswith("확정")


def typo_pairs(corrections):
    """확정된 오탈자 치환 쌍을 고유번호별로 모은다."""
    pairs = {}
    for c in corrections:
        if c.get("종류") != "오탈자" or not is_confirmed(c):
            continue
        before, after = c.get("치환전"), c.get("치환후")
        if not before or after is None:
            continue
        pairs.setdefault(c["고유번호"], []).append((before, after, c["id"]))
    return pairs


def apply_search_text(rows, corrections):
    """각 청크에 '본문_검색'(정정 반영 글자)을 채운다. 원본 '본문'은 바꾸지 않는다."""
    pairs = typo_pairs(corrections)
    applied = []
    for r in rows:
        text = r["본문"]
        used = []
        for before, after, cid in pairs.get(r["고유번호"], []):
            if before in text:
                text = text.replace(before, after)
                used.append((cid, before, after))
        r["본문_검색"] = text
        if used:
            r["정정안내"] = [f"표기 정정: '{b}' → '{a}' ({cid})" for cid, b, a in used]
            applied.extend(used)
    return rows, applied


def answer_note(chunk):
    """답변에 붙일 정정 안내 문구 목록. 적용된 정정이 없으면 빈 목록."""
    return chunk.get("정정안내", [])


def _norm(s):
    return re.sub(r"\s+", "", s or "")


def apply_reference_corrections(rows, corrections):
    """확정된 참조·인용 정정을 청크의 cites에 반영한다.
    - 정정 항목의 원문이 들어 있는 청크 안에서, 원문 일부와 같은 인용만 바꾼다.
    - 대상ID가 있으면 대상을 그 값으로 바꾸고, 없으면 정정안내 문구만 남긴다."""
    applied = []
    for x in corrections:
        if not (x.get("종류", "").startswith(("참조", "인용")) and is_confirmed(x)):
            continue
        src = _norm(x.get("원문"))
        if not src:
            continue
        target = article_id(x["대상ID"]) if x.get("대상ID") else None  # 항 단위로 적힌 대상도 조 청크로 맞춘다
        for r in rows:
            if r["고유번호"] != x["고유번호"] or src not in _norm(r["본문"]):
                continue
            for c in r["cites"]:
                key = _norm(c["원문"])
                if not key or key not in src:
                    continue
                if not (c["상태"].startswith(("확인필요", "미해결")) or (target and c.get("대상") != target)):
                    continue
                if target:
                    c["대상"] = target
                c["상태"] = "정정 확정"
                c["정정ID"] = x["id"]
                r.setdefault("정정안내", []).append(f"참조 정정: '{c['원문']}' → {x.get('정정안', '')} ({x['id']})")
                applied.append((x["id"], r["id"], c["원문"]))
    return rows, applied


def apply_none_findings(rows, corrections):
    """'없음(확인 완료)' 항목은 해당 인용의 상태를 '근거 없음(확인 완료)'으로 바꾼다."""
    n = 0
    for x in corrections:
        if not str(x.get("상태", "")).startswith("없음") or not x.get("원문"):
            continue
        src = _norm(x["원문"])
        for r in rows:
            if r["고유번호"] != x["고유번호"]:
                continue
            for c in r["cites"]:
                key = _norm(c["원문"])
                if key and key in src and c["상태"].startswith(("확인필요", "옛 규정")):
                    c["상태"] = "옛 규정 참조(검토 완료)" if "옛 규정" in str(x.get("상태", "")) else "근거 없음(확인 완료)"
                    c["정정ID"] = x["id"]
                    n += 1
    return rows, n
