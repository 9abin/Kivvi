"""정의 연결: 규정의 각 조 -> 그 규정의 정의·용어 조 (선 종류 DEF_OF).

질문은 상황을 말하고 정의를 묻지 않아서("이것도 괴롭힘이야?") 정의 조는 리랭크 점수가 낮고 상위 8조에서 밀린다.
그런데 같은 규정의 다른 조가 찾아졌다면 그 규정의 용어 뜻을 정한 조는 함께 읽어야 한다. 낱말이 아니라 구조(같은 규정)로 닿는다.
정의 조는 제목으로 찾는다: "정의", "용어의 정의", "성희롱의 정의", "용어정의" 등. 글자만 겹치는 제목("징계양정의 감경", "제정의")은 앞 글자가
한글이면 뺀다. 평가 질문·정답을 보고 넣은 선은 없다.

결과: <out>  [{"src", "dst", "kind": "DEF_OF"}]

  set -a; . ./.env; set +a
  python scripts/onto_def_edges.py eval/onto_full/def_edges.json
"""
import collections
import json
import re
import sys

sys.path.insert(0, "scripts")
sys.stdout.reconfigure(encoding="utf-8")
from onto_build import read_items  # noqa: E402
from onto_expand_eval import node  # noqa: E402

DEF_TITLE = re.compile(r"(?<![가-힣])정의|용어정의")


def main(out):
    arts = read_items()
    defs = collections.defaultdict(list)
    for aid, a in arts.items():
        if DEF_TITLE.search(a["title"] or ""):
            defs[aid.split(":")[0]].append(aid)
    rows = [{"src": node(aid), "dst": node(d), "kind": "DEF_OF"}
            for aid in arts for d in defs.get(aid.split(":")[0], []) if d != aid]
    json.dump(rows, open(out, "w", encoding="utf-8"), ensure_ascii=False)
    titles = collections.Counter(arts[d]["title"] for ds in defs.values() for d in ds)
    print(f"정의 조가 있는 규정 {len(defs)}개 / 전체 {len({a.split(':')[0] for a in arts})}개, 정의 조 {sum(map(len, defs.values()))}개, 선 {len(rows)}개")
    print("정의 조 제목:", dict(titles))


if __name__ == "__main__":
    main(sys.argv[1])
