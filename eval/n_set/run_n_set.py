"""고정 gold(80문항)를 용어집 끔/켬으로 실행하는 평가 runner. 답변 파이프라인은 건드리지 않는다.

- 문항 번호 홀수는 끔→켬, 짝수는 켬→끔으로 교차한다. 실행마다 고유 run_id를 부여한다.
- 실행 전에 run_manifest.json(코드·DB·프롬프트·모델·gold·규칙·runner 해시)을 저장하고, 다른 manifest의 기록과 섞이면 중단한다.
- --dry-run: API·모델·검색을 호출하지 않고 가짜 답으로 160건 구조를 검증한다(DB는 읽기만 한다).
사용(프로젝트 루트에서): python eval/n_set/run_n_set.py --outdir DIR [--dry-run] [--limit N]
"""
import argparse, hashlib, importlib, importlib.metadata as md, json, os, sys, time, datetime, glob

HERE = os.path.dirname(os.path.abspath(__file__))
PIPE = os.path.abspath(os.path.join(HERE, "..", ".."))
os.environ["LANGCHAIN_TRACING_V2"] = "false"; os.environ["LANGSMITH_TRACING"] = "false"
ORIG_CWD = os.getcwd()
os.chdir(PIPE); sys.path.insert(0, PIPE); sys.stdout.reconfigure(encoding="utf-8")

sha = lambda b: hashlib.sha256(b).hexdigest()
fsha = lambda p: sha(open(p, "rb").read())


def code_files():
    fs = glob.glob(os.path.join(PIPE, "src", "**", "*.py"), recursive=True) + [os.path.join(PIPE, "config.py")]
    fs += glob.glob(os.path.join(PIPE, "data", "manual", "*"))
    return sorted(f for f in fs if os.path.isfile(f))


def db_fingerprint():
    """답변에 쓰는 본문·검색본문·형태소·상태·색인여부·메타·임베딩 벡터와 문서 메타를 내용 해시로 남긴다."""
    from src.store.pgstore import _connect
    conn = _connect(); cur = conn.cursor()
    h_doc, h_chunk, h_emb = hashlib.sha256(), hashlib.sha256(), hashlib.sha256()
    cur.execute("SELECT doc_id,title,tier,revision,source_url,file_name FROM documents ORDER BY doc_id")
    nd = 0
    for r in cur.fetchall():
        h_doc.update(json.dumps(r, ensure_ascii=False).encode()); nd += 1
    cur.execute("SELECT chunk_id,doc_id,article,status,is_indexed,body,body_search,body_tokens,corrections_note,cites,embedding_model,indexed_at::text,embedding FROM chunks ORDER BY chunk_id")
    nc = ni = 0
    for r in cur.fetchall():
        *meta, emb = r
        h_chunk.update(json.dumps(meta, ensure_ascii=False, default=str, sort_keys=True).encode())
        if emb is not None: h_emb.update(emb.to_numpy().astype("float32").tobytes()); ni += 1
        nc += 1
    cur.execute("SELECT count(*), max(indexed_at)::text FROM chunks WHERE is_indexed"); iv = cur.fetchone()
    conn.close()
    return {"documents": nd, "documents_sha256": h_doc.hexdigest(), "chunks": nc, "chunks_content_sha256": h_chunk.hexdigest(),
            "indexed_chunks": ni, "embeddings_sha256": h_emb.hexdigest(), "index_version": {"indexed_count": iv[0], "max_indexed_at": iv[1]}}


def settings_for(flag):
    """한 실행에 적용되는 설정. 끔/켬 사이에 달라도 되는 키는 use_glossary뿐이다."""
    import config
    return {"use_glossary": flag, "scope_filter": True, "k": 5, "model": config.OPENAI_MODEL, "temperature": 0,
            "embed_model": config.EMBED_MODEL, "rerank_model": config.RERANK_MODEL, "context_char_budget": config.CONTEXT_CHAR_BUDGET,
            "answer_none_below": config.ANSWER_NONE_BELOW, "answer_partial_below": config.ANSWER_PARTIAL_BELOW,
            "rrf_k": config.RRF_K, "hybrid_weights": [config.HYBRID_VECTOR_WEIGHT, config.HYBRID_KEYWORD_WEIGHT]}


def prompts_hash():
    gen = importlib.import_module("src.answer.generate")
    d = {n: getattr(gen, n) for n in dir(gen) if n.isupper() and n.endswith("PROMPT") and isinstance(getattr(gen, n), str)}
    return {n: sha(v.encode()) for n, v in sorted(d.items())}


def build_manifest(gold_path, rules_path, dry):
    pk = ["langchain", "langchain-core", "langchain-openai", "openai", "sentence-transformers", "torch", "transformers",
          "kiwipiepy", "psycopg", "pgvector", "numpy", "tiktoken"]
    vers = {}
    for p in pk:
        try: vers[p] = md.version(p)
        except Exception: vers[p] = None
    return {"created_at": datetime.datetime.now().isoformat(timespec="seconds"), "dry_run": dry,
            "gold": {"file": os.path.basename(gold_path), "sha256": fsha(gold_path)},
            "scoring_rules": {"file": os.path.basename(rules_path), "sha256": fsha(rules_path)},
            "runner": {"file": os.path.basename(__file__), "sha256": fsha(os.path.abspath(__file__))},
            "code_and_data_files": {os.path.relpath(f, PIPE): fsha(f) for f in code_files()},
            "prompts": prompts_hash(), "settings_common": {k: v for k, v in settings_for(False).items() if k != "use_glossary"},
            "allowed_difference_between_arms": ["use_glossary"], "package_versions": vers, "db": db_fingerprint()}


def plan(gold):
    """홀수 번호는 끔→켬, 짝수 번호는 켬→끔. 반환: 실행 계획 목록."""
    out, seq = [], 0
    for g in gold:
        n = int(g["id"][1:]); order = [False, True] if n % 2 == 1 else [True, False]
        for pos, flag in enumerate(order, 1):
            seq += 1
            out.append({"seq": seq, "id": g["id"], "q": g["q"], "use_glossary": flag, "arm": "on" if flag else "off", "pair_position": pos,
                        "settings": settings_for(flag)})
    return out


def make_record(item, r, run_set, t0, mf_sha):
    base = {"run_id": f"{run_set}-{item['id']}-{item['arm']}-r0", "run_set": run_set, "seq": item["seq"], "id": item["id"], "glossary": item["use_glossary"],
            "arm": item["arm"], "pair_position": item["pair_position"], "settings": item["settings"], "manifest_sha": mf_sha,
            "started_at": t0, "finished_at": datetime.datetime.now().isoformat(timespec="seconds")}
    if "error" in r: return {**base, "error": r["error"]}
    return {**base, "mode": r["mode"], "answer": r["answer"], "withheld": r.get("withheld"), "warnings": r.get("warnings", []),
            "glossary_excluded": r.get("glossary_excluded", []), "scope_excluded": r.get("scope_excluded", []), "search_hint": r.get("search_hint", ""),
            "entries": [{"문서명": e["문서명"], "article": e["article"], "상태": e["조문상태"]} for e in r["entries"]],
            "top_score": r.get("top_score"), "sec": round(time.time() - r["_t"], 1)}


class FakeAssistant:
    """--dry-run 전용. 검색·모델·API를 호출하지 않는다. 특정 문항은 일부러 오류를 낸다(오류 기록 경로 검증)."""
    use_glossary = False
    def ask(self, q):
        if "FAIL" in q: raise RuntimeError("simulated failure")
        return {"mode": "answer", "answer": "[dry-run] " + q[:20], "withheld": None, "warnings": [], "glossary_excluded": [], "scope_excluded": [],
                "search_hint": "", "entries": [], "top_score": 0.5}
    def close(self): pass


def run(args):
    gold = [json.loads(l) for l in open(args.gold, encoding="utf-8")]
    gold.sort(key=lambda g: g["id"])
    if args.limit: gold = gold[:args.limit]
    mf = build_manifest(args.gold, args.rules, args.dry_run)
    mf_sha = sha(json.dumps({k: v for k, v in mf.items() if k not in ("created_at", "dry_run")}, sort_keys=True, ensure_ascii=False).encode())
    args.outdir = os.path.abspath(os.path.join(ORIG_CWD, args.outdir))
    os.makedirs(args.outdir, exist_ok=True)
    mpath, opath = os.path.join(args.outdir, "run_manifest.json"), os.path.join(args.outdir, "runs.jsonl")
    if os.path.exists(mpath):
        old = json.load(open(mpath, encoding="utf-8"))
        if old.get("manifest_sha") != mf_sha:
            sys.exit("중단: 같은 폴더에 다른 manifest의 실행 기록이 있다. 새 폴더를 쓰거나 기록을 섞지 않는다.")
    else:
        json.dump({**mf, "manifest_sha": mf_sha}, open(mpath, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    items = plan(gold)
    if args.dry_run: assistant = FakeAssistant()
    else:
        from src.answer.pipeline import Assistant
        assistant = Assistant()
    done = set()
    if os.path.exists(opath):
        done = {json.loads(l)["run_id"] for l in open(opath, encoding="utf-8")}
    run_set = "N" + str(len(gold)) + "-" + mf_sha[:8] + ("-dry" if args.dry_run else "")
    for it in items:
        rid = f"{run_set}-{it['id']}-{it['arm']}-r0"
        if rid in done: continue
        assistant.use_glossary = it["use_glossary"]
        t0 = datetime.datetime.now().isoformat(timespec="seconds"); ts = time.time()
        q = it["q"] + (" FAIL" if args.dry_run and it["id"] == "N015" and it["arm"] == "on" else "")
        try:
            r = assistant.ask(q); r["_t"] = ts
        except Exception as e:
            r = {"error": repr(e)[:300]}
        rec = make_record(it, r, run_set, t0, mf_sha)
        open(opath, "a", encoding="utf-8").write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(it["seq"], it["id"], it["arm"], rec.get("mode", "ERROR"), flush=True)
    assistant.close()
    print("DONE", mpath, opath)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default=os.path.join(HERE, "n_gold_80_v5_1.jsonl"))
    ap.add_argument("--rules", default=os.path.join(HERE, "scoring_rules_v5_1.md"))
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    run(ap.parse_args())
