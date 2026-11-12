"""조항 직접 편집 API. 원문은 읽고 검토 초안은 작업 폴더에 저장한다."""

import hashlib
import json
import os
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from regcheck.amend import AmendError, article_edit_amendment, build_changeset
from regcheck.cand_state import found_by
from regcheck.db import load_env

ROOT = Path(__file__).resolve().parents[1]


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
                             {"version": _version(a)} for a in reg["articles"]],
                "sections": sections}
    regs = [_reg(p.stem) for p in (ROOT / "out" / "parsed").glob("[0-9]*.json")]
    return {"regulations": [{"id": r["reg_id"], "name": r["name"]} for r in sorted(regs, key=lambda r: r["name"])]}


@lru_cache(maxsize=1)
def graph():
    from regcheck.graph_source import GraphSource

    return GraphSource.load(ROOT)


@lru_cache(maxsize=1)
def rag_backend():
    from regcheck.impact_view import make_rag
    from regcheck.vectors import Index, OllamaEncoder, load_encoder

    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    name = "qwen3-embedding-4b-q8_0" if os.environ.get("IMPACT_EMB") == "q4b" else "BAAI__bge-m3"
    path = ROOT / "out" / "vector" / name
    encoder = load_encoder(path)
    if isinstance(encoder, OllamaEncoder):
        encoder.timeout = 6
    return make_rag(encoder, Index(path))


def decide_scorer(reg, amendment, cid):
    python, model = os.environ.get("IMPACT_DECIDE_PYTHON"), os.environ.get("IMPACT_DECIDE_MODEL")
    if not (python and model):
        return None
    from regcheck.change_query import analyze

    kind = analyze(reg, amendment, cid).kind

    def score(inputs):
        if not inputs:
            return {}
        with tempfile.TemporaryDirectory() as tmp:
            pairs, scores = Path(tmp) / "pairs.json", Path(tmp) / "scores.json"
            pairs.write_text(json.dumps({"pairs": [{"key": k, "kind": kind, "state": v}
                                                     for k, v in inputs.items()]}, ensure_ascii=False), encoding="utf-8")
            subprocess.run([python, str(ROOT / "scripts" / "decide_score_pairs.py"), model, str(pairs), str(scores)],
                           cwd=ROOT, check=True, capture_output=True, timeout=8)
            return {k: v["lv"] for k, v in json.loads(scores.read_text(encoding="utf-8")).items()}

    return score


def _candidate(state, ctx, regs):
    node = ctx.nodes[state.id]
    return {"id": state.id, "regulation": regs.get(node.get("reg"), {}).get("name", ""),
            "label": node.get("label", state.id), "title": node.get("title", ""), "tier": state.tier,
            "paths": found_by(state.paths), "evidence": state.evidence, "answer": state.answer.get("text", ""),
            "text": ctx.unit_text.get(state.id, "")}


def analysis_backends(reg, amendment, cid):
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

    lv = optional("규칙 판단 모델", decide_scorer(reg, amendment, cid), {})
    luna = writer = None
    if not load_env().get("OPENAI_API_KEY", "").strip().strip('"\"'):
        notices["Luna"] = "OPENAI_API_KEY가 설정되지 않아 Luna 선별과 검토 문장 생성을 건너뛰었습니다."
    else:
        try:
            from openai import OpenAI
            from regcheck.luna_rerank import luna_call

            model = os.environ.get("IMPACT_LUNA_MODEL") or "gpt-5.6-luna"
            luna = luna_call(model, timeout=14, attempts=1, max_retries=0)
            client = OpenAI(api_key=load_env()["OPENAI_API_KEY"].strip().strip('"'), timeout=8, max_retries=0)

            def writer(prompt):
                response = client.chat.completions.create(
                    model=model, messages=[{"role": "user", "content": prompt}],
                    reasoning_effort="low", max_completion_tokens=600,
                )
                return response.choices[0].message.content or ""
        except Exception:
            notices["Luna"] = "Luna 클라이언트를 준비하지 못했습니다."

    return rag, lv, luna, writer, notices


def submit(body):
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
        amendment = article_edit_amendment(article, edited)
        cid = f"edit-{uuid4().hex}"
        draft, _ = build_changeset(reg, amendment, cid, title=f"{article['label']} 직접 편집")
    except AmendError as exc:
        return {"error": str(exc)}

    regs = {p.stem: _reg(p.stem) for p in (ROOT / "out" / "parsed").glob("[0-9]*.json")}
    regs[reg["reg_id"]] = reg
    ctx = load_context(ROOT, regs)
    if ctx is None:
        return {"error": "규칙 그래프가 없습니다. scripts/build_kg.py로 생성해 주세요."}
    try:
        domain = graph()
        rag, lv, luna, writer, notices = analysis_backends(reg, amendment, cid)

        def luna_error(_exc):
            notices["Luna"] = "Luna 선별이 제한 시간 안에 완료되지 않았거나 호출에 실패해 검색 순서로 후보를 표시했습니다."

        result = run_pipeline(ctx, regs, reg["reg_id"], amendment, cid, rag=rag, graph=domain, lv=lv, luna=luna,
                              on_luna_error=luna_error)
        if not result["error"] and writer and "Luna" not in notices:
            from regcheck.answer_writer import write_answers

            candidates = result["rule"] + result["other"]
            if candidates:
                def write_candidate(candidate):
                    write_answers([candidate], amendment, result["change_text"], ctx.nodes, writer)

                with ThreadPoolExecutor(max_workers=3) as pool:
                    list(pool.map(write_candidate, candidates[:3]))
                if any(s.answer.get("error") for s in candidates[:3]):
                    notices["검토 문장 생성"] = "일부 검토 문장을 생성하지 못해 후보의 발견 경로와 근거를 표시했습니다."
                elif len(candidates) > 3:
                    notices["검토 문장 생성"] = "상위 3개 후보에만 검토 문장을 생성하며, 나머지 후보는 발견 경로와 근거를 표시합니다."
    except Exception as exc:
        return {"error": f"영향 분석을 완료하지 못했습니다 ({type(exc).__name__}). 색인과 모델 설정을 확인해 주세요."}
    if result["error"]:
        return {"error": result["error"]}

    response = {
        "id": cid, "regulation": reg["name"], "article": article["label"], "amendment": amendment, "rows": draft["rows"],
        "kind": result["cq"].kind, "change_evidence": result["cq"].evidence, "hidden": result["hidden"],
        "rule": [_candidate(s, ctx, regs) for s in result["rule"]],
        "other": [_candidate(s, ctx, regs) for s in result["other"]],
        "options": {"도메인 그래프": domain is not None, "의미 검색": rag is not None and "의미 검색" not in notices,
                    "규칙 판단 모델": lv is not None and "규칙 판단 모델" not in notices,
                    "Luna 선별": bool(result["usage"]),
                    "검토 문장 생성": any(s.answer.get("checked") for s in result["rule"] + result["other"])},
        "notices": list(notices.values()),
    }
    folder = ROOT / "out" / "changes" / cid
    folder.mkdir(parents=True)
    (folder / "changeset.json").write_text(json.dumps(draft, ensure_ascii=False, indent=1), encoding="utf-8")
    return response
