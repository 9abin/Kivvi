"""온톨로지를 단계별로 늘리면 새 탐색이 가능해지고 성능이 어떻게 변하는가 (영향 점검 평가셋 68, 개발 분할, 후보 상한 같음).

  uv run python scripts/exp_ontology_ladder_dev.py [--cap 20]
단계 (사전에 정한 순서, 결과를 보고 바꾸지 않는다):
  O0  인용(CITES)만                     수정된 조를 인용하는 곳 (이동 범위 확장은 모든 단계에 공통)
  O1  + 규정 단위 인용(CITES_REG)       규정 이름만 인용한 곳 (뒤쪽 순위)
  O2  + 같은 제목(SAME_TITLE)           같은 사항을 따로 정한 다른 규정의 조
  O3  + 용어 정의 · 사용(USES_TERM)     정의가 바뀌면 그 용어를 쓰는 곳
  O4  + 규정 ↔ 지침 짝, 법령 인용        (조 단위 후보를 더하지 않으면 재현율은 안 변한다)
  O5  + 임베딩 이웃                      관계로 안 이어진 비슷한 조 (AI 보강, 상한까지 채움)
각 단계는 그 관계를 kg.impact의 allowed로 켜서 만든다. 수정 종류별 경로는 그대로이고 쓸 수 있는 관계만 늘어난다.
"""
import argparse
import collections
import glob
import json
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")

from regcheck.impact_eval import Truth  # noqa: E402
from regcheck.kg import KG, moved_range  # noqa: E402

K = ROOT / "eval/impact_label_kit/산출"
V = ROOT / "out/vector/BAAI__bge-m3"
LADDER = [
    ("O0 인용만", {"CITES"}),
    ("O1 + 규정 단위 인용(제목어 필터)", {"CITES", "CITES_REG"}),
    ("O2 + 같은 제목(값 비교)", {"CITES", "CITES_REG", "SAME_TITLE"}),
    ("O3 + 규칙 추론 관계(기한·정의 불일치, 이동 후 미반영)", {"CITES", "CITES_REG", "SAME_TITLE", "VALUE_DIFF", "DEF_DIFF", "STALE_CITE"}),
    ("O4 + 용어 정의·사용", {"CITES", "CITES_REG", "SAME_TITLE", "VALUE_DIFF", "DEF_DIFF", "STALE_CITE", "USES_TERM"}),
    ("O5 + 제목 언급(별표·서식·부칙 포함)", {"CITES", "CITES_REG", "SAME_TITLE", "VALUE_DIFF", "DEF_DIFF", "STALE_CITE", "USES_TERM", "MENTIONS_TITLE"}),
    ("O6 + 서식 이름 언급·부속 공유", {"CITES", "CITES_REG", "SAME_TITLE", "VALUE_DIFF", "DEF_DIFF", "STALE_CITE", "USES_TERM", "MENTIONS_TITLE", "MENTIONS_FORM", "SHARED_ATTACH"}),
    ("O7 + 제목 줄기(표기 변형)", {"CITES", "CITES_REG", "SAME_TITLE", "VALUE_DIFF", "DEF_DIFF", "STALE_CITE", "USES_TERM", "MENTIONS_TITLE", "MENTIONS_FORM", "SHARED_ATTACH", "MENTIONS_STEM"}),
    ("O8 + 같은 대상어·값", {"CITES", "CITES_REG", "SAME_TITLE", "VALUE_DIFF", "DEF_DIFF", "STALE_CITE", "USES_TERM", "MENTIONS_TITLE", "MENTIONS_FORM", "SHARED_ATTACH", "MENTIONS_STEM", "SAME_VALUE"}),
    ("O9 + 조직 이름 공유", {"CITES", "CITES_REG", "SAME_TITLE", "VALUE_DIFF", "DEF_DIFF", "STALE_CITE", "USES_TERM", "MENTIONS_TITLE", "MENTIONS_FORM", "SHARED_ATTACH", "MENTIONS_STEM", "SAME_VALUE", "MENTIONS_ORG"}),
    ("O10 + 규정↔지침 짝·법령", {"CITES", "CITES_REG", "SAME_TITLE", "VALUE_DIFF", "DEF_DIFF", "STALE_CITE", "USES_TERM", "MENTIONS_TITLE", "MENTIONS_FORM", "SHARED_ATTACH", "MENTIONS_STEM", "SAME_VALUE", "MENTIONS_ORG", "SUPPLEMENTS", "CITES_LAW"}),
]


FULL = LADDER[-1][1]
LOO = [(f"전체에서 − {r}", FULL - {r}) for r in sorted(FULL)] + [("전체에서 − 이동 범위 확장", FULL)]


def jl(p):
    return [json.loads(l) for l in Path(p).read_text(encoding="utf-8").splitlines() if l.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap", type=int, default=20)
    a = ap.parse_args()
    regs = {r["reg_id"]: r for r in (json.loads(Path(p).read_text(encoding="utf-8")) for p in glob.glob(str(ROOT / "out/parsed/[0-9]*.json")))}
    nodes = {r["id"]: {k: v for k, v in r.items() if k != "id"} for r in jl(ROOT / "out/kg/nodes.jsonl")}
    kg = KG(nodes, jl(ROOT / "out/kg/edges.jsonl"))
    T = Truth(nodes, regs)
    item_text = {it["id"]: it["norm"] for r in regs.values() for x in r["articles"] for it in x["items"]}
    emb = np.load(V / "embeddings.npy"); emb = emb / np.linalg.norm(emb, axis=1, keepdims=True)
    art = np.array([c["article_id"] for c in jl(V / "chunks.jsonl")]); byart = collections.defaultdict(list)
    for j, x in enumerate(art):
        byart[x].append(j)

    def neigh(x):
        s = (emb @ emb[byart[x]].T).max(axis=1); best = {}
        for j, sc in enumerate(s):
            b = art[j]
            if b != x and sc > best.get(b, -1):
                best[b] = sc
        return [y for y, _ in sorted(best.items(), key=lambda t: -t[1])]

    ch = {r["id"]: r for r in jl(K / "changes.jsonl")}; fin = {r["id"]: r for r in jl(K / "labels_final.jsonl")}
    names = [n for n, _ in LADDER] + ["O11 + 임베딩 이웃(AI 보강)"] + [n for n, _ in LOO]
    hit = collections.Counter(); tot = 0; per = collections.defaultdict(dict); sizes = collections.Counter(); ctrl = collections.defaultdict(list)
    kinds = {}
    for i in sorted(ch):
        c = ch[i]; s = c["수정"]
        if c["분할"] != "개발":
            continue
        rid = T.by_name[s["규정"]]; st = T.start_of(s); kind = s["종류"]
        starts = moved_range(s["개정문"], rid, nodes) if kind == "번호 이동" else []
        hint = None
        if kind == "정의 변경":
            q = re.search(r'"([^"]+)"', s["개정문"]); want = re.sub(r"\s+", "", q.group(1))[:12] if q else ""
            hint = [t for t in kg.terms_defined_in(st) if want and want in re.sub(r"\s+", "", item_text.get(nodes[t]["item"], ""))] or kg.terms_defined_in(st)
        truth = {T.resolve(e) for e in fin[i]["영향"]} - {None}
        kinds[i] = kind
        nr = neigh(st)
        for n, allowed in LADDER + [("O11 + 임베딩 이웃(AI 보강)", None)] + LOO:
            al = allowed if allowed is not None else LADDER[-1][1]
            use_starts = [] if n.endswith("이동 범위 확장") else starts
            res = kg.impact_many(use_starts, kind, allowed=al) if len(use_starts) > 1 else kg.impact(st, kind, hint, allowed=al)
            ranked = [x for x, _ in sorted(res["candidates"].items(), key=lambda t: -t[1]["score"])]
            ranked += [x for x, v in sorted(res["reg_level"].items(), key=lambda t: -t[1]["score"]) if not x.startswith("REG:") and x not in ranked]
            if allowed is None:
                ranked = list(dict.fromkeys(ranked[:a.cap] + nr))
            top = ranked[:a.cap]
            if not truth:
                ctrl[n].append(len(top)); continue
            hit[n] += len(truth & set(top)); sizes[n] += len(top); per[n][i] = f"{len(truth & set(top))}/{len(truth)}"
        tot += len(truth)
    ks = " ".join(f"{k}:{v}" for k, v in kinds.items() if per[names[0]].get(k))
    print(f"[후보 상한 {a.cap}개/문항, 개발 5문항, 정답 {tot}건]  ({ks})")
    print(f"{'온톨로지 단계':28} {'재현율':>10} {'후보 중 정답':>10}   문항별 I01~I05                  | 영향 없음 문항 후보수(I06~I08)")
    prev = 0
    for n in names:
        if n.startswith("전체에서"):
            if prev is not None:
                print("\n[하나씩 빼는 실험] 전체(O10) 대비 재현율이 얼마나 떨어지나 — 그 관계가 꼭 필요했나")
                base = hit["O10 + 규정↔지침 짝·법령"]; prev = None
            h = hit[n]
            print(f"{n:28} {h:>4}/{tot} {h/tot:>4.0%} {h/sizes[n]:>9.0%}   {' '.join(per[n][k] for k in sorted(per[n])):30} | ({h-base:+d})")
            continue
        h = hit[n]
        print(f"{n:28} {h:>4}/{tot} {h/tot:>4.0%} {h/sizes[n]:>9.0%}   {' '.join(per[n][k] for k in sorted(per[n])):30} | {ctrl[n]}   (+{h-prev})")
        prev = h


if __name__ == "__main__":
    main()
