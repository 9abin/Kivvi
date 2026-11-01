"""위임 연결: "이 사항은 다른 문서가 정한다"를 조 -> 조 선으로 바꾼다. 출처 세 가지를 따로 표시해 켜고 끄며 잴 수 있게 한다.

출처
  모델      추출한 규정문의 "위임처" (모델이 뽑음. 실행마다 흔들린다)
  규칙      조문 본문에서 위임 말투를 정규식으로 찾음: 「X」 또는 "~규정·지침·규칙·세칙·법" + "따른다·따라·정한 바·정하는 바·정한다".
            범위(추출한 조)가 아니라 모든 조에 쓴다. 같은 문장이면 항상 같은 결과다
  문서이름  개념 이름이 문서 제목의 핵심어인 문서("임금피크제 운영지침" <- 임금피크제). 위임 문구가 없어도 그 문서가 세부를 정하는 경우
도착 문서: 위임처 이름을 규정 제목에 맞춘다 (공백·「」 무시 정확 일치 > 포함 > 비슷한 이름 0.85). 별지·별표·위원회·직위·일반 명칭은 버려진다.
도착 조: 조 번호가 있으면 그 조. 없으면 도착 문서에서 개념 이름이 나오는 조 중 임베딩이 가까운 3개, 이름이 안 나오면 전체에서 가까운 2개.
법령 위임처는 여기서 잇지 않는다 (법령 정의 연결에서 따로 한다). 평가셋을 보고 넣은 선은 없다.

결과: <onto_dir>/deleg.json  [{"src", "dst", "개념", "위임처", "방법": 조번호|개념이름|임베딩, "출처": 모델|규칙|문서이름}]

  set -a; . ./.env; set +a
  python scripts/onto_deleg.py eval/onto_leave2 eval/cases_run_v3/cases_v3_20261028_r3/emb_qwen3-embedding-4b-q8_0.ids.json
"""
import collections
import difflib
import json
import os
import re
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "scripts")
sys.stdout.reconfigure(encoding="utf-8")
from graph_struct import article_vectors, pg  # noqa: E402
from onto_build import norm, read_items  # noqa: E402
from onto_expand_eval import node  # noqa: E402
from src.combo.onto_search import embed  # noqa: E402

JO = re.compile(r"제?\s*(\d+)\s*조(?:\s*의\s*(\d+))?")
# 특정 문서를 가리키지 않는 말 ("제규정에 따라 직원에 준하여")
GENERIC = {"제규정", "회사제규정", "관계법령", "관련법령", "관련법규", "현지관련법규", "이규정", "본규정", "이지침", "본지침", "내부규정"}
# 제목과 다르게 부르는 이름. 정관: 두 글자라 길이 기준에 걸린다 (위임처 42번). 국내보수규정: 해외근무직원 보수·복지규정이 국내 급여규정을 이렇게 부른다.
# 나머지는 외부 참조 점검(reports/external-refs-20261030_checked.md 2절)에서 원문·DB 근거가 있는 것만 넣었다. 추정 별칭(상벌규정 등)은 부서 확인 전이라 넣지 않는다
ALIAS = {norm("정관"): norm("한국벤처투자 정관"), norm("국내보수규정"): norm("급여규정"),
         norm("수당지급규정"): norm("제수수료 및 수당지급 지침"), norm("수수료 및 수당지급규정"): norm("제수수료 및 수당지급 지침"),
         norm("위임전결 규정"): norm("직무전결규정"), norm("징계관련 규정"): norm("징계지침")}
DELEG = re.compile(r"(?:[「｢]([^」｣]{2,40})[」｣]|([가-힣][가-힣·ㆍ‧\s]{1,30}?(?:규정|지침|규칙|세칙)))\s*(제\s*\d+\s*조(?:\s*의\s*\d+)?)?[^.。]{0,25}?"
                   r"(?:이|가|에서|에|의)?\s*(?:정한|정하는|따른다|따라|따르며|따르되|의한다|의하여|정한다)")
TITLE_TAIL = re.compile(r"(?:운영|시행|관리|업무처리)?\s*(?:지침|규정|규칙|세칙|요령|기준)$")


def match_doc(name, titles):
    """위임처 이름 -> 규정 doc_id. 정확(공백·「」 무시) > 한쪽이 다른 쪽을 포함(4자 이상) > 비슷한 이름(0.85)."""
    n = norm(name.strip("「」 "))
    n = ALIAS.get(n, n)
    if len(n) < 3 or n in GENERIC:
        return None
    if n in titles:
        return titles[n]
    inside = [t for t in titles if len(n) >= 4 and (n in t or t in n) and len(t) >= 4]
    if len(inside) == 1:
        return titles[inside[0]]
    best = difflib.get_close_matches(n, list(titles), n=1, cutoff=0.85)
    return titles[best[0]] if best else None


def candidates(ex, arts, titles, doc_title):
    """(출발 조, 개념, 요지, 위임처 이름, 조 표기, 출처) 목록."""
    out = []
    for aid, v in ex.items():
        for s in v["규정문"]:
            for t in s.get("위임처") or []:
                out.append((aid, s["개념"], s["요지"], t["이름"], t.get("조") or "", "모델"))
    concept_of = collections.defaultdict(list)   # (조, 항목) -> 그 항목 규정문의 개념
    for aid, v in ex.items():
        for s in v["규정문"]:
            concept_of[(aid, s["항목"])].append(s["개념"])
    for aid, a in arts.items():
        for it in a["items"]:
            for m in DELEG.finditer(it["text"]):
                name = (m.group(1) or m.group(2)).strip()
                cs = concept_of.get((aid, it["no"])) or [a["title"] or ""]
                out += [(aid, c, it["text"][:120], name, m.group(3) or "", "규칙") for c in dict.fromkeys(cs)]
    # 문서이름: 제목에서 "운영지침·규정" 같은 꼬리를 떼고 남은 핵심어가 개념 이름과 같은 문서
    core = {d: norm(TITLE_TAIL.sub("", t)) for d, t in doc_title.items()}
    for aid, v in ex.items():
        for s in v["규정문"]:
            c = norm(s["개념"])
            for d, k in core.items():
                if len(c) >= 3 and k == c and d != aid.split(":")[0]:
                    out.append((aid, s["개념"], s["요지"], doc_title[d], "", "문서이름"))
    return out


def main(onto, ids_path):
    ex = json.load(open(os.path.join(onto, "extract.json"), encoding="utf-8"))
    arts = read_items()
    with pg("regulations") as c:
        doc_title = {d: t for d, t in c.execute("select doc_id, title from documents").fetchall() if t}
    titles = {norm(t): d for d, t in doc_title.items()}
    by_doc = collections.defaultdict(list)
    for aid in arts:
        by_doc[aid.split(":")[0]].append(aid)
    vec = article_vectors(ids_path)
    cands = list(dict.fromkeys(candidates(ex, arts, titles, doc_title)))
    out, why, todo = [], collections.Counter(), []
    for aid, concept, gist, name, jo, src in cands:
        did = match_doc(name, titles)
        if not did:
            why[src, "문서 아님·못 맞춤"] += 1
            continue
        if did == aid.split(":")[0] and not jo:
            why[src, "같은 문서, 조 없음"] += 1
            continue
        m = JO.search(jo)
        dst = f"{did}:{m[1]}" + (f"의{m[2]}" if m[2] else "") if m else None
        if dst in arts:
            out.append({"src": aid, "dst": dst, "개념": concept, "위임처": name, "방법": "조번호", "출처": src})
        else:
            todo.append((aid, concept, gist, name, did, src))
    # 도착 문서 안에서 고른다: 개념 이름이 나오는 조가 있으면 그중에서, 없으면 전체에서, 임베딩이 가까운 순으로.
    # 이름이 나오는 조를 번호 순으로 자르면 목적·정의 조만 걸린다.
    Q = embed([f"{c}: {g}" for _, c, g, _, _, _ in todo]) if todo else []
    for (aid, concept, gist, name, did, src), q in zip(todo, Q):
        c_ = norm(concept)
        named = [a for a in by_doc[did] if len(c_) >= 2 and c_ in norm(arts[a]["title"] or "") + "".join(norm(i["text"]) for i in arts[a]["items"])]
        pool, how, k = (named, "개념이름", 3) if named else (by_doc[did], "임베딩", 2)
        best = sorted(((float(q @ vec[a]), a) for a in pool if a in vec), reverse=True)[:k]
        out += [{"src": aid, "dst": a, "개념": concept, "위임처": name, "방법": how, "출처": src, "sim": round(sim, 3)} for sim, a in best]
    seen, rows = set(), []
    for r in out:
        key = (r["src"], r["dst"], r["출처"])
        if key not in seen and r["src"] != r["dst"]:
            seen.add(key)
            rows.append({**r, "src": node(r["src"]), "dst": node(r["dst"])})
    json.dump(rows, open(os.path.join(onto, "deleg.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"후보 {len(cands)}개 {collections.Counter(x[5] for x in cands)} -> 버림 {dict(why)}")
    for src in ("모델", "규칙", "문서이름"):
        rs = [r for r in rows if r["출처"] == src]
        print(f"  {src}: 선 {len(rs)}개 {dict(collections.Counter(r['방법'] for r in rs))}, 출발 조 {len({r['src'] for r in rs})}개, 도착 문서 {len({r['dst'].split(':')[0] for r in rs})}개")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
