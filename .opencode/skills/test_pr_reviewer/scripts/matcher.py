"""Content-first matching between the base and head revisions of a Python file.

Why not name matching
---------------------
A survey of 46 real PyTorch "test decoupling" PRs shows class names are not a
reliable key.  Observed transformations include:

    FakeTensorPropTest       -> TestFakeTensorPropDevice      (reorder + suffix)
    TestCudaTrace            -> TestGpuTraceDevice            (device word swap)
    LoggingTests             -> TestLogging / TestLoggingCUDA / TestLoggingDevice
    TestQuantizedOps         -> TestQuantizedOpsCUDNN + ...Device
                                + _QuantizedActivationTestMixin   (1 -> N)
    TestQuantizeFx + TestQuantizeFxModels -> TestQuantizeFxCUDASpecific (N -> 1)
    TestVarlenAttention      -> _VarlenVsSdpaMixin + ...CuDNN + ...Device
    test_cuda_memory_usage   -> test_accelerator_memory_usage  (method rename)
    test_weight_sharing_gpu  -> test_weight_sharing
    setUp / _get_data_loader -> *copied* into the new class, kept in the old one

So the algorithm is:

  1. Split both revisions into *units*: one per method / module-level function
     (nested defs absorbed), plus a "class header" unit per class, so every
     line of the file belongs to exactly one unit.
  2. Score unit pairs with **body content first** (device-normalised line
     sequence similarity), using name and class-name similarity only as
     tiebreakers.  Candidate generation uses an inverted index over rare body
     lines so big files stay fast.
  3. Do *not* force 1:1.  Report the mutual-best pair as `primary` and keep the
     other strong candidates, which is what correctly expresses copies (1->N)
     and merges (N->1).  The UI lets the reviewer switch targets.
  4. Derive the class mapping by aggregating method votes -- never from names.
"""

from __future__ import annotations

import ast
import difflib
import hashlib
import re
import threading
from collections import Counter, OrderedDict, defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

# --------------------------------------------------------------------------- #
# vocabulary learned from the surveyed PRs
# --------------------------------------------------------------------------- #

DEVICE_WORDS = {
    "cuda",
    "cudnn",
    "cublas",
    "cusparse",
    "cusolver",
    "cudagraph",
    "cudagraphs",
    "rocm",
    "hip",
    "miopen",
    "xpu",
    "sycl",
    "onednn",
    "gpu",
    "gpus",
    "cpu",
    "cpus",
    "mps",
    "hpu",
    "mtia",
    "npu",
    "ipu",
    "xla",
    "tpu",
    "privateuse1",
    "device",
    "devices",
    "devicetype",
    "accelerator",
    "accelerators",
    "accel",
    "agnostic",
    "generic",
    "multigpu",
    "multiaccelerator",
    "nvidia",
    "intel",
    "amd",
}

# words that carry no identity in a test class/method name
NOISE_WORDS = {
    "test",
    "tests",
    "testing",
    "testcase",
    "case",
    "cases",
    "mixin",
    "helper",
    "helpers",
    "specific",
    "only",
    "base",
    "impl",
    "common",
    "utils",
    "util",
}

_WORD_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+")

# code fragments whose appearance/disappearance is expected in this refactor
_DEVICE_CODE_PATTERNS = [
    re.compile(r"\bhw_classification\s*=\s*[\w.]+"),
    re.compile(r"\bdevice_type\s*=\s*[^,)\n]+"),
    re.compile(r"\bdevice\s*=\s*self\.device\b"),
    re.compile(r"\bdevice\s*=\s*device\b"),
    re.compile(r"\bdevice\s*=\s*[\"'][\w:]+[\"']"),
    re.compile(r"\bself\.device_type\b"),
    re.compile(r"\bself\.device\b"),
    re.compile(r"\ballow_xpu\s*=\s*\w+"),
    re.compile(r"\b(?:except_for|only_for)\s*=\s*\[[^\]]*\]"),
    re.compile(r"\bdevice\b"),
    re.compile(r"\b(?:torch\.)?(?:cuda|xpu|mps|hpu|mtia|npu)\b"),
    re.compile(r"\baccelerator\b", re.I),
    re.compile(r"[\"'](?:cuda|xpu|cpu|mps|hpu|npu|mtia)(?::\d+)?[\"']"),
]

_TOKEN_RE = re.compile(r"\w+|\s+|[^\w\s]")

VERDICTS = ("identical", "blank", "indent", "device", "rename", "changed", "missing")
VERDICT_ORDER = {v: i for i, v in enumerate(VERDICTS)}


# --------------------------------------------------------------------------- #
# name normalisation
# --------------------------------------------------------------------------- #


def split_words(name: str) -> list[str]:
    return [w.lower() for w in _WORD_RE.findall(name or "")]


def name_key(name: str) -> tuple[str, ...]:
    """Identity of a class/method name: device + boilerplate words removed.

    TestCudaTrace / TestGpuTraceDevice        -> ('trace',)
    FakeTensorPropTest / TestFakeTensorPropDevice -> ('fake','tensor','prop')
    test_weight_sharing_gpu / test_weight_sharing -> ('weight','sharing')
    TestFooDeviceType / TestFooDevice         -> ('foo',)
    """
    words = split_words(name)
    out: list[str] = []
    prev_device = False
    for w in words:
        if w in DEVICE_WORDS:
            prev_device = True
            continue
        # "type" / "types" only qualify a preceding device word (DeviceType,
        # CudaType); elsewhere they are meaningful, so keep them.
        if prev_device and w in ("type", "types"):
            continue
        prev_device = False
        if w in NOISE_WORDS:
            continue
        out.append(w)
    return tuple(out)


def name_similarity(a: str, b: str) -> float:
    if a == b:
        return 1.0
    ka, kb = name_key(a), name_key(b)
    if ka and ka == kb:
        return 0.95
    sa, sb = set(ka), set(kb)
    if not sa or not sb:
        return 0.0
    jac = len(sa & sb) / len(sa | sb)
    seq = difflib.SequenceMatcher(a="_".join(ka), b="_".join(kb)).ratio()
    return 0.6 * jac + 0.4 * seq


def is_device_rename(a: str, b: str) -> bool:
    """True when two names differ only by device / boilerplate words."""
    return a != b and name_key(a) == name_key(b) and bool(name_key(a))


# --------------------------------------------------------------------------- #
# line normalisation
# --------------------------------------------------------------------------- #


def strip_device_code(text: str) -> str:
    out = text
    for pat in _DEVICE_CODE_PATTERNS:
        out = pat.sub("", out)
    out = re.sub(r",\s*(?=[,)\]}])", "", out)
    out = re.sub(r"\(\s*,", "(", out)
    out = re.sub(r",\s*\)", ")", out)
    return re.sub(r"\s+", "", out)


def squash(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _name_neutral(text: str) -> str:
    """Also neutralise identifier device words, for rename detection."""
    def repl(m: re.Match[str]) -> str:
        return "_".join(name_key(m.group(0))) or "X"

    return re.sub(r"[A-Za-z_][A-Za-z0-9_]*", repl, strip_device_code(text))


def classify_pair(base_line: str, head_line: str) -> str:
    if base_line == head_line:
        return "identical"
    if not base_line.strip() and not head_line.strip():
        return "blank"
    if base_line.strip() == head_line.strip() or squash(base_line) == squash(head_line):
        return "indent"
    if strip_device_code(base_line) == strip_device_code(head_line):
        return "device"
    if _name_neutral(base_line) == _name_neutral(head_line):
        return "rename"
    return "changed"


def classify_single(line: str) -> str:
    if not line.strip():
        return "blank"
    if strip_device_code(line) == "":
        return "device"
    return "changed"


def word_diff(a: str, b: str) -> tuple[list[dict], list[dict]]:
    at, bt = _TOKEN_RE.findall(a), _TOKEN_RE.findall(b)
    sm = difflib.SequenceMatcher(a=at, b=bt, autojunk=False)
    left: list[dict] = []
    right: list[dict] = []

    def push(dst: list[dict], text: str, mark: bool) -> None:
        if not text:
            return
        if dst and dst[-1]["m"] == mark:
            dst[-1]["t"] += text
        else:
            dst.append({"t": text, "m": mark})

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        la, lb = "".join(at[i1:i2]), "".join(bt[j1:j2])
        if tag == "equal":
            push(left, la, False)
            push(right, lb, False)
        elif tag == "delete":
            push(left, la, True)
        elif tag == "insert":
            push(right, lb, True)
        else:
            push(left, la, True)
            push(right, lb, True)
    return left, right


# --------------------------------------------------------------------------- #
# units
# --------------------------------------------------------------------------- #


@dataclass
class Unit:
    uid: int
    kind: str  # "method" | "function" | "class_header" | "free"
    name: str
    cls: str | None  # dotted class path, None for module level
    qualname: str
    start: int
    end: int
    def_line: int
    lines: list[str] = field(default_factory=list)  # raw source lines
    linenos: list[int] = field(default_factory=list)  # for non-contiguous units
    bases: list[str] = field(default_factory=list)
    decorators: list[str] = field(default_factory=list)
    signature: str = ""
    body: list[str] = field(default_factory=list)  # normalised, for scoring
    body_set: frozenset[str] = frozenset()

    @property
    def label(self) -> str:
        return self.qualname

    def to_json(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "kind": self.kind,
            "name": self.name,
            "cls": self.cls,
            "qualname": self.qualname,
            "start": self.start,
            "end": self.end,
            "def_line": self.def_line,
            "bases": self.bases,
            "decorators": self.decorators,
            "signature": self.signature,
            "nlines": len(self.linenos),
        }


def _decor_start(node: ast.AST) -> int:
    return min(
        [node.lineno]  # type: ignore[attr-defined]
        + [d.lineno for d in getattr(node, "decorator_list", []) or []]
    )


def _unparse(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return "?"


class FileModel:
    """One revision of a file, split into units covering every line."""

    def __init__(self, text: str, path: str = ""):
        self.path = path
        self.text = text
        self.lines = text.split("\n")
        if self.lines and self.lines[-1] == "":
            self.lines.pop()
        self.units: list[Unit] = []
        self.parse_error: str | None = None
        self._uid = 0
        self.class_info: dict[str, dict[str, Any]] = {}

        tree: ast.Module | None
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            self.parse_error = f"{exc.msg} (line {exc.lineno})"
            tree = None

        if tree is not None:
            self._walk(tree.body, cls=None, prefix="")
        self._finish()

    # -- construction ------------------------------------------------------- #

    def _new(self, **kw: Any) -> Unit:
        self._uid += 1
        return Unit(uid=self._uid, **kw)

    def _walk(self, body: Sequence[ast.stmt], cls: str | None, prefix: str) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                qual = prefix + node.name
                start, end = _decor_start(node), node.end_lineno or node.lineno
                own: list[int] = []
                child_spans: list[tuple[int, int]] = []
                for m in node.body:
                    if isinstance(
                        m, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
                    ):
                        child_spans.append((_decor_start(m), m.end_lineno or m.lineno))
                covered = set()
                for s, e in child_spans:
                    covered.update(range(s, e + 1))
                # Blank/comment-only filler *between* methods belongs to nobody
                # useful: keeping it in the class header would align stray empty
                # lines against real code.  Keep only the contiguous header
                # (decorators + `class` line + class-level statements before the
                # first method) plus any non-blank trailing statements.
                first_child = min((s for s, _ in child_spans), default=end + 1)
                own = [
                    ln
                    for ln in range(start, end + 1)
                    if ln not in covered
                    and (ln < first_child or self.lines[ln - 1].strip())
                ]
                u = self._new(
                    kind="class_header",
                    name=node.name,
                    cls=cls,
                    qualname=qual,
                    start=start,
                    end=end,
                    def_line=node.lineno,
                    bases=[_unparse(b) for b in node.bases],
                    decorators=[_unparse(d) for d in node.decorator_list],
                )
                u.linenos = own
                self.units.append(u)
                self.class_info[qual] = {
                    "name": node.name,
                    "bases": u.bases,
                    "decorators": u.decorators,
                    "start": start,
                    "end": end,
                    "def_line": node.lineno,
                    "parent": cls,
                }
                self._walk(node.body, cls=qual, prefix=qual + ".")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = prefix + node.name
                start, end = _decor_start(node), node.end_lineno or node.lineno
                u = self._new(
                    kind="method" if cls else "function",
                    name=node.name,
                    cls=cls,
                    qualname=qual,
                    start=start,
                    end=end,
                    def_line=node.lineno,
                    decorators=[_unparse(d) for d in node.decorator_list],
                    signature=_signature(node),
                )
                u.linenos = list(range(start, end + 1))
                self.units.append(u)
                # nested defs are absorbed into this unit on purpose

    def _finish(self) -> None:
        self.owner_of: dict[int, Unit] = {}
        for u in self.units:
            u.lines = [self.line(n) for n in u.linenos]
            u.body = [
                strip_device_code(s)
                for s in (ln.strip() for ln in u.lines)
                if s and not s.startswith("#")
            ]
            u.body_set = frozenset(u.body)
            for n in u.linenos:
                # innermost wins: methods are added after their class header and
                # class headers exclude child spans, so no conflict occurs
                self.owner_of.setdefault(n, u)

        self.free_lines = [
            n for n in range(1, len(self.lines) + 1) if n not in self.owner_of
        ]
        self.by_uid = {u.uid: u for u in self.units}
        self.by_qual: dict[str, Unit] = {}
        for u in self.units:
            key = u.qualname if u.kind != "class_header" else u.qualname + " (class)"
            self.by_qual[key] = u
        self.callables = [u for u in self.units if u.kind in ("method", "function")]
        self.class_headers = [u for u in self.units if u.kind == "class_header"]

    # -- queries ------------------------------------------------------------ #

    def line(self, no: int) -> str:
        return self.lines[no - 1] if 1 <= no <= len(self.lines) else ""

    def owner(self, no: int) -> Unit | None:
        return self.owner_of.get(no)

    def enclosing_class(self, no: int) -> str | None:
        best, best_start = None, -1
        for q, info in self.class_info.items():
            if info["start"] <= no <= info["end"] and info["start"] > best_start:
                best, best_start = q, info["start"]
        return best

    def class_span(self, qual: str) -> tuple[int, int] | None:
        info = self.class_info.get(qual)
        return (info["start"], info["end"]) if info else None

    def methods_of(self, qual: str) -> list[Unit]:
        return [u for u in self.callables if u.cls == qual]


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    try:
        return f"{node.name}({ast.unparse(node.args)})"
    except Exception:
        return node.name


# --------------------------------------------------------------------------- #
# pair scoring
# --------------------------------------------------------------------------- #


def _seq_ratio(a: Sequence[str], b: Sequence[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(a=list(a), b=list(b), autojunk=False).ratio()


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@dataclass
class Candidate:
    other: Unit
    score: float
    body: float
    name: float
    cls: float
    reason: str
    mutual: bool = False

    def to_json(self) -> dict[str, Any]:
        d = self.other.to_json()
        d.update(
            {
                "score": round(self.score, 4),
                "body_score": round(self.body, 4),
                "name_score": round(self.name, 4),
                "class_score": round(self.cls, 4),
                "reason": self.reason,
                "mutual": self.mutual,
            }
        )
        return d


class FileMatch:
    """Content-first unit correspondence between two revisions of a file."""

    # weights: body dominates, names only break ties
    W_BODY = 0.70
    W_NAME = 0.20
    W_CLS = 0.10
    MIN_SCORE = 0.35
    MAX_CANDIDATES = 6
    COMMON_LINE_DF = 40  # a body line seen in more units than this is not a signal

    def __init__(self, base: FileModel, head: FileModel):
        self.base = base
        self.head = head
        self.cls_sim_cache: dict[tuple[str | None, str | None], float] = {}
        self.b2h: dict[int, list[Candidate]] = {}
        self.h2b: dict[int, list[Candidate]] = {}
        # methods first: their bodies are the only trustworthy evidence
        self._match(base.callables, head.callables)
        self._mark_mutual()
        # the class mapping is derived from those method votes ...
        self.class_map, self.class_map_rev = self._derive_class_map()
        # ... and only then used to pair the (text-poor) class headers
        self._match_class_headers()
        self._mark_mutual()

    # -- similarity pieces -------------------------------------------------- #

    def _class_sim(self, a: str | None, b: str | None) -> float:
        key = (a, b)
        if key in self.cls_sim_cache:
            return self.cls_sim_cache[key]
        if a is None and b is None:
            v = 1.0
        elif a is None or b is None:
            v = 0.25
        else:
            v = name_similarity(a.split(".")[-1], b.split(".")[-1])
        self.cls_sim_cache[key] = v
        return v

    def _score(self, b: Unit, h: Unit) -> Candidate:
        body = _seq_ratio(b.body, h.body)
        nm = name_similarity(b.name, h.name)
        cs = self._class_sim(b.cls, h.cls)
        score = self.W_BODY * body + self.W_NAME * nm + self.W_CLS * cs

        if b.qualname == h.qualname:
            reason = "same location"
            score = max(score, 0.55 + 0.45 * body)
        elif b.name == h.name and b.cls != h.cls:
            reason = "moved (same method name)"
        elif is_device_rename(b.name, h.name):
            reason = "renamed (device word)"
        elif body >= 0.9:
            reason = "identical body"
        elif body >= 0.6:
            reason = "similar body"
        else:
            reason = "weak match"

        # a near-verbatim move is the signature of this refactor: trust it
        if body >= 0.97 and len(b.body) >= 3:
            score = max(score, 0.9)
        return Candidate(other=h, score=score, body=body, name=nm, cls=cs, reason=reason)

    # -- candidate generation ----------------------------------------------- #

    def _shortlist(self, src: Sequence[Unit], dst: Sequence[Unit]) -> dict[int, set[int]]:
        """Cheap inverted index over rare body lines + name buckets."""
        df: Counter[str] = Counter()
        index: dict[str, list[int]] = defaultdict(list)
        for u in dst:
            for ln in u.body_set:
                df[ln] += 1
                index[ln].append(u.uid)

        by_name: dict[str, list[int]] = defaultdict(list)
        by_key: dict[tuple[str, ...], list[int]] = defaultdict(list)
        for u in dst:
            by_name[u.name].append(u.uid)
            by_key[name_key(u.name)].append(u.uid)

        out: dict[int, set[int]] = {}
        for u in src:
            votes: Counter[int] = Counter()
            for ln in u.body_set:
                if df[ln] > self.COMMON_LINE_DF:
                    continue
                for uid in index[ln]:
                    votes[uid] += 1
            cand = {uid for uid, _ in votes.most_common(30)}
            cand.update(by_name.get(u.name, [])[:20])
            cand.update(by_key.get(name_key(u.name), [])[:20])
            if len(cand) < 3:
                # tiny/odd unit: fall back to the whole other side (bounded)
                cand.update(x.uid for x in dst[:400])
            out[u.uid] = cand
        return out

    def _match(self, bsrc: Sequence[Unit], hsrc: Sequence[Unit]) -> None:
        if not bsrc or not hsrc:
            return
        shortlist = self._shortlist(bsrc, hsrc)
        head_by_uid = {u.uid: u for u in hsrc}
        pair_cache: dict[tuple[int, int], Candidate] = {}

        for b in bsrc:
            cands: list[Candidate] = []
            for uid in shortlist[b.uid]:
                h = head_by_uid.get(uid)
                if h is None:
                    continue
                c = self._score(b, h)
                pair_cache[(b.uid, h.uid)] = c
                if c.score >= self.MIN_SCORE:
                    cands.append(c)
            cands.sort(key=lambda c: -c.score)
            self.b2h[b.uid] = cands[: self.MAX_CANDIDATES]

        # reverse direction, reusing scores and adding any missed pairs
        rev: dict[int, list[Candidate]] = defaultdict(list)
        rshort = self._shortlist(hsrc, bsrc)
        base_by_uid = {u.uid: u for u in bsrc}
        for h in hsrc:
            for uid in rshort[h.uid]:
                b = base_by_uid.get(uid)
                if b is None:
                    continue
                c = pair_cache.get((b.uid, h.uid))
                if c is None:
                    c = self._score(b, h)
                    pair_cache[(b.uid, h.uid)] = c
                if c.score >= self.MIN_SCORE:
                    rev[h.uid].append(
                        Candidate(
                            other=b,
                            score=c.score,
                            body=c.body,
                            name=c.name,
                            cls=c.cls,
                            reason=c.reason,
                        )
                    )
        for h in hsrc:
            lst = sorted(rev.get(h.uid, []), key=lambda c: -c.score)
            self.h2b[h.uid] = lst[: self.MAX_CANDIDATES]

    def _mark_mutual(self) -> None:
        best_h = {uid: (c[0].other.uid if c else None) for uid, c in self.b2h.items()}
        best_b = {uid: (c[0].other.uid if c else None) for uid, c in self.h2b.items()}
        for buid, cands in self.b2h.items():
            for c in cands:
                if best_b.get(c.other.uid) == buid and best_h.get(buid) == c.other.uid:
                    c.mutual = True
        for huid, cands in self.h2b.items():
            for c in cands:
                if best_h.get(c.other.uid) == huid and best_b.get(huid) == c.other.uid:
                    c.mutual = True

    # -- class mapping derived from method votes ---------------------------- #

    def _derive_class_map(
        self,
    ) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
        fwd: dict[str, Counter[str]] = defaultdict(Counter)
        weight: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for b in self.base.callables:
            cands = self.b2h.get(b.uid) or []
            if not cands:
                continue
            top = cands[0]
            if top.score < 0.5:
                continue
            bk = b.cls or "<module>"
            hk = top.other.cls or "<module>"
            fwd[bk][hk] += 1
            weight[bk][hk] += top.score

        def render(counter: Counter[str], wmap: dict[str, float], total: int) -> list[dict]:
            out = []
            for k, n in counter.most_common():
                out.append(
                    {
                        "cls": k,
                        "methods": n,
                        "share": round(n / total, 3) if total else 0.0,
                        "avg_score": round(wmap[k] / n, 3) if n else 0.0,
                    }
                )
            return out

        class_map: dict[str, list[dict]] = {}
        for bk, counter in fwd.items():
            class_map[bk] = render(counter, weight[bk], sum(counter.values()))

        rev: dict[str, Counter[str]] = defaultdict(Counter)
        rweight: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for h in self.head.callables:
            cands = self.h2b.get(h.uid) or []
            if not cands:
                continue
            top = cands[0]
            if top.score < 0.5:
                continue
            hk = h.cls or "<module>"
            bk = top.other.cls or "<module>"
            rev[hk][bk] += 1
            rweight[hk][bk] += top.score
        class_map_rev: dict[str, list[dict]] = {}
        for hk, counter in rev.items():
            class_map_rev[hk] = render(counter, rweight[hk], sum(counter.values()))
        return class_map, class_map_rev

    # -- class headers, paired via the method-vote evidence ------------------ #

    def _match_class_headers(self) -> None:
        """Pair `class X(...):` headers.

        A class header is only 1-3 lines and its distinguishing content
        (`hw_classification = ...`) is normalised away, so body similarity is
        useless here -- a brand-new `class TestFooDevice(TestCase):` would score
        0.0 and stay unmatched.  Instead use the class mapping that was already
        derived from method votes: if the methods of head class H came from base
        class B, then H's header corresponds to B's header.
        """
        bh = {u.qualname: u for u in self.base.class_headers}
        hh = {u.qualname: u for u in self.head.class_headers}

        def evidence(hq: str) -> tuple[str | None, float, int]:
            votes = self.class_map_rev.get(hq) or []
            for v in votes:
                if v["cls"] in bh:
                    return v["cls"], float(v["share"]), int(v["methods"])
            return None, 0.0, 0

        for hq, h in hh.items():
            bq, share, nmethods = evidence(hq)
            if bq is None:
                # no method evidence: fall back to an exact-name pairing only
                if hq in bh:
                    bq, share, nmethods = hq, 1.0, 0
                else:
                    continue
            b = bh[bq]
            same = b.qualname == h.qualname
            if same:
                reason = "same class"
            elif nmethods:
                reason = f"class mapped by {nmethods} method(s)"
            else:
                reason = "class mapped by name"
            # confidence: derived from how much of the class came from `bq`
            score = 0.55 + 0.4 * share if nmethods else 0.9
            if same:
                score = max(score, 0.95)
            hdr = align_lines(self.base, self.head, b.linenos, h.linenos)
            body = 1.0 - (
                sum(1 for r in hdr if r["verdict"] in ("changed", "missing"))
                / max(len(hdr), 1)
            )
            cand_h = Candidate(
                other=h,
                score=score,
                body=body,
                name=name_similarity(b.name, h.name),
                cls=1.0,
                reason=reason,
            )
            cand_b = Candidate(
                other=b,
                score=score,
                body=body,
                name=cand_h.name,
                cls=1.0,
                reason=reason,
            )
            self.b2h.setdefault(b.uid, [])
            self.h2b.setdefault(h.uid, [])
            # a base class split into several head classes keeps several
            # candidates, ordered by confidence
            self.b2h[b.uid] = sorted(
                [c for c in self.b2h[b.uid] if c.other.uid != h.uid] + [cand_h],
                key=lambda c: -c.score,
            )[: self.MAX_CANDIDATES]
            self.h2b[h.uid] = sorted(
                [c for c in self.h2b[h.uid] if c.other.uid != b.uid] + [cand_b],
                key=lambda c: -c.score,
            )[: self.MAX_CANDIDATES]

        # classes that vanished / appeared with no evidence at all stay empty,
        # which the UI reports as "no counterpart"

    # -- public ------------------------------------------------------------- #

    def candidates(self, side: str, uid: int) -> list[Candidate]:
        return (self.b2h if side == "base" else self.h2b).get(uid, [])

    def primary(self, side: str, uid: int) -> Candidate | None:
        cands = self.candidates(side, uid)
        return cands[0] if cands else None


# --------------------------------------------------------------------------- #
# alignment
# --------------------------------------------------------------------------- #


def align_lines(
    base: FileModel,
    head: FileModel,
    base_nos: Sequence[int],
    head_nos: Sequence[int],
) -> list[dict[str, Any]]:
    bl = [base.line(n) for n in base_nos]
    hl = [head.line(n) for n in head_nos]
    bkey = [strip_device_code(x) for x in bl]
    hkey = [strip_device_code(x) for x in hl]
    sm = difflib.SequenceMatcher(a=bkey, b=hkey, autojunk=False)
    rows: list[dict[str, Any]] = []

    def emit(bi: int | None, hi: int | None) -> None:
        bt = bl[bi] if bi is not None else None
        ht = hl[hi] if hi is not None else None
        if bt is not None and ht is not None:
            verdict = classify_pair(bt, ht)
            if verdict in ("identical", "blank"):
                lseg, rseg = [{"t": bt, "m": False}], [{"t": ht, "m": False}]
            else:
                lseg, rseg = word_diff(bt, ht)
        elif bt is not None:
            verdict = classify_single(bt)
            lseg = [{"t": bt, "m": verdict != "blank"}]
            rseg = []
        else:
            verdict = classify_single(ht or "")
            lseg = []
            rseg = [{"t": ht, "m": verdict != "blank"}]
        rows.append(
            {
                "base_no": None if bi is None else base_nos[bi],
                "head_no": None if hi is None else head_nos[hi],
                "base": bt,
                "head": ht,
                "base_seg": lseg,
                "head_seg": rseg,
                "verdict": verdict,
            }
        )

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                emit(i1 + k, j1 + k)
        elif tag == "delete":
            for k in range(i1, i2):
                emit(k, None)
        elif tag == "insert":
            for k in range(j1, j2):
                emit(None, k)
        else:
            for k in range(max(i2 - i1, j2 - j1)):
                emit(i1 + k if i1 + k < i2 else None, j1 + k if j1 + k < j2 else None)
    return rows


def rows_verdict(rows: Iterable[dict[str, Any]]) -> str:
    worst = "identical"
    for r in rows:
        if VERDICT_ORDER[r["verdict"]] > VERDICT_ORDER[worst]:
            worst = r["verdict"]
    return worst


def rows_stats(rows: Sequence[dict[str, Any]]) -> dict[str, int]:
    c = Counter(r["verdict"] for r in rows)
    return {v: c.get(v, 0) for v in VERDICTS}


# --------------------------------------------------------------------------- #
# line-level fallback for free lines (imports, module calls, ...)
# --------------------------------------------------------------------------- #


def best_line_match(
    src: FileModel, dst: FileModel, lineno: int
) -> tuple[int | None, float]:
    target = strip_device_code(src.line(lineno))
    if not target:
        return None, 0.0
    sm = difflib.SequenceMatcher(autojunk=False)
    sm.set_seq2(target)
    best_no, best = None, 0.0
    n_src = max(len(src.lines), 1)
    n_dst = max(len(dst.lines), 1)
    for i, raw in enumerate(dst.lines, start=1):
        key = strip_device_code(raw)
        if not key or abs(len(key) - len(target)) > max(12, len(target) * 0.7):
            continue
        sm.set_seq1(key)
        if sm.real_quick_ratio() < best or sm.quick_ratio() < best:
            continue
        r = sm.ratio() - 0.05 * abs(i / n_dst - lineno / n_src)
        if r > best:
            best, best_no = r, i
    return best_no, best


# --------------------------------------------------------------------------- #
# resolve a clicked line
# --------------------------------------------------------------------------- #


def resolve(
    match: FileMatch,
    side: str,
    lineno: int,
    target_uid: int | None = None,
    context: int = 8,
) -> dict[str, Any]:
    base, head = match.base, match.head
    src = base if side == "base" else head
    unit = src.owner(lineno)

    out: dict[str, Any] = {
        "side": side,
        "lineno": lineno,
        "line": src.line(lineno),
        "base_parse_error": base.parse_error,
        "head_parse_error": head.parse_error,
    }

    if unit is not None:
        cands = match.candidates(side, unit.uid)
        chosen: Candidate | None = None
        if target_uid is not None:
            chosen = next((c for c in cands if c.other.uid == target_uid), None)
        if chosen is None and cands:
            chosen = cands[0]
        out["src_unit"] = unit.to_json()
        out["candidates"] = [c.to_json() for c in cands]

        if chosen is not None:
            b_unit = unit if side == "base" else chosen.other
            h_unit = chosen.other if side == "base" else unit
            rows = align_lines(base, head, b_unit.linenos, h_unit.linenos)
            out.update(
                {
                    "mode": "unit",
                    "base_unit": b_unit.to_json(),
                    "head_unit": h_unit.to_json(),
                    "chosen_uid": chosen.other.uid,
                    "score": round(chosen.score, 4),
                    "body_score": round(chosen.body, 4),
                    "reason": chosen.reason,
                    "mutual": chosen.mutual,
                    "moved": b_unit.cls != h_unit.cls,
                    "renamed": b_unit.name != h_unit.name,
                    "rows": rows,
                    "focus": _focus(rows, side, lineno),
                    "verdict": rows_verdict(rows),
                    "stats": rows_stats(rows),
                }
            )
            return out
        out["orphan"] = True

    # free line / no counterpart unit -> line-level search
    if side == "base":
        other, score = best_line_match(base, head, lineno)
        b_no, h_no = lineno, other
    else:
        other, score = best_line_match(head, base, lineno)
        b_no, h_no = other, lineno

    if b_no is None or h_no is None:
        if side == "base":
            b_nos = list(
                range(max(1, lineno - context), min(len(base.lines), lineno + context) + 1)
            )
            h_nos: list[int] = []
        else:
            b_nos = []
            h_nos = list(
                range(max(1, lineno - context), min(len(head.lines), lineno + context) + 1)
            )
        rows = align_lines(base, head, b_nos, h_nos)
        out.update(
            {
                "mode": "none",
                "verdict": "missing",
                "rows": rows,
                "focus": _focus(rows, side, lineno),
                "stats": rows_stats(rows),
                "message": "no counterpart found in the other revision",
                "candidates": out.get("candidates", []),
            }
        )
        return out

    b_nos = list(range(max(1, b_no - context), min(len(base.lines), b_no + context) + 1))
    h_nos = list(range(max(1, h_no - context), min(len(head.lines), h_no + context) + 1))
    rows = align_lines(base, head, b_nos, h_nos)
    out.update(
        {
            "mode": "line",
            "score": round(score, 4),
            "reason": "nearest matching line",
            "rows": rows,
            "focus": _focus(rows, side, lineno),
            "verdict": classify_pair(base.line(b_no), head.line(h_no)),
            "stats": rows_stats(rows),
            "base_unit": _region(base, b_nos),
            "head_unit": _region(head, h_nos),
            "candidates": out.get("candidates", []),
        }
    )
    return out


def _region(model: FileModel, nos: Sequence[int]) -> dict[str, Any]:
    lo, hi = (nos[0], nos[-1]) if nos else (0, 0)
    return {
        "uid": -1,
        "kind": "region",
        "name": f"lines {lo}-{hi}",
        "cls": model.enclosing_class(lo) if nos else None,
        "qualname": f"<lines {lo}-{hi}>",
        "start": lo,
        "end": hi,
        "def_line": lo,
        "bases": [],
        "decorators": [],
        "signature": "",
        "nlines": len(nos),
    }


def _focus(rows: Sequence[dict[str, Any]], side: str, lineno: int) -> int | None:
    key = "base_no" if side == "base" else "head_no"
    for i, r in enumerate(rows):
        if r[key] == lineno:
            return i
    return None


# --------------------------------------------------------------------------- #
# whole-file line map for the side-by-side file views
# --------------------------------------------------------------------------- #


def build_line_map(
    match: FileMatch, anchors: Sequence[tuple[int, int]] | None = None
) -> dict[str, Any]:
    """Per-line verdict + counterpart for *every* line of both revisions.

    Panes 2 and 3 render the complete base / head file so the reviewer can
    scroll freely for context.  Colouring each line the way a normal diff tool
    does needs, for every line: its verdict, its counterpart line number on the
    other side, and the word-level segments.  Computing that per click would be
    far too slow on a 27k-line file, so it is done once per file and cached
    alongside the FileMatch.

    Lines inside a matched unit are aligned against their counterpart unit.
    Lines outside any unit (imports, module-level calls) are aligned with a
    plain diff of the leftover line ranges, so they are coloured too.

    `anchors` are (base_no, head_no) pairs that the unified diff itself reports
    as unchanged context.  Those are authoritative and take precedence: a line
    can be untouched context in the diff while sitting inside a method that
    moved, and unit-level alignment might otherwise pair a trivial line such as
    `)` with a similar line elsewhere in the counterpart method.
    """
    base, head = match.base, match.head
    nb, nh = len(base.lines), len(head.lines)

    # 1-based per-line records
    b_rows: list[dict[str, Any]] = [None] * (nb + 1)  # type: ignore[list-item]
    h_rows: list[dict[str, Any]] = [None] * (nh + 1)  # type: ignore[list-item]

    def put(side_rows, no, rec):
        if no is not None and 1 <= no < len(side_rows) and side_rows[no] is None:
            side_rows[no] = rec

    # --- 0. diff context anchors win ---------------------------------------- #
    # ... but only if they are actually consistent with the file contents.
    # GitHub truncates very large diffs, which leaves later hunks with stale
    # line numbers; trusting those would mis-colour thousands of lines.
    anchored_b: set[int] = set()
    anchored_h: set[int] = set()
    for bno, hno in anchors or ():
        if not (1 <= bno <= nb and 1 <= hno <= nh):
            continue
        bt, ht = base.line(bno), head.line(hno)
        # a context line must be identical on both sides; if it is not, the
        # anchor is stale and must be discarded
        if bt != ht:
            continue
        put(b_rows, bno, {"v": "identical", "o": hno, "seg": [{"t": bt, "m": False}]})
        put(h_rows, hno, {"v": "identical", "o": bno, "seg": [{"t": ht, "m": False}]})
        anchored_b.add(bno)
        anchored_h.add(hno)

    # --- 1. units that were paired ---------------------------------------- #
    done_b: set[int] = set()
    done_h: set[int] = set()
    pairs: list[tuple[Unit, Unit]] = []
    seen: set[tuple[int, int]] = set()
    for b in base.units:
        c = match.primary("base", b.uid)
        if c and (b.uid, c.other.uid) not in seen:
            seen.add((b.uid, c.other.uid))
            pairs.append((b, c.other))
    for h in head.units:
        c = match.primary("head", h.uid)
        if c and (c.other.uid, h.uid) not in seen:
            seen.add((c.other.uid, h.uid))
            pairs.append((c.other, h))

    for b_unit, h_unit in pairs:
        rows = align_lines(base, head, b_unit.linenos, h_unit.linenos)
        for r in rows:
            bno, hno = r["base_no"], r["head_no"]
            if bno is not None:
                put(
                    b_rows,
                    bno,
                    {
                        "v": r["verdict"],
                        "o": hno,
                        "seg": r["base_seg"],
                        "u": b_unit.qualname,
                        "t": h_unit.qualname,
                    },
                )
                done_b.add(bno)
                # anchored lines keep the diff's verdict but still gain the unit
                # names, so the UI can show "moved from/to" on them
                if bno in anchored_b and b_rows[bno] is not None:
                    b_rows[bno].setdefault("u", b_unit.qualname)
                    b_rows[bno].setdefault("t", h_unit.qualname)
            if hno is not None:
                put(
                    h_rows,
                    hno,
                    {
                        "v": r["verdict"],
                        "o": bno,
                        "seg": r["head_seg"],
                        "u": h_unit.qualname,
                        "t": b_unit.qualname,
                    },
                )
                done_h.add(hno)
                if hno in anchored_h and h_rows[hno] is not None:
                    h_rows[hno].setdefault("u", h_unit.qualname)
                    h_rows[hno].setdefault("t", b_unit.qualname)

    # --- 2. everything else: plain diff of the remaining lines ------------- #
    rest_b = [n for n in range(1, nb + 1) if n not in done_b and n not in anchored_b]
    rest_h = [n for n in range(1, nh + 1) if n not in done_h and n not in anchored_h]
    if rest_b or rest_h:
        for r in align_lines(base, head, rest_b, rest_h):
            bno, hno = r["base_no"], r["head_no"]
            if bno is not None:
                put(b_rows, bno, {"v": r["verdict"], "o": hno, "seg": r["base_seg"]})
            if hno is not None:
                put(h_rows, hno, {"v": r["verdict"], "o": bno, "seg": r["head_seg"]})

    # --- 3. anything still unfilled is unique to its side ------------------ #
    for no in range(1, nb + 1):
        if b_rows[no] is None:
            txt = base.line(no)
            b_rows[no] = {
                "v": classify_single(txt),
                "o": None,
                "seg": [{"t": txt, "m": bool(txt.strip())}],
            }
    for no in range(1, nh + 1):
        if h_rows[no] is None:
            txt = head.line(no)
            h_rows[no] = {
                "v": classify_single(txt),
                "o": None,
                "seg": [{"t": txt, "m": bool(txt.strip())}],
            }

    # --- 3.5 make the counterpart pointers mutually consistent ------------- #
    # Two passes (anchors, then units) can both claim the same target line from
    # different sources.  The pointer is what "jump to counterpart" and the
    # linked hover use, so an inconsistent pair would send the reviewer to the
    # wrong line.  Keep only pointers that agree in both directions; drop the
    # rest to None, which the UI renders as "no direct counterpart".
    for bno in range(1, nb + 1):
        o = b_rows[bno]["o"]
        if o and (o > nh or h_rows[o]["o"] != bno):
            b_rows[bno]["o"] = None
    for hno in range(1, nh + 1):
        o = h_rows[hno]["o"]
        if o and (o > nb or b_rows[o]["o"] != hno):
            h_rows[hno]["o"] = None

    def pack(rows, model):
        """Compact wire format for a whole file.

        A naive per-line object list costs ~1.4 MB on a 27k-line file, most of
        it repeated keys.  Instead:

          * `v`    one character per line ("i"dentical, "b"lank, "n"=indent,
                   "d"evice, "r"ename, "c"hanged, "m"issing) as a single string
          * `o`    counterpart line number per line, 0 = none (plain int array)
          * `seg`  word-level segments, sparse: only for lines that differ
          * `u`/`t` owning / target unit name, sparse, interned into `names`

        This keeps the whole-file view responsive even on the largest files.
        """
        names: list[str] = []
        name_idx: dict[str, int] = {}

        def intern(s: str | None) -> int | None:
            if not s:
                return None
            i = name_idx.get(s)
            if i is None:
                i = len(names)
                name_idx[s] = i
                names.append(s)
            return i

        vcode = {
            "identical": "i",
            "blank": "b",
            "indent": "n",
            "device": "d",
            "rename": "r",
            "changed": "c",
            "missing": "m",
        }
        v_chars: list[str] = []
        o_list: list[int] = []
        seg: dict[str, Any] = {}
        units: dict[str, int] = {}
        targets: dict[str, int] = {}

        for no in range(1, len(rows)):
            r = rows[no]
            v_chars.append(vcode.get(r["v"], "c"))
            o_list.append(r["o"] or 0)
            if r["v"] not in ("identical", "blank"):
                seg[str(no)] = r["seg"]
            ui = intern(r.get("u"))
            if ui is not None:
                units[str(no)] = ui
            ti = intern(r.get("t"))
            if ti is not None:
                targets[str(no)] = ti

        return {
            "v": "".join(v_chars),
            "o": o_list,
            "seg": seg,
            "u": units,
            "t": targets,
            "names": names,
        }

    return {
        "base": {"path": base.path, "text": base.lines, **pack(b_rows, base)},
        "head": {"path": head.path, "text": head.lines, **pack(h_rows, head)},
    }


# --------------------------------------------------------------------------- #
# file-level summary for the reviewer
# --------------------------------------------------------------------------- #


def summarize(match: FileMatch) -> dict[str, Any]:
    base, head = match.base, match.head

    classes: list[dict[str, Any]] = []
    for q, info in base.class_info.items():
        targets = match.class_map.get(q, [])
        classes.append(
            {
                "base_cls": q,
                "targets": targets,
                "split": len([t for t in targets if t["share"] >= 0.1]) > 1,
                "n_methods": len(base.methods_of(q)),
                "bases": info["bases"],
            }
        )
    new_classes: list[dict[str, Any]] = []
    for q, info in head.class_info.items():
        if q in base.class_info:
            continue
        new_classes.append(
            {
                "head_cls": q,
                "sources": match.class_map_rev.get(q, []),
                "n_methods": len(head.methods_of(q)),
                "bases": info["bases"],
            }
        )
    gone_classes = [q for q in base.class_info if q not in head.class_info]

    units: list[dict[str, Any]] = []
    for b in base.callables:
        c = match.primary("base", b.uid)
        if c is None:
            units.append(
                {
                    "base": b.to_json(),
                    "head": None,
                    "kind": "deleted",
                    "verdict": "missing",
                    "score": None,
                    "reason": None,
                    "stats": None,
                }
            )
            continue
        h = c.other
        rows = align_lines(base, head, b.linenos, h.linenos)
        verdict = rows_verdict(rows)
        if b.qualname == h.qualname:
            kind = "unchanged" if verdict in ("identical", "blank") else "edited"
        elif b.name == h.name:
            kind = "moved"
        else:
            kind = "moved+renamed" if b.cls != h.cls else "renamed"
        if not c.mutual:
            kind += " (shared target)"
        units.append(
            {
                "base": b.to_json(),
                "head": h.to_json(),
                "kind": kind,
                "verdict": verdict,
                "score": round(c.score, 3),
                "body_score": round(c.body, 3),
                "reason": c.reason,
                "mutual": c.mutual,
                "stats": rows_stats(rows),
                "n_candidates": len(match.candidates("base", b.uid)),
            }
        )
    added = [
        h.to_json()
        for h in head.callables
        if not match.candidates("head", h.uid)
    ]
    return {
        "classes": classes,
        "new_classes": new_classes,
        "gone_classes": gone_classes,
        "units": units,
        "added_units": added,
        "base_parse_error": base.parse_error,
        "head_parse_error": head.parse_error,
    }


# --------------------------------------------------------------------------- #
# cache
# --------------------------------------------------------------------------- #

# LRU caches with per-key locks.
#
# Two properties matter when several people use one server:
#   * evicting must drop only the least recently used entry.  A plain
#     `.clear()` would throw away every user's analysis the moment the cache
#     filled, so one person browsing a large PR would repeatedly un-cache
#     everyone else's file.
#   * two threads asking for the *same* file must not both run the analysis,
#     which can take seconds on a 27k-line file; the second should wait and
#     reuse the result.
_MATCH_CACHE: "OrderedDict[str, FileMatch]" = OrderedDict()
_MODEL_CACHE: "OrderedDict[str, FileModel]" = OrderedDict()
_CACHE_LOCK = threading.Lock()
_KEY_LOCKS: dict[str, threading.Lock] = {}

MATCH_CACHE_MAX = 64
MODEL_CACHE_MAX = 128


def _key_lock(k: str) -> threading.Lock:
    with _CACHE_LOCK:
        lk = _KEY_LOCKS.get(k)
        if lk is None:
            lk = threading.Lock()
            _KEY_LOCKS[k] = lk
        return lk


def _cache_get(cache: "OrderedDict[str, Any]", k: str) -> Any:
    with _CACHE_LOCK:
        v = cache.get(k)
        if v is not None:
            cache.move_to_end(k)
        return v


def _cache_put(cache: "OrderedDict[str, Any]", k: str, v: Any, limit: int) -> None:
    with _CACHE_LOCK:
        cache[k] = v
        cache.move_to_end(k)
        while len(cache) > limit:
            old, _ = cache.popitem(last=False)
            _KEY_LOCKS.pop(old, None)


def cache_stats() -> dict[str, int]:
    with _CACHE_LOCK:
        return {
            "matches": len(_MATCH_CACHE),
            "models": len(_MODEL_CACHE),
            "key_locks": len(_KEY_LOCKS),
        }


def _key(*parts: str) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update(p.encode("utf-8", "replace"))
        h.update(b"\0")
    return h.hexdigest()


def get_model(text: str, path: str = "") -> FileModel:
    k = _key(path, text)
    m = _cache_get(_MODEL_CACHE, k)
    if m is not None:
        return m
    with _key_lock("model:" + k):
        m = _cache_get(_MODEL_CACHE, k)
        if m is None:
            m = FileModel(text, path)
            _cache_put(_MODEL_CACHE, k, m, MODEL_CACHE_MAX)
    return m


def build_match(base_text: str, head_text: str, path: str = "") -> FileMatch:
    k = _key(path, base_text, head_text)
    m = _cache_get(_MATCH_CACHE, k)
    if m is not None:
        return m
    # only threads wanting this exact file wait here; others proceed freely
    with _key_lock("match:" + k):
        m = _cache_get(_MATCH_CACHE, k)
        if m is None:
            m = FileMatch(get_model(base_text, path), get_model(head_text, path))
            _cache_put(_MATCH_CACHE, k, m, MATCH_CACHE_MAX)
    return m
