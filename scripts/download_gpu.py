"""Install GPU packages for ORCP.

Called by setup_gpu.bat:
    uv run python scripts/download_gpu.py <TORCH_IDX> <PADDLE_IDX>
"""

import subprocess
import sys


def run(args, check=True):
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.stdout.strip():
        print(r.stdout.strip(), flush=True)
    if r.returncode != 0 and check:
        print(f"  FAILED: {r.stderr[:200]}", flush=True)
    return r


def main():
    if len(sys.argv) < 3:
        print("Usage: download_gpu.py <TORCH_IDX> <PADDLE_IDX>", flush=True)
        sys.exit(1)

    torch_idx = sys.argv[1]
    paddle_idx = sys.argv[2]
    uv = ["uv"]
    ok = True

    # -- GPU torch --
    print("  Installing torch (CUDA 12.6)...", flush=True)
    r = run(uv + ["pip", "install", "torch", "torchvision", "torchaudio",
                   "--index-url", torch_idx])
    # Verify torch actually has CUDA (index may lack Windows wheels)
    check = run([sys.executable, "-c",
                 "import torch; assert '+cpu' not in torch.__version__, 'CPU-only'; "
                 "assert torch.cuda.is_available(), 'CUDA N/A'; "
                 "print(f'  torch {torch.__version__} CUDA OK')"], check=False)
    if check.returncode != 0:
        print("  [WARN] torch CUDA not available - PaddleOCR GPU still works via paddle",
              flush=True)
    ok = True  # torch CUDA is optional; paddle CUDA is the priority

    # -- paddlepaddle-gpu --
    if ok:
        print("  Installing paddlepaddle-gpu...", flush=True)
        run(uv + ["pip", "uninstall", "paddlepaddle-gpu"], check=False)
        r2 = run(uv + ["pip", "install", "paddlepaddle-gpu",
                        "--extra-index-url", paddle_idx, "--quiet"])
        ok = r2.returncode == 0
        print("  paddlepaddle-gpu OK" if ok else "  paddle FAILED", flush=True)

    # -- Restore paddlepaddle dist metadata (PaddleX needs this package name) --
    if ok:
        r3 = run(uv + ["pip", "show", "paddlepaddle"], check=False)
        if r3.returncode != 0:
            run(uv + ["pip", "install", "paddlepaddle", "--no-deps", "--quiet"])
            print("  dist metadata restored", flush=True)

    # -- Verify --
    print("  Verifying GPU...", flush=True)
    run([sys.executable, "-c", "import torch; print('  torch CUDA:', torch.cuda.is_available())"])
    run([sys.executable, "-c", "import paddle; print('  paddle CUDA:', paddle.device.is_compiled_with_cuda())"])

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
