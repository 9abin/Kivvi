"""그래프 확장 상한: 정답 근거 중 하나를 찾았다고 가정할 때, 관계 표(amend.edge)로 다른 정답 근거에 닿는 비율.

  uv run python scripts/eval_graph_reach.py --gold C:/Users/Admin/Downloads/answer_eval_100.jsonl
검색(가빈 파이프라인)을 돌리지 않고 정답 근거 사이의 관계만 본다 — 「그래프로 근거를 넓히면 최대 얼마나 되는가」의 위쪽 한계.
  · 정답 근거를 조(규정ID:번호)·별표(규정ID:별표N) 단위로 바꾼다 (항은 같은 조로 묶임)
  · 이동: 인용하는 쪽(out, 지금 가빈 조립이 하는 것) / 인용받는 쪽(in, 안 하는 것) / 문서 인용·위임(그 규정 전체) / 같은 규정
  · 시작점은 정답 근거 중 어느 하나: 가장 유리한 하나(best), 아무거나 하나(평균)
「닿는다」는 후보에 들어간다는 뜻이지 순위가 높다는 뜻이 아니다. 비용(이웃 수)도 함께 본다.
"""

import argparse
import collections
import json
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def jl(p):
    return [json.loads(l) for l in Path(p).read_text(encoding="utf-8").splitlines() if l.strip()]


def node_of(reg_id: str, 조항: str) -> str | None:
    if m := re.match(r"별표\s*(\d+)", 조항):
        return f"{reg_id}:별표{m.group(1)}"
    if m := re.match(r"제\s*(\d+)\s*조(?:\s*의\s*(\d+))?", 조항):
        return f"{reg_id}:{m.group(1)}" + (f"의{m.group(2)}" if m.group(2) else "")
    return None


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", required=True)
    a = ap.parse_args()
    names = {}
    titles = {}                                   # 조 id → 제목
    appx = collections.defaultdict(set)           # 규정ID → 별표·별지 id
    articles = collections.defaultdict(set)
    for p in (ROOT / "out" / "parsed").glob("[0-9]*.json"):
        r = json.loads(p.read_text(encoding="utf-8"))
        names[r["name"]] = r["reg_id"]
        articles[r["reg_id"]] = {x["id"] for x in r["articles"]} | {x["id"] for x in r.get("appendices", [])}
        appx[r["reg_id"]] = {x["id"] for x in r.get("appendices", [])}
        for x in r["articles"]:
            if x["title"] and not x["deleted"]:
                titles[x["id"]] = re.sub(r"\s", "", x["title"])
    by_title = collections.defaultdict(set)
    for aid, t in titles.items():
        by_title[t].add(aid)
    rels = jl(ROOT / "out" / "relations" / "relations.jsonl")
    out_, in_, doc = collections.defaultdict(set), collections.defaultdict(set), collections.defaultdict(set)
    for e in rels:
        if e["src_kind"] == "부칙":
            continue
        s, t = e["src_article"], e["target"]
        if e["target_type"] in ("article", "appendix"):
            out_[s].add(t)
            in_[t].add(s)
        elif e["target_type"] == "regulation":
            doc[s].add(t)          # 그 규정 전체를 가리킴

    gold = jl(a.gold)
    unmapped = collections.Counter()
    qs = []
    for g in gold:
        nodes = []
        for ev in g["근거"]:
            rid = names.get(ev["규정"])
            n = node_of(rid, ev["조항"]) if rid else None
            if not n or n not in articles.get(rid, ()):
                unmapped["규정 못 찾음" if not rid else ("번호 못 읽음" if not n else "조 없음")] += 1
                continue
            if n not in nodes:
                nodes.append(n)
        if len(nodes) >= 2:
            qs.append((g["id"], nodes))
    total_items = sum(len(g["근거"]) for g in gold)
    print(f"문항 {len(gold)} · 정답 근거 {total_items}개 → 조·별표 단위로 읽은 것 {sum(len(n) for _, n in qs) + sum(1 for g in gold if False)} (근거 2곳 이상인 문항 {len(qs)}개만 계산) · 못 읽은 근거 {dict(unmapped)}")

    def reach(seed: str, hops: int, use_out: bool, use_in: bool, use_doc: bool = False, same_reg: bool = False,
              same_appx: bool = False, same_title: bool = False) -> set[str]:
        seen, frontier = {seed}, {seed}
        for _ in range(hops):
            nxt = set()
            for n in frontier:
                if use_out:
                    nxt |= out_[n]
                if use_in:
                    nxt |= in_[n]
                if use_doc:
                    for rid in doc[n]:
                        nxt |= articles.get(rid, set())
                if same_reg:
                    nxt |= articles.get(n.split(":")[0], set())
                if same_appx:
                    nxt |= appx.get(n.split(":")[0], set())
                if same_title and n in titles:
                    nxt |= by_title[titles[n]]
            frontier = nxt - seen
            seen |= nxt
        return seen

    modes = [
        ("① 인용하는 쪽 1단계 (지금 가빈 조립이 하는 것)", dict(hops=1, use_out=True, use_in=False)),
        ("② ① + 인용받는 쪽 1단계 (역방향)", dict(hops=1, use_out=True, use_in=True)),
        ("③ ② + 문서 인용·위임 (그 규정 전체)", dict(hops=1, use_out=True, use_in=True, use_doc=True)),
        ("④ ② 를 2단계까지", dict(hops=2, use_out=True, use_in=True)),
        ("⑤ ③ + 같은 규정의 별표·별지", dict(hops=1, use_out=True, use_in=True, use_doc=True, same_appx=True)),
        ("⑥ ⑤ + 다른 규정의 같은 제목 조", dict(hops=1, use_out=True, use_in=True, use_doc=True, same_appx=True, same_title=True)),
        ("(참고) 같은 규정 전체", dict(hops=1, use_out=False, use_in=False, same_reg=True)),
    ]
    n_other = sum(len(n) - 1 for _, n in qs)
    print(f"\n근거 2곳 이상인 문항 {len(qs)}개, 그 안의 「나머지 근거」(시작점이 아닌 것) 평균 기준 {n_other / max(len(qs), 1):.2f}개/문항\n")
    print("방법\t평균(아무거나 하나로 시작)\t가장 유리한 하나로 시작\t이웃 수(평균)")
    rows = {}
    for label, kw in modes:
        avg, best, fan = [], [], []
        for qid, nodes in qs:
            per_seed = []
            for s in nodes:
                r = reach(s, **kw)
                others = [x for x in nodes if x != s]
                per_seed.append(sum(x in r for x in others) / len(others))
                fan.append(len(r) - 1)
            avg.append(statistics.mean(per_seed))
            best.append(max(per_seed))
        rows[label] = (statistics.mean(avg), statistics.mean(best), statistics.mean(fan))
        print(f"{label}\t{rows[label][0]:.0%}\t{rows[label][1]:.0%}\t{rows[label][2]:.1f}")
    # 어떤 근거에도 닿지 못하는 문항
    # 나머지 근거가 어디에 있나: 같은 규정 / 다른 규정, 본문 조 / 별표
    where = collections.Counter()
    for qid, nodes in qs:
        for s0 in nodes:
            for x in nodes:
                if x != s0:
                    where[("같은 규정" if x.split(":")[0] == s0.split(":")[0] else "다른 규정", "별표" if "별표" in x else "본문 조")] += 1
    tot = sum(where.values()) or 1
    print("\n나머지 근거의 위치 (시작점마다 센 것):",{f"{k[0]}·{k[1]}": f"{v / tot:.0%}" for k, v in where.items()})
    kw = modes[2][1]
    stuck = [qid for qid, nodes in qs if not any(all(x in reach(s, **kw) for x in nodes if x != s) for s in nodes)]
    print(f"\n③으로도 어느 시작점에서든 나머지 근거를 다 못 모으는 문항: {len(stuck)}/{len(qs)}")
    out = ROOT / "out" / "eval" / "graph_reach.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"문항": len(qs), "방법별": {k: list(v) for k, v in rows.items()}, "못 모으는 문항": stuck, "못 읽은 근거": dict(unmapped)},
                              ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
