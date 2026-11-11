"""바꿔 끼우는 부품: 임베딩, 리랭커, LLM. 설정 파일의 [embedding] [reranker] [llm.*] 값으로 만든다."""
import gc
import hashlib
import os
import re
import subprocess

import numpy as np


def sha(*parts):
    h = hashlib.sha256()
    for p in parts:
        h.update(str(p).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:12]


def safe(name):
    """파일 이름에 쓸 수 있게: 'Qwen/Qwen3-Embedding-0.6B' -> 'qwen3-embedding-0.6b'."""
    tail = str(name).split("/")[-1].lower()
    return re.sub(r"[^0-9a-z가-힣._-]+", "-", tail).strip("-")


def free_gpu():
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:  # noqa: BLE001
        pass


def gpu_peak_gb():
    try:
        import torch
        return round(torch.cuda.max_memory_allocated() / 1e9, 2) if torch.cuda.is_available() else None
    except Exception:  # noqa: BLE001
        return None


def reset_gpu_peak():
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------- 임베딩 ----
class Embedder:
    """sentence-transformers 임베딩 모델. instruction은 질문 앞에 붙이는 지시문(Qwen3-Embedding 등), doc_prefix는 문서 앞 접두어."""

    def __init__(self, model, instruction="", doc_prefix="", max_seq_length=0, fp16=True, device="cuda",
                 batch_size=32, trust_remote_code=False, **_):
        from sentence_transformers import SentenceTransformer
        self.name, self.instruction, self.doc_prefix, self.bs = model, instruction or "", doc_prefix or "", batch_size
        self.max_seq_length = max_seq_length
        self.m = SentenceTransformer(model, device=device, trust_remote_code=trust_remote_code)
        if max_seq_length:
            self.m.max_seq_length = max_seq_length
        if fp16 and device == "cuda":
            self.m.half()

    def encode_queries(self, texts):
        kw = {"prompt": self.instruction} if self.instruction else {}
        return self.m.encode(list(texts), normalize_embeddings=True, convert_to_numpy=True, batch_size=self.bs,
                             show_progress_bar=False, **kw)

    def encode_docs(self, texts):
        return self.m.encode([self.doc_prefix + t for t in texts], normalize_embeddings=True, convert_to_numpy=True,
                             batch_size=self.bs, show_progress_bar=True)

    def close(self):
        del self.m
        free_gpu()


# ---------------------------------------------------------------- 리랭커 ----
class CrossReranker:
    """sentence-transformers CrossEncoder (bge-reranker-v2-m3 등). 운영 코드(Searcher)와 같이 기본은 fp32다."""

    def __init__(self, model, max_length=0, fp16=False, device="cuda", trust_remote_code=False, batch_size=16, **_):
        from sentence_transformers import CrossEncoder
        kw = {"device": device, "trust_remote_code": trust_remote_code}
        if max_length:
            kw["max_length"] = max_length
        self.name, self.bs = model, batch_size
        self.m = CrossEncoder(model, **kw)
        if fp16 and device == "cuda":
            self.m.model.half()

    def predict(self, pairs, batch_size=None, **_):
        return [float(x) for x in self.m.predict(pairs, batch_size=batch_size or self.bs, show_progress_bar=False)]

    def close(self):
        del self.m
        free_gpu()


class QwenReranker:
    """Qwen3-Reranker 계열: 'yes'/'no' 토큰 확률로 점수를 낸다. 모델이 GPU에 안 들어가면 gpu_mem만 GPU에 두고 나머지는 CPU로 넘긴다."""

    def __init__(self, model, max_length=4096, gpu_mem="6GiB", cpu_mem="24GiB", batch_size=4, instruction=None,
                 device="cuda", **_):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        self.name, self.max_length, self.bs = model, max_length, batch_size
        self.tok = AutoTokenizer.from_pretrained(model, padding_side="left")
        kw = {"torch_dtype": torch.float16}
        if device == "cuda":
            kw.update(device_map="auto", max_memory={0: gpu_mem, "cpu": cpu_mem})
        self.m = AutoModelForCausalLM.from_pretrained(model, **kw).eval()
        self.dev = next(self.m.parameters()).device
        self.yes, self.no = self.tok.convert_tokens_to_ids("yes"), self.tok.convert_tokens_to_ids("no")
        self.inst = instruction or "Given a Korean employee question, retrieve the internal company regulation passage that answers it"
        self.prefix = ("<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and the Instruct provided. "
                       "Note that the answer can only be \"yes\" or \"no\".<|im_end|>\n<|im_start|>user\n")
        self.suffix = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"

    def predict(self, pairs, batch_size=None, **_):
        import torch
        bs = batch_size or self.bs
        order = sorted(range(len(pairs)), key=lambda i: len(pairs[i][1]))  # 길이가 비슷한 것끼리 묶어 패딩을 줄인다
        scores = [0.0] * len(pairs)
        for s in range(0, len(order), bs):
            idx = order[s:s + bs]
            texts = [self.prefix + f"<Instruct>: {self.inst}\n<Query>: {pairs[i][0]}\n<Document>: {pairs[i][1]}" + self.suffix for i in idx]
            enc = self.tok(texts, padding=True, truncation=True, max_length=self.max_length, return_tensors="pt").to(self.dev)
            with torch.no_grad():
                # 마지막 위치만 계산한다(logits_to_keep=1): 전체 위치의 어휘 로짓(15만 차원)을 만들지 않아 메모리와 시간이 크게 준다
                logits = self.m(**enc, logits_to_keep=1).logits[:, -1, :]
            two = torch.stack([logits[:, self.no], logits[:, self.yes]], dim=1).float()
            for i, p in zip(idx, torch.softmax(two, dim=1)[:, 1].tolist()):
                scores[i] = float(p)
        return scores

    def close(self):
        del self.m
        free_gpu()


class OllamaEmbedder:
    """Ollama로 서빙하는 임베딩 모델 (예: qwen3-embedding:4b-q8_0, 2,560차원). 문서 벡터를 emb_<E>.npy로 저장해 쓰므로 DB 차원 제한이 없다.
    dimensions를 주면 앞쪽 N차원만 쓴다(Matryoshka 축소). max_seq_length는 Ollama 쪽 num_ctx가 정한다."""

    def __init__(self, model, base_url="http://localhost:11434", instruction="", doc_prefix="", batch_size=16, dimensions=0,
                 timeout=600, **_):
        self.name, self.url, self.bs, self.dimensions, self.timeout = model, base_url.rstrip("/"), batch_size, dimensions, timeout
        self.instruction, self.doc_prefix, self.max_seq_length = instruction or "", doc_prefix or "", 0

    def _embed(self, texts):
        import requests
        r = requests.post(f"{self.url}/api/embed", json={"model": self.name, "input": list(texts), "truncate": True}, timeout=self.timeout)
        r.raise_for_status()
        v = np.array(r.json()["embeddings"], dtype=np.float32)
        if self.dimensions:
            v = v[:, :self.dimensions]
        return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-12)

    def encode_queries(self, texts):
        return self._embed([self.instruction + t for t in texts])

    def encode_docs(self, texts):
        out = []
        for i in range(0, len(texts), self.bs):
            out.append(self._embed([self.doc_prefix + t for t in texts[i:i + self.bs]]))
            if (i // self.bs) % 20 == 0:
                print(f"  문서 임베딩 {i}/{len(texts)}", flush=True)
        return np.vstack(out)

    def close(self):
        try:
            subprocess.run(["ollama", "stop", self.name], capture_output=True, timeout=60)
        except Exception:  # noqa: BLE001
            pass


def make_embedder(spec):
    """kind = "st"(기본, sentence-transformers) 또는 "ollama"."""
    spec = dict(spec)
    kind = spec.pop("kind", "st")
    return {"st": Embedder, "ollama": OllamaEmbedder}[kind](**spec)


def make_reranker(spec):
    """kind = "none" 이면 None(리랭커 없음, RRF 순서 그대로). "cross" 또는 "qwen"."""
    spec = dict(spec)
    kind = spec.pop("kind", "cross")
    if kind == "none":
        return None
    header = spec.pop("header", None)
    r = {"cross": CrossReranker, "qwen": QwenReranker}[kind](**spec)
    r.header = header
    return r


# ------------------------------------------------------------------- LLM ----
_THINK = re.compile(r"<think>.*?</think>", re.S)


def strip_think(text):
    """사고 과정(<think>…</think>)을 지운다. 닫는 태그만 남은 경우도 처리한다."""
    text = _THINK.sub("", text or "")
    if "</think>" in text:
        text = text.split("</think>")[-1]
    return text.strip()


def _usage_handler(owner):
    from langchain_core.callbacks import BaseCallbackHandler

    class Usage(BaseCallbackHandler):
        def on_llm_end(self, response, **kwargs):
            for gens in response.generations:
                for g in gens:
                    u = getattr(getattr(g, "message", None), "usage_metadata", None) or {}
                    owner.tokens["in"] += u.get("input_tokens", 0)
                    owner.tokens["out"] += u.get("output_tokens", 0)
                    owner.tokens["calls"] += 1
    return Usage()


class LLM:
    """OpenAI 호환 채팅 모델 하나(OpenAI, Ollama, vLLM …). base_url을 주면 로컬 서버를 쓴다.

    필드: model(필수), base_url, api_key_env(기본 OPENAI_API_KEY), temperature(0), timeout(120), max_retries(2),
          reasoning_effort (Ollama의 Qwen3 계열은 "none"으로 사고 끔), extra_body.
    """

    def __init__(self, spec):
        from langchain_openai import ChatOpenAI
        self.spec = dict(spec)
        self.model = spec["model"]
        self.base_url = spec.get("base_url")
        self.tokens = {"in": 0, "out": 0, "calls": 0}
        kw = {"model": self.model, "temperature": spec.get("temperature", 0), "timeout": spec.get("timeout", 120),
              "max_retries": spec.get("max_retries", 2),
              "api_key": os.environ.get(spec.get("api_key_env", "OPENAI_API_KEY")) or "EMPTY"}
        if self.base_url:
            kw["base_url"] = self.base_url
        if spec.get("reasoning_effort"):
            kw["reasoning_effort"] = spec["reasoning_effort"]
        if spec.get("extra_body"):
            kw["extra_body"] = spec["extra_body"]
        if spec.get("max_tokens"):
            kw["max_tokens"] = spec["max_tokens"]
        self.chat = ChatOpenAI(stream_usage=True, callbacks=[_usage_handler(self)], **kw)

    @property
    def label(self):
        return safe(self.model)

    @property
    def is_ollama(self):
        return bool(self.spec.get("ollama", self.base_url and ":11434" in self.base_url))

    @staticmethod
    def _usage(msg):
        u = getattr(msg, "usage_metadata", None) or {}
        return {"in": u.get("input_tokens", 0), "out": u.get("output_tokens", 0)}

    def ask(self, system, user, usage=False):
        """시스템+사용자 메시지로 호출. usage=True면 (글, {"in", "out"}) 토큰 수까지 돌려준다."""
        msg = self.chat.invoke([("system", system), ("human", user)])
        text = strip_think(msg.content)
        return (text, self._usage(msg)) if usage else text

    def complete(self, prompt, usage=False):
        """사용자 메시지 하나만 보내는 호출 (조문 선택 프롬프트처럼 시스템 메시지가 따로 없는 경우)."""
        msg = self.chat.invoke(prompt)
        text = strip_think(msg.content)
        return (text, self._usage(msg)) if usage else text

    def runnable(self):
        """Generator.model 자리에 넣는 체인: 답변 글에서 사고 과정을 지우고 토큰 수를 센다."""
        from langchain_core.messages import AIMessage
        from langchain_core.runnables import RunnableLambda

        def fix(msg):
                return AIMessage(content=strip_think(msg.content))
        return self.chat | RunnableLambda(fix)

    def stop(self):
        """Ollama 모델을 GPU에서 내린다(단계 사이에 GPU를 비우기 위해). 실패해도 계속 진행한다."""
        if self.is_ollama:
            try:
                subprocess.run(["ollama", "stop", self.model], capture_output=True, timeout=60)
            except Exception:  # noqa: BLE001
                pass

    def take_tokens(self):
        t, self.tokens = self.tokens, {"in": 0, "out": 0, "calls": 0}
        return t


def doc_vectors(embedder, texts, path, meta):
    """색인 청크 전체의 문서 벡터. 같은 모델·접두어·본문이면 디스크 캐시(emb_<E>.npy)를 쓴다. meta가 다르면 오류."""
    import json
    meta_path = path[:-4] + ".meta.json"
    if os.path.exists(path) and os.path.exists(meta_path):
        old = json.load(open(meta_path, encoding="utf-8"))
        if old != meta:
            raise RuntimeError(f"{os.path.basename(path)}가 다른 설정으로 만들어졌습니다 (모델·접두어·본문이 다름). 이름을 바꾸거나 파일을 옮기세요.\n  기존 {old}\n  지금 {meta}")
        return np.load(path)
    mat = embedder.encode_docs(texts).astype(np.float32)
    np.save(path, mat)
    json.dump(meta, open(meta_path, "w", encoding="utf-8"), ensure_ascii=False)
    return mat
