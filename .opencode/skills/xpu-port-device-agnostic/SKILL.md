---
name: xpu-port-device-agnostic
description: Rules for rewriting CUDA-only PyTorch test code so the same test runs on CUDA and XPU (Intel GPU) - torch.cuda.* to torch.accelerator / torch.get_device_module, hardcoded "cuda" strings, onlyCUDA/requires_cuda gates, instantiate_device_type_tests allow_xpu/only_for, CUDA-only (category C) APIs that must stay onlyCUDA, and skip/xfail hygiene. Use when porting a test file to XPU, when the enable-xpu-tests workflow reaches the port step, or when asked to make a test device-agnostic. Driven by the pinned device API catalog in enable-xpu-tests/references.
---

# Port a test to device-agnostic code

Goal: the test body is identical on CUDA and XPU; device differences live only
in decorators. Start from the precheck plan (`enable-xpu-tests/tools/precheck.py`),
which classifies every `torch.cuda.*` call against the pinned catalog
`enable-xpu-tests/references/device_api_catalog.yaml` (first match wins C > B > A).

## API rewrite by catalog category

**Category A -> `torch.accelerator`** (drop-in):

| CUDA | device-agnostic |
|---|---|
| `torch.cuda.is_available()` | `torch.accelerator.is_available()` |
| `torch.cuda.device_count()` | `torch.accelerator.device_count()` |
| `torch.cuda.synchronize()` | `torch.accelerator.synchronize()` |
| `torch.cuda.current_stream()` / `set_stream` | `torch.accelerator.current_stream()` / `set_stream` |
| `torch.cuda.empty_cache()` | `torch.accelerator.empty_cache()` |
| `torch.cuda.memory_allocated/max_memory_allocated/memory_reserved/max_memory_reserved/memory_stats/reset_peak_memory_stats/reset_accumulated_memory_stats` | same name under `torch.accelerator` |

Names that **differ** (most common mistake):

| CUDA | device-agnostic |
|---|---|
| `torch.cuda.current_device()` | `torch.accelerator.current_device_index()` |
| `torch.cuda.set_device(i)` | `torch.accelerator.set_device_index(i)` |
| `with torch.cuda.device(i):` | `with torch.accelerator.device_index(i):` |
| `torch.cuda.mem_get_info()` | `torch.accelerator.get_memory_info()` |
| `torch.cuda.CUDAGraph()` | `torch.accelerator.Graph()` (or `torch.get_device_module(device).XPUGraph`) |

**Category B with xpu in its backend list -> `torch.get_device_module(device).<api>`**:
`Stream`, `Event`, `stream`, `StreamContext`, `manual_seed(_all)`, `get/set_rng_state`,
`get_device_name`, `get_device_properties`, `is_bf16_supported`, `graph`,
`graph_pool_handle`, `make_graphed_callables`, `MemPool`, `use_mem_pool`,
`memory_snapshot`, `set_per_process_memory_fraction`, ... Use the test's
`device` / `self.device` (or `GPU_TYPE` in inductor tests) to pick the module.

**Category B without xpu** (`default_stream`, `set_sync_debug_mode`, ...) and
**category C** (nvtx, nccl, jiterator, tunable, gds, green contexts, cudart, IPC,
pinned/host-memory stats, `get_device_capability` tuples, typed tensors/storages,
`torch.cuda.amp.*`, sanitizer, raw allocator, external streams): **do not
rewrite**. These cases still go through the gate-removed measurement (to record
the real error), and the final code keeps `@onlyCUDA` on that test. The verdict
(usually Not Applicable with a wontfix issue) is recorded in `cases.json`.

`torch.cuda.amp.autocast(...)` -> `torch.autocast(device_type=..., ...)` is the one
category C item with a direct modern equivalent; prefer it when the test is not
about the deprecated API itself.

## Device strings, decorators, instantiation

- Hardcoded `"cuda"` / `device="cuda"` / `.cuda()` -> `device` / `self.device` /
  `.to(device)`. Inductor tests: `GPU_TYPE` from `torch.testing._internal.inductor_utils`.
  `GPU_TYPE`, `HAS_GPU`, `HAS_GPU_AND_TRITON`, `requires_gpu` are for **inductor
  tests only**.
- Capability checks: `torch.cuda.get_device_capability()` returns `(major, minor)`;
  on XPU the accelerator version returns a dict. Do not compare them; mirror the
  library's own feature check (e.g. a `PLATFORM_SUPPORTS_*` flag) and add a
  matching `skipXPUIf` when needed.
- `@onlyCUDA` -> `@onlyAccelerator` only when the test uses category A/B APIs
  only. Keep `@onlyNativeDeviceTypes` as is (it includes CPU).
- `instantiate_device_type_tests(Cls, globals())`: add `allow_xpu=True`; with
  `only_for=("cuda",)` add `"xpu"`. A plain `TestCase` class with
  `@requires_cuda` / `if not TEST_CUDA: skip` -> gate on the accelerator instead
  (`torch.accelerator.is_available()`), or convert to a device-generic class when
  the file already uses `instantiate_device_type_tests`.
- For every CUDA-conditional decorator (`dtypesIfCUDA`, `skipCUDAIf`,
  `largeTensorTest(device="cuda")`, `precisionOverride`, `toleranceOverride`
  keyed to cuda) mirror the XPU counterpart with the same scope -- run the
  `xpu-test-decorator-parity` checklist on the file.

## Skip / xfail hygiene

- For the gate-removed run, remove **every** XPU gate on the case:
  `skipIfXpu`, `skipXPUIf`, `skipXPU`, `expectedFailureXPU`, xpu `DecorateInfo` in
  `op_db` (`torch/testing/_internal/common_methods_invocations.py` and friends),
  `onlyCUDA`, `requires_cuda`, `unittest.skipIf(not TEST_CUDA ...)`, conditional
  `self.skipTest(...)` in the body.
- A gate re-added after measuring has `msg` set to **just the issue URL**, e.g.
  `@skipIfXpu(msg="https://github.com/intel/torch-xpu-ops/issues/1234")`; for an
  undrafted issue use `.../issues/PLACEHOLDER` or the draft's unique placeholder
  token (`.../issues/XXXX_SHORT_NAME`). The root cause goes in `cases.json` `reason`,
  not in the decorator.
- `@skipIfXpu` is host-wide: it also skips CPU instantiations on a machine that has
  an XPU. When only the XPU variant should be skipped, use `@skipXPUIf(True, msg)`.
- Prefer `expectedFailureXPU` / `DecorateInfo(unittest.expectedFailure, ...,
  device_type="xpu")` over a skip when the test runs and fails from a bug, so it
  flips green when fixed.
- Remove a stale skip only when its issue is confirmed closed/fixed and the case
  passes. When a skip goes away, delete its orphaned comment and any import that
  is now unused.
- Between the gate-removed run and the final code, change **only gate lines**
  (decorators, their msg, `DecorateInfo` entries, restored `onlyCUDA`). Any other
  edit after measuring invalidates the evidence (verify_evidence R7): re-measure.

## Checklist before measuring

- [ ] No `torch.cuda.` left except category C / B-without-xpu calls under `onlyCUDA`.
- [ ] No hardcoded `"cuda"` outside CUDA-only tests.
- [ ] XPU variant is instantiated (`allow_xpu=True` / `only_for` / `GPU_TYPE`).
- [ ] Decorator parity checked.
- [ ] All XPU gates on the target cases removed for the gate-removed run.
