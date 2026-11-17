/* Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md. */
/* perch-cam: the Perch eye on a camera's video, in memory. No browser, no display, no screenshots.
 *
 * Frames come in as raw greyscale bytes (1 byte a pixel) on stdin, decoded from the camera's data by ffmpeg, e.g.
 *   ffmpeg -rtsp_transport tcp -i rtsp://... -f rawvideo -pix_fmt gray -s 640x360 - | perch-cam -s 640x360
 * Each grid cell keeps a slow background (the scene when nothing moves). A cell strikes when enough of its pixels differ from the
 * background by more than the threshold, so compression shimmer and noise do not count. A struck cell is zoomed (quadtree) to the
 * box that moved. Bots get one line when movement starts or moves on:  M x,y wxh n=CELLS  (and Q when it goes quiet), on stdout
 * and the tape; and the plane "cam" in the box of bot vectors: rows x cols x 3 (u8: brightness, motion 0..255, strike 0/1).
 * Habits (a scene's own restless spots are learned and tuned out, as a person stops noticing them):
 *   beat     a cell whose strikes START on a steady rhythm (8 in a row, gaps within 20% of their mean: a flickering lamp, a blinking
 *            light) is tuned out; one start off the rhythm and it counts again
 *   restless a cell struck in more than a quarter of the time over the last minute or so (a branch swaying past a lamp, rippling
 *            water: busy but going nowhere; a passing bird or person never stays that long) is tuned out until it calms (< 10%)
 *   crossing a habit cell struck in the same frame as a NON-habit neighbour still counts: something is coming in from outside
 *   Bots are told:  h c=CELL x,y beat 1.20s | h c=CELL x,y restless | h c=CELL x,y off
 * The camera moving (a pan, a tilt, auto-tracking turning its head): when most of the cells strike in the same frame it is the
 * VIEW that moved, not the world: one line  C moved n=CELLS , the new view taken as the background at once, a short settle
 * (0.6 s) without movement lines; then watching goes on (things moving inside the new view count).
 *   perch-cam -s WxH [-g 16x9] [-T 18 (pixel threshold)] [-p 3 (percent of a cell)] [-o DIR]
 */
#define _GNU_SOURCE
#include "perch-box.h"
#include <sys/resource.h>
#include <math.h>

static double now_s(void) { struct timespec t; clock_gettime(CLOCK_REALTIME, &t); return (double)t.tv_sec + t.tv_nsec / 1e9; }
static double cpu_s(void) {
    struct rusage r;
    getrusage(RUSAGE_SELF, &r);
    return (double)r.ru_utime.tv_sec + r.ru_utime.tv_usec / 1e6 + (double)r.ru_stime.tv_sec + r.ru_stime.tv_usec / 1e6;
}

static int W, H, T = 18;
/* a cell's habit: its strike starts (for a beat) and its busy share (for restlessness) */
#define BEATN 8
typedef struct { double on[BEATN + 1]; int non, struck, beat_habit, restless; double beat, busy, last_t; } Habit;
static int beat_check(Habit *h, double t) {           /* a strike STARTS at t: is it on a steady rhythm? returns 1 if it changed */
    int was = h->beat_habit;
    if (h->beat_habit && fabs((t - h->on[(h->non + BEATN) % (BEATN + 1)]) - h->beat) > 0.2 * h->beat + 0.1) { h->beat_habit = 0; h->non = 0; }
    h->on[h->non % (BEATN + 1)] = t; h->non++;
    if (!h->beat_habit && h->non > BEATN) {
        double g[BEATN], m = 0;
        for (int k = 0; k < BEATN; k++) { g[k] = h->on[(h->non - k - 1) % (BEATN + 1)] - h->on[(h->non - k - 2 + BEATN + 1) % (BEATN + 1)]; m += g[k]; }
        m /= BEATN; int steady = m > 0.05;
        for (int k = 0; k < BEATN && steady; k++) if (fabs(g[k] - m) > 0.2 * m) steady = 0;
        if (steady) { h->beat_habit = 1; h->beat = m; }
    }
    return was != h->beat_habit;
}
static unsigned char *fr, *bg;                                          /* this frame, the background */

static int moved(int x, int y, int w, int h, int need) {               /* pixels in the box past the threshold, at least `need` */
    int n = 0;
    for (int yy = y; yy < y + h; yy++)
        for (int xx = x; xx < x + w; xx++)
            if (abs(fr[yy * W + xx] - bg[yy * W + xx]) > T && ++n >= need) return 1;
    return 0;
}
static void zoom(int x, int y, int w, int h, int d, int *bx0, int *by0, int *bx1, int *by1) {
    if (w <= 0 || h <= 0 || !moved(x, y, w, h, w * h >= 64 ? 3 : 1)) return;
    if (d >= 5 || (w <= 4 && h <= 4)) {
        if (x < *bx0) *bx0 = x;
        if (y < *by0) *by0 = y;
        if (x + w > *bx1) *bx1 = x + w;
        if (y + h > *by1) *by1 = y + h;
        return;
    }
    int hw = w / 2, hh = h / 2;
    zoom(x, y, hw, hh, d + 1, bx0, by0, bx1, by1); zoom(x + hw, y, w - hw, hh, d + 1, bx0, by0, bx1, by1);
    zoom(x, y + hh, hw, h - hh, d + 1, bx0, by0, bx1, by1); zoom(x + hw, y + hh, w - hw, h - hh, d + 1, bx0, by0, bx1, by1);
}

int main(int argc, char **argv) {
    int GX = 16, GY = 9, pct = 3;
    const char *outdir = NULL;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "-s") && i + 1 < argc) { if (sscanf(argv[++i], "%dx%d", &W, &H) != 2) return 2; }
        else if (!strcmp(argv[i], "-g") && i + 1 < argc) { if (sscanf(argv[++i], "%dx%d", &GX, &GY) != 2) return 2; }
        else if (!strcmp(argv[i], "-T") && i + 1 < argc) T = atoi(argv[++i]);
        else if (!strcmp(argv[i], "-p") && i + 1 < argc) pct = atoi(argv[++i]);
        else if (!strcmp(argv[i], "-o") && i + 1 < argc) outdir = argv[++i];
        else { fprintf(stderr, "usage: ... | %s -s WxH [-g 16x9] [-T 18] [-p 3] [-o DIR]\n", argv[0]); return 2; }
    }
    if (W <= 0 || H <= 0 || GX < 1 || GY < 1) { fprintf(stderr, "perch-cam: give the frame size, -s WxH\n"); return 2; }
    size_t n = (size_t)W * H;
    fr = malloc(n); bg = malloc(n);
    unsigned char *acc = calloc(n, 2);                                  /* background as 8.8 fixed point, for a slow average */
    uint16_t *bgf = (uint16_t *)acc;
    if (!fr || !bg || !acc) return 1;
    char dir[1024], path[1200];
    if (outdir) snprintf(dir, sizeof dir, "%s", outdir); else snprintf(dir, sizeof dir, "%s/perch/tape", getenv("HOME") ? getenv("HOME") : ".");
    mkdir(dir, 0750);
    snprintf(path, sizeof path, "%s/cam.txt", dir);
    FILE *tape = fopen(path, "a");
    if (tape) setvbuf(tape, NULL, _IOLBF, 0);
    PerchBox box;
    uint32_t bdim[3] = {(uint32_t)GY, (uint32_t)GX, 3};
    char bf[300];
    snprintf(bf, sizeof bf, "brightness motion strike; per grid cell, u8; motion = share of the cell's pixels that moved, 0..255; frame %dx%d; "
                            "a frame per video frame with movement", W, H);
    int have_box = perch_box_create(&box, "cam", "u8", 3, bdim, bf) == 0;
    int cw = W / GX, ch = H / GY;
    int need = cw * ch * pct / 100;
    if (need < 1) need = 1;
    long frames = 0, mframes = 0, lines = 0;
    double t0 = now_s(), c0 = cpu_s();
    int was = 0, lx0 = -1, ly0 = -1, lx1 = -1, ly1 = -1;
    Habit *hab = calloc((size_t)GX * GY, sizeof(Habit));
    unsigned char *raw = calloc((size_t)GX * GY, 1);
    long tuned = 0, cmoves = 0;
    double moved_t = -1e9;
    setvbuf(stdout, NULL, _IOLBF, 0);
    while (fread(fr, 1, n, stdin) == n) {
        frames++;
        if (frames == 1) { memcpy(bg, fr, n); for (size_t i = 0; i < n; i++) bgf[i] = (uint16_t)(fr[i] << 8); continue; }
        int bx0 = W, by0 = H, bx1 = 0, by1 = 0, ncell = 0;
        double tnow = now_s();
        uint8_t *bv = have_box ? NULL : NULL;
        uint8_t cellv[4096 * 3];
        int ncells = GX * GY <= 4096 ? GX * GY : 4096;
        for (int c = 0; c < ncells; c++) {
            int x = (c % GX) * cw, y = (c / GX) * ch, cnt = 0;
            long sum = 0;
            for (int yy = y; yy < y + ch; yy++)
                for (int xx = x; xx < x + cw; xx++) { int p = fr[yy * W + xx]; sum += p; cnt += abs(p - bg[yy * W + xx]) > T; }
            cellv[c * 3] = (uint8_t)(sum / (cw * ch));
            cellv[c * 3 + 1] = (uint8_t)(cnt * 255 / (cw * ch));
            raw[c] = cnt >= need;
        }
        /* the camera moved? most cells struck at once: re-take the background, settle, say so once */
        int nraw = 0; for (int c = 0; c < ncells; c++) nraw += raw[c];
        if (nraw * 2 > ncells) {
            if (tnow - moved_t > 0.6) { char l[80]; snprintf(l, sizeof l, "%.2f C moved n=%d", tnow, nraw); puts(l); if (tape) fprintf(tape, "%s\n", l); lines++; cmoves++; }
            moved_t = tnow;
            memcpy(bg, fr, n); for (size_t i = 0; i < n; i++) bgf[i] = (uint16_t)(fr[i] << 8);
            if (was) { char l[60]; snprintf(l, sizeof l, "%.2f Q", tnow); puts(l); if (tape) fprintf(tape, "%s\n", l); lines++; was = 0; }
            continue;
        }
        if (tnow - moved_t < 0.6) {                                    /* settling after the view moved */
            for (size_t i = 0; i < n; i++) { int d = ((int)fr[i] << 8) - (int)bgf[i]; bgf[i] = (uint16_t)((int)bgf[i] + d / 4); bg[i] = (unsigned char)(bgf[i] >> 8); }
            continue;
        }
        /* habits: learn them, then count a habit cell only when something crosses into it from a plain neighbour */
        for (int c = 0; c < ncells; c++) {
            Habit *h = &hab[c];
            double dt = h->last_t > 0 ? tnow - h->last_t : 0; h->last_t = tnow;
            h->busy += (raw[c] - h->busy) * (dt / 60.0 < 1 ? dt / 60.0 : 1);          /* share of the last minute or so struck */
            int was_r = h->restless;
            if (!h->restless && h->busy > 0.25) h->restless = 1; else if (h->restless && h->busy < 0.10) h->restless = 0;
            int changed = was_r != h->restless;
            if (raw[c] && !h->struck) changed |= beat_check(h, tnow);
            h->struck = raw[c];
            if (changed) {
                char l[120];
                if (h->beat_habit) snprintf(l, sizeof l, "%.2f h c=%d %d,%d beat %.2fs", tnow, c, (c % GX) * cw, (c / GX) * ch, h->beat);
                else if (h->restless) snprintf(l, sizeof l, "%.2f h c=%d %d,%d restless", tnow, c, (c % GX) * cw, (c / GX) * ch);
                else snprintf(l, sizeof l, "%.2f h c=%d %d,%d off", tnow, c, (c % GX) * cw, (c / GX) * ch);
                puts(l); if (tape) fprintf(tape, "%s\n", l); lines++;
            }
        }
        for (int c = 0; c < ncells; c++) {
            int count = raw[c];
            if (count && (hab[c].beat_habit || hab[c].restless)) {
                count = 0;
                int cx = c % GX, cy = c / GX;
                for (int dy = -1; dy <= 1 && !count; dy++) for (int dx = -1; dx <= 1 && !count; dx++) {
                    int nx = cx + dx, ny = cy + dy, k = ny * GX + nx;
                    if ((dx || dy) && nx >= 0 && ny >= 0 && nx < GX && ny < GY && raw[k] && !hab[k].beat_habit && !hab[k].restless) count = 1;
                }
                if (!count) tuned++;
            }
            cellv[c * 3 + 2] = (uint8_t)count;
            if (count) { int x = (c % GX) * cw, y = (c / GX) * ch; zoom(x, y, cw, ch, 0, &bx0, &by0, &bx1, &by1); ncell++; }
        }
        (void)bv;
        /* the background keeps learning every pixel: fast (about 1 s) where it matches, slower (about 1.6 s) where it differs, so a moving
           thing is never taken in but a ghost (something that was there at the start, or has left) fades away */
        for (size_t i = 0; i < n; i++) {
            int d = ((int)fr[i] << 8) - (int)bgf[i];
            bgf[i] = (uint16_t)((int)bgf[i] + d / (abs(fr[i] - bg[i]) > T ? 24 : 16));
            bg[i] = (unsigned char)(bgf[i] >> 8);
        }
        if (ncell && bx1 > bx0) {
            mframes++;
            if (have_box) { uint8_t *o = perch_box_next(&box, now_s()); if (o) { memcpy(o, cellv, (size_t)ncells * 3); perch_box_commit(&box); } }
            /* a line when movement starts, or when its box has moved on by more than a quarter of its size */
            int mw = bx1 - bx0, mh = by1 - by0;
            if (!was || abs(bx0 - lx0) > mw / 4 + 4 || abs(by0 - ly0) > mh / 4 + 4 || abs(bx1 - lx1) > mw / 4 + 4 || abs(by1 - ly1) > mh / 4 + 4) {
                char l[120];
                snprintf(l, sizeof l, "%.2f M %d,%d %dx%d n=%d", now_s(), bx0, by0, mw, mh, ncell);
                puts(l); if (tape) fprintf(tape, "%s\n", l);
                lines++; lx0 = bx0; ly0 = by0; lx1 = bx1; ly1 = by1;
            }
            was = 1;
        } else if (was) {
            char l[60];
            snprintf(l, sizeof l, "%.2f Q", now_s());
            puts(l); if (tape) fprintf(tape, "%s\n", l);
            lines++; was = 0;
        }
    }
    double dt = now_s() - t0, dc = cpu_s() - c0;
    int nb = 0, nr = 0; for (int c = 0; c < GX * GY && c < 4096; c++) { nb += hab[c].beat_habit; nr += hab[c].restless; }
    fprintf(stderr, "perch-cam: %ld frames (%dx%d), %ld with movement, %ld lines, %ld habit strikes tuned out (habits now: %d beat, %d restless), camera moved %ld times, cpu %.2f s (%.2f%% of a core)\n",
            frames, W, H, mframes, lines, tuned, nb, nr, cmoves, dc, dt > 0 ? 100 * dc / dt : 0);
    return 0;
}
