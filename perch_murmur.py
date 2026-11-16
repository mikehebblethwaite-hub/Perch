# Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md.
# perch_murmur: Perch's own number grids (Mike, 16 Nov 2026: "write our own numpy in C for perch"). A murmuration: thousands of
# starlings turning as one; every number a bird, the whole grid moving at once. The loops are C (libperch-murmur.so, perch-murmur.c);
# this is the face Perch's Python uses: Grid, views, slicing, broadcasting, the familiar names (mm.zeros, mm.median, g.mean(1) ...).
#   import perch_murmur as mm
# Built to NOT have numpy's known faults (looked up 16 Nov 2026):
#   * never wraps: uint8 255 + 1 is 256 (maths on small whole numbers gives a type big enough); a value that does not fit a
#     whole-number grid (out of range, not whole, NaN) is refused in plain words, never stored wrapped or cut.
#   * no silent float -> whole cut: astype(uint8) of 3.9 asks how= 'round', 'floor' or 'trunc'.
#   * no silent outer grid: a shape that would stretch the BIGGER grid ((n,) against (n,1) -> n x n) is refused; a meant outer grid
#     (both with the same number of axes, as ogrid gives) works.
#   * views and copies said: a slice is a view (writes go through); a mask or index list gives a READ-ONLY copy, and writing to it
#     is an error that says so (numpy silently writes to a throwaway copy).
#   * one value comes back as a plain Python number (json works; numpy's float32 does not).
#   * plain errors: "a grid of 12 numbers is not one yes/no: use .any() or .all()".
#   * no ABI to break: a plain C library through ctypes; nothing to rebuild for a new Python.
#   * sums are done in doubles (Kahan), float32 is only how a grid is kept: no lost precision in long sums.
#   * the mean (median, max ...) of nothing is an error, not NaN with a warning.
import builtins as _b, ctypes, math, os, struct   # _b: Python's own sum/max/any... (murmur has its own)

_LIB = ctypes.CDLL(os.path.join(os.path.dirname(os.path.abspath(__file__)), "libperch-murmur.so"))
_MAXD = 8


class MurmurError(ValueError):
    """Every murmur error: plain words, what to do."""


class _C(ctypes.Structure):
    _fields_ = [("data", ctypes.c_void_p), ("dtype", ctypes.c_int), ("ndim", ctypes.c_int),
                ("shape", ctypes.c_int64 * _MAXD), ("strides", ctypes.c_int64 * _MAXD)]


_P = ctypes.POINTER(_C)
for _n, _args in {"pn_binary": [ctypes.c_int, _P, _P, _P], "pn_unary": [ctypes.c_int, _P, _P, ctypes.c_int, ctypes.c_double, ctypes.c_double],
                  "pn_reduce": [ctypes.c_int, _P, _P], "pn_percentile": [_P, _P, ctypes.c_double], "pn_count": [_P],
                  "pn_nonzero": [_P, ctypes.POINTER(ctypes.c_int64)],
                  "pn_take": [_P, _P, ctypes.POINTER(ctypes.c_int64), ctypes.c_int64],
                  "pn_put": [_P, _P, ctypes.POINTER(ctypes.c_int64), ctypes.c_int64],
                  "pn_eigh": [ctypes.c_int, ctypes.POINTER(ctypes.c_double), ctypes.POINTER(ctypes.c_double), ctypes.POINTER(ctypes.c_double)],
                  "pn_label": [_P, ctypes.POINTER(ctypes.c_int64), ctypes.POINTER(ctypes.c_int64)],
                  "pn_where": [_P, _P, _P, _P], "pn_bad": [], "pn_vecdot": [_P, _P, ctypes.POINTER(ctypes.c_double)],
                  "pn_reduce_mid": [ctypes.c_int, _P, _P], "pn_rfft": [_P, _P, ctypes.c_int64],
                  "pn_irfft": [_P, _P, ctypes.c_int, ctypes.c_int64, ctypes.c_int64],
                  "pn_cpart": [_P, ctypes.POINTER(ctypes.c_double), ctypes.c_int64, ctypes.c_int],
                  "pn_ramp": [_P, ctypes.c_double, ctypes.c_double], "pn_mask_take": [_P, _P, _P],
                  "pn_matmul": [_P, _P, _P], "pn_sort": [_P, _P, ctypes.c_int],
                  "pn_correlate": [ctypes.POINTER(ctypes.c_double), ctypes.POINTER(ctypes.c_double), ctypes.c_int64, ctypes.POINTER(ctypes.c_double), ctypes.c_int64],
                  "pn_roots": [ctypes.POINTER(ctypes.c_double), ctypes.c_int, ctypes.POINTER(ctypes.c_double), ctypes.POINTER(ctypes.c_double)], "pn_lstsq": [ctypes.POINTER(ctypes.c_double), ctypes.POINTER(ctypes.c_double), ctypes.c_int64, ctypes.c_int64, ctypes.POINTER(ctypes.c_double)],
                  "pn_shift_match": [_P, _P, ctypes.c_int], "pn_take_rows": [_P, _P, ctypes.POINTER(ctypes.c_int64), ctypes.c_int64],
                  "pn_cumsum": [_P, _P], "pn_rng_fill": [ctypes.POINTER(ctypes.c_uint64), ctypes.POINTER(ctypes.c_double), ctypes.c_int64, ctypes.c_int]}.items():
    getattr(_LIB, _n).argtypes = _args
_LIB.pn_count.restype = _LIB.pn_nonzero.restype = _LIB.pn_label.restype = _LIB.pn_bad.restype = ctypes.c_int64
_LIB.pn_alloc.argtypes = [ctypes.c_int64]; _LIB.pn_alloc.restype = ctypes.c_void_p
_LIB.pn_mask_take.restype = ctypes.c_int64
_LIB.pn_roots.restype = ctypes.c_int; _LIB.pn_shift_match.restype = ctypes.c_double
_LIB.pn_free.argtypes = [ctypes.c_void_p]; _LIB.pn_free.restype = None


class _Mem:
    """A grid's memory from murmur's own allocator (huge pages for big grids, not zeroed: every grid is filled at once);
    given back when the last grid (or view) using it is gone."""
    __slots__ = ("ptr",)
    def __init__(self, nbytes):
        self.ptr = _LIB.pn_alloc(nbytes)
        if not self.ptr: raise MemoryError(f"murmur could not get {nbytes} bytes")
    def __del__(self, _free=_LIB.pn_free):
        if self.ptr: _free(self.ptr); self.ptr = None


class _DT:
    def __init__(self, code, name, size, kind, fmt, typestr):
        self.code, self.name, self.size, self.kind, self.fmt, self.typestr = code, name, size, kind, fmt, typestr
    def __repr__(self): return self.name
    def __eq__(self, o):
        try: return _dt(o) is self
        except MurmurError: return False
    def __hash__(self): return self.code


uint8 = _DT(0, "uint8", 1, "u", "B", "|u1")
bool_ = _DT(1, "bool", 1, "b", "?", "|b1")
int64 = _DT(2, "int64", 8, "i", "q", "<i8")
float32 = _DT(3, "float32", 4, "f", "f", "<f4")
float64 = _DT(4, "float64", 8, "f", "d", "<f8")
int16 = _DT(5, "int16", 2, "i", "h", "<i2")              # sound as it comes (16-bit samples)
complex128 = _DT(6, "complex128", 16, "c", "dd", "<c16") # FFT results: to the C, pairs of float64 (re, im)
_BY_NAME = {"uint8": uint8, "u1": uint8, "bool": bool_, "int64": int64, "int": int64, "float32": float32, "float64": float64,
            "float": float64, "f4": float32, "f8": float64, "int16": int16, "i2": int16, "<i2": int16, "complex128": complex128,
            "complex": complex128, "<f4": float32, "<f8": float64}
pi, e, nan, inf = math.pi, math.e, float("nan"), float("inf")


def _dt(d):
    if isinstance(d, _DT): return d
    if d is bool: return bool_
    if d is int: return int64
    if d is float: return float64
    if d is complex: return complex128
    if isinstance(d, str) and d in _BY_NAME: return _BY_NAME[d]
    name = getattr(d, "__name__", None) or str(d)
    if name in _BY_NAME: return _BY_NAME[name]
    raise MurmurError(f"murmur keeps uint8, bool, int64, float32 and float64 grids; {d!r} is none of them")


# ---------------------------------------------------------------------------------------------------------------- the Grid

class Grid:
    """A grid of numbers: a block of memory seen with a shape and strides. A slice is a VIEW of the same memory."""
    __slots__ = ("_owner", "_addr", "shape", "strides", "dtype", "_ro", "_weak", "_cc")

    def __init__(self, owner, addr, shape, strides, dtype, ro=False, weak=False):
        self._owner, self._addr, self.shape, self.strides, self.dtype, self._ro, self._weak = owner, addr, tuple(shape), tuple(strides), dtype, ro, weak
        self._cc = None                          # its C description, made once (a grid's shape, strides and place never change)

    # -- facts
    @property
    def ndim(self): return len(self.shape)
    @property
    def size(self): return math.prod(self.shape)
    @property
    def itemsize(self): return self.dtype.size
    @property
    def nbytes(self): return self.size * self.dtype.size
    @property
    def T(self): return self.transpose()

    def _c(self):
        if self.dtype is complex128:
            raise MurmurError("a complex grid: murmur does abs, .real, .imag, angle, conj, and the FFT on it; "
                              "take abs(x) (or x.real) first for other maths")
        return self._craw()

    def _craw(self):                         # the C view, complex as float64 pairs to the FFT (the C reads them itself)
        if self._cc is not None: return self._cc
        if self.ndim > _MAXD: raise MurmurError(f"murmur grids have at most {_MAXD} axes; this has {self.ndim}")
        c = _C(); c.data = self._addr; c.dtype = 4 if self.dtype is complex128 else self.dtype.code; c.ndim = self.ndim
        for i, (n, s) in enumerate(zip(self.shape, self.strides)): c.shape[i] = n; c.strides[i] = s
        self._cc = c
        return c

    def _contiguous(self):
        st, acc = [], self.dtype.size
        for n in reversed(self.shape): st.append(acc); acc *= n
        return tuple(reversed(st)) == self.strides or self.size <= 1

    def __repr__(self):
        body = repr(self.tolist()) if self.size <= 200 else f"<{self.size} numbers>"
        return f"Grid({self.dtype.name}, shape {self.shape}) {body}"

    # -- one value out: plain Python numbers
    def _scalar(self):
        raw = ctypes.string_at(self._addr, self.dtype.size)
        if self.dtype is complex128:
            re, im = struct.unpack("<dd", raw); return complex(re, im)
        v = struct.unpack("<" + self.dtype.fmt, raw)[0]
        return bool(v) if self.dtype is bool_ else v

    def item(self):
        if self.size != 1: raise MurmurError(f"item() wants one number; this grid holds {self.size}")
        return self.reshape(())._scalar() if self.ndim else self._scalar()

    def __float__(self): return float(self.item())
    def __int__(self): return int(self.item())
    def __index__(self):
        if self.dtype.kind not in "uib": raise MurmurError("only a whole-number grid can be used as an index")
        return int(self.item())
    def __round__(self, n=None): return round(float(self), n) if n is not None else round(float(self))
    def __bool__(self):
        if self.size != 1:
            raise MurmurError(f"a grid of {self.size} numbers is not one yes/no: use .any() (is any true?) or .all() (are all true?)")
        return bool(self.item())
    def __len__(self):
        if not self.ndim: raise MurmurError("a single number has no length")
        return self.shape[0]
    def __iter__(self):
        if self.ndim == 1:
            yield from self.tolist()                  # a row: plain numbers, read in one go
        else:
            for i in range(len(self)): yield self._basic(i)

    def tolist(self):
        """All the numbers as (nested) Python lists, read in one go (not one at a time)."""
        if not self.ndim: return self._scalar()
        g = self if self._contiguous() else self.copy()
        n = g.size
        raw = ctypes.string_at(g._addr, n * g.dtype.size)
        if g.dtype is complex128:
            f = struct.unpack(f"<{2 * n}d", raw); flat = [complex(f[2 * i], f[2 * i + 1]) for i in range(n)]
        else:
            flat = list(struct.unpack(f"<{n}{g.dtype.fmt}", raw))
            if g.dtype is bool_: flat = [bool(v) for v in flat]
        for d in reversed(g.shape[1:]):              # back into rows
            flat = [flat[i:i + d] for i in range(0, len(flat), d)]
        return flat

    def tobytes(self):
        g = self if self._contiguous() else self.copy()
        return ctypes.string_at(g._addr, g.nbytes)

    @property
    def __array_interface__(self):           # lets PIL's Image.fromarray read a grid (it takes tobytes() when strides are given)
        g = self if self._contiguous() else self.copy()
        return {"shape": g.shape, "typestr": g.dtype.typestr, "data": (g._addr, False), "strides": g.strides, "version": 3}

    # -- shape work (views)
    def reshape(self, *shape):
        if len(shape) == 1 and isinstance(shape[0], (tuple, list)): shape = tuple(shape[0])
        shape = list(shape)
        if shape.count(-1) > 1: raise MurmurError("reshape: only one axis can be -1 (worked out)")
        if -1 in shape:
            known = math.prod(s for s in shape if s != -1)
            if known == 0 or self.size % known: raise MurmurError(f"reshape: {self.size} numbers do not make shape {tuple(shape)}")
            shape[shape.index(-1)] = self.size // known
        if math.prod(shape) != self.size: raise MurmurError(f"reshape: {self.size} numbers do not make shape {tuple(shape)}")
        g = self if self._contiguous() else self.copy()
        st, acc = [], g.dtype.size
        for n in reversed(shape): st.append(acc); acc *= n
        return Grid(g._owner, g._addr, shape, tuple(reversed(st)), g.dtype, self._ro)

    def ravel(self): return self.reshape(-1)
    flatten = ravel

    def transpose(self, *axes):
        if not axes: axes = tuple(reversed(range(self.ndim)))
        elif len(axes) == 1 and isinstance(axes[0], (tuple, list)): axes = tuple(axes[0])
        return Grid(self._owner, self._addr, [self.shape[a] for a in axes], [self.strides[a] for a in axes], self.dtype, self._ro)

    @property
    def real(self):
        return _pairs(self)[..., 0] if self.dtype is complex128 else self
    @property
    def imag(self):
        return _pairs(self)[..., 1] if self.dtype is complex128 else zeros(self.shape, _float_of(self.dtype))

    def copy(self):
        if self.dtype is complex128:
            out = _new(self.shape, complex128)
            _assign(_pairs(out), _pairs(self))
            return out
        out = _new(self.shape, self.dtype)
        _LIB.pn_unary(0, ctypes.byref(out._c()), ctypes.byref(self._c()), 0, 0.0, 0.0)
        _LIB.pn_bad()
        return out

    def astype(self, dtype, how=None):
        """Into another kind of grid. Float into a whole-number grid needs how= 'round', 'floor' or 'trunc' unless every value is
        already whole; a value out of range is refused (clip first), never wrapped."""
        d = _dt(dtype)
        src = self
        if how:
            ops = {"round": 12, "floor": 10, "trunc": 18, "ceil": 11}
            if how not in ops: raise MurmurError(f"how= must be 'round', 'floor', 'trunc' or 'ceil', not {how!r}")
            src = _unary_raw(self, ops[how], self.dtype)
        out = _new(self.shape, d)
        _LIB.pn_unary(0, ctypes.byref(out._c()), ctypes.byref(src._c()), 0, 0.0, 0.0)
        bad = _LIB.pn_bad()
        if bad:
            lo, hi = ("0", "255") if d is uint8 else ("-32768", "32767") if d is int16 else ("-2**63", "2**63")
            raise MurmurError(f"{bad} values do not fit {d.name} (whole numbers {lo}..{hi}): clip them first"
                              + ("" if how else ", or say how= 'round', 'floor' or 'trunc' for numbers that are not whole"))
        return out

    # -- indexing
    def _basic(self, key):
        if not isinstance(key, tuple): key = (key,)
        if _b.any(k is Ellipsis for k in key):
            i = key.index(Ellipsis)
            fill = self.ndim - (len(key) - 1 - _b.sum(1 for k in key if k is None))
            key = key[:i] + (slice(None),) * fill + key[i + 1:]
        addr, shape, strides, ax = self._addr, [], [], 0
        for k in key:
            if k is None: shape.append(1); strides.append(0); continue
            if ax >= self.ndim: raise MurmurError(f"too many indices: the grid has {self.ndim} axes")
            n, s = self.shape[ax], self.strides[ax]
            if isinstance(k, slice):
                start, stop, step = k.indices(n)
                cnt = len(range(start, stop, step))
                addr += start * s; shape.append(cnt); strides.append(s * step)
            else:
                i = int(k)
                if i < -n or i >= n: raise MurmurError(f"index {i} is outside axis {ax}, which runs 0..{n - 1}")
                addr += (i % n) * s
            ax += 1
        shape += self.shape[ax:]; strides += self.strides[ax:]
        return Grid(self._owner, addr, shape, strides, self.dtype, self._ro)

    def _flat_positions(self, key):
        """For a mask (or a list of indices on the first axis): the flat positions (C order over self.shape) it picks, and the
        picked shape."""
        if isinstance(key, (list, tuple)) and not _b.any(isinstance(k, (slice, type(None), type(Ellipsis))) for k in key) and \
                not _b.any(isinstance(k, Grid) for k in key):
            key = array(key, dtype=int64)
        if key.dtype is bool_:
            k = key.ndim
            if key.shape != self.shape[:k]:
                raise MurmurError(f"a yes/no mask of shape {key.shape} does not fit a grid of shape {self.shape}")
            n = _LIB.pn_count(ctypes.byref(key._c()))
            buf = (ctypes.c_int64 * _b.max(1, n))()
            _LIB.pn_nonzero(ctypes.byref(key._c()), buf)
            rest = math.prod(self.shape[k:])
            lead = list(buf[:n])
            pos = lead if rest == 1 else [p * rest + j for p in lead for j in range(rest)]
            return pos, (n,) + self.shape[k:]
        if key.dtype.kind in "ui":
            if key.ndim != 1: raise MurmurError("an index list must be one row of whole numbers")
            n0 = self.shape[0]; rest = math.prod(self.shape[1:])
            idx = []
            for i in key.tolist():
                if i < -n0 or i >= n0: raise MurmurError(f"index {i} is outside axis 0, which runs 0..{n0 - 1}")
                idx.append(i % n0)
            pos = idx if rest == 1 else [p * rest + j for p in idx for j in range(rest)]
            return pos, (len(idx),) + self.shape[1:]
        raise MurmurError(f"a grid of {key.dtype.name} cannot pick from a grid: use a yes/no mask or whole-number indices")

    def __getitem__(self, key):
        if isinstance(key, Grid) and key.dtype is bool_ and key.shape == self.shape and self.dtype is not complex128:
            n = _LIB.pn_count(ctypes.byref(key._c()))   # a mask of the grid's own shape: picked in C
            out = _new((n,), self.dtype)
            if n: _LIB.pn_mask_take(ctypes.byref(out._c()), ctypes.byref(self._c()), ctypes.byref(key._c()))
            out._ro = "copy"
            return out
        if isinstance(key, tuple) and _b.any(isinstance(k, (Grid, list)) for k in key):
            return self._fancy(key)
        rows = self._row_pick(key)
        if rows is not None: return rows
        if isinstance(key, Grid) or (isinstance(key, list)):
            pos, shape = self._flat_positions(key if isinstance(key, Grid) else key)
            out = _new(shape, self.dtype)
            arr = (ctypes.c_int64 * _b.max(1, len(pos)))(*pos)
            o = out.reshape(-1)
            _LIB.pn_take(ctypes.byref(o._c()), ctypes.byref(self._c()), arr, len(pos))
            out._ro = "copy"                       # a copy: writing to it would change nothing in self, so it is closed
            return out
        if type(key) is int and self.ndim == 1:      # one number from a row: read straight from memory, no view made
            n = self.shape[0]
            if key < -n or key >= n: raise MurmurError(f"index {key} is outside axis 0, which runs 0..{n - 1}")
            addr = self._addr + (key % n) * self.strides[0]
            if self.dtype is complex128:
                re, im = struct.unpack("<dd", ctypes.string_at(addr, 16)); return complex(re, im)
            v = struct.unpack("<" + self.dtype.fmt, ctypes.string_at(addr, self.dtype.size))[0]
            return bool(v) if self.dtype is bool_ else v
        g = self._basic(key)
        return g._scalar() if not g.ndim else g

    def _row_pick(self, key):
        """Whole rows by a mask over the leading axes, or by an index list on the first axis: rows handed over in C, not
        number by number. None when the key is not one of these."""
        if isinstance(key, list):
            if not key or not _b.all(isinstance(v, int) and not isinstance(v, bool) for v in key): return None
            idx = key
        elif isinstance(key, Grid) and key.dtype.kind in "ui" and key.ndim == 1:
            idx = key.tolist()
        elif isinstance(key, Grid) and key.dtype is bool_ and 0 < key.ndim < self.ndim and key.shape == self.shape[:key.ndim]:
            n = _LIB.pn_count(ctypes.byref(key._c()))
            buf = (ctypes.c_int64 * _b.max(1, n))()
            _LIB.pn_nonzero(ctypes.byref(key._c()), buf)
            lead = math.prod(self.shape[:key.ndim]); rest_shape = self.shape[key.ndim:]
            return self._take_rows(buf, n, lead, rest_shape)
        else:
            return None
        if self.ndim < 1 or self.dtype is complex128: return None
        n0 = self.shape[0]
        for v in idx:
            if v < -n0 or v >= n0: raise MurmurError(f"index {v} is outside axis 0, which runs 0..{n0 - 1}")
        buf = (ctypes.c_int64 * _b.max(1, len(idx)))(*[v % n0 for v in idx])
        return self._take_rows(buf, len(idx), n0, self.shape[1:])

    def _take_rows(self, buf, n, lead, rest_shape):
        """Rows numbered over the first len(shape) - len(rest_shape) axes, each with the numbers of rest_shape."""
        if self.dtype is complex128: return None
        k = self.ndim - len(rest_shape)                  # how many leading axes the row numbers run over
        rest = math.prod(rest_shape)
        if k == 1 and len(rest_shape) <= 1:              # one leading axis, rows of one axis: any steps work as they are
            a2 = Grid(self._owner, self._addr, (lead, rest), (self.strides[0], self.strides[1] if rest_shape else self.dtype.size), self.dtype)
        else:                                            # several axes joined: needs the memory side by side first
            src = self if self._contiguous() else self.copy()
            a2 = Grid(src._owner, src._addr, (lead, rest), (rest * src.dtype.size, src.dtype.size), src.dtype)
        out = _new((n, rest), self.dtype)
        if n: _LIB.pn_take_rows(ctypes.byref(out._c()), ctypes.byref(a2._c()), buf, n)
        out = out.reshape((n,) + tuple(rest_shape))
        out._ro = "copy"
        return out

    def _fancy(self, key):
        """d[rows, cols] (index lists side by side: element by element) or D[i, mask] (whole-number indices first, then one mask
        or index list): picked as a read-only copy. Other mixes are refused in plain words."""
        arrs = [isinstance(k, (Grid, list)) for k in key]
        if _b.all(arrs) or _b.all(a_ or isinstance(k, int) for a_, k in zip(arrs, key)) and _b.sum(arrs) >= 2:
            idx = [(_as(k).reshape(-1).tolist() if isinstance(k, (Grid, list)) else [int(k)]) for k in key]
            L = _b.max(len(i) for i in idx)
            if _b.any(len(i) not in (1, L) for i in idx): raise MurmurError("index lists side by side must be of one length")
            idx = [i * L if len(i) == 1 else i for i in idx]
            k = len(key); rest = math.prod(self.shape[k:]); pos = []
            for n in range(L):
                f = 0
                for ax in range(k):
                    v = idx[ax][n]; size = self.shape[ax]
                    if v < -size or v >= size: raise MurmurError(f"index {v} is outside axis {ax}, which runs 0..{size - 1}")
                    f = f * size + (v % size)
                pos.extend(f * rest + j for j in range(rest))
            out = _new((L,) + self.shape[k:], self.dtype)
            arr = (ctypes.c_int64 * _b.max(1, len(pos)))(*pos)
            _LIB.pn_take(ctypes.byref(out.reshape(-1)._c()), ctypes.byref(self._c()), arr, len(pos))
            out._ro = "copy"
            return out
        if arrs[-1] and not _b.any(arrs[:-1]) and _b.all(isinstance(k, int) for k in key[:-1]):
            return self._basic(tuple(key[:-1]))[key[-1]]
        raise MurmurError("this mix of index lists and slices is not in murmur: pick in two steps (grid[a][b])")

    def __setitem__(self, key, value):
        if self._ro: raise MurmurError(_READ_ONLY.get(self._ro, _READ_ONLY["copy"]))
        if isinstance(key, Grid) or isinstance(key, list):
            pos, shape = self._flat_positions(key)
            v = _as(value)
            if v.size not in (1, math.prod(shape)) and v.shape != shape[1:]:
                raise MurmurError(f"{v.size} values cannot fill the {len(pos)} places the mask picks")
            if v.size != 1 and v.shape != shape: v = _stretch(v, shape)
            v = v.reshape(-1) if v.size != 1 else v.reshape(1)
            arr = (ctypes.c_int64 * _b.max(1, len(pos)))(*pos)
            _LIB.pn_put(ctypes.byref(self._c()), ctypes.byref(v._c()), arr, len(pos))
            _check_fit(self.dtype)
            return
        view = self._basic(key)
        _assign(view, value)

    # -- maths
    def __add__(self, o): return _binary(0, self, o)
    def __radd__(self, o): return _binary(0, o, self)
    def __sub__(self, o): return _binary(1, self, o)
    def __rsub__(self, o): return _binary(1, o, self)
    def __mul__(self, o): return _binary(2, self, o)
    def __rmul__(self, o): return _binary(2, o, self)
    def __truediv__(self, o): return _binary(3, self, o)
    def __rtruediv__(self, o): return _binary(3, o, self)
    def __pow__(self, o): return _binary(4, self, o)
    def __rpow__(self, o): return _binary(4, o, self)
    def __floordiv__(self, o): return _binary(16, self, o)
    def __rfloordiv__(self, o): return _binary(16, o, self)
    def __mod__(self, o): return _binary(17, self, o)
    def __rmod__(self, o): return _binary(17, o, self)
    def __lt__(self, o): return _binary(7, self, o)
    def __le__(self, o): return _binary(8, self, o)
    def __gt__(self, o): return _binary(9, self, o)
    def __ge__(self, o): return _binary(10, self, o)
    def __eq__(self, o): return _binary(11, self, o)
    def __ne__(self, o): return _binary(12, self, o)
    def __and__(self, o): return _logic(13, self, o)
    def __rand__(self, o): return _logic(13, o, self)
    def __or__(self, o): return _logic(14, self, o)
    def __ror__(self, o): return _logic(14, o, self)
    def __xor__(self, o): return _logic(15, self, o)
    def __invert__(self):
        if self.dtype is not bool_: raise MurmurError("~ (not) is for yes/no grids; for numbers use -grid")
        return _unary_raw(self, 13, bool_)
    def __neg__(self):
        if self.dtype is bool_: raise MurmurError("-grid on a yes/no grid: did you mean ~grid (not)?")
        return _unary_raw(self, 1, int64 if self.dtype is uint8 else self.dtype)
    def __pos__(self): return self
    def __abs__(self): return _unary_raw(self, 2, self.dtype)
    def __matmul__(self, o): return matmul(self, o)
    def __rmatmul__(self, o): return matmul(o, self)
    __hash__ = None

    def __iadd__(self, o): _assign(self, _binary(0, self, o)); return self
    def __isub__(self, o): _assign(self, _binary(1, self, o)); return self
    def __imul__(self, o): _assign(self, _binary(2, self, o)); return self
    def __itruediv__(self, o): _assign(self, _binary(3, self, o)); return self

    # -- along an axis
    def sum(self, axis=None, keepdims=False): return _reduce(self, "sum", axis, keepdims)
    def prod(self, axis=None, keepdims=False): return _reduce(self, "prod", axis, keepdims)
    def mean(self, axis=None, keepdims=False): return _reduce(self, "mean", axis, keepdims)
    def max(self, axis=None, keepdims=False): return _reduce(self, "max", axis, keepdims)
    def min(self, axis=None, keepdims=False): return _reduce(self, "min", axis, keepdims)
    def argmax(self, axis=None): return _reduce(self, "argmax", axis, False)
    def argmin(self, axis=None): return _reduce(self, "argmin", axis, False)
    def any(self, axis=None, keepdims=False): return _reduce(self, "any", axis, keepdims)
    def all(self, axis=None, keepdims=False): return _reduce(self, "all", axis, keepdims)
    def var(self, axis=None, keepdims=False): return var(self, axis, keepdims)
    def std(self, axis=None, keepdims=False): return std(self, axis, keepdims)
    def clip(self, lo, hi): return clip(self, lo, hi)
    def cumsum(self, axis=None): return cumsum(self, axis)
    def nonzero(self): return nonzero(self)


# ------------------------------------------------------------------------------------------------------------- making grids

def _new(shape, dtype):
    shape = tuple(int(s) for s in shape)
    if _b.any(s < 0 for s in shape): raise MurmurError(f"a shape cannot have a negative length: {shape}")
    dtype = _dt(dtype)
    mem = _Mem(_b.max(1, math.prod(shape) * dtype.size))
    st, acc = [], dtype.size
    for n in reversed(shape): st.append(acc); acc *= n
    return Grid(mem, mem.ptr, shape, tuple(reversed(st)), dtype)


def _shape_of(x):
    if isinstance(x, (list, tuple)):
        if not x: return (0,)
        inner = [_shape_of(v) for v in x]
        if _b.any(s != inner[0] for s in inner): raise MurmurError("a list of lists of different lengths is not a grid")
        return (len(x),) + inner[0]
    return ()


def _flat(x, out):
    if isinstance(x, (list, tuple)):
        for v in x: _flat(v, out)
    elif isinstance(x, Grid):
        out.extend(x.reshape(-1).tolist() if x.ndim else [x.item()])
    else:
        out.append(x)


def array(x, dtype=None):
    if isinstance(x, Grid):
        return x.copy() if dtype is None else x.astype(dtype)
    if isinstance(x, (list, tuple)) and x and _b.all(isinstance(v, Grid) for v in x):
        return stack(x).astype(dtype) if dtype else stack(x)
    if isinstance(x, (list, tuple)) and not _b.any(isinstance(v, (list, tuple, Grid)) for v in x):
        shape, vals = (len(x),), list(x)                 # a flat row of numbers: no element-by-element walk
        if _b.any(isinstance(v, complex) for v in vals):  # complex numbers: kept as pairs
            out = _new(shape, complex128)
            flat = [f for v in vals for f in (complex(v).real, complex(v).imag)]
            if flat: ctypes.memmove(out._addr, struct.pack(f"<{len(flat)}d", *flat), len(flat) * 8)
            return out
    else:
        shape = _shape_of(x)
        vals = []; _flat(x, vals)
    if dtype is None:
        if vals and _b.all(isinstance(v, bool) for v in vals): d = bool_
        elif _b.all(isinstance(v, (bool, int)) for v in vals): d = int64
        else: d = float64
    else:
        d = _dt(dtype)
    out = _new(shape, float64 if d.kind in "ub" or d is int64 else d)
    if vals:
        fl = (ctypes.c_double * len(vals))(*[float(v) for v in vals]) if out.dtype is float64 else None
        if fl is not None:
            ctypes.memmove(out._addr, fl, len(vals) * 8)
        else:
            ctypes.memmove(out._addr, struct.pack(f"<{len(vals)}{out.dtype.fmt}", *vals), len(vals) * out.dtype.size)
    return out if out.dtype is d else out.astype(d)


def asarray(x, dtype=None):
    """A grid from a grid (no copy when the type already fits), a list, or a picture (PIL Image: height x width [x layers])."""
    if isinstance(x, Grid): return x if dtype is None or _dt(dtype) is x.dtype else x.astype(dtype)
    if hasattr(x, "tobytes") and hasattr(x, "mode") and hasattr(x, "size"):
        modes = {"RGB": 3, "RGBA": 4, "L": 1, "1": 1}
        im = x if x.mode in modes else x.convert("RGB")
        w, h = im.size; ch = modes[im.mode]
        raw = (im.convert("L") if im.mode == "1" else im).tobytes()
        g = frombuffer(raw, uint8).reshape((h, w) if ch == 1 else (h, w, ch))
        return g if dtype is None else g.astype(dtype)
    return array(x, dtype)


def frombuffer(data, dtype=uint8):
    d = _dt(dtype)
    if len(data) % d.size: raise MurmurError(f"{len(data)} bytes are not a whole number of {d.name} values")
    out = _new((len(data) // d.size,), d)
    ctypes.memmove(out._addr, bytes(data), len(data))
    return out


def full(shape, value, dtype=None):
    if isinstance(shape, int): shape = (shape,)
    d = _dt(dtype) if dtype is not None else (bool_ if isinstance(value, bool) else int64 if isinstance(value, int) else float64)
    out = _new(shape, d)
    _assign(out, value)
    return out


def zeros(shape, dtype=float64): return full(shape, 0, dtype)
def ones(shape, dtype=float64): return full(shape, 1, dtype)
def empty(shape, dtype=float64): return zeros(shape, dtype)
def zeros_like(g, dtype=None): return zeros(_as(g).shape, dtype or _as(g).dtype)
def ones_like(g, dtype=None): return ones(_as(g).shape, dtype or _as(g).dtype)
def full_like(g, value, dtype=None): return full(_as(g).shape, value, dtype or _as(g).dtype)


def _ramp(n, start, step, d):
    out = _new((n,), d)
    if n: _LIB.pn_ramp(ctypes.byref(out._c()), float(start), float(step)); _check_fit(d)
    return out


def arange(start, stop=None, step=1, dtype=None):
    if stop is None: start, stop = 0, start
    n = _b.max(0, math.ceil((stop - start) / step))
    d = _dt(dtype) if dtype else (int64 if _b.all(isinstance(v, int) for v in (start, stop, step)) else float64)
    return _ramp(n, start, step, d)


def linspace(a, b, n=50):
    if n < 1: return _new((0,), float64)
    if n == 1: return array([float(a)])
    out = _ramp(n, a, (b - a) / (n - 1), float64)
    _assign(out[n - 1:], float(b))                    # the end exactly b, as numpy
    return out


class _OGrid:
    """ogrid[:h, :w] -> a (h,1) column and a (1,w) row: an outer grid you mean (same number of axes, so murmur allows it)."""
    def __getitem__(self, key):
        key = key if isinstance(key, tuple) else (key,)
        out = []
        for i, k in enumerate(key):
            r = arange(k.start or 0, k.stop, k.step or 1)
            shape = [1] * len(key); shape[i] = len(r)
            out.append(r.reshape(shape))
        return tuple(out) if len(out) > 1 else out[0]


ogrid = _OGrid()


def _as(x):
    if isinstance(x, Grid): return x
    if isinstance(x, bool): g = _new((), bool_); _set_scalar(g, x); g._weak = True; return g
    if isinstance(x, int): g = _new((), int64); _set_scalar(g, x); g._weak = True; return g
    if isinstance(x, float): g = _new((), float64); _set_scalar(g, x); g._weak = True; return g
    if isinstance(x, complex): return array([x]).reshape(())
    if isinstance(x, (list, tuple)): return array(x)
    if hasattr(x, "tobytes") and hasattr(x, "mode"): return asarray(x)
    try:
        return _as(float(x))
    except (TypeError, ValueError):
        raise MurmurError(f"murmur cannot make a number grid from {type(x).__name__}")


def _set_scalar(g, v):
    ctypes.memmove(g._addr, struct.pack("<" + g.dtype.fmt, v), g.dtype.size)


def _check_fit(d):
    bad = _LIB.pn_bad()
    if bad:
        lo, hi = ("0", "255") if d is uint8 else ("-32768", "32767") if d is int16 else ("-2**63", "2**63")
        raise MurmurError(f"{bad} values do not fit this {d.name} grid (whole numbers {lo}..{hi}): clip or round them first; "
                          "murmur never wraps (numpy would store 256 as 0)")


# --------------------------------------------------------------------------------------------------- broadcasting, maths

def _bshape(sa, sb):
    """The shape two grids make together. Refuses the silent outer grid: when one grid has fewer axes, only IT may be stretched."""
    nd = _b.max(len(sa), len(sb))
    pa, pb = (1,) * (nd - len(sa)) + tuple(sa), (1,) * (nd - len(sb)) + tuple(sb)
    out = []
    for i, (x, y) in enumerate(zip(pa, pb)):
        if x != y and x != 1 and y != 1:
            raise MurmurError(f"shapes {tuple(sa)} and {tuple(sb)} do not fit together: axis {i} is {x} against {y}")
        out.append(_b.max(x, y))
    if len(sa) != len(sb) and len(sa) and len(sb):
        big, bp = (sa, pa) if len(sa) > len(sb) else (sb, pb)
        if _b.any(b == 1 and o > 1 for b, o in zip(bp, out)):
            raise MurmurError(f"shapes {tuple(sa)} and {tuple(sb)} would make a {tuple(out)} grid by stretching the bigger one "
                              "(numpy does this silently, a common bug); if you mean that, give both the same number of axes "
                              "(e.g. row.reshape(1, -1)) or use murmur.outer")
    return tuple(out)


def _stretch(g, shape):
    """A view of g stretched to shape (stride 0 on stretched axes)."""
    pad = len(shape) - g.ndim
    if pad < 0: raise MurmurError(f"a {g.shape} grid cannot be squeezed into {shape}")
    gs, gst = (1,) * pad + g.shape, (0,) * pad + g.strides
    st = []
    for n, s, o in zip(gs, gst, shape):
        if n == o: st.append(s)
        elif n == 1: st.append(0)
        else: raise MurmurError(f"a {g.shape} grid does not fit {shape}")
    return Grid(g._owner, g._addr, shape, st, g.dtype, g._ro)


_FLOAT_RESULT = {3, 4, 18}


def _result(op, a, b):
    if op in (7, 8, 9, 10, 11, 12, 13, 14, 15): return bool_      # comparisons and & | ^ give yes/no
    dts = [g.dtype for g in (a, b) if not g._weak] or [a.dtype, b.dtype]
    weak = [g.dtype for g in (a, b) if g._weak]
    floats = [d for d in dts if d.kind == "f"]
    if floats or _b.any(d.kind == "f" for d in weak) or op in _FLOAT_RESULT:
        if float64 in floats: return float64
        if float32 in floats: return float64 if int64 in dts else float32
        if op in _FLOAT_RESULT and not floats and _b.all(d in (uint8, bool_) for d in dts) and not _b.any(d.kind == "f" for d in weak): return float32
        return float64
    if op in (5, 6) and len(set(dts)) == 1: return dts[0]          # max/min of one kind cannot overflow
    return int64                                                   # never wraps: small whole numbers widen


def _merged(*views):
    """The same views with neighbouring axes joined wherever every view's memory runs straight on: one long run for the C instead
    of many short ones (a picture's 3 colours would otherwise be 230,400 runs of 3)."""
    shape = list(views[0].shape); sts = [list(v.strides) for v in views]
    d = len(shape) - 2
    while d >= 0:
        if _b.all(st[d] == st[d + 1] * shape[d + 1] for st in sts):
            shape[d] *= shape[d + 1]; del shape[d + 1]
            for st in sts: del st[d]                      # the joined axis steps like the INNER one
        d -= 1
    return [Grid(v._owner, v._addr, shape, st, v.dtype, v._ro) for v, st in zip(views, sts)]


def _binary(op, a, b):
    a, b = _as(a), _as(b)
    shape = _bshape(a.shape, b.shape)
    rd = _result(op, a, b)
    out = _new(shape, rd)
    o, x, y = _merged(out, _stretch(a, shape), _stretch(b, shape))
    _LIB.pn_binary(op, ctypes.byref(o._c()), ctypes.byref(x._c()), ctypes.byref(y._c()))
    _check_fit(rd)
    return out._scalar() if not shape else out          # single numbers in, a plain number out


def _logic(op, a, b):
    a, b = _as(a), _as(b)
    if a.dtype is not bool_ or b.dtype is not bool_:
        raise MurmurError("& | ^ are for yes/no grids; compare first, and mind Python's order: (a > 1) & (b < 2), with the brackets")
    return _binary(op, a, b)


def _unary_raw(g, op, rd, clip=False, lo=0.0, hi=0.0):
    out = _new(g.shape, rd)
    o, x = _merged(out, g)
    _LIB.pn_unary(op, ctypes.byref(o._c()), ctypes.byref(x._c()), 1 if clip else 0, float(lo), float(hi))
    _check_fit(rd)
    return out._scalar() if not g.shape else out


def _float_of(d): return d if d.kind == "f" else (float32 if d in (uint8, bool_, int16) else float64)


_READ_ONLY = {   # why a grid cannot be written, in plain words
    "copy": "this grid is a read-only COPY (made by a mask or an index list): writing to it would change nothing; write through "
            "the original: grid[mask] = value",
    "shared": "this grid is shared and read-only (a window kept for every caller): copy it first (g.copy()) to change it",
    "overlap": "this view is read-only: its numbers overlap (as_strided frames), so one write would change several places; "
               "copy it first (g.copy())",
    "file": "this grid is a file seen in memory (memmap), read-only: the file itself is never changed; copy it first (g.copy())"}


def _assign(view, value):
    if view._ro: raise MurmurError(_READ_ONLY.get(view._ro, _READ_ONLY["copy"]))
    v = _as(value)
    if v.shape != view.shape:
        if v.ndim > view.ndim: raise MurmurError(f"a {v.shape} grid cannot be put into a {view.shape} place")
        v = _stretch(v, view.shape)
    o, x = _merged(view, v)
    _LIB.pn_unary(0, ctypes.byref(o._c()), ctypes.byref(x._c()), 0, 0.0, 0.0)
    _check_fit(view.dtype)


def _pairs(g):
    """A complex grid seen as float64 pairs (..., 2): the same memory, a view."""
    return Grid(g._owner, g._addr, g.shape + (2,), g.strides + (8,), float64, g._ro)


def _cpart(g, mode):
    g = g if g._contiguous() else g.copy()
    out = _new((g.size,), float64)
    _LIB.pn_cpart(ctypes.byref(out._c()), ctypes.cast(g._addr, ctypes.POINTER(ctypes.c_double)), g.size, mode)
    return out.reshape(g.shape)


def abs(x):
    g = _as(x)
    return _cpart(g, 0) if g.dtype is complex128 else _unary_raw(g, 2, g.dtype)


def angle(x):
    g = _as(x)
    return _cpart(g, 2) if g.dtype is complex128 else where(g < 0, math.pi, 0.0)


def conj(x):
    g = _as(x)
    if g.dtype is not complex128: return g
    out = g.copy(); im = _pairs(out)[..., 1]; _assign(im, -im)
    return out


def real(x): return _as(x).real
def imag(x): return _as(x).imag


_WINDOWS = {}


def _window(kind, M, a0, a1):
    """a0 - a1 cos(2 pi k / (M - 1)), made once per length and kept READ-ONLY (shared: nobody can change it under another)."""
    key = (kind, int(M))
    if key not in _WINDOWS:
        if M < 1: w = _new((0,), float64)
        elif M == 1: w = array([1.0])
        else: w = _binary(1, a0, _binary(2, a1, cos(_ramp(int(M), 0.0, 2 * math.pi / (M - 1), float64))))
        w._ro = "shared"
        _WINDOWS[key] = w
    return _WINDOWS[key]


def hanning(M): return _window("hann", M, 0.5, 0.5)
def hamming(M): return _window("hamming", M, 0.54, 0.46)


def pad(x, width, value=0):
    """A row with value added before and after: pad(x, (before, after)) (or pad(x, n): n each side). One row in murmur."""
    g = _as(x)
    if g.ndim != 1: raise MurmurError("pad: one row at a time in murmur")
    before, after = (width, width) if isinstance(width, int) else (int(width[0]), int(width[1]))
    out = full((before + g.shape[0] + after,), value, g.dtype)
    if g.shape[0]: _assign(out[before:before + g.shape[0]], g)
    return out


class fft:
    """The FFT, any length (radix-2 for powers of two, Bluestein for the rest), in doubles, rows over every core."""
    @staticmethod
    def rfft(x, n=None, axis=-1):
        g = _as(x)
        if g.dtype is complex128: raise MurmurError("rfft takes real numbers; this grid is complex")
        if axis not in (-1, g.ndim - 1): raise MurmurError("rfft: along the last axis in murmur (transpose first)")
        n = g.shape[-1] if n is None else int(n)          # 0 is a length (refused), not "not given"
        if n < 1: raise MurmurError(f"rfft needs a length of 1 or more, not {n}")
        out = _new(g.shape[:-1] + (n // 2 + 1,), complex128)
        _LIB.pn_rfft(ctypes.byref(out._craw()), ctypes.byref(g._c()), n)
        return out

    @staticmethod
    def irfft(X, n=None, axis=-1):
        g = _as(X)
        if axis not in (-1, g.ndim - 1): raise MurmurError("irfft: along the last axis in murmur")
        k = g.shape[-1]
        n = 2 * (k - 1) if n is None else int(n)
        if n < 1: raise MurmurError(f"irfft needs a length of 1 or more, not {n}")
        out = _new(g.shape[:-1] + (n,), float64)
        c = g._craw() if g.dtype is complex128 else g._c()
        _LIB.pn_irfft(ctypes.byref(out._c()), ctypes.byref(c), 1 if g.dtype is complex128 else 0, k, n)
        return out

    @staticmethod
    def rfftfreq(n, d=1.0):
        return _ramp(n // 2 + 1, 0.0, 1.0 / (n * d), float64)

    @staticmethod
    def power(x, n=None):
        """|rfft(x)|^2 in one pass (what Perch's sound bands and voice prints use)."""
        return _cpart(fft.rfft(x, n), 1)
def sqrt(x): g = _as(x); return _unary_raw(g, 3, _float_of(g.dtype))
def log(x): g = _as(x); return _unary_raw(g, 4, _float_of(g.dtype))
def log10(x): g = _as(x); return _unary_raw(g, 5, _float_of(g.dtype))
def log2(x): g = _as(x); return _unary_raw(g, 17, _float_of(g.dtype))
def exp(x): g = _as(x); return _unary_raw(g, 6, _float_of(g.dtype))
def sin(x): g = _as(x); return _unary_raw(g, 7, _float_of(g.dtype))
def cos(x): g = _as(x); return _unary_raw(g, 8, _float_of(g.dtype))
def tan(x): g = _as(x); return _unary_raw(g, 9, _float_of(g.dtype))
def arctan(x): g = _as(x); return _unary_raw(g, 16, _float_of(g.dtype))
def floor(x): g = _as(x); return _unary_raw(g, 10, g.dtype)
def ceil(x): g = _as(x); return _unary_raw(g, 11, g.dtype)
def trunc(x): g = _as(x); return _unary_raw(g, 18, g.dtype)
def rint(x): g = _as(x); return _unary_raw(g, 12, g.dtype)
def isnan(x): return _unary_raw(_as(x), 14, bool_)
def sign(x): g = _as(x); return _unary_raw(g, 15, g.dtype if g.dtype is not uint8 else int64)
def logical_not(x): return _unary_raw(_as(x), 13, bool_)
def maximum(a, b): return _binary(5, a, b)
def minimum(a, b): return _binary(6, a, b)
def arctan2(y, x): return _binary(18, y, x)


def clip(x, lo, hi):
    g = _as(x)
    rd = g.dtype if (isinstance(lo, int) and isinstance(hi, int)) or g.dtype.kind == "f" else float64
    return _unary_raw(g, 0, rd, True, lo, hi)


def where(cond, x, y):
    c, a, b = _as(cond), _as(x), _as(y)
    shape = _bshape(_bshape(c.shape, a.shape), b.shape)
    rd = _result(0, a, b) if a.dtype is not b.dtype else a.dtype
    out = _new(shape, rd)
    _LIB.pn_where(ctypes.byref(out._c()), ctypes.byref(_stretch(c, shape)._c()), ctypes.byref(_stretch(a, shape)._c()),
                  ctypes.byref(_stretch(b, shape)._c()))
    _check_fit(rd)
    return out


def matmul(a, b):
    a, b = _as(a), _as(b)
    if b.ndim == 1 and a.ndim >= 1:
        if a.shape[-1] != b.shape[0]: raise MurmurError(f"@: the last axis of {a.shape} must match {b.shape}")
        if a.ndim == 1: return _reduce(_binary(2, a, b), "sum", None, False)
        rd = _result(2, a, b)
        out = _new(a.shape[:-1], rd)
        w = (ctypes.c_double * b.shape[0])(*[float(v) for v in b.tolist()])
        _LIB.pn_vecdot(ctypes.byref(out._c()), ctypes.byref(a._c()), w)
        _check_fit(rd)
        return out
    if a.ndim == 1 and b.ndim == 2:
        if a.shape[0] != b.shape[0]: raise MurmurError(f"@: {a.shape} against {b.shape}")
        return matmul(a.reshape(1, -1), b).reshape(-1)
    if a.ndim == 2 and b.ndim == 2:
        if a.shape[1] != b.shape[0]: raise MurmurError(f"@: {a.shape} against {b.shape}: inner lengths differ")
        if a.dtype is complex128 or b.dtype is complex128: raise MurmurError("@ of complex grids is not in murmur")
        out = _new((a.shape[0], b.shape[1]), _result(2, a, b))
        _LIB.pn_matmul(ctypes.byref(out._c()), ctypes.byref(a._c()), ctypes.byref(b._c()))
        _check_fit(out.dtype)
        return out
    raise MurmurError(f"@ of {a.shape} and {b.shape} is not one murmur knows yet")


dot = matmul


def outer(a, b):
    a, b = _as(a).reshape(-1), _as(b).reshape(-1)
    return _binary(2, a.reshape(-1, 1), b.reshape(1, -1))


# ---------------------------------------------------------------------------------------------------------- along an axis

_RCODE = {"sum": 0, "prod": 1, "max": 2, "min": 3, "argmax": 4, "argmin": 5, "any": 6, "all": 7}


def _axis_last(g, axis):
    axes = list(range(g.ndim)); axes.append(axes.pop(axis))
    return g.transpose(axes)


def _reduce(g, how, axis, keepdims):
    g = _as(g)
    if axis is None:
        if g.size == 0 and how not in ("sum", "any", "all", "prod"):
            raise MurmurError(f"the {how} of nothing: the grid is empty")
        flat = g.reshape(-1)
        r = _reduce(flat, how, 0, False)
        if keepdims and how not in ("argmax", "argmin"): return full((1,) * g.ndim, r, _rdtype(g.dtype, how))
        return r
    if isinstance(axis, (tuple, list)):
        out = g
        for ax in sorted((a % g.ndim for a in axis), reverse=True): out = _reduce(out, how, ax, keepdims)
        return out
    ax = axis % g.ndim if g.ndim else 0
    if g.shape[ax] == 0 and how not in ("sum", "any", "all", "prod"):
        raise MurmurError(f"the {how} of nothing: the grid is empty along axis {ax}")
    if how in ("sum", "max", "min", "any", "all") and ax < g.ndim - 1 and g.shape[ax] > 1:
        # not the last axis: add row onto row with the memory side by side (seen as (pre, m, post)), not column by column
        pre, post = math.prod(g.shape[:ax]), math.prod(g.shape[ax + 1:])
        src = g if g._contiguous() else g.copy()
        v3 = src.reshape(pre, g.shape[ax], post)
        rd = _rdtype(g.dtype, how)
        out = _new((pre, post), rd if rd.kind == "f" or how in ("any", "all", "max", "min") else float64)
        _LIB.pn_reduce_mid({"sum": 0, "max": 2, "min": 3, "any": 6, "all": 7}[how], ctypes.byref(out._c()), ctypes.byref(v3._c()))
        _LIB.pn_bad()
        if out.dtype is not rd: out = out.astype(rd)
        out = out.reshape(g.shape[:ax] + ((1,) if keepdims else ()) + g.shape[ax + 1:])
        return out._scalar() if not out.ndim else out
    v = _axis_last(g, ax)
    if how == "mean":
        s = _reduce(g, "sum", ax, keepdims)
        n = g.shape[ax]
        if isinstance(s, Grid): return _binary(3, s.astype(_float_of(g.dtype)) if s.dtype.kind != "f" else s, float(n))
        return s / n
    rd = _rdtype(g.dtype, how)
    if v.ndim == 1 and rd.kind == "f": rd = float64          # one answer: kept at full precision (float32 is only for grids)
    out = _new(v.shape[:-1], rd if how not in ("sum",) or rd.kind == "f" else float64)
    _LIB.pn_reduce(_RCODE[how], ctypes.byref(out._c()), ctypes.byref(v._c()))
    _LIB.pn_bad()
    if out.dtype is not rd: out = out.astype(rd)
    if keepdims: out = out.reshape(g.shape[:ax] + (1,) + g.shape[ax + 1:])
    return out._scalar() if not out.ndim else out


def _rdtype(d, how):
    if how in ("argmax", "argmin"): return int64
    if how in ("any", "all"): return bool_
    if how in ("sum", "prod"): return d if d.kind == "f" else int64
    if how == "mean": return _float_of(d)
    return d


def sum(g, axis=None, keepdims=False): return _reduce(g, "sum", axis, keepdims)
def prod(g, axis=None, keepdims=False): return _reduce(g, "prod", axis, keepdims)
def mean(g, axis=None, keepdims=False): return _reduce(g, "mean", axis, keepdims)
def max(g, axis=None, keepdims=False): return _reduce(g, "max", axis, keepdims)
def min(g, axis=None, keepdims=False): return _reduce(g, "min", axis, keepdims)
def argmax(g, axis=None): return _reduce(g, "argmax", axis, False)
def argmin(g, axis=None): return _reduce(g, "argmin", axis, False)
def any(g, axis=None, keepdims=False): return _reduce(g, "any", axis, keepdims)
def all(g, axis=None, keepdims=False): return _reduce(g, "all", axis, keepdims)
amax, amin = max, min


def var(g, axis=None, keepdims=False):
    g = _as(g)
    m = mean(g, axis, keepdims=True) if axis is not None else mean(g)
    d = _binary(1, g, m)
    return mean(_binary(2, d, d), axis, keepdims)


def std(g, axis=None, keepdims=False):
    v = var(g, axis, keepdims)
    return math.sqrt(v) if not isinstance(v, Grid) else sqrt(v)


def percentile(g, q, axis=None):
    if isinstance(q, (list, tuple, Grid)):
        qs = q.tolist() if isinstance(q, Grid) else list(q)
        parts = [percentile(g, v, axis) for v in qs]
        return array(parts) if not isinstance(parts[0], Grid) else stack(parts)
    g = _as(g)
    if axis is None:
        g = g.reshape(-1); ax = 0
    else:
        ax = axis % g.ndim
    if g.shape[ax] == 0: raise MurmurError("a percentile of nothing: the grid is empty")
    v = _axis_last(g, ax)
    out = _new(v.shape[:-1], _float_of(g.dtype))
    _LIB.pn_percentile(ctypes.byref(out._c()), ctypes.byref(v._c()), float(q))
    return out._scalar() if not out.ndim else out


def median(g, axis=None): return percentile(g, 50.0, axis)


def average(g, axis=None, weights=None):
    if weights is None: return mean(g, axis)
    g, w = _as(g), _as(weights)
    if axis is None:
        sw = sum(w)
        if sw == 0: raise MurmurError("average: the weights add up to nothing")
        return sum(_binary(2, g, w)) / sw
    raise MurmurError("average with weights along an axis is not in murmur yet")


def nonzero(g):
    g = _as(g)
    n = _LIB.pn_count(ctypes.byref(g._c()))
    buf = (ctypes.c_int64 * _b.max(1, n))()
    _LIB.pn_nonzero(ctypes.byref(g._c()), buf)
    flat = list(buf[:n])
    out = []
    for ax in range(g.ndim):
        inner = math.prod(g.shape[ax + 1:])
        out.append(array([(f // inner) % g.shape[ax] for f in flat], int64) if flat else _new((0,), int64))
    return tuple(out)


def count_nonzero(g): return _LIB.pn_count(ctypes.byref(_as(g)._c()))


def diff(g, axis=-1):
    g = _as(g)
    ax = axis % g.ndim
    hi = [slice(None)] * g.ndim; lo = [slice(None)] * g.ndim
    hi[ax] = slice(1, None); lo[ax] = slice(None, -1)
    return _binary(1, g._basic(tuple(hi)), g._basic(tuple(lo)))


def concatenate(gs, axis=0):
    gs = [_as(g) for g in gs]
    ax = axis % gs[0].ndim
    shape = list(gs[0].shape); shape[ax] = _b.sum(g.shape[ax] for g in gs)
    out = _new(shape, gs[0].dtype)
    at = 0
    for g in gs:
        key = [slice(None)] * len(shape); key[ax] = slice(at, at + g.shape[ax])
        _assign(out._basic(tuple(key)), g)
        at += g.shape[ax]
    return out


def stack(gs, axis=0):
    gs = [_as(g) for g in gs]
    return concatenate([g._basic(tuple([slice(None)] * axis + [None])) for g in gs], axis)


def _sort(g, axis, args):
    g = _as(g)
    if g.dtype is complex128: raise MurmurError("sorting complex numbers: sort abs(x) or x.real")
    if not g.ndim: return g
    ax = axis % g.ndim
    v = _axis_last(g, ax)
    out = _new(v.shape, int64 if args else g.dtype)
    _LIB.pn_sort(ctypes.byref(out._c()), ctypes.byref(v._c()), 1 if args else 0)
    _LIB.pn_bad()
    if ax != g.ndim - 1:                                  # back to the asked axis
        axes = list(range(g.ndim - 1)); axes.insert(ax, g.ndim - 1)
        out = out.transpose(axes).copy()
    return out


def sort(g, axis=-1):
    """Along an axis (the last by default): NaN last, ties keep their order."""
    return _sort(g, axis, False)


def argsort(g, axis=-1):
    """The order that sorts it (stable: ties keep their order)."""
    return _sort(g, axis, True)


def label(mask):
    """Joined patches of a 2-D yes/no grid (4 neighbours): (labels grid int64, how many). Numbered in reading order."""
    m = _as(mask)
    if m.ndim != 2: raise MurmurError("label: a 2-D yes/no grid")
    h, w = m.shape
    out = _new((h, w), int64)
    stack_ = (ctypes.c_int64 * _b.max(1, h * w))()
    n = _LIB.pn_label(ctypes.byref(m._c()), ctypes.cast(out._addr, ctypes.POINTER(ctypes.c_int64)), stack_)
    return out, n


def patches(mask):
    """Joined patches as lists of (y, x), in the order their first pixel comes reading row by row."""
    lab, n = label(mask)
    w = lab.shape[1]
    out = [[] for _ in range(n)]
    raw = ctypes.cast(lab._addr, ctypes.POINTER(ctypes.c_int64))
    flat = raw[:lab.size]
    for i, v in enumerate(flat):
        if v: out[v - 1].append((i // w, i % w))
    return out


class linalg:
    @staticmethod
    def eigh(m):
        """Values (ascending) and vectors (columns) of a small symmetric grid (Jacobi rotations, in doubles)."""
        g = _as(m)
        if g.ndim != 2 or g.shape[0] != g.shape[1]: raise MurmurError(f"eigh wants a square grid, not {g.shape}")
        n = g.shape[0]
        a = (ctypes.c_double * (n * n))(*[float(v) for row in g.tolist() for v in row])
        w = (ctypes.c_double * n)(); v = (ctypes.c_double * (n * n))()
        _LIB.pn_eigh(n, a, w, v)
        d = _float_of(g.dtype)
        return array(list(w), float64).astype(d), array([list(v[i * n:(i + 1) * n]) for i in range(n)], float64).astype(d)

    @staticmethod
    def norm(x, axis=None):
        g = _as(x)
        s = sum(_binary(2, g, g), axis)
        return math.sqrt(s) if not isinstance(s, Grid) else sqrt(s)



# ======== stage 3: voices and cuts ========

def _dbl(g):
    """A grid's numbers as a contiguous ctypes double array (a copy)."""
    g = _as(g)
    flat = g.astype(float64).reshape(-1) if g.dtype is not float64 else (g if g._contiguous() else g.copy()).reshape(-1)
    return ctypes.cast(flat._addr, ctypes.POINTER(ctypes.c_double)), flat


def correlate(a, v, mode="valid"):
    """numpy's correlate of two rows: 'full' (every overlap), 'same' (a's length) or 'valid' (full overlaps only)."""
    a, v = _as(a).reshape(-1), _as(v).reshape(-1)
    na, nv = a.shape[0], v.shape[0]
    if not na or not nv: raise MurmurError("correlate of an empty row")
    pa, ka = _dbl(a); pv, kv = _dbl(v)
    full = _new((na + nv - 1,), float64)
    _LIB.pn_correlate(ctypes.cast(full._addr, ctypes.POINTER(ctypes.c_double)), pa, na, pv, nv)
    if mode == "full": return full
    if mode == "same":
        n = _b.max(na, nv); st = (na + nv - 1 - n) // 2
        return full[st:st + n].copy()
    if mode == "valid":
        n = _b.max(na, nv) - _b.min(na, nv) + 1; st = _b.min(na, nv) - 1
        return full[st:st + n].copy()
    raise MurmurError(f"correlate: mode is 'full', 'same' or 'valid', not {mode!r}")


def roots(p):
    """The roots of a polynomial (coefficients highest first), as a complex grid; trailing zeros give roots at 0 (as numpy)."""
    c = [float(v) for v in _as(p).reshape(-1).tolist()]
    if len(c) > 257: raise MurmurError("roots: murmur takes polynomials up to degree 256")
    arr = (ctypes.c_double * _b.max(1, len(c)))(*c)
    re, im = (ctypes.c_double * 256)(), (ctypes.c_double * 256)()
    n = _LIB.pn_roots(arr, len(c), re, im)
    return array([complex(re[i], im[i]) for i in range(n)]) if n else _new((0,), complex128)


class _Lin:
    pass


def _lstsq(A, b):
    A, b = _as(A), _as(b).reshape(-1)
    if A.ndim != 2 or A.shape[0] != b.shape[0]: raise MurmurError(f"lstsq: A {A.shape} and b {b.shape} do not fit")
    m, n = A.shape
    if m < n: raise MurmurError(f"lstsq: {m} equations for {n} unknowns: murmur needs at least as many equations")
    pa, ka = _dbl(A.copy()); pb, kb = _dbl(b.copy())
    x = _new((n,), float64)
    _LIB.pn_lstsq(pa, pb, m, n, ctypes.cast(x._addr, ctypes.POINTER(ctypes.c_double)))
    return x


linalg.lstsq = staticmethod(_lstsq)


def polyfit(x, y, deg):
    """The least-squares polynomial through (x, y), coefficients highest first (as numpy)."""
    x, y = _as(x).reshape(-1).astype(float64), _as(y).reshape(-1)
    cols = [x ** (deg - j) if deg - j else full(x.shape, 1.0) for j in range(deg + 1)]
    return _lstsq(stack(cols, axis=1), y)


def cov(m, rowvar=True):
    """Covariance: rows are the variables (as numpy), divided by n - 1."""
    X = _as(m).astype(float64)
    if X.ndim == 1: X = X.reshape(1, -1)
    if not rowvar: X = X.T
    n = X.shape[1]
    if n < 2: raise MurmurError("cov needs at least two observations")
    Xc = X - X.mean(1, keepdims=True)
    C = matmul(Xc, Xc.T) / (n - 1)
    return C if C.shape != (1, 1) else C[0, 0]


def eye(n, dtype=float64):
    out = zeros((n, n), dtype)
    if n: _assign(Grid(out._owner, out._addr, (n,), ((n + 1) * out.dtype.size,), out.dtype), 1)
    return out


def diag(v):
    g = _as(v)
    if g.ndim == 1:
        n = g.shape[0]; out = zeros((n, n), g.dtype)
        if n: _assign(Grid(out._owner, out._addr, (n,), ((n + 1) * out.dtype.size,), out.dtype), g)
        return out
    if g.ndim == 2:
        n = _b.min(g.shape)
        return Grid(g._owner, g._addr, (n,), (g.strides[0] + g.strides[1],), g.dtype, g._ro).copy()
    raise MurmurError("diag: a row (to make a square) or a square (to take its diagonal)")


def trace(m): return sum(diag(m))


def cumsum(g, axis=None):
    """Running totals along an axis (no axis: the whole grid flattened, as numpy). Whole numbers give int64 totals; the sums are
    done in doubles with Kahan's correction, so a long float32 run does not drift."""
    g = _as(g)
    if g.dtype is complex128: raise MurmurError("cumsum of complex numbers: take .real or abs first")
    if axis is None: g, ax = g.reshape(-1), 0
    else: ax = axis % g.ndim if g.ndim else 0
    if not g.ndim: g, ax = g.reshape(1), 0
    rd = g.dtype if g.dtype.kind == "f" else int64
    v = _axis_last(g, ax)
    out = _new(v.shape, rd)
    if v.size: _LIB.pn_cumsum(ctypes.byref(out._c()), ctypes.byref(v._c())); _check_fit(rd)
    if ax != g.ndim - 1:
        axes = list(range(g.ndim - 1)); axes.insert(ax, g.ndim - 1)
        out = out.transpose(axes).copy()
    return out


def bincount(x, minlength=0):
    vals = [int(v) for v in _as(x).reshape(-1).tolist()]
    if _b.any(v < 0 for v in vals): raise MurmurError("bincount counts whole numbers 0 and up")
    n = _b.max([minlength] + [v + 1 for v in vals])
    counts = [0] * n
    for v in vals: counts[v] += 1
    return array(counts, int64)


def allclose(a, b, rtol=1e-05, atol=1e-08):
    a, b = _as(a), _as(b)
    return bool(all(abs(a - b) <= atol + rtol * abs(b)))


def subtract(a, b): return _binary(1, a, b)


def append(a, b):
    return concatenate([_as(a).reshape(-1), _as(b).reshape(-1)])


def as_strided(x, shape, strides):
    """A view of x with the shape and byte steps given (frames of a sound overlapping, as numpy's as_strided). READ-ONLY (its
    numbers overlap) and refused if it would reach outside x's own memory."""
    g = _as(x)
    if not g._contiguous(): g = g.copy()
    span = _b.sum((n - 1) * st for n, st in zip(shape, strides) if n > 0) + g.dtype.size if _b.all(n > 0 for n in shape) else 0
    if _b.any(st < 0 for st in strides) or span > g.nbytes:
        raise MurmurError(f"as_strided: shape {tuple(shape)} with steps {tuple(strides)} would reach outside the grid's own memory")
    return Grid(g._owner, g._addr, shape, strides, g.dtype, "overlap")


def shift_match(a, b, r=3):
    """The smallest mean difference between two 2-D grids over small shifts (-r..r each way): a pan is not a change."""
    a, b = _as(a), _as(b)
    if a.ndim != 2 or a.shape != b.shape: raise MurmurError(f"shift_match: two 2-D grids of one shape, not {a.shape} and {b.shape}")
    return _LIB.pn_shift_match(ctypes.byref(a._c()), ctypes.byref(b._c()), int(r))


def memmap(path, dtype=uint8, mode="r"):
    """A file's numbers without reading it all in (the system pages it in as it is read). Read-only."""
    import mmap
    if mode != "r": raise MurmurError("murmur's memmap reads only (mode='r')")
    d = _dt(dtype)
    f = open(path, "rb")
    size = os.fstat(f.fileno()).st_size
    if size % d.size: raise MurmurError(f"{path}: {size} bytes are not whole {d.name} values")
    if size == 0: f.close(); return _new((0,), d)
    mm_ = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_COPY)      # copy-on-write: the file itself can never be changed
    f.close()
    buf = (ctypes.c_char * size).from_buffer(mm_)
    return Grid((mm_, buf), ctypes.addressof(buf), (size // d.size,), (d.size,), d, "file")


_NPY = {"<f4": float32, "<f8": float64, "|u1": uint8, "<i8": int64, "<i2": int16, "|b1": bool_, "<c16": complex128}


def load(path):
    """A .npy file (numpy's own format, versions 1-3), without numpy."""
    import ast
    with open(path, "rb") as f:
        if f.read(6) != b"\x93NUMPY": raise MurmurError(f"{path} is not a .npy file")
        major = f.read(1)[0]; f.read(1)
        hl = struct.unpack("<H", f.read(2))[0] if major == 1 else struct.unpack("<I", f.read(4))[0]
        head = ast.literal_eval(f.read(hl).decode("latin1"))
        if head.get("fortran_order"): raise MurmurError(f"{path}: column-first (Fortran) order is not read by murmur")
        d = _NPY.get(head["descr"])
        if d is None: raise MurmurError(f"{path}: numbers of kind {head['descr']} are not one murmur keeps")
        shape = tuple(head["shape"])
        data = f.read(math.prod(shape) * d.size)
    g = frombuffer(data, d) if d is not complex128 else _from_complex_bytes(data)
    return g.reshape(shape) if shape else g.reshape(())


def _from_complex_bytes(data):
    out = _new((len(data) // 16,), complex128)
    ctypes.memmove(out._addr, data, len(data))
    return out


class random:
    """Seeded random numbers: xoshiro256** seeded through splitmix64 (well-tested generators). NOT numpy's stream: the same seed
    gives different numbers than numpy would (results that rest on a random start may differ; murmur's own are repeatable)."""
    class default_rng:
        def __init__(self, seed=None):
            if seed is None: seed = int.from_bytes(os.urandom(8), "little")
            x = int(seed) & 0xFFFFFFFFFFFFFFFF; st = []
            for _ in range(4):
                x = (x + 0x9E3779B97F4A7C15) & 0xFFFFFFFFFFFFFFFF
                z = x; z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
                z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & 0xFFFFFFFFFFFFFFFF; st.append(z ^ (z >> 31))
            self.s = st

        def _next(self):
            s = self.s; M = 0xFFFFFFFFFFFFFFFF
            r = ((((s[1] * 5) & M) << 7 | ((s[1] * 5) & M) >> 57) & M) * 9 & M
            t = (s[1] << 17) & M
            s[2] ^= s[0]; s[3] ^= s[1]; s[1] ^= s[2]; s[0] ^= s[3]; s[2] ^= t
            s[3] = ((s[3] << 45) | (s[3] >> 19)) & M
            return r

        def _fill(self, n, kind):
            """n numbers from the C side of the same stream (kind 0 uniform, 1 normal); the state comes back, so Python and C
            draws carry on from each other."""
            st = (ctypes.c_uint64 * 4)(*self.s)
            out = _new((n,), float64)
            if n: _LIB.pn_rng_fill(st, ctypes.cast(out._addr, ctypes.POINTER(ctypes.c_double)), n, kind)
            self.s = list(st)
            return out

        def random(self, size=None):
            if size is None: return (self._next() >> 11) * (1.0 / 9007199254740992.0)
            n = size if isinstance(size, int) else math.prod(size)
            return self._fill(n, 0).reshape(size)

        def normal(self, loc=0.0, scale=1.0, size=None):
            """Normal (Gaussian) numbers: mean loc, spread scale (Marsaglia's polar method on this generator's stream)."""
            if not isinstance(loc, (int, float)) or not isinstance(scale, (int, float)):
                raise MurmurError("normal: loc and scale as plain numbers in murmur (not grids)")
            if scale < 0: raise MurmurError(f"normal: the spread (scale) cannot be negative, not {scale}")
            if size is None: return loc + scale * self._fill(1, 1)[0]
            n = size if isinstance(size, int) else math.prod(size)
            g = self._fill(n, 1)
            if scale != 1.0: g = g * float(scale)
            if loc != 0.0: g = g + float(loc)
            return g.reshape(size)

        standard_normal = lambda self, size=None: self.normal(0.0, 1.0, size)

        def integers(self, low, high=None, size=None):
            if high is None: low, high = 0, low
            span = high - low
            if span <= 0: raise MurmurError(f"integers: nothing between {low} and {high}")
            def one():                                   # no bias: draws that would favour small numbers are thrown back
                lim = (1 << 64) - ((1 << 64) % span)
                while True:
                    v = self._next()
                    if v < lim: return low + v % span
            if size is None: return one()
            n = size if isinstance(size, int) else math.prod(size)
            return array([one() for _ in range(n)], int64).reshape(size)

        def choice(self, a, size=None, replace=True, p=None):
            pool = list(range(a)) if isinstance(a, int) else list(_as(a).tolist())
            n = len(pool)
            if p is not None:
                w = [float(v) for v in (_as(p).tolist() if not isinstance(p, list) else p)]
                tot = _b.sum(w)
                if len(w) != n or tot <= 0 or _b.any(v < 0 for v in w): raise MurmurError("choice: p must be one weight (0 or more) per item, not all 0")
                cdf, acc = [], 0.0
                for v in w: acc += v / tot; cdf.append(acc)
                def pick():
                    u = self.random(); lo, hi = 0, n - 1
                    while lo < hi:
                        mid = (lo + hi) // 2
                        if cdf[mid] < u: lo = mid + 1
                        else: hi = mid
                    return pool[lo]
                if size is None: return pick()
                if not replace: raise MurmurError("choice with p and without replacement is not in murmur")
                return array([pick() for _ in range(size)])
            if size is None: return pool[self.integers(n)]
            if replace: return array([pool[self.integers(n)] for _ in range(size)])
            if size > n: raise MurmurError(f"choice: {size} different items from {n}")
            idx = list(range(n))                          # partial Fisher-Yates: the first size places
            for i in range(size):
                j = i + self.integers(n - i); idx[i], idx[j] = idx[j], idx[i]
            return array([pool[i] for i in idx[:size]])
