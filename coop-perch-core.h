/* Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md. */
/* coop-perch-core.h: Perch's own way of seeing, shared by every eye (desktop X: coop-perch-eye, virtual machine: coop-perch-vm).
 *
 * An eye only has to say WHERE the screen changed (perch_damage) and keep `cur` holding the screen's pixels (x8r8g8b8 rows of
 * cur_stride bytes) for those places. The core does the rest:
 *   - each grid cell (default 16x10) keeps its raw vector (mean R,G,B,luma) up to date by swapping a changed box's old colours
 *     for its new ones (the whole cell is never re-read);
 *   - the tight box that really changed (a row memcmp), then a strike, then the zoom (quadtree) that pins it to a small box;
 *   - habituation: a cell on a steady beat (clock, cursor, spinner) is tuned out; a break in its beat counts again; nonstop = animated;
 *   - out: the plane in the box of bot vectors (u16 rows x cols x 9), vectors.bin (raw floats), tape.txt (one short line per
 *     strike frame), stats.txt (CPU, tokens: text tape vs screenshots), now.json (usage page live panel).
 * One process, one screen: the pixel state is file-level (static) so the hot loops stay plain.
 */
#ifndef COOP_PERCH_CORE_H
#define COOP_PERCH_CORE_H
#include "coop-perch-box.h"
#include <math.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <time.h>

#define VEC 4                     /* numbers per cell: R, G, B, luma (0..255) */
#define ZOOM_MIN 6                /* the quadtree stops at boxes this small (pixels) */
#define ZOOM_DEPTH 6
#define FRAME_MS 100              /* the eye looks at most 10 times a second; damage between looks is merged */
#define HABIT_WINDOW 30           /* seconds of strike history per cell used to learn its beat */
#define HABIT_HITS 8              /* this many strikes on a steady beat inside the window = a habit (tuned out) */
#define STATS_EVERY 10            /* seconds */
#define SHOT_TOKENS(w, h) (((double)(w) * (double)(h)) / 750.0)   /* what one image costs the bot (w*h/750, Claude's rule) */

typedef struct {
    float v[VEC];                 /* the cell's last vector */
    double sum[3];                /* running R,G,B sums over the whole cell: only the changed box is re-read and swapped in */
    int dx0, dy0, dx1, dy1;       /* the changed box inside this cell since the last look */
    int have;                     /* v holds a real look */
    double hits[HABIT_HITS * 2];  /* recent strike times (ring) */
    int nhits, hpos;
    int habit;                    /* tuned out */
    double beat;                  /* learned seconds between strikes when habit */
    double last;                  /* last strike time */
    double streak, lastc;         /* changing nonstop since; last change (animation: over 2 s without a 1 s break) */
} Cell;

static double perch_now(void) {
    struct timespec t;
    clock_gettime(CLOCK_REALTIME, &t);
    return (double)t.tv_sec + (double)t.tv_nsec / 1e9;
}
static double perch_cpu(void) {
    struct rusage r;
    if (getrusage(RUSAGE_SELF, &r) != 0) return 0.0;
    return (double)r.ru_utime.tv_sec + r.ru_utime.tv_usec / 1e6 + (double)r.ru_stime.tv_sec + r.ru_stime.tv_usec / 1e6;
}
static void clock_txt(double t, char *out, size_t n) {
    time_t s = (time_t)t;
    struct tm tm;
    localtime_r(&s, &tm);
    strftime(out, n, "%H:%M:%S", &tm);
}

/* ---- the screen: current pixels (only changed areas need to be fresh) and the pixels at the last look ---- */
static int W = 0, H = 0;
static uint8_t *cur = NULL;       /* x8r8g8b8, rows of cur_stride bytes; the eye fills it (or points it at its own image) */
static size_t cur_stride = 0;
static uint32_t *prev = NULL;
static inline uint32_t px(int x, int y) { return ((const uint32_t *)(cur + (size_t)y * cur_stride))[x]; }
static inline uint32_t ppx(int x, int y) { return prev[(size_t)y * W + x]; }

/* add (sign +1) or take away (sign -1) the colours of a box, from the fresh pixels (old = 0) or the last look's (old = 1) */
static void box_sum(int x0, int y0, int w, int h, int old, double sign, double *sum) {
    double r = 0, g = 0, b = 0;
    for (int y = y0; y < y0 + h; y++)
        for (int x = x0; x < x0 + w; x++) {
            uint32_t p = old ? ppx(x, y) : px(x, y);
            r += (p >> 16) & 255; g += (p >> 8) & 255; b += p & 255;
        }
    sum[0] += sign * r; sum[1] += sign * g; sum[2] += sign * b;
}
static void sum_vector(const double *sum, double n, float *v) {
    v[0] = (float)(sum[0] / n); v[1] = (float)(sum[1] / n); v[2] = (float)(sum[2] / n);
    v[3] = (float)(0.299 * v[0] + 0.587 * v[1] + 0.114 * v[2]);
}
static int box_differs(int x0, int y0, int w, int h) {
    for (int y = y0; y < y0 + h; y++)
        for (int x = x0; x < x0 + w; x++)
            if ((px(x, y) & 0xffffff) != (ppx(x, y) & 0xffffff)) return 1;
    return 0;
}

/* the tight box that really changed inside a box: memcmp each row (fast), then find the first and last changed pixel only in rows
   that differ. GPU apps (browsers, Electron) damage their whole window every frame, so this, not the damage, finds where. */
static int tight_box(int x, int y, int w, int h, int *tx0, int *ty0, int *tx1, int *ty1) {
    int found = 0;
    for (int yy = y; yy < y + h; yy++) {
        const uint32_t *a = (const uint32_t *)(cur + (size_t)yy * cur_stride) + x, *b = prev + (size_t)yy * W + x;
        if (!memcmp(a, b, (size_t)w * 4)) continue;
        int l = 0, r = w - 1;
        while (l < w && ((a[l] ^ b[l]) & 0xffffff) == 0) l++;
        if (l == w) continue;                                       /* only the unused top byte differed */
        while (r > l && ((a[r] ^ b[r]) & 0xffffff) == 0) r--;
        if (!found) { *tx0 = x + l; *tx1 = x + r + 1; *ty0 = yy; found = 1; }
        if (x + l < *tx0) *tx0 = x + l;
        if (x + r + 1 > *tx1) *tx1 = x + r + 1;
        *ty1 = yy + 1;
    }
    return found;
}

/* zoom: quadtree into the box, keep the bounding box of the smallest boxes that changed */
static void zoom(int x, int y, int w, int h, int depth, int *bx0, int *by0, int *bx1, int *by1, int *maxd) {
    if (w <= 0 || h <= 0 || !box_differs(x, y, w, h)) return;
    if (depth >= ZOOM_DEPTH || (w <= ZOOM_MIN && h <= ZOOM_MIN)) {
        if (x < *bx0) *bx0 = x;
        if (y < *by0) *by0 = y;
        if (x + w > *bx1) *bx1 = x + w;
        if (y + h > *by1) *by1 = y + h;
        if (depth > *maxd) *maxd = depth;
        return;
    }
    int hw = w / 2, hh = h / 2;
    zoom(x, y, hw, hh, depth + 1, bx0, by0, bx1, by1, maxd);
    zoom(x + hw, y, w - hw, hh, depth + 1, bx0, by0, bx1, by1, maxd);
    zoom(x, y + hh, hw, h - hh, depth + 1, bx0, by0, bx1, by1, maxd);
    zoom(x + hw, y + hh, w - hw, h - hh, depth + 1, bx0, by0, bx1, by1, maxd);
}

/* habituation: strikes on a steady beat (gaps within 20% of their mean) are a habit; a strike off the beat breaks it */
static int habit_check(Cell *c, double t) {
    int was = c->habit;
    if (c->habit && c->beat > 0) {
        double gap = t - c->last;
        if (fabs(gap - c->beat) > 0.35 * c->beat + 0.15) { c->habit = 0; c->nhits = 0; c->hpos = 0; }   /* beat broken: it counts again */
    }
    c->hits[c->hpos] = t;
    c->hpos = (c->hpos + 1) % (HABIT_HITS * 2);
    if (c->nhits < HABIT_HITS * 2) c->nhits++;
    c->last = t;
    if (!c->habit && c->nhits >= HABIT_HITS) {
        double ts[HABIT_HITS * 2];
        int k = 0;
        for (int i = 0; i < c->nhits; i++) {
            double h = c->hits[(c->hpos - 1 - i + HABIT_HITS * 2) % (HABIT_HITS * 2)];
            if (t - h <= HABIT_WINDOW) ts[k++] = h;
        }
        if (k >= HABIT_HITS) {
            double mean = (ts[0] - ts[k - 1]) / (k - 1);
            int steady = mean > 0.05;
            for (int i = 0; i + 1 < k && steady; i++)
                if (fabs((ts[i] - ts[i + 1]) - mean) > 0.2 * mean + 0.1) steady = 0;
            if (steady) { c->habit = 1; c->beat = mean; }
        }
    }
    return was && c->habit ? 2 : (c->habit ? 3 : (was ? 4 : 0));   /* 2 still a habit, 3 just learned, 4 just broken, 0 counts */
}

/* ---- the grid ---- */
static int GX = 16, GY = 10, cw = 1, ch = 1;
static Cell *cells = NULL;
static unsigned char *dirty = NULL;
#define CX0(c) ((c) * cw)
#define CY0(r) ((r) * ch)
#define CW(c) ((c) == GX - 1 ? W - (c) * cw : cw)
#define CH(r) ((r) == GY - 1 ? H - (r) * ch : ch)

/* a changed screen box: each grid cell it touches grows its own changed box by the part inside it */
static int mark(int x0, int y0, int x1, int y1) {
    if (x0 < 0) x0 = 0;
    if (y0 < 0) y0 = 0;
    if (x1 > W) x1 = W;
    if (y1 > H) y1 = H;
    if (x1 <= x0 || y1 <= y0 || !cells) return 0;
    for (int r = y0 / ch; r <= (y1 - 1) / ch && r < GY; r++)
        for (int c = x0 / cw; c <= (x1 - 1) / cw && c < GX; c++) {
            Cell *k = &cells[r * GX + c];
            int ax0 = x0 > CX0(c) ? x0 : CX0(c), ay0 = y0 > CY0(r) ? y0 : CY0(r);
            int ax1 = x1 < CX0(c) + CW(c) ? x1 : CX0(c) + CW(c), ay1 = y1 < CY0(r) + CH(r) ? y1 : CY0(r) + CH(r);
            if (ax1 <= ax0 || ay1 <= ay0) continue;
            if (!dirty[r * GX + c]) { k->dx0 = ax0; k->dy0 = ay0; k->dx1 = ax1; k->dy1 = ay1; dirty[r * GX + c] = 1; }
            else {
                if (ax0 < k->dx0) k->dx0 = ax0;
                if (ay0 < k->dy0) k->dy0 = ay0;
                if (ax1 > k->dx1) k->dx1 = ax1;
                if (ay1 > k->dy1) k->dy1 = ay1;
            }
        }
    return 1;
}

/* ---- one eye: its outputs and counters ---- */
typedef struct {
    char kind[16], name[64], plane[32];
    char nowpath[1200], nowtmp[1250];
    FILE *tape, *vecs, *stats;
    CoopBox box;
    int have_box;
    int *sbox, *queue;            /* this look: striking cells' zoom boxes (x0 y0 x1 y1) and the region walk */
    unsigned char *smark;
    float *zb;                    /* this look: per cell strike (1 strike, 2 tuned-out change, 3 animated) + zoom box */
    int pending;
    double t0, c0, last_stats, last_look, win_t0, win_c0;
    long ev_total, looks, strikes_total, habit_total, frames_with_strike, tape_bytes, px_read;
    long w_ev, w_looks, w_strikes, w_frames, w_tape, w_px, w_habit;
    char last_line[400];
    /* None Bot Items: changes that exist only for a human eye are dropped here, before anything is written. First: the pointer
     * the guest draws into its own pixels (Windows does), where the eye's own hands just put it (now and the place before) */
    int ptr_x, ptr_y, ptr_px, ptr_py;
    double ptr_t;
    long nonbot_total;
} PerchEye;

/* the hands moved the pointer to x,y (screen pixels) at time t */
static void __attribute__((unused)) perch_pointer(PerchEye *e, int x, int y, double t) {
    e->ptr_px = e->ptr_x; e->ptr_py = e->ptr_y;
    e->ptr_x = x; e->ptr_y = y; e->ptr_t = t;
}
/* a change box that is only the pointer being drawn or taken away: small, at the pointer's place, soon after it moved */
static int perch_nonbot(const PerchEye *e, int x0, int y0, int x1, int y1, double t) {
    if (e->ptr_t <= 0 || t - e->ptr_t > 3.0 || x1 - x0 > 48 || y1 - y0 > 48) return 0;
    for (int k = 0; k < 2; k++) {
        int px = k ? e->ptr_px : e->ptr_x, py = k ? e->ptr_py : e->ptr_y;
        if (x0 < px + 40 && x1 > px - 8 && y0 < py + 40 && y1 > py - 8) return 1;
    }
    return 0;
}

/* open the tape folder; kind = "eye"/"vm", name = display or guest, plane = box plane name. The screen comes later (perch_screen). */
static int perch_open(PerchEye *e, const char *dir, const char *kind, const char *name, const char *plane, int gx, int gy) {
    memset(e, 0, sizeof *e);
    snprintf(e->kind, sizeof e->kind, "%s", kind);
    snprintf(e->name, sizeof e->name, "%s", name);
    snprintf(e->plane, sizeof e->plane, "%s", plane);
    GX = gx; GY = gy;
    char path[1200];
    snprintf(path, sizeof path, "%s", dir);
    for (char *p = path + 1; *p; p++) if (*p == '/') { *p = 0; mkdir(path, 0750); *p = '/'; }
    mkdir(dir, 0750);
    snprintf(path, sizeof path, "%s/tape.txt", dir);
    e->tape = fopen(path, "a");
    snprintf(path, sizeof path, "%s/vectors.bin", dir);
    e->vecs = fopen(path, "ab");
    snprintf(path, sizeof path, "%s/stats.txt", dir);
    e->stats = fopen(path, "a");
    snprintf(e->nowpath, sizeof e->nowpath, "%s/now.json", dir);
    snprintf(e->nowtmp, sizeof e->nowtmp, "%s/now.json.tmp", dir);
    if (!e->tape || !e->vecs || !e->stats) { fprintf(stderr, "coop-perch-%s: cannot write in %s\n", kind, dir); return -1; }
    setvbuf(e->tape, NULL, _IOLBF, 0); setvbuf(e->stats, NULL, _IOLBF, 0);
    e->t0 = e->last_stats = e->win_t0 = perch_now();
    e->c0 = e->win_c0 = perch_cpu();
    char ts[16];
    clock_txt(e->t0, ts, sizeof ts);
    fprintf(e->tape, "# shape: time code fields | S strike col,row@x,y wxh or region col0,row0-col1,row1@x,y wxh .. | H tuned-out col,row beat_s | B beat broken col,row | L line app in/out reqs op:n.. | T text app x,y 'now' 'was' | J joined app | X left app bytes | O open | C closed\n");
    return 0;
}

/* (re)size the screen: grid, buffers, plane. own_cur = 1: the core keeps `cur` itself (the eye writes into it). */
static int perch_screen(PerchEye *e, int w, int h, int own_cur) {
    int ncell = GX * GY;
    if (own_cur) { free(cur); cur = calloc((size_t)w * h, 4); cur_stride = (size_t)w * 4; }
    free(prev); prev = calloc((size_t)w * h, 4);
    free(cells); cells = calloc((size_t)ncell, sizeof(Cell));
    free(dirty); dirty = calloc((size_t)ncell, 1);
    free(e->zb); e->zb = calloc((size_t)ncell * 5, sizeof(float));
    free(e->sbox); e->sbox = calloc((size_t)ncell * 4, sizeof(int));
    free(e->queue); e->queue = calloc((size_t)ncell, sizeof(int));
    free(e->smark); e->smark = calloc((size_t)ncell, 1);
    if ((own_cur && !cur) || !prev || !cells || !dirty || !e->zb || !e->sbox || !e->queue || !e->smark) { fprintf(stderr, "coop-perch-%s: out of memory\n", e->kind); return -1; }
    W = w; H = h;
    cw = W / GX; ch = H / GY;     /* last row/column take the remainder */
    if (cw < 1) cw = 1;
    if (ch < 1) ch = 1;
    if (e->have_box) coop_box_close(&e->box);
    uint32_t bdim[3] = {(uint32_t)GY, (uint32_t)GX, 9};
    char bfields[400];
    snprintf(bfields, sizeof bfields, "R G B luma strike zoom_x zoom_y zoom_w zoom_h; colours 0..255 cell means (rounded); strike 0 none 1 strike "
                                      "2 tuned-out cell changed 3 animated (nonstop over 2 s); zoom box in screen pixels; screen %dx%d %s %s; a frame per look that saw a change",
             W, H, !strcmp(e->kind, "eye") ? "display" : e->kind, e->name);
    e->have_box = coop_box_create(&e->box, e->plane, "u16", 3, bdim, bfields) == 0;
    if (!e->have_box) fprintf(stderr, "coop-perch-%s: no box in %s (going on without it)\n", e->kind, coop_box_dir());
    char ts[16];
    clock_txt(perch_now(), ts, sizeof ts);
    fprintf(e->tape, "%s O %s %s %dx%d %dx%d\n", ts, e->kind, e->name, W, H, GX, GY);
    return 0;
}

/* first look: `cur` holds the whole screen; every cell gets a vector to compare against */
static void perch_first_look(PerchEye *e, double t) {
    (void)t;
    for (int r = 0; r < GY; r++)
        for (int c = 0; c < GX; c++) {
            Cell *k = &cells[r * GX + c];
            k->sum[0] = k->sum[1] = k->sum[2] = 0;
            box_sum(CX0(c), CY0(r), CW(c), CH(r), 0, 1.0, k->sum);
            sum_vector(k->sum, (double)CW(c) * CH(r), k->v);
            k->have = 1; k->dx1 = k->dx0 = 0;
        }
    memset(dirty, 0, (size_t)GX * GY);
    for (int y = 0; y < H; y++) memcpy(prev + (size_t)y * W, cur + (size_t)y * cur_stride, (size_t)W * 4);
    e->pending = 0;
}

/* the eye says where the screen changed */
static void perch_damage(PerchEye *e, int x0, int y0, int x1, int y1) {
    e->ev_total++; e->w_ev++;
    if (mark(x0, y0, x1, y1)) e->pending = 1;
}

/* the look (at most every FRAME_MS): fetch(x,y,w,h) brings those pixels into `cur` first (NULL: they are already there).
   Returns 1 if it looked. */
static int perch_look(PerchEye *e, double t, int (*fetch)(int, int, int, int)) {
    if (!e->pending || (t - e->last_look) * 1000 < FRAME_MS) return 0;
    e->last_look = t; e->pending = 0; e->looks++; e->w_looks++;
    int ncell = GX * GY;
    float *zb = e->zb;
    char line[4000], ts[16];
    int ln = 0, nstrike = 0, nreal = 0;
    memset(zb, 0, (size_t)ncell * 5 * sizeof(float));
    clock_txt(t, ts, sizeof ts);
    /* one read for the whole look: the box around every cell's changed box (one round trip, not one per cell) */
    int ux0 = W, uy0 = H, ux1 = 0, uy1 = 0;
    for (int i = 0; i < ncell; i++)
        if (dirty[i]) {
            Cell *k = &cells[i];
            if (k->dx0 < ux0) ux0 = k->dx0;
            if (k->dy0 < uy0) uy0 = k->dy0;
            if (k->dx1 > ux1) ux1 = k->dx1;
            if (k->dy1 > uy1) uy1 = k->dy1;
        }
    int read_ok = ux1 > ux0 && uy1 > uy0 && (!fetch || fetch(ux0, uy0, ux1 - ux0, uy1 - uy0) == 0);
    if (read_ok) { long a = (long)(ux1 - ux0) * (uy1 - uy0); e->px_read += a; e->w_px += a; }
    for (int i = 0; i < ncell; i++) {
        if (!dirty[i]) continue;
        dirty[i] = 0;
        if (!read_ok) continue;
        int r = i / GX, c = i % GX;
        Cell *k = &cells[i];
        int x, y, x1, y1;
        if (!tight_box(k->dx0, k->dy0, k->dx1 - k->dx0, k->dy1 - k->dy0, &x, &y, &x1, &y1)) continue;   /* same pixels: nothing to see */
        int w = x1 - x, h = y1 - y;
        box_sum(x, y, w, h, 1, -1.0, k->sum);
        box_sum(x, y, w, h, 0, 1.0, k->sum);
        float v[VEC];
        sum_vector(k->sum, (double)CW(c) * CH(r), v);
        int bx0 = W, by0 = H, bx1 = 0, by1 = 0, md = 0;
        zoom(x, y, w, h, 0, &bx0, &by0, &bx1, &by1, &md);               /* pixels did change in the box: a strike */
        int hab = habit_check(k, t);
        nreal++;
        if (t - k->lastc > 1.0) k->streak = t;
        k->lastc = t;
        int anim = t - k->streak > 2.0;                                  /* animated: remembered here, so every reader knows */
        if (anim && (hab == 0 || hab == 4)) hab = 2;
        zb[i * 5] = anim ? 3.0f : (hab == 0 || hab == 4) ? 1.0f : 2.0f;
        if (bx1 > bx0) { zb[i * 5 + 1] = (float)bx0; zb[i * 5 + 2] = (float)by0; zb[i * 5 + 3] = (float)(bx1 - bx0); zb[i * 5 + 4] = (float)(by1 - by0); }
        if (hab == 3) { fprintf(e->tape, "%s H %d,%d %.1f\n", ts, c, r, k->beat); e->habit_total++; e->w_habit++; }
        else if (hab == 4) fprintf(e->tape, "%s B %d,%d\n", ts, c, r);
        int nonbot = bx1 > bx0 && perch_nonbot(e, bx0, by0, bx1, by1, t);
        if (nonbot) e->nonbot_total++;
        if ((hab == 0 || hab == 4) && !nonbot) {
            if (bx1 > bx0) { e->sbox[i * 4] = bx0; e->sbox[i * 4 + 1] = by0; e->sbox[i * 4 + 2] = bx1; e->sbox[i * 4 + 3] = by1; e->smark[i] = 1; }
            nstrike++;
        }
        if (e->vecs) {                                                   /* raw vector tape: time, cell, 4 floats; local only */
            float rec[2 + VEC] = {(float)(t - e->t0), (float)i};
            memcpy(rec + 2, v, sizeof v);
            fwrite(rec, sizeof rec, 1, e->vecs);
        }
        memcpy(k->v, v, sizeof v);
        for (int yy = y; yy < y + h; yy++) memcpy(prev + (size_t)yy * W + x, cur + (size_t)yy * cur_stride + (size_t)x * 4, (size_t)w * 4);
    }
    if (nreal && e->have_box) {                                          /* the whole grid, as it is now, into the box */
        uint16_t *bf = coop_box_next(&e->box, t);
        for (int i = 0; i < ncell; i++) {
            for (int k = 0; k < VEC; k++) bf[i * 9 + k] = (uint16_t)(cells[i].v[k] + 0.5f);
            for (int k = 0; k < 5; k++) bf[i * 9 + 4 + k] = (uint16_t)zb[i * 5 + k];
        }
        coop_box_commit(&e->box);
    }
    /* the text line: cells that strike together and touch (8 ways) are one region, "c0,r0-c1,r1@x,y wxh" with the box around their
       zoom boxes; a lone cell stays "c,r@x,y wxh". A full-screen repaint (top, a scrolling page) is one entry, not one per cell. */
    for (int i = 0; i < ncell; i++) {
        if (e->smark[i] != 1) continue;
        int head = 0, tail = 0, n = 0;
        int c0 = GX, r0 = GY, c1 = -1, r1 = -1, x0 = W, y0 = H, x1 = 0, y1 = 0;
        e->queue[tail++] = i; e->smark[i] = 2;
        while (head < tail) {
            int j = e->queue[head++], jr = j / GX, jc = j % GX;
            n++;
            if (jc < c0) c0 = jc;
            if (jc > c1) c1 = jc;
            if (jr < r0) r0 = jr;
            if (jr > r1) r1 = jr;
            if (e->sbox[j * 4] < x0) x0 = e->sbox[j * 4];
            if (e->sbox[j * 4 + 1] < y0) y0 = e->sbox[j * 4 + 1];
            if (e->sbox[j * 4 + 2] > x1) x1 = e->sbox[j * 4 + 2];
            if (e->sbox[j * 4 + 3] > y1) y1 = e->sbox[j * 4 + 3];
            for (int dr = -1; dr <= 1; dr++)
                for (int dc = -1; dc <= 1; dc++) {
                    int nr = jr + dr, nc = jc + dc;
                    if (nr < 0 || nc < 0 || nr >= GY || nc >= GX) continue;
                    int k = nr * GX + nc;
                    if (e->smark[k] == 1) { e->smark[k] = 2; e->queue[tail++] = k; }
                }
        }
        if (ln < (int)sizeof line - 80) {
            if (n == 1) ln += snprintf(line + ln, sizeof line - ln, "%s%d,%d@%d,%d %dx%d", ln ? " " : "", c0, r0, x0, y0, x1 - x0, y1 - y0);
            else ln += snprintf(line + ln, sizeof line - ln, "%s%d,%d-%d,%d@%d,%d %dx%d", ln ? " " : "", c0, r0, c1, r1, x0, y0, x1 - x0, y1 - y0);
        }
    }
    if (ncell > 0) memset(e->smark, 0, (size_t)ncell);
    if (nstrike && ln) {
        e->strikes_total += nstrike; e->w_strikes += nstrike; e->frames_with_strike++; e->w_frames++;
        int n = fprintf(e->tape, "%s S %s\n", ts, line);
        if (n > 0) { e->tape_bytes += n; e->w_tape += n; }
        snprintf(e->last_line, sizeof e->last_line, "%s S %.300s", ts, line);
    }
    return 1;
}

/* every STATS_EVERY seconds (or final = 1 at the end): the stats line and now.json */
static void perch_stats(PerchEye *e, double t, int final) {
    char ts[16];
    if (final) {
        double dt = t - e->t0, dc = perch_cpu() - e->c0;
        if (dt <= 0) dt = 1e-9;
        double scr = W > 0 && H > 0 ? (double)W * H : 1;
        clock_txt(t, ts, sizeof ts);
        fprintf(e->stats, "%s TOTAL %.0fs cpu %.2f%% | damage %ld looks %ld strikes %ld (frames %ld) tuned-out %ld not-for-bots %ld | px read %.1f%% of screen/s | "
                          "tokens: text %.0f, shots-on-strike %.0f, shot-per-sec %.0f\n",
                ts, dt, 100.0 * dc / dt, e->ev_total, e->looks, e->strikes_total, e->frames_with_strike, e->habit_total, e->nonbot_total, 100.0 * e->px_read / (scr * dt),
                e->tape_bytes / 4.0, e->frames_with_strike * SHOT_TOKENS(W, H), dt * SHOT_TOKENS(W, H));
        return;
    }
    if (t - e->last_stats < STATS_EVERY) return;
    double dt = t - e->win_t0, dc = perch_cpu() - e->win_c0;
    double scr = W > 0 && H > 0 ? (double)W * H : 1;
    double tok_text = e->w_tape / 4.0;                             /* about 4 characters per token */
    double tok_shot_strike = e->w_frames * SHOT_TOKENS(W, H);      /* a screenshot each time something struck */
    double tok_shot_sec = dt * SHOT_TOKENS(W, H);                  /* a screenshot every second */
    clock_txt(t, ts, sizeof ts);
    fprintf(e->stats, "%s cpu %.2f%% | damage %ld looks %ld strikes %ld (frames %ld) tuned-out %ld | px read %.1f%% of screen/s | "
                      "tokens: text %.0f, shots-on-strike %.0f, shot-per-sec %.0f\n",
            ts, 100.0 * dc / dt, e->w_ev, e->w_looks, e->w_strikes, e->w_frames, e->w_habit, 100.0 * e->w_px / (scr * dt),
            tok_text, tok_shot_strike, tok_shot_sec);
    FILE *nj = fopen(e->nowtmp, "w");
    if (nj) {
        char esc[700];
        int k = 0;
        for (const char *p = e->last_line; *p && k < (int)sizeof esc - 3; p++) { if (*p == '"' || *p == '\\') esc[k++] = '\\'; esc[k++] = *p; }
        esc[k] = 0;
        int habits = 0;
        for (int i = 0; cells && i < GX * GY; i++) habits += cells[i].habit;
        fprintf(nj, "{\"at\":%ld,\"%s\":\"%s\",\"grid\":\"%dx%d\",\"cpu_pct\":%.2f,\"damage\":%ld,\"looks\":%ld,\"strikes\":%ld,"
                    "\"tuned_out\":%d,\"tokens_text\":%.0f,\"tokens_shots_on_strike\":%.0f,\"tokens_shot_per_sec\":%.0f,\"window_s\":%.1f,\"last\":\"%s\"}\n",
                (long)t, !strcmp(e->kind, "eye") ? "display" : e->kind, e->name, GX, GY, 100.0 * dc / dt, e->w_ev, e->w_looks, e->w_strikes, habits,
                tok_text, tok_shot_strike, tok_shot_sec, dt, esc);
        fclose(nj);
        rename(e->nowtmp, e->nowpath);
    }
    e->win_t0 = t; e->win_c0 = perch_cpu(); e->last_stats = t;
    e->w_ev = e->w_looks = e->w_strikes = e->w_frames = e->w_tape = e->w_px = e->w_habit = 0;
}

static void perch_close(PerchEye *e) {
    char ts[16];
    clock_txt(perch_now(), ts, sizeof ts);
    fprintf(e->tape, "%s C %s\n", ts, e->kind);
    fclose(e->tape); fclose(e->vecs); fclose(e->stats);
    if (e->have_box) coop_box_close(&e->box);
}
#endif
