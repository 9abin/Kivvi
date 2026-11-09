"""4개 도메인 신규 평가셋 30건(kit_dom: 개정문 + 독립 라벨러 B1/B2 + 불일치 결정)을 평가용 케이스로 바꾼다.
사용: from kit_cases import load; cases = load(NS)   (NS = 작업 폴더, kit_dom/ 과 kg.pkl 이 있다)"""
import json, re, glob, pickle
from pathlib import Path
J = lambda p: [json.loads(l) for l in Path(p).read_text(encoding="utf-8").splitlines() if l.strip()]
norm = lambda s: re.sub(r"\s+", "", s or "")


def load(NS):
    nodes = pickle.load(open(NS / "kg.pkl", "rb"))["nodes"]
    regs = {}
    for p in glob.glob("C:/Users/Admin/Downloads/kvic_pr11/out/parsed/[0-9]*.json"):
        r = json.loads(Path(p).read_text(encoding="utf-8")); regs[re.sub(r'[\/:*?"<>|]', "_", r["name"])] = r["reg_id"]
    def uid(reg, jo):
        rid = regs.get(reg)
        if rid is None: return None
        m = re.fullmatch(r"제(\d+)조(?:의\s*(\d+))?", norm(jo))
        c = f"{rid}:{m.group(1)}" + (f"의{m.group(2)}" if m.group(2) else "") if m else f"{rid}:{norm(jo)}"
        return c if c in nodes else None
    ch = {c["id"]: c for c in J(NS / "kit_dom" / "산출" / "changes.jsonl")}
    cases, dropped = [], []
    for l in J(NS / "kit_dom" / "산출" / "labels_final.jsonl"):
        c = ch[l["id"]]; s = c["수정"]
        a = uid(s["규정"], s["조"])
        if a is None: dropped.append((l["id"], "시작 조 노드 없음")); continue
        w = []
        for e in l["영향"]:
            u = uid(e["규정"], e["조"])
            if u is None: dropped.append((l["id"], f"{e['규정']} {e['조']}"))
            elif u != a: w.append(u)
        cases.append({"id": l["id"], "split": c["분할"], "kind": s["종류"], "reg": s["규정"], "anchors": [(a, s["종류"], None)], "wanted": sorted(set(w)), "n_labeled": len(l["영향"]), "amend": s["개정문"]})
    return cases, dropped
