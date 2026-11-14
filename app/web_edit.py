"""조항 직접 편집 API. 원문은 읽고 검토 초안은 작업 폴더에 저장한다.

영향 분석은 평가한 파이프라인(regcheck.impact_pipeline.run_pipeline)을 그대로 쓴다(261113):
개정 영향 온톨로지 경로 · 개정문 낱말 일치(판단 모델로 거름) + 의미 검색(luna가 고름) → 첫 화면 10개 + 더 보기.
"""

import hashlib
import json
import os
import re
import sys
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from regcheck.amend import AmendError, article_edit_amendment, build_changeset
from regcheck.cand_state import found_by
from regcheck.db import load_env

ROOT = Path(__file__).resolve().parents[1]
ANSWER_LIMIT = 3   # 검토 문장은 첫 화면 앞 3개만 만든다(응답 시간)
DECIDE_TIMEOUT = 90   # 판단 모델 · luna(60초 × 2) · 검토 문장을 합쳐 브라우저 제한(edit-review.js REVIEW_TIMEOUT_MS)보다 짧게


def _reg(reg_id):
    if not isinstance(reg_id, str) or not reg_id.isascii() or not reg_id.isdecimal():
        raise AmendError("규정을 선택해 주세요.")
    path = ROOT / "out" / "parsed" / f"{reg_id}.json"
    if not path.is_file():
        raise AmendError("파싱한 규정을 찾지 못했습니다.")
    return json.loads(path.read_text(encoding="utf-8"))


def _version(article):
    return hashlib.sha256(article["text"].encode("utf-8")).hexdigest()


def catalog(reg_id=""):
    if reg_id:
        reg = _reg(reg_id)
        sections = [{"label": "부칙" + (f" ({a['date']})" if a.get("date") else ""), "text": a.get("text", "")}
                    for a in reg.get("addenda", [])]
        sections += [{"label": f"{a['kind']} {a['number']} {a['title']}".strip(), "text": a["text"]}
                     for a in reg.get("appendices", [])]
        return {"name": reg["name"], "preamble": reg.get("preamble") or [],
                "articles": [{k: a[k] for k in ("id", "label", "title", "text", "deleted")} |
                             {"version": _version(a), "chapter": a.get("chapter") or ""} for a in reg["articles"]],
                "sections": sections}
    regs = [_reg(p.stem) for p in (ROOT / "out" / "parsed").glob("[0-9]*.json")]
    return {"regulations": [{"id": r["reg_id"], "name": r["name"]} for r in sorted(regs, key=lambda r: r["name"])]}


@lru_cache(maxsize=1)
def rag_backend():
    from regcheck.impact_view import make_rag
    from regcheck.vectors import Index, OllamaEncoder, load_encoder

    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    name = "qwen3-embedding-4b-q8_0" if os.environ.get("IMPACT_EMB") == "q4b" else "BAAI__bge-m3"
    path = ROOT / "out" / "vector" / name
    # 판단 모델이 GPU를 쓰면 의미 검색 모델은 CPU로 둔다. 8GB GPU에 둘 다 올리면 판단 단계가 수 분으로 늘었다(261113: 230초 → 아래 참고)
    device = os.environ.get("IMPACT_EMB_DEVICE") or ("cpu" if os.environ.get("IMPACT_DECIDE_PYTHON") else None)
    encoder = load_encoder(path, device)
    if isinstance(encoder, OllamaEncoder):
        encoder.timeout = 6
    return make_rag(encoder, Index(path))


def _candidate(state, ctx, regs):
    node = ctx.nodes.get(state.id, {})
    return {"id": state.id, "regulation": regs.get(node.get("reg"), {}).get("name", ""),
            "label": node.get("label", state.id), "title": node.get("title", ""), "tier": state.tier,
            "group": "규칙 후보" if state.route == "decide" else "의미 검색 후보",
            "paths": found_by(state.paths), "evidence": state.evidence, "answer": state.answer.get("text", ""),
            "text": ctx.unit_text.get(state.id, "")}


def analysis_backends():
    notices = {}

    def optional(name, run, fallback):
        if run is None:
            return None

        def call(*args):
            if name in notices:
                return fallback
            try:
                return run(*args)
            except Exception:
                notices[name] = f"{name} 호출에 실패해 이 단계를 건너뛰었습니다."
                return fallback

        return call

    try:
        rag = optional("의미 검색", rag_backend(), [])
    except FileNotFoundError:
        rag = None
        notices["의미 검색"] = "의미 검색 색인이 없어 이 단계를 건너뛰었습니다."
    except Exception:
        rag = None
        notices["의미 검색"] = "의미 검색 색인이나 임베딩 모델을 불러오지 못했습니다."

    luna = writer = None
    if not load_env().get("OPENAI_API_KEY", "").strip().strip('"\"'):
        notices["Luna"] = "OPENAI_API_KEY가 설정되지 않아 Luna 선별과 검토 문장 생성을 건너뛰었습니다."
    else:
        try:
            from openai import OpenAI
            from regcheck.luna_rerank import luna_call

            model = os.environ.get("IMPACT_LUNA_MODEL") or "gpt-5.6-luna"
            luna = luna_call(model, timeout=60, attempts=1, max_retries=0)   # 후보 50개 · orders 2라 14초로는 끝나지 않았다(261113)
            client = OpenAI(api_key=load_env()["OPENAI_API_KEY"].strip().strip('"'), timeout=8, max_retries=0)

            def writer(prompt):
                response = client.chat.completions.create(
                    model=model, messages=[{"role": "user", "content": prompt}],
                    reasoning_effort="low", max_completion_tokens=600,
                )
                return response.choices[0].message.content or ""
        except Exception:
            notices["Luna"] = "Luna 클라이언트를 준비하지 못했습니다."

    return rag, luna, writer, notices


def submit(body):
    from regcheck.change_query import analyze
    from regcheck.decide_runner import subprocess_scorer
    from regcheck.impact_pipeline import run_pipeline
    from regcheck.impact_view import load_context

    try:
        reg = _reg(body.get("reg_id"))
        article = next((a for a in reg["articles"] if a["id"] == body.get("article_id") and not a.get("deleted")), None)
        if article is None:
            raise AmendError("편집할 본문 조항을 선택해 주세요.")
        if body.get("version") != _version(article):
            raise AmendError("현행 원문이 바뀌었습니다. 조항을 다시 불러온 뒤 수정해 주세요.")
        edited = body.get("edited")
        if not isinstance(edited, str):
            raise AmendError("수정 문안을 입력해 주세요.")
        amendment = article_edit_amendment(article, edited, reg=reg)
        cid = f"edit-{uuid4().hex}"
        draft, revised_reg = build_changeset(reg, amendment, cid, title=f"{article['label']} 직접 편집")
        revised = next(a for a in revised_reg["articles"] if a["_origin"] == article["id"])
    except AmendError as exc:
        return {"error": str(exc)}

    regs = {p.stem: _reg(p.stem) for p in (ROOT / "out" / "parsed").glob("[0-9]*.json")}
    regs[reg["reg_id"]] = reg
    ctx = load_context(ROOT, regs)
    if ctx is None:
        return {"error": "조항 검색 메타데이터가 없습니다. scripts/build_kg.py로 생성해 주세요."}
    try:
        rag, luna, writer, notices = analysis_backends()
        cq = analyze(reg, amendment, cid)
        if cq.kind == "분석 실패":
            return {"error": cq.error or "개정문을 분석하지 못했습니다."}
        if rag is not None:   # 의미 검색 색인에는 그래프에 없는 단위(전문 · 항목 등)도 있다. 화면에 못 그리므로 거른다(261113 Codex 리뷰)
            search = rag
            rag = lambda q, k: [a for a in search(q, k) if a in ctx.nodes and a in ctx.unit_text]  # noqa: E731
        scorer = subprocess_scorer(ROOT, reg, amendment, cid, timeout=DECIDE_TIMEOUT)
        if scorer is None:
            notices["판단 모델"] = "판단 모델 환경(IMPACT_DECIDE_PYTHON · IMPACT_DECIDE_MODEL)이 없어 규칙 후보를 거르지 않고 모두 표시했습니다."

        def lv(inputs):
            try:
                return scorer(inputs)
            except Exception:
                notices["판단 모델"] = "판단 모델 호출에 실패해 규칙 후보를 거르지 않고 모두 표시했습니다."
                return {}

        def luna_failed(exc):
            notices["Luna"] = "Luna 선별이 제한 시간 안에 완료되지 않았거나 호출에 실패해 검색 순서로 후보를 표시했습니다."

        res = run_pipeline(ctx, regs, reg["reg_id"], amendment, cid, rag=rag, lv=lv if scorer else None,
                           luna=luna, writer=None, on_luna_error=luna_failed)
        if res["error"]:
            return {"error": res["error"]}
        reranked = luna is not None and "Luna" not in notices
        first, more = res["first"], res["more"]
        if reranked and writer and first:
            from regcheck.answer_writer import write_answers

            def write_candidate(candidate):
                write_answers([candidate], amendment, res["change_text"], ctx.nodes, writer)

            with ThreadPoolExecutor(max_workers=3) as pool:
                list(pool.map(write_candidate, first[:ANSWER_LIMIT]))
            if any(s.answer.get("error") for s in first[:ANSWER_LIMIT]):
                notices["검토 문장 생성"] = "일부 검토 문장을 생성하지 못해 후보의 발견 경로와 근거를 표시했습니다."
            elif len(first) + len(more) > ANSWER_LIMIT:
                notices["검토 문장 생성"] = f"상위 {ANSWER_LIMIT}개 후보에만 검토 문장을 생성하며, 나머지 후보는 발견 경로와 근거를 표시합니다."
        shown = first + more
        response = {
            "id": cid, "regulation": reg["name"],
            "article": f"{article['label']} → {revised['label']}" if revised["label"] != article["label"] else article["label"],
            "amendment": amendment, "rows": draft["rows"],
            "kind": cq.kind, "change_evidence": cq.evidence,
            "retrieved": len(res["states"]),
            "rerank_input": sum(1 for s in res["states"] if s.route == "luna" and s.rerank.get("by") in ("luna", "skipped")),
            "candidates": [_candidate(s, ctx, regs) for s in shown], "first_n": len(first),
            "options": {"온톨로지 경로": True,
                        "의미 검색": rag is not None and "의미 검색" not in notices,
                        "규칙 판단 모델": scorer is not None and "판단 모델" not in notices,
                        "Luna 리랭킹": reranked, "검토 문장 생성": any(s.answer.get("checked") for s in shown)},
            "notices": list(notices.values()),
        }
    except Exception as exc:
        return {"error": f"영향 분석을 완료하지 못했습니다 ({type(exc).__name__}). 색인과 모델 설정을 확인해 주세요."}
    folder = ROOT / "out" / "changes" / cid
    folder.mkdir(parents=True)
    (folder / "changeset.json").write_text(json.dumps(draft, ensure_ascii=False, indent=1), encoding="utf-8")
    return response


# ---- 검토 저장 · 검토 내역 -------------------------------------------------------------
# 규정 원문 DB는 건드리지 않는다. 분석할 때 만든 초안 폴더(out/changes/<id>/)에 review.json을 한 개 더 둔다.
# (실제 배포에서는 이 기록을 DB 표로 옮긴다. 화면이 부르는 주소와 JSON 모양은 그대로 쓸 수 있게 만들었다.)
CHANGES = ROOT / "out" / "changes"
_CID = re.compile(r"edit-[0-9a-f]{32}")


def _text(value, limit):
    return value[:limit] if isinstance(value, str) else ""


def save_review(body):
    cid = body.get("id")
    if not isinstance(cid, str) or not _CID.fullmatch(cid):
        return {"error": "검토 번호가 올바르지 않습니다."}
    folder = CHANGES / cid
    if not (folder / "changeset.json").is_file():
        return {"error": "분석 결과를 찾지 못했습니다. 영향 분석을 다시 실행해 주세요."}
    items = body.get("items")
    if not isinstance(items, list) or len(items) > 300:
        return {"error": "저장할 검토 항목이 올바르지 않습니다."}
    clean = [{"id": _text(it.get("id"), 80), "regulation": _text(it.get("regulation"), 100), "label": _text(it.get("label"), 60),
              "title": _text(it.get("title"), 100), "reason": _text(it.get("reason"), 2000), "done": bool(it.get("done")),
              "note": _text(it.get("note"), 500), "badge": _text(it.get("badge"), 100), "quote": _text(it.get("quote"), 600),
              "text": _text(it.get("text"), 3200)} for it in items if isinstance(it, dict)]
    record = {"id": cid, "saved_at": datetime.now().astimezone().isoformat(timespec="seconds"),
              "regulation": _text(body.get("regulation"), 100), "article": _text(body.get("article"), 100),
              "before": _text(body.get("before"), 20000), "after": _text(body.get("after"), 20000),
              "total": len(clean), "done": sum(1 for it in clean if it["done"]), "items": clean}
    (folder / "review.json").write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"ok": True, "saved_at": record["saved_at"], "total": record["total"], "done": record["done"]}


def history(cid=""):
    if cid:
        if not _CID.fullmatch(cid):
            return {"error": "검토 번호가 올바르지 않습니다."}
        path = CHANGES / cid / "review.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"error": "저장된 검토가 없습니다."}
    rows = []
    for path in CHANGES.glob("edit-*/review.json"):
        try:
            r = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        rows.append({k: r.get(k) for k in ("id", "saved_at", "regulation", "article", "total", "done")})
    return {"items": sorted(rows, key=lambda r: r.get("saved_at") or "", reverse=True)}


def delete_review(body):
    """저장한 검토(review.json)만 지운다. 분석 초안(changeset.json)은 그대로 둔다."""
    cid = body.get("id")
    if not isinstance(cid, str) or not _CID.fullmatch(cid):
        return {"error": "검토 번호가 올바르지 않습니다."}
    path = CHANGES / cid / "review.json"
    if not path.is_file():
        return {"error": "저장된 검토가 없습니다."}
    path.unlink()
    return {"ok": True}
