"""조합 도구(src.combo.run)를 검색 설정만 바꿔 실행한다. 가빈 코드는 고치지 않고 값을 이 프로세스 안에서만 덮어쓴다.

사용(MINI_CNS_rag에서): python scripts/combo_weight_run.py <벡터가중치> <키워드가중치> <src.combo.run 인자...>
환경 변수 RERANK_TOP=<수>를 주면 리랭커에 넘기는 후보 수(기본 30)도 바꾼다.
캐시 파일 이름에 가중치 · 후보 수가 들어가지 않으므로 반드시 --set data.out_dir 로 설정마다 다른 폴더를 준다.
"""
import os
import sys

sys.path.insert(0, os.getcwd())  # MINI_CNS_rag의 config · src를 쓴다 (이 스크립트 폴더가 아니라)
import config  # noqa: E402

config.HYBRID_VECTOR_WEIGHT, config.HYBRID_KEYWORD_WEIGHT = float(sys.argv[1]), float(sys.argv[2])
msg = f"하이브리드 가중치: 벡터 {config.HYBRID_VECTOR_WEIGHT} / 키워드 {config.HYBRID_KEYWORD_WEIGHT}"
if os.environ.get("RERANK_TOP"):
    from src.search import search as search_module

    top = int(os.environ["RERANK_TOP"])
    search_module.RERANK_TOP = top
    f = search_module.Searcher.rerank_window
    f.__defaults__ = (top,) + f.__defaults__[1:]  # 기본 인자(top)가 정의 때 묶여 있어 함께 바꾼다
    msg += f" | 리랭커에 넘기는 후보 {top}개"
from src.combo import models as combo_models  # noqa: E402

_orig_embedder_init = combo_models.Embedder.__init__


def _embedder_init(self, model, instruction="", doc_prefix="", max_seq_length=0, fp16=True, device="cuda", batch_size=32,
                   trust_remote_code=False, **kw):
    """jina-embeddings-v4: 검색 어댑터(task=retrieval)를 고르고 bf16으로 올린다. 도구 기본(fp16 변환)은 이 모델에서 값이 틀어진다.
    transformers 4.53 · sentence-transformers 4.1 · peft 0.15.2 환경에서만 맞게 동작한다 (5.x · peft 0.21은 어댑터가 안 붙음)."""
    if "jina-embeddings-v4" not in model:
        return _orig_embedder_init(self, model, instruction, doc_prefix, max_seq_length, fp16, device, batch_size, trust_remote_code, **kw)
    import torch
    from sentence_transformers import SentenceTransformer
    self.name, self.instruction, self.doc_prefix, self.bs = model, instruction or "", doc_prefix or "", batch_size
    self.max_seq_length = max_seq_length
    self.m = SentenceTransformer(model, device=device, trust_remote_code=True,
                                 model_kwargs={"default_task": "retrieval", "torch_dtype": torch.bfloat16})
    if max_seq_length:
        self.m.max_seq_length = max_seq_length


combo_models.Embedder.__init__ = _embedder_init
sys.argv = ["src.combo.run"] + sys.argv[3:]
print(msg, flush=True)

from src.combo import run  # noqa: E402

run.main()
