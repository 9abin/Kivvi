"""실험: 후보 수집 결과를 LLM이 읽고 영향 여부를 답하게 하면 정렬이 좋아지는가. 프롬프트 변형 비교 (영향 점검 평가셋 68, 개발 분할).

  uv run python scripts/exp_infer_dev.py --prompt P1|P2|P3 [--model gpt-5.6-luna] [--per-item 60]
프롬프트 (사전에 정한 3개; 결과를 보고 더 늘리지 않는다)
  P1  상/중/하/불가 3단계 (첫 시도)
  P2  영향으로 보는 경우 4가지와 아닌 경우를 명시, 후보가 된 이유를 같이 줌, 「예/아니오/불확실」 + 유형
  P3  P2 + 가상 예시 3개 (평가 규정 밖의 가상 차량관리지침)
외부 전송: 가상 개정문과 공개 규정(ALIO) 문장만 API 모델에 보낸다 (평가 목적). 호출 결과는 out/exp/infer_<model>_<프롬프트>.json에 캐시한다.
LLM은 후보를 지우지 않는다. 정렬에만 쓰고 정답 후보를 아래로 내리는 비율(재현율 손실)을 같이 잰다.
근거 문장은 후보 원문에 글자 그대로 있어야 인정한다.
"""
import argparse
import collections
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.stdout.reconfigure(encoding="utf-8")

from eval_collect_dev import build  # noqa: E402
from regcheck.nl import _post  # noqa: E402

P1 = """회사 내부 규정(내규)을 개정하려 한다. 개정 내용과 확인 대상 조문이 있다. 이 개정이 확인 대상 조문에 영향을 주어 담당자가 살펴봐야 하는지 판단한다.

[개정문]
{amend}

[수정되는 조] {where}
- 바뀌기 전 문구: {removed}
- 바뀐 뒤 문구: {added}

[확인 대상 조문] {label}
{body}

출력은 JSON 한 줄만. 다른 글은 쓰지 않는다.
{{"영향": "상" 또는 "중" 또는 "하" 또는 "불가", "근거": "확인 대상 조문에서 글자 그대로 복사한 문장(60자 이내)", "이유": "한 문장"}}

규칙
- 상: 개정 내용에 직접 기대거나 어긋나서 고쳐야 할 가능성이 높음. 중: 관련은 있으나 확인이 필요함. 하: 관련이 약함. 불가: 판단할 수 없음.
- 근거는 확인 대상 조문에 있는 글자를 그대로 쓴다. 조 번호나 숫자를 새로 만들지 않는다.
- 법적 판단을 하지 않는다."""

P2 = """회사 내부 규정(내규)을 개정하려 한다. 담당자가 이 개정 때문에 함께 확인·정비해야 하는 조문을 가려내는 일을 돕는다.

[영향으로 보는 경우] 아래 중 하나에 해당하고, 개정으로 바뀐 내용이 확인 대상 조문의 문장·번호·값·용어에 실제로 닿을 때만 영향이다.
 1. 인용: 개정되거나 번호가 밀리거나 삭제되는 조를 번호나 이름으로 인용·준용·위임한다.
 2. 값·기준: 같은 사항(같은 대상의 기한·횟수·금액·기준)을 따로 정하고 있어 개정 후 값이나 내용이 어긋난다.
 3. 용어: 개정으로 정의가 바뀐 용어를 그 뜻으로 쓰고 있어 범위·적용을 확인해야 한다.
 4. 후속절차: 개정으로 새로 생기거나 달라지는 기준·절차와 이어지는 후속 절차·서식·별표다.
[영향이 아닌 경우] 주제나 낱말만 비슷하고 개정된 내용과 연결되지 않는다. 같은 낱말이 다른 뜻으로 쓰였다. 개정과 무관한 일반 규정이다.

[개정문]
{amend}

[수정되는 조] {where}
- 바뀌기 전 문구: {removed}
- 바뀐 뒤 문구: {added}

[확인 대상 조문] {label}
{body}

[이 조문이 후보가 된 이유(자동 검색)] {reason}

출력은 JSON 한 줄만. 다른 글은 쓰지 않는다.
{{"영향": "예" 또는 "아니오" 또는 "불확실", "유형": "인용" 또는 "값·기준" 또는 "용어" 또는 "후속절차" 또는 "해당없음", "근거": "확인 대상 조문에서 글자 그대로 복사한 문장(60자 이내)", "이유": "한 문장"}}

규칙
- 후보가 된 이유는 검색이 남긴 단서일 뿐이다. 영향 여부는 개정된 내용이 이 조문에 실제로 닿는지로만 판단한다. 낱말이 겹친다는 이유만으로 「예」라고 하지 않는다.
- 근거는 확인 대상 조문에 있는 글자를 그대로 쓴다. 조 번호나 숫자를 새로 만들지 않는다.
- 개정으로 달라지는 부분이 이 조문과 닿는지 알 수 없으면 「불확실」이다. 법적 판단을 하지 않는다."""

P4 = """회사 내부 규정(내규)의 한 조문이 개정된다. 개정 때문에 아래 확인 대상 조문도 함께 살펴보거나 고쳐야 하는지 판단한다.

판단 원칙: 개정으로 바뀌는 내용(바뀌기 전과 뒤의 차이)이 확인 대상 조문의 내용에 실제로 닿는가를 본다. 닿는다는 것은, 확인 대상 조문이 개정되는 부분에 기대고 있거나, 같은 사항을 따로 정하고 있거나, 개정으로 달라지는 뜻을 전제로 쓰고 있는 경우다. 주제나 낱말이 비슷하기만 한 것은 닿는 것이 아니다.

[개정문]
{amend}

[수정되는 조] {where}
- 바뀌기 전 문구: {removed}
- 바뀐 뒤 문구: {added}

[확인 대상 조문] {label}
{body}

[후보가 된 이유(자동 검색, 참고만)] {reason}

출력은 JSON 한 줄만. 다른 글은 쓰지 않는다.
{{"영향": "예" 또는 "아니오" 또는 "불확실", "근거": "확인 대상 조문에서 글자 그대로 복사한 문장(60자 이내)", "이유": "한 문장"}}

규칙
- 근거는 확인 대상 조문에 있는 글자를 그대로 쓴다. 없는 조 번호나 숫자를 만들지 않는다.
- 닿는지 알 수 없으면 「불확실」이다. 닿지 않는다고 확신할 때만 「아니오」다. 법적 판단을 하지 않는다."""
SHOTS = """
[예시 1] 개정문: 제5조제1항 중 "7일"을 "10일"로 한다. (차량관리지침 제5조 차량 반납 기한)
확인 대상 조문: 업무용 차량의 반납은 제5조제1항에 따른 기한 안에 한다. -> {"영향": "예", "유형": "인용", "근거": "제5조제1항에 따른 기한", "이유": "개정된 기한을 직접 인용한다."}
[예시 2] 개정문: 제5조제1항 중 "7일"을 "10일"로 한다. (차량관리지침 제5조 차량 반납 기한)
확인 대상 조문: 도서는 대출일부터 7일 안에 반납한다. -> {"영향": "아니오", "유형": "해당없음", "근거": "대출일부터 7일 안에 반납한다", "이유": "같은 7일이지만 다른 대상(도서)의 별개 기한이다."}
[예시 3] 개정문: 제2조제3호 중 "임직원"을 "임직원 및 협력업체 직원"으로 한다. (보안지침 제2조 취급자의 정의)
확인 대상 조문: 취급자는 연 1회 보안교육을 받는다. -> {"영향": "예", "유형": "용어", "근거": "취급자는 연 1회 보안교육을 받는다", "이유": "정의가 넓어진 취급자에게 교육 의무가 새로 적용될 수 있다."}
"""
SHOTS = SHOTS.replace("{", "{{").replace("}", "}}")   # str.format에서 예시의 JSON 중괄호가 자리표시자로 읽히지 않게
PROMPTS = {"P4": P4, "P1": P1, "P2": P2, "P3": P2.replace("[개정문]\n{amend}", SHOTS + "\n[실제 판단 대상]\n[개정문]\n{amend}", 1)}
SCORE = {"상": 3, "중": 2, "하": 1, "불가": 1.5, "예": 3, "불확실": 1.5, "아니오": 0.5}
REASON = {"CITES_in": "개정된 조를 번호로 인용함", "STALE_CITE": "옛 번호를 그대로 인용하고 있을 수 있음", "USES_TERM": "정의가 바뀐 용어를 씀", "TERM_CORE": "정의가 바뀐 용어의 핵심 부분이 나옴",
          "MENTIONS_TITLE": "개정된 조의 제목이 본문에 나옴", "MENTIONS_STEM": "개정된 조의 제목 줄기가 나옴", "SAME_TITLE": "다른 규정에 제목이 같은 조",
          "SAME_VALUE": "같은 대상어와 같은 값이 있음", "CITES_REG": "규정 이름만 인용함", "PHRASE": "개정으로 새로 들어간 말이 나옴", "RAG": "의미가 비슷함(자동 검색)",
          "VALUE_DIFF": "같은 종류의 기한이 다름", "DEF_DIFF": "같은 용어의 정의가 다름", "MENTIONS_FORM": "서식 이름이 나옴", "SHARED_ATTACH": "같은 서식·별표를 씀", "MENTIONS_ORG": "같은 조직 이름이 나옴"}


def norm(s):
    return re.sub(r"\s+", "", s or "")


def parse(s):
    m = re.search(r"\{.*\}", s or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
        return d if d.get("영향") in SCORE else None
    except json.JSONDecodeError:
        return None


def auc(pairs):
    pos = [s for s, y in pairs if y]; neg = [s for s, y in pairs if not y]
    if not pos or not neg:
        return None
    return sum((p > n) + 0.5 * (p == n) for p in pos for n in neg) / (len(pos) * len(neg))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", default="P1", choices=sorted(PROMPTS))
    ap.add_argument("--model", default="gpt-5.6-luna")
    ap.add_argument("--per-item", type=int, default=60)
    ap.add_argument("--splits", default="개발")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    pos_rows, neg_rows, unit_text, nodes = build(a.splits)
    tag = re.sub(r"[^A-Za-z0-9._-]", "_", a.model)
    cache_p = ROOT / "out/exp" / (f"infer_{tag}.json" if a.prompt == "P1" else f"infer_{tag}_{a.prompt}.json")
    cache_p.parent.mkdir(parents=True, exist_ok=True)
    cache = json.loads(cache_p.read_text(encoding="utf-8")) if cache_p.exists() else {}
    tmpl = PROMPTS[a.prompt]
    jobs = []
    for r in pos_rows + neg_rows:
        cq, tx = r["cq"], r["tx"]
        for c in r["cands"][:a.per_item]:
            key = f"{r['id']}|{c.id}"
            if key in cache:
                continue
            nd = nodes.get(c.id, {})
            label = f"{nd.get('label', c.id)} {nd.get('title', '')}".strip()
            reason = " / ".join(dict.fromkeys(REASON.get(p.split(':')[0] if p.startswith(('SAME_TITLE', 'CITES_REG')) else p, p) for p in c.paths))
            prompt = tmpl.format(amend=tx["amend"], where=f"{tx['title']} ({', '.join(cq.units[:2])})", removed=(" / ".join(cq.removed)[:300] or "(없음)"),
                                 added=(" / ".join(cq.added)[:300] or "(없음)"), label=label, body=unit_text.get(c.id, "")[:900], reason=reason)
            jobs.append((key, prompt))
    print(f"[{a.prompt}] 모델 {a.model}: 새로 호출할 후보 {len(jobs)}개 (캐시 {len(cache)}개)")

    def call(job):
        key, prompt = job
        try:
            return key, {"raw": _post(a.model, [{"role": "user", "content": prompt}], timeout=120)}
        except Exception as ex:  # noqa: BLE001
            return key, {"error": f"{type(ex).__name__}: {str(ex)[:80]}"}

    with ThreadPoolExecutor(a.workers) as ex:
        for n, (key, res) in enumerate(ex.map(call, jobs), 1):
            cache[key] = res
            if n % 40 == 0:
                cache_p.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    cache_p.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")

    def judged(row, c):
        d = parse((cache.get(f"{row['id']}|{c.id}") or {}).get("raw"))
        if not d:
            return None
        return {"점수": SCORE[d["영향"]], "영향": d["영향"], "근거일치": bool(d.get("근거")) and norm(d["근거"]) in norm(unit_text.get(c.id, ""))}

    labels = ("상", "중", "하", "불가") if a.prompt == "P1" else ("예", "불확실", "아니오")
    allp = [(c.tier, judged(r, c), c.id in r["truth"]) for r in pos_rows for c in r["cands"][:a.per_item] if judged(r, c)]
    fail = sum(1 for r in pos_rows for c in r["cands"][:a.per_item] if judged(r, c) is None)
    print(f"\n[판단 결과 {a.prompt}] 후보 {len(allp)}개 (형식 오류 {fail}개). 근거 문장이 원문과 일치한 비율 {sum(j['근거일치'] for _, j, _ in allp)/max(len(allp), 1):.0%}")
    print(f"  라벨 순서: {'/'.join(labels)}")
    for tier in ("확정급", "확인 권장", "참고", None):
        sub = [(j, y) for t, j, y in allp if tier is None or t == tier]
        dist = collections.Counter((j["영향"], y) for j, y in sub)
        u = auc([(j["점수"] - (0 if j["근거일치"] else 0.4), y) for j, y in sub])
        print(f"  {tier or '전체':8} 후보 {len(sub):3d} (정답 {sum(y for _, y in sub)}) | 정답 후보 " + "/".join(str(dist[(k, True)]) for k in labels) + " | 비정답 후보 " + "/".join(str(dist[(k, False)]) for k in labels) + (f" | AUC {u:.2f}" if u is not None else ""))
    print("\n[정렬 효과] 후보 상한에서의 재현율 (확정급 먼저 + 나머지를 LLM 점수순)")
    for cap in (10, 20):
        base = new = tot = 0
        for r in pos_rows:
            lst = r["cands"][:a.per_item]
            strong = [c for c in lst if c.tier == "확정급"]
            rest = sorted([c for c in lst if c.tier != "확정급"], key=lambda c: -(judged(r, c) or {"점수": 1.5})["점수"])
            tot += len(r["truth"]); base += len(r["truth"] & {c.id for c in lst[:cap]}); new += len(r["truth"] & {c.id for c in (strong + rest)[:cap]})
        print(f"  상한 {cap}: 원래 {base}/{tot} ({base/tot:.0%}) -> LLM 정렬 {new}/{tot} ({new/tot:.0%})")
    low_label = "하" if a.prompt == "P1" else "아니오"
    allpos = [j for _, j, y in allp if y]
    print(f"\n[재현율 손실 위험] 정답 후보 {len(allpos)}개 중 「{low_label}」로 본 것 {sum(1 for j in allpos if j['영향'] == low_label)}개")
    hi = ("상", "중") if a.prompt == "P1" else ("예", "불확실")
    print("[영향 없음 문항] 후보 중 " + "/".join(hi) + " 으로 본 수 (낮을수록 좋음)")
    for r in neg_rows:
        js = [judged(r, c) for c in r["cands"][:a.per_item]]
        print(f"  {r['id']}[{r['kind']}]: 후보 {len(js)}개 중 " + ", ".join(f"{k} {sum(1 for j in js if j and j['영향'] == k)}" for k in hi))


if __name__ == "__main__":
    main()
