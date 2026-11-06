"""그래프 경로(RAG 아닌 경로)가 닿지 않는 정답의 이유를 모아 어떤 관계 유형인지 본다 (평가 5묶음, 규칙 변경 없음).
  uv run python scripts/exp_unlinked_reasons.py
"""
import collections
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "scripts"))
sys.stdout.reconfigure(encoding="utf-8")
import eval_collect_dev as ecd  # noqa: E402
from regcheck.impact_eval import Truth  # noqa: E402

K1 = ecd.K
SETS = (("개발", K1, "개발"), ("1차봉인", K1, "봉인"), ("2차", ROOT / "eval/impact_label_kit_r2/산출", "봉인"), ("3차", ROOT / "eval/impact_label_kit_r3/산출", "봉인"), ("4차", ROOT / "eval/impact_label_kit_r4/산출", "봉인"))
out = []
tot = linked = 0
for tag, kit, sp in SETS:
    ecd.K = kit
    pos, neg, ut, nodes = ecd.build(sp)
    regs = None
    fin = {r["id"]: r for r in ecd.jl(kit / "labels_final.jsonl")}
    # 정답 이유 찾기: Truth.resolve로 노드 id를 맞춘다
    import glob
    regs = {r["reg_id"]: r for r in (json.loads(Path(p).read_text(encoding="utf-8")) for p in glob.glob(str(ROOT / "out/parsed/[0-9]*.json")))}
    T = Truth(nodes, regs)
    why = {}
    for i, r in fin.items():
        for e in r["영향"]:
            n = T.resolve(e)
            if n:
                why[(i, n)] = (e["이유"], e.get("확신"))
    for r in pos:
        by = {c.id: c for c in r["cands"]}
        for t in r["truth"]:
            tot += 1
            c = by.get(t)
            g = c is not None and any(p.split(":")[0] not in ("RAG", "RAG_NEW") for p in c.paths)
            linked += g
            if not g:
                out.append((tag, r["id"], r["kind"], "RAG에는 있음" if c else "후보에도 없음", why.get((r["id"], t), ("", ""))[0]))
print(f"정답 {tot}건 중 그래프 경로로 이어진 것 {linked} ({linked/tot:.0%}), 이어지지 않은 것 {len(out)}")
pats = collections.OrderedDict([
    ("같은 사항을 따로 정함/겹침", r"따로 정|별도로 정|같은 사항|겹치|중복|함께 정|동일한|같은 .{0,6}를 정|엇갈|불일치|일치"),
    ("절차·보고·후속 연계", r"보고|통보|제출|후속|절차|승인|심사|공지|안내|연결"),
    ("주체·권한·위임 변경", r"주체|권한|위임|책임관|대표이사|위원회|이사회|담당"),
    ("용어·정의 범위", r"용어|정의|범위|포함|의미"),
    ("서식·별표", r"서식|별표|별지|양식"),
    ("기간·횟수·금액 값", r"기간|횟수|금액|주기|이내|이상|값"),
])
cnt = collections.Counter()
for tag, i, k, st, why in out:
    hit = [n for n, p in pats.items() if re.search(p, why)]
    for n in hit or ["기타"]:
        cnt[n] += 1
print("\n[이유의 유형 (겹칠 수 있음)] ", dict(cnt.most_common()))
print("\n[표본 이유]")
seen = set()
for tag, i, k, st, why in out:
    if why[:40] in seen:
        continue
    seen.add(why[:40]); print(f" {tag}/{i} [{k}] ({st}) {why[:110]}")
    if len(seen) >= 36:
        break
