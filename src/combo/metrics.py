"""조합 실험 지표 (rag-combo-experiment.md 5절). 근거는 서로 다른 조·별표(노드)로 센다. 색인에 없는 근거(부칙, 별지, 삭제 조)는 제외한다."""
import math
import random
import re


def node_key(doc, art):
    """(규정, 조) 비교 키. 별표 하위 표기("별표 5 주1", "별표 2 제1호 나목")는 별표 번호로 합친다."""
    m = re.match(r"\s*제\s*(\d+)\s*조(?:\s*의\s*(\d+))?", art or "")
    if m:
        return f"{doc}:제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "")
    m = re.match(r"\s*(별표\s*\d+(?:\s*의\s*\d+)?)", art or "")
    return f"{doc}:" + (re.sub(r"\s+", "", m.group(1)) if m else re.sub(r"\s+", "", art or ""))


def evidence_category(art):
    """색인에서 빠지는 근거의 종류 (제외 사유를 표에 적기 위해)."""
    a = art or ""
    if "부칙" in a:
        return "부칙"
    if "별지" in a:
        return "별지"
    if "삭제" in a:
        return "삭제 조"
    return "기타"


def gold_nodes(g, title_to_doc, indexed_nodes):
    """질문 하나의 정답 노드: {"core": 필수, "aux": 보조, "excluded": {종류: 개수}}.
    같은 노드가 필수와 보조에 모두 있으면 필수로 센다. 색인에 없는 노드는 제외한다."""
    core, aux, excluded = set(), set(), {}
    for e in g.get("근거") or []:
        doc = title_to_doc.get(e.get("규정"))
        node = node_key(doc, e.get("조항"))
        if node not in indexed_nodes:
            cat = evidence_category(e.get("조항"))
            excluded[cat] = excluded.get(cat, 0) + 1
            continue
        (core if e.get("tier", "필수") == "필수" else aux).add(node)
    return {"core": core, "aux": aux - core, "excluded": excluded}


def first_seen(nodes):
    """순서를 유지한 채 중복 노드를 한 번만."""
    seen, out = set(), []
    for n in nodes:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def hits_at(order, gold, k):
    return len(gold & set(order[:k]))


def rr(order, gold):
    for i, n in enumerate(order, 1):
        if n in gold:
            return 1.0 / i
    return 0.0


def ndcg(order, core, aux, k=8):
    """필수 2, 보조 1의 이득. 같은 노드는 한 번만 센다."""
    gain = lambda n: 2 if n in core else (1 if n in aux else 0)  # noqa: E731
    dcg = sum(gain(n) / math.log2(i + 1) for i, n in enumerate(order[:k], 1))
    ideal = sorted([2] * len(core) + [1] * len(aux), reverse=True)[:k]
    idcg = sum(g / math.log2(i + 1) for i, g in enumerate(ideal, 1))
    return dcg / idcg if idcg else None


def micro(pairs):
    """[(맞은 수, 전체 수)] -> 맞은 합 / 전체 합."""
    tot = sum(t for _, t in pairs)
    return sum(h for h, _ in pairs) / tot if tot else None


def bootstrap(pairs, n=1000, seed=0):
    """질문 단위 부트스트랩 95% 구간 (micro 비율). 질문을 복원 추출한다."""
    pairs = [p for p in pairs if p[1]]
    if not pairs:
        return None
    rnd = random.Random(seed)
    vals = []
    for _ in range(n):
        s = [pairs[rnd.randrange(len(pairs))] for _ in pairs]
        vals.append(micro(s))
    vals.sort()
    return vals[int(n * 0.025)], vals[int(n * 0.975) - 1]


def paired_diff_cluster(rows, n=1000, seed=0):
    """작업(묶음) 단위로 복원 추출한 짝 차이(a - b)의 평균과 95% 구간. rows = [(a 맞은 수, a 전체 수, b 맞은 수, b 전체 수)]를 묶음마다 하나씩.
    같은 작업의 세 변형은 같은 정답을 공유해 독립이 아니므로, 질문이 아니라 묶음을 뽑는다."""
    rows = [r for r in rows if r[1] and r[3]]
    if not rows:
        return None
    rnd = random.Random(seed)
    diff = lambda s: micro([(r[0], r[1]) for r in s]) - micro([(r[2], r[3]) for r in s])  # noqa: E731
    diffs = sorted(diff([rows[rnd.randrange(len(rows))] for _ in rows]) for _ in range(n))
    return diff(rows), diffs[int(n * 0.025)], diffs[int(n * 0.975) - 1]


def paired_diff(a_pairs, b_pairs, n=1000, seed=0):
    """같은 질문끼리 짝지은 차이(a - b)의 평균과 95% 구간. 입력은 같은 순서의 [(맞은 수, 전체 수)]."""
    rows = [(a, b) for a, b in zip(a_pairs, b_pairs) if a[1]]
    if not rows:
        return None
    rnd = random.Random(seed)
    diffs = []
    for _ in range(n):
        s = [rows[rnd.randrange(len(rows))] for _ in rows]
        diffs.append(micro([x[0] for x in s]) - micro([x[1] for x in s]))
    diffs.sort()
    return micro([r[0] for r in rows]) - micro([r[1] for r in rows]), diffs[int(n * 0.025)], diffs[int(n * 0.975) - 1]
