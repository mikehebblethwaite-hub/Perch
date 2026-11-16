// Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md.
// perch-murmur: Perch's own number grids (Mike, 16 Nov 2026: "write our own numpy in C for perch"). Named for a murmuration:
// thousands of starlings turning as one; every number a bird, the whole grid moving at once. A picture is a grid of
// numbers (height x width x colour layers), a sound a long row of them; this is the C that walks them. perch_num.py gives it the
// array face Perch's Python uses (views, slicing, broadcasting); here are only loops over strided memory:
//   every element is read into a double, worked, and written back in the grid's own type (uint8, bool, int64, float32, float64),
//   one run (the last axis) at a time, so the inner loops are plain and the compiler can vectorise them.
// Built as libperch-murmur.so (make). No other library but libm. A plain C ABI (ctypes, no Python C-API): no rebuild per Python.
// Never wraps: a value that does not fit a whole-number grid (out of range, not whole, NaN) is counted in *bad, not stored
// wrapped; perch_murmur refuses the result in plain words (numpy wraps 255 + 1 to 0 without a word).
#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

enum { U8 = 0, BOOL = 1, I64 = 2, F32 = 3, F64 = 4 };
#define MAXD 8

typedef struct {
    char *data;               // the first element of this view
    int dtype;
    int ndim;
    int64_t shape[MAXD];
    int64_t strides[MAXD];    // in bytes; 0 = broadcast along that axis
} pn_arr;

static void load(const pn_arr *a, const char *p, int64_t n, int64_t st, double *out) {
    // elements side by side (the usual case): plain loops the compiler turns into wide (SIMD) instructions
    if (a->dtype == F32 && st == 4) { const float *f = (const float *)p; for (int64_t i = 0; i < n; i++) out[i] = f[i]; return; }
    if (a->dtype == F64 && st == 8) { memcpy(out, p, n * sizeof(double)); return; }
    if ((a->dtype == U8 || a->dtype == BOOL) && st == 1) {
        const uint8_t *u = (const uint8_t *)p;
        if (a->dtype == U8) for (int64_t i = 0; i < n; i++) out[i] = u[i]; else for (int64_t i = 0; i < n; i++) out[i] = u[i] != 0;
        return;
    }
    switch (a->dtype) {
    case U8: for (int64_t i = 0; i < n; i++) out[i] = *(const uint8_t *)(p + i * st); break;
    case BOOL: for (int64_t i = 0; i < n; i++) out[i] = *(const uint8_t *)(p + i * st) ? 1.0 : 0.0; break;
    case I64: for (int64_t i = 0; i < n; i++) out[i] = (double)*(const int64_t *)(p + i * st); break;
    case F32: for (int64_t i = 0; i < n; i++) out[i] = *(const float *)(p + i * st); break;
    default: for (int64_t i = 0; i < n; i++) out[i] = *(const double *)(p + i * st); break;
    }
}

static int64_t BAD;                                    // values that would not fit (per call; perch_murmur reads it at once)
int64_t pn_bad(void) { int64_t b = BAD; BAD = 0; return b; }
static void store(const pn_arr *a, char *p, int64_t n, int64_t st, const double *in) {
    if (a->dtype == F32 && st == 4) { float *f = (float *)p; for (int64_t i = 0; i < n; i++) f[i] = (float)in[i]; return; }
    if (a->dtype == F64 && st == 8) { memcpy(p, in, n * sizeof(double)); return; }
    if (a->dtype == BOOL && st == 1) { uint8_t *u = (uint8_t *)p; for (int64_t i = 0; i < n; i++) u[i] = in[i] != 0.0; return; }
    switch (a->dtype) {
    case U8: for (int64_t i = 0; i < n; i++) {
            double v = in[i];
            if (!(v >= 0.0 && v <= 255.0) || v != floor(v)) { BAD++; v = v != v ? 0 : v < 0 ? 0 : v > 255 ? 255 : floor(v); }
            *(uint8_t *)(p + i * st) = (uint8_t)v; } break;
    case BOOL: for (int64_t i = 0; i < n; i++) *(uint8_t *)(p + i * st) = in[i] != 0.0; break;
    case I64: for (int64_t i = 0; i < n; i++) {
            double v = in[i];
            if (!(v >= -9.2233720368547748e18 && v < 9.2233720368547748e18) || v != floor(v)) { BAD++; v = v != v ? 0 : floor(v); }
            *(int64_t *)(p + i * st) = (int64_t)v; } break;
    case F32: for (int64_t i = 0; i < n; i++) *(float *)(p + i * st) = (float)in[i]; break;
    default: for (int64_t i = 0; i < n; i++) *(double *)(p + i * st) = in[i]; break;
    }
}

// walk every run (all axes but the last) of up to three views of the same shape; call f on each run
typedef void (*run_fn)(int64_t n, char **p, int64_t *st, void *ctx);
static void each_run(int k, pn_arr **v, run_fn f, void *ctx) {
    pn_arr *o = v[0];
    int nd = o->ndim;
    int64_t n = nd ? o->shape[nd - 1] : 1;
    int64_t st[4] = {0, 0, 0, 0};
    for (int j = 0; j < k; j++) st[j] = nd ? v[j]->strides[nd - 1] : 0;
    int64_t idx[MAXD] = {0};
    int64_t outer = 1;
    for (int d = 0; d < nd - 1; d++) outer *= o->shape[d];
    if (n == 0) return;
    for (int64_t r = 0; r < outer; r++) {
        char *p[4];
        for (int j = 0; j < k; j++) {
            p[j] = v[j]->data;
            for (int d = 0; d < nd - 1; d++) p[j] += idx[d] * v[j]->strides[d];
        }
        f(n, p, st, ctx);
        for (int d = nd - 2; d >= 0; d--) { if (++idx[d] < o->shape[d]) break; idx[d] = 0; }
    }
}

// ---- elementwise: out = a OP b ----
enum { ADD, SUB, MUL, DIV, POW, MAX2, MIN2, LT, LE, GT, GE, EQ, NE, AND, OR, XOR, FLOORDIV, MOD, ATAN2 };
typedef struct { int op; pn_arr *v[3]; double *x, *y; } bin_ctx;

static void bin_run(int64_t n, char **p, int64_t *st, void *c_) {
    bin_ctx *c = c_;
    double *x = c->x, *y = c->y;
    load(c->v[1], p[1], n, st[1], x);
    load(c->v[2], p[2], n, st[2], y);
    switch (c->op) {
    case ADD: for (int64_t i = 0; i < n; i++) x[i] += y[i]; break;
    case SUB: for (int64_t i = 0; i < n; i++) x[i] -= y[i]; break;
    case MUL: for (int64_t i = 0; i < n; i++) x[i] *= y[i]; break;
    case DIV: for (int64_t i = 0; i < n; i++) x[i] /= y[i]; break;
    case POW: for (int64_t i = 0; i < n; i++) x[i] = y[i] == 2.0 ? x[i] * x[i] : pow(x[i], y[i]); break;
    case MAX2: for (int64_t i = 0; i < n; i++) x[i] = (x[i] != x[i] || x[i] >= y[i]) ? x[i] : y[i]; break;
    case MIN2: for (int64_t i = 0; i < n; i++) x[i] = (x[i] != x[i] || x[i] <= y[i]) ? x[i] : y[i]; break;
    case LT: for (int64_t i = 0; i < n; i++) x[i] = x[i] < y[i]; break;
    case LE: for (int64_t i = 0; i < n; i++) x[i] = x[i] <= y[i]; break;
    case GT: for (int64_t i = 0; i < n; i++) x[i] = x[i] > y[i]; break;
    case GE: for (int64_t i = 0; i < n; i++) x[i] = x[i] >= y[i]; break;
    case EQ: for (int64_t i = 0; i < n; i++) x[i] = x[i] == y[i]; break;
    case NE: for (int64_t i = 0; i < n; i++) x[i] = x[i] != y[i]; break;
    case AND: for (int64_t i = 0; i < n; i++) x[i] = (x[i] != 0) && (y[i] != 0); break;
    case OR: for (int64_t i = 0; i < n; i++) x[i] = (x[i] != 0) || (y[i] != 0); break;
    case XOR: for (int64_t i = 0; i < n; i++) x[i] = (x[i] != 0) != (y[i] != 0); break;
    case FLOORDIV: for (int64_t i = 0; i < n; i++) x[i] = floor(x[i] / y[i]); break;
    case MOD: for (int64_t i = 0; i < n; i++) { double m = fmod(x[i], y[i]); x[i] = (m != 0 && ((m < 0) != (y[i] < 0))) ? m + y[i] : m; } break;
    case ATAN2: for (int64_t i = 0; i < n; i++) x[i] = atan2(x[i], y[i]); break;
    }
    store(c->v[0], p[0], n, st[0], x);
}

int pn_binary(int op, pn_arr *out, pn_arr *a, pn_arr *b) {
    int64_t n = out->ndim ? out->shape[out->ndim - 1] : 1;
    bin_ctx c = {op, {out, a, b}, malloc(sizeof(double) * (n ? n : 1)), malloc(sizeof(double) * (n ? n : 1))};
    if (!c.x || !c.y) { free(c.x); free(c.y); return -1; }
    each_run(3, c.v, bin_run, &c);
    free(c.x); free(c.y);
    return 0;
}

// ---- elementwise: out = f(a); also the copy that makes astype, contiguous copies and slice assignment ----
enum { COPY, NEG, ABS, SQRT, LOG, LOG10, EXP, SIN, COS, TAN, FLOOR, CEIL, ROUND, NOT, ISNAN, SIGN, ATAN, LOG2, TRUNC };
typedef struct { int op; double lo, hi; int clip; pn_arr *v[2]; double *x; } un_ctx;

static void un_run(int64_t n, char **p, int64_t *st, void *c_) {
    un_ctx *c = c_;
    double *x = c->x;
    load(c->v[1], p[1], n, st[1], x);
    switch (c->op) {
    case COPY: break;
    case NEG: for (int64_t i = 0; i < n; i++) x[i] = -x[i]; break;
    case ABS: for (int64_t i = 0; i < n; i++) x[i] = fabs(x[i]); break;
    case SQRT: for (int64_t i = 0; i < n; i++) x[i] = sqrt(x[i]); break;
    case LOG: for (int64_t i = 0; i < n; i++) x[i] = log(x[i]); break;
    case LOG10: for (int64_t i = 0; i < n; i++) x[i] = log10(x[i]); break;
    case LOG2: for (int64_t i = 0; i < n; i++) x[i] = log2(x[i]); break;
    case EXP: for (int64_t i = 0; i < n; i++) x[i] = exp(x[i]); break;
    case SIN: for (int64_t i = 0; i < n; i++) x[i] = sin(x[i]); break;
    case COS: for (int64_t i = 0; i < n; i++) x[i] = cos(x[i]); break;
    case TAN: for (int64_t i = 0; i < n; i++) x[i] = tan(x[i]); break;
    case ATAN: for (int64_t i = 0; i < n; i++) x[i] = atan(x[i]); break;
    case FLOOR: for (int64_t i = 0; i < n; i++) x[i] = floor(x[i]); break;
    case CEIL: for (int64_t i = 0; i < n; i++) x[i] = ceil(x[i]); break;
    case TRUNC: for (int64_t i = 0; i < n; i++) x[i] = trunc(x[i]); break;
    case ROUND: for (int64_t i = 0; i < n; i++) x[i] = nearbyint(x[i]); break;   // halves to even, as numpy
    case NOT: for (int64_t i = 0; i < n; i++) x[i] = x[i] == 0.0; break;
    case ISNAN: for (int64_t i = 0; i < n; i++) x[i] = x[i] != x[i]; break;
    case SIGN: for (int64_t i = 0; i < n; i++) x[i] = (x[i] > 0) - (x[i] < 0); break;
    }
    if (c->clip) for (int64_t i = 0; i < n; i++) x[i] = x[i] < c->lo ? c->lo : x[i] > c->hi ? c->hi : x[i];
    store(c->v[0], p[0], n, st[0], x);
}

int pn_unary(int op, pn_arr *out, pn_arr *a, int clip, double lo, double hi) {
    int64_t n = out->ndim ? out->shape[out->ndim - 1] : 1;
    un_ctx c = {op, lo, hi, clip, {out, a}, malloc(sizeof(double) * (n ? n : 1))};
    if (!c.x) return -1;
    each_run(2, c.v, un_run, &c);
    free(c.x);
    return 0;
}

// ---- reductions along the LAST axis (perch_num moves the asked axis there first): out has the shape without it ----
enum { SUM, PROD, RMAX, RMIN, ARGMAX, ARGMIN, ANY, ALL, SUMSQ };
int pn_reduce(int op, pn_arr *out, pn_arr *a) {
    int nd = a->ndim;
    int64_t n = nd ? a->shape[nd - 1] : 1, st = nd ? a->strides[nd - 1] : 0;
    int64_t outer = 1;
    for (int d = 0; d < nd - 1; d++) outer *= a->shape[d];
    double *x = malloc(sizeof(double) * (n ? n : 1));
    if (!x) return -1;
    int64_t idx[MAXD] = {0};
    for (int64_t r = 0; r < outer; r++) {
        const char *p = a->data; char *q = out->data;
        for (int d = 0; d < nd - 1; d++) { p += idx[d] * a->strides[d]; q += idx[d] * out->strides[d]; }
        load(a, p, n, st, x);
        double v = 0; int64_t k = 0;
        switch (op) {
        case SUM: { double s = 0, c = 0; for (int64_t i = 0; i < n; i++) { double y = x[i] - c, t = s + y; c = (t - s) - y; s = t; } v = s; break; }  // Kahan: long rows stay exact
        case SUMSQ: { double s = 0; for (int64_t i = 0; i < n; i++) s += x[i] * x[i]; v = s; break; }
        case PROD: v = 1; for (int64_t i = 0; i < n; i++) v *= x[i]; break;
        case RMAX: v = n ? x[0] : NAN; for (int64_t i = 1; i < n; i++) if (x[i] > v || x[i] != x[i]) { v = x[i]; if (v != v) break; } break;
        case RMIN: v = n ? x[0] : NAN; for (int64_t i = 1; i < n; i++) if (x[i] < v || x[i] != x[i]) { v = x[i]; if (v != v) break; } break;
        case ARGMAX: for (int64_t i = 1; i < n; i++) if (x[i] > x[k]) k = i; v = (double)k; break;
        case ARGMIN: for (int64_t i = 1; i < n; i++) if (x[i] < x[k]) k = i; v = (double)k; break;
        case ANY: v = 0; for (int64_t i = 0; i < n; i++) if (x[i] != 0) { v = 1; break; } break;
        case ALL: v = 1; for (int64_t i = 0; i < n; i++) if (x[i] == 0) { v = 0; break; } break;
        }
        store(out, q, 1, 0, &v);
        for (int d = nd - 2; d >= 0; d--) { if (++idx[d] < a->shape[d]) break; idx[d] = 0; }
    }
    free(x);
    return 0;
}

// ---- percentiles along the last axis (median = 50), numpy's "linear" way: the value at (n-1)*q/100 between neighbours ----
static double select_k(double *x, int64_t n, int64_t k) {   // quickselect: the k-th smallest, x reordered
    int64_t lo = 0, hi = n - 1;
    while (lo < hi) {
        double piv = x[lo + (hi - lo) / 2];
        int64_t i = lo, j = hi;
        while (i <= j) {
            while (x[i] < piv) i++;
            while (x[j] > piv) j--;
            if (i <= j) { double t = x[i]; x[i] = x[j]; x[j] = t; i++; j--; }
        }
        if (k <= j) hi = j; else if (k >= i) lo = i; else return x[k];
    }
    return x[k];
}

int pn_percentile(pn_arr *out, pn_arr *a, double q) {
    int nd = a->ndim;
    int64_t n = nd ? a->shape[nd - 1] : 1, st = nd ? a->strides[nd - 1] : 0;
    int64_t outer = 1;
    for (int d = 0; d < nd - 1; d++) outer *= a->shape[d];
    double *x = malloc(sizeof(double) * (n ? n : 1));
    if (!x) return -1;
    int64_t idx[MAXD] = {0};
    for (int64_t r = 0; r < outer; r++) {
        const char *p = a->data; char *o = out->data;
        for (int d = 0; d < nd - 1; d++) { p += idx[d] * a->strides[d]; o += idx[d] * out->strides[d]; }
        load(a, p, n, st, x);
        double v = NAN;
        int nan = 0;
        for (int64_t i = 0; i < n; i++) if (x[i] != x[i]) { nan = 1; break; }
        if (n && !nan) {
            double pos = (n - 1) * q / 100.0;
            int64_t lo = (int64_t)floor(pos);
            double frac = pos - lo;
            double a0 = select_k(x, n, lo);
            if (frac > 0 && lo + 1 < n) {
                double a1 = x[lo + 1];                  // after the select, the next smallest is the least of the upper part
                for (int64_t i = lo + 2; i < n; i++) if (x[i] < a1) a1 = x[i];
                v = a0 + (a1 - a0) * frac;
            } else v = a0;
        }
        store(out, o, 1, 0, &v);
        for (int d = nd - 2; d >= 0; d--) { if (++idx[d] < a->shape[d]) break; idx[d] = 0; }
    }
    free(x);
    return 0;
}

// ---- masks: count, the places that are true (nonzero), gather a[mask] into a flat run, scatter values into a[mask] ----
static int64_t walk_flat(pn_arr *a, int64_t (*f)(char *p, int64_t i, void *ctx), void *ctx) {
    int nd = a->ndim;
    int64_t total = 1;
    for (int d = 0; d < nd; d++) total *= a->shape[d];
    int64_t idx[MAXD] = {0}, acc = 0;
    for (int64_t i = 0; i < total; i++) {
        char *p = a->data;
        for (int d = 0; d < nd; d++) p += idx[d] * a->strides[d];
        acc += f(p, i, ctx);
        for (int d = nd - 1; d >= 0; d--) { if (++idx[d] < a->shape[d]) break; idx[d] = 0; }
    }
    return acc;
}

typedef struct { pn_arr *m; int64_t *out; int64_t k; } nz_ctx;
static int64_t nz_count(char *p, int64_t i, void *c) { (void)i; nz_ctx *x = c; double v; load(x->m, p, 1, 0, &v); return v != 0; }
static int64_t nz_take(char *p, int64_t i, void *c) {
    nz_ctx *x = c; double v; load(x->m, p, 1, 0, &v);
    if (v != 0) x->out[x->k++] = i;
    return 0;
}
int64_t pn_count(pn_arr *m) { nz_ctx c = {m, NULL, 0}; return walk_flat(m, nz_count, &c); }
// flat positions (in C order) of the true elements; perch_num turns them into per-axis indices
int64_t pn_nonzero(pn_arr *m, int64_t *out) { nz_ctx c = {m, out, 0}; walk_flat(m, nz_take, &c); return c.k; }

// gather / scatter through a list of flat positions (from pn_nonzero, or any index list) into / from a contiguous run
int pn_take(pn_arr *out, pn_arr *a, const int64_t *flat, int64_t n) {
    int nd = a->ndim;
    for (int64_t i = 0; i < n; i++) {
        int64_t f = flat[i]; char *p = a->data;
        for (int d = nd - 1; d >= 0; d--) { int64_t s = a->shape[d]; p += (f % s) * a->strides[d]; f /= s; }
        double v; load(a, p, 1, 0, &v);
        store(out, out->data + i * out->strides[0], 1, 0, &v);
    }
    return 0;
}
int pn_put(pn_arr *a, pn_arr *vals, const int64_t *flat, int64_t n) {   // vals: length n, or 1 (one value for all)
    int nd = a->ndim;
    int64_t vn = vals->ndim ? vals->shape[0] : 1;
    for (int64_t i = 0; i < n; i++) {
        int64_t f = flat[i]; char *p = a->data;
        for (int d = nd - 1; d >= 0; d--) { int64_t s = a->shape[d]; p += (f % s) * a->strides[d]; f /= s; }
        double v; load(vals, vals->data + (vn == 1 ? 0 : i) * (vals->ndim ? vals->strides[0] : 0), 1, 0, &v);
        store(a, p, 1, 0, &v);
    }
    return 0;
}

// ---- small symmetric eigen problems (Jacobi rotations): a is n x n doubles, row-major; w = values ascending, v = columns ----
int pn_eigh(int n, double *a, double *w, double *v) {
    for (int i = 0; i < n; i++) for (int j = 0; j < n; j++) v[i * n + j] = i == j;
    for (int sweep = 0; sweep < 100; sweep++) {
        double off = 0;
        for (int i = 0; i < n; i++) for (int j = i + 1; j < n; j++) off += a[i * n + j] * a[i * n + j];
        if (off < 1e-30) break;
        for (int p = 0; p < n; p++) for (int q = p + 1; q < n; q++) {
            double apq = a[p * n + q];
            if (fabs(apq) < 1e-300) continue;
            double app = a[p * n + p], aqq = a[q * n + q];
            double theta = (aqq - app) / (2 * apq);
            double t = (theta >= 0 ? 1.0 : -1.0) / (fabs(theta) + sqrt(theta * theta + 1));
            double c = 1 / sqrt(t * t + 1), s = t * c;
            for (int k = 0; k < n; k++) {                           // A = J^T A J
                double akp = a[k * n + p], akq = a[k * n + q];
                a[k * n + p] = c * akp - s * akq; a[k * n + q] = s * akp + c * akq;
            }
            for (int k = 0; k < n; k++) {
                double apk = a[p * n + k], aqk = a[q * n + k];
                a[p * n + k] = c * apk - s * aqk; a[q * n + k] = s * apk + c * aqk;
            }
            for (int k = 0; k < n; k++) {                           // V = V J
                double vkp = v[k * n + p], vkq = v[k * n + q];
                v[k * n + p] = c * vkp - s * vkq; v[k * n + q] = s * vkp + c * vkq;
            }
        }
    }
    for (int i = 0; i < n; i++) w[i] = a[i * n + i];
    for (int i = 0; i < n; i++) {                                   // ascending, columns follow (as numpy's eigh)
        int m = i;
        for (int j = i + 1; j < n; j++) if (w[j] < w[m]) m = j;
        if (m != i) {
            double t = w[i]; w[i] = w[m]; w[m] = t;
            for (int k = 0; k < n; k++) { double u = v[k * n + i]; v[k * n + i] = v[k * n + m]; v[k * n + m] = u; }
        }
    }
    return 0;
}

// ---- joined patches (4-neighbour): label each true pixel of a 2-D mask with its patch number (1, 2, ...), 0 elsewhere.
// Patches are numbered in the order their first pixel comes in C order (row by row), as Perch's old Python flood fill found them.
// Returns how many patches; labels is h*w int64, row-major. A patch is walked with its own stack, never recursion.
int64_t pn_label(pn_arr *m, int64_t *labels, int64_t *stack) {
    int64_t h = m->shape[0], w = m->ndim > 1 ? m->shape[1] : 1;
    int64_t s0 = m->strides[0], s1 = m->ndim > 1 ? m->strides[1] : 0;
    memset(labels, 0, sizeof(int64_t) * h * w);
    int64_t next = 0;
    for (int64_t y = 0; y < h; y++) for (int64_t x = 0; x < w; x++) {
        double v; load(m, m->data + y * s0 + x * s1, 1, 0, &v);
        if (v == 0 || labels[y * w + x]) continue;
        next++;
        int64_t top = 0;
        stack[top++] = y * w + x; labels[y * w + x] = next;
        while (top) {
            int64_t f = stack[--top], cy = f / w, cx = f % w;
            int64_t nb[4][2] = {{cy + 1, cx}, {cy - 1, cx}, {cy, cx + 1}, {cy, cx - 1}};
            for (int k = 0; k < 4; k++) {
                int64_t yy = nb[k][0], xx = nb[k][1];
                if (yy < 0 || yy >= h || xx < 0 || xx >= w || labels[yy * w + xx]) continue;
                double u; load(m, m->data + yy * s0 + xx * s1, 1, 0, &u);
                if (u == 0) continue;
                labels[yy * w + xx] = next; stack[top++] = yy * w + xx;
            }
        }
    }
    return next;
}

// ---- where: out = c ? x : y (all four the same shape; perch_murmur broadcasts first) ----
typedef struct { pn_arr *v[4]; double *c, *x, *y; } wh_ctx;
static void wh_run(int64_t n, char **p, int64_t *st, void *c_) {
    wh_ctx *w = c_;
    load(w->v[1], p[1], n, st[1], w->c); load(w->v[2], p[2], n, st[2], w->x); load(w->v[3], p[3], n, st[3], w->y);
    for (int64_t i = 0; i < n; i++) w->x[i] = w->c[i] != 0 ? w->x[i] : w->y[i];
    store(w->v[0], p[0], n, st[0], w->x);
}
int pn_where(pn_arr *out, pn_arr *c, pn_arr *x, pn_arr *y) {
    int64_t n = out->ndim ? out->shape[out->ndim - 1] : 1; if (!n) n = 1;
    wh_ctx w = {{out, c, x, y}, malloc(sizeof(double) * n), malloc(sizeof(double) * n), malloc(sizeof(double) * n)};
    if (!w.c || !w.x || !w.y) { free(w.c); free(w.x); free(w.y); return -1; }
    each_run(4, w.v, wh_run, &w);
    free(w.c); free(w.x); free(w.y);
    return 0;
}

// ---- the last axis against a short row of weights (a picture's colours @ (r, g, b) weights): out = sum_k a[..., k] * w[k] ----
int pn_vecdot(pn_arr *out, pn_arr *a, const double *w) {
    int nd = a->ndim;
    int64_t n = a->shape[nd - 1], st = a->strides[nd - 1], outer = 1;
    for (int d = 0; d < nd - 1; d++) outer *= a->shape[d];
    double *x = malloc(sizeof(double) * (n ? n : 1));
    if (!x) return -1;
    int64_t idx[MAXD] = {0};
    for (int64_t r = 0; r < outer; r++) {
        const char *p = a->data; char *q = out->data;
        for (int d = 0; d < nd - 1; d++) { p += idx[d] * a->strides[d]; q += idx[d] * out->strides[d]; }
        load(a, p, n, st, x);
        double s = 0; for (int64_t k = 0; k < n; k++) s += x[k] * w[k];
        store(out, q, 1, 0, &s);
        for (int d = nd - 2; d >= 0; d--) { if (++idx[d] < a->shape[d]) break; idx[d] = 0; }
    }
    free(x);
    return 0;
}
