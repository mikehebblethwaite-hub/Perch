/* coop-perch-eye: the local eye on a Perch display (default :2).
 *
 * The display (Xvfb :2, started by coop-perch-display) is a real X screen: any app drawn on it thinks it is a monitor.
 * This eye never takes screenshots. It:
 *   1. asks the X server (DAMAGE extension) which rectangles changed: free change detection, no frame comparing;
 *   2. reads only the changed boxes (never whole cells) and keeps each grid cell's raw vector (mean R,G,B,luma, default grid 16x10)
 *      up to date by swapping the box's old colours for its new ones;
 *   3. a box whose pixels really changed = a strike; the eye zooms into that cell (quadtree, "look closer where it moved")
 *      until the change is pinned to a small box;
 *   4. habituation: a cell that keeps striking on a beat (clock, cursor, spinner) gets tuned out; a break in its beat counts again.
 * Vectors go to the box of bot vectors (shared memory, plane "eye", see coop-perch-box.h) and the binary tape: raw, no tokens. Text is the bottom layer: one short line per strike frame, the same shape every time.
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
#include "coop-perch-box.h"
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
    int dx0, dy0, dx1, dy1;       /* the changed box inside this cell since the last look (dx1 <= dx0 = none) */
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
   that differ. GPU apps (browsers, Electron) damage their whole window every frame, so this, not DAMAGE, finds where. */
static int tight_box(int x, int y, int w, int h, int *tx0, int *ty0, int *tx1, int *ty1) {
    int found = 0;
    for (int yy = y; yy < y + h; yy++) {
        const uint32_t *a = (const uint32_t *)(img->data + (size_t)yy * img->bytes_per_line) + x, *b = prev + (size_t)yy * W + x;
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

/* ---- the grid, and marking what changed ---- */
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
    if (x1 <= x0 || y1 <= y0) return 0;
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

/* ---- the windows: DAMAGE on each top-level window, like a compositor does. Damage on the root alone is useless under a
   compositing window manager (xfwm4, mutter...): it repaints the whole screen every frame, so every root damage is full screen. ---- */
typedef struct { Window w; Damage d; int x, y, wd, ht, mapped; } Win;
static Win *wins = NULL;
static int nwins = 0, capwins = 0;

static int x_error(Display *d, XErrorEvent *e) { (void)d; (void)e; return 0; }   /* a window that went away mid-call is normal: ignore */

static Win *win_find(Window w) {
    for (int i = 0; i < nwins; i++) if (wins[i].w == w) return &wins[i];
    return NULL;
}
static void win_add(Window w) {
    if (win_find(w)) return;
    XWindowAttributes a;
    if (!XGetWindowAttributes(dpy, w, &a) || a.class == InputOnly) return;
    if (nwins == capwins) {
        int nc = capwins ? capwins * 2 : 64;
        Win *nw = realloc(wins, (size_t)nc * sizeof(Win));
        if (!nw) return;
        wins = nw; capwins = nc;
    }
    Win *v = &wins[nwins++];
    v->w = w; v->x = a.x; v->y = a.y; v->wd = a.width + 2 * a.border_width; v->ht = a.height + 2 * a.border_width;
    v->mapped = a.map_state == IsViewable;
    v->d = XDamageCreate(dpy, w, XDamageReportRawRectangles);
}
static void win_drop(Window w) {
    for (int i = 0; i < nwins; i++)
        if (wins[i].w == w) {
            if (wins[i].mapped) mark(wins[i].x, wins[i].y, wins[i].x + wins[i].wd, wins[i].y + wins[i].ht);   /* what was under it shows again */
            wins[i] = wins[--nwins];
            return;                                                  /* its Damage went with the window */
        }
}

int main(int argc, char **argv) {
    const char *disp = ":2", *outdir = NULL;
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


    int ncell = GX * GY;
    cells = calloc((size_t)ncell, sizeof(Cell));
    dirty = calloc((size_t)ncell, 1);
    float *zb = calloc((size_t)ncell * 5, sizeof(float));      /* this look: per cell strike (1 strike, 2 tuned-out change) + zoom box */
    if (!cells || !dirty || !zb) { fprintf(stderr, "coop-perch-eye: out of memory\n"); return 1; }
    cw = W / GX; ch = H / GY;   /* last row/column take the remainder */

    CoopBox box;                                                     /* the eye plane: rows x cols x 9 */
    uint32_t bdim[3] = {(uint32_t)GY, (uint32_t)GX, 9};
    char bfields[300];
    snprintf(bfields, sizeof bfields, "R G B luma strike zoom_x zoom_y zoom_w zoom_h; colours 0..255 cell means; strike 0 none 1 strike 2 tuned-out cell changed; "
                                      "zoom box 0..1 of the screen; screen %dx%d display %s; a frame per look that saw a change", W, H, disp);
    int have_box = coop_box_create(&box, "eye", 3, bdim, bfields) == 0;
    if (!have_box) fprintf(stderr, "coop-perch-eye: no box in %s (going on without it)\n", coop_box_dir());

    /* watch the top-level windows (and the root, for anything drawn straight onto it), hear windows come, go and move */
    XSetErrorHandler(x_error);
    XSelectInput(dpy, root, SubstructureNotifyMask);
    XGrabServer(dpy);
    Window rr, rp, *kids = NULL;
    unsigned int nk = 0;
    if (XQueryTree(dpy, root, &rr, &rp, &kids, &nk))
        for (unsigned int i = 0; i < nk; i++) win_add(kids[i]);
    if (kids) XFree(kids);
    XUngrabServer(dpy);
    int root_full = 0;                                                /* the root's own damage, unless a compositor floods it */
    Damage rootd = XDamageCreate(dpy, root, XDamageReportRawRectangles);
    XSync(dpy, False);

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
        for (int c = 0; c < GX; c++) {
            Cell *k = &cells[r * GX + c];
            box_sum(CX0(c), CY0(r), CW(c), CH(r), 0, 1.0, k->sum);
            sum_vector(k->sum, (double)CW(c) * CH(r), k->v);
            k->have = 1; k->dx1 = k->dx0 = 0;
        }
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
                if (d->damage == rootd) {
                    /* full-screen root damage = a compositor repainting; ignore it from then on, the windows' own damage says where */
                    if (d->area.width >= W && d->area.height >= H) {
                        if (!root_full) { root_full = 1; clock_txt(now_s(), ts, sizeof ts); fprintf(tape, "%s perch: compositor repaints the whole screen, watching windows only\n", ts); }
                        continue;
                    }
                    if (root_full) continue;
                    ev_total++; w_ev++;
                    if (mark(d->area.x, d->area.y, d->area.x + d->area.width, d->area.y + d->area.height)) pending = 1;
                    continue;
                }
                Win *v = win_find(d->drawable);
                if (!v || !v->mapped) continue;
                ev_total++; w_ev++;
                if (mark(v->x + d->area.x, v->y + d->area.y, v->x + d->area.x + d->area.width, v->y + d->area.y + d->area.height)) pending = 1;
            } else if (e.type == CreateNotify) {
                if (e.xcreatewindow.parent == root) win_add(e.xcreatewindow.window);
            } else if (e.type == DestroyNotify) {
                win_drop(e.xdestroywindow.window); pending = 1;
            } else if (e.type == ReparentNotify) {
                if (e.xreparent.parent == root) win_add(e.xreparent.window); else win_drop(e.xreparent.window);
                pending = 1;
            } else if (e.type == MapNotify || e.type == UnmapNotify) {
                Win *v = win_find(e.type == MapNotify ? e.xmap.window : e.xunmap.window);
                if (v) { v->mapped = e.type == MapNotify; mark(v->x, v->y, v->x + v->wd, v->y + v->ht); pending = 1; }
            } else if (e.type == ConfigureNotify) {
                Win *v = win_find(e.xconfigure.window);
                if (v) {
                    if (v->mapped) mark(v->x, v->y, v->x + v->wd, v->y + v->ht);           /* where it was */
                    v->x = e.xconfigure.x; v->y = e.xconfigure.y;
                    v->wd = e.xconfigure.width + 2 * e.xconfigure.border_width; v->ht = e.xconfigure.height + 2 * e.xconfigure.border_width;
                    if (v->mapped) { mark(v->x, v->y, v->x + v->wd, v->y + v->ht); pending = 1; }   /* where it is now */
                }
            }
        }
        t = now_s();
        if (pending && (t - last_look) * 1000 >= FRAME_MS) {
            /* the look: read only the dirty cells, make their vectors, strike, zoom */
            last_look = t; pending = 0; looks++; w_looks++;
            char line[4000];
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
            int read_ok = ux1 > ux0 && uy1 > uy0 && grab(ux0, uy0, ux1 - ux0, uy1 - uy0) == 0;
            if (read_ok) { px_read += (long)(ux1 - ux0) * (uy1 - uy0); w_px += (long)(ux1 - ux0) * (uy1 - uy0); }
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
                {                                                      /* pixels did change in the box: a strike */
                    zoom(x, y, w, h, 0, &bx0, &by0, &bx1, &by1, &md);
                    int hab = habit_check(&cells[i], t);
                    nreal++;
                    zb[i * 5] = (hab == 0 || hab == 4) ? 1.0f : 2.0f;
                    if (bx1 > bx0) { zb[i * 5 + 1] = (float)bx0 / W; zb[i * 5 + 2] = (float)by0 / H; zb[i * 5 + 3] = (float)(bx1 - bx0) / W; zb[i * 5 + 4] = (float)(by1 - by0) / H; }
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
            }
            if (nreal && have_box) {                                    /* the whole grid, as it is now, into the box */
                float *bf = coop_box_next(&box, t);
                for (int i = 0; i < ncell; i++) { memcpy(bf + (size_t)i * 9, cells[i].v, VEC * sizeof(float)); memcpy(bf + (size_t)i * 9 + 4, zb + (size_t)i * 5, 5 * sizeof(float)); }
                coop_box_commit(&box);
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
