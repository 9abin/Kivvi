"""개정안 작성(에이전트 C)·검토 결정·제출·패키지 파일 — 화면 서버(app/web_server.py)가 부른다.

쓰기는 .env 계정으로 접속한 뒤 `SET ROLE editor`로 권한을 낮춰서 한다 (amend.changeset·impact 쓰기, decision_log 덧붙이기만).
개정안은 참고 문안이다. 규정 원문(public)과 우리 파싱 결과(out/parsed)는 바꾸지 않는다 (AGENTS §3).
  draft(chunk, request)        자연어 요청 → LLM(DRAFT_MODEL, 기본 gpt-5.6-luna API · 폐쇄망은 qwen2.5:7b) 개정문 초안 → 적용기 검증 → 연쇄 수정 → 영향 → DB 초안 저장
  decide(cid, key, state, ..)  영향 카드 수락·기각 → decision_log
  submit(cid, ..)              개정안 상태 「검토」(화면: 검토 요청됨) + decision_log. 상태 값은 sql/amend_schema.sql CHECK (초안·검토·승인·공포·기각)
  package_html(cid)            개정 패키지 HTML (내려받기)
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from regcheck.db import load_env  # noqa: E402

# 261019: 오타 검사 · 주제 추론 · 카드 설명도 API(luna)로 — 시연 중 로컬 9B가 GPU 메모리를 차지하지 않게. 폐쇄망: SPELL_MODEL · HYPO_MODEL=qwen3.5:9b
SPELL_MODEL = os.environ.get("SPELL_MODEL", "gpt-5.6-luna") or None   # 오타 검사 (바뀐 문장만). 큰 모델일수록 낫다(리포트 38), 끄려면 SPELL_MODEL=
HYPOTHESIS = os.environ.get("HYPOTHESIS", "1") != "0"   # 가설 카드 (리포트 49 후속)
HYPO_MODEL = os.environ.get("HYPO_MODEL", "gpt-5.6-luna")    # 주제 추론 · 카드 설명. 로컬이면 9B가 7B보다 낫다 (7B는 주제 2개, 9B는 3개)
DRAFT_MODEL = os.environ.get("DRAFT_MODEL", "gpt-5.6-luna")   # 261018 API로 바꿈: 자연어 개정 120건 의도 맞음 7B 93 · 30B 108 · luna 116 (리포트 57). 폐쇄망: DRAFT_MODEL=qwen2.5:7b
STATES = {"accept": "수락", "reject": "기각"}
_lock = threading.Lock()


def write_conn():
    e = load_env()
    con = psycopg.connect(host="127.0.0.1", port=e.get("PG_PORT", "5432"), dbname=e["PG_DATABASE"], user=e["PG_USER"],
                          password=e["PG_PASSWORD"], gssencmode="disable", connect_timeout=5)
    con.execute("SET ROLE editor")
    return con


# 역할별로 할 수 있는 일 (내규관리규정: 입안·협의는 업무담당부서, 확정은 대표이사 결재·이사회 의결(제22조), 등록·공지는 내규담당부서(제23조)).
# 시연용이라 로그인은 없고 화면이 역할을 보낸다. 실제 배포는 회사 계정(SSO)의 권한으로 바꾼다 — 서버가 역할을 확인하는 이 자리가 그 연결점이다.
ROLE_LABEL = {"employee": "일반 직원", "editor": "개정 담당자", "approver": "승인권자"}
ACTIONS = {"draft": ("editor",), "decide": ("editor",), "submit": ("editor",), "lawname_create": ("editor",), "scan": ("editor",),
           "approve": ("approver",), "reject": ("approver",), "publish": ("editor",)}
SELF_BLOCK = ("approve", "reject")   # 기안한 사람이 자기 개정안을 승인·반려할 수 없다


def guard(role: str, action: str, user: str, cid: str = "") -> dict | None:
    """허용되지 않으면 {"error": ...}, 되면 None"""
    allowed = ACTIONS[action]
    if not role:
        return {"error": "역할이 지정되지 않았습니다 (요청에 role: employee·editor·approver 필요)"}
    if role not in allowed:
        return {"error": f"{ROLE_LABEL.get(role, role)} 역할에는 허용되지 않는 작업입니다 ({'·'.join(ROLE_LABEL[r] for r in allowed)} 전용)"}
    if action in SELF_BLOCK and cid:
        with write_conn() as con:
            row = con.execute("SELECT created_by FROM amend.changeset WHERE id = %s", (cid,)).fetchone()
        if row and row[0] and row[0] == user:
            return {"error": f"기안한 사람({user})은 자기 개정안을 승인·반려할 수 없습니다. 승인권자가 따로 처리해야 합니다"}
    return None


@lru_cache(maxsize=1)
def corpus():
    """우리 파싱·관계·인용 (에이전트 입력). 처음 한 번 읽는다."""
    regs = {r["reg_id"]: r for r in (json.loads(p.read_text(encoding="utf-8")) for p in (ROOT / "out" / "parsed").glob("[0-9]*.json"))}
    rels = [json.loads(l) for l in (ROOT / "out" / "relations" / "relations.jsonl").read_text(encoding="utf-8").splitlines()]
    edges = [json.loads(l) for l in (ROOT / "out" / "edges" / "edges_all.jsonl").read_text(encoding="utf-8").splitlines()]
    return regs, rels, edges


_own_models: dict = {}


def _models():
    """임베딩·reranker: 채팅(가빈 검색기)이 이미 올린 같은 모델이 있으면 같이 쓴다 (8GB GPU에 두 벌 올리지 않게).
    우리 색인(out/vector/BAAI__bge-m3: 의미 유사 · 중복 · 가설)은 bge-m3 벡터라 bge-m3가 필요하다. 채팅이 Ollama 임베딩
    (261019 기본 Qwen3-Embedding-4B)을 쓰면 searcher.model이 없으므로 bge-m3만 따로 올리고 reranker는 같이 쓴다."""
    shared_rr = None
    try:
        import web_chat
        a = web_chat._state.get("assistant")
        if a is not None:
            if a.searcher.model is not None:
                return a.searcher.model, a.searcher.reranker
            shared_rr = a.searcher.reranker
    except Exception:
        pass
    if "m" not in _own_models:
        from sentence_transformers import SentenceTransformer
        m = SentenceTransformer("BAAI/bge-m3", device="cuda")
        m.half()
        _own_models["m"] = m
    if shared_rr is not None:
        return _own_models["m"], shared_rr
    if "r" not in _own_models:
        from sentence_transformers import CrossEncoder
        _own_models["r"] = CrossEncoder("BAAI/bge-reranker-v2-m3", device="cuda", model_kwargs={"dtype": "float16"})
    return _own_models["m"], _own_models["r"]


@lru_cache(maxsize=1)
def _vector_index():
    import numpy as np
    d = ROOT / "out" / "vector" / "BAAI__bge-m3"
    return np.load(d / "embeddings.npy"), [json.loads(l) for l in (d / "chunks.jsonl").read_text(encoding="utf-8").splitlines()]


TARGET_INDEX = os.environ.get("TARGET_INDEX", "qwen3-embedding-4b-q8_0")   # 개정 위치 찾기 전용 색인 (리포트 55). bge로 되돌리려면 BAAI__bge-m3
TARGET_K = 5   # 화면에 보일 후보 수. 3 → 5: 조 @3 69% → @5 77% (리포트 55). 어차피 사람이 고른다


@lru_cache(maxsize=1)
def _target_index():
    """개정 위치 찾기 색인 (vecs, chunks), meta. Ollama 임베딩 색인이 없으면 None → bge 색인을 쓴다."""
    import numpy as np
    d = ROOT / "out" / "vector" / TARGET_INDEX
    if TARGET_INDEX == "BAAI__bge-m3" or not (d / "embeddings.npy").exists():
        return None
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    return (np.load(d / "embeddings.npy"), [json.loads(l) for l in (d / "chunks.jsonl").read_text(encoding="utf-8").splitlines()]), meta


def _ollama_embed(meta: dict, texts: list[str]):
    import urllib.request

    import numpy as np
    body = json.dumps({"model": meta["model"], "input": [meta.get("query_instruction", "") + t for t in texts], "truncate": True}).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:11434/api/embed", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        v = np.asarray(json.loads(r.read())["embeddings"], dtype=np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


@lru_cache(maxsize=1)
def _vector_obj():
    from regcheck.vectors import Index
    return Index(ROOT / "out" / "vector" / "BAAI__bge-m3")


def targets(text: str) -> dict:
    """규정·조를 모르는 개정 요청 → 고칠 후보 조문 3개 (regcheck.target). 점수가 낮으면 로컬 LLM으로 바꿔 써 한 번 더 찾는다."""
    from regcheck.nl import _post
    from regcheck.target import find_targets
    regs, _, _ = corpus()
    model, rr = _models()

    def rewrite(q: str) -> str:
        msg = [{"role": "system", "content": "회사 내부 규정(내규)의 조문을 찾는 검색어를 만든다. 요청을 내규 조문에 나올 법한 낱말로 바꿔 한 줄로만 쓴다. 설명 없이 검색어만."},
               {"role": "user", "content": q}]
        return _post(DRAFT_MODEL, msg, timeout=60).strip().splitlines()[0][:80]

    rerank = lambda q, ts: rr.predict([(q, t) for t in ts], batch_size=16, show_progress_bar=False).tolist()  # noqa: E731
    rw = rewrite if os.environ.get("TARGET_REWRITE") == "1" else None   # 기본 끔: 독립 평가에서 효과 없음(조 @3 62%→60%, 리포트 51). 켜려면 TARGET_REWRITE=1
    with _lock:   # GPU를 채팅·개정안 작성과 나눠 쓴다
        ti = _target_index()
        if ti:   # Qwen3-Embedding-4B 색인 (Ollama): 조 @3 62% → 69% (리포트 55). Ollama가 꺼져 있으면 bge로
            try:
                out = find_targets(text, regs, ti[0], lambda t: _ollama_embed(ti[1], t), rerank, rewrite=rw, k=TARGET_K)
                out["index"] = TARGET_INDEX
                return out
            except Exception:
                pass
        out = find_targets(text, regs, _vector_index(), lambda t: model.encode(t, normalize_embeddings=True), rerank, rewrite=rw, k=TARGET_K)
        out["index"] = "BAAI__bge-m3"
        return out


def package_path(cid: str) -> Path:
    return ROOT / "out" / "changes" / cid / "package.json"


def load_package(cid: str) -> dict | None:
    p = package_path(cid)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def draft(chunk: str, request: str, user: str) -> dict:
    request = (request or "").strip()
    if len(request) < 4:
        return {"error": "어떻게 바꿀지 한 문장으로 적어 주세요 (예: 분할 횟수를 3회로 늘린다)"}
    reg_id = (chunk or "").split("_")[0]
    regs, rels, edges = corpus()
    reg = regs.get(reg_id)
    if not reg:
        return {"error": f"규정을 찾지 못했습니다: {reg_id}"}
    label = (chunk.split("_")[1] if "_" in chunk else "")
    full_request = f"{reg['name']} {label} {request}" if label and label not in request else f"{reg['name']} {request}"
    from regcheck.nl import draft as nl_draft
    from regcheck.agent import build_package
    from regcheck.lawdb import DEFAULT as LAW_DB, LawDB
    with _lock:   # 로컬 LLM·GPU를 채팅과 나눠 쓰므로 한 번에 하나
        try:
            res = nl_draft(regs, full_request, model=DRAFT_MODEL, reg=reg)
        except Exception as e:   # 로컬 모델(Ollama)이 꺼졌거나 모델 이름이 없을 때, API 키·네트워크 오류 (urllib의 URLError·HTTPError는 OSError)
            where = "API 키(.env OPENAI_API_KEY · UPSTAGE_API_KEY)와 네트워크" if DRAFT_MODEL.startswith(("gpt-", "solar-")) else "Ollama가 켜져 있는지, 모델이 설치돼 있는지"
            return {"error": f"개정안 작성 모델 「{DRAFT_MODEL}」에 연결하지 못했습니다. {where}를 확인해 주세요 ({type(e).__name__})"}
        if not res.get("ok"):
            if not res.get("tries") and res.get("error"):   # LLM 전에 걸러진 경우는 그 안내를 그대로
                return {"error": res["error"], "tries": 0}
            last = (res.get("tries") or [{}])[-1].get("error", "")
            return {"error": "개정문을 만들지 못했습니다. 바꿀 조·항과 값을 더 구체적으로 적어 주세요." + (f" ({last[:80]})" if last else ""),
                    "tries": len(res.get("tries", []))}
        cid = "W" + datetime.now().strftime("%y%m%d-%H%M%S")
        cs = res["changeset"]
        cs["id"], cs["title"] = cid, request
        from regcheck.neighbors import load as load_neighbors   # 의미 이웃 (out/relations/neighbors.json, 없으면 끔)
        model, rr = _models()
        embed = lambda t: model.encode(t, normalize_embeddings=True)  # noqa: E731
        hypo = None
        if HYPOTHESIS:   # 모델이 추론한 「함께 확인할 주제」 가설 카드 (보조·낮음, 끄려면 HYPOTHESIS=0)
            from regcheck.hypothesis import hypotheses
            from regcheck.nl import _post as _llm
            rerank = lambda q, ts: rr.predict([(q, t) for t in ts], batch_size=16, show_progress_bar=False).tolist()  # noqa: E731
            hypo = lambda r, art, new, diff, skip: hypotheses(lambda m: _llm(HYPO_MODEL, m, timeout=90), regs, r, art, new, diff,  # noqa: E731
                                                              (_vector_obj().vecs, _vector_obj().chunks), embed, rerank, skip)
        pkg = build_package(cs, regs, rels, edges, lawdb=LawDB(LAW_DB) if LAW_DB.exists() else None, spell_model=SPELL_MODEL,
                            neighbors=load_neighbors(), dup=(embed, _vector_obj()), hypo=hypo,
                            vector=(model, _vector_obj()))   # 의미 유사(실시간): 이웃표(0.78)가 놓치는 용어 변형(당연면직↔당연해직, S02)을 0.7대에서 잡음
    n = _store(cid, reg_id, f"{label} — {request}" if label else request, cs, pkg, chunk, user, request, {"chunk": chunk, "model": DRAFT_MODEL})
    return {"id": cid, "breaks": [pkg["breaks_before"], pkg["breaks_after"]], "companion": len(pkg["companion"]), "impacts": n}


# ── 여러 조문 함께 개정 (261019) ─────────────────────────────────────────
# 요청 하나에 고칠 조문이 여럿 나오면(「육아휴직 분할 3회」 → 인사규정 제30조 · 취업규칙 제43조 …) AI가 서로 어긋나지 않는 개정 방안을
# 2~3개 내고(plan), 담당자가 고르고 고친 조문별 지시를 묶어 개정안을 만든다(draft_bundle). 같은 규정의 조문들은 개정문 하나로 합치고,
# 규정마다 개정안 하나 — 묶음 ID(payload.bundle)로 이어 대표 개정안에서 한 번에 제출 · 승인 · 반려한다.
# AI 방안은 참고다: 법적 판단을 하지 않고, 법령은 반입 스냅샷의 조문을 보여 주며 대조할 점만 적는다 (AGENTS §3).
PLAN_PROMPT = """너는 회사 내부 규정(내규) 개정을 돕는 보조자다. 개정 요청과 관련 내규 조문, 관련 법령 조문을 보고 개정 방안을 2~3개 제안한다.
- 방안마다 어느 조문을 어떻게 고칠지 조문별로 한 문장씩 적는다. 같은 값(횟수 · 기간 · 금액 등)이 여러 조문에 있으면 서로 어긋나지 않게 함께 고친다.
- 고치지 않는 조문은 방안에 넣지 않는다. 조문 번호는 주어진 ref(A1, A2 …)만 쓴다.
- 법령 조문과 대조해 개정안 값이 법령 기준보다 낮거나 다르면 그 점을 law에 사실로만 적는다. 위법 · 적법 판단은 하지 않는다.
- 방안끼리는 범위가 달라야 한다 (예: 최소 수정 / 관련 조문까지 맞춤 / 법령 기준에 맞춤).
JSON만 출력: {"options":[{"title":"짧은 이름","summary":"한두 문장","edits":[{"ref":"A1","how":"이 조문을 어떻게 고칠지 한 문장"}],"law":"법령 대조 메모","caution":"담당자가 확인할 점"}]}"""


def _law_context(db, art: dict, request: str) -> list[dict]:
    """조문과 제목이 같은 법령에서 요청 · 조문과 가장 가까운 항 2개 (반입 스냅샷, web_law.related). 조문 제목만 맞추면 본문이 비거나
    (남녀고용평등법 제19조는 항이 따로) 다른 조(분할 사용은 제19조의4)가 실제 기준이다"""
    if db is None or not art.get("title"):
        return []
    from web_law import related
    from regcheck.nl import article_text
    out, seen = [], set()
    for law, key, _ in db.same_title(art["title"], limit=2):
        if law["title"] in seen:
            continue
        seen.add(law["title"])
        for x in related(law, article_text(art), k=2, prefer=key, focus=request):
            out.append({"law": law["title"], "label": x["article"], "title": x["title"], "text": x["text"][:500]})
    return out[:3]


def plan(request: str, chunks: list[str]) -> dict:
    """고칠 후보 조문들 → AI 개정 방안. {articles:[{ref, chunk, reg, label, title, text, laws}], options:[...]}"""
    from regcheck.nl import _post, article_text
    from regcheck.lawdb import DEFAULT as LAW_DB, LawDB
    request = (request or "").strip()
    if len(request) < 4:
        return {"error": "어떻게 바꿀지 한 문장으로 적어 주세요"}
    regs, _, _ = corpus()
    db = LawDB(LAW_DB) if LAW_DB.exists() else None
    arts, seen = [], set()
    for ch in chunks[:6]:
        rid, _, rest = (ch or "").partition("_")
        label = rest.split("_")[0]
        a = next((a for a in regs.get(rid, {}).get("articles", []) if a["label"] == label and not a.get("deleted")), None)
        if not a or (rid, label) in seen:
            continue
        seen.add((rid, label))
        arts.append({"ref": f"A{len(arts) + 1}", "chunk": ch, "reg_id": rid, "reg": regs[rid]["name"], "label": label,
                     "title": a["title"], "text": article_text(a)[:1200], "laws": _law_context(db, a, request)})
    if not arts:
        return {"error": "고칠 조문 후보가 없습니다"}
    law_seen, law_lines = set(), []
    for a in arts:
        for l in a["laws"]:
            if (l["law"], l["label"]) not in law_seen:
                law_seen.add((l["law"], l["label"]))
                law_lines.append(f"「{l['law']}」 {l['label']}({l['title']})\n{l['text']}")
    user_msg = (f"개정 요청: {request}\n\n관련 내규 조문:\n" + "\n\n".join(f"[{a['ref']}] 「{a['reg']}」 {a['text']}" for a in arts)
                + "\n\n관련 법령 조문 (반입 스냅샷):\n" + ("\n\n".join(law_lines) or "(없음)"))
    try:
        with _lock:
            raw = _post(DRAFT_MODEL, [{"role": "system", "content": PLAN_PROMPT}, {"role": "user", "content": user_msg}], timeout=180)
    except Exception as e:
        return {"error": f"개정 방안 모델 「{DRAFT_MODEL}」에 연결하지 못했습니다 ({type(e).__name__})", "articles": arts, "options": []}
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        opts = json.loads(m.group(0))["options"] if m else []
    except (json.JSONDecodeError, KeyError, TypeError):
        opts = []
    refs = {a["ref"] for a in arts}
    clean = []
    for o in opts[:3]:   # 주어진 조문(ref)만, 지시가 빈 것은 버린다
        edits = [{"ref": e["ref"], "how": str(e.get("how", "")).strip()} for e in o.get("edits", []) if isinstance(e, dict) and e.get("ref") in refs and str(e.get("how", "")).strip()]
        if edits:
            clean.append({"title": str(o.get("title", ""))[:40], "summary": str(o.get("summary", ""))[:300], "edits": edits,
                          "law": str(o.get("law", ""))[:400], "caution": str(o.get("caution", ""))[:300]})
    if not clean:
        return {"error": "AI가 개정 방안을 만들지 못했습니다. 조문을 하나 골라 개정하거나 요청을 더 구체적으로 적어 주세요.", "articles": arts, "options": []}
    return {"request": request, "articles": arts, "options": clean, "model": DRAFT_MODEL}


def draft_bundle(request: str, edits: list[dict], user: str) -> dict:
    """[{chunk, how}] → 규정별 개정안 (같은 규정의 조문들은 개정문 하나로). 첫 규정이 대표, 나머지는 묶음 구성원"""
    from collections import OrderedDict
    from regcheck.amend import AmendError, build_changeset
    from regcheck.nl import draft as nl_draft
    request = (request or "").strip()
    edits = [e for e in (edits or []) if isinstance(e, dict) and e.get("chunk") and len(str(e.get("how", "")).strip()) >= 4][:8]
    if not edits:
        return {"error": "고칠 조문과 고칠 내용을 하나 이상 적어 주세요"}
    regs, _, _ = corpus()
    by_reg: OrderedDict[str, list[dict]] = OrderedDict()
    for e in edits:
        by_reg.setdefault(e["chunk"].split("_")[0], []).append(e)
    if any(r not in regs for r in by_reg):
        return {"error": "규정을 찾지 못한 조문이 있습니다"}
    drafts = []   # (reg_id, 합친 개정문, 첫 조문 chunk, 조문 라벨들)
    for rid, es in by_reg.items():
        reg, sents = regs[rid], []
        for e in es:
            label = e["chunk"].split("_")[1] if "_" in e["chunk"] else ""
            with _lock:
                try:
                    res = nl_draft(regs, f"{reg['name']} {label} {e['how']}", model=DRAFT_MODEL, reg=reg)
                except Exception as ex:
                    return {"error": f"개정안 작성 모델에 연결하지 못했습니다 ({type(ex).__name__})"}
            if not res.get("ok"):
                return {"error": f"「{reg['name']}」 {label} 개정문을 만들지 못했습니다. 이 조문의 고칠 내용을 더 구체적으로 적어 주세요."}
            sents += [s for s in res["amendment"].strip().splitlines() if s.strip() and s not in sents]
        text = "\n".join(sents)
        try:   # 같은 규정의 문장들을 합쳐도 원문에 함께 적용되는지 (같은 곳을 두 번 고치면 여기서 걸린다)
            build_changeset(reg, text, "check")
        except AmendError as ex:
            return {"error": f"「{reg['name']}」의 조문별 개정문을 하나로 합치지 못했습니다: {ex}"}
        drafts.append((rid, text, es[0]["chunk"], [e["chunk"].split("_")[1] for e in es]))
    bid = "B" + datetime.now().strftime("%y%m%d-%H%M%S")
    made = []
    for i, (rid, text, chunk, labels) in enumerate(drafts):
        cid = f"W{bid[1:]}-{i + 1}"
        ref = {"chunk": chunk, "model": DRAFT_MODEL, "kind": "bundle", "bundle": bid, "bundle_main": f"W{bid[1:]}-1", "bundle_request": request}
        r = _package_store(cid, regs[rid], text, request, chunk, user, ref, title=f"{' · '.join(dict.fromkeys(labels))} — {request}")
        if r.get("error"):
            return {**r, "made": made}
        made.append({"id": cid, "reg": regs[rid]["name"], **r})
    return {"id": made[0]["id"], "bundle": bid, "members": made}


def _package_store(cid: str, reg: dict, amendment: str, request: str, chunk: str, user: str, ref: dict, title: str) -> dict:
    """개정문(검증된 것) → 연쇄 수정 · 영향 점검 패키지 → DB 초안. draft()와 같은 점검 설정"""
    from regcheck.agent import build_package
    from regcheck.amend import build_changeset
    from regcheck.lawdb import DEFAULT as LAW_DB, LawDB
    from regcheck.neighbors import load as load_neighbors
    regs, rels, edges = corpus()
    with _lock:
        cs, _ = build_changeset(reg, amendment, cid, request)
        model, rr = _models()
        embed = lambda t: model.encode(t, normalize_embeddings=True)  # noqa: E731
        hypo = None
        if HYPOTHESIS:
            from regcheck.hypothesis import hypotheses
            from regcheck.nl import _post as _llm
            rerank = lambda q, ts: rr.predict([(q, t) for t in ts], batch_size=16, show_progress_bar=False).tolist()  # noqa: E731
            hypo = lambda r, art, new, diff, skip: hypotheses(lambda m: _llm(HYPO_MODEL, m, timeout=90), regs, r, art, new, diff,  # noqa: E731
                                                              (_vector_obj().vecs, _vector_obj().chunks), embed, rerank, skip)
        pkg = build_package(cs, regs, rels, edges, lawdb=LawDB(LAW_DB) if LAW_DB.exists() else None, spell_model=SPELL_MODEL,
                            neighbors=load_neighbors(), dup=(embed, _vector_obj()), hypo=hypo, vector=(model, _vector_obj()))
    n = _store(cid, reg["reg_id"], title, cs, pkg, chunk, user, request, ref)
    return {"breaks": [pkg["breaks_before"], pkg["breaks_after"]], "companion": len(pkg["companion"]), "impacts": n}


def bundle_ids(cid: str) -> tuple[str | None, str | None, list[str]]:
    """(묶음 ID, 대표 ID, 구성원 ID들 — 대표 먼저). 묶음이 아니면 (None, None, [])"""
    with write_conn() as con:
        row = con.execute("SELECT payload->>'bundle', payload->>'bundle_main' FROM amend.changeset WHERE id = %s", (cid,)).fetchone()
        if not row or not row[0]:
            return None, None, []
        ids = [r[0] for r in con.execute("SELECT id FROM amend.changeset WHERE payload->>'bundle' = %s ORDER BY id", (row[0],)).fetchall()]
    return row[0], row[1], ids


def _bundled(fn):
    """제출 · 승인 · 반려 · 공포: 묶음이면 대표 개정안에서 구성원 모두에 (구성원 화면에서는 막는다)"""
    def run(cid: str, user: str, comment: str = "") -> dict:
        bid, main, ids = bundle_ids(cid)
        if not bid:
            return fn(cid, user, comment)
        if cid != main:
            return {"error": f"묶음 개정안입니다. 대표 개정안 {main}에서 한 번에 처리하세요"}
        if fn is approve:   # 승인은 모두 승인할 수 있을 때만 (하나라도 미결정이면 아무것도 바꾸지 않는다)
            for m in ids:
                fin = final_package(m)
                if fin.get("counts", {}).get("미결정"):
                    return {"error": f"묶음의 {fin['reg_name']} 개정안({m})에 결정하지 않은 카드가 {fin['counts']['미결정']}건 있습니다"}
        out = None
        for m in ids:
            r = fn(m, user, comment)
            if r.get("error"):
                return {**r, "error": f"{m}: {r['error']}"}
            out = out or r
        return {**out, "bundle": bid, "members": ids}
    return run


def _store(cid: str, reg_id: str, title: str, cs: dict, pkg: dict, chunk: str | None, user: str, request: str, ref: dict) -> int:
    """개정 패키지 파일 + amend.changeset·impact + 「초안」 결정 기록. 영향 수를 돌려준다"""
    from regcheck.amend_db import impact_rows
    from agent_fix import render
    pkg["run"] = cid
    out = package_path(cid).parent
    out.mkdir(parents=True, exist_ok=True)
    package_path(cid).write_text(json.dumps(pkg, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "package.html").write_text(render(pkg), encoding="utf-8")
    found = pkg["law_check"] + pkg["review"] + [{**b, "category": "breaks"} for b in pkg.get("remaining_breaks", []) if isinstance(b, dict)]
    changes = cs.get("changes") or [{"origin": r["origin"]} for r in pkg["rows"] if r.get("origin")]   # v_chunk_open_change가 바뀌는 조를 여기서 찾음
    payload = {"id": cid, "run": cid, "rows": pkg["rows"], "changes": changes, "request": request, "model": ref.get("model"),
               "kind": ref.get("kind", "draft"), **({"origin": ref["origin"]} if ref.get("origin") else {}),
               **({k: ref[k] for k in ("bundle", "bundle_main", "bundle_request")} if ref.get("bundle") else {})}
    with write_conn() as con, con.cursor() as cur:
        cur.execute("INSERT INTO amend.changeset (id, reg_id, title, amendment, status, origin_chunk, created_by, payload) "
                    "VALUES (%s, %s, %s, %s, '초안', %s, %s, %s)",
                    (cid, reg_id, title, pkg["amendment_final"], chunk, user, json.dumps(payload, ensure_ascii=False)))
        rows = impact_rows(cid, found)
        if rows:
            cur.executemany("INSERT INTO amend.impact VALUES (" + ",".join(["%s"] * 14) + ")", rows)
        cur.execute("INSERT INTO amend.decision_log (key, kind, prev, state, reviewer, comment, ref) VALUES (%s, 'changeset', NULL, '초안', %s, %s, %s)",
                    (f"C:{cid}", user, request, json.dumps(ref, ensure_ascii=False)))
    return len(rows)


def _lawname_rows() -> list[dict]:
    from regcheck.lawname_fix import mapping
    with write_conn() as con, con.cursor(row_factory=dict_row) as cur:
        fs = cur.execute("select f.type, f.quote, f.suggest, f.reg_id, f.article, d.title as reg_name from amend.finding f "
                         "left join public.documents d on d.doc_id = f.reg_id where f.type in ('법령명_변경', '법령명_오기')").fetchall()
        # 변경 감시가 새 법령 판에서 찾은 것도 (기준선에 없던 법령명 변경 → 감시에서 바로 초안). 바꿀 이름은 메모에서 읽음
        new = cur.execute("select i.type, i.quote, i.note, i.reg_id, i.article, i.reg_name from amend.scan_item i "
                          "where i.scan_id = (select id from amend.scan order by started_at desc limit 1) and i.change = 'new' "
                          "and i.type in ('법령명_변경', '법령명_오기')").fetchall()
    have = {(f["reg_id"], f["article"], f["quote"]) for f in fs}
    for x in new:
        m = re.search(r"지금 「([^」]+)」|정식 이름은 「([^」]+)」|「([^」]+)」가 거의 같음", x.get("note") or "")
        if m and (x["reg_id"], x["article"], x["quote"]) not in have:
            fs.append({**x, "suggest": next(g for g in m.groups() if g)})
    return mapping(fs)


def lawname_plan() -> dict:
    """법령명 일괄 정비 초안 (고도화 7): 점검 결과의 옛 이름·오기 → 규정별 개정문. 아직 저장하지 않는다"""
    from regcheck.lawname_fix import plan
    regs, _, _ = corpus()
    p = plan(regs, _lawname_rows())
    open_regs = {r[0] for r in _open_lawname()}
    for pk in p["packages"]:
        pk["open"] = pk["reg_id"] in open_regs
        pk.pop("rows", None)
    keep = ("reg_name", "unit", "article", "old", "new", "token", "context")
    return {"mapping": p["mapping"], "packages": p["packages"], "not_found": p["not_found"],
            "check": [{k: h.get(k) for k in keep} for h in p["check"]], "manual": [{k: h.get(k) for k in keep} for h in p["manual"]]}


def _open_lawname() -> list[tuple]:
    with write_conn() as con:
        return con.execute("select reg_id, id from amend.changeset where status in ('초안', '검토', '승인') and payload->>'kind' = 'lawname'").fetchall()


def lawname_create(reg_ids: list[str], user: str) -> dict:
    """고른 규정마다 개정안 하나(초안). 이미 진행 중인 법령명 정비 개정안이 있는 규정은 건너뛴다"""
    from regcheck.agent import build_package
    from regcheck.amend import build_changeset
    from regcheck.lawdb import DEFAULT as LAW_DB, LawDB
    from regcheck.lawname_fix import plan
    regs, rels, edges = corpus()
    rows = _lawname_rows()
    p = plan(regs, rows)
    busy = {r[0] for r in _open_lawname()}
    made, skipped = [], []
    with _lock:
        db = LawDB(LAW_DB) if LAW_DB.exists() else None
        for pk in p["packages"]:
            if pk["reg_id"] not in reg_ids:
                continue
            if pk["reg_id"] in busy or pk["status"] != "ok":
                skipped.append({"reg": pk["reg_name"], "why": "진행 중인 법령명 정비 개정안 있음" if pk["reg_id"] in busy else pk["status"]})
                continue
            cid = "L" + datetime.now().strftime("%y%m%d-%H%M%S") + f"-{len(made):02d}"
            names = [r for r in rows if r["old"] in pk["amendment"]]
            title = "법령명 정비 — " + ", ".join(f"{r['old']} → {r['new']}" for r in names)
            cs, _ = build_changeset(regs[pk["reg_id"]], pk["amendment"], cid, title)
            cs["kind"] = "lawname"
            pkg = build_package(cs, regs, rels, edges, lawdb=db, spell_model=SPELL_MODEL)
            n = _store(cid, pk["reg_id"], title, cs, pkg, None, user, title, {"kind": "lawname", "names": names})
            made.append({"id": cid, "reg": pk["reg_name"], "impacts": n})
    return {"made": made, "skipped": skipped}


def decide(cid: str, key: str, state: str, user: str) -> dict:
    if state not in (*STATES, "clear"):
        return {"error": "state는 accept · reject · clear"}
    if not key.startswith(f"I:{cid}:"):
        return {"error": "이 개정안의 영향 카드가 아닙니다"}
    with write_conn() as con, con.cursor() as cur:
        st = cur.execute("SELECT status FROM amend.changeset WHERE id = %s", (cid,)).fetchone()
        if not st or st[0] not in ("초안", "검토"):   # 승인·공포·기각된 개정안의 결정은 잠근다
            return {"error": f"{cid}는 {st[0] if st else '없는'} 상태라 결정을 바꿀 수 없습니다"}
        prev = cur.execute("SELECT state FROM amend.v_decision_state WHERE key = %s", (key,)).fetchone()
        new = "미결정" if state == "clear" else STATES[state]
        cur.execute("INSERT INTO amend.decision_log (key, kind, prev, state, reviewer, ref) VALUES (%s, 'impact', %s, %s, %s, %s)",
                    (key, prev[0] if prev else None, new, user, json.dumps({"changeset": cid}, ensure_ascii=False)))
    return {"key": key, "state": new}


def submit(cid: str, user: str, comment: str = "") -> dict:
    with write_conn() as con, con.cursor() as cur:
        row = cur.execute("SELECT status FROM amend.changeset WHERE id = %s", (cid,)).fetchone()
        if not row:
            return {"error": f"개정안이 없습니다: {cid}"}
        if row[0] != "초안":
            return {"error": f"초안만 제출할 수 있습니다 (지금 {row[0]})"}
        open_ = cur.execute("SELECT count(*) FROM amend.impact i LEFT JOIN amend.v_decision_state d ON d.key = i.review_key "
                            "WHERE i.changeset_id = %s AND coalesce(d.state, '미결정') NOT IN ('수락', '기각')", (cid,)).fetchone()[0]
        cur.execute("UPDATE amend.changeset SET status = '검토' WHERE id = %s", (cid,))
        cur.execute("INSERT INTO amend.decision_log (key, kind, prev, state, reviewer, comment, ref) VALUES (%s, 'changeset', %s, '검토', %s, %s, %s)",
                    (f"C:{cid}", row[0], user, comment or None, json.dumps({"undecided": open_}, ensure_ascii=False)))
    return {"id": cid, "status": "검토", "undecided": open_}


def package_html(cid: str) -> str | None:
    p = package_path(cid)
    html = p.parent / "package.html"
    if html.exists():
        return html.read_text(encoding="utf-8")
    pkg = load_package(cid)
    if pkg:
        from agent_fix import render
        return render(pkg)
    return None


# ── 최종 개정안 (수락한 카드만 반영) · 승인 흐름 ─────────────────────────────
# 수락의 뜻: 고칠 문구(suggest)가 딸린 「다른 규정이 바뀐 조를 인용」 카드 → 그 고침을 「함께 개정할 규정」 개정문에 넣는다.
#           기각 → 넣지 않는다 (예: 부칙 「다른 내규와의 관계」로 해결). 고칠 문구가 없는 카드(법령 대조·충돌 해소·확인 필요)는 확인 기록만.
# 상태: 초안 → 검토(제출) → 승인(미결정 0일 때, 최종안 저장) → 공포.  검토 중 반려 → 기각.  (sql/amend_schema.sql CHECK 값)

def _impacts_with_state(cur, cid: str) -> list[dict]:
    rows = cur.execute("SELECT i.review_key, i.category, i.reg_id, i.payload, coalesce(d.state, '미결정') "
                       "FROM amend.impact i LEFT JOIN amend.v_decision_state d ON d.key = i.review_key WHERE i.changeset_id = %s", (cid,)).fetchall()
    out = []
    for key, cat, rid, payload, state in rows:
        it = payload if isinstance(payload, dict) else json.loads(payload or "{}")
        out.append({**it, "key": key, "category": cat, "reg_id": rid, "state": state})
    return out


def _companion_from(items: list[dict], main_reg: str, cid: str) -> list[dict]:
    """수락한 고침만으로 규정별 함께 개정문을 다시 만든다 (agent.build_package와 같은 규칙)."""
    from collections import defaultdict
    from regcheck.agent import _unit_label, fix_sentence
    from regcheck.amend import AmendError, build_changeset
    regs, _, _ = corpus()
    others: dict[str, list[dict]] = defaultdict(list)
    for f in items:
        if f.get("path") == "인용받음" and f.get("suggest") and f["reg_id"] != main_reg:
            others[f["reg_id"]].append(f)
    companion = []
    for rid, fs in others.items():
        by_unit, manual = defaultdict(list), []
        for f in fs:
            if f.get("unit") in ("별표", "별지", "부칙"):   # 개정문 적용기가 다루지 않는 단위 → 사람이 직접
                manual.append({"where": f"{f.get('unit')} {f['article']}", "quote": f.get("quote", ""), "suggest": f["suggest"]})
            else:
                by_unit[(f["article"], f.get("para") or 0)].append((f.get("quote", ""), f["suggest"]))
        sents = [fix_sentence(_unit_label(lab, para), list(dict.fromkeys(pr for pr in prs if pr[0] != pr[1])))
                 for (lab, para), prs in by_unit.items() if any(pr[0] != pr[1] for pr in prs)]
        entry = {"reg_id": rid, "reg_name": regs[rid]["name"] if rid in regs else rid, "amendment": "\n".join(sents), "status": "ok", "manual": manual}
        if sents:
            try:   # 적용기로 다시 확인 — 수락한 고침이 원문에 실제로 적용되는지
                ocs, _ = build_changeset(regs[rid], entry["amendment"], f"{cid}-{rid}")
                entry["rows"] = ocs["rows"]
            except AmendError as e:
                entry["status"] = f"적용 못함: {e}"
        else:
            entry["status"] = "수작업만"
        companion.append(entry)
    return companion


def final_package(cid: str) -> dict:
    with write_conn() as con, con.cursor() as cur:
        row = cur.execute("SELECT reg_id, title, amendment, status FROM amend.changeset WHERE id = %s", (cid,)).fetchone()
        if not row:
            return {"error": f"개정안이 없습니다: {cid}"}
        items = _impacts_with_state(cur, cid)
    reg_id, title, amendment, status = row
    pkg = load_package(cid) or {}
    regs, _, _ = corpus()

    def name(rid):
        return regs[rid]["name"] if rid in regs else rid
    fixable = [f for f in items if f.get("path") == "인용받음" and f.get("suggest") and f["reg_id"] != reg_id]
    undecided = [f for f in items if f["state"] not in ("수락", "기각")]
    companion = _companion_from([f for f in fixable if f["state"] == "수락"], reg_id, cid)
    return {
        "id": cid, "status": status, "title": title, "reg_id": reg_id, "reg_name": name(reg_id),
        "amendment_final": pkg.get("amendment_final") or amendment, "rows": pkg.get("rows", []),
        "companion": [{k: v for k, v in c.items() if k != "rows"} | {"applied": bool(c.get("rows"))} for c in companion],
        "_companion_rows": companion,
        "excluded": [{"where": f"{name(f['reg_id'])} {f.get('article', '')}", "quote": f.get("quote", ""), "suggest": f["suggest"]}
                     for f in fixable if f["state"] == "기각"],
        "addendum_draft": pkg.get("addendum_draft", ""),
        "questions": pkg.get("questions", []),
        "counts": {"수락": sum(f["state"] == "수락" for f in items), "기각": sum(f["state"] == "기각" for f in items),
                   "미결정": len(undecided), "고칠 문구 있음": len(fixable), "반영": sum(f["state"] == "수락" for f in fixable)},
        "undecided": [f"{name(f['reg_id'])} {f.get('article', '')}" for f in undecided][:20],
    }


def final_html(fin: dict) -> str:
    """최종안을 기존 패키지 화면 형식으로 (agent_fix.render 재사용)"""
    from agent_fix import render
    pkg = load_package(fin["id"]) or {}
    c = fin["counts"]
    p = {**pkg, "id": fin["id"], "reg_name": fin["reg_name"],
         "request": f"[최종안] {fin['title']} — 수락 {c['수락']} · 기각 {c['기각']} · 함께 개정 반영 {c['반영']}/{c['고칠 문구 있음']}",
         "amendment_input": fin["amendment_final"], "amendment_final": fin["amendment_final"], "rows": fin["rows"] or pkg.get("rows", []),
         "companion": fin["_companion_rows"], "addendum_draft": fin["addendum_draft"], "questions": fin["questions"],
         "law_check": [], "review": [], "rounds": pkg.get("rounds", []), "added_sentences": pkg.get("added_sentences", []),
         "breaks_before": pkg.get("breaks_before"), "breaks_after": pkg.get("breaks_after", 0)}
    return render(p)


def _set_status(cid: str, want_from: tuple[str, ...], to: str, user: str, comment: str = "", ref: dict | None = None) -> dict:
    with write_conn() as con, con.cursor() as cur:
        row = cur.execute("SELECT status FROM amend.changeset WHERE id = %s", (cid,)).fetchone()
        if not row:
            return {"error": f"개정안이 없습니다: {cid}"}
        if row[0] not in want_from:
            return {"error": f"{'·'.join(want_from)} 상태에서만 할 수 있습니다 (지금 {row[0]})"}
        cur.execute("UPDATE amend.changeset SET status = %s WHERE id = %s", (to, cid))
        cur.execute("INSERT INTO amend.decision_log (key, kind, prev, state, reviewer, comment, ref) VALUES (%s, 'changeset', %s, %s, %s, %s, %s)",
                    (f"C:{cid}", row[0], to, user, comment or None, json.dumps(ref or {}, ensure_ascii=False)))
    return {"id": cid, "status": to}


def approve(cid: str, user: str, comment: str = "") -> dict:
    fin = final_package(cid)
    if fin.get("error"):
        return fin
    if fin["status"] != "검토":
        return {"error": f"검토 상태에서만 승인할 수 있습니다 (지금 {fin['status']})"}
    if fin["counts"]["미결정"]:
        return {"error": f"결정하지 않은 영향 카드가 {fin['counts']['미결정']}건 있습니다: {', '.join(fin['undecided'][:3])} …"}
    bad = [c["reg_name"] for c in fin["companion"] if c["status"].startswith("적용 못함")]
    if bad:
        return {"error": f"함께 개정문이 원문에 적용되지 않습니다: {', '.join(bad)}"}
    out = package_path(cid).parent
    out.mkdir(parents=True, exist_ok=True)
    (out / "final.html").write_text(final_html(fin), encoding="utf-8")
    (out / "final.json").write_text(json.dumps({k: v for k, v in fin.items() if k != "_companion_rows"}, ensure_ascii=False, indent=1), encoding="utf-8")
    res = _set_status(cid, ("검토",), "승인", user, comment, {"counts": fin["counts"], "companion": [c["reg_name"] for c in fin["companion"]]})
    return res if res.get("error") else {**res, "counts": fin["counts"]}


def reject_cs(cid: str, user: str, comment: str = "") -> dict:
    return _set_status(cid, ("검토",), "기각", user, comment)


def publish(cid: str, user: str, comment: str = "") -> dict:
    """공포(등록·공지). 이어서 수락한 함께 개정안을 다른 내규의 초안으로 만들어 검토 대기열에 올린다 (내규 공포 → 따라 고칠 곳)."""
    res = _set_status(cid, ("승인",), "공포", user, comment)
    if res.get("error"):
        return res
    try:
        follow = companion_drafts(cid, user)
    except Exception as e:   # 공포 자체는 끝났으므로 후속 초안 실패는 알리기만
        follow = {"made": [], "skipped": [{"reg": "", "why": f"함께 개정 초안을 만들지 못함: {e}"}]}
    return {**res, "follow_up": follow}


def companion_drafts(cid: str, user: str) -> dict:
    """공포된 개정안의 함께 개정안(수락한 카드만) → 내규별 초안. 점검은 공포된 개정이 반영된 본문 기준.
    같은 원 개정안에서 이미 만든 초안이 있으면 건너뛴다."""
    from regcheck.agent import build_package
    from regcheck.amend import AmendError, apply, build_changeset, parse_amendment
    from regcheck.change_check import _reidentify
    from regcheck.lawdb import DEFAULT as LAW_DB, LawDB
    from regcheck.neighbors import load as load_neighbors
    fin = final_package(cid)
    if fin.get("error"):
        return {"made": [], "skipped": [{"reg": "", "why": fin["error"]}]}
    regs, rels, edges = corpus()
    origin = fin["reg_id"]
    try:   # 공포된 개정이 반영된 원 내규 (번호가 바뀌었으면 새 번호로 ID를 다시 붙임)
        after, _ = apply(regs[origin], parse_amendment(fin["amendment_final"]))
        regs_after = {**regs, origin: _reidentify(after)}
    except (AmendError, KeyError):
        regs_after = regs
    with write_conn() as con:
        done = {r[0] for r in con.execute("select reg_id from amend.changeset where payload->>'kind' = 'companion' and payload->>'origin' = %s", (cid,)).fetchall()}
    made, skipped = [], []
    with _lock:
        db = LawDB(LAW_DB) if LAW_DB.exists() else None
        for c in fin["_companion_rows"]:
            if c["reg_id"] in done:
                skipped.append({"reg": c["reg_name"], "why": "이미 만든 함께 개정 초안이 있음"})
                continue
            if c["status"] != "ok" or not c.get("amendment"):
                skipped.append({"reg": c["reg_name"], "why": c["status"] + (f" (수작업 {len(c.get('manual') or [])}건)" if c.get("manual") else "")})
                continue
            ncid = "F" + datetime.now().strftime("%y%m%d-%H%M%S") + f"-{len(made):02d}"
            title = f"함께 개정 — 「{fin['reg_name']}」 개정({cid}) 공포에 따른 인용 정비"
            cs, _ = build_changeset(regs_after[c["reg_id"]], c["amendment"], ncid, title)
            cs["kind"] = "companion"
            pkg = build_package(cs, regs_after, rels, edges, lawdb=db, spell_model=None, neighbors=load_neighbors())
            n = _store(ncid, c["reg_id"], title, cs, pkg, None, user, title, {"kind": "companion", "origin": cid})
            made.append({"id": ncid, "reg": c["reg_name"], "impacts": n})
    return {"made": made, "skipped": skipped}


def final_file(cid: str) -> str | None:
    f = package_path(cid).parent / "final.html"
    return f.read_text(encoding="utf-8") if f.exists() else None
