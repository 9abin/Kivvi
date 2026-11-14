"""엉뚱한 조 인용을 LLM이 잡을 수 있나 — 규칙(설명구 + 리랭커) 대 로컬 LLM 대 API (오류 주입, 짝 시험).

  uv run python scripts/eval_inject_llm.py --n 60 --seed 1 --models qwen2.5:7b qwen3-30b-a3b-2507-q4km-ctx12k api:gpt-5.6-luna
같은 인용을 짝으로 시험한다 (scripts/eval_inject_semantic.py와 같은 주입 방식, 다만 표본은 설명구 유무와 관계없이 조 인용 전체에서)
  원래   인용 문장 + 실제로 인용한 조 → 「맞음」이어야 함 (「틀림」이면 오탐)
  주입   같은 인용 문장 + 같은 규정의 먼 조 → 「틀림」이어야 함 (「맞음」이면 놓침)
LLM에는 인용 문장(인용 표현은 [인용]으로 가림)과 후보 조문(규정명 · 조 제목 · 본문 앞부분)만 준다. 판정: 맞음 / 틀림 / 모름.
규칙 방식은 설명구(「…에 따른 X」)가 있는 인용만 검사할 수 있다 → 설명구가 없으면 「검사 못 함」으로 센다.
API(api:모델)는 OpenAI 호환(.env의 OPENAI_API_KEY). 보내는 내용은 공개 규정(ALIO) 문장뿐이다. 출력: out/eval/inject_llm_s{seed}.json
"""

import argparse
import json
import os
import random
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from regcheck.relocate import citing_text  # noqa: E402
from semantic_check import RERANKER, article_text, descriptor, free_gpu, load_regs  # noqa: E402

PROMPT_V1 = ("너는 회사 내부 규정의 인용 관계를 점검한다. 규정 문장 속 [인용] 자리는 다른 조문을 가리킨다.\n"
             "주어진 후보 조문이 그 [인용] 자리에 들어가는 것이 내용상 맞는지 판단한다. 문장이 그 조문의 내용(제목, 정한 사항)을 근거로 삼거나 가리키고 있으면 맞음,"
             " 문장이 말하는 내용과 조문 내용이 어긋나면 틀림, 정보가 부족해 판단할 수 없으면 모름.\n"
             '출력은 JSON 하나만: {"판정": "맞음|틀림|모름", "이유": "한 문장"}')
# v2 (시드 1의 오탐 18건을 읽고 고침): 규정 인용은 「그 조에 나오는 대상 · 절차」를 가리키는 경우가 많다.
# 「[인용]에 따라 인수한 기록물」(인용한 조 = 이관 절차), 「[인용]의 가족」(인용한 조 = 가족여비 지급)은 맞는 인용인데 v1은 「배치 · 범위를 정하지 않는다」며 틀림으로 봤다
PROMPT = ("너는 회사 내부 규정의 인용 관계를 점검한다. 규정 문장 속 [인용] 자리는 다른 조문을 가리킨다.\n"
          "규정의 인용은 「그 조에서 정한 사항에 따라」뿐 아니라 「그 조에 나오는 대상(용어 · 사람 · 기구 · 서류)」이나 「그 조에 따른 절차의 결과」를 가리키기도 한다."
          " 그래서 인용한 조가 문장의 내용을 직접 정하고 있지 않아도, 문장이 가리키는 대상이나 절차를 그 조가 다루거나 언급하면 맞음이다.\n"
          "후보 조문의 주제가 문장이 가리키는 대상 · 절차와 전혀 관계없을 때만 틀림. 관계가 애매하면 모름.\n"
          '출력은 JSON 하나만: {"판정": "맞음|틀림|모름", "이유": "한 문장"}')


PROMPT_CHOOSE = ("너는 회사 내부 규정의 인용 관계를 점검한다. 규정 문장 속 [인용] 자리는 아래 목록 중 한 조문을 가리킨다.\n"
                 "규정의 인용은 그 조에서 정한 사항뿐 아니라 그 조에 나오는 대상(용어 · 사람 · 기구 · 서류)이나 절차를 가리키기도 한다.\n"
                 "목록에서 [인용] 자리에 가장 알맞은 조문 하나를 고른다. 둘 이상이 비슷하게 알맞으면 둘째 후보도 적는다.\n"
                 '출력은 JSON 하나만: {"고른 조": "제N조", "둘째 후보": "제M조 또는 없음", "이유": "한 문장"}')


def ask_local(model: str, user: str, system: str | None = None) -> str:
    body = {"model": model, "messages": [{"role": "system", "content": system or PROMPT}, {"role": "user", "content": user}],
            "stream": False, "think": False, "format": "json", "options": {"temperature": 0, "num_predict": 160}}
    req = urllib.request.Request("http://127.0.0.1:11434/api/chat", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())["message"]["content"]


def ask_api(model: str, user: str, system: str | None = None) -> str:
    key = os.environ.get("OPENAI_API_KEY")
    body = {"model": model, "messages": [{"role": "system", "content": system or PROMPT}, {"role": "user", "content": user}],
            "response_format": {"type": "json_object"}}
    req = urllib.request.Request("https://api.openai.com/v1/chat/completions", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())["choices"][0]["message"]["content"]


def verdict(raw: str) -> str:
    try:
        v = json.loads(raw).get("판정", "")
    except Exception:
        v = raw
    return "틀림" if "틀" in v else "맞음" if "맞" in v else "모름"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--rank", type=int, default=5, help="규칙 방식: 리랭커 순위가 이 밖이면 「틀림」")
    ap.add_argument("--chars", type=int, default=600)
    ap.add_argument("--models", nargs="+", default=["qwen2.5:7b"])
    ap.add_argument("--mode", choices=["judge", "choose"], default="judge",
                    help="judge: 후보 조 하나가 맞는지 / choose: 대상 규정의 조 목록에서 가장 알맞은 조를 고르게 해 인용한 조와 비교")
    a = ap.parse_args()
    from regcheck.db import load_env
    os.environ.setdefault("OPENAI_API_KEY", load_env().get("OPENAI_API_KEY", "").strip('"'))   # load_env는 사전만 돌려준다
    regs = load_regs()
    edges = [json.loads(l) for l in (ROOT / "out" / "edges" / "edges_all.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    pool = [e for e in edges if e["status"] == "ok" and e["target_type"] == "article" and e["src_kind"] == "본문" and e.get("target_reg") in regs]
    rng = random.Random(a.seed)
    rng.shuffle(pool)
    picks = []
    for e in pool:
        tgt = regs[e["target_reg"]]
        src_article = e["src"].rsplit("-", 3)[0]
        live = [x for x in tgt["articles"] if not x["deleted"] and x["id"] != src_article]
        idx = next((i for i, x in enumerate(live) if x["id"] == e["target_id"]), None)
        sent = citing_text(regs, e)
        if idx is None or len(live) < 8 or "[인용]" not in sent:
            continue
        far = [x for i, x in enumerate(live) if abs(i - idx) > 2]
        picks.append({"edge": e, "sent": sent, "desc": descriptor(regs, e), "wrong": rng.choice(far)["id"]})
        if len(picks) == a.n:
            break
    arts = {x["id"]: (r, x) for r in regs.values() for x in r["articles"]}

    def card(aid):
        r, x = arts[aid]
        return f"{r['name']} {article_text(x, a.chars)}"

    rows = [{"src": p["edge"]["src"], "src_name": p["edge"]["src_name"], "sent": p["sent"], "has_desc": bool(p["desc"]),
             "orig": p["edge"]["target_id"], "wrong": p["wrong"], "res": {}} for p in picks]
    # 규칙 방식 (설명구 + 리랭커 순위)
    from sentence_transformers import CrossEncoder
    model = CrossEncoder(RERANKER, device="cuda", model_kwargs={"dtype": "float16"})
    for p, row in zip(picks, rows):
        if not p["desc"]:
            row["res"]["규칙"] = {"orig": "검사 못 함", "wrong": "검사 못 함"}
            continue
        e = p["edge"]
        tgt = regs[e["target_reg"]]
        src_article = e["src"].rsplit("-", 3)[0]
        cand = [x for x in tgt["articles"] if not x["deleted"] and x["id"] != src_article]
        sc = model.predict([(p["desc"], article_text(x, 300)) for x in cand], batch_size=16, show_progress_bar=False)
        ranked = [x["id"] for _, x in sorted(zip(sc.tolist(), cand), key=lambda z: -z[0])]
        row["res"]["규칙"] = {k: ("틀림" if ranked.index(row[k]) + 1 > a.rank else "맞음") for k in ("orig", "wrong")}
    del model
    free_gpu()
    for m in a.models:
        api = m.startswith("api:")
        name = m.split(":", 1)[1] if api else m
        secs = []
        for row in rows:
            out = {}
            if a.mode == "choose":   # 한 번 물어 원래 · 주입 둘 다 판정 (고른 조와 같으면 맞음)
                tgt_reg = arts[row["orig"]][0]
                lines = [f"{x['label']}({x['title']}) {article_text(x, 90)[len(x['label']) + len(x['title']) + 3:]}"
                         for x in tgt_reg["articles"] if not x["deleted"] and x["id"] != row["src"].rsplit("-", 3)[0]]
                user = f"문장: {row['sent']}\n\n「{tgt_reg['name']}」 조문 목록:\n" + "\n".join(lines)
                t = time.time()
                try:
                    raw = (ask_api if api else ask_local)(name, user, PROMPT_CHOOSE)
                    js = json.loads(raw)
                    picks = {re.sub(r"\s+", "", m.group(0)) for f in ("고른 조", "둘째 후보")   # 「제5조(목적)」에서 조 번호만
                             for m in re.finditer(r"제\s*\d+\s*조(?:\s*의\s*\d+)?", str(js.get(f, "")))}
                except Exception as ex:
                    raw, picks = f"오류 {ex}", set()
                secs.append(time.time() - t)
                lab = {k: re.sub(r"\s+", "", arts[row[k]][1]["label"]) for k in ("orig", "wrong")}
                for k in ("orig", "wrong"):
                    out[k] = "모름" if not picks else ("맞음" if lab[k] in picks else "틀림")
                    out[k + "_raw"] = raw[:200]
                row["res"][m] = out
                continue
            for k in ("orig", "wrong"):
                user = f"문장: {row['sent']}\n\n후보 조문: {card(row[k])}"
                t = time.time()
                try:
                    raw = (ask_api if api else ask_local)(name, user)
                except Exception as ex:
                    raw = f"오류 {ex}"
                secs.append(time.time() - t)
                out[k] = verdict(raw)
                out[k + "_raw"] = raw[:200]
            row["res"][m] = out
        print(f"  {m}: 판정 {len(secs)}건, 건당 {sum(secs) / len(secs):.1f}초", flush=True)
    n = len(rows)
    print(f"\n엉뚱한 조 인용 (시드 {a.seed}, 짝 {n}개, 설명구 있는 인용 {sum(r['has_desc'] for r in rows)}개)")
    print("방식 | 주입 탐지(틀림) | 원래 인용 오탐(틀림) | 모름 · 검사 못 함")
    for m in ["규칙"] + a.models:
        caught = sum(r["res"][m]["wrong"] == "틀림" for r in rows)
        fp = sum(r["res"][m]["orig"] == "틀림" for r in rows)
        unk = sum(r["res"][m][k] in ("모름", "검사 못 함") for r in rows for k in ("orig", "wrong"))
        print(f"{m} | {caught}/{n} ({caught / n:.0%}) | {fp}/{n} ({fp / n:.0%}) | {unk}/{2 * n}")
    p = ROOT / "out" / "eval" / f"inject_llm_s{a.seed}{'_choose' if a.mode == 'choose' else ''}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
