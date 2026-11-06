"""온톨로지(스키마) 자기 점검: 전문가 없이 코드로 확인할 수 있는 것만.
  uv run python scripts/check_ontology.py
스키마 정의(kg.ONTOLOGY)와 실제 그래프 파일 · 규칙 레지스트리 · 개정 분류 · 주제 층 사전이 서로 맞는지 본다. 맞다 틀리다의 판정은 하지 않고 어긋난 곳을 센다.
"""
import collections
import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")
from regcheck import ruleset  # noqa: E402
from regcheck.kg import ONTOLOGY, RULE_REL  # noqa: E402


def jl(p):
    return [json.loads(x) for x in Path(p).read_text(encoding="utf-8").splitlines() if x.strip()]


def main():
    nodes = {n["id"]: n for n in jl(ROOT / "out/kg/nodes.jsonl")}
    edges = jl(ROOT / "out/kg/edges.jsonl")
    cls, rel = ONTOLOGY["classes"], ONTOLOGY["relations"]
    out = []

    def sec(t):
        out.append(f"\n## {t}")

    sec("1. 스키마 대 데이터")
    kinds = collections.Counter(e["type"] for e in edges)
    ntypes = collections.Counter(n["type"] for n in nodes.values())
    out.append(f"노드 {len(nodes)}개 {dict(ntypes)} / 엣지 {len(edges)}개 {len(kinds)}종")
    out.append(f"정의에 없는 노드 종류: {sorted(set(ntypes) - set(cls)) or '없음'}")
    out.append(f"정의에 없는 관계 종류: {sorted(set(kinds) - set(rel)) or '없음'}")
    out.append(f"정의는 있으나 엣지가 0개인 관계: {sorted(set(rel) - set(kinds)) or '없음'}")
    out.append(f"노드가 0개인 클래스: {sorted(set(cls) - set(ntypes)) or '없음'}")

    sec("2. 엣지 무결성")
    dangling = [e for e in edges if e["src"] not in nodes or e["dst"] not in nodes]
    selfloop = [e for e in edges if e["src"] == e["dst"]]
    dup = [k for k, v in collections.Counter((e["type"], e["src"], e["dst"]) for e in edges).items() if v > 1]
    dup_struct = [k for k in dup if k[0] == "PART_OF"]
    out.append(f"끊어진 엣지(노드 없음): {len(dangling)} {collections.Counter(e['type'] for e in dangling).most_common(4)}")
    out.append(f"자기 자신으로 가는 엣지: {len(selfloop)} {collections.Counter(e['type'] for e in selfloop).most_common(4)}")
    out.append(f"같은 (종류, 출발, 도착) 중복: {len(dup)} (인용 · 값 엣지의 중복은 근거가 여럿인 것으로 봄). 구조 엣지(PART_OF) 중복 {len(dup_struct)}개는 같은 조 번호가 둘인 경우로 원본 번호 오류 후보")
    viol = collections.Counter()
    ex = {}
    for e in edges:
        d = rel.get(e["type"])
        if not d or e["src"] not in nodes or e["dst"] not in nodes:
            continue
        a, b = nodes[e["src"]]["type"], nodes[e["dst"]]["type"]
        if a not in d["from"].split("|") or b not in d["to"].split("|"):
            viol[e["type"]] += 1
            ex.setdefault(e["type"], (a, b))
    out.append(f"정의한 출발 · 도착 클래스를 벗어난 엣지: {sum(viol.values())} {[(k, v, ex[k]) for k, v in viol.most_common(5)]}")
    sym = [r for r, d in rel.items() if d.get("symmetric")]
    for r in sym:
        s = {(e["src"], e["dst"]) for e in edges if e["type"] == r}
        out.append(f"대칭 관계 {r}: 한쪽만 저장된 엣지 {sum(1 for a, b in s if (b, a) not in s)} / {len(s)}")

    sec("3. 그래프 형태")
    deg = collections.Counter()
    for e in edges:
        deg[e["src"]] += 1
        deg[e["dst"]] += 1
    iso = [n for n in nodes if deg[n] == 0]
    out.append(f"엣지가 하나도 없는 노드: {len(iso)} {collections.Counter(nodes[n]['type'] for n in iso).most_common(5)}")
    top = deg.most_common(5)
    out.append("차수가 큰 노드: " + ", ".join(f"{nodes[n].get('label') or nodes[n].get('name')}({nodes[n]['type']}) {d}" for n, d in top))

    sec("4. 정의 내부 일관성")
    paths = ONTOLOGY["change_paths"]
    used = {r for ps in paths.values() for r, _, _ in ps}
    out.append(f"change_paths가 정의에 없는 관계를 씀: {sorted(used - set(rel)) or '없음'}")
    out.append(f"어떤 개정 종류의 경로에도 쓰이지 않는 관계: {sorted(set(rel) - used) or '없음'}")
    og = {r for rs in ONTOLOGY['origin'].values() for r in rs}
    out.append(f"origin 표시가 없는 관계: {sorted(set(rel) - og) or '없음'}")

    sec("5. 규칙 레지스트리 대 그래프")
    for r in ruleset.load_rules():
        if r["산출"] != "엣지":
            continue
        kind = r["관계"]
        outside = ONTOLOGY.get("rules_outside_graph", {})
        out.append(f"{r['id']} ({kind}): " + (f"그래프의 {RULE_REL[kind]}로 연결됨" if kind in RULE_REL else ("그래프에 넣지 않음(정의에 명시: " + outside[r['id']] + ")" if r["id"] in outside else "**그래프에도 연결되지 않고 정의에도 없음**")))
    out.append("사용 안 함 규칙: " + ", ".join(r["id"] for r in json.loads((ROOT / "data/manual/rules.json").read_text(encoding="utf-8"))["규칙"] if not r.get("사용", True)))

    sec("6. 개정 분류 대 경로")
    from regcheck import change_query
    produced = {"값 변경", "삭제", "번호 이동", "정의 변경", "위임·법령 변경", "내용 변경", "표현 수정", "분석 실패"}
    out.append(f"change_query가 내는 종류 중 경로 표에 없는 것: {sorted(produced - set(paths) - {'분석 실패'}) or '없음'}")
    out.append(f"경로 표에 있으나 change_query가 내지 않는 종류: {sorted(set(paths) - produced) or '없음'} (코드 문자열 확인: {[k for k in paths if k not in open(ROOT / 'src/regcheck/change_query.py', encoding='utf-8').read()] or '모두 있음'})")

    sec("7. 주제 층 사전이 말뭉치에 닿는가")
    regs = {r["reg_id"]: r for r in (json.loads(Path(p).read_text(encoding="utf-8")) for p in glob.glob(str(ROOT / "out/parsed/[0-9]*.json")))}
    ut = {a["id"]: a["norm"] for r in regs.values() for a in r["articles"] if not a.get("deleted")}
    from regcheck.disc_layer import DiscLayer
    from regcheck.leave_layer import LeaveLayer
    from regcheck.pay_layer import PayLayer
    for nm, L in (("휴직", LeaveLayer), ("보수", PayLayer), ("징계", DiscLayer)):
        lay = L(ut)
        kc = collections.Counter(k for ps in lay.assign.values() for k, _ in ps)
        ac = collections.Counter(a for ps in lay.assign.values() for _, a in ps)
        zk = [k for k in list(lay.spec.kinds) + [lay.spec.common] if kc[k] == 0]
        za = [a for a in lay.spec.attrs if ac[a] == 0]
        unk = sum(1 for ps in lay.assign.values() if all(a == "미분류" for _, a in ps))
        out.append(f"{nm}: 배정 조 {len(lay.assign)}개, 조가 0개인 종류 {zk or '없음'}, 조가 0개인 속성 {za or '없음'}, 속성 미분류만 가진 조 {unk}개, 종류별 {dict(kc.most_common())}")
    text = "\n".join(out)
    (ROOT / "out/ontology_check.txt").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
