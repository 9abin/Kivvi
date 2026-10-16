"""시간·자원 측정 도우미 (rag-combo-experiment.md 5.3절). 추가 패키지 없이 표준 라이브러리와 torch, nvidia-smi, ollama만 쓴다."""
import os
import platform
import subprocess
import sys


def ram_peak_gb():
    """이 프로세스의 RAM 피크(작업 집합 최대값, GB). 알 수 없으면 None."""
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class PMC(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
            pmc = PMC()
            pmc.cb = ctypes.sizeof(PMC)
            handle = ctypes.windll.kernel32.GetCurrentProcess()
            ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
            if ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(pmc), pmc.cb):
                return round(pmc.PeakWorkingSetSize / 1e9, 2)
            return None
        import resource
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss  # Linux는 KB, macOS는 바이트
        return round(rss / (1e6 if sys.platform != "darwin" else 1e9), 2)
    except Exception:  # noqa: BLE001
        return None


def ram_total_gb():
    try:
        if sys.platform == "win32":
            import ctypes

            class MEM(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong), ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MEM()
            m.dwLength = ctypes.sizeof(MEM)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return round(m.ullTotalPhys / 1e9, 1)
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1e9, 1)
    except Exception:  # noqa: BLE001
        return None


def _run(cmd, timeout=15):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        return (r.stdout or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def ollama_ps():
    """지금 Ollama가 올려 둔 모델 (모델, 크기, GPU/CPU 비율, 컨텍스트). 오프로드 여부를 이 값으로 적는다."""
    out = _run(["ollama", "ps"])
    return out.splitlines()[1:] if out else []


def ctx_from_ps(lines):
    """`ollama ps` 줄에서 컨텍스트 길이(예: 4096)를 읽는다. 설정에 num_ctx를 적지 않았을 때 환경 한 줄에 실제 값을 적기 위해서다."""
    import re
    for line in lines or []:
        m = re.search(r"(?:GPU|CPU)\s+(\d{3,6})(?!\d)", line)
        if m:
            return int(m.group(1))
    return None


def env_line(model_tags, num_ctx=None):
    """측정 환경 한 줄: GPU, RAM, 드라이버, 모델 태그, num_ctx."""
    gpu = None
    try:
        import torch
        gpu = f"{torch.cuda.get_device_name(0)} {round(torch.cuda.get_device_properties(0).total_memory / 2**30)}GiB" if torch.cuda.is_available() else "GPU 없음"
        torch_v = torch.__version__
    except Exception:  # noqa: BLE001
        torch_v = "?"
    driver = _run(["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"]) or "?"
    ctx = f"num_ctx={num_ctx}" if num_ctx else "num_ctx=Ollama 기본값"
    return (f"{gpu}, RAM {ram_total_gb()}GB, 드라이버 {driver}, {platform.system()} {platform.release()}, python {platform.python_version()}, "
            f"torch {torch_v}, 모델 {', '.join(model_tags)}, {ctx}")


def git_state():
    """코드 커밋 해시(짧게)와 작업 폴더 수정 여부. 결과에 기록한다."""
    head = _run(["git", "rev-parse", "--short", "HEAD"])
    dirty = bool(_run(["git", "status", "--porcelain", "--untracked-files=no"]))
    return head + ("+수정" if dirty else "")
