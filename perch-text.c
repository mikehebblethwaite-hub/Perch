/* Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md.
 * perch-text: the words an app draws, caught where they are drawn, not read back from pixels (libperch-text.so).
 * Loaded into apps on Perch's display by perch-run (LD_PRELOAD). For apps that give no words of their own (custom-drawn
 * widgets, drawing areas, apps with no accessibility): the text exists as text right up to the drawing call, so it is taken
 * there, exactly, with its place.
 *   HarfBuzz  hb_shape / hb_shape_full: text in, glyph numbers out -> which run of glyphs means which text
 *   Cairo     cairo_show_glyphs: a run of glyphs drawn at a place -> the text (matched by its glyphs) and its box
 *             cairo_show_text / cairo_show_text_glyphs: the text itself
 *             cairo_set_source_surface: an off-screen surface copied onto another carries its words along (GTK paints
 *             off-screen, then copies to the window); cairo_paint / cairo_fill: what they cover is drawn over, so the words
 *             under them go
 * Out: one small file per window, $XDG_RUNTIME_DIR/perch-text/<display>/<window>: "x,y wxh text" in window pixels, written
 * when a paint of that window ends. perch-look turns them into D lines (drawn words) for windows that give no words.
 * Builds with no Cairo/HarfBuzz/xcb headers: their few calls are declared here and found at run time; an app without them is
 * left exactly as it was. Nothing leaves the machine; nothing is written but those files.
 * Build: make (libperch-text.so)    Use: perch-run APP [ARGS]  (or LD_PRELOAD=.../libperch-text.so)
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

typedef struct _cairo cairo_t;
typedef struct _cairo_surface cairo_surface_t;
typedef struct { unsigned long index; double x, y; } cairo_glyph_t;
typedef struct { double x_bearing, y_bearing, width, height, x_advance, y_advance; } cairo_text_extents_t;
typedef struct hb_font_t hb_font_t;
typedef struct hb_buffer_t hb_buffer_t;
typedef struct { uint32_t codepoint, mask, cluster, var1, var2; } hb_glyph_info_t;
enum { SURFACE_IMAGE = 0, SURFACE_XLIB = 3 };

#define MAXW 64            /* words kept per surface */
#define MAXS 256           /* surfaces with words */
#define RUNS 4096          /* glyph runs remembered (a ring) */
#define TXT 200

typedef struct { double x, y, w, h; char t[TXT]; } word_t;
typedef struct { cairo_surface_t *s; int n, dirty; word_t w[MAXW]; } surf_t;
typedef struct { uint64_t key; char t[TXT]; } run_t;

static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
static surf_t surfs[MAXS];
static run_t runs[RUNS];
static __thread int inside;          /* our own calls into Cairo are not caught again */

#define REAL(ret, name, args) static ret (*real_##name) args; if (!real_##name) real_##name = dlsym(RTLD_NEXT, #name)
#define HELP(ret, name, args) static ret (*name##_) args; if (!name##_) name##_ = dlsym(RTLD_DEFAULT, #name)

/* ---------- which glyph run means which text (HarfBuzz) ---------- */
static uint64_t mix(uint64_t h, uint64_t v) { h ^= v + 0x9e3779b97f4a7c15ULL + (h << 6) + (h >> 2); return h * 0x100000001b3ULL; }

static int utf8(uint32_t c, char *o) {
    if (c < 0x80) { o[0] = (char)c; return 1; }
    if (c < 0x800) { o[0] = (char)(0xc0 | c >> 6); o[1] = (char)(0x80 | (c & 63)); return 2; }
    if (c < 0x10000) { o[0] = (char)(0xe0 | c >> 12); o[1] = (char)(0x80 | (c >> 6 & 63)); o[2] = (char)(0x80 | (c & 63)); return 3; }
    o[0] = (char)(0xf0 | c >> 18); o[1] = (char)(0x80 | (c >> 12 & 63)); o[2] = (char)(0x80 | (c >> 6 & 63)); o[3] = (char)(0x80 | (c & 63)); return 4;
}

static void before_shape(hb_buffer_t *b, char *text) {
    HELP(hb_glyph_info_t *, hb_buffer_get_glyph_infos, (hb_buffer_t *, unsigned *));
    text[0] = 0; if (!hb_buffer_get_glyph_infos_) return;
    unsigned n = 0; hb_glyph_info_t *in = hb_buffer_get_glyph_infos_(b, &n); int k = 0;
    for (unsigned i = 0; in && i < n && k < TXT - 5; i++) {
        uint32_t c = in[i].codepoint;
        if (c < 0x20 || (c >= 0x7f && c < 0xa0) || c > 0x10ffff) c = ' ';
        k += utf8(c, text + k);
    }
    text[k] = 0;
}

static uint64_t glyph_key(const uint32_t *g, size_t step, unsigned n) {
    uint64_t h = 1469598103934665603ULL; unsigned m = 0;
    for (unsigned i = 0; i < n; i++) { uint32_t v = *(const uint32_t *)((const char *)g + i * step); if (v & 0x10000000U) continue; h = mix(h, v); m++; }
    return m ? mix(h, m) : 0;        /* empty glyphs (Pango's 0x0FFFFFFF and the like) are not drawn: left out both ways */
}

static void after_shape(hb_buffer_t *b, const char *text) {
    HELP(hb_glyph_info_t *, hb_buffer_get_glyph_infos, (hb_buffer_t *, unsigned *));
    if (!text[0] || !hb_buffer_get_glyph_infos_) return;
    unsigned n = 0; hb_glyph_info_t *out = hb_buffer_get_glyph_infos_(b, &n);
    uint64_t k = out ? glyph_key(&out[0].codepoint, sizeof *out, n) : 0; if (!k) return;
    pthread_mutex_lock(&mu);
    run_t *r = &runs[k % RUNS]; r->key = k; snprintf(r->t, TXT, "%s", text);
    pthread_mutex_unlock(&mu);
}

int hb_shape_full(hb_font_t *f, hb_buffer_t *b, const void *feat, unsigned nf, const char *const *shapers) {
    REAL(int, hb_shape_full, (hb_font_t *, hb_buffer_t *, const void *, unsigned, const char *const *));
    char text[TXT]; before_shape(b, text);
    int r = real_hb_shape_full(f, b, feat, nf, shapers);
    after_shape(b, text); return r;
}

void hb_shape(hb_font_t *f, hb_buffer_t *b, const void *feat, unsigned nf) {
    REAL(void, hb_shape, (hb_font_t *, hb_buffer_t *, const void *, unsigned));
    char text[TXT]; before_shape(b, text);
    real_hb_shape(f, b, feat, nf);
    after_shape(b, text);
}

/* ---------- words on surfaces ---------- */
static surf_t *surf(cairo_surface_t *s, int make) {
    surf_t *free_ = NULL;
    for (int i = 0; i < MAXS; i++) { if (surfs[i].s == s) return &surfs[i]; if (!surfs[i].s && !free_) free_ = &surfs[i]; }
    if (!make) return NULL;
    if (!free_) free_ = &surfs[(uintptr_t)s / 16 % MAXS];        /* full: the oldest guess goes */
    memset(free_, 0, sizeof *free_); free_->s = s; return free_;
}

static double over(const word_t *w, double x, double y, double ww, double hh) {
    double ix = (w->x + w->w < x + ww ? w->x + w->w : x + ww) - (w->x > x ? w->x : x);
    double iy = (w->y + w->h < y + hh ? w->y + w->h : y + hh) - (w->y > y ? w->y : y);
    return ix > 0 && iy > 0 ? ix * iy / (w->w * w->h > 1 ? w->w * w->h : 1) : 0;
}

static void clear_box(surf_t *S, double x, double y, double w, double h) {     /* drawn over: the words mostly under it go */
    int j = 0;
    for (int i = 0; i < S->n; i++) if (over(&S->w[i], x, y, w, h) < 0.5) S->w[j++] = S->w[i];
    if (j != S->n) { S->n = j; S->dirty = 1; }
}

static void add(cairo_surface_t *s, double x, double y, double w, double h, const char *t) {
    if (!t || !t[0] || w < 1 || h < 1) return;
    surf_t *S = surf(s, 1); clear_box(S, x, y, w, h);
    if (S->n == MAXW) { memmove(S->w, S->w + 1, sizeof(word_t) * (MAXW - 1)); S->n--; }
    word_t *W = &S->w[S->n++]; W->x = x; W->y = y; W->w = w; W->h = h; snprintf(W->t, TXT, "%s", t); S->dirty = 1;
}

/* the box of a drawing in user space -> device (surface) pixels */
static void to_device(cairo_t *cr, double *x, double *y, double *w, double *h) {
    HELP(void, cairo_user_to_device, (cairo_t *, double *, double *));
    double x0 = *x, y0 = *y, x1 = *x + *w, y1 = *y + *h;
    if (cairo_user_to_device_) { cairo_user_to_device_(cr, &x0, &y0); cairo_user_to_device_(cr, &x1, &y1); }
    *x = x0 < x1 ? x0 : x1; *y = y0 < y1 ? y0 : y1; *w = x1 > x0 ? x1 - x0 : x0 - x1; *h = y1 > y0 ? y1 - y0 : y0 - y1;
}

/* ---------- is a surface a window? (its own xcb line: an answer, never an X error in the app) ---------- */
typedef struct { unsigned sequence; } xcb_cookie_t;
static void *xc; static int xc_tried;
static unsigned long known_win[256]; static int known_is[256];

static int is_window(unsigned long id) {
    for (int i = 0; i < 256; i++) if (known_win[i] == id) return known_is[i];
    static void *(*conn)(const char *, int *); static xcb_cookie_t (*ask)(void *, uint32_t);
    static void *(*reply)(void *, xcb_cookie_t, void **);
    if (!xc_tried) {
        xc_tried = 1;
        void *lib = dlopen("libxcb.so.1", RTLD_LAZY | RTLD_LOCAL);
        if (lib) { conn = dlsym(lib, "xcb_connect"); ask = dlsym(lib, "xcb_get_window_attributes"); reply = dlsym(lib, "xcb_get_window_attributes_reply"); }
        if (conn && ask && reply) xc = conn(NULL, NULL);
    }
    int yes = 0;
    if (xc) { void *err = NULL; void *r = reply(xc, ask(xc, (uint32_t)id), &err); yes = r != NULL; free(r); free(err); }
    int slot = (int)(id % 256); known_win[slot] = id; known_is[slot] = yes; return yes;
}

static unsigned long window_of(cairo_surface_t *s) {
    HELP(int, cairo_surface_get_type, (cairo_surface_t *));
    HELP(unsigned long, cairo_xlib_surface_get_drawable, (cairo_surface_t *));
    if (!cairo_surface_get_type_ || !cairo_xlib_surface_get_drawable_ || cairo_surface_get_type_(s) != SURFACE_XLIB) return 0;
    unsigned long d = cairo_xlib_surface_get_drawable_(s);
    return d && is_window(d) ? d : 0;
}

static void write_window(surf_t *S) {
    unsigned long win = window_of(S->s); S->dirty = 0; if (!win) return;
    const char *rt = getenv("XDG_RUNTIME_DIR"), *disp = getenv("DISPLAY");
    if (!rt || !disp) return;
    const char *colon = strchr(disp, ':'); if (!colon) return;
    char dir[512], path[600], tmp[620]; int dn = atoi(colon + 1);
    snprintf(dir, sizeof dir, "%s/perch-text", rt); mkdir(dir, 0700);
    snprintf(dir, sizeof dir, "%s/perch-text/%d", rt, dn); mkdir(dir, 0700);
    snprintf(path, sizeof path, "%s/0x%lx", dir, win); snprintf(tmp, sizeof tmp, "%s.%d", path, (int)getpid());
    mode_t old = umask(077); FILE *f = fopen(tmp, "w"); umask(old); if (!f) return;      /* screen words: this user only */
    fprintf(f, "# pid %d\n", (int)getpid());
    for (int i = 0; i < S->n; i++) {
        word_t *W = &S->w[i];
        fprintf(f, "%d,%d %dx%d ", (int)W->x, (int)W->y, (int)(W->w + 0.5), (int)(W->h + 0.5));
        for (const char *c = W->t; *c; c++) { if (*c == '\\') fputs("\\\\", f); else if (*c == '\n') fputs("\\n", f); else fputc(*c, f); }
        fputc('\n', f);
    }
    fclose(f); rename(tmp, path);
}

/* ---------- Cairo: drawing ---------- */
void cairo_show_glyphs(cairo_t *cr, const cairo_glyph_t *g, int n) {
    REAL(void, cairo_show_glyphs, (cairo_t *, const cairo_glyph_t *, int));
    if (!inside && g && n > 0) {
        inside = 1;
        HELP(cairo_surface_t *, cairo_get_target, (cairo_t *));
        HELP(void, cairo_glyph_extents, (cairo_t *, const cairo_glyph_t *, int, cairo_text_extents_t *));
        uint32_t ids[512]; int m = n < 512 ? n : 512;
        for (int i = 0; i < m; i++) ids[i] = (uint32_t)g[i].index;
        uint64_t k = glyph_key(ids, sizeof ids[0], (unsigned)m);
        if (k && cairo_get_target_ && cairo_glyph_extents_) {
            pthread_mutex_lock(&mu);
            run_t *r = &runs[k % RUNS];
            if (r->key == k) {
                cairo_text_extents_t e; cairo_glyph_extents_(cr, g, n, &e);
                double x = g[0].x + e.x_bearing, y = g[0].y + e.y_bearing, w = e.width, h = e.height;
                to_device(cr, &x, &y, &w, &h); add(cairo_get_target_(cr), x, y, w, h, r->t);
            }
            pthread_mutex_unlock(&mu);
        }
        inside = 0;
    }
    real_cairo_show_glyphs(cr, g, n);
}

static void text_at(cairo_t *cr, const char *t) {
    HELP(cairo_surface_t *, cairo_get_target, (cairo_t *));
    HELP(void, cairo_text_extents, (cairo_t *, const char *, cairo_text_extents_t *));
    HELP(void, cairo_get_current_point, (cairo_t *, double *, double *));
    if (!cairo_get_target_ || !cairo_text_extents_ || !cairo_get_current_point_) return;
    cairo_text_extents_t e; double x, y; cairo_text_extents_(cr, t, &e); cairo_get_current_point_(cr, &x, &y);
    x += e.x_bearing; y += e.y_bearing; double w = e.width, h = e.height;
    to_device(cr, &x, &y, &w, &h);
    char c[TXT]; snprintf(c, TXT, "%s", t);
    pthread_mutex_lock(&mu); add(cairo_get_target_(cr), x, y, w, h, c); pthread_mutex_unlock(&mu);
}

void cairo_show_text(cairo_t *cr, const char *t) {
    REAL(void, cairo_show_text, (cairo_t *, const char *));
    if (!inside && t && t[0]) { inside = 1; text_at(cr, t); inside = 0; }
    real_cairo_show_text(cr, t);
}

void cairo_show_text_glyphs(cairo_t *cr, const char *t, int len, const cairo_glyph_t *g, int n, const void *cl, int ncl, int flags) {
    REAL(void, cairo_show_text_glyphs, (cairo_t *, const char *, int, const cairo_glyph_t *, int, const void *, int, int));
    if (!inside && t && g && n > 0) {
        inside = 1;
        HELP(cairo_surface_t *, cairo_get_target, (cairo_t *));
        HELP(void, cairo_glyph_extents, (cairo_t *, const cairo_glyph_t *, int, cairo_text_extents_t *));
        if (cairo_get_target_ && cairo_glyph_extents_) {
            char c[TXT]; int l = len < 0 ? (int)strlen(t) : len; snprintf(c, TXT, "%.*s", l < TXT - 1 ? l : TXT - 1, t);
            cairo_text_extents_t e; cairo_glyph_extents_(cr, g, n, &e);
            double x = g[0].x + e.x_bearing, y = g[0].y + e.y_bearing, w = e.width, h = e.height;
            to_device(cr, &x, &y, &w, &h);
            pthread_mutex_lock(&mu); add(cairo_get_target_(cr), x, y, w, h, c); pthread_mutex_unlock(&mu);
        }
        inside = 0;
    }
    real_cairo_show_text_glyphs(cr, t, len, g, n, cl, ncl, flags);
}

/* ---------- Cairo: drawn over, copied, finished ---------- */
static void covered(cairo_t *cr, int clip) {
    HELP(cairo_surface_t *, cairo_get_target, (cairo_t *));
    HELP(void, cairo_clip_extents, (cairo_t *, double *, double *, double *, double *));
    HELP(void, cairo_fill_extents, (cairo_t *, double *, double *, double *, double *));
    if (!cairo_get_target_ || !cairo_clip_extents_ || (!clip && !cairo_fill_extents_)) return;
    pthread_mutex_lock(&mu);
    surf_t *S = surf(cairo_get_target_(cr), 0);
    if (S && S->n) {
        /* only what is really drawn: the shape inside the clip (a blinking cursor repaints a line's highlight through a
           clip the size of the cursor: the line's words stay) */
        double x0, y0, x1, y1; cairo_clip_extents_(cr, &x0, &y0, &x1, &y1);
        if (!clip) {
            double a0, b0, a1, b1; cairo_fill_extents_(cr, &a0, &b0, &a1, &b1);
            x0 = a0 > x0 ? a0 : x0; y0 = b0 > y0 ? b0 : y0; x1 = a1 < x1 ? a1 : x1; y1 = b1 < y1 ? b1 : y1;
        }
        if (x1 > x0 && y1 > y0) { double w = x1 - x0, h = y1 - y0; to_device(cr, &x0, &y0, &w, &h); clear_box(S, x0, y0, w, h); }
    }
    pthread_mutex_unlock(&mu);
}

/* the source a later paint copies from: its words go along (once the paint happens) */
static __thread cairo_surface_t *src_s; static __thread double src_x, src_y;

void cairo_set_source_surface(cairo_t *cr, cairo_surface_t *s, double x, double y) {
    REAL(void, cairo_set_source_surface, (cairo_t *, cairo_surface_t *, double, double));
    src_s = s; src_x = x; src_y = y;
    real_cairo_set_source_surface(cr, s, x, y);
}

static void carry(cairo_t *cr) {
    HELP(cairo_surface_t *, cairo_get_target, (cairo_t *));
    HELP(cairo_surface_t *, cairo_get_source, (cairo_t *));
    HELP(int, cairo_pattern_get_surface, (void *, cairo_surface_t **));
    if (!src_s || !cairo_get_target_ || !cairo_get_source_ || !cairo_pattern_get_surface_) return;
    cairo_surface_t *now = NULL;
    if (cairo_pattern_get_surface_(cairo_get_source_(cr), &now) != 0 || now != src_s) return;   /* the source changed since */
    pthread_mutex_lock(&mu);
    surf_t *S = surf(src_s, 0); cairo_surface_t *t = cairo_get_target_(cr);
    if (S && S->n && t != src_s) {
        word_t copy[MAXW]; int n = S->n; memcpy(copy, S->w, sizeof(word_t) * n);
        for (int i = 0; i < n; i++) {
            double x = copy[i].x + src_x, y = copy[i].y + src_y, w = copy[i].w, h = copy[i].h;
            to_device(cr, &x, &y, &w, &h); add(t, x, y, w, h, copy[i].t);
        }
    }
    pthread_mutex_unlock(&mu);
}

void cairo_paint(cairo_t *cr) {
    REAL(void, cairo_paint, (cairo_t *));
    if (!inside) { inside = 1; covered(cr, 1); carry(cr); inside = 0; }
    real_cairo_paint(cr);
}

void cairo_paint_with_alpha(cairo_t *cr, double a) {
    REAL(void, cairo_paint_with_alpha, (cairo_t *, double));
    if (!inside && a > 0.99) { inside = 1; covered(cr, 1); carry(cr); inside = 0; }
    real_cairo_paint_with_alpha(cr, a);
}

void cairo_fill(cairo_t *cr) {
    REAL(void, cairo_fill, (cairo_t *));
    if (!inside) { inside = 1; covered(cr, 0); carry(cr); inside = 0; }
    real_cairo_fill(cr);
}

void cairo_destroy(cairo_t *cr) {
    REAL(void, cairo_destroy, (cairo_t *));
    if (!inside) {
        inside = 1;
        HELP(cairo_surface_t *, cairo_get_target, (cairo_t *));
        if (cairo_get_target_) {
            pthread_mutex_lock(&mu);
            surf_t *S = surf(cairo_get_target_(cr), 0);
            if (S && S->dirty) write_window(S);
            pthread_mutex_unlock(&mu);
        }
        inside = 0;
    }
    real_cairo_destroy(cr);
}

void cairo_surface_destroy(cairo_surface_t *s) {
    REAL(void, cairo_surface_destroy, (cairo_surface_t *));
    HELP(unsigned, cairo_surface_get_reference_count, (cairo_surface_t *));
    if (s && cairo_surface_get_reference_count_ && cairo_surface_get_reference_count_(s) == 1) {   /* about to be freed */
        pthread_mutex_lock(&mu); surf_t *S = surf(s, 0); if (S) memset(S, 0, sizeof *S); pthread_mutex_unlock(&mu);
    }
    real_cairo_surface_destroy(s);
}
