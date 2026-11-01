"""조합 실험 단계 실행 (rag-combo-experiment.md 4절). 단계마다 결과를 파일로 저장하고, 있으면 다시 계산하지 않는다.

  ① rewrite  rewrite_<L>.json            질문 바꿔 쓰기
  ② cands    emb_<E>.npy, cands_<E>_<L>.json   문서·질문 임베딩, 후보 30개
  ③ rerank   rerank_<E>_<L>_<R>.json     리랭크 점수 (R0은 계산 없이 RRF 순서)
  ④⑤ final   select_<조합>.json, results_<조합>.json   선택(끔/켬) 뒤 최종 근거와 지표
  ⑥ answer   answers_<조합>.json          답변과 채점

모든 단계는 질문별 소요 시간(time.perf_counter)을 결과와 함께 저장한다. 캐시를 쓰는 단계는 처음 계산한 시간을 파일에 같이 저장해 두었다가
검색 합계에 더한다 (5.3절). 모델 적재 시간과 GPU·RAM 피크는 파일의 meta에 따로 적는다.
"""
import json
import os
import re
import statistics
import sys
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor

import config as app_config
import src.search.search as search_module
from src.answer import generate as gen_module
from src.combo import env, graph_expand
from src.combo import metrics as M
from src.combo.angle_hyde import ANGLE_HYDE_PROMPT, parse_angles
from src.combo.models import (LLM, free_gpu, gpu_peak_gb, make_embedder, make_reranker, reset_gpu_peak, safe, sha)
from src.combo.searcher import ComboSearcher
from src.search import glossary, synonyms
from src.search.search import group_by_article

DEFAULTS = {
    "data": {"questions": "eval/answer_eval_100_tiered.jsonl", "out_dir": "eval/combo", "limit": 0},
    "rewrite": {"source": "llm", "workers": 1},
    "embedding": {"model": "BAAI/bge-m3"},
    "reranker": {"kind": "cross", "model": "BAAI/bge-reranker-v2-m3"},
    "select": {"enabled": False, "pool": 16, "ks": [8, 10], "workers": 1},
    "eval": {"rerank_ks": [5, 8, 10, 16], "final_ks": [8, 10], "answer_k": 8, "num_ctx": None},
}
ROLES = ("rewrite", "select", "answer", "verify")

# HyDE: 질문에 답하는 조문이 있다면 어떤 문장일지 규정 문체로 쓰게 하고, 그 가상 조문으로 검색한다 (rewrite.prompt = "hyde")
HYDE_PROMPT = """직원이 사내 규정에 대해 물은 질문이다. 이 질문에 답하는 사내 규정 조문이 있다면 어떤 문장일지, 규정 문체로 2~3문장 쓴다.
- 질문의 상황이 규정에서 어떤 제도·사항으로 다뤄질지 떠올려 그 제도 이름과 요건·절차·효과를 쓴다.
- 조 번호, 규정 이름, 구체적 숫자·금액·일수는 지어내지 않는다 ("일정 기간", "정하는 바에 따라"처럼 쓴다).
- 출력은 한 줄로, 조문 문장만 쓴다. 설명, 따옴표, 번호를 쓰지 않는다."""


def deep_merge(base, over):
    out = dict(base)
    for k, v in over.items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config(path, overrides=()):
    with open(path, "rb") as f:
        cfg = deep_merge(DEFAULTS, tomllib.load(f))
    for ov in overrides:  # "select.enabled=true" 처럼 점으로 구분한 경로 = 값(TOML 값). 값이 TOML로 읽히지 않으면 문자열
        key, _, val = ov.partition("=")
        try:
            val = tomllib.loads(f"v = {val}")["v"]
        except tomllib.TOMLDecodeError:
            pass
        node = cfg
        parts = key.split(".")
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = val
    emb = cfg["embedding"]
    if emb.get("instruction") == "@config":  # config.EMBED_QUERY_INSTRUCT (Qwen3-Embedding 질문 지시문)
        emb["instruction"] = app_config.EMBED_QUERY_INSTRUCT
    cfg["_name"] = cfg.get("name") or os.path.splitext(os.path.basename(path))[0]
    return cfg


def pct(vals, p):
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        return None
    return round(vals[min(len(vals) - 1, int(round(p / 100 * (len(vals) - 1))))], 3)


def p50_p95(vals):
    return {"p50": pct(vals, 50), "p95": pct(vals, 95)}


def save_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def load_json(path):
    return json.load(open(path, encoding="utf-8")) if os.path.exists(path) else None


def fmt(x, nd=3):
    return "" if x is None else (round(x, nd) if isinstance(x, float) else x)


class Run:
    def __init__(self, cfg, run_tag="", force=()):
        self.cfg, self.tag, self.force = cfg, run_tag, set(force)
        d = cfg["data"]
        self.gold = [json.loads(line) for line in open(d["questions"], encoding="utf-8") if line.strip()]
        if d.get("limit"):
            self.gold = self.gold[:d["limit"]]
        self.stem = os.path.splitext(os.path.basename(d["questions"]))[0]
        self.dir = os.path.join(d["out_dir"], self.stem)
        os.makedirs(self.dir, exist_ok=True)
        self.eval_hash = sha(open(d["questions"], "rb").read())
        self._llms, self._searcher = {}, None
        self.computed = set()  # 이번 실행에서 새로 계산한 단계 (나머지는 캐시)
        emb, rr = cfg["embedding"], cfg["reranker"]
        self.E = emb.get("label") or safe(emb["model"])
        self.R = "none" if rr.get("kind") == "none" else (rr.get("label") or safe(rr["model"]))
        self.S = int(cfg["select"]["enabled"])
        rw = cfg["rewrite"]
        self.Lrw = (rw.get("label") or safe(os.path.splitext(os.path.basename(rw["file"]))[0])) if rw["source"] == "file" \
            else safe(self.llm_spec("rewrite")["model"]) + ("" if rw.get("prompt", "default") == "default" else f"_{rw['prompt']}")
        # 문자열 검색(드문 단어 글자 그대로 일치)을 후보에 더하면 후보·리랭크 캐시 이름을 따로 쓴다
        self.Kw = "_kw" if (cfg.get("keyword") or {}).get("enabled") else ""
        # 갈래별로 따로 검색해 합치는 방식(rewrite.fuse = "angles")은 후보·리랭크 캐시 이름을 따로 쓴다. Kw와 달리 문자열 검색을 켜지 않는다
        self.Af = "_af" if rw.get("fuse") == "angles" else ""
        self.Lsel = safe(self.llm_spec("select")["model"]) if self.S else None
        self.combo = f"{self.E}_{self.R}_{self.Lrw}_S{self.S}" + (f"_{self.Lsel}" if self.S and self.Lsel != self.Lrw else "")
        self.t2d = None
        self.rdir = self.dir

    # ---- 이름과 파일 ---------------------------------------------------
    def p(self, name):
        return os.path.join(self.dir, name)

    def llm_spec(self, role):
        llm = self.cfg.get("llm", {})
        spec = llm.get(role) or llm.get("default")
        if not spec:
            raise SystemExit(f"[llm.{role}] 또는 [llm.default]가 설정에 없습니다.")
        return spec

    def llm(self, role):
        spec = self.llm_spec(role)
        key = json.dumps(spec, sort_keys=True)
        if key not in self._llms:
            self._llms[key] = LLM(spec)
        return self._llms[key]

    def stop_llms(self):
        for m in self._llms.values():
            m.stop()

    def tagged(self, name, ext=".json"):
        return self.p(f"{name}{('_' + self.tag) if self.tag else ''}{ext}")

    def new_result_path(self, name):
        """결과 파일은 덮어쓰지 않는다: 이미 있으면 _2, _3 … 을 붙인다."""
        base = self.tagged(name, "")
        path, i = base + ".json", 2
        while os.path.exists(path):
            path, i = f"{base}_{i}.json", i + 1
        return path

    def searcher(self, embedder=None, reranker=None):
        if self._searcher is not None:
            self._searcher.close()
        self._searcher = ComboSearcher(embedder, reranker)
        s = self._searcher
        s.glossary_filter = True
        search_module.CITE_WHOLE_ARTICLE = True
        self.t2d = {v["title"]: d for d, v in s.documents.items()}
        self.node_of = {cid: M.node_key(r["doc_id"], r["article"]) for cid, r in s.row_info.items()}
        self.indexed_nodes = set(self.node_of.values())
        return s

    def gold_of(self, g):
        return M.gold_nodes(g, self.t2d, self.indexed_nodes)

    def stage_meta(self, extra=None):
        meta = {"combo": self.combo, "time": time.strftime("%Y-%m-%d %H:%M:%S"), "commit": env.git_state(), "eval_hash": self.eval_hash,
                "db": os.environ.get("PG_DATABASE"), "gpu_peak_gb": gpu_peak_gb(), "ram_peak_gb": env.ram_peak_gb()}
        meta.update(extra or {})
        return meta

    # ---- ① 바꿔 쓰기 ---------------------------------------------------
    def stage_rewrite(self):
        path = self.p(f"rewrite_{self.Lrw}.json")
        data = load_json(path) or {"meta": {}, "items": {}}
        todo = [g for g in self.gold if g["id"] not in data["items"] or "rewrite" in self.force]
        if not todo:
            print(f"① 바꿔 쓰기: 캐시 사용 ({os.path.basename(path)})", flush=True)
            return data
        self.computed.add("rewrite")
        rw = self.cfg["rewrite"]
        if rw["source"] == "file":  # 이미 만들어 둔 바꿔 쓰기(예: luna 캐시)를 기준선으로 쓴다. 시간은 알 수 없어 0으로 둔다
            src = json.load(open(rw["file"], encoding="utf-8"))
            for g in todo:
                v = src.get(g["q"])
                data["items"][g["id"]] = {"q": g["q"], "rewritten": v or None, "raw": v, "sec": 0.0, "tokens": {"in": 0, "out": 0},
                                          "source": "file", "error": None if v else "캐시에 없음"}
            data["meta"] = self.stage_meta({"source": "file", "file": rw["file"], "label": self.Lrw, "time_note": "캐시 파일이라 시간 없음"})
            save_json(path, data)
            return data
        kind = rw.get("prompt", "default")
        if kind in ("both", "both3"):  # 기존 바꿔 쓰기와 HyDE(both3: 다각도 HyDE) 결과를 이어 붙인다 (두 파일이 먼저 있어야 한다)
            base = self.Lrw[: -len("_" + kind)]
            a = load_json(self.p(f"rewrite_{base}.json"))["items"]
            b = load_json(self.p(f"rewrite_{base}_{'hyde3' if kind == 'both3' else 'hyde'}.json"))["items"]
            for g in todo:
                x, y = a[g["id"]], b[g["id"]]
                text = " ".join(t for t in (x.get("rewritten"), y.get("rewritten")) if t) or None
                data["items"][g["id"]] = {"q": g["q"], "rewritten": text, "raw": text, "sec": x["sec"] + y["sec"],
                                          "tokens": {k: x["tokens"][k] + y["tokens"][k] for k in ("in", "out")}, "error": x.get("error") or y.get("error")}
                if kind == "both3":  # fuse = "angles"일 때 쓴다: 검색어 한 줄과 갈래 목록을 따로 둔다
                    data["items"][g["id"]].update({"search": x.get("rewritten"), "angles": y.get("angles") or []})
            data["meta"] = self.stage_meta({"source": "both", "label": self.Lrw})
            save_json(path, data)
            return data
        prompt = ANGLE_HYDE_PROMPT if kind == "hyde3" else HYDE_PROMPT if kind == "hyde" else gen_module.REWRITE_PROMPT
        llm = self.llm("rewrite")
        times = []

        def one(g):
            t = time.perf_counter()
            raw, usage, err = None, {"in": 0, "out": 0}, None
            try:
                raw, usage = llm.ask(prompt, g["q"], usage=True)
            except Exception as e:  # noqa: BLE001
                err = repr(e)[:200]
            sec = time.perf_counter() - t
            if kind == "hyde3":  # 갈래 목록. rewritten은 갈래를 이어 붙인 글 (이어 붙여 쓰는 방식용)
                angles = parse_angles(raw)
                return g["id"], {"q": g["q"], "rewritten": " ".join(angles) or None, "angles": angles, "raw": raw, "sec": round(sec, 3),
                                 "tokens": usage, "error": err}
            lines = (raw or "").strip().strip('"').splitlines()
            rewritten = lines[0].strip() if lines and lines[0].strip() else None
            return g["id"], {"q": g["q"], "rewritten": rewritten, "raw": raw, "sec": round(sec, 3), "tokens": usage, "error": err}

        print(f"① 바꿔 쓰기: {len(todo)}개 ({llm.model})", flush=True)
        with ThreadPoolExecutor(rw.get("workers", 1)) as ex:
            for i, (gid, rec) in enumerate(ex.map(one, todo), 1):
                data["items"][gid] = rec
                times.append(rec["sec"])
                if i % 10 == 0:
                    save_json(path, data)
                    print(f"  {i}/{len(todo)}", flush=True)
        tok = {"in": sum(r["tokens"]["in"] for r in data["items"].values()), "out": sum(r["tokens"]["out"] for r in data["items"].values())}
        data["meta"] = self.stage_meta({"source": "llm", "label": self.Lrw, "model": llm.model, "base_url": llm.base_url,
                                        "first_call_sec": times[0] if times else None, "tokens": tok, "ollama_ps": env.ollama_ps()})
        save_json(path, data)
        return data

    # ---- ② 후보 --------------------------------------------------------
    def stage_cands(self, rew):
        path = self.p(f"cands_{self.E}_{self.Lrw}{self.Kw}{self.Af}.json")
        data = load_json(path)
        if data and "cands" not in self.force and all(g["id"] in data["items"] for g in self.gold):
            print(f"② 후보: 캐시 사용 ({os.path.basename(path)})", flush=True)
            return data
        self.computed.add("cands")
        self.stop_llms()
        free_gpu()
        reset_gpu_peak()
        print(f"② 후보: 임베딩 {self.cfg['embedding']['model']} 적재", flush=True)
        t = time.perf_counter()
        embedder = make_embedder(self.cfg["embedding"])
        load_sec = time.perf_counter() - t
        t = time.perf_counter()
        s = self.searcher(embedder, None)
        s.load_doc_vectors(self.dir, self.E)
        doc_sec = time.perf_counter() - t
        wv, wk = app_config.HYBRID_VECTOR_WEIGHT, app_config.HYBRID_KEYWORD_WEIGHT
        items = {}
        for n, g in enumerate(self.gold, 1):
            q = g["q"]
            rewritten = (rew["items"].get(g["id"]) or {}).get("rewritten")
            s.embed_time = 0.0
            t0 = time.perf_counter()
            exact = s.exact_hits(q)
            taken = {h["chunk_id"] for h in exact}
            t1 = time.perf_counter()
            vec = s.vector_hits(q, None)
            t2 = time.perf_counter()
            key = s.bm25_hits(q, None)
            t3 = time.perf_counter()
            item = rew["items"].get(g["id"]) or {}
            angles = item.get("angles") if self.Af else None
            # 갈래별 검색: 바꿔 쓴 글에서 가상 조문은 빼고(검색어 한 줄만) 갈래마다 따로 검색해 합친다. 갈래 가중치 합 = 바꿔 쓴 글 하나의 가중치
            hint = " ".join(x for x in (item.get("search") if angles else rewritten, synonyms.expand(q, s.synonym_table),
                                        glossary.signal_terms(q, s.glossary)) if x)
            extra = ()
            if hint:
                extra = (("벡터(바꿔쓴)", s.vector_hits(hint, None), wv), ("키워드(바꿔쓴)", s.bm25_hits(hint, None), wk))
            for i_, a_ in enumerate(angles or [], 1):
                extra += ((f"벡터(갈래{i_})", s.vector_hits(a_, None), wv / len(angles)), (f"키워드(갈래{i_})", s.bm25_hits(a_, None), wk / len(angles)))
            if self.Kw:
                extra += (("문자열", self.literal_hits(s, q), wk),)
            fused = [h for h in s.fuse(vec, key, wv, wk, extra) if h["chunk_id"] not in taken]
            window = s.rerank_window(fused)
            total = time.perf_counter() - t0
            orig = [h for h in s.fuse(vec, key, wv, wk) if h["chunk_id"] not in taken]  # 바꿔 쓰기 효과를 보려는 원질문만 후보
            items[g["id"]] = {"q": q, "hint": hint, "exact": [h["chunk_id"] for h in exact],
                              "fused50": [h["chunk_id"] for h in fused[:50]],
                              "window": [{"cid": h["chunk_id"], "rrf": h.get("score")} for h in window],
                              "vec30": [h["chunk_id"] for h in vec[:30]], "orig30": [h["chunk_id"] for h in orig[:30]],
                              "sec": {"exact": round(t1 - t0, 4), "embed_query": round(s.embed_time, 4), "vector": round(t2 - t1, 4),
                                      "bm25": round(t3 - t2, 4), "total": round(total, 4)}}
            if n % 25 == 0:
                print(f"  {n}/{len(self.gold)}", flush=True)
        data = {"meta": self.stage_meta({"embedding": self.cfg["embedding"]["model"], "load_sec": round(load_sec, 2),
                                         "doc_vectors_sec": round(doc_sec, 2), "label": self.E}), "items": items}
        save_json(path, data)
        embedder.close()
        s.embedder = None
        free_gpu()
        return data

    def literal_hits(self, s, q):
        """문자열 검색: 질문의 낱말 중 규정 전체에서 드물게 나오는(청크 max_df개 이하) 것을 글자 그대로 가진 청크.
        BM25는 형태소로 쪼개서 "선공제포인트" 같은 합성어가 흐려질 수 있어 따로 본다. 흔한 말("휴가")은 수백 청크에 걸려 쓰지 않는다."""
        kcfg = self.cfg.get("keyword") or {}
        if not hasattr(self, "_lit"):
            self._lit = {cid: re.sub(r"\s+", "", r.get("body_search") or "") for cid, r in s.row_info.items()}
        terms = set()
        for w in re.findall(r"[가-힣A-Za-z0-9·ㆍ]{2,}", q):
            w = re.sub(r"(은|는|이|가|을|를|에|에서|의|로|으로|도|만|까지|부터|와|과|하고|이나|나|요|예요|이에요|인가요|하나요|해요)$", "", w)
            if len(w) >= kcfg.get("min_len", 3):
                terms.add(w)
        score = {}
        for t in terms:
            hit = [cid for cid, txt in self._lit.items() if t in txt]
            if 0 < len(hit) <= kcfg.get("max_df", 10):
                w = 1.0 / len(hit)
                for cid in hit:
                    score[cid] = score.get(cid, 0.0) + w
        return [{"chunk_id": cid, **s.row_info[cid]} for cid, _ in sorted(score.items(), key=lambda x: -x[1])[:30]]

    # ---- ③ 리랭크 ------------------------------------------------------
    def stage_rerank(self, cands, rew=None):
        # reranker.query: 리랭커에 넣는 질문. q = 원질문(기본), rewritten = 바꿔 쓴 검색어, both = 둘을 이어 붙임
        mode = self.cfg["reranker"].get("query", "q")
        path = self.p(f"rerank_{self.E}_{self.Lrw}{self.Kw}{self.Af}_{self.R}{'' if mode == 'q' else '_q-' + mode}.json")
        data = load_json(path)
        if data and "rerank" not in self.force and all(g["id"] in data["items"] for g in self.gold):
            print(f"③ 리랭크: 캐시 사용 ({os.path.basename(path)})", flush=True)
            return data
        self.computed.add("rerank")
        self.stop_llms()
        free_gpu()
        reset_gpu_peak()
        rcfg = self.cfg["reranker"]
        t = time.perf_counter()
        reranker = make_reranker(rcfg)
        load_sec = time.perf_counter() - t
        print(f"③ 리랭크: {self.R} (적재 {load_sec:.1f}초)", flush=True)
        s = self.searcher(None, reranker)
        items = {}
        for n, g in enumerate(self.gold, 1):
            c = cands["items"][g["id"]]
            cd = [{**s.row_info[w["cid"]], "chunk_id": w["cid"], "score": w["rrf"]} for w in c["window"]]
            t0 = time.perf_counter()
            rewritten = ((rew or {}).get("items", {}).get(g["id"]) or {}).get("rewritten")
            query = {"q": g["q"], "rewritten": rewritten or g["q"], "both": f"{g['q']} {rewritten}" if rewritten else g["q"]}[mode]
            ranked = s.rerank(query, cd)
            sec = time.perf_counter() - t0
            items[g["id"]] = {"order": [h["chunk_id"] for h in ranked],
                              "scores": {h["chunk_id"]: h.get("rerank") for h in ranked} if reranker is not None else None,
                              "sec": round(sec, 4)}
            if n % 25 == 0:
                print(f"  {n}/{len(self.gold)} (질문당 {statistics.mean(i['sec'] for i in items.values()):.2f}초)", flush=True)
        offload = None
        if reranker is not None and hasattr(getattr(reranker, "m", None), "hf_device_map"):
            dm = reranker.m.hf_device_map
            offload = {"cpu_modules": sum(1 for v in dm.values() if v in ("cpu", "disk")), "total_modules": len(dm),
                       "gpu_mem": rcfg.get("gpu_mem"), "cpu_mem": rcfg.get("cpu_mem")}
        data = {"meta": self.stage_meta({"reranker": rcfg.get("model"), "kind": rcfg.get("kind"), "load_sec": round(load_sec, 2),
                                         "fp16": rcfg.get("fp16"), "offload": offload, "label": self.R}), "items": items}
        save_json(path, data)
        if reranker is not None:
            reranker.close()
        s.reranker = None
        free_gpu()
        return data

    # ---- ③' 온톨로지 그래프 검색 후보 리랭크 (onto.mode = "A") ----------
    def onto(self, rew=None):
        if not hasattr(self, "_onto"):
            from src.combo.onto_search import OntoSearch
            ocfg = self.cfg.get("onto") or {}
            rewrites = {k: v.get("rewritten") for k, v in ((rew or {}).get("items") or {}).items()}
            self._onto = OntoSearch(ocfg["dir"], self.gold, self.stem, ocfg.get("source", "understand"), rewrites,
                                    ocfg.get("top_stmt", 20), ocfg.get("max_df", 10), ocfg.get("stmt_title", False),
                                    300 if ocfg.get("rerank_arts") else 0) if ocfg.get("enabled") else None
        return self._onto

    def stage_onto_rerank(self, cands, rew):
        """그래프가 찾은 조의 청크 중 리랭커 후보(window)에 없던 것을 같은 리랭커·같은 질문으로 점수 매긴다."""
        mode = self.cfg["reranker"].get("query", "q")
        ocfg = self.cfg.get("onto") or {}
        cap = ocfg.get("rerank_arts") or 0   # 0이면 규정문 검색이 고른 조(top_stmt개 규정문) 전부, 아니면 가까운 순 앞 cap개 조
        # 규정문 글이 달라지면(stmt_title) 같은 질문도 다른 조가 골라지므로 이름에 넣는다
        path = self.p(f"rerank_onto_{self.E}_{self.Lrw}_{self.R}{'' if mode == 'q' else '_q-' + mode}"
                      f"{f'_a{cap}' if cap else ''}{'_t' if ocfg.get('stmt_title') else ''}.json")
        data = load_json(path) or {"items": {}}
        todo = [g for g in self.gold if g["id"] not in data["items"]]
        if not todo:
            print(f"③' 그래프 후보 리랭크: 캐시 사용 ({os.path.basename(path)})", flush=True)
            return data
        free_gpu()
        reranker = make_reranker(self.cfg["reranker"])
        s = self.searcher(None, reranker)
        cids_of = {}
        for cid, n in self.node_of.items():
            cids_of.setdefault(n, []).append(cid)
        for g in todo:
            win = {w["cid"] for w in cands["items"][g["id"]]["window"]}
            cand_nodes = self.onto().deep_nodes(g["id"], cap) if cap else self.onto().nodes(g["id"])
            extra = [cid for n in cand_nodes for cid in cids_of.get(n, []) if cid not in win]
            rewritten = ((rew or {}).get("items", {}).get(g["id"]) or {}).get("rewritten")
            query = {"q": g["q"], "rewritten": rewritten or g["q"], "both": f"{g['q']} {rewritten}" if rewritten else g["q"]}[mode]
            ranked = s.rerank(query, [{**s.row_info[cid], "chunk_id": cid, "score": None} for cid in extra]) if extra else []
            data["items"][g["id"]] = {h["chunk_id"]: h.get("rerank") for h in ranked}
        save_json(path, data)
        reranker.close()
        s.reranker = None
        free_gpu()
        return data

    # ---- ④⑤ 선택 + 최종 근거 + 지표 ------------------------------------
    def ranked_of(self, s, g, cands, rr, onto_rr=None):
        c, r = cands["items"][g["id"]], rr["items"][g["id"]]
        rrf = {w["cid"]: w["rrf"] for w in c["window"]}
        scores = r["scores"] or {}
        ranked = [{**s.row_info[cid], "chunk_id": cid, "score": rrf.get(cid), "rerank": scores.get(cid)} for cid in r["order"]]
        if onto_rr:  # A: 그래프 후보를 리랭크 점수로 같은 줄에 끼워 넣는다
            ranked += [{**s.row_info[cid], "chunk_id": cid, "score": None, "rerank": v, "via": "그래프"} for cid, v in onto_rr[g["id"]].items()]
            ranked.sort(key=lambda h: -(h["rerank"] if h["rerank"] is not None else float("-inf")))
        ranked, excluded = glossary.filter_candidates(g["q"], ranked, s.glossary)
        s.last_glossary_excluded = excluded
        return group_by_article(ranked)

    def select_one(self, s, llm, q, articles, k):
        """리랭크 상위 pool개 조에서 LLM이 k개를 고른다 (운영 코드 Searcher._llm_select와 같은 프롬프트·파싱). 이상 출력을 센다."""
        cands = articles[:self.cfg["select"]["pool"]]
        if len(cands) <= k:
            return {"chosen": [h["chunk_id"] for h in cands], "raw": None, "sec": 0.0, "tokens": {"in": 0, "out": 0},
                    "anom": {"skipped": True}}
        lines = []
        for i, h in enumerate(cands, 1):
            r = s.row_info[h["chunk_id"]]
            title = s.documents.get(r["doc_id"], {}).get("title") or r["doc_id"]
            lines.append(f"[{i}] {title} {r['article']} | " + re.sub(r"\s+", " ", r["body_search"] or "")[:220])
        prompt = search_module.SELECT_PROMPT.format(k=k, q=q, cands="\n".join(lines))
        t = time.perf_counter()
        raw, usage, err = None, {"in": 0, "out": 0}, None
        try:
            raw, usage = llm.complete(prompt, usage=True)
        except Exception as e:  # noqa: BLE001
            err = repr(e)[:200]
        sec = time.perf_counter() - t
        anom = {"parse_fail": False, "out_of_range": 0, "duplicate": 0, "count_violation": False, "error": err}
        ids = []
        try:
            arr = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))["순서"]
            for x in arr:
                try:
                    ids.append(int(x))
                except (TypeError, ValueError):
                    anom["out_of_range"] += 1
        except Exception:  # noqa: BLE001
            anom["parse_fail"] = True
        chosen, seen = [], set()
        for i in ids:
            if not 1 <= i <= len(cands):
                anom["out_of_range"] += 1
            elif i in seen:
                anom["duplicate"] += 1
            else:
                seen.add(i)
                chosen.append(cands[i - 1])
        anom["count_violation"] = len(chosen) != k
        chosen = chosen[:k]
        picked = {c["chunk_id"] for c in chosen}
        chosen += [h for h in cands if h["chunk_id"] not in picked][:k - len(chosen)]
        return {"chosen": [h["chunk_id"] for h in chosen], "raw": raw, "sec": round(sec, 3), "tokens": usage, "anom": anom}

    def stage_select(self, cands, rr):
        """⑤ 선택 켬: 질문마다, 설정한 k마다 LLM 선택 결과를 select_<조합>.json에 저장한다."""
        path = self.tagged(f"select_{self.combo}")
        data = load_json(path) or {"meta": {}, "items": {}}
        ks = self.cfg["select"]["ks"]
        todo = [g for g in self.gold if any(str(k) not in data["items"].get(g["id"], {}) for k in ks)]
        if not todo:
            print(f"⑤ 선택: 캐시 사용 ({os.path.basename(path)})", flush=True)
            return data
        self.computed.add("select")
        s = self.searcher(None, None)
        llm = self.llm("select")
        print(f"⑤ 선택: {len(todo)}문항 × k={ks} ({llm.model})", flush=True)
        first = None

        arts_cache = {}
        for g in todo:  # ranked_of는 DB·용어집을 쓰므로 한 스레드에서 먼저 만들어 둔다
            arts_cache[g["id"]] = self.ranked_of(s, g, cands, rr)

        def work(g):
            return g["id"], {str(k): self.select_one(s, llm, g["q"], arts_cache[g["id"]], k) for k in ks}

        with ThreadPoolExecutor(self.cfg["select"].get("workers", 1)) as ex:
            for i, (gid, rec) in enumerate(ex.map(work, todo), 1):
                data["items"].setdefault(gid, {}).update(rec)
                if first is None:
                    first = max(v["sec"] for v in rec.values())
                if i % 10 == 0:
                    save_json(path, data)
                    print(f"  {i}/{len(todo)}", flush=True)
        tok = {"in": sum(v["tokens"]["in"] for it in data["items"].values() for v in it.values()),
               "out": sum(v["tokens"]["out"] for it in data["items"].values() for v in it.values())}
        data["meta"] = self.stage_meta({"model": llm.model, "base_url": llm.base_url, "pool": self.cfg["select"]["pool"], "ks": ks,
                                        "first_call_sec": first, "tokens": tok, "ollama_ps": env.ollama_ps(),
                                        "prompt_hash": sha(search_module.SELECT_PROMPT)})
        save_json(path, data)
        return data

    def budget_assemble(self, s, g, rewritten, results, graph_nodes, bcfg, kinds=None, support=None, cited=None):
        """그래프로 찾은 조를 예산 안에 넣는다 ([budget] 설정).
        total  전체 글자 예산 (기본 config.CONTEXT_CHAR_BUDGET)
        share  그래프 조 몫. 검색 결과·인용은 total - share 안에서 먼저 조립하고(넘치면 긴 조부터 깎임), 남은 자리에 그래프 조를 넣는다
        order  "rel": 질문과 임베딩이 가까운 순, "id": 예전처럼 들어온 순서, "kind": 선 종류 우선,
               "links": 입력 속 여러 조가 함께 가리키는 후보 먼저(규정문 검색이 골랐으면 1 더함), 같으면 질문과 가까운 순
        shorten 긴 그래프 조는 남은 자리만큼 앞에서부터 줄여 넣는다 (500자보다 적게는 줄이지 않는다)
        규정 조와 법령 조(LAW:)를 같은 줄에 세운다. 돌려주는 값은 assemble 결과에 extra_nodes(그래프 몫으로 넣은 조)를 더한 것."""
        from src.combo.graph_answer import graph_entries
        from src.combo.onto_search import embed
        kinds = kinds or {}
        total = bcfg.get("total", app_config.CONTEXT_CHAR_BUDGET)
        share = min(bcfg.get("share", 0), total)
        # 형제항(같은 조의 다른 항)은 그래프 조 뒤에 둔다 (예전 조립 순서: 검색 -> 인용 -> 주제 -> 그래프 -> 형제항)
        prim = s.assemble([{**r, "siblings": []} for r in results], budget=total - share)
        used = prim["chars"]
        have = {M.node_key(it["doc_id"], it["article"]) for grp in prim["groups"] for it in grp["items"]}
        # cited_first: 1차 조립(검색 결과 + 인용)이 예산에 차서 잘린 인용 조는 "이미 들어간 것"으로 취급돼 그래프 후보에서도 빠진다.
        # 직접 인용은 가장 강한 연결이므로 그래프 후보보다 먼저, 인용한 결과가 많은 순으로 남은 자리에 넣는다.
        lead = [n for n in sorted(cited or {}, key=lambda n: (-cited[n], n)) if n not in have] if bcfg.get("cited_first") else []
        cands = [n for n in dict.fromkeys(lead + list(graph_nodes)) if n not in have]
        texts = {}
        for n in cands:
            if n.startswith(graph_expand.LAW):
                doc, key = n[len(graph_expand.LAW):].rsplit(":", 1)
                texts[n] = self.law_text().get((doc, key), "")
            else:
                e = graph_entries(s, [n])
                texts[n] = e[0]["body"] if e else ""
        cands = [n for n in cands if texts[n]]
        head = [n for n in cands if n in set(lead)]   # 인용 조는 아래 순서 정렬에서 빼고 맨 앞에 둔다
        cands = [n for n in cands if n not in set(lead)]
        if bcfg.get("order", "rel") in ("rel", "kind", "links") and cands:
            q = embed([app_config.EMBED_QUERY_INSTRUCT + " ".join(x for x in (g["q"], rewritten) if x)])[0]
            V = embed([texts[n][:1500] for n in cands])
            sim = dict(zip(cands, (V @ q).tolist()))
            if bcfg.get("order") == "kind":
                # 선 종류가 먼저: 인용·별표로 직접 딸린 조(별표·정의 조는 질문과 문장이 안 닮아 유사도가 낮다) > 온톨로지 조 > 위임 > 이웃 조 > 법령
                rank = {"CITES": 0, "CITES_ANNEX": 0, "온톨로지": 1, "DELEGATES_TO": 2, "ONTO_DELEG": 2, "SAME_REG_NEAR": 3}
                tier = lambda n: 4 if n.startswith(graph_expand.LAW) else min((rank.get(k, 3) for k in kinds.get(n, ())), default=3)  # noqa: E731
                cands = sorted(cands, key=lambda n: (tier(n), -sim[n]))
            elif bcfg.get("order") == "links":
                # 짧은 별표·정의·주요조직 조는 질문과 문장이 안 닮아 유사도에서 밀리지만, 입력 속 여러 조가 가리키면 함께 읽어야 할 가능성이 크다
                cands = sorted(cands, key=lambda n: (-(support or {}).get(n, 0), -sim[n]))
            else:
                cands = sorted(cands, key=lambda n: -sim[n])
        cands = head + cands
        if bcfg.get("def_last"):
            # 정의 연결(DEF_OF)로만 닿은 조는 맨 뒤: 남는 자리에만 들어가고 다른 후보를 밀어내지 못한다
            tail = [n for n in cands if (kinds or {}).get(n) == {"DEF_OF"} and n not in set(head)]
            cands = [n for n in cands if n not in set(tail)] + tail
        extra = []
        for n in cands:
            room = total - used
            if room < search_module.MIN_SHORTENED:
                break
            t = texts[n]
            if len(t) > room:
                if not bcfg.get("shorten", True):
                    continue
                t = search_module.shorten_article(t, room - len(search_module.SHORTENED_NOTE))
                if len(t) > room:
                    continue
            used += len(t)
            extra.append(n)
        seen = {it["chunk_id"] for grp in prim["groups"] for it in grp["items"]}
        for r in results:  # 남은 자리에 형제항
            for sib in r.get("siblings", []):
                if sib["chunk_id"] not in seen and used + len(sib["body"]) <= total:
                    seen.add(sib["chunk_id"])
                    used += len(sib["body"])
        return {**prim, "chars": used, "extra_nodes": extra}

    def law_text(self):
        if not hasattr(self, "_law_text"):
            sys.path.insert(0, "scripts")
            from graph_law import law_texts
            self._law_text = law_texts()[0]
        return self._law_text

    def stage_final(self, cands, rr, sel, rew):
        """④ 선택 끔 / ⑤ 선택 켬 뒤 최종 근거를 만들고 질문별 지표를 계산해 results_<조합>.json에 저장한다."""
        s = self.searcher(None, None)
        rcfg = self.cfg["eval"]
        rks, fks = rcfg["rerank_ks"], rcfg["final_ks"]
        titles = [d["title"] for d in s.documents.values() if d.get("title")]
        gcfg = self.cfg.get("graph") or {}
        edges = graph_expand.load_edges(gcfg.get("uri", graph_expand.DEFAULT_URI)) if gcfg.get("enabled") else []
        if gcfg.get("deleg_file"):  # 온톨로지 위임 연결 (파일). deleg_sources로 출처(모델·규칙·문서이름)를 고른다
            srcs = set(gcfg.get("deleg_sources") or ["모델", "규칙", "문서이름"])
            edges += [(r["src"], r["dst"], "ONTO_DELEG") for r in json.load(open(gcfg["deleg_file"], encoding="utf-8"))
                      if r.get("출처", "모델") in srcs]
        if gcfg.get("def_file"):  # 정의 연결: 규정의 각 조 -> 그 규정의 정의·용어 조 (scripts/onto_def_edges.py)
            edges += [(r["src"], r["dst"], r["kind"]) for r in json.load(open(gcfg["def_file"], encoding="utf-8"))]
        if gcfg.get("same_file"):  # 같은 제도·다른 면 조 간 선 (scripts/onto_same_edges.py)
            edges += [(r["src"], r["dst"], "ONTO_SAME") for r in json.load(open(gcfg["same_file"], encoding="utf-8"))]
        if gcfg.get("law_file"):  # 법령 층 선 (scripts/graph_law_link.py)
            edges += [(r["src"], r["dst"], r["kind"]) for r in json.load(open(gcfg["law_file"], encoding="utf-8"))]
        gx = graph_expand.Expander(edges, self.indexed_nodes, gcfg.get("policy", {})) if gcfg.get("enabled") else None
        from src.combo.answer import with_graph
        from src.combo.graph_answer import graph_entries
        ocfg = self.cfg.get("onto") or {}
        omode = ocfg.get("mode") if ocfg.get("enabled") else None
        bcfg = self.cfg.get("budget") or {}
        if omode:
            self.onto(rew)  # 바꿔 쓴 질문을 규정문 검색에 넘기려고 여기서 먼저 만든다
        # mode A, 또는 B에 onto.rerank_arts를 더하면 규정문 검색 조의 청크를 리랭커 후보에 넣어 본 결과(상위 k)부터 달라진다
        onto_rr = self.stage_onto_rerank(cands, rew)["items"] if omode == "A" or (omode and ocfg.get("rerank_arts")) else None
        hop2 = gcfg.get("hop2") or []
        gx2 = graph_expand.Expander(graph_expand.load_edges(gcfg.get("uri", graph_expand.DEFAULT_URI)), self.indexed_nodes,
                                    {k_: "both" for k_ in hop2}) if gx and hop2 else None
        s = self.searcher(None, None)
        qrecs = []
        for n, g in enumerate(self.gold, 1):
            gn = self.gold_of(g)
            core, aux = gn["core"], gn["aux"]
            c, r = cands["items"][g["id"]], rr["items"][g["id"]]
            rw = rew["items"].get(g["id"]) or {}
            nodes_of = lambda cids: M.first_seen([self.node_of[x] for x in cids])  # noqa: E731
            # 후보 단계: 상위 N개 항 안의 노드 (정확 조회는 후보가 아니라서 뺀다)
            fused_nodes = {N: set(nodes_of(c["fused50"][:N])) for N in (10, 30, 50)}
            win_nodes = set(nodes_of([w["cid"] for w in c["window"]]))
            cand_core = {f"@{N}": len(core & fused_nodes[N]) for N in (10, 30, 50)}
            cand_core["window30"] = len(core & win_nodes)
            cand_core["vec30"] = len(core & set(nodes_of(c["vec30"])))
            cand_core["orig30"] = len(core & set(nodes_of(c["orig30"])))
            exact = s.exact_hits(g["q"])
            exact_nodes = M.first_seen([M.node_key(h["doc_id"], h["article"]) for h in exact])
            arts = self.ranked_of(s, g, cands, rr, onto_rr)
            order = M.first_seen(exact_nodes + [self.node_of[h["chunk_id"]] for h in arts])
            onto_nodes = self.onto().nodes(g["id"]) if omode else []
            rq = {"core": {f"@{k}": M.hits_at(order, core, k) for k in rks}, "aux": {f"@{k}": M.hits_at(order, aux, k) for k in rks},
                  "hit1_core": bool(order and order[0] in core) if core else None,
                  "hit1_any": bool(order and order[0] in (core | aux)) if (core | aux) else None,
                  "rr_core": M.rr(order, core) if core else None, "ndcg8": M.ndcg(order, core, aux, 8)}
            fin, sels = {}, {}
            for k in fks:
                if self.S:
                    sr = sel["items"][g["id"]][str(k)]
                    byid = {h["chunk_id"]: h for h in arts}
                    pick = [byid[cid] for cid in sr["chosen"] if cid in byid]
                    sel_nodes = M.first_seen(exact_nodes + [self.node_of[h["chunk_id"]] for h in pick])
                    base_nodes = set(order[:k])
                    sels[str(k)] = {"nodes": sel_nodes[:k], "core": M.hits_at(sel_nodes, core, k),
                                    "aux": M.hits_at(sel_nodes, aux, k), "saved": len((core & set(sel_nodes[:k])) - base_nodes),
                                    "dropped": len((core & base_nodes) - set(sel_nodes[:k])), "anom": sr["anom"], "sec": sr["sec"],
                                    "tokens": sr["tokens"]}
                else:
                    pick = arts[:k]
                t0 = time.perf_counter()
                results = s._as_articles(exact + pick, k, True)
                onto_added = []
                have = {M.node_key(it["doc_id"], it["article"]) for it in results} | \
                       {M.node_key(c["doc_id"], c["article"]) for it in results for c in it.get("cited", [])}
                if omode == "B":  # 하이브리드 결과에 없던 그래프 조를 max개까지
                    onto_added = [n for n in onto_nodes if n not in have][:ocfg.get("max", 5)]
                    if ocfg.get("place", "related") == "primary":  # 검색 결과 뒤에 붙인다 (예산이 넘치면 긴 조부터 함께 깎인다)
                        results = results + graph_entries(s, onto_added)
                asm = s.assemble(results)
                expand = time.perf_counter() - t0
                final_nodes = {M.node_key(it["doc_id"], it["article"]) for grp in asm["groups"] for it in grp["items"]}
                graph_added = gx.expand(final_nodes, set(order[:gcfg.get("near_k", 3)])) if gx else set()  # 그래프로 더한 조: 글자 수·조 수는 더하기 전 값이다
                got = final_nodes | graph_added
                # 실제 답변 입력: 답변 단계(with_graph)처럼 구조선으로 더한 조를 related로 붙여 글자 예산 안에서 조립한 것
                struct = []
                if (omode == "B" and ocfg.get("place", "related") == "related") or bcfg.get("enabled"):  # 예산 조립은 온톨로지를 꺼도 쓴다 (대조군)
                    # 그래프 조도 구조선 확장의 시작점이 된다. 예산 순서: 검색 결과 -> 인용 -> 구조선 조 -> 그래프 조
                    near = set(order[:gcfg.get("near_k", 3)]) | set(onto_added)
                    struct = sorted(gx.expand(have | set(onto_added), near) - set(onto_added)) if gx else []
                    if gx2:  # 두 번째 건너기: 첫 번째로 닿은 조에서 정해 둔 선(별표·위임)만 한 번 더 탄다
                        struct += sorted(gx2.expand(set(struct)) - have - set(onto_added) - set(struct))
                    if bcfg.get("enabled"):
                        kinds = dict(gx.expand_detail(have | set(onto_added), near)) if gx else {}
                        support = {n_: len(v_) for n_, v_ in gx.expand_support(have | set(onto_added), near).items()} if gx else {}
                        for node_ in onto_added:  # n은 바깥 문항 번호라 쓰지 않는다
                            kinds.setdefault(node_, set()).add("온톨로지")
                            support[node_] = support.get(node_, 0) + 1   # 규정문 검색이 고른 것도 하나의 근거다
                        cited_cnt = {}
                        for it_ in results:
                            for c_ in it_.get("cited", []):
                                key_ = M.node_key(c_["doc_id"], c_["article"])
                                cited_cnt[key_] = cited_cnt.get(key_, 0) + 1
                        inp = self.budget_assemble(s, g, rw.get("rewritten"), results, onto_added + [n for n in struct if n not in onto_added],
                                                   bcfg, kinds, support, cited_cnt)
                    else:
                        rin = [{**r} for r in results]
                        rin[0]["related"] = list(rin[0].get("related") or []) + graph_entries(s, struct) + graph_entries(s, onto_added)
                        inp = s.assemble(rin)
                else:
                    inp = s.assemble(with_graph(s, gx, [{**r} for r in results], order, gcfg.get("near_k", 3)))
                input_nodes = {M.node_key(it["doc_id"], it["article"]) for grp in inp["groups"] for it in grp["items"]}
                input_nodes |= set(inp.get("extra_nodes", ()))  # budget_assemble이 그래프 몫으로 넣은 조 (규정 조, 법령 조 LAW:)
                input_nodes_law = {n for n in input_nodes if n.startswith(graph_expand.LAW)}
                input_nodes -= input_nodes_law
                fin[str(k)] = {"nodes": sorted(final_nodes), "core": len(core & got), "aux": len(aux & got),
                               "all_core": bool(core) and core <= got, "chars": asm["chars"], "n_articles": len(asm["groups"]),
                               "sec_expand": round(expand, 4),
                               "input_core": len(core & input_nodes), "input_all_core": bool(core) and core <= input_nodes,
                               "input_chars": inp["chars"], "input_articles": len(inp["groups"]) + len(inp.get("extra_nodes", ())),
                               "input_nodes": sorted(input_nodes), "input_law": sorted(input_nodes_law)}
                if gx:
                    fin[str(k)]["graph_added"] = sorted(graph_added)
                if (omode == "B" and ocfg.get("place", "related") == "related") or bcfg.get("enabled"):  # 예산 조립은 온톨로지를 꺼도 쓴다 (대조군)
                    fin[str(k)]["struct_added"] = struct  # 그래프 조까지 시작점으로 삼아 구조선으로 더한 조 (예산 전)
                if gx and gcfg.get("law_file"):
                    # 법령: 찾은 조에서 법령 선(CITES_LAW, LAW_PICK)으로 간 법령 조 + 거기서 같은 법 안 참조(LAW_CITES) 한 번 더.
                    # 입력 쪽은 규정 조가 인용해 답변 입력에 같이 들어간 법령 조 (chunk_id "eflaw:000130_19")
                    # 구조선 확장이 실제로 더한 법령 조(한 번 건넘)만 센다. 법령 안 참조(LAW_CITES)가 켜져 있으면 거기서 한 번 더
                    law_found = {n for n in graph_added | set(struct) if n.startswith(graph_expand.LAW)}
                    law_found |= {n for n in gx.expand(law_found) if n.startswith(graph_expand.LAW)}
                    law_input = {f"{graph_expand.LAW}{it['chunk_id'].rsplit('_', 1)[0]}:{it['chunk_id'].rsplit('_', 1)[1]}"
                                 for grp in inp["groups"] for it in grp["items"] if str(it.get("chunk_id", "")).startswith("eflaw:")}
                    law_input |= input_nodes_law
                    law_gold = {f"{graph_expand.LAW}{e['법령ID']}:" + re.sub(r"^제(\d+)조(의\d+)?$", lambda m_: m_[1] + (m_[2] or ""), e["조항"])
                                for e in g.get("법령근거") or [] if e.get("tier", "필수") == "필수"}
                    fin[str(k)]["law"] = {"gold": sorted(law_gold), "found": sorted(law_gold & (law_found | law_input)),
                                          "input": sorted(law_gold & law_input), "n_found": len(law_found | law_input)}
                if omode:
                    picked = {self.node_of[h["chunk_id"]] for h in pick}
                    fin[str(k)]["onto"] = {"found": sorted(onto_nodes), "core_found": sorted(core & set(onto_nodes)),
                                           "picked_via_graph": sorted({self.node_of[h["chunk_id"]] for h in pick if h.get("via")}),
                                           "added": onto_added, "core_input": sorted(core & set(onto_nodes) & input_nodes),
                                           "core_in_pick": sorted(core & set(onto_nodes) & picked)}
            sec = {"rewrite": rw.get("sec", 0.0), "cands": c["sec"]["total"], "rerank": r["sec"]}
            tot = {}
            for k in fks:
                sec_sel = sels[str(k)]["sec"] if self.S else 0.0
                tot[str(k)] = round(sec["rewrite"] + sec["cands"] + sec["rerank"] + sec_sel + fin[str(k)]["sec_expand"], 4)
            rewritten = rw.get("rewritten")
            qrecs.append({
                "id": g["id"], "유형": g.get("유형"), "난이도": g.get("난이도"), "질문자": g.get("질문자"),
                "combo": {"E": self.E, "R": self.R, "L": self.Lrw, "S": self.S}, "q": g["q"], "rewritten": rewritten,
                "rewrite_check": self.check_rewrite(rw, titles),
                "n_core": len(core), "n_aux": len(aux), "excluded": gn["excluded"],
                "cand": cand_core, "cand_window": [{"rank": i, **w} for i, w in enumerate(c["window"], 1)],
                "rerank": rq, "rerank_order": [{"cid": cid, "score": (r["scores"] or {}).get(cid)} for cid in r["order"]],
                "select": sels, "final": fin,
                "stage_nodes": {"cand30": sorted(core & win_nodes), "rerank8": sorted(core & set(order[:8])), "rerank16": sorted(core & set(order[:16])),
                                "sel8": sorted(core & set(sels["8"]["nodes"])) if "8" in sels else None,
                                "final8": sorted(core & set(fin["8"]["nodes"])) if "8" in fin else None, "core": sorted(core), "aux": sorted(aux)},
                "sec": sec, "sec_select": {k: sels[k]["sec"] for k in sels}, "sec_search_total": tot,
                "cache": {"rewrite": "rewrite" not in self.computed, "cands": "cands" not in self.computed,
                          "rerank": "rerank" not in self.computed, "select": ("select" not in self.computed) if self.S else None},
                "tokens": {"rewrite": rw.get("tokens"), "select": {k: sels[k]["tokens"] for k in sels}},
                "error": rw.get("error")})
            if n % 25 == 0:
                print(f"  {n}/{len(self.gold)}", flush=True)
        agg = self.aggregate(qrecs, cands, rr, sel, rew)
        out = {"meta": self.stage_meta({"combo": self.combo, "E": self.E, "R": self.R, "L": self.Lrw, "S": self.S,
                                        "config_hash": sha(json.dumps({k: v for k, v in self.cfg.items()}, sort_keys=True, default=str)),
                                        "prompt_hash": self.prompt_hash(), "run_tag": self.tag,
                                        # 해시만으로는 설정을 되살릴 수 없어 설정 전체와 온톨로지 추출 프롬프트 해시를 같이 남긴다
                                        "cfg": json.loads(json.dumps(self.cfg, default=str)), "onto_extract": self.onto_extract_meta()}),
               "agg": agg, "questions": qrecs}
        path = self.new_result_path(f"results_{self.combo}")
        save_json(path, out)
        print(f"결과 저장: {path}", flush=True)
        self.write_summary(out, path)
        return out

    @staticmethod
    def prompt_hash():
        return sha(gen_module.REWRITE_PROMPT, gen_module.SYSTEM_PROMPT, gen_module.VERIFY_PROMPT, gen_module.CONFLICT_PROMPT,
                   search_module.SELECT_PROMPT, getattr(gen_module, "HIT_NOTE", ""), HYDE_PROMPT, ANGLE_HYDE_PROMPT)

    def onto_extract_meta(self):
        """[onto] dir의 extract_meta.json (추출 모델·프롬프트 해시). 없으면 None."""
        d = (self.cfg.get("onto") or {}).get("dir")
        p = os.path.join(d, "extract_meta.json") if d else None
        return load_json(p) if p else None

    @staticmethod
    def check_rewrite(rw, titles):
        """바꿔 쓰기 출력 점검: 빈 출력, 형식 위반(여러 줄·너무 긺·JSON/코드블록), 없는 규정명·조 번호 포함."""
        raw, text = rw.get("raw"), rw.get("rewritten")
        if not text:
            return {"empty": True, "format": False, "invented": False}
        lines = [x for x in (raw or "").strip().splitlines() if x.strip()]
        fmt_bad = len(lines) > 1 or len(text) > 200 or "{" in text or "```" in (raw or "")
        art = bool(re.search(r"제\s*\d+\s*조", text))
        names = re.findall(r"([가-힣A-Za-z0-9·\s]{2,20}?(?:규정|지침|규칙|세칙))", text)
        unknown = any(not any(n.strip().replace(" ", "") in t.replace(" ", "") for t in titles) for n in names)
        return {"empty": False, "format": fmt_bad, "invented": art or unknown}

    def aggregate(self, qrecs, cands, rr, sel, rew):
        rks, fks = self.cfg["eval"]["rerank_ks"], self.cfg["eval"]["final_ks"]
        n = len(qrecs)
        pairs = lambda getter: [(getter(q), q["n_core"]) for q in qrecs]  # noqa: E731
        apairs = lambda getter: [(getter(q), q["n_aux"]) for q in qrecs]  # noqa: E731

        def rec(pl):
            ci = M.bootstrap(pl)
            return {"value": M.micro(pl), "ci": ci, "hits": sum(h for h, _ in pl), "total": sum(t for _, t in pl)}
        agg = {"n_questions": n, "n_core_nodes": sum(q["n_core"] for q in qrecs), "n_aux_nodes": sum(q["n_aux"] for q in qrecs),
               "n_questions_with_core": sum(1 for q in qrecs if q["n_core"]),
               "excluded": {}, "cand": {}, "rerank": {}, "select": {}, "final": {}, "rewrite": {}, "time": {}}
        for q in qrecs:
            for cat, v in q["excluded"].items():
                agg["excluded"][cat] = agg["excluded"].get(cat, 0) + v
        for key in ("@10", "@30", "@50", "vec30", "orig30"):
            agg["cand"][key] = rec(pairs(lambda q, key=key: q["cand"][key]))
        d = M.paired_diff(pairs(lambda q: q["cand"]["window30"]), pairs(lambda q: q["cand"]["orig30"]))
        agg["rewrite"]["recall30_change_vs_orig"] = {"diff": d[0], "ci": [d[1], d[2]]} if d else None
        chk = [q["rewrite_check"] for q in qrecs]
        agg["rewrite"].update({"empty_rate": sum(c["empty"] for c in chk) / n, "format_violation_rate": sum(c["format"] for c in chk) / n,
                               "invented_rate": sum(c["invented"] for c in chk) / n})
        for k in rks:
            agg["rerank"][f"core@{k}"] = rec(pairs(lambda q, k=k: q["rerank"]["core"][f"@{k}"]))
            agg["rerank"][f"aux@{k}"] = rec(apairs(lambda q, k=k: q["rerank"]["aux"][f"@{k}"]))
        wc = [q for q in qrecs if q["n_core"]]
        agg["rerank"]["hit1_core"] = {"hits": sum(1 for q in wc if q["rerank"]["hit1_core"]), "total": len(wc)}
        agg["rerank"]["mrr_core"] = sum(q["rerank"]["rr_core"] for q in wc) / len(wc) if wc else None
        nd = [q["rerank"]["ndcg8"] for q in qrecs if q["rerank"]["ndcg8"] is not None]
        agg["rerank"]["ndcg8"] = sum(nd) / len(nd) if nd else None
        c30 = agg["cand"]["@30"]["value"]
        agg["rerank"]["preservation@16"] = (agg["rerank"].get("core@16", {}).get("value") / c30) if c30 and "core@16" in agg["rerank"] else None
        for k in fks:
            agg["final"][f"core@{k}"] = rec(pairs(lambda q, k=k: q["final"][str(k)]["core"]))
            agg["final"][f"aux@{k}"] = rec(apairs(lambda q, k=k: q["final"][str(k)]["aux"]))
            agg["final"][f"all_core@{k}"] = {"hits": sum(1 for q in wc if q["final"][str(k)]["all_core"]), "total": len(wc)}
            agg["final"][f"mean_articles@{k}"] = sum(q["final"][str(k)]["n_articles"] for q in qrecs) / n
            agg["final"][f"mean_chars@{k}"] = sum(q["final"][str(k)]["chars"] for q in qrecs) / n
            agg["final"][f"input_core@{k}"] = rec(pairs(lambda q, k=k: q["final"][str(k)]["input_core"]))
            agg["final"][f"input_all_core@{k}"] = {"hits": sum(1 for q in wc if q["final"][str(k)]["input_all_core"]), "total": len(wc)}
            agg["final"][f"mean_input_chars@{k}"] = sum(q["final"][str(k)]["input_chars"] for q in qrecs) / n
            if self.S:
                a = [q["select"][str(k)]["anom"] for q in qrecs]
                run = [x for x in a if not x.get("skipped")]
                agg["select"][f"core@{k}"] = rec(pairs(lambda q, k=k: q["select"][str(k)]["core"]))
                agg["select"][f"saved@{k}"] = sum(q["select"][str(k)]["saved"] for q in qrecs)
                agg["select"][f"dropped@{k}"] = sum(q["select"][str(k)]["dropped"] for q in qrecs)
                m = len(run) or 1
                agg["select"][f"anomaly@{k}"] = {"calls": len(run), "parse_fail": sum(x["parse_fail"] for x in run) / m,
                                                 "count_violation": sum(x["count_violation"] for x in run) / m,
                                                 "out_of_range": sum(1 for x in run if x["out_of_range"]) / m,
                                                 "duplicate": sum(1 for x in run if x["duplicate"]) / m}
        T = agg["time"]
        T["rewrite"] = p50_p95([q["sec"]["rewrite"] for q in qrecs])
        T["cands"] = p50_p95([q["sec"]["cands"] for q in qrecs])
        T["rerank"] = p50_p95([q["sec"]["rerank"] for q in qrecs])
        T["embed_query"] = p50_p95([cands["items"][q["id"]]["sec"]["embed_query"] for q in qrecs])
        for k in fks:
            T[f"select@{k}"] = p50_p95([q["sec_select"].get(str(k), 0.0) for q in qrecs])
            T[f"expand@{k}"] = p50_p95([q["final"][str(k)]["sec_expand"] for q in qrecs])
            T[f"search_total@{k}"] = p50_p95([q["sec_search_total"][str(k)] for q in qrecs])
        T["load_sec"] = {"embedding": cands["meta"].get("load_sec"), "doc_vectors": cands["meta"].get("doc_vectors_sec"),
                         "reranker": rr["meta"].get("load_sec"), "llm_first_call_rewrite": rew["meta"].get("first_call_sec"),
                         "llm_first_call_select": sel["meta"].get("first_call_sec") if sel else None}
        T["tokens"] = {"rewrite": rew["meta"].get("tokens"), "select": sel["meta"].get("tokens") if sel else None}
        T["gpu_peak_gb"] = {"cands": cands["meta"].get("gpu_peak_gb"), "rerank": rr["meta"].get("gpu_peak_gb")}
        T["ram_peak_gb"] = {"cands": cands["meta"].get("ram_peak_gb"), "rerank": rr["meta"].get("ram_peak_gb"), "final": env.ram_peak_gb()}
        T["offload"] = rr["meta"].get("offload")
        T["ollama_ps"] = {"rewrite": rew["meta"].get("ollama_ps"), "select": sel["meta"].get("ollama_ps") if sel else None}
        tags = [self.cfg["embedding"]["model"], self.cfg["reranker"].get("model") or "없음"] + sorted({m.model for m in self._llms.values()})
        T["env"] = env.env_line(tags, self.cfg["eval"].get("num_ctx"))
        by = {}
        for q in qrecs:
            key = q.get("유형") or "?"
            h, t = by.get(key, (0, 0))
            by[key] = (h + q["final"][str(fks[0])]["core"], t + q["n_core"])
        agg["by_type_final_core@%d" % fks[0]] = {k: {"hits": h, "total": t} for k, (h, t) in by.items()}
        return agg

    def write_summary(self, out, path):
        """조합별 요약 한 줄을 summary.csv에 덧붙인다 (덮어쓰지 않음)."""
        import csv
        a, m = out["agg"], out["meta"]
        fks = self.cfg["eval"]["final_ks"]
        k0 = fks[0]
        T = a["time"]
        row = {"time": m["time"], "combo": self.combo, "E": self.E, "R": self.R, "L": self.Lrw, "S": self.S, "n_questions": a["n_questions"],
               "n_core_nodes": a["n_core_nodes"], "n_aux_nodes": a["n_aux_nodes"], "excluded": json.dumps(a["excluded"], ensure_ascii=False),
               "cand_core@30": fmt(a["cand"]["@30"]["value"]), "cand_core@10": fmt(a["cand"]["@10"]["value"]), "cand_core@50": fmt(a["cand"]["@50"]["value"]),
               "vec30": fmt(a["cand"]["vec30"]["value"]), "rewrite_gain@30": fmt((a["rewrite"].get("recall30_change_vs_orig") or {}).get("diff")),
               "rerank_core@8": fmt(a["rerank"].get("core@8", {}).get("value")), "rerank_core@16": fmt(a["rerank"].get("core@16", {}).get("value")),
               "hit1": f"{a['rerank']['hit1_core']['hits']}/{a['rerank']['hit1_core']['total']}", "mrr": fmt(a["rerank"]["mrr_core"]),
               "ndcg8": fmt(a["rerank"]["ndcg8"])}
        for k in fks:
            f = a["final"][f"core@{k}"]
            row[f"final_core@{k}"] = fmt(f["value"])
            row[f"final_core@{k}_ci"] = "" if not f["ci"] else f"{f['ci'][0]:.3f}~{f['ci'][1]:.3f}"
            row[f"all_core@{k}"] = f"{a['final'][f'all_core@{k}']['hits']}/{a['final'][f'all_core@{k}']['total']}"
            row[f"final_aux@{k}"] = fmt(a["final"][f"aux@{k}"]["value"])
            row[f"chars@{k}"] = fmt(a["final"][f"mean_chars@{k}"], 0)
            row[f"search_p50@{k}"] = T[f"search_total@{k}"]["p50"]
            row[f"search_p95@{k}"] = T[f"search_total@{k}"]["p95"]
        for st in ("rewrite", "cands", "rerank", "embed_query"):
            row[f"t_{st}_p50"] = T[st]["p50"]
            row[f"t_{st}_p95"] = T[st]["p95"]
        row[f"t_select_p50@{k0}"] = T[f"select@{k0}"]["p50"]
        row[f"t_expand_p50@{k0}"] = T[f"expand@{k0}"]["p50"]
        row.update({"load_embed_sec": T["load_sec"]["embedding"], "load_rerank_sec": T["load_sec"]["reranker"],
                    "gpu_peak_cands": T["gpu_peak_gb"]["cands"], "gpu_peak_rerank": T["gpu_peak_gb"]["rerank"],
                    "ram_peak_gb": max([x for x in T["ram_peak_gb"].values() if x], default=None),
                    "tokens": json.dumps(T["tokens"], ensure_ascii=False), "offload": json.dumps(T["offload"], ensure_ascii=False),
                    "env": T["env"], "commit": m["commit"], "eval_hash": m["eval_hash"], "config_hash": m["config_hash"],
                    "prompt_hash": m["prompt_hash"], "db": m["db"], "result_file": os.path.basename(path)})
        csv_path = self.p("summary.csv")
        old = []
        if os.path.exists(csv_path):
            old = list(csv.DictReader(open(csv_path, encoding="utf-8-sig")))
        cols = list(dict.fromkeys([c for r in old for c in r] + list(row)))
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for r in old + [row]:
                w.writerow(r)
        print(f"요약 추가: {csv_path}", flush=True)
