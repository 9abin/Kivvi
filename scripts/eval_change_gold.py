"""변경안 골든셋 만들기·채점: 조항을 추가·삭제·수정했을 때 생기는 문제를 잡는가.

  uv run python scripts/eval_change_gold.py make [--seed 1]     # eval/agent/change_gold_s1.jsonl (문항 약 100개)
  uv run python scripts/eval_change_gold.py run  [--seed 1] [--split all|dev|test]
문항 종류와 정답은 src/regcheck/change_gold.py. 검출 = 변경안 점검(change_check.run, 의미 유사 후보 끔) + 개정안 법령 대조 카드의
숫자 비교(app/web_law). 실제 사례 S01~S03(eval/scenarios)은 그 시나리오의 기대 결과로 따로 채점한다.
출력: out/eval/change_gold_s{seed}.json, 화면 표
"""

import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "app"))

from regcheck import runinfo  # noqa: E402
from regcheck.amend import AmendError, build_changeset  # noqa: E402
from regcheck.change_check import run as run_check, score as score_scenario  # noqa: E402
from regcheck.change_gold import MAJOR, hits, make, make_extra  # noqa: E402
from regcheck.lawdb import DEFAULT as LAW_DB, LawDB  # noqa: E402

CAT = {"breaks": "깨짐", "review": "다른 규정 확인 필요", "new_conflicts": "새 충돌", "law_name": "법령명(옛 이름·오기)", "typo": "오타", "law_num": "법령 수치"}
FLAG = "현행 값은 법령 조문에 있으나 개정안 값은 다름"


def jl(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def law_num(cs: dict, regs: dict, edges: list[dict], db) -> list[dict]:
    """개정안 법령 대조 카드의 숫자 비교 (검토 화면 lawcheck와 같은 계산): 바뀐 항이 인용하는 법령 조문과 숫자가 어긋나면"""
    import web_law
    out = []
    reg = regs[cs["reg_id"]]
    for row in cs["rows"]:
        # 대비표 행: 줄마다 [글자, 바뀜 여부] 조각 목록. 첫 줄(조 제목)은 빼고 본문만
        ot = " ".join("".join(seg[0] for seg in line) for line in (row.get("old") or [])[1:])
        nt = " ".join("".join(seg[0] for seg in line) for line in (row.get("new") or [])[1:])
        if not ot or ot == nt or not row.get("origin"):
            continue
        aid = row["origin"]
        for e in edges:
            if e["src"].split("-")[0] != aid or e["status"] != "law_pending" or e["target_type"] != "article":
                continue
            law = db.find(e["target_doc"])
            key, u = db.article(law, e["raw"]) if law else (None, None)
            if not u:
                continue
            focus, units = web_law.focus_of(ot, nt)
            rel = web_law.related(law, ot, k=2, prefer=key, focus=focus, units=units)
            if any(r["note"] == FLAG for r in web_law.compare([x["text"] for x in rel], ot, nt)):
                art = next(a for a in reg["articles"] if a["id"] == aid)
                out.append({"category": "law_num", "reg_id": reg["reg_id"], "article": art["label"], "para": int(e["src"].split("-")[1])})
    return out


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["make", "run"])
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--per", type=int, default=8, help="문제 심기 유형마다 문항 수")
    ap.add_argument("--scale", type=int, default=1, help="문항 수 배율 (새 시드로 크게 만들 때: 설계에 쓰지 않은 시험)")
    ap.add_argument("--split", choices=["all", "dev", "test"], default="all")
    ap.add_argument("--spell", default="", help="오타 검사 로컬 모델 (예: qwen3.5:9b). 비우면 끔")
    ap.add_argument("--neighbors", action="store_true", help="의미 이웃(out/relations/neighbors.json) 경로를 켬")
    ap.add_argument("--vector", action="store_true", help="실제 사례(S01~S03)에 실시간 의미 유사 경로를 켬 = 화면(web_agent.draft)과 같은 설정 (CPU로 임베딩, 느림)")
    a = ap.parse_args()
    regs = {r["reg_id"]: r for r in (json.loads(p.read_text(encoding="utf-8")) for p in (ROOT / "out" / "parsed").glob("[0-9]*.json"))}
    edges = jl(ROOT / "out" / "edges" / "edges_all.jsonl")
    db = LawDB(LAW_DB)
    gold_path = ROOT / "eval" / "agent" / f"change_gold_s{a.seed}.jsonl"
    if a.cmd == "make":
        items = make(regs, edges, db, a.seed, a.per, a.scale)
        items += make_extra(regs, edges, db, a.seed, len(items) + 1, scale=a.scale)   # v0 뒤에 덧붙임 (기존 번호는 그대로)
        gold_path.parent.mkdir(parents=True, exist_ok=True)
        gold_path.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in items), encoding="utf-8")
        print(f"변경안 골든셋 {len(items)}문항 → {gold_path}")
        print("  " + ", ".join(f"{k} {v}" for k, v in collections.Counter(x["kind"] for x in items).items()))
        return

    rels = jl(ROOT / "out" / "relations" / "relations.jsonl")
    items = jl(gold_path)
    if a.split != "all":
        items = [x for x in items if (int(x["id"][1:]) % 2 == 0) == (a.split == "dev")]
    run = runinfo.start("eval_change_gold", [gold_path, ROOT / "out" / "parsed", ROOT / "out" / "relations" / "relations.jsonl", LAW_DB],
                        params=vars(a))
    rec = collections.defaultdict(lambda: [0, 0])          # 기대 분류 → [탐지, 전체]
    kind_rec = collections.defaultdict(lambda: [0, 0])     # 문항 종류 → [탐지, 전체]
    false_major, clean_n, results, failed = 0, 0, [], []
    from regcheck.neighbors import load as load_neighbors
    nb = load_neighbors() if a.neighbors else None
    nb_cards = []                                          # 문항마다 의미 이웃 카드 수 (검토 부담)
    for x in items:
        reg = regs[x["reg_id"]]
        try:
            cs, _ = build_changeset(reg, x["amendment"], x["id"], x["note"])
        except AmendError as e:
            failed.append((x["id"], str(e)[:80]))
            continue
        found = run_check(cs, regs, rels, edges, lawdb=db, spell_model=a.spell or None, neighbors=nb) + law_num(cs, regs, edges, db)
        nb_cards.append(sum(1 for f in found if str(f.get("path", "")).startswith("의미 이웃")))
        for f in found:   # 채점용 분류: 새 문장 속 법령명 → law_name, 오타 의심 → typo
            if f.get("path") == "재검사(법령명)":
                f["category"] = "law_name"
            elif str(f.get("path", "")).startswith("오타 의심"):   # LLM 지적과 글자 연쇄 신호 모두
                f["category"] = "typo"
        row = {"id": x["id"], "kind": x["kind"], "note": x["note"], "found": [(f["category"], f["reg_id"], f.get("article"), f.get("para"))
                                                                          for f in found if f["category"] in (*MAJOR, "law_num")], "exp": []}
        for cat, locs in x["expected"].items():
            for loc in locs:
                ok = hits(found, loc, cat)
                rec[cat][0] += ok
                rec[cat][1] += 1
                kind_rec[x["kind"]][0] += ok
                kind_rec[x["kind"]][1] += 1
                row["exp"].append({**loc, "category": cat, "hit": ok})
        if not x["expected"]:   # 아무것도 걸리면 안 되는 문항: 높은 우선순위(깨짐·새 충돌) 경보 수
            clean_n += 1
            n = sum(1 for f in found if f["category"] in MAJOR)
            false_major += n
            row["false_major"] = n
            kind_rec[x["kind"]][1] += 1
            kind_rec[x["kind"]][0] += n == 0
        for loc in x["not_affected"]:
            if any(f["category"] in MAJOR and f["reg_id"] == loc["reg_id"] and f.get("article") == loc["article"] for f in found):
                row["false_at_loc"] = True
        results.append(row)

    # 실제 사례 (시나리오 S01~S03)
    vec = None
    if a.vector:
        from sentence_transformers import SentenceTransformer
        from regcheck.vectors import Index
        vec = (SentenceTransformer("BAAI/bge-m3", device="cpu"), Index(ROOT / "out" / "vector" / "BAAI__bge-m3"))
    real = []
    for p in sorted((ROOT / "eval" / "scenarios").glob("S*.json")):
        sc = json.loads(p.read_text(encoding="utf-8"))
        ch = sc["change"]
        cs, _ = build_changeset(regs[ch["target"]["reg_id"]], ch["amendment"], sc["id"], sc["title"])
        sres = score_scenario(run_check(cs, regs, rels, edges, lawdb=db, neighbors=nb, vector=vec), sc["expected"])
        tot = [r for cat, rows in sres.items() if cat != "not_affected" for r in rows]
        real.append((sc["id"], sum(r["result"] == "탐지" for r in tot), len(tot),
                     sum(r["result"] == "오탐" for r in sres.get("not_affected", []))))

    out = ROOT / "out" / "eval" / f"change_gold_s{a.seed}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"results": results, "failed": failed, "real": real}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"변경안 골든셋 (시드 {a.seed}, {a.split}) 문항 {len(items)} · 개정문 적용 실패 {len(failed)}")
    print("\n[문제 유형별 재현율]")
    for cat, (h, n) in rec.items():
        print(f"  {CAT[cat]}\t{h}/{n}\t{h / n:.0%}")
    print("\n[문항 종류별] (문제 없는 문항은 높은 우선순위 경보 0이면 통과)")
    for k, (h, n) in sorted(kind_rec.items()):
        print(f"  {k}\t{h}/{n}")
    print(f"\n문제 없어야 하는 문항 {clean_n}개: 높은 우선순위(깨짐·새 충돌) 경보 {false_major}건")
    if nb is not None:
        print(f"의미 이웃 카드: 문항 {len(nb_cards)}개 중 {sum(1 for n in nb_cards if n)}개에서 · 합계 {sum(nb_cards)}장 (문항당 최대 {max(nb_cards, default=0)})")
    print("실제 사례: " + ", ".join(f"{i} {h}/{n} (오탐 {fp})" for i, h, n, fp in real))
    for f in failed:
        print(f"  [적용 실패] {f}")
    misses = [(r["id"], r["kind"], r["note"][:50]) for r in results if any(not e["hit"] for e in r["exp"])]
    print(f"\n놓친 문항 {len(misses)}개 (앞 15개):")
    for m in misses[:15]:
        print("  ", *m)
    runinfo.finish(run, [out], {"문항": len(items), "유형별": {CAT[k]: v for k, v in rec.items()}, "문제 없는 문항 경보": false_major,
                                "적용 실패": len(failed)})


if __name__ == "__main__":
    main()
