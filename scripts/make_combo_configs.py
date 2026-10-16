"""담당별 조합 설정 파일을 만든다 (rag-combo-experiment.md 2절). 결과는 configs/combo/<담당>/ 에 저장돼 있으니 보통 다시 돌릴 필요가 없다.

  python scripts/make_combo_configs.py          # 20개 + 선택 확장 1개를 다시 쓴다

모델이나 옵션을 바꾸고 싶으면 만든 toml을 직접 고치거나 --set 으로 덮어쓴다. 코드는 건드리지 않는다.
"""
import os

OUT = os.path.join("configs", "combo")

E = {
    "bge-m3": ('model = "BAAI/bge-m3"\nfp16 = true', "BAAI/bge-m3"),
    "qwen3-emb-0.6b": ('model = "Qwen/Qwen3-Embedding-0.6B"\nfp16 = true\ninstruction = "@config"   # config.EMBED_QUERY_INSTRUCT (질문 앞 지시문)\nmax_seq_length = 512\nbatch_size = 16', "Qwen/Qwen3-Embedding-0.6B"),
    "qwen3-emb-4b": ('kind = "ollama"                 # Ollama로 서빙 (먼저 ollama pull qwen3-embedding:4b-q8_0)\nmodel = "qwen3-embedding:4b-q8_0"\nbase_url = "http://localhost:11434"\ninstruction = "@config"\nbatch_size = 16', "qwen3-embedding:4b-q8_0"),
}
R = {
    "none": 'kind = "none"                    # 리랭커 없음: RRF 순서 그대로',
    "bge": 'kind = "cross"\nmodel = "BAAI/bge-reranker-v2-m3"\nfp16 = true',
    "qwen4b": 'kind = "qwen"                    # GPU 6GiB + 나머지 CPU 오프로드 (100문항 약 6분)\nmodel = "Qwen/Qwen3-Reranker-4B"\ngpu_mem = "6GiB"\ncpu_mem = "24GiB"\nbatch_size = 6                      # 4명 모두 같게 둔다: 질문당 3.7초, GPU 6.9GB (배치 2는 6.9초, 15 이상은 GPU 메모리 부족으로 느려짐). 배치가 다르면 점수가 0.01 안팎 달라진다\nmax_length = 2048',
}
L = {
    "9b": ('[llm.default]\nmodel = "qwen3.5:9b"\nbase_url = "http://localhost:11434/v1"   # Ollama OpenAI 호환 주소\nreasoning_effort = "none"                # 사고 모드 끄기 (파싱이 깨지지 않게)\ntimeout = 300', "qwen3.5:9b"),
    "30b": ('[llm.default]\nmodel = "qwen3:30b-a3b-instruct-2507-q4_K_M"\nbase_url = "http://localhost:11434/v1"\ntimeout = 600', "qwen3:30b-a3b-instruct-2507-q4_K_M"),
}
PEOPLE = {  # 담당: (임베딩, 리랭커들, LLM)
    "lee": ("bge-m3", ["none", "bge"], "9b"),
    "han": ("qwen3-emb-0.6b", ["none", "bge"], "30b"),
    "kim": ("qwen3-emb-0.6b", ["none", "bge", "qwen4b"], "9b"),
    "park": ("bge-m3", ["none", "bge", "qwen4b"], "30b"),
}
TEMPLATE = '''# {title}
# 임베딩 {e_model} | 리랭커 {r} | LLM {l_model} | 선택 {s_txt}
# 코드는 고정이다. 바꾸는 것은 이 파일의 [embedding] [reranker] [llm.*] [select] 값뿐이다.
name = "{name}"

[data]
questions = "eval/answer_eval_100_tiered.jsonl"   # 개발용 평가셋. 시험용(holdout)은 마지막에 상위 2개 조합에만 1회: --set data.questions=\\"eval/answer_eval_holdout.jsonl\\"
out_dir = "eval/combo"

[rewrite]
source = "llm"                                    # 담당 LLM이 질문 전체를 직접 바꿔 쓴다 (① 단계). 시간을 재므로 workers = 1
workers = 1

[embedding]
{emb}

[reranker]
{rr}

{llm}

[select]
enabled = {enabled}                              # 켬: 리랭크 상위 pool개 조에서 LLM이 k개(8, 10)를 고른다
pool = 16
ks = [8, 10]

[judge]                                           # 채점 모델 (⑥ 답변 단계에서만 쓴다). 후보 LLM이 아닌 모델로 확정 필요 (미정)
model = "gpt-5.6-luna"

[eval]
rerank_ks = [5, 8, 10, 16]
final_ks = [8, 10]
answer_k = 8
# num_ctx = 4096                                  # Ollama에 적용한 컨텍스트 길이를 적어 두면 요약의 환경 한 줄에 기록된다
'''


def write(person, e, r, l, s):
    e_txt, e_model = E[e]
    l_txt, l_model = L[l]
    name = f"{person}_{e}_{r}_{l}_S{s}"
    body = TEMPLATE.format(title=name, e_model=e_model, r=r, l_model=l_model, s_txt="켬" if s else "끔", name=name, emb=e_txt, rr=R[r],
                           llm=l_txt, enabled="true " if s else "false")
    d = os.path.join(OUT, person)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, f"{e}_{r}_{l}_S{s}.toml"), "w", encoding="utf-8", newline="\n") as f:
        f.write(body)
    return name


def main():
    n = 0
    for person, (e, rs, l) in PEOPLE.items():
        for r in rs:
            for s in (0, 1):
                write(person, e, r, l, s)
                n += 1
    write("park", "qwen3-emb-4b", "none", "30b", 0)  # 박형건 선택 확장: Qwen3-Embedding-4B (후보 단계 지표까지)
    print(f"{n}개 + 선택 확장 1개 작성: {OUT}")


if __name__ == "__main__":
    main()
