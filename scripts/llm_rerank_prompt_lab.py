"""LLM 리랭커 프롬프트 변형 실험 (2026.10.15, 검색_설계.md 5절 후속). GPU 없이 돈다.

입력은 `eval/runs/rank_trace_llm_team_on.json`(93문항, 팀 DB 항 단위, 교차 인코더 상위 15개를 gpt-4o-mini가 채점한 기록).
그 행의 교차 인코더 순위(`리랭크_순위`)는 조 ID로 합쳐져 있어 항 청크를 바로 알 수 없다. 그래서
  - 상위 15개 청크는 `llm_top`(조 ID, LLM 점수, 교차 인코더 점수)과 기존 캐시(`llm_rerank_cache.json`, 키 "모델\\t질문\\tchunk_id")를
    조 ID + 점수로 맞춰 팀 DB의 chunk_id를 되찾고, 교차 인코더 점수 내림차순으로 원래 순서를 복원한다 (V0가 정확히 재현되는지로 검증).
  - 16~30위(TOP 30 변형)는 조 ID만 남아 있어 그 조의 팀 DB 항 청크를 모두 후보로 넣는다 (실제 창의 상위 집합. 보고서에 명시).
조문 글자는 PG_DATABASE(.env, regulations_team)에서 PgStore로 읽어 Searcher.rerank_text()와 같은 "[문서명 장 조항] body_search"로 만든다.
모델 로드 없음. src/search/llm_rerank.py의 LLMReranker를 상속해 프롬프트만 바꿔 끼운다 (원본은 수정하지 않는다).

실행 (저장소 루트에서):
  uv run python scripts/llm_rerank_prompt_lab.py --variants V0            # 재현 확인만 (API 호출 없음)
  uv run python scripts/llm_rerank_prompt_lab.py --variants V1,V2,V3,V4   # 변형 실행 (캐시: eval/runs/prompt_lab/cache_<변형>.json)
  uv run python scripts/llm_rerank_prompt_lab.py --raw "경조금 나와요" 23970_별표1   # 한 (질문, 청크)의 원문 응답을 찍는다 (V0 프롬프트)
결과: eval/runs/prompt_lab/result_<변형>.json, summary.json, summary.md
"""
import argparse
import copy
import hashlib
import json
import os
import re
import sys
import time
from collections import defaultdict

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.stdout.reconfigure(encoding="utf-8")

from config import LLM_RERANK_CACHE, LLM_RERANK_MODEL  # noqa: E402
from src.chunk.articles import article_id  # noqa: E402
from src.eval.rank_trace import summarize  # noqa: E402  (search.py를 import만 한다. 모델은 만들지 않는다)
from src.search.llm_rerank import SYSTEM_PROMPT, LLMReranker, parse_score, reorder  # noqa: E402
from src.store.pgstore import PgStore  # noqa: E402

TRACE = os.path.join(BASE, "eval", "runs", "rank_trace_llm_team_on.json")
OUT_DIR = os.path.join(BASE, "eval", "runs", "prompt_lab")

# 프롬프트 변형 ------------------------------------------------------------------
_HEAD = """너는 사내 규정 검색의 채점기다. 직원의 질문과 규정 조문 하나가 주어진다. 이 조문이 질문에 답하는 근거로 얼마나 알맞은지 0~10 정수로 채점한다.

기준:
- 8~10: 질문의 핵심 요건(대상·조건·금액·기한·절차 등 질문이 실제로 묻는 것)을 이 조문이 직접 규정한다.
- 3~7: 같은 제도·주제를 다루지만 질문이 묻는 요건은 다른 조문에 있거나, 보충·예외·절차처럼 간접적으로만 관련된다.
- 0~2: 질문과 무관하거나 말만 겹친다 (다른 대상·다른 제도)."""
_TAIL = """

조문 앞의 [문서명 조항]은 어느 규정의 조문인지 알려 주는 머리말이다. 조문에 없는 내용을 추측하지 않는다.
출력은 숫자 하나만 쓴다 (설명 없이). 예: 7"""
RULE_DEF = """
- 점수는 조문의 종류가 아니라 질문이 묻는 수치·요건·대상·절차가 이 글자 안에 실제로 있는가로 매긴다. 정의·적용범위 조문이라도 질문의 답이 들어 있으면 8~10."""
RULE_ANNEX = """
- 조항이 '별표'인 글(【별표 n】·[별표 n]로 시작)은 긴 표의 한 조각일 수 있다. 질문이 묻는 항목의 행·금액·일수가 이 조각에 보이면 8~10, 같은 표이지만 다른 항목의 행만 보이면 4~6."""
RULE_DELEGATE = """
- '별표 n에 따른다', '따로 정한다'처럼 답을 다른 곳에 위임하는 조문은 질문의 대상·제도를 직접 규정하면 6~8(근거 경로), 아니면 0~3."""
P1 = _HEAD + RULE_DEF + _TAIL
P2 = _HEAD + RULE_DEF + RULE_ANNEX + _TAIL
P3 = _HEAD + RULE_DEF + RULE_ANNEX + RULE_DELEGATE + _TAIL

VARIANTS = {
    "V0": dict(system=SYSTEM_PROMPT, hint=False, top=15, note="기준 (현재 SYSTEM_PROMPT, 기존 캐시 점수로만 계산)"),
    "V1": dict(system=P1, hint=False, top=15, note="정의 감점 제거"),
    "V2": dict(system=P2, hint=False, top=15, note="V1 + 별표 조각 규칙"),
    "V3": dict(system=P3, hint=False, top=15, note="V2 + 위임 조문 규칙"),
    "V4": dict(system=P3, hint=True, top=15, note="V3 + 보조 검색어(hint) 한 줄"),
    "V5": dict(system=P3, hint=True, top=30, note="V4 + TOP 30"),
    "V6": dict(system=SYSTEM_PROMPT, hint=False, top=30, note="V0 프롬프트 + TOP 30"),
}


def user_message(question, text, hint=None):
    lines = [f"질문: {question}"]
    if hint:
        lines.append(f"질문이 묻는 것(보조 검색어): {hint}")
    return "\n".join(lines) + f"\n\n조문:\n{text}"


class LabReranker(LLMReranker):
    """프롬프트를 바꿔 끼운 LLMReranker. 캐시 키에 프롬프트 해시를 넣고, 같은 프롬프트(V0)면 기존 캐시도 읽는다."""

    def __init__(self, name, system, use_hint, top, cache_path, base_cache=None, seed_cache=None, workers=8):
        super().__init__(model=LLM_RERANK_MODEL, top=top, cache_path=cache_path, workers=workers)
        self.name, self.system, self.use_hint = name, system, use_hint
        self.phash = hashlib.sha1((system + "\n--hint--" + str(use_hint)).encode("utf-8")).hexdigest()[:10]
        self.base_cache = base_cache if (base_cache and system == SYSTEM_PROMPT and not use_hint) else {}
        if seed_cache:  # 같은 프롬프트의 다른 변형 캐시(V4→V5)
            for k, v in seed_cache.items():
                self.cache.setdefault(k, v)
        self.hints = {}
        self.failures = []  # (question, chunk_id, raw)

    def _key(self, question, chunk_id):
        return f"{self.model_name}\t{self.phash}\t{question}\t{chunk_id}"

    def _ask(self, question, text):
        try:
            if self.llm is None:
                from langchain_openai import ChatOpenAI
                self.llm = ChatOpenAI(model=self.model_name, temperature=0)
            msg = user_message(question, text, self.hints.get(question) if self.use_hint else None)
            out = self.llm.invoke([("system", self.system), ("human", msg)])
            return out.content if isinstance(out.content, str) else str(out.content)
        except Exception as e:  # noqa: BLE001
            return f"__ERROR__ {type(e).__name__}: {e}"

    def score(self, question, chunk_id, text):
        key = self._key(question, chunk_id)
        with self.lock:
            if key in self.cache:
                return self.cache[key]
            base = self.base_cache.get(f"{self.model_name}\t{question}\t{chunk_id}")
            if base is not None:
                return base
        with self.lock:
            self.calls += 1
        raw = self._ask(question, text)
        v = None if (raw or "").startswith("__ERROR__") else parse_score(raw)
        with self.lock:
            if v is not None:
                self.cache[key] = v
            else:
                self.failures.append((question, chunk_id, raw))
        return v


# 팀 DB 글자 --------------------------------------------------------------------
def load_texts():
    """chunk_id -> rerank_text (Searcher.rerank_text와 같은 조립: '[문서명 장 조항] body_search'), 조 ID -> 항 청크 목록."""
    store = PgStore()
    docs = {d["doc_id"]: d for d in store.load_documents()}
    rows = store.load_indexed_rows()
    store.close()
    texts, art_chunks = {}, defaultdict(list)
    for r in rows:
        title = docs.get(r["doc_id"], {}).get("title") or r["doc_id"]
        label = " ".join(x for x in (title, r.get("section"), r["article"]) if x)
        texts[r["chunk_id"]] = f"[{label}] {r['body_search']}"
        art_chunks[article_id(r["chunk_id"])].append(r["chunk_id"])

    def para_no(cid):
        m = re.search(r"_(\d+)항$", cid)
        return (int(m.group(1)) if m else 0, cid)
    for a in art_chunks:
        art_chunks[a].sort(key=para_no)
    return texts, art_chunks


# 상위 15개 청크 복원 ---------------------------------------------------------------
def rebuild_head(row, base_cache, team_ids, model=LLM_RERANK_MODEL):
    """llm_top(조 ID, 점수, 교차 인코더 점수, 정답)의 각 줄에 팀 DB chunk_id를 붙이고 교차 인코더 순서로 돌려준다.
    돌려주는 것: [(chunk_id, V0 점수, ce 점수)] (교차 인코더 내림차순), 못 맞춘 줄 목록."""
    q = row["q"]
    pool = {}  # chunk_id -> 점수 (이 질문의 캐시 중 팀 DB에 있는 청크)
    prefix = f"{model}\t{q}\t"
    for k, v in base_cache.items():
        if k.startswith(prefix):
            cid = k[len(prefix):]
            if cid in team_ids:
                pool[cid] = v
    used, head, unmatched = set(), [], []
    for art, score, ce, _gold in row["llm_top"]:
        cands = [c for c, v in pool.items() if c not in used and article_id(c) == art and v == score]
        if not cands:
            unmatched.append((art, score, ce))
            continue
        used.add(cands[0])
        head.append((cands[0], score, ce))
    # 교차 인코더 순서: ce 점수 내림차순 (같은 값은 llm_top 순서 = 같은 점수 안에서는 원래 순서)
    head.sort(key=lambda t: -t[2])
    return head, unmatched


def by_article(chunk_ids):
    out, seen = [], set()
    for c in chunk_ids:
        a = article_id(c)
        if a not in seen:
            seen.add(a)
            out.append(a)
    return out


def best_rank(group, order):
    ranks = [i for i, a in enumerate(order, start=1) if a in group]
    return min(ranks) if ranks else None


def first_gold_rank(row, order):
    flat = set().union(*[set(g) for g in row["정답묶음"]])
    return best_rank(flat, order)


# 변형 하나 실행 ------------------------------------------------------------------
def run_variant(name, rows, heads, texts, art_chunks, base_cache, workers, seed_cache=None):
    spec = VARIANTS[name]
    cache_path = os.path.join(OUT_DIR, f"cache_{name}.json")
    rr = LabReranker(name, spec["system"], spec["hint"], spec["top"], cache_path, base_cache=base_cache,
                     seed_cache=seed_cache, workers=workers)
    for r in rows:
        rr.hints[r["q"]] = r["hint"]
    results, t0 = [], time.time()
    n_cands = []
    for i, r in enumerate(rows, 1):
        head = heads[r["q"]]
        cands = [c for c, _, _ in head]
        head_arts = set(article_id(c) for c in cands)
        if spec["top"] > 15:
            for a in r["리랭크_순위"]:
                if a not in head_arts:
                    cands.extend(art_chunks.get(a, []))
        n_cands.append(len(cands))
        if name == "V0":
            scores = [s for _, s, _ in head]
        else:
            jobs = [rr.pool.submit(rr.score, r["q"], c, texts[c]) for c in cands]
            scores = [j.result() for j in jobs]
        ordered = reorder(cands, scores)
        head_order = by_article(ordered)
        # TOP 15: 16위 이하 조는 교차 인코더 순서 그대로 뒤에. TOP 30: 뒤 조가 모두 후보에 들어가므로 꼬리가 빈다
        order = head_order + [a for a in r["리랭크_순위"] if a not in head_arts and a not in set(head_order)]
        results.append({"q": r["q"], "cands": cands, "scores": scores, "order": order,
                        "first_gold": first_gold_rank(r, order),
                        "groups": [best_rank(set(g), order) for g in r["정답묶음"]]})
        if name != "V0" and i % 10 == 0:
            with rr.lock:
                rr._save_cache()
            print(f"  [{name}] {i}/{len(rows)} 호출 {rr.calls} 실패 {len(rr.failures)} {time.time() - t0:.0f}초", flush=True)
    if name != "V0":
        with rr.lock:
            rr._save_cache()
    meta = {"variant": name, "note": spec["note"], "top": spec["top"], "hint": spec["hint"], "prompt_hash": rr.phash,
            "calls": rr.calls, "failures": rr.failures, "elapsed_sec": round(time.time() - t0, 1),
            "mean_candidates": round(sum(n_cands) / len(n_cands), 1), "system_prompt": spec["system"]}
    json.dump({"meta": meta, "rows": results}, open(os.path.join(OUT_DIR, f"result_{name}.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    return meta, results


# 지표 --------------------------------------------------------------------------
def metrics(rows, results, key):
    rows2 = copy.deepcopy(rows)
    for r, res in zip(rows2, results):
        r[key] = res["order"]
    m = summarize(rows2, key)
    n = len(rows)
    fg = [res["first_gold"] for res in results]
    m["MRR"] = round(sum(1 / g for g in fg if g) / n, 3)
    for k in (1, 3, 5):
        m[f"Hit@{k}"] = sum(1 for g in fg if g and g <= k)
    return m


def q_key(res):
    """문항 비교용: (5위 안 묶음 수, -첫 정답 순위). 클수록 좋다."""
    in5 = sum(1 for b in res["groups"] if b and b <= 5)
    fg = res["first_gold"] or 10 ** 6
    return (in5, -fg)


def compare(base, var):
    up = down = same = 0
    for b, v in zip(base, var):
        kb, kv = q_key(b), q_key(v)
        up += kv > kb
        down += kv < kb
        same += kv == kb
    return up, same, down


def classify_misses(rows, heads, v0):
    """V0에서 5위 밖인 묶음을 (창 밖 / 창 안 15개 밖 / 정답 7점 이하 / 오답 우위)로 나눈다."""
    out = []
    for r, res in zip(rows, v0):
        head = heads[r["q"]]
        for gi, (g, b) in enumerate(zip(r["정답묶음"], res["groups"])):
            if b and b <= 5:
                continue
            g = set(g)
            gold_scores = [s for c, s, _ in head if article_id(c) in g]
            if not any(a in g for a in r["리랭크_순위"]):
                kind = "창밖"
            elif not gold_scores:
                kind = "15개밖"
            elif max(s for s in gold_scores) <= 7:
                kind = "저평가"
            else:
                kind = "오답우위"
            out.append({"q": r["q"], "gi": gi, "group": sorted(g), "v0_rank": b, "kind": kind,
                        "gold_scores": [(c, s) for c, s, _ in head if article_id(c) in g]})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", default="V0")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--raw", nargs=2, metavar=("Q_SUBSTR", "CHUNK_ID"), help="V0 프롬프트로 원문 응답 하나를 찍는다")
    ap.add_argument("--force", action="store_true", help="V0 재현이 안 맞아도 계속")
    args = ap.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)

    rows = json.load(open(TRACE, encoding="utf-8"))
    base_cache = json.load(open(LLM_RERANK_CACHE, encoding="utf-8"))
    texts, art_chunks = load_texts()
    team_ids = set(texts)
    print(f"문항 {len(rows)}, 팀 DB 색인 청크 {len(team_ids)}, 기존 캐시 {len(base_cache)}건")

    if args.raw:
        qsub, cid = args.raw
        r = next(r for r in rows if qsub in r["q"])
        from langchain_openai import ChatOpenAI
        llm = ChatOpenAI(model=LLM_RERANK_MODEL, temperature=0)
        msg = user_message(r["q"], texts[cid])
        print("=== 사용자 메시지 ===\n" + msg)
        out = llm.invoke([("system", SYSTEM_PROMPT), ("human", msg)])
        print("=== 원문 응답 ===\n" + repr(out.content) + f"\n=== parse_score → {parse_score(out.content)}")
        print("기존 캐시:", base_cache.get(f"{LLM_RERANK_MODEL}\t{r['q']}\t{cid}"))
        return

    # 1) 상위 15개 청크 복원 + V0 재현 확인
    heads, bad = {}, 0
    for r in rows:
        head, unmatched = rebuild_head(r, base_cache, team_ids)
        heads[r["q"]] = head
        if unmatched or len(head) != len(r["llm_top"]):
            bad += 1
            print(f"  복원 실패: {r['q'][:40]} 못 맞춘 줄 {unmatched}")
    v0_meta, v0 = run_variant("V0", rows, heads, texts, art_chunks, base_cache, args.workers)
    mism = [(r["q"], res["order"][:8], r["LLM리랭크_순위"][:8]) for r, res in zip(rows, v0) if res["order"] != r["LLM리랭크_순위"]]
    m_ref = summarize(rows, "LLM리랭크_순위")
    m_v0 = metrics(rows, v0, "V0_순위")
    print(f"V0 재현: 복원 실패 문항 {bad}, 순위 불일치 문항 {len(mism)}\n  기록된 LLM 결과 {m_ref}\n  재계산          {m_v0}")
    for q, a, b in mism[:5]:
        print(f"  불일치: {q[:40]}\n    재계산 {a}\n    기록   {b}")
    reproduced = not bad and not mism and all(m_ref[k] == m_v0[k] for k in m_ref)
    if not reproduced and not args.force:
        print("V0가 재현되지 않아 멈춥니다 (--force로 계속).")
        sys.exit(1)

    misses = classify_misses(rows, heads, v0)
    kinds = defaultdict(int)
    for m in misses:
        kinds[m["kind"]] += 1
    print(f"V0 5위 밖 묶음 {len(misses)}: {dict(kinds)}")

    # 2) 변형 실행
    names = [n for n in args.variants.split(",") if n and n != "V0"]
    all_results, metas = {"V0": v0}, {"V0": v0_meta}
    for n in names:
        if n not in VARIANTS:
            sys.exit(f"모르는 변형 {n}")
        seed = None
        if n == "V5":
            p4 = os.path.join(OUT_DIR, "cache_V4.json")
            seed = json.load(open(p4, encoding="utf-8")) if os.path.exists(p4) else None
        print(f"== {n}: {VARIANTS[n]['note']}", flush=True)
        metas[n], all_results[n] = run_variant(n, rows, heads, texts, art_chunks, base_cache, args.workers, seed_cache=seed)
        print(f"  호출 {metas[n]['calls']}회, 실패 {len(metas[n]['failures'])}, {metas[n]['elapsed_sec']}초, "
              f"후보 평균 {metas[n]['mean_candidates']}", flush=True)

    # 3) 지표 (이전 실행 결과 result_*.json도 합친다)
    for f in sorted(os.listdir(OUT_DIR)):
        mm = re.fullmatch(r"result_(V\d+)\.json", f)
        if mm and mm.group(1) not in all_results:
            d = json.load(open(os.path.join(OUT_DIR, f), encoding="utf-8"))
            all_results[mm.group(1)], metas[mm.group(1)] = d["rows"], d["meta"]
    summary, lines = {}, []
    hdr = ("| 변형 | 설명 | TOP | 호출 | 실패 | R@5 | 전부5위안 | R@7 | R@10 | 6위이하 | 창밖 | MRR | Hit@1 | Hit@3 | Hit@5 | "
           "개선/동일/악화(문항) | 저평가 구제 | 15개밖 구제 | 새로 5위 밖 |")
    lines.append(hdr)
    lines.append("|" + "---|" * (hdr.count("|") - 1))
    low = [m for m in misses if m["kind"] == "저평가"]
    out15 = [m for m in misses if m["kind"] == "15개밖"]
    idx = {r["q"]: i for i, r in enumerate(rows)}
    for n in sorted(all_results):
        res, meta = all_results[n], metas[n]
        m = metrics(rows, res, f"{n}_순위")
        up, same, down = compare(v0, res)
        rescued = [mm for mm in low if (b := res[idx[mm["q"]]]["groups"][mm["gi"]]) and b <= 5]
        rescued15 = [mm for mm in out15 if (b := res[idx[mm["q"]]]["groups"][mm["gi"]]) and b <= 5]
        newly_bad = []
        for r, b0, bv in zip(rows, v0, res):
            for gi, (g, x, y) in enumerate(zip(r["정답묶음"], b0["groups"], bv["groups"])):
                if x and x <= 5 and not (y and y <= 5):
                    newly_bad.append({"q": r["q"], "group": g, "v0": x, "var": y})
        summary[n] = {**m, "meta": {k: v for k, v in meta.items() if k != "system_prompt"}, "up": up, "same": same, "down": down,
                      "rescued_low": [(x["q"], x["group"]) for x in rescued],
                      "rescued_out15": [(x["q"], x["group"]) for x in rescued15], "newly_bad": newly_bad}
        nn = m["묶음"]
        lines.append(f"| {n} | {meta['note']} | {meta['top']} | {meta['calls']} | {len(meta['failures'])} | "
                     f"{m['R@5'] / nn:.3f} ({m['R@5']}/{nn}) | {m['전부5위안']} | {m['R@7'] / nn:.3f} | {m['R@10'] / nn:.3f} | "
                     f"{m['6위이하']} | {m['창밖']} | {m['MRR']:.3f} | {m['Hit@1']} | {m['Hit@3']} | {m['Hit@5']} | "
                     f"{up}/{same}/{down} | {len(rescued)}/{len(low)} | {len(rescued15)}/{len(out15)} | {len(newly_bad)} |")
    table = "\n".join(lines)
    print("\n" + table)
    json.dump({"misses_v0": misses, "variants": summary}, open(os.path.join(OUT_DIR, "summary.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    open(os.path.join(OUT_DIR, "summary.md"), "w", encoding="utf-8").write(table + "\n")


if __name__ == "__main__":
    main()
