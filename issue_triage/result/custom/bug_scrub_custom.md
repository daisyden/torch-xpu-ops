# XPU Ops Bug Scrub Report — custom selection (19 issues)

- **Repository**: `intel/torch-xpu-ops`
- **Generated**: 2026-06-03 (cutoff for Section 6: 2026-05-27)
- **Total issues in workbook**: 19
- **Classified (non-empty `AR`)**: 19
- **Empty `AR` (no verdict)**: 0

## 1. Summary

This report groups the 19 tracked torch-xpu-ops issues by the `AR` (Action Required) column in the workbook. An issue may appear in multiple AR buckets if its `AR` cell contains more than one value (joined with `; `). Cross-cutting slices (duplicated issues, external dependency blockers, newly filed issues, stale requests) are listed separately for visibility.

**Headline counts (multi-membership — an issue with N AR values is counted N times):**

| AR Bucket | Issues |
|---|---:|
| Close/Skip | 3 |
| Need Owner | 0 |
| Land PR | 10 |
| Wait for PR | 2 |
| Need Response | 3 |
| Need check case existence | 7 |
| Verify | 6 |
| UNCLASSIFIED | 0 |
| Duplicated | 0 |
| External dependency (non-upstream-pytorch, non-SYCL-kernel) | 3 |
| Upstream-pytorch | 1 |
| CPU fallback | 2 |
| Filed within last 7 days | 0 |
| Requests pending > 1 week | 2 |

<a id="sec-2"></a>
## 2. Index

- [3. Action Required (by AR bucket)](#sec-3)
  - [UNCLASSIFIED](#sec-3-0-unclassified)
  - [Close/Skip](#sec-3-1-closeskip)
  - [Need Owner](#sec-3-2-need-owner)
  - [Land PR](#sec-3-3-land-pr)
  - [Wait for PR](#sec-3-4-wait-for-pr)
  - [Need Response](#sec-3-5-need-response)
  - [Need check case existence](#sec-3-6-need-check-case-existence)
  - [Verify](#sec-3-7-verify)
- [4. Duplicated issues](#sec-4)
- [5. Dependency (external blockers)](#sec-5)
  - [Third Parties](#sec-5-1-third-parties)
  - [upstream-pytorch](#sec-5-2-upstream-pytorch)
  - [CPU fallback](#sec-5-3-cpu-fallback)
- [6. New submitted issues (<7 days)](#sec-6)
- [7. Requests pending > 1 week](#sec-7)
- [8. Statistics](#sec-8)

<a id="sec-3"></a>
## 3. Action Required (by AR bucket)

_[↑ Back to Index](#sec-2)_

Issues are grouped by the `AR` column from the Issues sheet. Each issue appears in every AR bucket it lists. Rows inside each bucket are split by `Category` (existing taxonomy column); rows within a category table are sorted by `Priority` (P0 → P3).

Issues whose `Dependency` is a third-party blocker (`oneDNN` / `oneMKL` / `oneAPI` / `triton` / `driver` / `xccl`) are hidden here and listed only under §5 Dependency, except when their AR includes `Land PR` or `Wait for PR` (a live next action makes the row actionable).

<a id="sec-3-0-unclassified"></a>
- **UNCLASSIFIED**  ·  0 issues

_[↑ Back to Index](#sec-2)_

**UNCLASSIFIED — Phase 4d produced no AR verdict; should be empty after AR backfill**

_No issues._


<a id="sec-3-1-closeskip"></a>
- **Close/Skip**  ·  3 issues

**Close/Skip — terminal QA action (close fixed, verify-and-close merged fix, skip not-target / wontfix)**

<a id="sec-3-1-1-flash-attention"></a>
#### 3.1.1 Flash Attention  ·  1 issues

_[↑ Back to Index](#sec-2)_

| Issue | Title | Owner | Owner Transferred | action_TBD | Fix Approach | Priority | action_reason | Reporter | Labels |
|---|---|---|---|---|---|---|---|---|---|
| [#2285](https://github.com/intel/torch-xpu-ops/issues/2285) | Support efficient attention | daisyden | daisyden | <ul><li>Skip issue</li><li>check_case_avaliablity</li></ul> | - - - Implement and register aten::_efficient_attention_forward / _backward for XPU in torch-xpu-op…<br>[→ details](details/2285.md) | P2 | not target feature | daisyden | skipped, not_target |


<a id="sec-3-1-2-others"></a>
#### 3.1.2 Others  ·  2 issues

_[↑ Back to Index](#sec-2)_

| Issue | Title | Owner | Owner Transferred | action_TBD | Fix Approach | Priority | action_reason | Reporter | Labels |
|---|---|---|---|---|---|---|---|---|---|
| [#2531](https://github.com/intel/torch-xpu-ops/issues/2531) | [upstream_ut] AssertionError: Torch not compiled<br>with CUDA enabled | daisyden | daisyden | <ul><li>Skip issue</li><li>check_case_avaliablity</li></ul> | - - - Port each test in third_party/torch-xpu-ops/test/xpu/ to substitute xpu for cuda (use TEST_XP…<br>[→ details](details/2531.md) | P2 | not target feature | daisyden | skipped, port_from_skiplist, not_target |
| [#2436](https://github.com/intel/torch-xpu-ops/issues/2436) | [upstream_ut] AttributeError: 'NoneType' object<br>has no attribute 'clone' | daisyden | daisyden | <ul><li>Close the fixed issue; Add label 'dependency component: upstream-pytorch' - ExpandedWeights input.grad None is tracked as upstream PyTorch test-design bug reproducing beyond XPU.; Wait for dependency fix [pytorch/pytorch#97395](https://github.com/pytorch/pytorch/pull/97395)</li></ul> | - - - Keep cases in skip list as 'random/community'. - track upstream pytorch/pytorch#97395 for the…<br>[→ details](details/2436.md) | P3 | Fixed and passed in CI | daisyden | skipped, dependency component: communit… |


<a id="sec-3-2-need-owner"></a>
- **Need Owner**  ·  0 issues

**Need Owner — awaiting triage-lead to assign an owner**

<a id="sec-3-3-land-pr"></a>
- **Land PR**  ·  10 issues

**Land PR — numbered PR in action_TBD is the next concrete action**

<a id="sec-3-3-1-flash-attention"></a>
#### 3.3.1 Flash Attention  ·  4 issues

_[↑ Back to Index](#sec-2)_

| Issue | Title | Owner | Owner Transferred | action_TBD | Fix Approach | Priority | action_reason | Reporter | Labels |
|---|---|---|---|---|---|---|---|---|---|
| [#2270](https://github.com/intel/torch-xpu-ops/issues/2270) | Backend Compatibility Error in<br>test/xpu/test_decomp_xpu.py | LuFinch | LuFinch, libohao1201 | <ul><li>Address CI failures on PR [pytorch/pytorch#181559](https://github.com/pytorch/pytorch/pull/181559)</li><li>check_case_avaliablity</li></ul> | - - - Add the test to skip_list for test_decomp_xpu (decomp cross-ref doesn't make sense for backen…<br>[→ details](details/2270.md) | P2 | pytorch/pytorch#181559 is the open verified fix path, but its current checks include failing jobs from 2026-05-28, so CI is the highest-pri… | libohao1201 | module: ut, skipped |
| [#2698](https://github.com/intel/torch-xpu-ops/issues/2698) | Title: [upstream_ut] RuntimeError:<br>FlashAttentionForwardXPU only support headdim<br>64,96,128,192 | LuFinch | LuFinch | <ul><li>Address CI failures on PR [pytorch/pytorch#180646](https://github.com/pytorch/pytorch/pull/180646)</li></ul> | - - - In mha_fwd.cpp, instead of TORCH_CHECK(false, ...), return a status indicating unsupported he…<br>[→ details](details/2698.md) | P2 | The highest-priority live fixing PR is open pytorch/pytorch#180646; despite approval, its status rollup has failing XPU build/test checks c… | daisyden | module: inductor, skipped, ut_upstream |
| [#3140](https://github.com/intel/torch-xpu-ops/issues/3140) | [upstream_ut] RuntimeError:<br>FlashAttentionForwardXPU does not only support<br>dropout > 0.0 yet | LuFinch | LuFinch, daisyden | <ul><li>Address CI failures on PR [intel/torch-xpu-ops#3766](https://github.com/intel/torch-xpu-ops/pull/3766)</li><li>check_case_avaliablity</li></ul> | - - - Either (a) implement dropout support in mha_fwd.cpp / mha_bwd.cpp sycltla kernels, or (b) fix…<br>[→ details](details/3140.md) | P2 | Assignee LuFinch linked PR intel/torch-xpu-ops#3766 as the dropout implementation fix, and its latest required failing checks completed on … | daisyden | module: ut, skipped, ut_upstream |
| [#2853](https://github.com/intel/torch-xpu-ops/issues/2853) | [upstream_ut]<br>torch.ops.aten._flash_attention_forward lack of<br>support for XPU. | LuFinch | LuFinch, BBBela | <ul><li>Verify fix from merged PR [intel/torch-xpu-ops#3404](https://github.com/intel/torch-xpu-ops/pull/3404) and close</li><li>Address CI failures on PR [pytorch/pytorch#181559](https://github.com/pytorch/pytorch/pull/181559)</li><li>check_case_avaliablity</li></ul> | - - - Either (a) register an XPU kernel for _flash_attention_forward in torch-xpu-ops (transformers…<br>[→ details](details/2853.md) | P3 | The torch-xpu-ops implementation PR #3404 is verified and merged, so the reporter should verify that portion of the fix. \| The upstream Py… | BBBela | skipped |


<a id="sec-3-3-2-inductor"></a>
#### 3.3.2 Inductor  ·  2 issues

_[↑ Back to Index](#sec-2)_

| Issue | Title | Owner | Owner Transferred | action_TBD | Fix Approach | Priority | action_reason | Reporter | Labels |
|---|---|---|---|---|---|---|---|---|---|
| [#2997](https://github.com/intel/torch-xpu-ops/issues/2997) | AssertionError of test_linear_and_cel_max_autotune | daisyden | daisyden | <ul><li>Address CI failures on PR [pytorch/pytorch#181822](https://github.com/pytorch/pytorch/pull/181822)</li></ul> | - - - Bisect: disable inplace-padding (config.inplace_padding=False) and disable oneDNN-backed bf16…<br>[→ details](details/2997.md) | P2 | The highest-priority actionable verified fixing/re-enablement PR is open pytorch/pytorch#181822; GraphQL statusCheckRollup shows FAILURE wi… | daisyden | module: inductor, ut_upstream |
| [#3095](https://github.com/intel/torch-xpu-ops/issues/3095) | cutlass support blocks some unit test cases | tszulist-hbn | tszulist-hbn | <ul><li>Wait for review on PR [pytorch/pytorch#183530](https://github.com/pytorch/pytorch/pull/183530)</li></ul> | - - - Either (a) add device skip markers for XPU on test_cudacodecache.py upstream until an Inducto…<br>[→ details](details/3095.md) | P2 | PR pytorch/pytorch#183530 is an open verified fix with passing visible checks, but reviewDecision is not APPROVED; the review clock uses th… | daisyden | module: inductor, ut_upstream |


<a id="sec-3-3-3-others"></a>
#### 3.3.3 Others  ·  1 issues

_[↑ Back to Index](#sec-2)_

| Issue | Title | Owner | Owner Transferred | action_TBD | Fix Approach | Priority | action_reason | Reporter | Labels |
|---|---|---|---|---|---|---|---|---|---|
| [#2783](https://github.com/intel/torch-xpu-ops/issues/2783) | [Bug Skip]: Key "xpu" is missing from dict<br>"driver" in test_svd | daisyden | daisyden | <ul><li>Address CI failures on PR [pytorch/pytorch#181822](https://github.com/pytorch/pytorch/pull/181822)</li></ul> | - - - Upstream patch in pytorch/test/test_linalg.py: add an 'xpu' entry to the SVD driver dict (or…<br>[→ details](details/2783.md) | P3 | Open verified fixing PR pytorch/pytorch#181822 is approved but has failing required/status checks, with the latest failing XPU build on 202… | CuiYifeng | module: ut, skipped |


<a id="sec-3-3-4-sparse"></a>
#### 3.3.4 Sparse  ·  1 issues

_[↑ Back to Index](#sec-2)_

| Issue | Title | Owner | Owner Transferred | action_TBD | Fix Approach | Priority | action_reason | Reporter | Labels |
|---|---|---|---|---|---|---|---|---|---|
| [#2229](https://github.com/intel/torch-xpu-ops/issues/2229) | test/test_sparse_csr.py::TestSparseCompressedCPU::<br>test_invalid_input meet message not match | chunhuanMeng | chunhuanMeng | <ul><li>Land PR [intel/torch-xpu-ops#3713](https://github.com/intel/torch-xpu-ops/pull/3713)</li></ul> | - - - Implement aten::_validate_compressed_sparse_indices for the XPU backend in torch-xpu-ops so t…<br>[→ details](details/2229.md) | P3 | PR intel/torch-xpu-ops#3713 is the open, approved replacement for the closed unmerged fixing PR and has no failing checks in the live state… | wincent8 | skipped |


<a id="sec-3-3-5-torch-ops---eltwise"></a>
#### 3.3.5 Torch Ops - eltwise  ·  1 issues

_[↑ Back to Index](#sec-2)_

| Issue | Title | Owner | Owner Transferred | action_TBD | Fix Approach | Priority | action_reason | Reporter | Labels |
|---|---|---|---|---|---|---|---|---|---|
| [#2376](https://github.com/intel/torch-xpu-ops/issues/2376) | [Bug Skip]: NotImplementedError: "logaddexp_xpu"<br>not implemented for 'Complex' | daisyden | daisyden, mengfei25 | <ul><li>Verify fix from merged PR [intel/torch-xpu-ops#2807](https://github.com/intel/torch-xpu-ops/pull/2807) and close</li><li>Wait for review on PR [laifenxiawucha/pytorch#6](https://github.com/laifenxiawucha/pytorch/pull/6) (>1 week)</li><li>check_case_avaliablity</li></ul> | - - - Extend LogAddExpKernels.cpp dispatch to AT_DISPATCH_FLOATING_AND_COMPLEX_TYPES_AND* (mirrorin…<br>[→ details](details/2376.md) | P1 | intel/torch-xpu-ops#2807 is a merged verified kernel fix for complex logaddexp on XPU. \| laifenxiawucha/pytorch#6 is an open verified foll… | mengfei25 | module: ut, skipped |


<a id="sec-3-3-6-torch-ops---gemm"></a>
#### 3.3.6 Torch Ops - gemm  ·  1 issues

_[↑ Back to Index](#sec-2)_

| Issue | Title | Owner | Owner Transferred | action_TBD | Fix Approach | Priority | action_reason | Reporter | Labels |
|---|---|---|---|---|---|---|---|---|---|
| [#2253](https://github.com/intel/torch-xpu-ops/issues/2253) | the supported dtypes are not align with cuda | daisyden | daisyden | <ul><li>Wait for review on PR [intel-sandbox/torch-xpu-ops-exp#1700](https://github.com/intel-sandbox/torch-xpu-ops-exp/pull/1700) (>1 week)</li></ul> | - - - Either expand XPU op registrations to cover the same dtype set as CUDA (e.g. addmm/addmv/badd…<br>[→ details](details/2253.md) | P2 | The open verified fixing PR has no checks reported and no approval; the most recent commit was pushed on 2026-05-13, so the review gate is … | daisyden | duplicate, skipped, ut_upstream, agent:… |


<a id="sec-3-4-wait-for-pr"></a>
- **Wait for PR**  ·  2 issues

**Wait for PR — fix path is known but no PR is filed yet; awaiting PR submission (or external non-PR tracker)**

<a id="sec-3-4-1-distributed"></a>
#### 3.4.1 Distributed  ·  1 issues

_[↑ Back to Index](#sec-2)_

| Issue | Title | Owner | Owner Transferred | action_TBD | Fix Approach | Priority | action_reason | Reporter | Labels |
|---|---|---|---|---|---|---|---|---|---|
| [#2958](https://github.com/intel/torch-xpu-ops/issues/2958) | AssertionError of test_dtensor_basic_compile | daisyden | daisyden | <ul><li>Wait for fix PR</li></ul> | - - - Re-run on current main to confirm. - if still failing, regenerate the expected inline output…<br>[→ details](details/2958.md) | P3 | The assignee provided a concrete branch/commit and verification for removing the XPU skip, but no verified public PR was discovered. | daisyden | module: inductor, ut_upstream |


<a id="sec-3-4-2-inductor"></a>
#### 3.4.2 Inductor  ·  1 issues

_[↑ Back to Index](#sec-2)_

| Issue | Title | Owner | Owner Transferred | action_TBD | Fix Approach | Priority | action_reason | Reporter | Labels |
|---|---|---|---|---|---|---|---|---|---|
| [#3094](https://github.com/intel/torch-xpu-ops/issues/3094) | XPUGraph tree support | BBBela | BBBela | <ul><li>Wait for fix PR; Add label 'dependency component: upstream-pytorch' - CUDAGraph tree tests require XPUGraph Tree implementation under active PyTorch RFC work.; Wait for dependency fix [pytorch/pytorch#180168](https://github.com/pytorch/pytorch/pull/180168)</li></ul> | - - - Implement XPUGraph (SYCL graph based capture/replay) runtime APIs in torch/xpu and an XPU bra…<br>[→ details](details/3094.md) | P2 | Assignee BBBela stated XPUGraph Tree support is under development, linked the PyTorch RFC, and plans to follow the implementation before ch… | daisyden | module: inductor, ut_upstream |


<a id="sec-3-5-need-response"></a>
- **Need Response**  ·  0 issues

**Need Response — owner / reporter must answer an open question (or no response yet on a new issue)**

<a id="sec-3-6-need-check-case-existence"></a>
- **Need check case existence**  ·  6 issues

**Need check case existence — XPU test case missing in repo; QA must verify case existence before action**

<a id="sec-3-6-1-flash-attention"></a>
#### 3.6.1 Flash Attention  ·  4 issues

_[↑ Back to Index](#sec-2)_

| Issue | Title | Owner | Owner Transferred | action_TBD | Fix Approach | Priority | action_reason | Reporter | Labels |
|---|---|---|---|---|---|---|---|---|---|
| [#2270](https://github.com/intel/torch-xpu-ops/issues/2270) | Backend Compatibility Error in<br>test/xpu/test_decomp_xpu.py | LuFinch | LuFinch, libohao1201 | <ul><li>Address CI failures on PR [pytorch/pytorch#181559](https://github.com/pytorch/pytorch/pull/181559)</li><li>check_case_avaliablity</li></ul> | - - - Add the test to skip_list for test_decomp_xpu (decomp cross-ref doesn't make sense for backen…<br>[→ details](details/2270.md) | P2 | pytorch/pytorch#181559 is the open verified fix path, but its current checks include failing jobs from 2026-05-28, so CI is the highest-pri… | libohao1201 | module: ut, skipped |
| [#2285](https://github.com/intel/torch-xpu-ops/issues/2285) | Support efficient attention | daisyden | daisyden | <ul><li>Skip issue</li><li>check_case_avaliablity</li></ul> | - - - Implement and register aten::_efficient_attention_forward / _backward for XPU in torch-xpu-op…<br>[→ details](details/2285.md) | P2 | not target feature | daisyden | skipped, not_target |
| [#3140](https://github.com/intel/torch-xpu-ops/issues/3140) | [upstream_ut] RuntimeError:<br>FlashAttentionForwardXPU does not only support<br>dropout > 0.0 yet | LuFinch | LuFinch, daisyden | <ul><li>Address CI failures on PR [intel/torch-xpu-ops#3766](https://github.com/intel/torch-xpu-ops/pull/3766)</li><li>check_case_avaliablity</li></ul> | - - - Either (a) implement dropout support in mha_fwd.cpp / mha_bwd.cpp sycltla kernels, or (b) fix…<br>[→ details](details/3140.md) | P2 | Assignee LuFinch linked PR intel/torch-xpu-ops#3766 as the dropout implementation fix, and its latest required failing checks completed on … | daisyden | module: ut, skipped, ut_upstream |
| [#2853](https://github.com/intel/torch-xpu-ops/issues/2853) | [upstream_ut]<br>torch.ops.aten._flash_attention_forward lack of<br>support for XPU. | LuFinch | LuFinch, BBBela | <ul><li>Verify fix from merged PR [intel/torch-xpu-ops#3404](https://github.com/intel/torch-xpu-ops/pull/3404) and close</li><li>Address CI failures on PR [pytorch/pytorch#181559](https://github.com/pytorch/pytorch/pull/181559)</li><li>check_case_avaliablity</li></ul> | - - - Either (a) register an XPU kernel for _flash_attention_forward in torch-xpu-ops (transformers…<br>[→ details](details/2853.md) | P3 | The torch-xpu-ops implementation PR #3404 is verified and merged, so the reporter should verify that portion of the fix. \| The upstream Py… | BBBela | skipped |


<a id="sec-3-6-2-others"></a>
#### 3.6.2 Others  ·  1 issues

_[↑ Back to Index](#sec-2)_

| Issue | Title | Owner | Owner Transferred | action_TBD | Fix Approach | Priority | action_reason | Reporter | Labels |
|---|---|---|---|---|---|---|---|---|---|
| [#2531](https://github.com/intel/torch-xpu-ops/issues/2531) | [upstream_ut] AssertionError: Torch not compiled<br>with CUDA enabled | daisyden | daisyden | <ul><li>Skip issue</li><li>check_case_avaliablity</li></ul> | - - - Port each test in third_party/torch-xpu-ops/test/xpu/ to substitute xpu for cuda (use TEST_XP…<br>[→ details](details/2531.md) | P2 | not target feature | daisyden | skipped, port_from_skiplist, not_target |


<a id="sec-3-6-3-torch-ops---eltwise"></a>
#### 3.6.3 Torch Ops - eltwise  ·  1 issues

_[↑ Back to Index](#sec-2)_

| Issue | Title | Owner | Owner Transferred | action_TBD | Fix Approach | Priority | action_reason | Reporter | Labels |
|---|---|---|---|---|---|---|---|---|---|
| [#2376](https://github.com/intel/torch-xpu-ops/issues/2376) | [Bug Skip]: NotImplementedError: "logaddexp_xpu"<br>not implemented for 'Complex' | daisyden | daisyden, mengfei25 | <ul><li>Verify fix from merged PR [intel/torch-xpu-ops#2807](https://github.com/intel/torch-xpu-ops/pull/2807) and close</li><li>Wait for review on PR [laifenxiawucha/pytorch#6](https://github.com/laifenxiawucha/pytorch/pull/6) (>1 week)</li><li>check_case_avaliablity</li></ul> | - - - Extend LogAddExpKernels.cpp dispatch to AT_DISPATCH_FLOATING_AND_COMPLEX_TYPES_AND* (mirrorin…<br>[→ details](details/2376.md) | P1 | intel/torch-xpu-ops#2807 is a merged verified kernel fix for complex logaddexp on XPU. \| laifenxiawucha/pytorch#6 is an open verified foll… | mengfei25 | module: ut, skipped |


<a id="sec-3-7-verify"></a>
- **Verify**  ·  6 issues

**Verify — referenced PR in action_TBD has merged AND owner_transferred=Reporter; reporter must verify the fix and confirm closure**

<a id="sec-3-7-1-distributed"></a>
#### 3.7.1 Distributed  ·  1 issues

_[↑ Back to Index](#sec-2)_

| Issue | Title | Owner | Owner Transferred | action_TBD | Fix Approach | Priority | action_reason | Reporter | Labels |
|---|---|---|---|---|---|---|---|---|---|
| [#2958](https://github.com/intel/torch-xpu-ops/issues/2958) | AssertionError of test_dtensor_basic_compile | daisyden | daisyden | <ul><li>Wait for fix PR</li></ul> | - - - Re-run on current main to confirm. - if still failing, regenerate the expected inline output…<br>[→ details](details/2958.md) | P3 | The assignee provided a concrete branch/commit and verification for removing the XPU skip, but no verified public PR was discovered. | daisyden | module: inductor, ut_upstream |


<a id="sec-3-7-2-flash-attention"></a>
#### 3.7.2 Flash Attention  ·  3 issues

_[↑ Back to Index](#sec-2)_

| Issue | Title | Owner | Owner Transferred | action_TBD | Fix Approach | Priority | action_reason | Reporter | Labels |
|---|---|---|---|---|---|---|---|---|---|
| [#2270](https://github.com/intel/torch-xpu-ops/issues/2270) | Backend Compatibility Error in<br>test/xpu/test_decomp_xpu.py | LuFinch | LuFinch, libohao1201 | <ul><li>Address CI failures on PR [pytorch/pytorch#181559](https://github.com/pytorch/pytorch/pull/181559)</li><li>check_case_avaliablity</li></ul> | - - - Add the test to skip_list for test_decomp_xpu (decomp cross-ref doesn't make sense for backen…<br>[→ details](details/2270.md) | P2 | pytorch/pytorch#181559 is the open verified fix path, but its current checks include failing jobs from 2026-05-28, so CI is the highest-pri… | libohao1201 | module: ut, skipped |
| [#3140](https://github.com/intel/torch-xpu-ops/issues/3140) | [upstream_ut] RuntimeError:<br>FlashAttentionForwardXPU does not only support<br>dropout > 0.0 yet | LuFinch | LuFinch, daisyden | <ul><li>Address CI failures on PR [intel/torch-xpu-ops#3766](https://github.com/intel/torch-xpu-ops/pull/3766)</li><li>check_case_avaliablity</li></ul> | - - - Either (a) implement dropout support in mha_fwd.cpp / mha_bwd.cpp sycltla kernels, or (b) fix…<br>[→ details](details/3140.md) | P2 | Assignee LuFinch linked PR intel/torch-xpu-ops#3766 as the dropout implementation fix, and its latest required failing checks completed on … | daisyden | module: ut, skipped, ut_upstream |
| [#2853](https://github.com/intel/torch-xpu-ops/issues/2853) | [upstream_ut]<br>torch.ops.aten._flash_attention_forward lack of<br>support for XPU. | LuFinch | LuFinch, BBBela | <ul><li>Verify fix from merged PR [intel/torch-xpu-ops#3404](https://github.com/intel/torch-xpu-ops/pull/3404) and close</li><li>Address CI failures on PR [pytorch/pytorch#181559](https://github.com/pytorch/pytorch/pull/181559)</li><li>check_case_avaliablity</li></ul> | - - - Either (a) register an XPU kernel for _flash_attention_forward in torch-xpu-ops (transformers…<br>[→ details](details/2853.md) | P3 | The torch-xpu-ops implementation PR #3404 is verified and merged, so the reporter should verify that portion of the fix. \| The upstream Py… | BBBela | skipped |


<a id="sec-3-7-3-sparse"></a>
#### 3.7.3 Sparse  ·  1 issues

_[↑ Back to Index](#sec-2)_

| Issue | Title | Owner | Owner Transferred | action_TBD | Fix Approach | Priority | action_reason | Reporter | Labels |
|---|---|---|---|---|---|---|---|---|---|
| [#2283](https://github.com/intel/torch-xpu-ops/issues/2283) | [upstream_ut] sparse._sampled_addmm is not<br>supported | jenniew | daisyden | <ul><li>Verify fix from merged PR [intel/torch-xpu-ops#3018](https://github.com/intel/torch-xpu-ops/pull/3018) and close</li></ul> | - - - Either register a CPU fallback for aten::sparse_sampled_addmm on SparseCsrXPU (analogous to o…<br>[→ details](details/2283.md) | P1 | PR intel/torch-xpu-ops#3018 is a verified fixing PR for SparseCsrXPU sampled_addmm and is now merged. | daisyden | skipped, ut_upstream, agent:active, age… |


<a id="sec-3-7-4-torch-ops---eltwise"></a>
#### 3.7.4 Torch Ops - eltwise  ·  1 issues

_[↑ Back to Index](#sec-2)_

| Issue | Title | Owner | Owner Transferred | action_TBD | Fix Approach | Priority | action_reason | Reporter | Labels |
|---|---|---|---|---|---|---|---|---|---|
| [#2376](https://github.com/intel/torch-xpu-ops/issues/2376) | [Bug Skip]: NotImplementedError: "logaddexp_xpu"<br>not implemented for 'Complex' | daisyden | daisyden, mengfei25 | <ul><li>Verify fix from merged PR [intel/torch-xpu-ops#2807](https://github.com/intel/torch-xpu-ops/pull/2807) and close</li><li>Wait for review on PR [laifenxiawucha/pytorch#6](https://github.com/laifenxiawucha/pytorch/pull/6) (>1 week)</li><li>check_case_avaliablity</li></ul> | - - - Extend LogAddExpKernels.cpp dispatch to AT_DISPATCH_FLOATING_AND_COMPLEX_TYPES_AND* (mirrorin…<br>[→ details](details/2376.md) | P1 | intel/torch-xpu-ops#2807 is a merged verified kernel fix for complex logaddexp on XPU. \| laifenxiawucha/pytorch#6 is an open verified foll… | mengfei25 | module: ut, skipped |



<a id="sec-4"></a>
## 4. Duplicated issues

_[↑ Back to Index](#sec-2)_

Rows where `duplicated_issue` is set or `action_TBD` contains "duplicate of".  —  0 issues.

_No issues._


<a id="sec-5"></a>
## 5. Dependency (external blockers)

_[↑ Back to Index](#sec-2)_

Issues with a non-blank `Dependency` value, excluding `upstream-pytorch`, `CPU fallback`, and `SYCL kernel:*` (in-repo kernel pointers). Rows whose AR is `Close/Skip` are also excluded.  —  3 issues.

<a id="sec-5-1-third-parties"></a>
- **Third Parties**

_[↑ Back to Index](#sec-2)_

| Issue | Dependency | Title | Owner | Owner Transferred | action_TBD | Fix Approach | Priority | action_reason | Reporter | Labels |
|---|---|---|---|---|---|---|---|---|---|---|
| [#2329](https://github.com/intel/torch-xpu-ops/issues/2329) | Triton | [upstream_ut] feature missing: get_device_tflops<br>and get_drams_gbps | etaf | etaf | <ul><li>No action — investigate further; Wait for dependency fix [intel/intel-xpu-backend-for-triton#5792](https://github.com/intel/intel-xpu-backend-for-triton/pull/5792)</li></ul> | - - - Implement XPU branches in torch/_inductor/utils.py:get_device_tflops/get_dram_gbps using valu…<br>[→ details](details/2329.md) | P1 | No verified torch-xpu-ops or upstream PyTorch fixing PR was found for get_device_tflops/get_dram_gbps XPU support; dependency audit verbs a… | daisyden | duplicate, dependency component: Triton… |
| [#3165](https://github.com/intel/torch-xpu-ops/issues/3165) | Triton | test_sparse_csr_xpu.py::TestSparseCompressedTriton<br>KernelsXPU::test_triton_bsr_softmax meet<br>RuntimeError: ZE_RESULT_ERROR_INVALID_KERNEL_NAME | jafraustro | jafraustro | <ul><li>No action — investigate further; Wait for dependency fix [intel/intel-xpu-backend-for-triton#6872](https://github.com/intel/intel-xpu-backend-for-triton/pull/6872)</li></ul> | - - - File a Triton-XPU backend bug with the failing BSR softmax kernel and the ZE_RESULT_ERROR_INV…<br>[→ details](details/3165.md) | P2 | No verified fixing PR in torch-xpu-ops or upstream PyTorch was discovered; the external Triton dependency tracking belongs to Phase 4e rath… | CuiYifeng | dependency component: Triton, skipped, … |
| [#3142](https://github.com/intel/torch-xpu-ops/issues/3142) | oneAPI | [upstream_ut] RuntimeError: The<br>sycl_ext_oneapi_work_group_scratch_memory feature<br>is not yet available for use with SYCL Graph<br>extension. | LuFinch | LuFinch, daisyden | <ul><li>@daisyden: please reply to @tye1's request for latest results after the 26.0 CI/CD uplift</li><li>check_case_avaliablity; Wait for dependency fix CMPLRLLVM-72057</li></ul> | - - - External oneAPI compiler dependency — wait for oneAPI 2026.0 / CMPLRLLVM-72057 to lift the wo…<br>[→ details](details/3142.md) | P2 | No verified fixing PR exists, and MEMBER tye1 made a fresh blocking request for @daisyden to provide the latest post-26.0 results; the requ… | daisyden | dependency component: oneAPI, module: u… |


<a id="sec-5-2-upstream-pytorch"></a>
- **upstream-pytorch**

_[↑ Back to Index](#sec-2)_

Issues whose fix lives in `pytorch/pytorch` (Dynamo/Inductor, AOTAutograd, `_prims_common`, benchmark harness, test-list sync, etc.). Close/Skip rows excluded.  —  1 issues.

| Issue | Dependency | Title | Owner | Owner Transferred | action_TBD | Fix Approach | Priority | action_reason | Reporter | Labels |
|---|---|---|---|---|---|---|---|---|---|---|
| [#3094](https://github.com/intel/torch-xpu-ops/issues/3094) | upstream-pytorch | XPUGraph tree support | BBBela | BBBela | <ul><li>Wait for fix PR; Add label 'dependency component: upstream-pytorch' - CUDAGraph tree tests require XPUGraph Tree implementation under active PyTorch RFC work.; Wait for dependency fix [pytorch/pytorch#180168](https://github.com/pytorch/pytorch/pull/180168)</li></ul> | - - - Implement XPUGraph (SYCL graph based capture/replay) runtime APIs in torch/xpu and an XPU bra…<br>[→ details](details/3094.md) | P2 | Assignee BBBela stated XPUGraph Tree support is under development, linked the PyTorch RFC, and plans to follow the implementation before ch… | daisyden | module: inductor, ut_upstream |


<a id="sec-5-3-cpu-fallback"></a>
- **CPU fallback**

_[↑ Back to Index](#sec-2)_

Issues where the XPU operator is missing and a CPU fallback is registered in torch-xpu-ops. Close/Skip rows excluded.  —  2 issues.

| Issue | Dependency | Title | Owner | Owner Transferred | action_TBD | Fix Approach | Priority | action_reason | Reporter | Labels |
|---|---|---|---|---|---|---|---|---|---|---|
| [#2283](https://github.com/intel/torch-xpu-ops/issues/2283) | CPU fallback | [upstream_ut] sparse._sampled_addmm is not<br>supported | jenniew | daisyden | <ul><li>Verify fix from merged PR [intel/torch-xpu-ops#3018](https://github.com/intel/torch-xpu-ops/pull/3018) and close</li></ul> | - - - Either register a CPU fallback for aten::sparse_sampled_addmm on SparseCsrXPU (analogous to o…<br>[→ details](details/2283.md) | P1 | PR intel/torch-xpu-ops#3018 is a verified fixing PR for SparseCsrXPU sampled_addmm and is now merged. | daisyden | skipped, ut_upstream, agent:active, age… |
| [#2229](https://github.com/intel/torch-xpu-ops/issues/2229) | CPU fallback | test/test_sparse_csr.py::TestSparseCompressedCPU::<br>test_invalid_input meet message not match | chunhuanMeng | chunhuanMeng | <ul><li>Land PR [intel/torch-xpu-ops#3713](https://github.com/intel/torch-xpu-ops/pull/3713)</li></ul> | - - - Implement aten::_validate_compressed_sparse_indices for the XPU backend in torch-xpu-ops so t…<br>[→ details](details/2229.md) | P3 | PR intel/torch-xpu-ops#3713 is the open, approved replacement for the closed unmerged fixing PR and has no failing checks in the live state… | wincent8 | skipped |


<a id="sec-6"></a>
## 6. New submitted issues (<7 days)

_[↑ Back to Index](#sec-2)_

Issues created on or after 2026-05-27, excluding Close/Skip rows.  —  0 issues.

| Issue | Created | Title | Owner | Owner Transferred | action_TBD | Fix Approach | Priority | action_reason | Reporter | Labels |
|---|---|---|---|---|---|---|---|---|---|---|


<a id="sec-7"></a>
## 7. Requests pending > 1 week

_[↑ Back to Index](#sec-2)_

Issues whose `action_TBD` contains one or more verbs flagged `(>1 week)` — an unresolved comment AR, unresolved PR review comments, or unaddressed CI failures that have been sitting more than 7 days. These are the highest-priority candidates for owner follow-up.

| Issue | Title | Owner | Stale Requests | Priority | Reporter | Labels |
|---|---|---|---|---|---|---|
| [#2376](https://github.com/intel/torch-xpu-ops/issues/2376) | [Bug Skip]: NotImplementedError: "logaddexp_xpu"<br>not implemented for 'Complex' | daisyden | <ul><li>Wait for review on PR laifenxiawucha/pytorch#6 (>1 week)</li></ul> | P1 | mengfei25 | module: ut, skipped |
| [#2253](https://github.com/intel/torch-xpu-ops/issues/2253) | the supported dtypes are not align with cuda | daisyden | <ul><li>Wait for review on PR intel-sandbox/torch-xpu-ops-exp#1700 (>1 week)</li></ul> | P2 | daisyden | duplicate, skipped, ut_upstream, agent:… |


<a id="sec-8"></a>
## 8. Statistics

_[↑ Back to Index](#sec-2)_

- Total rows: **19**
- Classified (non-empty `AR`): **19**
- Empty `AR` (no verdict yet): **0**
- Issues flagged for test-case existence check (`Need check case existence`): **7**

- **AR bucket distribution (multi-membership — an issue with N AR values is counted N times)**

_[↑ Back to Index](#sec-2)_

| AR Bucket | Issues |
|---|---:|
| Close/Skip | 3 |
| Land PR | 10 |
| Wait for PR | 2 |
| Need Response | 3 |
| Need check case existence | 7 |
| Verify | 6 |

- **Priority distribution**

_[↑ Back to Index](#sec-2)_

| Priority | Issues |
|---|---:|
| P1 | 3 |
| P2 | 11 |
| P3 | 5 |

- **Status distribution**

_[↑ Back to Index](#sec-2)_

| Status | Issues |
|---|---:|
| open | 19 |

- **Category column distribution (top 20)**

_[↑ Back to Index](#sec-2)_

| Category | Issues |
|---|---:|
| Flash Attention | 6 |
| Inductor | 5 |
| Others | 3 |
| Sparse | 2 |
| Distributed | 1 |
| Torch Ops - eltwise | 1 |
| Torch Ops - gemm | 1 |

- **`Need check case existence` issue IDs**

_[↑ Back to Index](#sec-2)_

7 issues flagged for XPU test-case existence check:

> #2270, #2285, #2376, #2531, #2853, #3140, #3142
