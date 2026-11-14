"""임베딩 튜닝용 합성 질문 생성 (docs/09 가이드라인 A층) — 파일럿.

  uv run --extra db python scripts/gen_questions.py gen --n 100 [--model gpt-5.4-mini] [--cap-usd 1.0]
  uv run --extra db python scripts/gen_questions.py qc            # 자동 품질 검사 + 사람이 볼 표본 CSV
조(article) 단위로 GPT를 부른다: 한 조의 색인 청크(항)들을 보여 주고, 청크마다 세 형태의 질문을 받는다.
  구어   직원이 짧게 묻는 말            「반반차도 쓸 수 있어요?」
  상황   일상 상황을 설명하며 묻는 말   조문의 핵심 낱말을 그대로 쓰지 않는다 (개념 비약을 배우게 함)
  검색어 규정 용어 명사구 한 줄         바꿔쓰기(REWRITE_PROMPT)가 내는 모양
입력은 공개된 ALIO 규정 문장뿐이다. 평가 질문은 입력에 넣지 않는다. 출력은 data/embedding/ (git 제외).
비용: 응답의 usage로 실제 토큰을 세고 --cap-usd를 넘으면 멈춘다. 키는 .env의 OPENAI_API_KEY (출력하지 않음).
"""

import argparse
import collections
import json
import random
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

DATA = ROOT / "data" / "embedding"
OUT = DATA / "pilot_gen_v1.jsonl"   # --prompt v2이면 pilot_gen_v2.jsonl (main에서 정함)
PRICE = {"gpt-5.4-mini": (0.75, 4.50), "gpt-5.4-nano": (0.20, 1.25), "gpt-5.5": (5.0, 30.0)}   # 1M 토큰당 달러 (입력, 출력). 공식 가격표로 확인할 것
KRW = 1400

PROMPT = """너는 한국벤처투자 직원이 사내 규정 도우미에게 묻는 질문을 만든다. 아래 [조문]의 청크(항)마다, 그 청크만 읽고 답할 수 있는 질문을 세 형태로 하나씩 만든다.

형태
- 구어: 직원이 짧게 묻는 말. 예: 「반반차도 쓸 수 있어요?」
- 상황: 직원이 자기 일상 상황을 1~2문장으로 설명하며 묻는 말. 조문의 핵심 낱말(규정 용어)을 그대로 쓰지 말고 일상 표현으로 바꿔 쓴다. 예: 조문이 「영리를 목적으로 하는 업무에 종사」이면 「주말에 유튜브 채널로 돈을 벌고 있는데 문제될까요?」
- 검색어: 질문을 규정에서 쓰는 공식 용어로 바꾼 명사구 한 줄(주제어 2~4개, 조사 최소). 예: 「영리업무 겸직 허가 기준」

규칙
1. 질문은 지정한 청크의 내용으로 답이 나와야 한다. 청크에 없는 내용을 전제하지 않는다. 다른 청크·다른 조가 있어야 답이 나오면 만들지 않는다.
2. 규정명, 「제N조」, 청크 번호를 질문에 쓰지 않는다.
3. 같은 청크의 세 질문은 핵심 표현이 서로 달라야 한다.
4. 청크가 단순한 도입·정의·적용 범위라서 일상 상황으로 묻기 어려우면 상황 질문은 만들지 않아도 된다(그 청크의 상황 항목을 생략).
5. 상황·구어 질문의 bridge에는 「질문 속 일상 표현 → 조문 속 규정 용어」를 한 쌍 적는다(조문에 실제로 있는 낱말). 바꿔 쓴 말이 없으면 빈 문자열.
6. 질문은 한 문장(상황은 두 문장까지), 90자 이내.

[규정] {reg}
[조문] {label}({title})
[청크]
{chunks}

JSON 하나로만 답한다: {{"items": [{{"target": "청크 표시(예: 2항)", "form": "구어|상황|검색어", "q": "…", "bridge": "…"}}]}}"""

PROMPT_V2 = """너는 한국벤처투자 직원이 사내 규정 도우미에게 묻는 질문을 만든다. 아래 [조문]의 청크(항)마다 네 형태의 질문을 하나씩 만든다. 이 질문들은 검색 모델을 훈련하는 데 쓰이므로, **쉬운 질문보다 비슷한 다른 조문과 헷갈리기 쉬운 질문**이 더 쓸모 있다.

형태
- 구어: 직원이 짧게 묻는 말. 예: 「반반차도 쓸 수 있어요?」
- 간접: 조문의 핵심 낱말(규정 용어)을 하나도 쓰지 않고, 이 조문이 적용되는 **구체적인 일상·업무 사례**로 묻는 1~2문장. 질문만 봐서는 어느 규정인지 바로 떠오르지 않아야 한다. 예: 조문이 「영리를 목적으로 하는 업무에 종사」이면 「주말에 올린 영상 채널에 광고 수익이 붙기 시작했는데 괜찮을까요?」
- 혼동: 아래 [비슷한 조문]과 헷갈리기 쉽지만, **답은 이 청크에서만** 나오는 질문. 비슷한 조문이 아니라 이 청크만 정하고 있는 세부 대상·조건·수치·절차를 묻는다.
- 검색어: 질문을 규정에서 쓰는 공식 용어로 바꾼 명사구 한 줄(주제어 2~4개, 조사 최소). 예: 「영리업무 겸직 허가 기준」

규칙
1. 질문은 지정한 청크의 내용으로 답이 나와야 한다. 청크에 없는 내용을 전제하지 않는다. 다른 청크·다른 조가 있어야 답이 나오면 만들지 않는다.
2. 규정명, 「제N조」, 청크 번호를 질문에 쓰지 않는다.
3. 같은 청크의 질문끼리 핵심 표현이 서로 달라야 한다.
4. 청크가 단순한 도입·정의·적용 범위라서 간접·혼동 질문을 만들기 어려우면 그 형태는 생략해도 된다. 억지로 만들지 않는다.
5. 간접·구어 질문의 bridge에는 「질문 속 일상 표현 → 조문 속 규정 용어」를 한 쌍 적는다(조문에 실제로 있는 낱말). 혼동 질문의 bridge에는 「이 청크만의 구별 요소」를 적는다. 없으면 빈 문자열.
6. 질문은 한 문장(간접은 두 문장까지), 90자 이내.

[규정] {reg}
[조문] {label}({title})
[청크]
{chunks}

[비슷한 조문] (검색 모델이 이 조문과 자주 헷갈리는 다른 조문. 이 청크의 답이 아니다)
{rivals}

JSON 하나로만 답한다: {{"items": [{{"target": "청크 표시(예: 2항)", "form": "구어|간접|혼동|검색어", "q": "…", "bridge": "…"}}]}}"""

SCHEMA = {"type": "json_schema", "json_schema": {"name": "qs", "strict": True, "schema": {
    "type": "object", "additionalProperties": False, "required": ["items"],
    "properties": {"items": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["target", "form", "q", "bridge"],
        "properties": {"target": {"type": "string"}, "form": {"type": "string", "enum": ["구어", "상황", "간접", "혼동", "검색어"]},
                       "q": {"type": "string"}, "bridge": {"type": "string"}}}}}}}}


def E_node(cid):
    import exp_embed_baseline as E
    return E.node_of_chunk(cid)


def load_articles():
    """색인 청크를 (규정, 조) 단위로 묶는다. 조 제목은 우리 파서 결과에서"""
    import exp_embed_baseline as E
    rows = E.load_db()
    parsed = {}
    for p in (ROOT / "out" / "parsed").glob("[0-9]*.json"):
        reg = json.loads(p.read_text(encoding="utf-8"))
        for a in reg["articles"]:
            parsed[a["id"]] = (reg["name"], a)
    arts = collections.defaultdict(list)
    for r in rows:
        n = E.node_of_chunk(r["chunk_id"])
        if n and "별표" not in n and n in parsed:
            arts[n].append(r)
    return rows, arts, parsed


def rivals_of(rows, arts):
    """조마다 기본 BGE-m3가 가장 헷갈려 하는 다른 조 5개 (조 임베딩 = 청크 임베딩 평균). 혼동형 질문의 재료이자 어려운 오답 후보"""
    import numpy as np
    import exp_embed_baseline as E
    emb = np.load(DATA / "emb_BAAI_bge_m3_v0.npy")
    idx = collections.defaultdict(list)
    for i, r in enumerate(rows):
        n = E.node_of_chunk(r["chunk_id"])
        if n:
            idx[n].append(i)
    nodes = sorted(idx)
    C = np.stack([emb[idx[n]].mean(0) for n in nodes])
    C /= np.linalg.norm(C, axis=1, keepdims=True)
    S = C @ C.T
    out = {}
    for k, n in enumerate(nodes):
        order = [j for j in np.argsort(-S[k]) if nodes[j] != n][:5]
        out[n] = [nodes[j] for j in order]
    return out


def chunk_key(cid: str) -> str:
    m = re.search(r"_(\d+)항$", cid)
    return f"{m.group(1)}항" if m else "조전체"


def cmd_gen(a) -> None:
    from openai import OpenAI
    from regcheck.db import load_env

    rows, arts, parsed = load_articles()
    rng = random.Random(a.seed)
    by_reg = collections.defaultdict(list)
    for n in arts:
        by_reg[n.split(":")[0]].append(n)
    pick = [rng.choice(sorted(v)) for k, v in sorted(by_reg.items())]       # 규정마다 한 조씩 (골고루)
    rest = sorted(set(arts) - set(pick))
    rng.shuffle(rest)
    pick = (pick + rest)[:a.n]
    global OUT
    OUT = DATA / f"pilot_gen_{a.prompt}.jsonl"
    rivals = rivals_of(rows, arts) if a.prompt == "v2" else {}
    client = OpenAI(api_key=load_env().get("OPENAI_API_KEY"), timeout=180)
    pin, pout = PRICE[a.model]
    lock = threading.Lock()
    spent = {"in": 0, "out": 0, "reason": 0, "cached": 0, "calls": 0, "usd": 0.0, "stop": False}
    done = {json.loads(l)["node"] for l in OUT.read_text(encoding="utf-8").splitlines() if l.strip()} if OUT.exists() and not a.fresh else set()
    if a.fresh and OUT.exists():
        OUT.unlink()
    todo = [n for n in pick if n not in done]
    print(f"조 {len(pick)}개 중 새로 부를 것 {len(todo)}개 · 모델 {a.model} · 상한 ${a.cap_usd}")

    def work(node):
        if spent["stop"]:
            return
        name, art = parsed[node]
        chunks = sorted(arts[node], key=lambda r: r["chunk_id"])[:8]
        text = "\n".join(f"({chunk_key(r['chunk_id'])}) {r['body_search'].strip()}" for r in chunks)
        if a.prompt == "v2":
            rv = []
            for rn in rivals.get(node, []):
                rname, rart = parsed.get(rn, ("", None))
                head = next((r["body_search"].strip()[:70] for r in sorted(arts.get(rn, []), key=lambda r: r["chunk_id"])), "")
                rv.append(f"- {rname} {rart['label'] if rart else rn}({(rart or {}).get('title') or ''}): {head}")
            prompt = PROMPT_V2.format(reg=name, label=art["label"], title=art["title"] or "", chunks=text, rivals="\n".join(rv) or "(없음)")
        else:
            prompt = PROMPT.format(reg=name, label=art["label"], title=art["title"] or "", chunks=text)
        for attempt in range(3):
            try:
                r = client.chat.completions.create(model=a.model, messages=[{"role": "user", "content": prompt}], response_format=SCHEMA,
                                                   reasoning_effort="low", max_completion_tokens=6000)
                break
            except Exception as e:   # 한도·일시 오류는 잠깐 쉬고 다시
                err = str(e)[:120]
                time.sleep(3 * (attempt + 1))
        else:
            print("  실패", node, err)
            return
        u = r.usage
        rt = getattr(u.completion_tokens_details, "reasoning_tokens", 0) or 0
        ct = getattr(getattr(u, "prompt_tokens_details", None), "cached_tokens", 0) or 0
        try:
            items = json.loads(r.choices[0].message.content)["items"]
        except Exception:
            items = []
        with lock:
            spent["in"] += u.prompt_tokens
            spent["out"] += u.completion_tokens
            spent["reason"] += rt
            spent["cached"] += ct
            spent["calls"] += 1
            spent["usd"] = (spent["in"] * pin + spent["out"] * pout) / 1e6
            if spent["usd"] > a.cap_usd:
                spent["stop"] = True
            with OUT.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"node": node, "reg": name, "label": art["label"], "title": art["title"],
                                    "chunks": {chunk_key(c["chunk_id"]): {"chunk_id": c["chunk_id"], "text": c["body_search"]} for c in chunks},
                                    "items": items, "usage": {"in": u.prompt_tokens, "out": u.completion_tokens, "reasoning": rt},
                                    "model": a.model, "prompt": a.prompt, "rivals": rivals.get(node, [])}, ensure_ascii=False) + "\n")

    t0 = time.time()
    with ThreadPoolExecutor(a.workers) as ex:
        list(ex.map(work, todo))
    print(f"\n호출 {spent['calls']}회 · {time.time() - t0:.0f}초 · 입력 {spent['in']:,} · 출력 {spent['out']:,} (그중 추론 {spent['reason']:,}) · 캐시 {spent['cached']:,}")
    print(f"실제 비용 ${spent['usd']:.3f} ≈ {spent['usd'] * KRW:,.0f}원 (단가 입력 ${pin}/출력 ${pout} per 1M, 환율 {KRW})" + ("  ※ 상한에서 멈춤" if spent["stop"] else ""))
    if spent["calls"]:
        per = spent["usd"] / spent["calls"]
        print(f"조당 ${per:.4f} → 전체 조 2,029개로 환산 ${per * 2029:.2f} ≈ {per * 2029 * KRW:,.0f}원 (배치 API면 절반)")


def cmd_qc(a) -> None:
    import numpy as np
    from kiwipiepy import Kiwi
    from sentence_transformers import SentenceTransformer
    import exp_embed_baseline as E

    global OUT
    OUT = DATA / f"pilot_gen_{a.prompt}.jsonl"
    recs = [json.loads(l) for l in OUT.read_text(encoding="utf-8").splitlines() if l.strip()]
    rows = E.load_db()
    ids = [r["chunk_id"] for r in rows]
    pos = {c: i for i, c in enumerate(ids)}
    node = [E.node_of_chunk(c) for c in ids]
    titles = {r["title"] for r in rows}
    E_doc = np.load(DATA / "emb_BAAI_bge_m3_v0.npy")
    kiwi = Kiwi()
    nouns = lambda t: {x.form for x in kiwi.tokenize(t) if x.tag.startswith(("NN", "VV", "VA", "XR"))}  # noqa: E731

    items = []
    for rec in recs:
        for it in rec["items"]:
            tgt = it["target"].strip().strip("()（） ")
            if tgt not in rec["chunks"] and list(rec["chunks"]) == ["조전체"]:
                tgt = "조전체"   # 청크가 조 전체 하나뿐이면 어느 항을 말하든 그 청크
            ch = rec["chunks"].get(tgt)
            if not ch:
                items.append({**it, "node": rec["node"], "reg": rec["reg"], "bad": "대상 청크 없음"})
                continue
            items.append({**it, "node": rec["node"], "reg": rec["reg"], "label": rec["label"], "chunk_id": ch["chunk_id"], "chunk": ch["text"], "bad": ""})
    print(f"조 {len(recs)}개 · 질문 {len(items)}개 · 형태별 {dict(collections.Counter(i['form'] for i in items))}")

    # 1) 형식 검사
    for i in items:
        q = i["q"].strip()
        if i["bad"]:
            continue
        if not (8 <= len(q) <= 100):
            i["bad"] = "길이"
        elif re.search(r"제\s*\d+\s*조|청크|\d+항", q):
            i["bad"] = "조 번호·청크 표시"
        elif any(t in q for t in titles if len(t) >= 4):
            i["bad"] = "규정명"
        elif i["form"] in ("상황", "간접") and i["bridge"].strip():
            left, _, right = i["bridge"].partition("→")
            i["bridge_ok"] = bool(right.strip()) and right.strip() in i["chunk"]   # 조문에 실제로 있는 낱말인지
    ok = [i for i in items if not i["bad"]]
    print(f"형식 통과 {len(ok)}/{len(items)} · 거른 사유 {dict(collections.Counter(i['bad'] for i in items if i['bad']))}")
    sit = [i for i in ok if i["form"] in ("상황", "간접")]
    print(f"상황·간접 질문 {len(sit)}개 중 bridge가 조문에 실제로 있는 낱말: {sum(1 for i in sit if i.get('bridge_ok'))}")

    # 2) 새로움: 질문의 핵심 낱말 중 조문에 그대로 있는 비율 (낮을수록 어휘가 다른 질문)
    for i in ok:
        qn, cn = nouns(i["q"]), nouns(i["chunk"])
        i["overlap"] = len(qn & cn) / max(len(qn), 1)
    FORMS = ("구어", "상황", "간접", "혼동", "검색어")
    for f in FORMS:
        v = [i["overlap"] for i in ok if i["form"] == f]
        if v:
            print(f"  낱말 겹침 평균 [{f}] {np.mean(v):.2f} · 0.7 이상(쉬운 질문) {sum(x >= 0.7 for x in v) / len(v):.0%}")

    # 3) 왕복 검사: 기본 BGE-m3 벡터로 질문 → 정답 조가 몇 위인가 (조 단위)
    model = SentenceTransformer("BAAI/bge-m3", device="cuda")
    model.half()
    Q = model.encode([i["q"] for i in ok], normalize_embeddings=True, convert_to_numpy=True, batch_size=32)
    S = Q @ E_doc.T
    for k, i in enumerate(ok):
        order = np.argsort(-S[k])
        seen, rank = set(), None
        for j in order:
            n = node[j]
            if n in seen:
                continue
            seen.add(n)
            if n == i["node"]:
                rank = len(seen)
                break
        i["rank"] = rank
    bins = [(1, 3), (4, 10), (11, 50), (51, 100), (101, 10**9)]
    print("\n왕복 검사 (정답 조의 순위, 조 단위; 상위 3은 쉬움, 4~50이 학습 신호가 큼, 100 밖은 엉뚱한 질문 의심)")
    for f in (*FORMS, "전체"):
        v = [i["rank"] for i in ok if (f == "전체" or i["form"] == f) and i["rank"]]
        if v:
            row = "  ".join(f"{lo}~{hi if hi < 10**9 else ''}위 {sum(lo <= r <= hi for r in v) / len(v):.0%}" for lo, hi in bins)
            print(f"  [{f}] n={len(v)}  {row}")
    for i in ok:
        if i["rank"] and i["rank"] > 100:
            i["bad"] = "왕복 100 밖"

    # 4) 평가 질문 누출: 평가 100문항과의 최대 코사인
    ev = [json.loads(l)["q"] for l in (DATA / "answer_eval_100.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    EV = model.encode(ev, normalize_embeddings=True, convert_to_numpy=True)
    mx = (Q @ EV.T).max(1)
    leak = [(float(m), i) for m, i in zip(mx, ok) if m >= 0.88]
    print(f"\n평가 질문과 코사인 0.88 이상: {len(leak)}개 (최대 {mx.max():.3f}) → 학습에서 제외")
    for m, i in leak[:5]:
        print(f"   {m:.3f} {i['q'][:40]}")
    leaked = {id(i) for _, i in leak}
    for i in ok:
        if id(i) in leaked:
            i["bad"] = "평가 질문과 유사"
    # 5) 같은 청크 질문끼리 중복
    by = collections.defaultdict(list)
    for k, i in enumerate(ok):
        by[i["chunk_id"]].append(k)
    dup = 0
    for ks in by.values():
        for x in range(len(ks)):
            for y in range(x + 1, len(ks)):
                if float(Q[ks[x]] @ Q[ks[y]]) >= 0.92 and not ok[ks[y]]["bad"]:
                    ok[ks[y]]["bad"] = "같은 청크 질문과 중복"
                    dup += 1
    good = [i for i in items if not i["bad"]]
    print(f"같은 청크 중복 {dup}개 · 최종 통과 {len(good)}/{len(items)} ({len(good) / max(len(items), 1):.0%})")

    # 학습용으로 내보내기: 자동 검사를 통과한 질문 (어려운 오답은 scripts/emb_build_pairs.py가 붙인다)
    with (DATA / f"pairs_{a.prompt}_good.jsonl").open("w", encoding="utf-8") as fp:
        for i in good:
            fp.write(json.dumps({"q": i["q"], "form": i["form"], "node": i["node"], "chunk_id": i["chunk_id"], "reg": i["reg"],
                                 "bridge": i["bridge"], "rank": i.get("rank")}, ensure_ascii=False) + "\n")

    # 6) 사람이 볼 표본 50쌍 (상황 25, 구어 13, 검색어 12)
    rng = random.Random(7)
    sample = []
    plan = {"v1": (("상황", 25), ("구어", 13), ("검색어", 12)), "v2": (("간접", 20), ("혼동", 15), ("구어", 8), ("검색어", 7)),
            "v3": (("간접", 30), ("구어", 10), ("검색어", 10))}[a.prompt]
    for f, n in plan:
        pool = [i for i in good if i["form"] == f]
        sample += rng.sample(pool, min(n, len(pool)))
    rng.shuffle(sample)
    import csv
    path = DATA / ("pilot_review.csv" if a.prompt == "v1" else f"pilot_review_{a.prompt}.csv")
    with path.open("w", encoding="utf-8-sig", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["번호", "판정", "메모", "형태", "질문", "규정", "조", "조문(항)", "바꿔 쓴 말(bridge)", "왕복 순위"])
        for k, i in enumerate(sample, 1):
            w.writerow([k, "", "", i["form"], i["q"], i["reg"], i["label"], i["chunk"].replace("\n", " ")[:300], i["bridge"], i.get("rank")])
    (DATA / "pilot_qc.json").write_text(json.dumps({"items": len(items), "pass": len(good), "by_form": collections.Counter(i["form"] for i in good),
                                                    "reasons": collections.Counter(i["bad"] for i in items if i["bad"])}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n사람 확인용 표본 {len(sample)}쌍 → {path}")
    print("엑셀에서 「판정」 칸에: 맞음(이 조문이 답) / 아님 / 모호 — 상황 질문은 특히 「정말 이 조문과 연결되는가」를 봐 주세요.")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["gen", "qc"])
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--model", default="gpt-5.4-mini")
    ap.add_argument("--cap-usd", type=float, default=1.0)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--fresh", action="store_true", help="이전 결과를 지우고 다시")
    ap.add_argument("--prompt", choices=["v1", "v2", "v3"], default="v1")
    a = ap.parse_args()
    {"gen": cmd_gen, "qc": cmd_qc}[a.cmd](a)


if __name__ == "__main__":
    main()
