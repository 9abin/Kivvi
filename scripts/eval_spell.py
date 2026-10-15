"""오타·맞춤법 검사 평가: 실제 조문 문장에 오타를 넣은 문장 + 손대지 않은 문장을 로컬 LLM에 넣어 비교한다.

  uv run python scripts/eval_spell.py make [--seed 1] [--per 10]       # eval/agent/spell_s1.jsonl
  uv run python scripts/eval_spell.py run --model qwen2.5:7b [--seed 1] [--split dev|test|all]
오타 종류: 자리바꿈(휴직기간→휴기직간) · 모음(직→즉, 애↔에) · 받침빠짐 · 맞춤법(됀다·돼어·로써·던지·안는) · 반복(지급급)
탐지 = 확인 규칙(regcheck.spell.verify)을 통과한 지적이 넣은 낱말과 겹침. 오경보 = 손대지 않은 문장에 지적이 나옴
(원문 자체의 오타일 수도 있어 목록을 함께 보여 준다). 번호 짝수 = 개발용, 홀수 = 시험용.
"""

import argparse
import collections
import json
import random
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from regcheck import runinfo  # noqa: E402
from regcheck.spell import _ask, corpus_text, verify  # noqa: E402
from regcheck.typo_gen import mutate  # noqa: E402

def make(seed: int, per: int) -> list[dict]:
    rng = random.Random(seed)
    sents = []
    for p in sorted((ROOT / "out" / "parsed").glob("[0-9]*.json")):
        r = json.loads(p.read_text(encoding="utf-8"))
        for a in r["articles"]:
            for it in a["items"]:
                t = re.sub(r"\s*[<\[][^<>\[\]]*[>\]]", "", it["norm"]).strip()
                if 30 <= len(t) <= 180 and len(re.findall(r"[가-힣]", t)) > len(t) * 0.6:
                    sents.append((r["name"], a["label"], t))
    rng.shuffle(sents)
    out, i = [], 0
    for kind in ("자리바꿈", "모음", "받침빠짐", "맞춤법", "반복"):
        k = 0
        while k < per and i < len(sents):
            name, lab, s = sents[i]
            i += 1
            m = mutate(kind, s, rng)
            if not m:
                continue
            out.append({"kind": kind, "reg": name, "article": lab, "text": m[0], "orig": m[1], "typo": m[2]})
            k += 1
    for name, lab, s in sents[i: i + len(out)]:
        out.append({"kind": "정상", "reg": name, "article": lab, "text": s})
    rng.shuffle(out)
    for n, x in enumerate(out, 1):
        x["id"] = f"T{n:03d}"
    return out


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["make", "run"])
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--per", type=int, default=10)
    ap.add_argument("--model", default="qwen2.5:7b")
    ap.add_argument("--split", choices=["all", "dev", "test"], default="all")
    a = ap.parse_args()
    path = ROOT / "eval" / "agent" / f"spell_s{a.seed}.jsonl"
    if a.cmd == "make":
        cases = make(a.seed, a.per)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in cases), encoding="utf-8")
        print(f"{len(cases)}문장 → {path}  {dict(collections.Counter(x['kind'] for x in cases))}")
        return
    cases = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    if a.split != "all":
        cases = [x for x in cases if (int(x["id"][1:]) % 2 == 0) == (a.split == "dev")]
    run = runinfo.start("eval_spell", [path], models={"llm": a.model}, params=vars(a))
    corpus = corpus_text()
    rec = collections.defaultdict(lambda: [0, 0])
    raw_rec = collections.defaultdict(lambda: [0, 0])
    fp, fp_raw, n_clean, secs, res = 0, 0, 0, [], []
    for x in cases:
        t0 = time.time()
        raw = _ask(x["text"], a.model)
        secs.append(time.time() - t0)
        ok = [e for e in raw if verify(x["text"], e, corpus)]
        if x["kind"] == "정상":
            n_clean += 1
            fp += bool(ok)
            fp_raw += bool(raw)
        else:
            def hit(es):
                return any(x["typo"] in str(e["wrong"]) or str(e["wrong"]) in x["typo"] for e in es)
            rec[x["kind"]][0] += hit(ok)
            rec[x["kind"]][1] += 1
            raw_rec[x["kind"]][0] += hit(raw)
            raw_rec[x["kind"]][1] += 1
        res.append({**x, "raw": raw, "kept": ok, "sec": round(secs[-1], 1)})
    out = ROOT / "out" / "eval" / f"spell_s{a.seed}_{a.model.replace(':', '_').replace('/', '_')}_{a.split}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in res), encoding="utf-8")
    tot = [sum(v[0] for v in rec.values()), sum(v[1] for v in rec.values())]
    print(f"[{a.model}] 문장 {len(cases)} ({a.split}) · 문장당 평균 {sum(secs) / len(secs):.1f}초")
    print("오타 종류\t탐지(확인 후)\t(확인 전)")
    for k in rec:
        print(f"  {k}\t{rec[k][0]}/{rec[k][1]}\t{raw_rec[k][0]}/{raw_rec[k][1]}")
    print(f"  전체\t{tot[0]}/{tot[1]} ({tot[0] / max(tot[1], 1):.0%})")
    print(f"손대지 않은 문장 {n_clean}개 중 지적이 나온 문장: 확인 후 {fp} / 확인 전 {fp_raw}")
    for r in res:
        if r["kind"] == "정상" and r["kept"]:
            print(f"  [오경보?] {r['reg']} {r['article']}: {[(e['wrong'], e['right']) for e in r['kept']]}")
    runinfo.finish(run, [out], {"탐지": tot, "오경보 문장": fp, "확인 전 오경보": fp_raw, "평균 초": round(sum(secs) / len(secs), 1)})


if __name__ == "__main__":
    main()
