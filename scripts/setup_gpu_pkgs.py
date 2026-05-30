"""Install GPU packages (torch CUDA + paddlepaddle-gpu + nvidia runtime DLLs).

Called by setup_gpu.bat after base uv sync.
Exit 0 = success, Exit 1 = failure.
"""

import subprocess
import sys

TORCH_IDX = "https://download.pytorch.org/whl/cu126"
PADDLE_IDX = "https://www.paddlepaddle.org.cn/packages/stable/cu126/"


def run(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0 and check:
        print(f"  FAILED: {' '.join(args)}", flush=True)
        if r.stdout.strip():
            print(f"  stdout: {r.stdout.strip()[:500]}", flush=True)
        if r.stderr.strip():
            print(f"  stderr: {r.stderr.strip()[:500]}", flush=True)
    return r


def pkg_installed(name: str) -> bool:
    return run(["uv", "pip", "show", name], check=False).returncode == 0


def main():
    uv = ["uv"]
    failed = False

    # ── Step 1: GPU torch ──
    print("  Installing torch (CUDA 12.6)...", flush=True)
    r = run(uv + ["pip", "install", "torch", "torchvision", "torchaudio",
                   "--index-url", TORCH_IDX, "--quiet"])
    if r.returncode != 0:
        print("  [ERROR] GPU torch install failed", flush=True)
        failed = True
    else:
        print("  GPU torch installed", flush=True)

    # ── Step 2: paddlepaddle-gpu ──
    if not failed:
        print("  Installing paddlepaddle-gpu (CUDA 12.6)...", flush=True)
        run(uv + ["pip", "uninstall", "paddlepaddle-gpu"], check=False)
        r = run(uv + ["pip", "install", "paddlepaddle-gpu",
                       "--extra-index-url", PADDLE_IDX, "--quiet"])
        if r.returncode != 0:
            print("  [ERROR] paddlepaddle-gpu install failed", flush=True)
            failed = True
        else:
            print("  paddlepaddle-gpu installed", flush=True)

    # ── Step 3: restore paddlepaddle dist metadata (PaddleX needs this name) ──
    if not failed and not pkg_installed("paddlepaddle"):
        print("  Restoring paddlepaddle dist metadata...", flush=True)
        run(uv + ["pip", "install", "paddlepaddle", "--no-deps", "--quiet"])

    # ── Step 4: verify ──
    print("  Verifying GPU...", flush=True)
    ok = True
    try:
        r = run([sys.executable, "-c",
                 "import torch; assert torch.cuda.is_available(); "
                 "print(f'  torch GPU: {torch.cuda.get_device_name(0)}')"])
        if r.returncode != 0:
            print("  [WARN] torch GPU not available", flush=True)
            ok = False
    except Exception:
        ok = False

    try:
        r = run([sys.executable, "-c",
                 "import paddle; ok=paddle.device.is_compiled_with_cuda(); "
                 "print(f'  paddle CUDA: {ok}'); "
                 "assert ok, 'paddle no CUDA'"])
        if r.returncode != 0:
            print("  [WARN] paddle has no CUDA support", flush=True)
        else:
            print("  PaddlePaddle GPU OK", flush=True)
    except Exception:
        pass

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
