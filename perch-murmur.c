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
#include <pthread.h>
#include <stdint.h>
#include <unistd.h>
#include <stdlib.h>
#include <string.h>

enum { U8 = 0, BOOL = 1, I64 = 2, F32 = 3, F64 = 4, I16 = 5 };   // (complex grids are float64 pairs to the C)
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
    case I16: for (int64_t i = 0; i < n; i++) out[i] = *(const int16_t *)(p + i * st); break;
    case U8: for (int64_t i = 0; i < n; i++) out[i] = *(const uint8_t *)(p + i * st); break;
    case BOOL: for (int64_t i = 0; i < n; i++) out[i] = *(const uint8_t *)(p + i * st) ? 1.0 : 0.0; break;
    case I64: for (int64_t i = 0; i < n; i++) out[i] = (double)*(const int64_t *)(p + i * st); break;
    case F32: for (int64_t i = 0; i < n; i++) out[i] = *(const float *)(p + i * st); break;
    default: for (int64_t i = 0; i < n; i++) out[i] = *(const double *)(p + i * st); break;
    }
}

static int64_t BAD;                                    // values that would not fit, added up from every thread
static __thread int64_t TBAD;                          // ... counted per thread, so threads never trip over one count
int64_t pn_bad(void) { int64_t b = __atomic_exchange_n(&BAD, 0, __ATOMIC_SEQ_CST) + TBAD; TBAD = 0; return b; }
static void store(const pn_arr *a, char *p, int64_t n, int64_t st, const double *in) {
    if (a->dtype == F32 && st == 4) { float *f = (float *)p; for (int64_t i = 0; i < n; i++) f[i] = (float)in[i]; return; }
    if (a->dtype == F64 && st == 8) { memcpy(p, in, n * sizeof(double)); return; }
    if (a->dtype == BOOL && st == 1) { uint8_t *u = (uint8_t *)p; for (int64_t i = 0; i < n; i++) u[i] = in[i] != 0.0; return; }
    switch (a->dtype) {
    case U8: for (int64_t i = 0; i < n; i++) {
            double v = in[i];
            if (!(v >= 0.0 && v <= 255.0) || v != floor(v)) { TBAD++; v = v != v ? 0 : v < 0 ? 0 : v > 255 ? 255 : floor(v); }
            *(uint8_t *)(p + i * st) = (uint8_t)v; } break;
    case BOOL: for (int64_t i = 0; i < n; i++) *(uint8_t *)(p + i * st) = in[i] != 0.0; break;
    case I16: for (int64_t i = 0; i < n; i++) {
            double v = in[i];
            if (!(v >= -32768.0 && v <= 32767.0) || v != floor(v)) { TBAD++; v = v != v ? 0 : v < -32768 ? -32768 : v > 32767 ? 32767 : floor(v); }
            *(int16_t *)(p + i * st) = (int16_t)v; } break;
    case I64: for (int64_t i = 0; i < n; i++) {
            double v = in[i];
            if (!(v >= -9.2233720368547748e18 && v < 9.2233720368547748e18) || v != floor(v)) { TBAD++; v = v != v ? 0 : floor(v); }
            *(int64_t *)(p + i * st) = (int64_t)v; } break;
    case F32: for (int64_t i = 0; i < n; i++) *(float *)(p + i * st) = (float)in[i]; break;
    default: for (int64_t i = 0; i < n; i++) *(double *)(p + i * st) = in[i]; break;
    }
}

// ---- all the cores (Mike, 16 Nov 2026: "do YOUR murmur all cores"): a range of work items split over every online core, each
// thread with its own scratch. The only bound is physical: starting a thread costs ~50 us, so work under PAR_MIN numbers runs on
// one core (splitting it would make it slower); everything bigger gets every core there is. No setting to turn it down.
#define PAR_MIN 65536
#define BLOCK 2048                                     // numbers worked at a time: 3 x 16 KB of doubles fit a core's L1/L2
int pn_cores(void) { static int n = 0; if (!n) { long c = sysconf(_SC_NPROCESSORS_ONLN); n = c > 0 ? (int)c : 1; } return n; }
typedef void (*range_fn)(int64_t i0, int64_t i1, void *ctx);
typedef struct { range_fn f; void *ctx; int64_t i0, i1; } rjob;
static void *rjob_run(void *a) {
    rjob *j = a;
    j->f(j->i0, j->i1, j->ctx);
    if (TBAD) { __atomic_add_fetch(&BAD, TBAD, __ATOMIC_SEQ_CST); TBAD = 0; }
    return NULL;
}
static void par_range(int64_t items, int64_t work, range_fn f, void *ctx) {
    int64_t T = pn_cores();
    if (work < PAR_MIN) T = 1;
    if (T > items) T = items;
    if (T <= 1) { if (items > 0) f(0, items, ctx); return; }
    pthread_t th[T]; rjob jb[T];
    int started[T];
    for (int64_t t = 0; t < T; t++) {
        jb[t] = (rjob){f, ctx, items * t / T, items * (t + 1) / T};
        started[t] = pthread_create(&th[t], NULL, rjob_run, &jb[t]) == 0;
        if (!started[t]) rjob_run(&jb[t]);            // no thread to be had: do that share here
    }
    for (int64_t t = 0; t < T; t++) if (started[t]) pthread_join(th[t], NULL);
}

// walk every run (all axes but the last) of up to four views of the same shape; call f on each run (or piece of a run: a long
// single run is cut so every core gets some). f gets scratch buffers of its own thread.
typedef void (*run_fn)(int64_t n, char **p, int64_t *st, void *ctx, double **s);
typedef struct { int k, nbuf; pn_arr **v; run_fn f; void *ctx; int64_t n, outer, piece, pieces; } runs_ctx;
static void runs_range(int64_t i0, int64_t i1, void *c_) {
    runs_ctx *c = c_;
    pn_arr *o = c->v[0];
    int nd = o->ndim;
    double *s[3] = {0, 0, 0};
    for (int b = 0; b < c->nbuf; b++) s[b] = malloc(sizeof(double) * BLOCK);   // small: stays in the core's own cache
    int64_t st[4] = {0, 0, 0, 0};
    for (int j = 0; j < c->k; j++) st[j] = nd ? c->v[j]->strides[nd - 1] : 0;
    for (int64_t it = i0; it < i1; it++) {
        int64_t r = it / c->pieces, c0 = (it % c->pieces) * c->piece;
        int64_t len = c->n - c0 < c->piece ? c->n - c0 : c->piece;
        if (len <= 0) continue;
        char *p[4];
        for (int j = 0; j < c->k; j++) {
            p[j] = c->v[j]->data + c0 * st[j];
            int64_t rr = r;
            for (int d = nd - 2; d >= 0; d--) { p[j] += (rr % o->shape[d]) * c->v[j]->strides[d]; rr /= o->shape[d]; }
        }
        for (int64_t b0 = 0; b0 < len; b0 += BLOCK) {           // in blocks: load, work and store while it is all in cache
            int64_t bl = len - b0 < BLOCK ? len - b0 : BLOCK;
            char *q[4];
            for (int j = 0; j < c->k; j++) q[j] = p[j] + b0 * st[j];
            c->f(bl, q, st, c->ctx, s);
        }
    }
    for (int b = 0; b < c->nbuf; b++) free(s[b]);
}
static void each_run(int k, pn_arr **v, run_fn f, void *ctx, int nbuf) {
    pn_arr *o = v[0];
    int nd = o->ndim;
    int64_t n = nd ? o->shape[nd - 1] : 1, outer = 1;
    for (int d = 0; d < nd - 1; d++) outer *= o->shape[d];
    if (n == 0 || outer == 0) return;
    int64_t T = pn_cores(), pieces = 1;
    if (n * outer >= PAR_MIN && outer < T) pieces = (T + outer - 1) / outer;
    int64_t piece = (n + pieces - 1) / pieces;
    pieces = (n + piece - 1) / piece;
    runs_ctx c = {k, nbuf, v, f, ctx, n, outer, piece, pieces};
    par_range(outer * pieces, n * outer, runs_range, &c);
}

// ---- elementwise: out = a OP b ----
enum { ADD, SUB, MUL, DIV, POW, MAX2, MIN2, LT, LE, GT, GE, EQ, NE, AND, OR, XOR, FLOORDIV, MOD, ATAN2 };
typedef struct { int op; pn_arr *v[3]; } bin_ctx;

// the picture case, straight: float32 (or float64) in and out, side by side or one value spread (stride 0); no conversion
#define FAST_BIN(T, OPS) do { \
    T *o = (T *)p[0]; const T *A = (const T *)p[1], *B = (const T *)p[2]; (void)o; \
    int sa = st[1] != 0, sb = st[2] != 0; \
    switch (c->op) { OPS } } while (0)
#define FB(OP, EXPR) case OP: \
    if (sa && sb) for (int64_t i = 0; i < n; i++) { T x = A[i], y = B[i]; o[i] = (EXPR); } \
    else if (sa) { T y = B[0]; for (int64_t i = 0; i < n; i++) { T x = A[i]; o[i] = (EXPR); } } \
    else if (sb) { T x = A[0]; for (int64_t i = 0; i < n; i++) { T y = B[i]; o[i] = (EXPR); } } \
    else { T x = A[0], y = B[0]; for (int64_t i = 0; i < n; i++) o[i] = (EXPR); } return;
#define FB_CMP(OP, EXPR) case OP: { uint8_t *ob = (uint8_t *)p[0]; \
    if (sa && sb) for (int64_t i = 0; i < n; i++) { T x = A[i], y = B[i]; ob[i] = (EXPR); } \
    else if (sa) { T y = B[0]; for (int64_t i = 0; i < n; i++) { T x = A[i]; ob[i] = (EXPR); } } \
    else if (sb) { T x = A[0]; for (int64_t i = 0; i < n; i++) { T y = B[i]; ob[i] = (EXPR); } } \
    else { T x = A[0], y = B[0]; for (int64_t i = 0; i < n; i++) ob[i] = (EXPR); } return; }
static int fast_ok(pn_arr *v, int64_t st, int code, int64_t size) { return v->dtype == code && (st == size || st == 0); }

static void bin_run(int64_t n, char **p, int64_t *st, void *c_, double **s) {
    bin_ctx *c = c_;
    int op = c->op;
    for (int fl = 0; fl < 2; fl++) {
        int code = fl ? F64 : F32; int64_t sz = fl ? 8 : 4;
        if (!fast_ok(c->v[1], st[1], code, sz) || !fast_ok(c->v[2], st[2], code, sz)) continue;
        if (op <= MIN2 && op != POW && c->v[0]->dtype == code && st[0] == sz) {
            if (fl) { typedef double T; (void)sizeof(T); FAST_BIN(double, FB(ADD, x + y) FB(SUB, x - y) FB(MUL, x * y) FB(DIV, x / y)
                      FB(MAX2, (x != x || x >= y) ? x : y) FB(MIN2, (x != x || x <= y) ? x : y) default: break;); }
            else { typedef float T; (void)sizeof(T); FAST_BIN(float, FB(ADD, x + y) FB(SUB, x - y) FB(MUL, x * y) FB(DIV, x / y)
                      FB(MAX2, (x != x || x >= y) ? x : y) FB(MIN2, (x != x || x <= y) ? x : y) default: break;); }
        }
        if (op >= LT && op <= NE && c->v[0]->dtype == BOOL && st[0] == 1) {
            if (fl) { typedef double T; (void)sizeof(T); FAST_BIN(double, FB_CMP(LT, x < y) FB_CMP(LE, x <= y) FB_CMP(GT, x > y)
                      FB_CMP(GE, x >= y) FB_CMP(EQ, x == y) FB_CMP(NE, x != y) default: break;); }
            else { typedef float T; (void)sizeof(T); FAST_BIN(float, FB_CMP(LT, x < y) FB_CMP(LE, x <= y) FB_CMP(GT, x > y)
                      FB_CMP(GE, x >= y) FB_CMP(EQ, x == y) FB_CMP(NE, x != y) default: break;); }
        }
    }
    double *x = s[0], *y = s[1];
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
    bin_ctx c = {op, {out, a, b}};
    each_run(3, c.v, bin_run, &c, 2);
    return 0;
}

// ---- elementwise: out = f(a); also the copy that makes astype, contiguous copies and slice assignment ----
enum { COPY, NEG, ABS, SQRT, LOG, LOG10, EXP, SIN, COS, TAN, FLOOR, CEIL, ROUND, NOT, ISNAN, SIGN, ATAN, LOG2, TRUNC };
typedef struct { int op; double lo, hi; int clip; pn_arr *v[2]; } un_ctx;

#define FAST_UN(T, SQ, AB) do { T *o = (T *)p[0]; const T *A = (const T *)p[1]; \
    switch (c->op) { \
    case COPY: for (int64_t i = 0; i < n; i++) o[i] = A[i]; return; \
    case NEG: for (int64_t i = 0; i < n; i++) o[i] = -A[i]; return; \
    case ABS: for (int64_t i = 0; i < n; i++) o[i] = AB(A[i]); return; \
    case SQRT: for (int64_t i = 0; i < n; i++) o[i] = SQ(A[i]); return; \
    default: break; } } while (0)
static void un_run(int64_t n, char **p, int64_t *st, void *c_, double **s) {
    un_ctx *c = c_;
    if (!c->clip && c->v[0]->dtype == c->v[1]->dtype && st[0] == st[1]) {
        if (c->v[0]->dtype == F32 && st[0] == 4) FAST_UN(float, sqrtf, fabsf);
        if (c->v[0]->dtype == F64 && st[0] == 8) FAST_UN(double, sqrt, fabs);
    }
    double *x = s[0];
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
    un_ctx c = {op, lo, hi, clip, {out, a}};
    each_run(2, c.v, un_run, &c, 1);
    return 0;
}

// ---- reductions along the LAST axis (perch_num moves the asked axis there first): out has the shape without it ----
enum { SUM, PROD, RMAX, RMIN, ARGMAX, ARGMIN, ANY, ALL, SUMSQ };
typedef struct { double v, c; int64_t k; } part;            // a piece's answer: value, Kahan carry, index (arg*)
static part reduce_piece(int op, const double *x, int64_t n, int64_t base) {
    part r = {0, 0, base};
    switch (op) {
    case SUM: {                       // 8 running totals the CPU adds side by side, then Kahan between groups of 8: near-exact and wide
        double s = 0, c = 0; int64_t i = 0;
        for (; i + 64 <= n; i += 64) {
            double t8[8] = {0, 0, 0, 0, 0, 0, 0, 0};
            for (int64_t j = 0; j < 64; j += 8) for (int k = 0; k < 8; k++) t8[k] += x[i + j + k];
            double g = ((t8[0] + t8[1]) + (t8[2] + t8[3])) + ((t8[4] + t8[5]) + (t8[6] + t8[7]));
            double y = g - c, t = s + y; c = (t - s) - y; s = t;
        }
        for (; i < n; i++) { double y = x[i] - c, t = s + y; c = (t - s) - y; s = t; }
        r.v = s; r.c = c; break; }
    case SUMSQ: { double s = 0; for (int64_t i = 0; i < n; i++) s += x[i] * x[i]; r.v = s; break; }
    case PROD: r.v = 1; for (int64_t i = 0; i < n; i++) r.v *= x[i]; break;
    case RMAX: r.v = n ? x[0] : NAN; for (int64_t i = 1; i < n; i++) if (x[i] > r.v || x[i] != x[i]) { r.v = x[i]; if (r.v != r.v) break; } break;
    case RMIN: r.v = n ? x[0] : NAN; for (int64_t i = 1; i < n; i++) if (x[i] < r.v || x[i] != x[i]) { r.v = x[i]; if (r.v != r.v) break; } break;
    case ARGMAX: { int64_t k = 0; for (int64_t i = 1; i < n; i++) if (x[i] > x[k]) k = i; r.v = n ? x[k] : NAN; r.k = base + k; break; }
    case ARGMIN: { int64_t k = 0; for (int64_t i = 1; i < n; i++) if (x[i] < x[k]) k = i; r.v = n ? x[k] : NAN; r.k = base + k; break; }
    case ANY: r.v = 0; for (int64_t i = 0; i < n; i++) if (x[i] != 0) { r.v = 1; break; } break;
    case ALL: r.v = 1; for (int64_t i = 0; i < n; i++) if (x[i] == 0) { r.v = 0; break; } break;
    }
    return r;
}
static part combine(int op, part a, part b) {                // pieces in order: a before b (ties keep the first, as numpy)
    switch (op) {
    case SUM: { double y = b.v - a.c - b.c, t = a.v + y; a.c = (t - a.v) - y; a.v = t; return a; }
    case SUMSQ: a.v += b.v; return a;
    case PROD: a.v *= b.v; return a;
    case RMAX: if (a.v != a.v) return a; if (b.v != b.v || b.v > a.v) return b; return a;
    case RMIN: if (a.v != a.v) return a; if (b.v != b.v || b.v < a.v) return b; return a;
    case ARGMAX: return b.v > a.v ? b : a;
    case ARGMIN: return b.v < a.v ? b : a;
    case ANY: a.v = a.v != 0 || b.v != 0; return a;
    case ALL: a.v = a.v != 0 && b.v != 0; return a;
    }
    return a;
}
static double answer(int op, part r) { return (op == ARGMAX || op == ARGMIN) ? (double)r.k : r.v; }

typedef struct { int op; pn_arr *out, *a; int64_t n, st; } red_ctx;
static void reduce_rows(int64_t r0, int64_t r1, void *c_) {
    red_ctx *c = c_;
    pn_arr *a = c->a, *out = c->out;
    int nd = a->ndim;
    double *x = malloc(sizeof(double) * (c->n ? c->n : 1));
    for (int64_t r = r0; r < r1; r++) {
        const char *p = a->data; char *q = out->data;
        int64_t rr = r;
        for (int d = nd - 2; d >= 0; d--) { int64_t i = rr % a->shape[d]; rr /= a->shape[d]; p += i * a->strides[d]; q += i * out->strides[d]; }
        load(a, p, c->n, c->st, x);
        double v = answer(c->op, reduce_piece(c->op, x, c->n, 0));
        store(out, q, 1, 0, &v);
    }
    free(x);
}
typedef struct { int op; pn_arr *a; int64_t n, st, pieces; part *parts; } one_ctx;
static void reduce_pieces(int64_t i0, int64_t i1, void *c_) {
    one_ctx *c = c_;
    double *x = malloc(sizeof(double) * BLOCK);              // in cache-sized blocks, each block's answer combined in order
    for (int64_t i = i0; i < i1; i++) {
        int64_t lo = c->n * i / c->pieces, hi = c->n * (i + 1) / c->pieces;
        part r = {0, 0, lo}; int first = 1;
        for (int64_t b0 = lo; b0 < hi; b0 += BLOCK) {
            int64_t bl = hi - b0 < BLOCK ? hi - b0 : BLOCK;
            load(c->a, c->a->data + b0 * c->st, bl, c->st, x);
            part pb = reduce_piece(c->op, x, bl, b0);
            r = first ? pb : combine(c->op, r, pb); first = 0;
        }
        c->parts[i] = r;
    }
    free(x);
}
// reductions along the LAST axis (perch_murmur moves the asked axis there first): out has the shape without it
int pn_reduce(int op, pn_arr *out, pn_arr *a) {
    int nd = a->ndim;
    int64_t n = nd ? a->shape[nd - 1] : 1, st = nd ? a->strides[nd - 1] : 0, outer = 1;
    for (int d = 0; d < nd - 1; d++) outer *= a->shape[d];
    if (outer == 1 && n >= PAR_MIN && pn_cores() > 1) {      // one long row: in pieces over the cores, then combined in order
        int64_t pieces = pn_cores();
        part *parts = malloc(sizeof(part) * pieces);
        one_ctx c = {op, a, n, st, pieces, parts};
        par_range(pieces, n, reduce_pieces, &c);
        part r = parts[0];
        for (int64_t i = 1; i < pieces; i++) r = combine(op, r, parts[i]);
        double v = answer(op, r);
        store(out, out->data, 1, 0, &v);
        free(parts);
        return 0;
    }
    red_ctx c = {op, out, a, n, st};
    par_range(outer, n * outer, reduce_rows, &c);
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

typedef struct { pn_arr *out, *a; int64_t n, st; double q; } pct_ctx;
static void pct_rows(int64_t r0, int64_t r1, void *c_) {
    pct_ctx *c = c_;
    pn_arr *a = c->a, *out = c->out;
    int nd = a->ndim;
    int64_t n = c->n;
    double *x = malloc(sizeof(double) * (n ? n : 1));
    for (int64_t r = r0; r < r1; r++) {
        const char *p = a->data; char *o = out->data;
        int64_t rr = r;
        for (int d = nd - 2; d >= 0; d--) { int64_t i = rr % a->shape[d]; rr /= a->shape[d]; p += i * a->strides[d]; o += i * out->strides[d]; }
        load(a, p, n, c->st, x);
        double v = NAN;
        int nan = 0;
        for (int64_t i = 0; i < n; i++) if (x[i] != x[i]) { nan = 1; break; }
        if (n && !nan) {
            double pos = (n - 1) * c->q / 100.0;
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
    }
    free(x);
}
int pn_percentile(pn_arr *out, pn_arr *a, double q) {
    int nd = a->ndim;
    int64_t n = nd ? a->shape[nd - 1] : 1, st = nd ? a->strides[nd - 1] : 0, outer = 1;
    for (int d = 0; d < nd - 1; d++) outer *= a->shape[d];
    pct_ctx c = {out, a, n, st, q};
    par_range(outer, n * outer, pct_rows, &c);
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
typedef struct { pn_arr *v[4]; } wh_ctx;
static void wh_run(int64_t n, char **p, int64_t *st, void *c_, double **s) {
    wh_ctx *w = c_;
    double *c = s[0], *x = s[1], *y = s[2];
    load(w->v[1], p[1], n, st[1], c); load(w->v[2], p[2], n, st[2], x); load(w->v[3], p[3], n, st[3], y);
    for (int64_t i = 0; i < n; i++) x[i] = c[i] != 0 ? x[i] : y[i];
    store(w->v[0], p[0], n, st[0], x);
}
int pn_where(pn_arr *out, pn_arr *c, pn_arr *x, pn_arr *y) {
    wh_ctx w = {{out, c, x, y}};
    each_run(4, w.v, wh_run, &w, 3);
    return 0;
}

// ---- the last axis against a short row of weights (a picture's colours @ (r, g, b) weights): out = sum_k a[..., k] * w[k] ----
typedef struct { pn_arr *out, *a; const double *w; int64_t n, st; } vd_ctx;
static void vd_rows(int64_t r0, int64_t r1, void *c_) {
    vd_ctx *c = c_;
    pn_arr *a = c->a, *out = c->out;
    int nd = a->ndim;
    double *x = malloc(sizeof(double) * (c->n ? c->n : 1));
    for (int64_t r = r0; r < r1; r++) {
        const char *p = a->data; char *q = out->data;
        int64_t rr = r;
        for (int d = nd - 2; d >= 0; d--) { int64_t i = rr % a->shape[d]; rr /= a->shape[d]; p += i * a->strides[d]; q += i * out->strides[d]; }
        double s = 0;
        if (a->dtype == F32 && c->st == 4) { const float *f = (const float *)p; for (int64_t k = 0; k < c->n; k++) s += f[k] * c->w[k]; }
        else { load(a, p, c->n, c->st, x); for (int64_t k = 0; k < c->n; k++) s += x[k] * c->w[k]; }
        if (out->dtype == F32) *(float *)q = (float)s; else store(out, q, 1, 0, &s);
    }
    free(x);
}
int pn_vecdot(pn_arr *out, pn_arr *a, const double *w) {
    int nd = a->ndim;
    int64_t n = a->shape[nd - 1], st = a->strides[nd - 1], outer = 1;
    for (int d = 0; d < nd - 1; d++) outer *= a->shape[d];
    vd_ctx c = {out, a, w, n, st};
    par_range(outer, n * outer, vd_rows, &c);
    return 0;
}

// ---- memory for grids: big ones on 2 MB huge pages (one page fault per 2 MB, not per 4 KB: a 25 MB picture grid was ~6,000
// faults), never zeroed first (every murmur grid is filled at once), first touched by the threads that fill it.
#include <sys/mman.h>
#define HUGE_FROM (4 << 20)
void *pn_alloc(int64_t nbytes) {
    if (nbytes < 1) nbytes = 1;
    if (nbytes >= HUGE_FROM) {
        void *p = NULL;
        size_t sz = ((size_t)nbytes + (2u << 20) - 1) & ~((size_t)(2u << 20) - 1);
        if (posix_memalign(&p, 2u << 20, sz) == 0) { madvise(p, sz, MADV_HUGEPAGE); return p; }
    }
    return malloc((size_t)nbytes);
}
void pn_free(void *p) { free(p); }

// ---- reduce over a MIDDLE axis row onto row (memory side by side): a is (pre, m, post), out (pre, post). Columns of post split
// over the cores; each keeps double running values for its columns (sum, max, min, any, all).
typedef struct { int op; pn_arr *out, *a; } mid_ctx;
static void mid_cols(int64_t c0, int64_t c1, void *c_) {
    mid_ctx *c = c_;
    pn_arr *a = c->a, *out = c->out;
    int64_t pre = a->shape[0], m = a->shape[1], post = a->shape[2];
    (void)post;
    int64_t w = c1 - c0;
    double *acc = malloc(sizeof(double) * (w ? w : 1)), *row = malloc(sizeof(double) * (w ? w : 1));
    for (int64_t q = 0; q < pre; q++) {
        for (int64_t i = 0; i < m; i++) {
            load(a, a->data + q * a->strides[0] + i * a->strides[1] + c0 * a->strides[2], w, a->strides[2], row);
            if (i == 0) { memcpy(acc, row, sizeof(double) * w); if (c->op == ANY || c->op == ALL) for (int64_t j = 0; j < w; j++) acc[j] = acc[j] != 0; continue; }
            switch (c->op) {
            case SUM: for (int64_t j = 0; j < w; j++) acc[j] += row[j]; break;
            case RMAX: for (int64_t j = 0; j < w; j++) if (row[j] > acc[j] || row[j] != row[j]) acc[j] = acc[j] != acc[j] ? acc[j] : row[j]; break;
            case RMIN: for (int64_t j = 0; j < w; j++) if (row[j] < acc[j] || row[j] != row[j]) acc[j] = acc[j] != acc[j] ? acc[j] : row[j]; break;
            case ANY: for (int64_t j = 0; j < w; j++) acc[j] = acc[j] != 0 || row[j] != 0; break;
            case ALL: for (int64_t j = 0; j < w; j++) acc[j] = acc[j] != 0 && row[j] != 0; break;
            }
        }
        store(out, out->data + q * out->strides[0] + c0 * out->strides[1], w, out->strides[1], acc);
    }
    free(acc); free(row);
}
int pn_reduce_mid(int op, pn_arr *out, pn_arr *a) {          // only SUM, RMAX, RMIN, ANY, ALL (perch_murmur asks only these)
    mid_ctx c = {op, out, a};
    par_range(a->shape[2], a->shape[0] * a->shape[1] * a->shape[2], mid_cols, &c);
    return 0;
}


// ---- the FFT (Mike, 16 Nov 2026: "do the FFT next"): any length. Powers of two by iterative radix-2; every other length by
// Bluestein's way (the length-n transform as a power-of-two convolution), so no length is refused. Doubles throughout. A plan
// (twiddles, Bluestein's chirp and its spectrum) is made once per call and shared read-only by every core; rows split over cores.
typedef struct { double re, im; } cpx;

// a plan: the length's factors (4, 2, 3, 5, then other primes up to 13) and its twiddles; Bluestein (through a plan for a power
// of two) only when a prime factor is bigger than 13. Plans are kept per length (made once, shared read-only by every core).
#define MAXF 64
typedef struct fft_plan { int64_t n; int nf; int f[MAXF]; cpx *tw; int blue, pow2; int64_t m; struct fft_plan *sub; cpx *chirp, *bspec; cpx *rtw; } fft_plan;
static cpx *twiddles_full(int64_t n) {
    cpx *t = malloc(sizeof(cpx) * (n ? n : 1));
    for (int64_t k = 0; k < n; k++) { double a = -2.0 * M_PI * (double)k / (double)n; t[k] = (cpx){cos(a), sin(a)}; }
    return t;
}
static int factorise(int64_t n, int *f) {
    int nf = 0;
    while (n % 4 == 0) { f[nf++] = 4; n /= 4; }
    while (n % 2 == 0) { f[nf++] = 2; n /= 2; }
    for (int p = 3; p <= 13; p += 2) while (n % p == 0) { f[nf++] = p; n /= p; }
    return n == 1 ? nf : -1;                                   // -1: a prime bigger than 13 is left (Bluestein)
}
static void fft2(cpx *a, int64_t n, const cpx *tw, int64_t N) {      // in place, n a power of two; tw: the table for length N
    for (int64_t i = 1, j = 0; i < n; i++) {
        int64_t bit = n >> 1;
        for (; j & bit; bit >>= 1) j ^= bit;
        j ^= bit;
        if (i < j) { cpx t = a[i]; a[i] = a[j]; a[j] = t; }
    }
    for (int64_t len = 2; len <= n; len <<= 1) {
        int64_t half = len >> 1, step = N / len;
        for (int64_t i = 0; i < n; i += len)
            for (int64_t k = 0; k < half; k++) {
                cpx w = tw[k * step], u = a[i + k], v = a[i + k + half];
                cpx t = {v.re * w.re - v.im * w.im, v.re * w.im + v.im * w.re};
                a[i + k] = (cpx){u.re + t.re, u.im + t.im};
                a[i + k + half] = (cpx){u.re - t.re, u.im - t.im};
            }
    }
}
static void cfft_rec(const cpx *in, cpx *out, int64_t n, int64_t stride, const int *f, const cpx *tw, int64_t N) {
    if (n == 1) { out[0] = in[0]; return; }
    int p = f[0]; int64_t m = n / p, step = N / n;
    for (int q = 0; q < p; q++) cfft_rec(in + q * stride, out + q * m, m, stride * p, f + 1, tw, N);
    for (int64_t k = 0; k < m; k++) {
        cpx x[16];
        for (int q = 0; q < p; q++) {                          // x_q = out[q m + k] * W_n^(q k)
            cpx v = out[q * m + k], w = tw[(int64_t)q * k * step];      // q k step < N: no remainder needed
            x[q] = q ? (cpx){v.re * w.re - v.im * w.im, v.re * w.im + v.im * w.re} : v;
        }
        if (p == 2) {
            out[k] = (cpx){x[0].re + x[1].re, x[0].im + x[1].im};
            out[k + m] = (cpx){x[0].re - x[1].re, x[0].im - x[1].im};
        } else if (p == 4) {
            cpx a = {x[0].re + x[2].re, x[0].im + x[2].im}, b = {x[0].re - x[2].re, x[0].im - x[2].im};
            cpx c = {x[1].re + x[3].re, x[1].im + x[3].im}, d = {x[1].re - x[3].re, x[1].im - x[3].im};
            out[k] = (cpx){a.re + c.re, a.im + c.im};
            out[k + m] = (cpx){b.re + d.im, b.im - d.re};       // b - i d
            out[k + 2 * m] = (cpx){a.re - c.re, a.im - c.im};
            out[k + 3 * m] = (cpx){b.re - d.im, b.im + d.re};   // b + i d
        } else {                                               // any small prime: W_p^(j q) from the table
            for (int j = 0; j < p; j++) {
                cpx s = {0, 0};
                for (int q = 0; q < p; q++) {
                    cpx w = tw[(((int64_t)j * q) % p) * (N / p)];      // (j q mod p: small numbers)
                    s.re += x[q].re * w.re - x[q].im * w.im; s.im += x[q].re * w.im + x[q].im * w.re;
                }
                out[k + j * m] = s;
            }
        }
    }
}
static fft_plan *plan_new(int64_t n) {
    fft_plan *p = calloc(1, sizeof *p);
    p->n = n;
    p->pow2 = (n & (n - 1)) == 0;
    p->rtw = malloc(sizeof(cpx) * (n + 1));                    // e^(-2 pi i k / 2n): untangles a real row of 2n packed into n
    for (int64_t k = 0; k <= n; k++) { double a = -M_PI * (double)k / (double)n; p->rtw[k] = (cpx){cos(a), sin(a)}; }
    p->nf = factorise(n, p->f);
    if (p->nf >= 0) { p->tw = twiddles_full(n); return p; }
    p->blue = 1;
    p->m = 1; while (p->m < 2 * n - 1) p->m <<= 1;
    p->sub = plan_new(p->m);
    p->chirp = malloc(sizeof(cpx) * n);                        // w_k = e^(-i pi k^2 / n), k^2 taken mod 2n to stay exact
    for (int64_t k = 0; k < n; k++) { double a = -M_PI * (double)((k * k) % (2 * n)) / (double)n; p->chirp[k] = (cpx){cos(a), sin(a)}; }
    cpx *b = calloc(p->m, sizeof(cpx)), *t = malloc(sizeof(cpx) * p->m);
    for (int64_t k = 0; k < n; k++) { cpx c = {p->chirp[k].re, -p->chirp[k].im}; b[k] = c; if (k) b[p->m - k] = c; }
    cfft_rec(b, t, p->m, 1, p->sub->f, p->sub->tw, p->m);
    p->bspec = t; free(b);
    return p;
}
static void plan_free(fft_plan *p) { if (!p) return; free(p->tw); free(p->chirp); free(p->bspec); free(p->rtw); plan_free(p->sub); free(p); }
// kept plans: one per length asked for, made once (lengths Perch uses are few; past 64 lengths a plan is made and freed per call)
static fft_plan *KEPT[64]; static int NKEPT; static pthread_mutex_t KEPT_LOCK = PTHREAD_MUTEX_INITIALIZER;
static fft_plan *plan_get(int64_t n, int *temp) {
    pthread_mutex_lock(&KEPT_LOCK);
    for (int i = 0; i < NKEPT; i++) if (KEPT[i]->n == n) { pthread_mutex_unlock(&KEPT_LOCK); *temp = 0; return KEPT[i]; }
    fft_plan *p = plan_new(n);
    if (NKEPT < 64) { KEPT[NKEPT++] = p; *temp = 0; } else *temp = 1;
    pthread_mutex_unlock(&KEPT_LOCK);
    return p;
}
static int64_t plan_work(const fft_plan *p) { return p->blue ? 2 * p->m : p->n; }   // scratch a call needs
// a: n values in, their transform out (inv: the inverse, not yet divided by n). work: plan_work(p) values of scratch.
static void fft_any(const fft_plan *p, cpx *a, cpx *work, int inv) {
    int64_t n = p->n;
    if (inv) for (int64_t k = 0; k < n; k++) a[k].im = -a[k].im;   // the inverse: conj, forward, conj
    if (!p->blue && p->pow2) {
        fft2(a, n, p->tw, n);
    } else if (!p->blue) {
        memcpy(work, a, sizeof(cpx) * n);
        cfft_rec(work, a, n, 1, p->f, p->tw, n);
    } else {
        int64_t m = p->m;
        cpx *u = work;
        for (int64_t k = 0; k < n; k++) { cpx w = p->chirp[k]; u[k] = (cpx){a[k].re * w.re - a[k].im * w.im, a[k].re * w.im + a[k].im * w.re}; }
        for (int64_t k = n; k < m; k++) u[k] = (cpx){0, 0};
        fft2(u, m, p->sub->tw, m);
        for (int64_t k = 0; k < m; k++) { cpx b = p->bspec[k], x = u[k]; u[k] = (cpx){x.re * b.re - x.im * b.im, -(x.re * b.im + x.im * b.re)}; }
        fft2(u, m, p->sub->tw, m);                             // inverse of the product by the conj trick: forward of its conj, conj after
        for (int64_t k = 0; k < n; k++) {
            cpx x = {u[k].re / (double)m, -u[k].im / (double)m}, w = p->chirp[k];
            a[k] = (cpx){x.re * w.re - x.im * w.im, x.re * w.im + x.im * w.re};
        }
    }
    if (inv) for (int64_t k = 0; k < n; k++) a[k].im = -a[k].im;
}

// rfft of every row: in (..., L) any real kind, cut or zero-padded to n; out (..., n/2+1) complex as float64 pairs (..., n/2+1, 2)
typedef struct { pn_arr *out, *in; int64_t n, L, rows; fft_plan *plan, *half; } rfft_ctx;
static void rfft_rows(int64_t r0, int64_t r1, void *c_) {
    rfft_ctx *c = c_;
    int64_t n = c->n, m2 = n / 2 + 1, L = c->L < n ? c->L : n;
    cpx *a = malloc(sizeof(cpx) * n), *work = malloc(sizeof(cpx) * plan_work(c->plan));
    double *x = malloc(sizeof(double) * (L ? L : 1));
    pn_arr *in = c->in, *out = c->out;
    int nd = in->ndim;
    for (int64_t r = r0; r < r1; r++) {
        const char *p = in->data; char *q = out->data;
        int64_t rr = r;
        for (int d = nd - 2; d >= 0; d--) { int64_t i = rr % in->shape[d]; rr /= in->shape[d]; p += i * in->strides[d]; q += i * out->strides[d]; }
        load(in, p, L, in->strides[nd - 1], x);
        int64_t so = out->strides[nd - 1];
        if (c->half) {                                          // even n: z_j = x_2j + i x_2j+1 (n/2 values), one FFT of n/2
            int64_t h = n / 2;
            for (int64_t j = 0; j < h; j++) a[j] = (cpx){2 * j < L ? x[2 * j] : 0.0, 2 * j + 1 < L ? x[2 * j + 1] : 0.0};
            fft_any(c->half, a, work, 0);
            const cpx *rt = c->half->rtw;                       // e^(-2 pi i k / n)
            for (int64_t k = 0; k <= h; k++) {
                cpx zk = a[k % h], zc = a[(h - k) % h]; zc.im = -zc.im;       // Z_k and conj(Z_(h-k))
                cpx e = {(zk.re + zc.re) / 2, (zk.im + zc.im) / 2};           // the even samples' part
                cpx d = {(zk.re - zc.re) / 2, (zk.im - zc.im) / 2};           // i times the odd samples' part
                cpx o = {d.im, -d.re};                                         // d / i
                cpx w = rt[k];
                double *out2 = (double *)(q + k * so);
                out2[0] = e.re + (o.re * w.re - o.im * w.im);
                out2[1] = e.im + (o.re * w.im + o.im * w.re);
            }
            continue;
        }
        for (int64_t k = 0; k < n; k++) a[k] = (cpx){k < L ? x[k] : 0.0, 0.0};
        fft_any(c->plan, a, work, 0);
        for (int64_t k = 0; k < m2; k++) { double *o = (double *)(q + k * so); o[0] = a[k].re; o[1] = a[k].im; }
    }
    free(a); free(work); free(x);
}
int pn_rfft(pn_arr *out, pn_arr *in, int64_t n) {
    int temp, temph = 0;
    fft_plan *half = n % 2 == 0 && n >= 4 ? plan_get(n / 2, &temph) : NULL;   // real rows: half the work
    fft_plan *plan = half ? half : plan_get(n, &temp);
    if (half) temp = 0;
    int nd = in->ndim; int64_t rows = 1;
    for (int d = 0; d < nd - 1; d++) rows *= in->shape[d];
    rfft_ctx c = {out, in, n, in->shape[nd - 1], rows, plan, half};
    par_range(rows, rows * n * 8, rfft_rows, &c);
    if (temp) plan_free(plan);
    if (temph) plan_free(half);
    return 0;
}

// irfft of every row: in (..., k) half spectrum as float64 pairs (..., k, 2) (or real (..., k) when cplx = 0); out (..., n) real.
// As numpy: the first n/2+1 values are used (zeros past the end), the rest come from symmetry; the imaginary parts of the
// first value (and of the middle one when n is even) are not used.
typedef struct { pn_arr *out, *in; int cplx; int64_t n, k; fft_plan *plan; } irfft_ctx;
static void irfft_rows(int64_t r0, int64_t r1, void *c_) {
    irfft_ctx *c = c_;
    int64_t n = c->n, m2 = n / 2 + 1;
    cpx *a = malloc(sizeof(cpx) * n), *work = malloc(sizeof(cpx) * plan_work(c->plan));
    double *ro = malloc(sizeof(double) * n);
    pn_arr *in = c->in, *out = c->out;
    int ndo = out->ndim;
    for (int64_t r = r0; r < r1; r++) {
        const char *p = in->data; char *q = out->data;
        int64_t rr = r;
        for (int d = ndo - 2; d >= 0; d--) { int64_t i = rr % out->shape[d]; rr /= out->shape[d]; p += i * in->strides[d]; q += i * out->strides[d]; }
        int64_t si = in->strides[ndo - 1];
        for (int64_t k = 0; k < m2; k++) {
            cpx v = {0, 0};
            if (k < c->k) {
                if (c->cplx) { const double *d = (const double *)(p + k * si); v = (cpx){d[0], d[1]}; }
                else { double d; load(in, p + k * si, 1, 0, &d); v = (cpx){d, 0}; }
            }
            if (k == 0 || (n % 2 == 0 && k == n / 2)) v.im = 0;
            a[k] = v;
            if (k && k < n - k) a[n - k] = (cpx){v.re, -v.im};
        }
        fft_any(c->plan, a, work, 1);
        for (int64_t k = 0; k < n; k++) ro[k] = a[k].re / (double)n;
        store(out, q, n, out->strides[ndo - 1], ro);
    }
    free(a); free(work); free(ro);
}
int pn_irfft(pn_arr *out, pn_arr *in, int cplx, int64_t k, int64_t n) {
    int temp; fft_plan *plan = plan_get(n, &temp);
    int nd = out->ndim; int64_t rows = 1;
    for (int d = 0; d < nd - 1; d++) rows *= out->shape[d];
    irfft_ctx c = {out, in, cplx, n, k, plan};
    par_range(rows, rows * n * 8, irfft_rows, &c);
    if (temp) plan_free(plan);
    return 0;
}

// complex parts, from float64 pairs (n, 2) side by side: 0 abs, 1 abs squared (power), 2 angle
typedef struct { pn_arr *out; const double *pr; int mode; } cp_ctx;
static void cpart_range(int64_t i0, int64_t i1, void *c_) {
    cp_ctx *c = c_;
    double buf[BLOCK];
    for (int64_t b0 = i0; b0 < i1; b0 += BLOCK) {
        int64_t bl = i1 - b0 < BLOCK ? i1 - b0 : BLOCK;
        for (int64_t i = 0; i < bl; i++) {
            double re = c->pr[2 * (b0 + i)], im = c->pr[2 * (b0 + i) + 1];
            buf[i] = c->mode == 0 ? hypot(re, im) : c->mode == 1 ? re * re + im * im : atan2(im, re);
        }
        store(c->out, c->out->data + b0 * c->out->strides[0], bl, c->out->strides[0], buf);
    }
}
int pn_cpart(pn_arr *out, const double *pairs, int64_t n, int mode) {
    cp_ctx c = {out, pairs, mode};
    par_range(n, n, cpart_range, &c);
    return 0;
}

// ---- a ramp: out[k] = start + k * step (arange, linspace, FFT frequencies, windows' positions), split over the cores
typedef struct { pn_arr *out; double start, step; } ramp_ctx;
static void ramp_range(int64_t i0, int64_t i1, void *c_) {
    ramp_ctx *c = c_;
    double buf[BLOCK];
    for (int64_t b0 = i0; b0 < i1; b0 += BLOCK) {
        int64_t bl = i1 - b0 < BLOCK ? i1 - b0 : BLOCK;
        for (int64_t i = 0; i < bl; i++) buf[i] = c->start + (double)(b0 + i) * c->step;
        store(c->out, c->out->data + b0 * c->out->strides[0], bl, c->out->strides[0], buf);
    }
}
int pn_ramp(pn_arr *out, double start, double step) {
    ramp_ctx c = {out, start, step};
    par_range(out->shape[0], out->shape[0], ramp_range, &c);
    return 0;
}

// ---- a[mask] when the mask has a's own shape: the picked values, in reading order, into a contiguous run. Returns how many.
int64_t pn_mask_take(pn_arr *out, pn_arr *a, pn_arr *m) {
    int nd = a->ndim;
    int64_t total = 1, k = 0;
    for (int d = 0; d < nd; d++) total *= a->shape[d];
    int64_t idx[MAXD] = {0};
    for (int64_t i = 0; i < total; i++) {
        const char *pa = a->data, *pm = m->data;
        for (int d = 0; d < nd; d++) { pa += idx[d] * a->strides[d]; pm += idx[d] * m->strides[d]; }
        if (*(const uint8_t *)pm) { double v; load(a, pa, 1, 0, &v); store(out, out->data + k * out->strides[0], 1, 0, &v); k++; }
        for (int d = nd - 1; d >= 0; d--) { if (++idx[d] < a->shape[d]) break; idx[d] = 0; }
    }
    return k;
}

// ======== stage 3 (Mike, 16 Nov 2026: "do the next murmur stage for voices and cuts") ========

// ---- matrix multiply: out (m, n) = a (m, k) @ b (k, n), any kinds and strides. b is read once into doubles, shared read-only;
// rows of a over the cores; inner loop along b's rows (side by side: wide instructions).
typedef struct { pn_arr *out, *a; const double *B; int64_t m, k, n; } mm_ctx;
static void mm_rows(int64_t i0, int64_t i1, void *c_) {
    mm_ctx *c = c_;
    int64_t k = c->k, n = c->n;
    double *ar = malloc(sizeof(double) * (k ? k : 1)), *acc = malloc(sizeof(double) * (n ? n : 1));
    for (int64_t i = i0; i < i1; i++) {
        load(c->a, c->a->data + i * c->a->strides[0], k, c->a->strides[1], ar);
        memset(acc, 0, sizeof(double) * n);
        for (int64_t l = 0; l < k; l++) {
            double v = ar[l]; const double *br = c->B + l * n;
            if (v != 0.0) for (int64_t j = 0; j < n; j++) acc[j] += v * br[j];
            else for (int64_t j = 0; j < n; j++) acc[j] += 0.0 * br[j];     // keeps NaN/inf in b as numpy does
        }
        store(c->out, c->out->data + i * c->out->strides[0], n, c->out->strides[1], acc);
    }
    free(ar); free(acc);
}
int pn_matmul(pn_arr *out, pn_arr *a, pn_arr *b) {
    int64_t m = a->shape[0], k = a->shape[1], n = b->shape[1];
    double *B = malloc(sizeof(double) * (k * n > 0 ? k * n : 1));
    if (!B) return -1;
    for (int64_t l = 0; l < k; l++) load(b, b->data + l * b->strides[0], n, b->strides[1], B + l * n);
    mm_ctx c = {out, a, B, m, k, n};
    par_range(m, m * k * n, mm_rows, &c);
    free(B);
    return 0;
}

// ---- sort along the last axis (values, or the order as indices): NaN last; ties keep their order (stable), rows over the cores
typedef struct { double v; int64_t i; } vi;
static int vi_cmp(const void *x, const void *y) {
    const vi *a = x, *b = y;
    int an = a->v != a->v, bn = b->v != b->v;
    if (an || bn) { if (an && bn) return a->i < b->i ? -1 : 1; return an ? 1 : -1; }
    if (a->v < b->v) return -1;
    if (a->v > b->v) return 1;
    return a->i < b->i ? -1 : a->i > b->i;
}
typedef struct { pn_arr *out, *a; int args; int64_t n; } sort_ctx;
static void sort_rows(int64_t r0, int64_t r1, void *c_) {
    sort_ctx *c = c_;
    pn_arr *a = c->a, *out = c->out;
    int nd = a->ndim; int64_t n = c->n;
    double *x = malloc(sizeof(double) * (n ? n : 1)); vi *p = malloc(sizeof(vi) * (n ? n : 1));
    for (int64_t r = r0; r < r1; r++) {
        const char *pa = a->data; char *q = out->data;
        int64_t rr = r;
        for (int d = nd - 2; d >= 0; d--) { int64_t i = rr % a->shape[d]; rr /= a->shape[d]; pa += i * a->strides[d]; q += i * out->strides[d]; }
        load(a, pa, n, a->strides[nd - 1], x);
        for (int64_t i = 0; i < n; i++) p[i] = (vi){x[i], i};
        qsort(p, n, sizeof(vi), vi_cmp);
        for (int64_t i = 0; i < n; i++) x[i] = c->args ? (double)p[i].i : p[i].v;
        store(out, q, n, out->strides[nd - 1], x);
    }
    free(x); free(p);
}
int pn_sort(pn_arr *out, pn_arr *a, int args) {
    int nd = a->ndim; int64_t n = a->shape[nd - 1], rows = 1;
    for (int d = 0; d < nd - 1; d++) rows *= a->shape[d];
    sort_ctx c = {out, a, args, n};
    par_range(rows, rows * n * 8, sort_rows, &c);
    return 0;
}

// ---- correlate, numpy's "full": out[k] = sum_i a[i] * v[i - k + nv - 1], k = 0 .. na + nv - 2
int pn_correlate(double *out, const double *a, int64_t na, const double *v, int64_t nv) {
    for (int64_t k = 0; k < na + nv - 1; k++) {
        double s = 0;
        int64_t lo = k - (nv - 1) > 0 ? k - (nv - 1) : 0, hi = k < na - 1 ? k : na - 1;
        for (int64_t i = lo; i <= hi; i++) s += a[i] * v[i - k + nv - 1];
        out[k] = s;
    }
    return 0;
}

// ---- polynomial roots (coefficients highest first): Aberth's way (all roots at once, each pushed away from the others), then
// Newton's polish. Leading zeros dropped; trailing zeros are roots at 0 (as numpy). Returns how many roots, into re/im.
#include <complex.h>
static double complex horner(const double *c, int n, double complex z, double complex *dz) {   // p(z), and p'(z)
    double complex p = c[0], d = 0;
    for (int i = 1; i <= n; i++) { d = d * z + p; p = p * z + c[i]; }
    *dz = d; return p;
}
int pn_roots(const double *coef, int len, double *re, double *im) {
    int s = 0; while (s < len && coef[s] == 0.0) s++;              // leading zeros: not part of the polynomial
    int e = len - 1, zeros = 0; while (e > s && coef[e] == 0.0) { e--; zeros++; }   // trailing zeros: roots at 0
    int n = e - s;                                                   // degree left
    const double *c = coef + s;
    int out = 0;
    if (n >= 1) {
        double complex z[256]; if (n > 256) return -1;
        double r = 0;                                                // start on a circle of the roots' typical size
        for (int i = 1; i <= n; i++) { double t = pow(fabs(c[i] / c[0]), 1.0 / i); if (t > r) r = t; }
        if (r == 0) r = 1;
        for (int i = 0; i < n; i++) z[i] = r * cexp(I * (2 * M_PI * i / n + 0.4));
        for (int it = 0; it < 500; it++) {
            double moved = 0;
            for (int i = 0; i < n; i++) {
                double complex d, p = horner(c, n, z[i], &d);
                if (cabs(p) == 0) continue;
                double complex ratio = p / d, sum = 0;
                for (int j = 0; j < n; j++) if (j != i) sum += 1.0 / (z[i] - z[j]);
                double complex w = ratio / (1.0 - ratio * sum);
                z[i] -= w; moved = fmax(moved, cabs(w) / fmax(1.0, cabs(z[i])));
            }
            if (moved < 1e-15) break;
        }
        for (int i = 0; i < n; i++) for (int k = 0; k < 3; k++) {    // Newton's polish
            double complex d, p = horner(c, n, z[i], &d);
            if (cabs(d) > 0) z[i] -= p / d;
        }
        for (int i = 0; i < n; i++) { re[out] = creal(z[i]); im[out] = cimag(z[i]); out++; }
    }
    for (int i = 0; i < zeros; i++) { re[out] = 0; im[out] = 0; out++; }
    return out;
}

// ---- least squares: x minimising |A x - b| for A (m, n), m >= n, by Householder QR (doubles; A and b are overwritten)
int pn_lstsq(double *A, double *b, int64_t m, int64_t n, double *x) {
    for (int64_t j = 0; j < n; j++) {
        double norm = 0; for (int64_t i = j; i < m; i++) norm += A[i * n + j] * A[i * n + j];
        norm = sqrt(norm);
        if (norm == 0) continue;
        double alpha = A[j * n + j] > 0 ? -norm : norm;
        double v0 = A[j * n + j] - alpha;
        double vn = v0 * v0; for (int64_t i = j + 1; i < m; i++) vn += A[i * n + j] * A[i * n + j];
        if (vn == 0) continue;
        for (int64_t k = j; k < n; k++) {                            // apply H = I - 2 v v^T / (v^T v) to A's columns
            double s = v0 * A[j * n + k]; for (int64_t i = j + 1; i < m; i++) s += A[i * n + j] * A[i * n + k];
            s = 2 * s / vn;
            A[j * n + k] -= s * v0; for (int64_t i = j + 1; i < m; i++) if (k != j) A[i * n + k] -= s * A[i * n + j];
        }
        double s = v0 * b[j]; for (int64_t i = j + 1; i < m; i++) s += A[i * n + j] * b[i];
        s = 2 * s / vn; b[j] -= s * v0; for (int64_t i = j + 1; i < m; i++) b[i] -= s * A[i * n + j];
        A[j * n + j] = alpha;
    }
    for (int64_t j = n - 1; j >= 0; j--) {                           // back substitution on R
        double s = b[j]; for (int64_t k = j + 1; k < n; k++) s -= A[j * n + k] * x[k];
        x[j] = A[j * n + j] != 0 ? s / A[j * n + j] : 0;
    }
    return 0;
}

// ---- the smallest mean difference between two 2-D grids over small shifts (dy, dx in -r..r): a pan or a walk does not count as
// change (perch_cuts' rule, in one call instead of 49 small ones per frame pair)
double pn_shift_match(pn_arr *a, pn_arr *c, int r) {
    int64_t H = a->shape[0], W = a->shape[1];
    double *A = malloc(sizeof(double) * H * W), *C = malloc(sizeof(double) * H * W);
    for (int64_t y = 0; y < H; y++) { load(a, a->data + y * a->strides[0], W, a->strides[1], A + y * W); load(c, c->data + y * c->strides[0], W, c->strides[1], C + y * W); }
    double best = INFINITY;
    for (int dy = -r; dy <= r; dy++) for (int dx = -r; dx <= r; dx++) {
        int64_t y0 = dy > 0 ? dy : 0, y1 = H + (dy < 0 ? dy : 0), x0 = dx > 0 ? dx : 0, x1 = W + (dx < 0 ? dx : 0);
        double s = 0; int64_t cnt = 0;
        for (int64_t y = y0; y < y1; y++) for (int64_t x = x0; x < x1; x++) { s += fabs(A[y * W + x] - C[(y - dy) * W + (x - dx)]); cnt++; }
        if (cnt) { double m = s / (double)cnt; if (m < best) best = m; }
    }
    free(A); free(C);
    return best;
}

// ---- whole rows picked: a seen as (rows, rest); rows[] lists which (from a mask over the leading axes, or an index list);
// each picked row copied whole into out (count, rest). The rows split over the cores.
typedef struct { pn_arr *out, *a; const int64_t *rows; } tr_ctx;
static void take_rows_range(int64_t i0, int64_t i1, void *c_) {
    tr_ctx *c = c_;
    int64_t rest = c->a->shape[1];
    double *buf = malloc(sizeof(double) * (rest ? rest : 1));
    for (int64_t i = i0; i < i1; i++) {
        load(c->a, c->a->data + c->rows[i] * c->a->strides[0], rest, c->a->strides[1], buf);
        store(c->out, c->out->data + i * c->out->strides[0], rest, c->out->strides[1], buf);
    }
    free(buf);
}
int pn_take_rows(pn_arr *out, pn_arr *a, const int64_t *rows, int64_t n) {
    tr_ctx c = {out, a, rows};
    par_range(n, n * a->shape[1], take_rows_range, &c);
    return 0;
}

// ---- cumsum along the last axis (perch_murmur moves the asked axis there first): running totals in doubles, rows over cores
typedef struct { pn_arr *out, *a; int64_t n; } cs_ctx;
static void cumsum_rows(int64_t r0, int64_t r1, void *c_) {
    cs_ctx *c = c_;
    pn_arr *a = c->a, *out = c->out;
    int nd = a->ndim; int64_t n = c->n;
    double *x = malloc(sizeof(double) * (n ? n : 1));
    for (int64_t r = r0; r < r1; r++) {
        const char *p = a->data; char *q = out->data;
        int64_t rr = r;
        for (int d = nd - 2; d >= 0; d--) { int64_t i = rr % a->shape[d]; rr /= a->shape[d]; p += i * a->strides[d]; q += i * out->strides[d]; }
        load(a, p, n, a->strides[nd - 1], x);
        double s = 0, comp = 0;
        for (int64_t i = 0; i < n; i++) { double y = x[i] - comp, t = s + y; comp = (t - s) - y; s = t; x[i] = s; }   // Kahan: long runs stay exact
        store(out, q, n, out->strides[nd - 1], x);
    }
    free(x);
}
int pn_cumsum(pn_arr *out, pn_arr *a) {
    int nd = a->ndim; int64_t n = a->shape[nd - 1], rows = 1;
    for (int d = 0; d < nd - 1; d++) rows *= a->shape[d];
    cs_ctx c = {out, a, n};
    par_range(rows, rows * n, cumsum_rows, &c);
    return 0;
}

// ---- murmur's random numbers in C: xoshiro256** (the same stream perch_murmur's Python side makes), state carried in and out.
// kind 0: uniform in [0, 1) (53 bits); kind 1: normal (mean 0, spread 1) by Marsaglia's polar method (pairs; none wasted).
static inline uint64_t rotl(uint64_t x, int k) { return (x << k) | (x >> (64 - k)); }
static inline uint64_t xnext(uint64_t *s) {
    uint64_t r = rotl(s[1] * 5, 7) * 9, t = s[1] << 17;
    s[2] ^= s[0]; s[3] ^= s[1]; s[1] ^= s[2]; s[0] ^= s[3]; s[2] ^= t; s[3] = rotl(s[3], 45);
    return r;
}
static inline double xuni(uint64_t *s) { return (double)(xnext(s) >> 11) * (1.0 / 9007199254740992.0); }
int pn_rng_fill(uint64_t *state, double *out, int64_t n, int kind) {
    if (kind == 0) { for (int64_t i = 0; i < n; i++) out[i] = xuni(state); return 0; }
    for (int64_t i = 0; i < n; i += 2) {
        double u, v, q;
        do { u = 2 * xuni(state) - 1; v = 2 * xuni(state) - 1; q = u * u + v * v; } while (q >= 1.0 || q == 0.0);
        double f = sqrt(-2.0 * log(q) / q);
        out[i] = u * f; if (i + 1 < n) out[i + 1] = v * f;
    }
    return 0;
}
