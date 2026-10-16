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
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor

import config as app_config
import src.search.search as search_module
from src.answer import generate as gen_module
from src.combo import env
from src.combo import metrics as M
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
    def __init__(self, cfg, run_tag="", force=(), fresh=False):
        self.cfg, self.tag, self.force = cfg, run_tag, set(force)
        # fresh: 같은 설정을 처음부터 다시 돌려 변동을 본다. 바꿔 쓰기·후보·리랭크도 태그를 붙인 새 파일에 만든다(기존 캐시를 쓰지 않고 덮어쓰지도 않는다)
        self.sfx = f"_{run_tag}" if (fresh and run_tag) else ""
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
            else safe(self.llm_spec("rewrite")["model"])
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
        path = self.p(f"rewrite_{self.Lrw}{self.sfx}.json")
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
        llm = self.llm("rewrite")
        times = []

        def one(g):
            t = time.perf_counter()
            raw, usage, err = None, {"in": 0, "out": 0}, None
            try:
                raw, usage = llm.ask(gen_module.REWRITE_PROMPT, g["q"], usage=True)
            except Exception as e:  # noqa: BLE001
                err = repr(e)[:200]
            sec = time.perf_counter() - t
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
        path = self.p(f"cands_{self.E}_{self.Lrw}{self.sfx}.json")
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
            hint = " ".join(x for x in (rewritten, synonyms.expand(q, s.synonym_table), glossary.signal_terms(q, s.glossary)) if x)
            extra = ()
            if hint:
                extra = (("벡터(바꿔쓴)", s.vector_hits(hint, None), wv), ("키워드(바꿔쓴)", s.bm25_hits(hint, None), wk))
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

    # ---- ③ 리랭크 ------------------------------------------------------
    def stage_rerank(self, cands):
        path = self.p(f"rerank_{self.E}_{self.Lrw}_{self.R}{self.sfx}.json")
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
            ranked = s.rerank(g["q"], cd)
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
        data = {"meta": self.stage_meta({"reranker": None if rcfg.get("kind") == "none" else rcfg.get("model"), "kind": rcfg.get("kind"), "load_sec": round(load_sec, 2),
                                         "fp16": rcfg.get("fp16"), "offload": offload, "label": self.R}), "items": items}
        save_json(path, data)
        if reranker is not None:
            reranker.close()
        s.reranker = None
        free_gpu()
        return data

    # ---- ④⑤ 선택 + 최종 근거 + 지표 ------------------------------------
    def ranked_of(self, s, g, cands, rr):
        c, r = cands["items"][g["id"]], rr["items"][g["id"]]
        rrf = {w["cid"]: w["rrf"] for w in c["window"]}
        scores = r["scores"] or {}
        ranked = [{**s.row_info[cid], "chunk_id": cid, "score": rrf.get(cid), "rerank": scores.get(cid)} for cid in r["order"]]
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

    def stage_final(self, cands, rr, sel, rew):
        """④ 선택 끔 / ⑤ 선택 켬 뒤 최종 근거를 만들고 질문별 지표를 계산해 results_<조합>.json에 저장한다."""
        s = self.searcher(None, None)
        rcfg = self.cfg["eval"]
        rks, fks = rcfg["rerank_ks"], rcfg["final_ks"]
        titles = [d["title"] for d in s.documents.values() if d.get("title")]
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
            arts = self.ranked_of(s, g, cands, rr)
            order = M.first_seen(exact_nodes + [self.node_of[h["chunk_id"]] for h in arts])
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
                asm = s.assemble(results)
                expand = time.perf_counter() - t0
                final_nodes = {M.node_key(it["doc_id"], it["article"]) for grp in asm["groups"] for it in grp["items"]}
                fin[str(k)] = {"nodes": sorted(final_nodes), "core": len(core & final_nodes), "aux": len(aux & final_nodes),
                               "all_core": bool(core) and core <= final_nodes, "chars": asm["chars"], "n_articles": len(asm["groups"]),
                               "sec_expand": round(expand, 4)}
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
                                        "prompt_hash": self.prompt_hash(), "run_tag": self.tag}),
               "agg": agg, "questions": qrecs}
        path = self.new_result_path(f"results_{self.combo}")
        save_json(path, out)
        print(f"결과 저장: {path}", flush=True)
        self.write_summary(out, path)
        return out

    @staticmethod
    def prompt_hash():
        return sha(gen_module.REWRITE_PROMPT, gen_module.SYSTEM_PROMPT, gen_module.VERIFY_PROMPT, gen_module.CONFLICT_PROMPT,
                   search_module.SELECT_PROMPT, getattr(gen_module, "HIT_NOTE", ""))

    @staticmethod
    def check_rewrite(rw, titles):
        """바꿔 쓰기 출력 점검: 빈 출력, 형식 위반(여러 줄·너무 긺·JSON/코드블록), 없는 규정명·조 번호 포함, 한자(중국어) 섞임.

        규정명은 '붙여 쓴 한글 낱말 + 규정/지침/규칙/세칙'만 보고 제목 목록에 없으면 지어낸 것으로 센다.
        "휴가 관련 규정"처럼 띄어 쓴 일반어는 규정명이 아니라서 세지 않는다 (처음에는 이것까지 세어 58%로 과대 집계됐다)."""
        raw, text = rw.get("raw"), rw.get("rewritten")
        if not text:
            return {"empty": True, "format": False, "invented": False, "cjk": False}
        lines = [x for x in (raw or "").strip().splitlines() if x.strip()]
        fmt_bad = len(lines) > 1 or len(text) > 200 or "{" in text or "```" in (raw or "")
        art = bool(re.search(r"제\s*\d+\s*조", text))
        names = re.findall(r"([가-힣]{2,25}(?:규정|지침|규칙|세칙))", text)
        unknown = any(not any(n in t.replace(" ", "") for t in titles) for n in names)
        return {"empty": False, "format": fmt_bad, "invented": art or unknown, "cjk": bool(re.search(r"[一-鿿]", text))}

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
                               "invented_rate": sum(c["invented"] for c in chk) / n,
                               "cjk_rate": sum(c.get("cjk", False) for c in chk) / n})
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
        tags = [self.cfg["embedding"]["model"], "리랭커 없음" if self.cfg["reranker"].get("kind") == "none" else self.cfg["reranker"].get("model")]             + sorted({m.model for m in self._llms.values()})
        ps = (rew["meta"].get("ollama_ps") or []) + ((sel["meta"].get("ollama_ps") or []) if sel else [])
        T["env"] = env.env_line(tags, self.cfg["eval"].get("num_ctx") or env.ctx_from_ps(ps))
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
