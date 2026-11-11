"""정답 항목마다 시스템 단위 id를 붙인다 (261111). 사용: python eval/relabel_v2/resolve_ids.py

규정 파일 안에 첨부 문서(별지 · 별첨 규칙)가 있으면 같은 번호의 조가 둘 이상일 수 있다(예: 취업규칙 본문 제9조 신원보증보험 /
첨부 제9조 퇴직절차). 그래서 「규정 + 조 번호」만으로 id를 정하면 틀린 조가 정답이 된다. 근거문장 위치로 정한다.

id_근거
- 조 번호: 라벨의 조(또는 별표 · 별지) 안에 근거문장이 있다
- 근거문장 위치: 라벨의 조에는 없고, 같은 규정의 다른 단위(첨부 문서 · 별지 등) 한 곳에 있다 -> 그 단위
- 대응 없음: 어느 단위에도 없거나 여러 곳 -> id 없음 (평가에서 빠짐)
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8")

_PUNCT = str.maketrans({"ㆍ": "·", "․": "·", "‧": "·", "∙": "·", "“": '"', "”": '"', "‘": "'", "’": "'", "「": "", "」": ""})


def n(s: str | None) -> str:
    """띄어쓰기와 가운뎃점 · 따옴표 표기 차이를 없앤다 (라벨 키트 원문과 파싱 원문의 표기가 다르다)"""
    return re.sub(r"\s+", "", (s or "").translate(_PUNCT))


def units(root: Path) -> tuple[dict, dict]:
    """(규정 이름, 라벨) -> id, 규정 이름 -> [(id, 정규화 글)]"""
    lab, by_reg = {}, {}
    for p in sorted((root / "out/parsed").glob("[0-9]*.json")):
        r = json.loads(p.read_text(encoding="utf-8"))
        rows = by_reg.setdefault(r["name"], [])
        for a in r["articles"]:
            lab[(r["name"], a["label"])] = a["id"]
            rows.append((a["id"], n((a.get("norm") or a.get("text") or "") + "".join(i.get("norm") or i.get("text") or "" for i in a.get("items") or []))))
        for ap in r.get("appendices") or []:
            key = n(f"{ap.get('kind')}{ap.get('number')}").replace("서식", "서식")
            lab[(r["name"], key)] = ap["id"]
            lab[(r["name"], n(ap["id"].split(":", 1)[1]))] = ap["id"]
            rows.append((ap["id"], n(ap.get("text"))))
        for ad in r.get("addenda") or []:
            for a in ad.get("articles") or []:
                rows.append((a["id"], n(a.get("norm") or a.get("text"))))
    return lab, by_reg


def resolve(e: dict, lab: dict, by_reg: dict) -> tuple[str | None, str]:
    q = n(e.get("근거문장"))
    aid = lab.get((e["규정"], e["조"])) or lab.get((e["규정"], n(e["조"])))
    texts = dict(by_reg.get(e["규정"], []))
    if aid and q and q in texts.get(aid, ""):
        return aid, "조 번호"
    hits = [i for i, t in by_reg.get(e["규정"], []) if q and q in t]
    if len(hits) == 1:
        return hits[0], "근거문장 위치"
    if aid and not q:
        return aid, "조 번호"
    return None, "대응 없음"


def main() -> None:
    lab, by_reg = units(ROOT)
    for d in ("val", "bm"):
        for f in ("gold.jsonl", "gold_low.jsonl"):
            p = ROOT / "eval/relabel_v2" / d / f
            rows, cnt = [], {}
            for line in p.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                r = json.loads(line)
                for e in r["영향"]:
                    e["id"], e["id_근거"] = resolve(e, lab, by_reg)
                    cnt[e["id_근거"]] = cnt.get(e["id_근거"], 0) + 1
                    if e["id_근거"] != "조 번호":
                        print(f"  {d}/{f} {r['id']} {e['규정']} {e['조']} -> {e['id']} ({e['id_근거']})")
                rows.append(json.dumps(r, ensure_ascii=False))
            p.write_text("\n".join(rows) + "\n", encoding="utf-8")
            print(d, f, cnt)


if __name__ == "__main__":
    main()
