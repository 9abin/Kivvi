"""amend 스키마 적재: 우리 산출물(out/) → 가빈 규정 DB 안의 amend 스키마. 표 정의는 sql/amend_schema.sql.

public(가빈 표)은 읽기만 한다. 적재는 amend_builder 역할로 해서, 실수로 public에 쓰려 해도 DB가 막는다.
가빈 청크와의 대응(chunk_id)은 ID 규칙으로 계산한다 — 합의안 4절 (같음 / 단일 항 조 / 가빈 조 단위).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .review import key_baseline, key_impact

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_SQL = ROOT / "sql" / "amend_schema.sql"


def chunk_key(reg_id: str, label: str, hang: int, hangs: list[int], gabin: set[str]) -> tuple[str | None, str | None]:
    """우리 (조, 항) → 가빈 chunk_id와 대응 방식. hangs = 그 조의 항 번호 목록."""
    key = f"{reg_id}_{label}" + (f"_{hang}항" if hang else "")
    art = f"{reg_id}_{label}"
    if key in gabin:
        return key, "같음"
    single = hangs in ([1], [0])
    if single and hang == 1 and art in gabin:          # 항이 ①만 있는 조: 가빈은 조 전체
        return art, "단일 항 조"
    if not hang and f"{art}_1항" in gabin:               # 반대: 우리는 조 전체, 가빈은 「_1항」
        return f"{art}_1항", "단일 항 조"
    if hang and art in gabin:                            # 가빈이 이 조를 항으로 나누지 않음
        return art, "가빈 조 단위"
    return None, None


def unit_chunk(reg_id: str, unit: str, src_id: str, gabin: set[str]) -> str | None:
    """부칙·별표·별지 → 가빈 청크. 가빈은 부칙·별지를 규정별 한 덩어리(23245_부칙, 23245_별지), 별표는 번호별(23245_별표1,
    긴 표는 23245_별표2_01 …)로 나눈다. 별표 조각이 여럿이면 첫 조각. 합의안 4절 별표·별지 ID 규칙이 정해지면 바꾼다."""
    if unit in ("부칙", "별지"):
        k = f"{reg_id}_{unit}"
        return k if k in gabin else None
    if unit == "별표":
        m = re.match(r"[^:]+:별표\s*(\d*)", src_id or "")
        n = m.group(1) if m else ""
        for k in (f"{reg_id}_별표{n}", f"{reg_id}_별표{n}_01", f"{reg_id}_별표_01" if not n else ""):
            if k and k in gabin:
                return k
    return None


def _date(s: str | None) -> str | None:
    """'2026-08-28' 그대로, '2021-03'(월까지만 알려진 개정본)은 그 달 1일."""
    if not s:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    if re.fullmatch(r"\d{4}-\d{2}", s):
        return s + "-01"
    return None


def detail_rows(regs: dict[str, dict], gabin: set[str]) -> list[tuple]:
    rows, seen = [], {}
    for r in regs.values():
        for a in r["articles"]:
            hangs = sorted({it["path"][0] for it in a["items"]}) or [0]
            for it in a["items"]:
                h, ho, mok = it["path"]
                cid, how = chunk_key(r["reg_id"], a["label"], h, hangs, gabin)
                occ = seen[it["id"]] = seen.get(it["id"], -1) + 1   # 원문 항 번호 중복이면 1, 2 …
                rows.append((it["id"], occ, r["reg_id"], a["id"], a["label"], a["title"], h, ho, mok, it["text"], it["norm"],
                             bool(it.get("deleted") or a.get("deleted")), json.dumps(it.get("amend_marks", []), ensure_ascii=False),
                             cid, how))
    return rows


def version_rows(regs: dict[str, dict]) -> list[tuple]:
    """지금은 현행본 한 벌만 (과거 개정본은 반입되면 같은 표에 valid_from을 달리해 쌓는다)."""
    rows, seen = [], {}
    for r in regs.values():
        for a in r["articles"]:
            occ = seen[a["id"]] = seen.get(a["id"], -1) + 1
            rows.append((a["id"], occ, _date(r["revised"]) or "1900-01-01", None, r["reg_id"], a["label"], a["title"],
                         json.dumps([{"path": it["path"], "text": it["text"]} for it in a["items"]], ensure_ascii=False), r["file"]))
    return rows


def edge_rows(rels: list[dict]) -> list[tuple]:
    return [(e["id"], e["src"], e["src_article"], e["src_kind"], str(e["target"]), e["target_type"], e["kind"], e.get("evidence"),
             e.get("context"), e.get("confidence"), e.get("verification"), e.get("check_status"), _date(e.get("src_version")),
             _date(e.get("target_version")), e.get("run")) for e in rels]


def finding_rows(findings: list[dict], item_chunk: dict[str, str | None], gabin: set[str]) -> list[tuple]:
    return [(f["fid"], key_baseline(f), f["reg_id"], f["unit"], f.get("article"), f.get("para"), f.get("src_id"),
             item_chunk.get(f.get("src_id") or "") or unit_chunk(f["reg_id"], f["unit"], f.get("src_id") or "", gabin), f["type"], f.get("method"), f.get("quote"), f.get("context"),
             f.get("suggest"), f.get("note"), f.get("run")) for f in findings]


def impact_rows(cid: str, found: list[dict]) -> list[tuple]:
    seen, out = set(), []
    for it in found:
        k = key_impact(cid, it)
        if k in seen:
            continue
        seen.add(k)
        out.append((cid, k, it["category"], it["reg_id"], it.get("unit"), it.get("article"), it.get("para"), it.get("quote"),
                    it.get("reason"), it.get("handling"), it.get("priority"), it.get("path"), it.get("suggest"),
                    json.dumps(it, ensure_ascii=False)))
    return out


def apply_schema(conn) -> None:
    conn.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
    conn.commit()


def load(conn, regs: dict[str, dict], rels: list[dict], findings: list[dict], changes: list[tuple[dict, list[dict]]],
         decisions: list[dict]) -> dict:
    """amend 표를 다시 채운다. 원문·관계·점검 결과는 통째로 교체, 개정안은 id별 교체, 결정은 없는 줄만 덧붙임."""
    gabin = {r[0] for r in conn.execute("SELECT chunk_id FROM public.chunks").fetchall()}
    conn.execute("SET ROLE amend_builder")   # 이후 쓰기는 amend 표만 허용 (public 쓰기는 DB가 거부)
    details = detail_rows(regs, gabin)
    item_chunk = {d[0]: d[13] for d in details if d[1] == 0}
    with conn.cursor() as cur:
        cur.execute("TRUNCATE amend.chunk_detail, amend.article_version, amend.edge, amend.finding")
        cur.executemany("INSERT INTO amend.chunk_detail VALUES (" + ",".join(["%s"] * 15) + ")", details)
        cur.executemany("INSERT INTO amend.article_version VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", version_rows(regs))
        cur.executemany("INSERT INTO amend.edge VALUES (" + ",".join(["%s"] * 15) + ")", edge_rows(rels))
        cur.executemany("INSERT INTO amend.finding VALUES (" + ",".join(["%s"] * 15) + ")", finding_rows(findings, item_chunk, gabin))
        n_imp = 0
        for cs, found in changes:
            cur.execute("""INSERT INTO amend.changeset (id, reg_id, title, amendment, payload) VALUES (%s,%s,%s,%s,%s)
                           ON CONFLICT (id) DO UPDATE SET reg_id = EXCLUDED.reg_id, title = EXCLUDED.title,
                           amendment = EXCLUDED.amendment, payload = EXCLUDED.payload""",
                        (cs["id"], cs["reg_id"], cs.get("title"), cs["amendment"], json.dumps(cs, ensure_ascii=False)))
            cur.execute("DELETE FROM amend.impact WHERE changeset_id = %s", (cs["id"],))
            rows = impact_rows(cs["id"], found)
            cur.executemany("INSERT INTO amend.impact VALUES (" + ",".join(["%s"] * 14) + ")", rows)
            n_imp += len(rows)
        have = {(k, str(a), s) for k, a, s in cur.execute("SELECT key, to_char(at, 'YYYY-MM-DD\"T\"HH24:MI:SS'), state FROM amend.decision_log")}
        new = [d for d in decisions if (d["key"], d["at"], d["state"]) not in have]
        cur.executemany("INSERT INTO amend.decision_log (key, kind, prev, state, reviewer, at, comment, ref) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                        [(d["key"], d["kind"], d.get("prev"), d["state"], d["reviewer"], d["at"], d.get("comment"),
                          json.dumps(d.get("ref") or {}, ensure_ascii=False)) for d in new])
    conn.commit()
    conn.execute("RESET ROLE")
    matched = sum(1 for d in details if d[13])
    return {"항·호·목": len(details), "가빈 청크 대응": matched, "조문 사본": len(version_rows(regs)), "관계": len(rels),
            "점검 결과": len(findings), "개정안": len(changes), "영향": n_imp, "결정 추가": len(new)}
