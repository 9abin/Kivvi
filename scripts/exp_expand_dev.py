"""실험: 검색어 확장(유의어·표기 변형)이 지식 그래프 + 임베딩 이웃이 놓친 정답을 얼마나 더 잡는가 (개발 분할만).

  uv run python scripts/exp_expand_dev.py --model qwen3:30b-a3b-instruct-2507-q4_K_M
비교:
  기본(KG ∪ 이웃 상위 20)        이미 가진 후보
  +제목어(LLM 없음)               수정된 조의 제목 낱말로 본문 검색
  +LLM 확장                       LLM이 만든 검색어(핵심 용어 · 유의어 · 표기 변형)로 본문 검색
검색어는 등장 조가 DF_MAX개를 넘으면 버린다 (흔한 말이 후보를 쏟아내지 않게). 결과는 확인 후보이고, 정답은 사람 검토 전 초안이다.
LLM 출력은 out/exp/expand_<model>.json에 캐시한다.
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
from regcheck.nl import _post  # noqa: E402

K = ROOT / "eval/impact_label_kit/산출"
DF_MAX = 40
PROMPT = """회사 내부 규정(내규)을 개정하려 한다. 이 개정 때문에 영향받을 수 있는 다른 조문을 규정 전체에서 찾으려고 한다. 찾을 때 쓸 검색어를 만든다.

[개정문]
{amend}

[수정되는 조] {where}
[그 조의 원문]
{body}

규칙
- 이 조가 다루는 핵심 용어, 그리고 다른 규정에서 같은 뜻으로 쓰일 수 있는 다른 표현(유의어, 띄어쓰기·한자어·약칭 변형)을 합쳐 최대 12개.
- 2어절 이내의 짧은 명사구만. 문장이나 설명은 쓰지 않는다.
- JSON 배열(문자열 목록) 하나만 출력한다. 다른 글은 쓰지 않는다."""


def jl(p):
    return [json.loads(l) for l in Path(p).read_text(encoding="utf-8").splitlines() if l.strip()]


def parse_list(s):
    m = re.search(r"\[.*\]", s or "", re.S)
    if not m:
        return []
    try:
        return [x.strip() for x in json.loads(m.group(0)) if isinstance(x, str) and 2 <= len(x.strip()) <= 20]
    except json.JSONDecodeError:
        return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen3:30b-a3b-instruct-2507-q4_K_M")
    ap.add_argument("--k", type=int, default=20)
    ap.add_argument("--min-terms", type=int, default=1, help="검색어가 이 개수 이상 겹친 조만 추가 후보로 센다")
    a = ap.parse_args()
    regs = {r["reg_id"]: r for r in (json.loads(Path(p).read_text(encoding="utf-8")) for p in glob.glob(str(ROOT / "out/parsed/[0-9]*.json")))}
    nodes = {r["id"]: {k: v for k, v in r.items() if k != "id"} for r in jl(ROOT / "out/kg/nodes.jsonl")}
    kg = KG(nodes, jl(ROOT / "out/kg/edges.jsonl"))
    T = Truth(nodes, regs)
    text = {}
    for r in regs.values():
        for x in r["articles"]:
            if not x.get("deleted"):
                text[x["id"]] = x["norm"]
        for ad in r.get("addenda") or []:
            for x in ad.get("articles") or []:
                text[x["id"]] = x["norm"]
        for ap_ in r.get("appendices") or []:
            text[ap_["id"]] = ap_.get("text") or ""
    ntext = {k: re.sub(r"\s+", "", v) for k, v in text.items()}
    item_text = {it["id"]: it["norm"] for r in regs.values() for x in r["articles"] for it in x["items"]}
    V = ROOT / "out/vector/BAAI__bge-m3"
    emb = np.load(V / "embeddings.npy"); emb = emb / np.linalg.norm(emb, axis=1, keepdims=True)
    art = np.array([c["article_id"] for c in jl(V / "chunks.jsonl")])
    byart = collections.defaultdict(list)
    for j, x in enumerate(art):
        byart[x].append(j)

    def neigh(x, k):
        s = (emb @ emb[byart[x]].T).max(axis=1); best = {}
        for j, sc in enumerate(s):
            b = art[j]
            if b != x and sc > best.get(b, -1):
                best[b] = sc
        return {y for y, _ in sorted(best.items(), key=lambda t: -t[1])[:k]}

    def search(terms, exclude):
        hits = collections.defaultdict(set)
        for t in terms:
            tn = re.sub(r"\s+", "", t)
            ids = [k for k, v in ntext.items() if tn in v and k not in exclude]
            if 0 < len(ids) <= DF_MAX:
                for k in ids:
                    hits[k].add(t)
        return hits

    cache_p = ROOT / "out/exp" / f"expand_{re.sub(r'[^A-Za-z0-9._-]', '_', a.model)}.json"
    cache_p.parent.mkdir(parents=True, exist_ok=True)
    cache = json.loads(cache_p.read_text(encoding="utf-8")) if cache_p.exists() else {}
    ch = {r["id"]: r for r in jl(K / "changes.jsonl")}; fin = {r["id"]: r for r in jl(K / "labels_final.jsonl")}
    tot = collections.Counter(); rows = []
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
        res = kg.impact_many(starts, kind) if len(starts) > 1 else kg.impact(st, kind, hint)
        base = set(res["candidates"]) | neigh(st, a.k)
        truth = [(e, T.resolve(e)) for e in fin[i]["영향"]]
        if i not in cache:
            prompt = PROMPT.format(amend=s["개정문"], where=f"{s['규정']} {s['조']}", body=text.get(st, "")[:1800])
            try:
                cache[i] = parse_list(_post(a.model, [{"role": "user", "content": prompt}], timeout=300))
            except Exception as ex:  # noqa: BLE001
                print(f"{i} LLM 실패 {type(ex).__name__}: {str(ex)[:80]}"); cache[i] = []
            cache_p.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
        title = [w for w in re.split(r"[\s·‧,]+", nodes.get(st, {}).get("title", "")) if len(w) >= 2]
        ex = set(starts or [st])
        h_title = {k: v for k, v in search(title, ex).items() if len(v) >= a.min_terms}
        h_llm = {k: v for k, v in search(cache[i], ex).items() if len(v) >= a.min_terms}
        miss = [n for e, n in truth if n and n not in base]
        got_t = [n for n in miss if n in h_title]; got_l = [n for n in miss if n in h_llm]
        print(f"\n{i} {s['규정']} {s['조']} [{kind}] 정답 {len(truth)} | 기본 후보 {len(base)}, 놓친 정답 {len(miss)}")
        print(f"   LLM 검색어: {cache[i]}")
        print(f"   +제목어: 후보 {len(set(h_title) - base)}개 추가, 놓친 정답 {len(got_t)}건 잡음 | +LLM: 후보 {len(set(h_llm) - base)}개 추가, 놓친 정답 {len(got_l)}건 잡음")
        for n in got_l:
            print(f"      잡음: {T.nodes[n].get('label', n)} {nodes[n].get('reg', '')} ← {sorted(h_llm[n])}")
        for k_, v in (("기본", len(base)), ("추가후보_제목어", len(set(h_title) - base)), ("추가후보_LLM", len(set(h_llm) - base)), ("놓침", len(miss)),
                      ("잡음_제목어", len(got_t)), ("잡음_LLM", len(got_l))):
            tot[k_] += v
        rows.append(i)
    print("\n== 합계 (개발 분할)")
    print(dict(tot))


if __name__ == "__main__":
    main()
