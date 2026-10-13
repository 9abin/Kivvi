"""규정 도우미 조항 수정 화면(app/web)과 읽기 전용 API를 함께 내보내는 작은 서버.

  uv run --extra db python app/web_server.py            # http://127.0.0.1:8765
  uv run --extra db python app/web_server.py --port 9000

DB: 공유 DB(reg-db)에 팀 읽기 전용 계정(.env.team의 TEAM_*)으로 접속한다. 없으면 .env의 PG_*를 읽기 전용 세션으로 쓴다.
    이 서버는 DB를 바꾸지 않는다. 카드 수락·기각을 decision_log에 쓰는 일은 다음 단계(editor 계정)다.
API (모두 GET, JSON)
  /api/status                      접속 확인, 청크·개정안 수
  /api/find?q=인사규정 제30조 2항   규정명 + 조(·항) → chunk_id
  /api/article?chunk=<chunk_id>    근거 원문: 규정·조 제목·항 전체·강조할 항
  /api/amend?chunk=<chunk_id>      개정 정보: 진행 중인 개정안, 인용하는 곳, 점검 결과, 인용하는 법령, 제목이 같은 법령 조문
  /api/changesets                  개정안 목록
  /api/changeset?id=S01            개정문, 대비표(payload), 영향(impact)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from functools import lru_cache
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "app" / "web"
sys.path.insert(0, str(ROOT / "src"))

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

from regcheck.db import load_env  # noqa: E402

IMPACT_LABEL = {"breaks": ("break", "깨짐"), "resolved": ("resolved", "충돌 해소"), "law_check": ("law", "법령 대조"),
                "review": ("review", "확인 필요"), "undeterminable": ("review", "판단 불가"), "info": ("topic", "참고")}
# 법령마다 흔한 조 제목 — 같은 제목이라도 내용이 관계없어 「제목이 같은 법령 조문」에서 뺀다
GENERIC_TITLES = {t.replace(" ", "") for t in ("목적", "정의", "용어의 정의", "용어의 뜻", "적용범위", "적용 범위", "시행일", "다른 법률과의 관계",
                                               "다른 규정과의 관계", "준용", "위임", "벌칙", "과태료", "권한의 위임", "시행세칙", "기능", "구성", "운영",
                                               "보칙", "업무", "임무", "책임", "경과조치", "기본원칙", "세부사항", "그 밖의 사항")}
STATUS = {"초안": "draft", "검토 요청됨": "review", "수정 요청": "change", "확정": "done"}


def connect():
    team = load_env(ROOT / ".env.team")
    if team.get("TEAM_USER"):
        dsn = dict(host="127.0.0.1", port=team.get("TEAM_PORT", "5432"), dbname=team["TEAM_DATABASE"],
                   user=team["TEAM_USER"], password=team["TEAM_PASSWORD"])
    else:
        e = load_env()
        dsn = dict(host=e.get("PG_HOST", "127.0.0.1"), port=e.get("PG_PORT", "5432"), dbname=e["PG_DATABASE"],
                   user=e["PG_USER"], password=e["PG_PASSWORD"])
    con = psycopg.connect(**dsn, gssencmode="disable", autocommit=True, connect_timeout=5)
    con.execute("SET default_transaction_read_only = on")
    return con


def q(sql: str, *args) -> list[dict]:
    with connect() as con, con.cursor(row_factory=dict_row) as cur:
        return cur.execute(sql, args).fetchall()


@lru_cache(maxsize=1)
def lawdb():
    try:
        from regcheck.lawdb import LawDB
        db = LawDB()
        _ = db.laws  # 여기서 다 읽어 둔다. SQLite 연결은 만든 스레드에서만 쓸 수 있어서, 요청 스레드가 연결을 다시 쓰지 않게
        return db
    except Exception:  # 법률 DB 파일이 없으면 법령 조문 대조만 빠진다
        return None


def reg_titles() -> dict[str, str]:
    return {r["doc_id"]: r["title"] for r in q("select doc_id, title from public.documents")}


def find(text: str) -> dict:
    docs = sorted(reg_titles().items(), key=lambda x: -len(x[1]))
    flat = text.replace(" ", "")
    hit = next(((d, t) for d, t in docs if t.replace(" ", "") in flat), None)
    m = re.search(r"제?\s*(\d+)\s*조(?:\s*의\s*(\d+))?(?:\s*제?\s*(\d+)\s*항)?", text)
    if not hit or not m:
        return {"error": "규정명과 조 번호를 함께 적어 주세요 (예: 인사규정 제30조 2항)"}
    label = f"제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "")
    rows = q("select chunk_id from amend.chunk_detail where reg_id = %s and label = %s and chunk_id is not null "
             "group by chunk_id, hang order by min(hang)", hit[0], label)
    if not rows:
        return {"error": f"{hit[1]} {label}을(를) 찾지 못했습니다"}
    want = f"_{m.group(3)}항" if m.group(3) else None
    chunk = next((r["chunk_id"] for r in rows if want and r["chunk_id"].endswith(want)), rows[0]["chunk_id"])
    return {"chunk": chunk, "reg": hit[1], "label": label}


def article(chunk: str) -> dict:
    head = q("select d.reg_id, d.article_id, d.label, d.title, doc.title as reg, doc.revision "
             "from amend.chunk_detail d join public.documents doc on doc.doc_id = d.reg_id where d.chunk_id = %s limit 1", chunk)
    if not head:
        return {"error": f"청크를 찾지 못했습니다: {chunk}"}
    h = head[0]
    paras = q("select c.chunk_id, c.body, c.status from public.chunks c join ("
              "  select chunk_id, min(item_id) k from amend.chunk_detail where article_id = %s and chunk_id is not null group by chunk_id"
              ") d using (chunk_id) order by d.k", h["article_id"])
    return {"chunk": chunk, "reg_id": h["reg_id"], "article_id": h["article_id"], "reg": h["reg"],
            "revision": h["revision"], "label": f"{h['label']}({h['title']})" if h["title"] else h["label"], "title": h["title"],
            "paras": [{"chunk": p["chunk_id"], "text": p["body"], "status": p["status"]} for p in paras],
            "highlight": next((i for i, p in enumerate(paras) if p["chunk_id"] == chunk), 0)}


def amend(chunk: str) -> dict:
    a = article(chunk)
    if "error" in a:
        return a
    aid, titles = a["article_id"], reg_titles()
    chunks = [p["chunk"] for p in a["paras"]]
    open_ = q("select distinct changeset_id, status, title from amend.v_chunk_open_change where chunk_id = any(%s)", chunks)
    inbound = q("select i.src, i.src_label, i.context, i.kind, d.label, d.hang from amend.v_inbound i "
                "left join amend.chunk_detail d on d.item_id = i.src where i.target = %s or i.target like %s", aid, aid + "-%")
    findings = q("select type, quote, note, suggest from amend.finding where chunk_id = any(%s) order by type", chunks)
    laws = q("select distinct evidence, context from amend.edge where src_article = %s and target_type = 'law'", aid)
    same = []
    db = lawdb()
    if db and a["title"] and a["title"].replace(" ", "") not in GENERIC_TITLES:
        # 이 규정이 실제로 인용하는 법령을 앞에 (흔하지 않은 제목도 다른 법에 우연히 같을 수 있어서)
        cited = {r["d"].replace(" ", "") for r in q("select distinct regexp_replace(target, '\\s*제\\s*\\d+.*$', '') d from amend.edge "
                                                    "where src like %s and target_type = 'law'", a["reg_id"] + ":%")}
        cand = db.same_title(a["title"], limit=20)
        cand.sort(key=lambda x: x[0]["title"].replace(" ", "") not in cited)
        for law, key, u in cand[:3]:
            hang = u.get("항") or []
            hang = hang if isinstance(hang, list) else [hang]
            body = " ".join(h.get("항내용", "") for h in hang if isinstance(h, dict)).strip() or (u.get("조문내용") or "")
            same.append({"law": law["title"], "article": f"제{key}조({u.get('조문제목', '')})", "text": body[:220]})

    def where(r):
        reg_id = r["src"].split(":")[0]
        place = r["label"] or r["src"].split(":")[1].split("-")[0]
        return f"{titles.get(reg_id, reg_id)} {place}" + (f" 제{r['hang']}항" if r.get("hang") else "")
    return {"article": {k: a[k] for k in ("reg", "label", "chunk")},
            "open": [{"id": o["changeset_id"], "status": o["status"], "title": o["title"]} for o in open_],
            "inbound": [{"where": where(r), "quote": (r["context"] or "").strip()[:90], "kind": r["kind"]} for r in inbound],
            "findings": [{"type": f["type"].replace("_", " "), "quote": f["quote"], "note": f["note"] or f["suggest"] or ""} for f in findings],
            "laws": [{"where": l["evidence"], "quote": (l["context"] or "")[:90]} for l in laws],
            "sameTitleLaws": same}


def changesets() -> list[dict]:
    titles = reg_titles()
    return [{"id": r["id"], "reg": titles.get(r["reg_id"], r["reg_id"]), "title": r["title"], "status": STATUS.get(r["status"], "draft"),
             "status_label": r["status"], "origin": r["origin_chunk"]}
            for r in q("select id, reg_id, title, status, origin_chunk from amend.changeset order by id")]


def segments(rows: list, side: str) -> list:
    """payload rows[].old/new: [[[글, 바뀜?], ...] 줄] → 화면 조각 [str | {del}/{ins}] (생략 줄은 뺌)"""
    out = []
    for r in rows:
        for line in r.get(side) or []:
            text = "".join(t for t, _ in line)
            if text.strip() in ("(생 략)", "(현행과 같음)"):
                continue
            if out:
                out.append("\n")
            for t, changed in line:
                out.append({("del" if side == "old" else "ins"): t} if changed else t)
    return out


def changeset(cid: str) -> dict:
    rows = q("select id, reg_id, title, amendment, status, payload from amend.changeset where id = %s", cid)
    if not rows:
        return {"error": f"개정안이 없습니다: {cid}"}
    c, titles = rows[0], reg_titles()
    payload = c["payload"] or {}
    imp = q("select category, reg_id, unit, article, para, quote, reason, handling, priority from amend.impact "
            "where changeset_id = %s order by case priority when '높음' then 0 when '중간' then 1 else 2 end, category", cid)
    impacts = []
    for r in imp:
        kind, label = IMPACT_LABEL.get(r["category"], ("review", r["category"]))
        place = r["article"] + (f" 제{r['para']}항" if r["para"] else "")
        impacts.append({"kind": kind, "label": label, "where": f"{titles.get(r['reg_id'], r['reg_id'])} {place}",
                        "quote": r["quote"] or "", "reason": r["reason"] or "", "priority": r["priority"]})
    return {"id": c["id"], "reg": titles.get(c["reg_id"], c["reg_id"]), "title": c["title"], "status": c["status"],
            "amendment": c["amendment"], "diff": {"old": segments(payload.get("rows", []), "old"), "new": segments(payload.get("rows", []), "new")},
            "impacts": impacts}


ROUTES = {
    "/api/status": lambda p: q("select (select count(*) from public.chunks) as chunks, (select count(*) from amend.changeset) as changesets, current_user as db_user")[0],
    "/api/find": lambda p: find(p.get("q", "")),
    "/api/article": lambda p: article(p.get("chunk", "")),
    "/api/amend": lambda p: amend(p.get("chunk", "")),
    "/api/changesets": lambda p: changesets(),
    "/api/changeset": lambda p: changeset(p.get("id", "")),
}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(WEB), **kw)

    def do_GET(self):  # noqa: N802
        u = urlparse(self.path)
        route = ROUTES.get(u.path)
        if not route:
            return super().do_GET()
        params = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            body, code = route(params), 200
        except Exception as e:  # 화면에 원인을 보여 준다 (비밀번호 등은 담기지 않음)
            body, code = {"error": f"{type(e).__name__}: {str(e)[:200]}"}, 500
        data = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):  # API 호출만 짧게
        if self.path.startswith("/api/"):
            sys.stderr.write(f"{self.address_string()} {self.path[:80]} {args[1] if len(args) > 1 else ''}\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1", help="같은 와이파이 팀원에게 보이려면 0.0.0.0 (방화벽 규칙 필요)")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    lawdb()  # 법률 DB를 시작할 때 한 번 읽음 (첫 요청이 느려지지 않게)
    print(f"규정 도우미 화면: http://{a.host}:{a.port}/  (DB 상태: /api/status)")
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
