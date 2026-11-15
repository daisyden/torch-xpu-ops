import csv, os, ast, json, sys
from collections import Counter, defaultdict

# Authoritative source tree = the checkout the UT run came from (must equal the
# installed torch). Override with env vars when running elsewhere.
SRC=os.environ.get("XPU_SRC_TREE", "/home/daisyden/upstream/ut_result_analysis/test")
CSV=os.environ.get("XPU_UT_CSV", "/home/daisyden/opencode/ut_result_refresh/ut_regression_pvc_cuda_notrun.csv")

# Decorator/source tokens that BLOCK a method on XPU (keep TBD) when found on the method.
# NOTE: onlyNativeDeviceTypes is NOT a block: NATIVE_DEVICES includes 'xpu'.
# dtypesIfCUDA is NOT a block: xpu falls back to @dtypes default and still runs.
METHOD_BLOCK={
 "onlyCUDA":"@onlyCUDA",
 "skipXPU":"@skipXPU",
 "skipXPUIf":"@skipXPUIf",
 "requires_cuda_and_triton":"@requires_cuda_and_triton",
 "requires_cuda":"@requires_cuda",
}
# module/class guard names meaning CUDA-only (no XPU path)
CUDA_GUARD={"HAS_CUDA_AND_TRITON","HAS_MULTIGPU"}

def _deco_name(d):
    # base callable name of a decorator node, whether bare (@skipXPU) or called (@skipXPUIf(...))
    t=d.func if isinstance(d,ast.Call) else d
    if isinstance(t,ast.Name): return t.id
    if isinstance(t,ast.Attribute): return t.attr
    return ""

def deco_tokens(node, src):
    toks=set(); dtypesIfCUDA=False; dtypesIfXPU=False
    for d in node.decorator_list:
        nm=_deco_name(d)
        s=ast.get_source_segment(src,d) or ""
        if nm in METHOD_BLOCK:
            if nm=="requires_cuda" and "requires_cuda_and_triton" in s: pass
            else: toks.add(nm)
        if nm=="onlyOn" and "cuda" in s and "xpu" not in s: toks.add("onlyOn")
        if nm=="dtypesIfCUDA": dtypesIfCUDA=True
        if nm=="dtypesIfXPU": dtypesIfXPU=True
    return toks, dtypesIfCUDA, dtypesIfXPU

def guard_tag(test):
    ns=set()
    for n in ast.walk(test):
        if isinstance(n,ast.Name): ns.add(n.id)
        if isinstance(n,ast.Attribute): ns.add(n.attr)
    g=ns & CUDA_GUARD
    return "cuda-guard:"+",".join(sorted(g)) if g else None

def analyze(path):
    try: src=open(path).read(); tree=ast.parse(src)
    except Exception: return None
    inst={}
    for n in ast.walk(tree):
        if isinstance(n,ast.Call):
            fn=n.func; nm=getattr(fn,'id',getattr(fn,'attr',''))
            if nm=="instantiate_device_type_tests" and n.args:
                inst[ast.unparse(n.args[0])]={k.arg:(ast.unparse(k.value) if k.value else '') for k in n.keywords}
    classes={}
    def visit(body,guards):
        for n in body:
            if isinstance(n,ast.If):
                g=guard_tag(n.test)
                visit(n.body,guards+([g] if g else []))
                visit(n.orelse,guards)
            elif isinstance(n,ast.ClassDef):
                cdeco=" ".join(ast.get_source_segment(src,d) or "" for d in n.decorator_list)
                cblock = ("skipIf" in cdeco and ("TEST_CUDA" in cdeco or "not TEST_CUDA" in cdeco)) or \
                         ("skipUnless" in cdeco and "TEST_CUDA" in cdeco)
                meths={}
                for b in n.body:
                    if isinstance(b,(ast.FunctionDef,ast.AsyncFunctionDef)) and b.name.startswith("test"):
                        meths[b.name]=deco_tokens(b,src)
                classes[n.name]={"guards":list(guards),"cblock":cblock,"meths":meths}
            elif isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.With,ast.For,ast.Try)):
                visit(getattr(n,"body",[]),guards)
    visit(tree.body,[])
    return {"inst":inst,"classes":classes}

def enabled(kw):
    return kw is not None and (kw.get("allow_xpu")=="True" or (kw.get("only_for") and "xpu" in kw.get("only_for")))

# cache
cache={}
def get(f):
    if f not in cache: cache[f]=analyze(os.path.join(SRC,f))
    return cache[f]

def find_method(meths, name):
    if name in meths: return meths[name]
    # strip trailing device + dtype params heuristically: match longest prefix
    cand=[d for d in meths if name.startswith(d)]
    if cand: return meths[max(cand,key=len)]
    base=name
    for suf in ("_cuda","_xpu"):
        base=base.replace(suf,"")
    cand=[d for d in meths if base.startswith(d)]
    if cand: return meths[max(cand,key=len)]
    return None

def verdict(f, cls, name):
    """Return (action, mechanism) per any_gate source, or (None,'unknown')."""
    a=get(f)
    if a is None: return (None,"file-missing")
    base=cls[:-4] if cls.endswith("CUDA") else cls
    kw = a["inst"].get(cls) or a["inst"].get(base)
    # case-insensitive instantiate-map resolution: CSV `TestFooCUDA` -> source
    # `TestFooCuda` (only_for=cuda) must not be mistaken for a plain base class.
    ci_inst = {k.lower(): k for k in a["inst"]}
    if kw is None:
        srck = ci_inst.get(cls.lower()) or ci_inst.get(base.lower())
        if srck is not None:
            kw = a["inst"][srck]; base = srck
    cinfo = a["classes"].get(cls) or a["classes"].get(base)
    # device-generic classes: the source class is `base`; its instance is base+device
    if kw is not None:
        if not enabled(kw):
            only=kw.get("only_for")
            if only and "xpu" not in only:
                return ("TBD", f"only_for:{only}")
            return ("TBD","needs-allow_xpu")
        # class enabled; check method/guards on the source class
        srcinfo=a["classes"].get(base) or cinfo
        if srcinfo:
            if srcinfo["guards"]: return ("TBD","class-cuda-guard")
            if srcinfo["cblock"]: return ("TBD","class-TEST_CUDA-skip")
            m=find_method(srcinfo["meths"], name)
            if m:
                toks,dcuda,dxpu=m
                if toks: return ("TBD","method-block:"+",".join(sorted(toks)))
                # dtypesIfCUDA-only is not a block: xpu runs with @dtypes default
        return ("rerun","class-enabled-no-block")
    # no instantiate: plain class
    if cinfo:
        if cinfo["guards"]: return ("TBD","class-cuda-guard")
        if cinfo["cblock"]: return ("TBD","class-TEST_CUDA-skip")
        m=find_method(cinfo["meths"], name)
        if m and m[0]: return ("TBD","method-block:"+",".join(sorted(m[0])))
        return ("rerun","plain-no-block")
    return (None,"noclass")

if __name__=="__main__":
    rows=list(csv.reader(open(CSV)))
    diff=Counter(); mech=Counter(); unknown=Counter()
    changes=[]
    for r in rows[1:]:
        if r[16]!="Portable": continue
        if r[18] not in ("TBD","rerun"): continue
        act,m=verdict(r[6],r[7],r[8])
        if act is None:
            unknown[(r[6],m)]+=1; continue
        mech[m]+=1
        if act!=r[18]:
            diff[(r[18],act,r[6])]+=1
            changes.append((r,act,m))
    print("=== verdict mechanisms (all Portable TBD/rerun rows) ===")
    for k,v in mech.most_common(20): print(f"  {v:5d} {k}")
    print("\n=== DIFFS (current -> any_gate verdict) by file ===")
    agg=Counter()
    for (cur,new,f),v in diff.items(): agg[(cur,new)]+=v
    for k,v in agg.items(): print(f"  {k[0]} -> {k[1]}: {v}")
    byfile=Counter()
    for (cur,new,f),v in diff.items(): byfile[(cur,new,f)]=v
    print("\n  top diff files:")
    for k,v in byfile.most_common(25): print(f"    {v:5d} {k[0]}->{k[1]} {k[2]}")
    print("\n=== unknown (noclass/file-missing) top ===")
    for k,v in unknown.most_common(15): print(f"  {v:5d} {k}")
    print("\ntotal diffs:",sum(diff.values()),"unknown:",sum(unknown.values()))
    json.dump([[r[6],r[7],r[8],r[18],act,m] for r,act,m in changes],
              open("/tmp/xpucollect/audit_changes.json","w"))
