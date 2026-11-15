import re, json, sys
import torch
from torch.testing._internal.common_methods_invocations import op_db
try:
    from torch.testing._internal.opinfo.definitions import python_ref_db
except Exception:
    python_ref_db = []
ALL_OPS = list(op_db) + list(python_ref_db)

# dtype name -> torch dtype
DTYPES = {}
for a in dir(torch):
    o = getattr(torch, a)
    if isinstance(o, torch.dtype):
        DTYPES[a] = o
# also handle 'uint64' etc already covered; sort names longest-first for suffix match
DT_NAMES = sorted(DTYPES, key=len, reverse=True)

# formatted_name -> op (longest first)
FN = {}
for o in ALL_OPS:
    FN.setdefault(o.formatted_name, o)
FN_SORTED = sorted(FN, key=len, reverse=True)


def parse(name):
    """Return (op, dtype) or (None,None)."""
    dt = None
    base = name
    for dn in DT_NAMES:
        if name.endswith("_" + dn):
            dt = DTYPES[dn]
            base = name[: -(len(dn) + 1)]
            break
    op = None
    for fn in FN_SORTED:
        if fn and fn in base:
            op = FN[fn]
            break
    return op, dt


def probe(op, dtype):
    if "jiterator" in op.name:
        return "NA", "jiterator is CUDA/ROCm-only; no XPU equivalent"
    dt = dtype or (op.supported_dtypes("xpu") and sorted(op.supported_dtypes("xpu"), key=str)[0])
    if dt is None:
        dt = torch.float32
    try:
        s = next(iter(op.sample_inputs("xpu", dt, requires_grad=False)))
    except StopIteration:
        return "TBD", ""
    except Exception as e:
        return "TBD", ""  # cannot build sample -> no evidence of non-portability
    try:
        op(s.input, *s.args, **s.kwargs)
        return "TBD", ""
    except Exception as e:
        m = str(e)
        mm = re.search(r"Could not run '([^']+)' with arguments from the '\w+' backend", m)
        if mm:
            return "NA", f"no XPU kernel for {mm.group(1)} (no CPU fallback)"
        if "Jiterator is only supported" in m:
            return "NA", "jiterator is CUDA/ROCm-only; no XPU equivalent"
        low = m.lower()
        if "not implemented for" in low or ("not supported" in low and "xpu" in low):
            return "NA", m.split("\n")[0][:120]
        return "TBD", ""  # ambiguous/test-specific error -> keep portable


def main():
    pairs = json.load(open(sys.argv[1]))  # list of test names
    cache = {}
    out = {}
    for name in pairs:
        op, dt = parse(name)
        if op is None:
            out[name] = ["TBD", "unmapped op; keep portable"]
            continue
        key = (op.name, str(dt))
        if key not in cache:
            cache[key] = probe(op, dt)
        out[name] = list(cache[key])
    json.dump(out, open(sys.argv[2], "w"))
    from collections import Counter
    print("verdicts:", Counter(v[0] for v in out.values()))
    print("distinct (op,dtype) probed:", len(cache))
    nas = [(k, v) for k, v in cache.items() if v[0] == "NA"]
    for k, v in nas[:15]:
        print("  NA", k, v[1])


if __name__ == "__main__":
    main()
