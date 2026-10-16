"""징계 사례의 조문 묶음을 조 → 조 링크 표로 만든다 (data/manual/case_links.json, 검색_설계.md 14절).

같은 사례(data/manual/discipline_cases.jsonl)에 함께 나온 조문끼리 양방향으로 잇는다. 사례 본문은 표에 넣지 않고
사례 id만 남긴다. 검색기는 CASE_LINKS=1일 때 찾은 조마다 이 표를 조회해 빠진 조문을 "같은 주제 조문(참고)"으로 붙인다.
대상 키는 "<고유번호>_<조항 첫 토큰>" (예: 48441_제17조, 23245_별표2). 별지 서식은 잇지 않는다.

실행: python scripts/build_case_links.py [--cases data/manual/discipline_cases.jsonl] [--chunks out/chunks.jsonl]
"""
import argparse
import json
import os
import sys
from collections import OrderedDict, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.search import topics  # noqa: E402

# 사례 유형·행위에서 주제어를 자동으로 못 잡거나 엉뚱하게 잡는 사례의 주제 (topics.json 주제 이름). 검색기는 질문에
# 이 주제어가 있을 때만 그 링크를 따라간다 (주제가 비면 항상 따라간다).
# 링크를 만들지 않는 사례와 이유 (2026.10.15 100문항 측정, 검색_설계.md 14.3)
EXCLUDE_CASES = {
    "D33": "징계 절차 요약(실제 사례 아님). 징계지침 조문 6개를 서로 이어 징계 질문마다 무관한 절차 조문(제7조·제16조의2)이 따라왔다",
    "D26": "대결 — 주제어가 범위어(문서·전결)뿐이라 게이트 없이 항상 따라왔고 Q051에서 도움이 안 됐다",
}
TOPIC_OVERRIDE = {
    "D12": ["전보·파견", "퇴직"],      # 인수인계서 (인사발령·퇴직)
    "D14": ["근무시간", "연차휴가"],    # 근태 점검
    "D16": ["이해충돌", "근무시간"],    # 주식거래 신고
    "D20": ["정보보안"],                # 정보시스템 관리자 실수
    "D22": ["계약", "연구용역"],       # 선집행 후승인
    "D26": [],                           # 대결 (범위어 문서·전결은 링크 안 함 → 항상 따라감)
}

MAX_REASON = 60


def article_key(row):
    return f"{row['고유번호']}_{row['조항'].split(' ')[0]}"


def case_topics(c, table):
    """사례의 주제어 이름 목록: 수동 지정이 있으면 그것, 없으면 유형+행위에서 사전으로 찾는다."""
    if c["id"] in TOPIC_OVERRIDE:
        return list(TOPIC_OVERRIDE[c["id"]])
    return topics.detect(f"{c['유형']} {c['행위']}", table)


def build(cases, rows, table=None):
    """{출발 키: [{"대상", "주제", "주제들", "사례유형", "등급", "이유", "사례"}]}. 같은 (출발, 대상) 쌍은 사례 id·주제를 합친다."""
    table = topics.load() if table is None else table
    links = defaultdict(OrderedDict)
    skipped = []
    for c in cases:
        if c["id"] in EXCLUDE_CASES:
            skipped.append((c["id"], "-", "제외: " + EXCLUDE_CASES[c["id"]][:30]))
            continue
        names = case_topics(c, table)
        items = OrderedDict()
        for x in c["조문"]:
            r = rows.get(x["chunk_id"])
            if r is None:
                skipped.append((c["id"], x["chunk_id"], "청크 없음"))
                continue
            if r["조항"].startswith("별지"):
                skipped.append((c["id"], x["chunk_id"], "별지 서식"))
                continue
            k = article_key(r)
            items.setdefault(k, x)  # 같은 조의 여러 항은 첫 인용만
        for src in items:
            for dst, x in items.items():
                if dst == src:
                    continue
                reason = " ".join(x.get("인용", "").split())
                if len(reason) > MAX_REASON:
                    reason = reason[:MAX_REASON].rstrip() + "…"
                link = links[src].get(dst)
                if link is None:
                    links[src][dst] = {"대상": dst, "주제": names[0] if names else "", "주제들": list(names),
                                       "사례유형": c["유형"], "등급": "핵심", "이유": reason, "사례": [c["id"]]}
                elif c["id"] not in link["사례"]:
                    link["사례"].append(c["id"])
                    link["주제들"] += [n for n in names if n not in link["주제들"]]
    table = OrderedDict()
    table["_설명"] = ("조 → 조 링크 표 (scripts/build_case_links.py가 discipline_cases.jsonl에서 만든다). 같은 징계·감사 사례에 "
                    "함께 나온 조문끼리 양방향으로 이었다. 주제들 = 사례의 주제어(topics.json 이름, 질문에 있을 때만 따라감, 비면 항상), 사례유형 = 사례 유형, 이유 = 대상 조문에서 사례가 짚은 문장, 사례 = 사례 id. "
                    "검색기(CASE_LINKS=1)는 찾은 조마다 조회만 하고 사례 본문은 쓰지 않는다. 사람이 뺄 것은 빼고 이유를 고친다.")
    for src in sorted(links):
        table[src] = list(links[src].values())
    return table, skipped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default=os.path.join(ROOT, "data", "manual", "discipline_cases.jsonl"))
    ap.add_argument("--chunks", default=os.path.join(ROOT, "out", "chunks.jsonl"))
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "manual", "case_links.json"))
    a = ap.parse_args()
    cases = [json.loads(l) for l in open(a.cases, encoding="utf-8") if l.strip()]
    rows = {}
    for l in open(a.chunks, encoding="utf-8"):
        r = json.loads(l)
        rows[r["id"]] = r
    table, skipped = build(cases, rows)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(table, f, ensure_ascii=False, indent=1)
    n_src = len(table) - 1
    n_links = sum(len(v) for k, v in table.items() if not k.startswith("_"))
    print(f"사례 {len(cases)}건 → 출발 조 {n_src}개, 링크 {n_links}개 → {a.out}")
    for s in skipped:
        print("  건너뜀:", *s)


if __name__ == "__main__":
    main()
