# Wontfix features (intel/torch-xpu-ops)

Curated map of feature keyword -> intel/torch-xpu-ops issue labeled `wontfix`.
`precheck.py` reads the table below (keywords are matched case-insensitively
against the test name, its source, the cuda-only API groups found, and the CSV
xpu_message). Always re-verify the state with `gh issue view N --repo
intel/torch-xpu-ops --json state,labels` before citing.

Add a row whenever a new wontfix tracker is filed or found. Keep keywords
specific (an API name, op name, or library), never generic words.

| keywords | issue | what |
|---|---|---|
| torch._custom_ops, custom_ops.custom_op | 6049 | deprecated torch._custom_ops API hardcodes {cpu, cuda} |
| tunable, tunableop, _cuda_tunableop | 2535 | TunableOp is CUDA/ROCm only |
| cudnn_rnn, _cudnn_rnn | 2472 | cuDNN RNN kernels are CUDA only |
| FloatTensor, DoubleTensor, HalfTensor, typed tensor | 2792 | legacy torch.cuda typed tensor aliases |
| TypedStorage, FloatStorage, typed storage | 2508 | deprecated typed storage classes |
| xnnpack | 4179 | XNNPACK not built for XPU CI |
| _broadcast_coalesced | 2228 | CUDA-only comm helper |
| no_cuda_monkeypatch | 2164 | CUDA-specific monkeypatch test |
| Torch not compiled with CUDA enabled | 2531 | test calls CUDA runtime directly |
| ipex_woq_linear | 2032 | IPEX-specific op |
