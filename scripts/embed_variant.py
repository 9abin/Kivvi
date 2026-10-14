"""임베딩 모델 비교용: 로컬 regulations_team 사본을 새 DB로 복제하고, 색인 대상 청크 벡터를 다른 모델로 다시 만든다.

본문·BM25 토큰·인용은 그대로이고 chunks.embedding, embedding_model만 바뀐다 (벡터 차원 1024 모델만).
팀 DB는 건드리지 않는다. 원본 regulations_team도 읽기만 한다 (pg_dump).
임베딩 입력은 팀 DB와 같은 body_search (bge-m3로 다시 만들어 저장된 벡터와 코사인 1.000으로 일치 확인, 2026.10.14).

실행 (저장소 루트에서):
  uv run python -m scripts.embed_variant --model nlpai-lab/KURE-v1 --target regulations_team_kure
검색: $env:PG_DATABASE = "regulations_team_kure"; $env:EMBED_MODEL = "nlpai-lab/KURE-v1"
"""
import argparse
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")
CONTAINER = "db-pg"


def run(args, check=True):
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (r.stdout + r.stderr).strip()
    if out:
        print(out[-500:])
    if check and r.returncode != 0:
        sys.exit("실패: " + " ".join(args[:6]))
    return r


def clone(source, target):
    psql = ["docker", "exec", CONTAINER, "psql", "-U", "postgres", "-v", "ON_ERROR_STOP=1"]
    if run(psql + ["-d", "postgres", "-tAc", f"select 1 from pg_database where datname='{target}'"]).stdout.strip():
        print(f"{target} 이미 있음: 복제를 건너뛰고 벡터만 다시 만든다")
        return
    run(psql + ["-d", "postgres", "-c", f"CREATE DATABASE {target} OWNER rag_reg"])
    run(psql + ["-d", target, "-c", "CREATE EXTENSION IF NOT EXISTS vector"])
    dump = f"/tmp/{source}.dump"
    run(["docker", "exec", CONTAINER, "pg_dump", "-U", "postgres", "-d", source, "-n", "public",
         "--no-owner", "--no-privileges", "-Fc", "-f", dump])
    run(["docker", "exec", CONTAINER, "pg_restore", "-U", "rag_reg", "-d", target, "--no-owner", "--no-privileges", dump],
        check=False)  # 'schema public already exists'는 무해
    run(["docker", "exec", CONTAINER, "rm", "-f", dump])


def embed(target, model_name, batch):
    os.environ["PG_DATABASE"] = target
    from sentence_transformers import SentenceTransformer
    from src.store.pgstore import PgStore
    store = PgStore()
    with store.conn.cursor() as cur:
        cur.execute("SELECT chunk_id, coalesce(body_search, body) FROM chunks WHERE is_indexed ORDER BY chunk_id")
        rows = cur.fetchall()
    model = SentenceTransformer(model_name, device="cuda")
    model.half()
    model.max_seq_length = min(model.max_seq_length or 8192, 2048)
    assert model.get_sentence_embedding_dimension() == 1024, model.get_sentence_embedding_dimension()
    # 긴 것끼리 묶으면 패딩이 줄어 빠르다
    rows.sort(key=lambda r: len(r[1]))
    with store.conn.cursor() as cur:
        for i in range(0, len(rows), batch):
            part = rows[i:i + batch]
            vecs = model.encode([t for _, t in part], batch_size=batch, normalize_embeddings=True, convert_to_numpy=True)
            cur.executemany("UPDATE chunks SET embedding = %s, embedding_model = %s WHERE chunk_id = %s",
                            [(v.astype("float32"), model_name, cid) for (cid, _), v in zip(part, vecs)])
            if (i // batch) % 40 == 0:
                print(f"  {i + len(part)}/{len(rows)}", flush=True)
        cur.execute("REINDEX INDEX chunks_embedding_hnsw")
    store.conn.commit()
    with store.conn.cursor() as cur:
        cur.execute("SELECT embedding_model, count(*) FROM chunks WHERE is_indexed GROUP BY 1")
        print("완료:", cur.fetchall())
    store.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--target", required=True)
    ap.add_argument("--source", default="regulations_team")
    ap.add_argument("--batch", type=int, default=16)
    args = ap.parse_args()
    clone(args.source, args.target)
    embed(args.target, args.model, args.batch)


if __name__ == "__main__":
    main()
