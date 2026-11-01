"""온톨로지 확장(6단계)을 기준선 결과 위에 얹었을 때의 효과. 검색은 다시 돌리지 않는다.

질문 -> 개념: 질문과 재작성 질문에 개념 이름이 글자 그대로 나오면 그 개념 (긴 이름 안에만 나오는 짧은 이름은 뺀다).
개념 -> 조: 그 개념에 대한 규정문(ABOUT) 또는 그 개념에 미치는 효과 규정문(SCOPE)이 있는 조.
비교하는 넓히기:
  개념        질문 개념만
  +종류       상위 1단계와 하위 전부 (BROADER)
  +관계       관계(RELATED)로 이어진 개념
  교차        질문 개념이 둘 이상이면, 그중 둘 이상에 걸친 조만 (규정문의 개념·영향개념, 또는 관계의 양 끝)
기준은 검색 최종 8개 + 인용 확장(CITES 정방향, CITES_ANNEX 양방향). 휴가·휴직 문항 = 정답 조가 온톨로지 범위(105조)에 하나라도 있는 질문.

  set -a; . ./.env; set +a
  python scripts/onto_expand_eval.py eval/onto_leave <결과 폴더> [<결과 폴더> ...] [--understand]
  --understand: 모델이 질문마다 개념과 면을 고르게 하고(이해), 그 면의 규정문이 있는 조만 더하는 것(이해+면)도 비교한다.
"""
import collections
import json
import os
import re
import statistics
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "scripts")
from graph_expand_eval import indexed_nodes  # noqa: E402
from graph_kinds_eval import BASE  # noqa: E402
from onto_build import norm, remap_tree  # noqa: E402
from src.combo import graph_expand as G  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")
RES = "results_qwen3-embedding-4b-q8_0_bge-reranker-v2-m3_qwen3-30b-a3b-instruct-2507-q4_k_m_S0.json"
EMB = "emb_qwen3-embedding-4b-q8_0.ids.json"


def node(aid):
    """조 ID 24010:4의3 -> 결과 파일의 노드 24010:제4조의3."""
    reg, no = aid.split(":")
    m = re.fullmatch(r"(\d+)(의\d+)?", no)
    return f"{reg}:제{m[1]}조{m[2] or ''}"


def load(onto):
    ex = json.load(open(os.path.join(onto, "extract.json"), encoding="utf-8"))
    # 합치기·트리는 없어도 된다 (규정문 검색만 쓰는 구축은 추출까지만 한다): 없으면 합치지 않은 이름, 빈 트리
    opt = lambda name, empty: json.load(open(os.path.join(onto, name), encoding="utf-8")) if os.path.exists(os.path.join(onto, name)) else empty  # noqa: E731
    rep = opt("normalize.json", {"rep": {}})["rep"]
    tree = remap_tree(opt("tree.json", {"parents": {}, "relations": []}), rep)
    scope = json.load(open(os.path.join(onto, "scope.json"), encoding="utf-8"))
    arts_of = collections.defaultdict(set)    # 개념 -> 조 노드
    facet_of = collections.defaultdict(set)   # (개념, 면) -> 조 노드. 효과 규정문은 영향개념 쪽에도 "효과"로 넣는다
    pairs_of = collections.defaultdict(set)   # 조 노드 -> 한 규정문·관계가 함께 묶은 개념 쌍
    for aid, v in ex.items():
        for s in v["규정문"]:
            c = rep.get(s["개념"], s["개념"])
            arts_of[c].add(node(aid))
            facet_of[(c, s["면"])].add(node(aid))
            if s.get("영향개념"):
                e = rep.get(s["영향개념"], s["영향개념"])
                arts_of[e].add(node(aid))
                facet_of[(e, "효과")].add(node(aid))
                pairs_of[node(aid)].add(frozenset((c, e)))
    for r in tree["relations"]:
        pairs_of[node(r["근거"])].add(frozenset((r["A"], r["B"])))
    kids = collections.defaultdict(set)
    for c, p in tree["parents"].items():
        kids[p].add(c)
    rel = collections.defaultdict(set)
    for r in tree["relations"]:
        rel[r["A"]].add(r["B"])
        rel[r["B"]].add(r["A"])
    names = collections.defaultdict(set)
    for n in rep:
        names[norm(n)].add(rep[n])
    for c in arts_of:
        names[norm(c)].add(c)
    alias = collections.defaultdict(set, {k: set(v) for k, v in names.items()})
    apath = os.path.join(onto, "aliases.json")
    if os.path.exists(apath):
        for c, v in json.load(open(apath, encoding="utf-8")).items():
            for x in v["다른이름"] + v["일상어"]:
                alias[norm(x)].add(rep.get(c, c))
    keep = lambda d: {k: v for k, v in d.items() if len(k) >= 2}  # noqa: E731
    return {"arts_of": arts_of, "facet_of": facet_of, "pairs_of": pairs_of, "parents": tree["parents"], "kids": kids, "rel": rel,
            "names": keep(names), "alias": keep(alias), "scope": {node(a) for a in scope["hit"] + scope["cited"]}}


def q_concepts(text, O, use_alias=False):
    t = norm(text)
    table = O["alias"] if use_alias else O["names"]
    hit = {k for k in table if k in t}
    hit = {x for x in hit if t.count(x) > sum(t.count(y) for y in hit if x != y and x in y)}
    return set().union(*(table[k] for k in hit)) if hit else set()


UNDERSTAND_PROMPT = """직원의 질문과 사내 규정 온톨로지의 개념 목록이다. 이 질문에 답하려면 규정에서 어떤 개념의 어떤 면을 찾아봐야 하는지 고른다.
- "개념": 목록에 있는 이름 그대로 1~5개. 질문이 직접 묻는 제도와, 그 제도가 영향을 주는 제도(지급, 평가, 기간 산정 등)를 함께 고른다. 목록에 맞는 것이 없으면 빈 배열.
- "면": 그 개념에서 찾아야 할 면. 다음 중에서 고른다: {facets}
JSON 하나만: {{"찾을것": [{{"개념": "이름", "면": ["기간·시기·횟수"]}}]}}"""


def understand(qs, O, path):
    """모델이 질문마다 개념과 면을 고른다 (질문 시점에 하는 일이라 평가 누수가 아니다). 결과는 path에 캐시한다."""
    from onto_build import FACETS, ask
    done = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
    known = sorted(O["arts_of"])
    alias_of = collections.defaultdict(list)
    for k, cs in O["alias"].items():
        for c in cs:
            if norm(c) != k and len(alias_of[c]) < 3:
                alias_of[c].append(k)
    listing = "\n".join(f"- {c}" + (f" (=" + ", ".join(alias_of[c]) + ")" if alias_of[c] else "") for c in known)
    system = UNDERSTAND_PROMPT.format(facets=", ".join(FACETS))
    for x in qs:
        if x["id"] in done:
            continue
        try:
            d = json.loads(ask(system, f"질문: {x['q']}\n\n개념 목록:\n{listing}", num_predict=400, fmt="json"))
        except ValueError:
            d = {}
        done[x["id"]] = [{"개념": str(t.get("개념")), "면": [f for f in (t.get("면") or []) if f in FACETS]}
                         for t in d.get("찾을것") or [] if isinstance(t, dict) and t.get("개념") in O["arts_of"]]
        json.dump(done, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return done


def understood_nodes(picks, O, mode):
    out = set()
    for p in picks:
        cs = {p["개념"]} | (descendants(p["개념"], O["kids"]) if mode == "이해+면+종류" else set())
        for c in cs:
            if mode == "이해" or not p["면"]:
                out |= O["arts_of"].get(c, set())
            else:
                for f in p["면"]:
                    out |= O["facet_of"].get((c, f), set())
    return out


def descendants(c, kids):
    out, todo = set(), [c]
    while todo:
        for k in kids.get(todo.pop(), ()):
            if k not in out:
                out.add(k)
                todo.append(k)
    return out


def widen(cs, O, mode):
    out = set(cs)
    if mode in ("+종류", "+관계"):
        for c in cs:
            out |= descendants(c, O["kids"])
            if c in O["parents"]:
                out.add(O["parents"][c])
    if mode == "+관계":
        for c in list(out):
            out |= O["rel"].get(c, set())
    return out


def onto_nodes(cs, O, mode):
    if mode == "교차":
        if len(cs) < 2:
            return set().union(*(O["arts_of"].get(c, set()) for c in cs)) if cs else set()
        return {n for n, ps in O["pairs_of"].items() if any(len(p & cs) == 2 for p in ps)}
    return set().union(*(O["arts_of"].get(c, set()) for c in widen(cs, O, mode))) if cs else set()


def main(onto, dirs):
    O = load(onto)
    edges = G.load_edges()
    for d in dirs:
        qs = json.load(open(os.path.join(d, RES), encoding="utf-8"))["questions"]
        qs = list(qs.values()) if isinstance(qs, dict) else qs
        idx = indexed_nodes(os.path.join(d, EMB))
        ex = G.Expander(edges, idx, BASE)
        leave = [x for x in qs if set(x["stage_nodes"]["core"]) & O["scope"]]
        und = understand(qs, O, os.path.join(onto, f"understand_{os.path.basename(os.path.normpath(d))}.json")) if UNDERSTAND else {}
        print(f"\n## {os.path.basename(os.path.normpath(d))}: 질문 {len(qs)}개, 휴가·휴직 문항 {len(leave)}개")
        print("  넓히기     | 휴가·휴직 문항: 전부 찾음 / 정답 조 / 더한 조 평균 | 전체: 전부 찾음 / 정답 조 / 더한 조 평균")
        labels = ["기준", "개념", "+종류", "+관계", "교차", "개념+별칭", "+종류+별칭", "+관계+별칭", "교차+별칭"]
        labels += ["이해", "이해+면", "이해+면+종류"] if UNDERSTAND else []
        for label in labels:
            mode, use_alias = label.replace("+별칭", ""), label.endswith("+별칭")
            row = []
            for group in (leave, qs):
                ok = got = tot = 0
                adds = []
                for x in group:
                    core = set(x["stage_nodes"]["core"])
                    base = set(x["final"]["8"]["nodes"])
                    base |= ex.expand(base)
                    add = set()
                    if mode.startswith("이해"):
                        add = (understood_nodes(und.get(x["id"], []), O, mode) & idx) - base
                    elif mode != "기준":
                        add = (onto_nodes(q_concepts(x["q"] + " " + (x.get("rewritten") or ""), O, use_alias), O, mode) & idx) - base
                    full = base | add
                    ok += core <= full
                    got += len(core & full)
                    tot += len(core)
                    adds.append(len(add))
                row.append(f"{ok:2}/{len(group)} / {got}/{tot} / {statistics.mean(adds) if adds else 0:5.1f}")
            print(f"  {label:10} | {row[0]:28} | {row[1]}")
        print("  휴가·휴직 문항별 (기준에서 놓친 정답 -> 개념/+종류/+관계로 찾은 것, 질문 개념 / 별칭으로 더 잡힌 개념):")
        for x in leave:
            core = set(x["stage_nodes"]["core"])
            base = set(x["final"]["8"]["nodes"])
            base |= ex.expand(base)
            miss = core - base
            if not miss:
                continue
            text = x["q"] + " " + (x.get("rewritten") or "")
            cs, ca = q_concepts(text, O), q_concepts(text, O, True)
            found = {m: [n for n in miss if n in onto_nodes(ca, O, m) & idx] for m in ("개념", "+종류", "+관계")}
            print(f"    {x['id']}: 놓침 {sorted(miss)} | 별칭 포함 개념 {found['개념']} +종류 {found['+종류']} +관계 {found['+관계']} | {sorted(cs)} / {sorted(ca - cs)}")
            if UNDERSTAND:
                picks = und.get(x["id"], [])
                got = {m: [n for n in miss if n in understood_nodes(picks, O, m)] for m in ("이해", "이해+면")}
                print(f"      이해 {got['이해']} 이해+면 {got['이해+면']} | 고른 것 {[(p['개념'], p['면']) for p in picks]}")


UNDERSTAND = "--understand" in sys.argv

if __name__ == "__main__":
    args = [x for x in sys.argv[1:] if x != "--understand"]
    main(args[0], args[1:])
