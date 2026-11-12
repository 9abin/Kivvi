"""조항 직접 편집 API. 원문은 읽고 검토 초안은 작업 폴더에 저장한다."""

import hashlib
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from regcheck.amend import AmendError, article_edit_amendment, build_changeset
from regcheck.cand_state import CandState, found_by
from regcheck.db import load_env
from regcheck.rank_fusion import fuse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ontology-search"))
REVIEW_LIMIT = 10


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
    from ontology_search.validated_graph import DomainGraph

    return DomainGraph(ROOT / "ontology-search" / "data" / "ontology.db")


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


@lru_cache(maxsize=1)
def lexical_index(texts):
    from ontology_search.bm25 import BM25

    return BM25(texts)


def review_corpus(regs, ctx):
    corpus = {}
    for reg in regs.values():
        units = list(reg["articles"]) + list(reg.get("appendices", []))
        for addendum in reg.get("addenda", []):
            units.extend(addendum["articles"])
        for unit in units:
            if not unit.get("deleted") and unit["id"] in ctx.unit_text and unit["id"] in ctx.nodes:
                corpus[unit["id"]] = unit
    return corpus


def review_candidates(ctx, corpus, article, revised, reg_name, cq, rag, domain, notices):
    # 평가의 focus_queries와 같이 변경 문구를 앞에 두고 변경된 항을 그래프의 시작점으로 쓴다.
    new_items = {tuple(it["path"]): it["norm"] for it in revised["items"] if not it.get("deleted")}
    changed_items = [it for it in article["items"] if not it.get("deleted")
                     and new_items.get(tuple(it["path"])) != it["norm"]]
    source = "\n".join(it["norm"] for it in changed_items) or article["norm"][:700]
    number_change = f"{article['label']} → {revised['label']}\n" if article["label"] != revised["label"] else ""
    query = f"{reg_name} {article['label']} {article['title']}\n{number_change}{' '.join(cq.removed)} → {' '.join(cq.added)}\n{source}"[:1200]
    ids = sorted(corpus)
    texts = tuple(f"{ctx.nodes[aid].get('label', '')} {ctx.unit_text[aid]}" for aid in ids)
    lex = dict(zip(ids, lexical_index(texts).scores(query).tolist()))
    excluded = {article["id"]}
    keyword = sorted((aid for aid in ids if aid not in excluded and lex[aid] > 0), key=lambda aid: (-lex[aid], aid))[:200]
    dense = [aid for aid in (rag(query, 200) if rag else []) if aid in corpus and aid not in excluded]
    graph_ids = []
    if domain is not None:
        try:
            graph_ids, _ = domain.rank(corpus, [article], query, lex, [it["id"] for it in changed_items])
            graph_ids = [aid for aid in graph_ids if aid in corpus and aid not in excluded]
        except Exception:
            notices["도메인 그래프"] = "그래프 탐색에 실패해 검색 후보로 계속 분석했습니다."
    citing = {edge["src"] for edge in ctx.kg.inb.get(("CITES", article["id"]), [])} & corpus.keys() - excluded
    graph_rank = list(dict.fromkeys([*sorted(citing, key=lambda aid: (-lex[aid], aid)), *graph_ids])) if number_change else graph_ids
    ranked = fuse(fuse(dense, keyword), graph_rank)
    picked = []
    for order, aid in enumerate(ranked[:REVIEW_LIMIT]):
        paths = []
        if aid in dense[:100]:
            paths.append("VECTOR")
        if aid in keyword[:100]:
            paths.append("BM25")
        if aid in graph_ids[:100]:
            paths.append("GRAPH_CONCEPT")
        # 직접 인용은 합쳐진 후보에 발견 근거로만 표시한다.
        if aid in citing:
            paths.append("CITES_in")
        picked.append(CandState(aid, "graph" if "GRAPH_CONCEPT" in paths else "rag", "luna", paths, "참고", order))
    return picked, len(ranked)


def _candidate(state, ctx, regs):
    node = ctx.nodes[state.id]
    return {"id": state.id, "regulation": regs.get(node.get("reg"), {}).get("name", ""),
            "label": node.get("label", state.id), "title": node.get("title", ""), "tier": state.tier,
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

    return rag, luna, writer, notices


def submit(body):
    from regcheck.change_query import analyze
    from regcheck.impact_pipeline import apply_luna, luna_items
    from regcheck.impact_view import load_context
    from regcheck.luna_rerank import change_block, rank_list

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
        try:
            domain = graph()
        except Exception:
            domain = None
            notices["도메인 그래프"] = "도메인 그래프를 불러오지 못해 검색 후보로 계속 분석했습니다."
        cq = analyze(reg, amendment, cid)
        if cq.kind == "분석 실패":
            return {"error": cq.error or "개정문을 분석하지 못했습니다."}
        candidates, retrieved = review_candidates(ctx, review_corpus(regs, ctx), article, revised, reg["name"], cq, rag, domain, notices)
        change_text = change_block(reg, amendment, article["id"])
        reranked = False
        if luna and candidates:
            try:
                result = rank_list(change_text, luna_items(candidates, ctx.nodes, ctx.unit_text), luna)
                apply_luna(candidates, result)
                by_id = {candidate.id: candidate for candidate in candidates}
                candidates = [by_id[aid] for aid in result["order"]]
                reranked = True
            except Exception:
                notices["Luna"] = "Luna 선별이 제한 시간 안에 완료되지 않았거나 호출에 실패해 검색 순서로 후보를 표시했습니다."
        if reranked and writer and candidates:
            from regcheck.answer_writer import write_answers

            def write_candidate(candidate):
                write_answers([candidate], amendment, change_text, ctx.nodes, writer)

            with ThreadPoolExecutor(max_workers=3) as pool:
                list(pool.map(write_candidate, candidates[:3]))
            if any(s.answer.get("error") for s in candidates[:3]):
                notices["검토 문장 생성"] = "일부 검토 문장을 생성하지 못해 후보의 발견 경로와 근거를 표시했습니다."
            elif len(candidates) > 3:
                notices["검토 문장 생성"] = "상위 3개 후보에만 검토 문장을 생성하며, 나머지 후보는 발견 경로와 근거를 표시합니다."
    except Exception as exc:
        return {"error": f"영향 분석을 완료하지 못했습니다 ({type(exc).__name__}). 색인과 모델 설정을 확인해 주세요."}
    response = {
        "id": cid, "regulation": reg["name"],
        "article": f"{article['label']} → {revised['label']}" if revised["label"] != article["label"] else article["label"],
        "amendment": amendment, "rows": draft["rows"],
        "kind": cq.kind, "change_evidence": cq.evidence, "retrieved": retrieved, "rerank_input": min(retrieved, REVIEW_LIMIT),
        "candidates": [_candidate(s, ctx, regs) for s in candidates],
        "options": {"BM25": True, "도메인 그래프": domain is not None and "도메인 그래프" not in notices,
                    "의미 검색": rag is not None and "의미 검색" not in notices,
                    "Luna 리랭킹": reranked, "검토 문장 생성": any(s.answer.get("checked") for s in candidates)},
        "notices": list(notices.values()),
    }
    folder = ROOT / "out" / "changes" / cid
    folder.mkdir(parents=True)
    (folder / "changeset.json").write_text(json.dumps(draft, ensure_ascii=False, indent=1), encoding="utf-8")
    return response
