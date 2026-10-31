/* coop-perch-eye: the local eye on a Perch display (default :2).
 *
 * The display (Xvfb :2, started by coop-perch-display) is a real X screen: any app drawn on it thinks it is a monitor.
 * This eye never takes screenshots. It:
 *   1. asks the X server (DAMAGE extension) which rectangles changed: free change detection, no frame comparing;
 *   2. turns only the touched cells of a coarse grid (periphery, default 16x10) into raw vectors (mean R,G,B,luma per cell);
 *   3. a cell whose vector moved = a strike; the eye zooms into that cell (quadtree, "look closer where it moved")
 *      until the change is pinned to a small box;
 *   4. habituation: a cell that keeps striking on a beat (clock, cursor, spinner) gets tuned out; a break in its beat counts again.
 * Vectors stay local (binary tape, no tokens). Text is the bottom layer: one short line per strike frame, the same shape every time.
 * Every 10 s a stats line: the eye's own CPU, events, strikes, and the tokens the text tape would cost the bot,
 * next to what screenshots would have cost for the same moments.
 *
 * Build: make     Run: coop-perch-eye [-d :2] [-g 16x10] [-o DIR] [-t SECONDS]
 */
#define _GNU_SOURCE
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <X11/extensions/Xdamage.h>
#include <X11/extensions/XShm.h>
#include <X11/extensions/Xfixes.h>
#include <sys/ipc.h>
#include <sys/shm.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <poll.h>
#include <errno.h>
#include <math.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define VEC 4                     /* numbers per cell: R, G, B, luma (0..255) */
#define STRIKE_DELTA 1.5          /* a cell strikes when its vector moves by more than this (sum of abs, per channel mean) */
#define ZOOM_MIN 6                /* the quadtree stops at boxes this small (pixels) */
#define ZOOM_DEPTH 6
#define FRAME_MS 100              /* the eye looks at most 10 times a second; damage between looks is merged */
#define HABIT_WINDOW 30           /* seconds of strike history per cell used to learn its beat */
#define HABIT_HITS 8              /* this many strikes on a steady beat inside the window = a habit (tuned out) */
#define STATS_EVERY 10            /* seconds */
#define SHOT_TOKENS(w, h) (((double)(w) * (double)(h)) / 750.0)   /* what one image costs the bot (w*h/750, Claude's rule) */

typedef struct {
    float v[VEC];                 /* the cell's last vector */
    int have;                     /* v holds a real look */
    double hits[HABIT_HITS * 2];  /* recent strike times (ring) */
    int nhits, hpos;
    int habit;                    /* tuned out */
    double beat;                  /* learned seconds between strikes when habit */
    double last;                  /* last strike time */
} Cell;

static volatile sig_atomic_t stop_now = 0;
static void on_stop(int s) { (void)s; stop_now = 1; }

static double now_s(void) {
    struct timespec t;
    clock_gettime(CLOCK_REALTIME, &t);
    return (double)t.tv_sec + (double)t.tv_nsec / 1e9;
}
static double cpu_s(void) {
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

/* ---- pixels: one shared-memory image the size of the screen, filled only for the rects asked ---- */
static Display *dpy = NULL;
static XImage *img = NULL;        /* current pixels (only dirty areas are fresh) */
static uint32_t *prev = NULL;     /* pixels at the last look, for the zoom compare */
static XShmSegmentInfo shm;
static int use_shm = 0, W = 0, H = 0;
static Window root;

static int grab(int x, int y, int w, int h) {
    if (w <= 0 || h <= 0) return 0;
    if (use_shm) {
        XImage *sub = XShmCreateImage(dpy, DefaultVisual(dpy, DefaultScreen(dpy)), DefaultDepth(dpy, DefaultScreen(dpy)),
                                      ZPixmap, NULL, &shm, (unsigned)w, (unsigned)h);
        if (!sub) return -1;
        sub->data = shm.shmaddr;
        if (!XShmGetImage(dpy, root, sub, x, y, AllPlanes)) { sub->data = NULL; XDestroyImage(sub); return -1; }
        for (int r = 0; r < h; r++)
            memcpy(img->data + (size_t)(y + r) * img->bytes_per_line + (size_t)x * 4, sub->data + (size_t)r * sub->bytes_per_line, (size_t)w * 4);
        sub->data = NULL;
        XDestroyImage(sub);
        return 0;
    }
    XImage *sub = XGetImage(dpy, root, x, y, (unsigned)w, (unsigned)h, AllPlanes, ZPixmap);
    if (!sub) return -1;
    for (int r = 0; r < h; r++)
        memcpy(img->data + (size_t)(y + r) * img->bytes_per_line + (size_t)x * 4, sub->data + (size_t)r * sub->bytes_per_line, (size_t)w * 4);
    XDestroyImage(sub);
    return 0;
}
static inline uint32_t px(int x, int y) { return ((uint32_t *)(img->data + (size_t)y * img->bytes_per_line))[x]; }
static inline uint32_t ppx(int x, int y) { return prev[(size_t)y * W + x]; }

static void cell_vector(int x0, int y0, int w, int h, float *v) {
    double r = 0, g = 0, b = 0;
    for (int y = y0; y < y0 + h; y++)
        for (int x = x0; x < x0 + w; x++) {
            uint32_t p = px(x, y);
            r += (p >> 16) & 255; g += (p >> 8) & 255; b += p & 255;
        }
    double n = (double)w * h;
    v[0] = (float)(r / n); v[1] = (float)(g / n); v[2] = (float)(b / n);
    v[3] = (float)(0.299 * v[0] + 0.587 * v[1] + 0.114 * v[2]);
}

static int box_differs(int x0, int y0, int w, int h) {
    for (int y = y0; y < y0 + h; y++)
        for (int x = x0; x < x0 + w; x++)
            if ((px(x, y) & 0xffffff) != (ppx(x, y) & 0xffffff)) return 1;
    return 0;
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

int main(int argc, char **argv) {
    const char *disp = ":2", *outdir = NULL;
    int GX = 16, GY = 10;
    double run_for = 0;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "-d") && i + 1 < argc) disp = argv[++i];
        else if (!strcmp(argv[i], "-g") && i + 1 < argc) { if (sscanf(argv[++i], "%dx%d", &GX, &GY) != 2 || GX < 1 || GY < 1) { fprintf(stderr, "bad -g\n"); return 2; } }
        else if (!strcmp(argv[i], "-o") && i + 1 < argc) outdir = argv[++i];
        else if (!strcmp(argv[i], "-t") && i + 1 < argc) run_for = atof(argv[++i]);
        else { fprintf(stderr, "usage: %s [-d :2] [-g 16x10] [-o DIR] [-t SECONDS]\n", argv[0]); return 2; }
    }
    char dir[1024];
    if (outdir) snprintf(dir, sizeof dir, "%s", outdir);
    else snprintf(dir, sizeof dir, "%s/coop-perch/tape", getenv("HOME") ? getenv("HOME") : ".");
    mkdir(dir, 0750);

    dpy = XOpenDisplay(disp);
    if (!dpy) { fprintf(stderr, "coop-perch-eye: cannot open display %s (is coop-perch-display running?)\n", disp); return 1; }
    int scr = DefaultScreen(dpy);
    root = RootWindow(dpy, scr);
    W = DisplayWidth(dpy, scr); H = DisplayHeight(dpy, scr);
    if (DefaultDepth(dpy, scr) < 24) { fprintf(stderr, "coop-perch-eye: display depth %d, need 24\n", DefaultDepth(dpy, scr)); return 1; }

    int dmg_ev = 0, dmg_err = 0, fx_ev = 0, fx_err = 0;
    if (!XDamageQueryExtension(dpy, &dmg_ev, &dmg_err)) { fprintf(stderr, "coop-perch-eye: display has no DAMAGE extension\n"); return 1; }
    if (!XFixesQueryExtension(dpy, &fx_ev, &fx_err)) { fprintf(stderr, "coop-perch-eye: display has no XFIXES extension\n"); return 1; }

    /* full-screen image buffer (ordinary memory) + shared memory for the actual reads */
    img = XCreateImage(dpy, DefaultVisual(dpy, scr), DefaultDepth(dpy, scr), ZPixmap, 0, calloc((size_t)W * H, 4), (unsigned)W, (unsigned)H, 32, W * 4);
    prev = calloc((size_t)W * H, 4);
    if (!img || !img->data || !prev) { fprintf(stderr, "coop-perch-eye: out of memory\n"); return 1; }
    if (XShmQueryExtension(dpy)) {
        shm.shmid = shmget(IPC_PRIVATE, (size_t)W * H * 4, IPC_CREAT | 0600);
        if (shm.shmid >= 0) {
            shm.shmaddr = shmat(shm.shmid, NULL, 0);
            shm.readOnly = False;
            if (shm.shmaddr != (char *)-1 && XShmAttach(dpy, &shm)) { XSync(dpy, False); shmctl(shm.shmid, IPC_RMID, NULL); use_shm = 1; }
        }
    }

    Damage damage = XDamageCreate(dpy, root, XDamageReportRawRectangles);
    (void)damage;
    XSync(dpy, False);

    int ncell = GX * GY;
    Cell *cells = calloc((size_t)ncell, sizeof(Cell));
    unsigned char *dirty = calloc((size_t)ncell, 1);
    float *frame = calloc((size_t)ncell * VEC, sizeof(float));
    if (!cells || !dirty || !frame) { fprintf(stderr, "coop-perch-eye: out of memory\n"); return 1; }
    int cw = W / GX, ch = H / GY;   /* last row/column take the remainder */
#define CX0(c) ((c) * cw)
#define CY0(r) ((r) * ch)
#define CW(c) ((c) == GX - 1 ? W - (c) * cw : cw)
#define CH(r) ((r) == GY - 1 ? H - (r) * ch : ch)

    char path[1200];
    snprintf(path, sizeof path, "%s/tape.txt", dir);
    FILE *tape = fopen(path, "a");
    snprintf(path, sizeof path, "%s/vectors.bin", dir);
    FILE *vecs = fopen(path, "ab");
    snprintf(path, sizeof path, "%s/stats.txt", dir);
    FILE *stats = fopen(path, "a");
    char nowpath[1200], nowtmp[1250];
    snprintf(nowpath, sizeof nowpath, "%s/now.json", dir);
    snprintf(nowtmp, sizeof nowtmp, "%s/now.json.tmp", dir);
    if (!tape || !vecs || !stats) { fprintf(stderr, "coop-perch-eye: cannot write in %s\n", dir); return 1; }
    setvbuf(tape, NULL, _IOLBF, 0); setvbuf(stats, NULL, _IOLBF, 0);

    signal(SIGINT, on_stop); signal(SIGTERM, on_stop);
    double t0 = now_s(), c0 = cpu_s(), last_stats = t0, last_look = 0;
    double win_t0 = t0, win_c0 = c0;
    long ev_total = 0, looks = 0, strikes_total = 0, habit_total = 0, frames_with_strike = 0, tape_bytes = 0, px_read = 0;
    long w_ev = 0, w_looks = 0, w_strikes = 0, w_frames = 0, w_tape = 0, w_px = 0, w_habit = 0;
    char last_line[400] = "";
    int pending = 0;
    char ts[16];
    clock_txt(t0, ts, sizeof ts);
    fprintf(tape, "%s perch: eye open on %s %dx%d, grid %dx%d, %s\n", ts, disp, W, H, GX, GY, use_shm ? "shm" : "no shm");

    /* first look: whole screen, so every cell has a vector to compare against */
    grab(0, 0, W, H);
    for (int r = 0; r < GY; r++)
        for (int c = 0; c < GX; c++) { Cell *k = &cells[r * GX + c]; cell_vector(CX0(c), CY0(r), CW(c), CH(r), k->v); k->have = 1; }
    for (int y = 0; y < H; y++) memcpy(prev + (size_t)y * W, img->data + (size_t)y * img->bytes_per_line, (size_t)W * 4);

    struct pollfd pfd = {.fd = ConnectionNumber(dpy), .events = POLLIN};
    while (!stop_now) {
        double t = now_s();
        if (run_for > 0 && t - t0 >= run_for) break;
        int wait = pending ? (int)fmax(0, FRAME_MS - (t - last_look) * 1000) : 1000;
        if (!XPending(dpy)) poll(&pfd, 1, wait);
        while (XPending(dpy)) {
            XEvent e;
            XNextEvent(dpy, &e);
            if (e.type == dmg_ev + XDamageNotify) {
                XDamageNotifyEvent *d = (XDamageNotifyEvent *)&e;
                ev_total++; w_ev++;
                int x0 = d->area.x, y0 = d->area.y, x1 = d->area.x + d->area.width, y1 = d->area.y + d->area.height;
                if (x0 < 0) x0 = 0;
                if (y0 < 0) y0 = 0;
                if (x1 > W) x1 = W;
                if (y1 > H) y1 = H;
                for (int r = y0 / ch; r <= (y1 - 1) / ch && r < GY; r++)
                    for (int c = x0 / cw; c <= (x1 - 1) / cw && c < GX; c++) dirty[r * GX + c] = 1;
                pending = 1;
            }
        }
        t = now_s();
        if (pending && (t - last_look) * 1000 >= FRAME_MS) {
            /* the look: read only the dirty cells, make their vectors, strike, zoom */
            last_look = t; pending = 0; looks++; w_looks++;
            char line[4000];
            int ln = 0, nstrike = 0;
            clock_txt(t, ts, sizeof ts);
            for (int i = 0; i < ncell; i++) {
                if (!dirty[i]) continue;
                dirty[i] = 0;
                int r = i / GX, c = i % GX;
                int x = CX0(c), y = CY0(r), w = CW(c), h = CH(r);
                if (grab(x, y, w, h) != 0) continue;
                px_read += (long)w * h; w_px += (long)w * h;
                float v[VEC];
                cell_vector(x, y, w, h, v);
                float delta = 0;
                for (int k = 0; k < VEC; k++) delta += fabsf(v[k] - cells[i].v[k]);
                int bx0 = W, by0 = H, bx1 = 0, by1 = 0, md = 0;
                if (delta > STRIKE_DELTA || box_differs(x, y, w, h)) {
                    zoom(x, y, w, h, 0, &bx0, &by0, &bx1, &by1, &md);
                    int hab = habit_check(&cells[i], t);
                    if (hab == 3) { fprintf(tape, "%s perch: cell %d,%d tuned out (beat %.1fs)\n", ts, c, r, cells[i].beat); habit_total++; w_habit++; }
                    else if (hab == 4) fprintf(tape, "%s perch: cell %d,%d broke its beat, watching again\n", ts, c, r);
                    if (hab == 0 || hab == 4) {
                        if (bx1 > bx0 && ln < (int)sizeof line - 80)
                            ln += snprintf(line + ln, sizeof line - ln, "%s%d,%d@%d,%d %dx%d", nstrike ? " " : "", c, r, bx0, by0, bx1 - bx0, by1 - by0);
                        nstrike++;
                    }
                    if (vecs) {                           /* raw vector tape: time, cell, 4 floats; local only */
                        float rec[2 + VEC] = {(float)(t - t0), (float)i};
                        memcpy(rec + 2, v, sizeof v);
                        fwrite(rec, sizeof rec, 1, vecs);
                    }
                }
                memcpy(cells[i].v, v, sizeof v);
                for (int yy = y; yy < y + h; yy++) memcpy(prev + (size_t)yy * W + x, img->data + (size_t)yy * img->bytes_per_line + (size_t)x * 4, (size_t)w * 4);
                memcpy(frame + (size_t)i * VEC, v, sizeof v);
            }
            if (nstrike) {
                strikes_total += nstrike; w_strikes += nstrike; frames_with_strike++; w_frames++;
                int n = fprintf(tape, "%s strike %d: %s\n", ts, nstrike, line);
                if (n > 0) { tape_bytes += n; w_tape += n; }
                snprintf(last_line, sizeof last_line, "%s strike %d: %.300s", ts, nstrike, line);
            }
        }
        t = now_s();
        if (t - last_stats >= STATS_EVERY) {
            double dt = t - win_t0, dc = cpu_s() - win_c0;
            double tok_text = w_tape / 4.0;                         /* about 4 characters per token */
            double tok_shot_strike = w_frames * SHOT_TOKENS(W, H);  /* a screenshot each time something struck */
            double tok_shot_sec = dt * SHOT_TOKENS(W, H);           /* a screenshot every second */
            clock_txt(t, ts, sizeof ts);
            fprintf(stats, "%s cpu %.2f%% | damage %ld looks %ld strikes %ld (frames %ld) tuned-out %ld | px read %.1f%% of screen/s | "
                           "tokens: text %.0f, shots-on-strike %.0f, shot-per-sec %.0f\n",
                    ts, 100.0 * dc / dt, w_ev, w_looks, w_strikes, w_frames, w_habit, 100.0 * w_px / ((double)W * H * dt),
                    tok_text, tok_shot_strike, tok_shot_sec);
            FILE *nj = fopen(nowtmp, "w");
            if (nj) {
                char esc[700];
                int e = 0;
                for (const char *p = last_line; *p && e < (int)sizeof esc - 3; p++) { if (*p == '"' || *p == '\\') esc[e++] = '\\'; esc[e++] = *p; }
                esc[e] = 0;
                int habits = 0;
                for (int i = 0; i < ncell; i++) habits += cells[i].habit;
                fprintf(nj, "{\"at\":%ld,\"display\":\"%s\",\"grid\":\"%dx%d\",\"cpu_pct\":%.2f,\"damage\":%ld,\"looks\":%ld,\"strikes\":%ld,"
                            "\"tuned_out\":%d,\"tokens_text\":%.0f,\"tokens_shots_on_strike\":%.0f,\"tokens_shot_per_sec\":%.0f,\"window_s\":%.1f,\"last\":\"%s\"}\n",
                        (long)t, disp, GX, GY, 100.0 * dc / dt, w_ev, w_looks, w_strikes, habits, tok_text, tok_shot_strike, tok_shot_sec, dt, esc);
                fclose(nj);
                rename(nowtmp, nowpath);
            }
            win_t0 = t; win_c0 = cpu_s(); last_stats = t;
            w_ev = w_looks = w_strikes = w_frames = w_tape = w_px = w_habit = 0;
        }
    }
    double dt = now_s() - t0, dc = cpu_s() - c0;
    clock_txt(now_s(), ts, sizeof ts);
    fprintf(stats, "%s TOTAL %.0fs cpu %.2f%% | damage %ld looks %ld strikes %ld (frames %ld) tuned-out %ld | px read %.1f%% of screen/s | "
                   "tokens: text %.0f, shots-on-strike %.0f, shot-per-sec %.0f\n",
            ts, dt, 100.0 * dc / dt, ev_total, looks, strikes_total, frames_with_strike, habit_total, 100.0 * px_read / ((double)W * H * dt),
            tape_bytes / 4.0, frames_with_strike * SHOT_TOKENS(W, H), dt * SHOT_TOKENS(W, H));
    fprintf(tape, "%s perch: eye closed\n", ts);
    fclose(tape); fclose(vecs); fclose(stats);
    if (use_shm) { XShmDetach(dpy, &shm); shmdt(shm.shmaddr); }
    XCloseDisplay(dpy);
    return 0;
}
