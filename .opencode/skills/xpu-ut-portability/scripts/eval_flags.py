#!/usr/bin/env python3
"""Evaluate PLATFORM_SUPPORTS_* flags live on the target XPU box.

Run inside the conda env that has torch built with XPU, e.g.:
    source ~/miniforge3/etc/profile.d/conda.sh && conda activate any_gate
    python eval_flags.py

Prints each flag's boolean value so classification reflects ground truth rather
than assumptions. Flags that are absent on this torch build are reported as
"(missing)".
"""
import torch  # noqa: F401  (import side effect: registers xpu)

FLAGS = [
    "PLATFORM_SUPPORTS_FLASH_ATTENTION",
    "PLATFORM_SUPPORTS_MEM_EFF_ATTENTION",
    "PLATFORM_SUPPORTS_CUDNN_ATTENTION",
    "PLATFORM_SUPPORTS_FUSED_ATTENTION",
    "PLATFORM_SUPPORTS_FUSED_SDPA",
    "PLATFORM_SUPPORTS_CK_SDPA",
    "PLATFORM_SUPPORTS_BF16",
    "PLATFORM_SUPPORTS_FP8",
    "PLATFORM_SUPPORTS_FP8_GROUPED_GEMM",
    "PLATFORM_SUPPORTS_MXFP8_GROUPED_GEMM",
    "PLATFORM_SUPPORTS_MX_GEMM",
    "PLATFORM_SUPPORTS_FP8_SPARSE",
    "PLATFORM_SUPPORTS_BF16_ATOMICS",
    "PLATFORM_SUPPORTS_HALF_ATOMICS",
    "PLATFORM_SUPPORTS_GREEN_CONTEXT",
    "PLATFORM_SUPPORTS_WORKQUEUE_CONFIG",
]


def main():
    print("xpu available:", torch.xpu.is_available(),
          "devices:", torch.xpu.device_count() if torch.xpu.is_available() else 0)
    try:
        import torch.testing._internal.common_cuda as cc
    except Exception as e:  # noqa: BLE001
        print("could not import common_cuda:", e)
        cc = None
    try:
        import torch.testing._internal.common_xpu as cx
    except Exception:
        cx = None
    for f in FLAGS:
        val = None
        for mod in (cx, cc):
            if mod is not None and hasattr(mod, f):
                val = getattr(mod, f)
                break
        print(f"{f}: {'(missing)' if val is None else bool(val)}")


if __name__ == "__main__":
    main()
