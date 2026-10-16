"""주제 링크 확장 (data/manual/topics.json, `docs/검색_설계.md` 12절).

같은 주제어("건강검진", "연차")를 담은 조문이 여러 규정에 흩어져 있을 때, 질문에 그 주제어가 있으면 흩어진 조문을
모아 리랭커로 다시 채점해 상위 몇 개를 "같은 주제 조문(참고)"으로 붙인다 (Searcher._related).
인용(`cites`)은 "제N조"로 가리킨 조문만 따라가므로, 복지후생 운영지침 제3조(건강진단 비용)와 복무규정 제25조(공가,
법정 건강검진 시간)처럼 서로 가리키지 않는 조문은 이 길로만 이어진다.

원칙
- 그래프 DB를 두지 않는다. 주제 → 조문 표는 Searcher가 색인을 읽을 때 메모리에 만든다 (build_index).
  조 단위·항 단위 색인 어느 쪽이든 그 색인의 행을 그대로 훑으므로 재색인이 필요 없다.
- 사전의 terms는 규정 원문에 실제로 있는 말만 둔다. 구어체는 synonyms.json·GPT 바꿔쓰기가 담당한다.
- 공백을 지운 뒤 부분일치로 찾는다 ("건강 검진" = "건강검진"). except는 지우고 나서 센다 ("유지보수"의 "보수").
- 링크=false 주제는 범위어(거의 모든 규정에 나오는 말)라 감지·확장 모두에서 뺀다.
- 후보 순위는 원래 질문으로 리랭크한 점수다 (주제어로 채점하거나 둘을 섞으면 11문항 모두에서 더 나빴다, 2026.10.14).
  질문이 직접 묻지 않는 보충 조문(건강검진 비용 질문의 "검진 시간은 공가")은 점수로 올라오지 않으므로,
  사람이 사전의 "함께"에 적어 두면 점수와 무관하게 먼저 붙인다 (위임 조문을 cites에 사람이 넣는 것과 같은 원칙).
"""
import json
import os
import re

from config import RELATED_ARTICLES, TOPICS

_WS = re.compile(r"\s+")
_TITLE = re.compile(r"^제\d+조(?:의\d+)?\((.+?)\)")


def squash(text):
    """공백을 모두 지운다. 사전과 본문을 같은 모양으로 맞추기 위해서다."""
    return _WS.sub("", text or "")


def load(path=TOPICS):
    """사전을 읽어 {주제: {"terms", "except", "링크", "묶음"}}로 돌려준다. 파일이 없으면 빈 사전."""
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    table = {}
    for name, spec in raw.items():
        if name.startswith("_") or not isinstance(spec, dict):
            continue
        terms = [squash(t) for t in spec.get("terms", [])]
        table[name] = {
            "terms": [t for t in terms if t],
            "except": [squash(t) for t in spec.get("except", []) if squash(t)],
            "링크": bool(spec.get("링크", True)),
            "묶음": spec.get("묶음", ""),
            # 함께 볼 조문: [{"대상": "<고유번호>_제N조", "이유": "..."}]. 점수와 무관하게 먼저 붙인다 (Searcher._related)
            "함께": [p for p in spec.get("함께", []) if isinstance(p, dict) and p.get("대상")],
        }
    return table


def load_related(path=RELATED_ARTICLES):
    """조 → 연관 조 표 (TOPIC_MODE="article"). 형식: {"<고유번호>_제N조": [{"대상": "<고유번호>_제M조", "주제": "건강검진",
    "등급": "핵심"|"참고", "이유": "..."(선택)}, ...]}. "_"로 시작하는 키는 설명이다. 파일이 없으면 빈 표."""
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return {k: [x for x in v if isinstance(x, dict) and x.get("대상")]
            for k, v in raw.items() if not k.startswith("_") and isinstance(v, list)}


def _clean(squashed, spec):
    for x in spec["except"]:
        squashed = squashed.replace(x, "")
    return squashed


def detect(text, table):
    """질문(과 바꿔 쓴 검색어)에 들어 있는 링크용 주제 이름 목록. 사전 순서를 지킨다."""
    s = squash(text)
    found = []
    for name, spec in table.items():
        if not spec["링크"]:
            continue
        cleaned = _clean(s, spec)
        if any(t in cleaned for t in spec["terms"]):
            found.append(name)
    return found


def mentions(body, spec, squashed=None):
    """본문이 주제어를 담고 있으면 (제목 일치 여부, 등장 횟수), 없으면 None.
    제목 일치 = 본문 첫머리 '제N조(…)' 괄호 안에 주제어가 있음. 리랭커에 넘길 후보를 고를 때 앞세운다."""
    s = _clean(squashed if squashed is not None else squash(body), spec)
    n = sum(s.count(t) for t in spec["terms"])
    if n == 0:
        return None
    m = _TITLE.match(s)
    title = m.group(1) if m else ""
    return (any(t in title for t in spec["terms"]), n)


def build_index(rows, table):
    """색인 행 목록(chunk_id, body_search/body)에서 주제 → 청크 표와 청크 → 주제 표를 만든다.
    반환: ({주제: [chunk_id, ...]}, {chunk_id: {주제: (제목일치, 횟수)}}). 링크=false 주제는 넣지 않는다."""
    linked = {name: spec for name, spec in table.items() if spec["링크"]}
    topic_chunks = {name: [] for name in linked}
    chunk_topics = {}
    for r in rows:
        body = r.get("body_search") or r.get("body") or ""
        squashed = squash(body)
        if not squashed:
            continue
        for name, spec in linked.items():
            hit = mentions(body, spec, squashed)
            if hit:
                topic_chunks[name].append(r["chunk_id"])
                chunk_topics.setdefault(r["chunk_id"], {})[name] = hit
    return topic_chunks, chunk_topics


def rank_candidates(chunk_ids, chunk_topics, found, limit):
    """후보 청크를 (제목이 주제어인 주제 수, 주제어 등장 횟수 합) 내림차순으로 정렬해 앞 limit개만 남긴다.
    리랭커 비용을 후보 단계와 같은 수준으로 묶기 위한 사전 정렬이다. 최종 순위는 리랭커가 정한다."""
    def key(cid):
        hits = [chunk_topics.get(cid, {}).get(t) for t in found]
        hits = [h for h in hits if h]
        return (sum(1 for h in hits if h[0]), sum(h[1] for h in hits))
    return sorted(chunk_ids, key=key, reverse=True)[:limit]
