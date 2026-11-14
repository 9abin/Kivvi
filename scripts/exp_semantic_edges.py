"""실험: 의미 쪽 자산(연관 조 표 · 의미 이웃 · 주제어 사전)을 후보로 더하면 기본 화면(RAG 30 + 그래프 20)이 못 가져온 정답을 얼마나 더 가져오나.
  uv run python scripts/exp_semantic_edges.py
기본 화면은 그대로 두고, 각 자산이 내는 후보 중 기본 화면에 없는 것만 센다 (후보 수 · 정답 수 · 정밀도). 4개 평가 묶음(전부 규칙을 고른 문항이라 독립 평가 아님).
  related_articles.json  조 -> 같은 주제어를 담은 연관 조 (자동 초안, 등급 핵심/참고)
  neighbors.json         조 -> 임베딩 유사 조 (점수)
  topics.json            주제어 사전 (바뀌는 조에 주제어가 있으면 같은 주제어를 담은 다른 조, 주제당 40개 이하)
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
from regcheck.review_list import split_view  # noqa: E402

rel = json.loads((ROOT / "data/manual/related_articles.json").read_text(encoding="utf-8"))
nb = json.loads((ROOT / "out/relations/neighbors.json").read_text(encoding="utf-8"))
topics = {k: v for k, v in json.loads((ROOT / "data/manual/topics.json").read_text(encoding="utf-8")).items() if not k.startswith("_")}


def nid(s):   # "10770_제3조의2" -> "10770:3의2"
    m = re.match(r"(\d+)_제(\d+)조(?:의(\d+))?", s)
    return f"{m.group(1)}:{m.group(2)}" + (f"의{m.group(3)}" if m.group(3) else "") if m else None


K1 = ecd.K
SETS = (("개발", K1, "개발"), ("1차봉인", K1, "봉인"), ("2차", ROOT / "eval/impact_label_kit_r2/산출", "봉인"), ("3차", ROOT / "eval/impact_label_kit_r3/산출", "봉인"), ("4차", ROOT / "eval/impact_label_kit_r4/산출", "봉인"))
stat = collections.defaultdict(collections.Counter)
for tag, kit, sp in SETS:
    ecd.K = kit
    pos, neg, unit_text, nodes = ecd.build(sp)
    ntext = {k: re.sub(r"\s+", "", v) for k, v in unit_text.items()}
    for r in pos:
        starts = r["cq"].starts[:6]
        base = {c.id for c in split_view(r["cands"])["default"]} | set(r["cq"].starts)
        miss = r["truth"] - base
        src = {"연관표 핵심": set(), "연관표 전체": set(), "이웃 ≥0.80": set(), "이웃 전체": set(), "주제어": set()}
        for s in starts:
            if "reg" not in nodes.get(s, {}):   # 법령 노드 등은 연관표에 없다
                continue
            reg, lab = nodes[s]["reg"], nodes[s]["label"]
            key = f"{reg}_{lab}"
            for e in rel.get(key, []):
                t = nid(e["대상"])
                if t:
                    src["연관표 전체"].add(t)
                    if e["등급"] == "핵심":
                        src["연관표 핵심"].add(t)
            for e in nb.get(s, []):
                src["이웃 전체"].add(e["article_id"])
                if e["score"] >= 0.80:
                    src["이웃 ≥0.80"].add(e["article_id"])
            text = ntext.get(s, "")
            for name, v in topics.items():
                terms = [re.sub(r"\s+", "", t) for t in v["terms"]]
                if any(t in text for t in terms):
                    hits = [k for k, x in ntext.items() if any(t in x for t in terms)]
                    if len(hits) <= 40:
                        src["주제어"].update(hits)
        for name, ids in src.items():
            ids = {i for i in ids if i in nodes} - base
            stat[name]["문항"] += 1
            stat[name]["새 후보"] += len(ids)
            stat[name]["새 정답"] += len(ids & r["truth"])
            stat[name]["남은 정답"] += len(miss)
        stat["_합계"]["남은 정답"] += len(miss)
print("[기본 화면이 못 가져온 정답 수] ", stat["_합계"]["남은 정답"], "(5개 묶음 합, 영향 있는 문항)")
for name in ("연관표 핵심", "연관표 전체", "이웃 ≥0.80", "이웃 전체", "주제어"):
    c = stat[name]
    print(f"  {name:10} 새 후보 {c['새 후보']:5d} | 새 정답 {c['새 정답']:3d} ({c['새 정답']/max(c['새 후보'],1):.1%}) | 문항당 새 후보 {c['새 후보']/max(c['문항'],1):.0f}")
