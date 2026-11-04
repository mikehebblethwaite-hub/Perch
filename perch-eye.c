/* Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md. */
/* perch-eye: the local eye on a Perch display (default :2).
 *
 * The display (Xvfb :2, started by perch-display) is a real X screen: any app drawn on it thinks it is a monitor.
 * This eye never takes screenshots. It:
 *   1. asks the X server (DAMAGE extension) which rectangles changed: free change detection, no frame comparing;
 *   2. reads only the changed boxes (never whole cells) and keeps each grid cell's raw vector (mean R,G,B,luma, default grid 16x10)
 *      up to date by swapping the box's old colours for its new ones;
 *   3. a box whose pixels really changed = a strike; the eye zooms into that cell (quadtree, "look closer where it moved")
 *      until the change is pinned to a small box;
 *   4. habituation: a cell that keeps striking on a beat (clock, cursor, spinner) gets tuned out; a break in its beat counts again.
 * Vectors go to the box of bot vectors (shared memory, plane "eye", see perch-box.h) and the binary tape: raw, no tokens. Text is the bottom layer: one short line per strike frame, the same shape every time.
 * Every 10 s a stats line: the eye's own CPU, events, strikes, and the tokens the text tape would cost the bot,
 * next to what screenshots would have cost for the same moments.
 *
 * Build: make     Run: perch-eye [-d :2] [-g 16x10] [-o DIR] [-t SECONDS]
 */
#define _GNU_SOURCE
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <X11/extensions/Xdamage.h>
#include <X11/extensions/XShm.h>
#include <X11/extensions/Xfixes.h>
#include "perch-box.h"
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

#include "perch-core.h"

static volatile sig_atomic_t stop_now = 0;
static void on_stop(int s) { (void)s; stop_now = 1; }

/* ---- pixels: one shared-memory image the size of the screen, filled only for the rects asked ---- */
static Display *dpy = NULL;
static XImage *img = NULL;        /* current pixels (only dirty areas are fresh) */
static XShmSegmentInfo shm;
static int use_shm = 0;
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

/* ---- the windows: DAMAGE on each top-level window, like a compositor does. Damage on the root alone is useless under a
   compositing window manager (xfwm4, mutter...): it repaints the whole screen every frame, so every root damage is full screen. ---- */
typedef struct {
    Window w; Damage d; int x, y, wd, ht, mapped;
    int full_hits;                  /* whole-window damage in a row: 3+ = drawn by the graphics card, a whole new frame each time */
    int pend, px0, py0, px1, py1;   /* its damage held back until the next slow look */
    double last;
} Win;
#define GPU_LOOK 0.25               /* seconds between looks at a graphics-card window (others: FRAME_MS) */
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
    v->full_hits = 0; v->pend = 0; v->last = 0;
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
    int gx = 16, gy = 10;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "-d") && i + 1 < argc) disp = argv[++i];
        else if (!strcmp(argv[i], "-g") && i + 1 < argc) { if (sscanf(argv[++i], "%dx%d", &gx, &gy) != 2 || gx < 1 || gy < 1) { fprintf(stderr, "bad -g\n"); return 2; } }
        else if (!strcmp(argv[i], "-o") && i + 1 < argc) outdir = argv[++i];
        else if (!strcmp(argv[i], "-t") && i + 1 < argc) run_for = atof(argv[++i]);
        else { fprintf(stderr, "usage: %s [-d :2] [-g 16x10] [-o DIR] [-t SECONDS]\n", argv[0]); return 2; }
    }
    char dir[1024];
    if (outdir) snprintf(dir, sizeof dir, "%s", outdir);
    else snprintf(dir, sizeof dir, "%s/perch/tape", getenv("HOME") ? getenv("HOME") : ".");

    dpy = XOpenDisplay(disp);
    if (!dpy) { fprintf(stderr, "perch-eye: cannot open display %s (is perch-display running?)\n", disp); return 1; }
    int scr = DefaultScreen(dpy);
    root = RootWindow(dpy, scr);
    int sw = DisplayWidth(dpy, scr), sh = DisplayHeight(dpy, scr);
    if (DefaultDepth(dpy, scr) < 24) { fprintf(stderr, "perch-eye: display depth %d, need 24\n", DefaultDepth(dpy, scr)); return 1; }

    int dmg_ev = 0, dmg_err = 0, fx_ev = 0, fx_err = 0;
    if (!XDamageQueryExtension(dpy, &dmg_ev, &dmg_err)) { fprintf(stderr, "perch-eye: display has no DAMAGE extension\n"); return 1; }
    if (!XFixesQueryExtension(dpy, &fx_ev, &fx_err)) { fprintf(stderr, "perch-eye: display has no XFIXES extension\n"); return 1; }

    /* full-screen image buffer (ordinary memory) + shared memory for the actual reads; the core reads the image in place */
    img = XCreateImage(dpy, DefaultVisual(dpy, scr), DefaultDepth(dpy, scr), ZPixmap, 0, calloc((size_t)sw * sh, 4), (unsigned)sw, (unsigned)sh, 32, sw * 4);
    if (!img || !img->data) { fprintf(stderr, "perch-eye: out of memory\n"); return 1; }
    cur = (uint8_t *)img->data; cur_stride = (size_t)img->bytes_per_line;
    if (XShmQueryExtension(dpy)) {
        shm.shmid = shmget(IPC_PRIVATE, (size_t)sw * sh * 4, IPC_CREAT | 0600);
        if (shm.shmid >= 0) {
            shm.shmaddr = shmat(shm.shmid, NULL, 0);
            shm.readOnly = False;
            if (shm.shmaddr != (char *)-1 && XShmAttach(dpy, &shm)) { XSync(dpy, False); shmctl(shm.shmid, IPC_RMID, NULL); use_shm = 1; }
        }
    }

    PerchEye eye;
    if (perch_open(&eye, dir, "eye", disp, "eye", gx, gy) != 0) return 1;
    if (perch_screen(&eye, sw, sh, 0) != 0) return 1;

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

    signal(SIGINT, on_stop); signal(SIGTERM, on_stop);
    char ts[16];
    double t0 = perch_now();

    /* first look: whole screen, so every cell has a vector to compare against */
    grab(0, 0, W, H);
    perch_first_look(&eye, t0);

    struct pollfd pfd = {.fd = ConnectionNumber(dpy), .events = POLLIN};
    while (!stop_now) {
        double t = perch_now();
        if (run_for > 0 && t - t0 >= run_for) break;
        int held = 0;
        for (int i = 0; i < nwins; i++) held |= wins[i].pend;
        int wait = eye.pending ? (int)fmax(0, FRAME_MS - (t - eye.last_look) * 1000) : held ? (int)(GPU_LOOK * 1000) : 1000;
        if (!XPending(dpy)) poll(&pfd, 1, wait);
        while (XPending(dpy)) {
            XEvent e;
            XNextEvent(dpy, &e);
            if (e.type == dmg_ev + XDamageNotify) {
                XDamageNotifyEvent *d = (XDamageNotifyEvent *)&e;
                if (d->damage == rootd) {
                    /* full-screen root damage = a compositor repainting; ignore it from then on, the windows' own damage says where */
                    if (d->area.width >= W && d->area.height >= H) {
                        if (!root_full) { root_full = 1; clock_txt(perch_now(), ts, sizeof ts); fprintf(eye.tape, "%s O windows-only\n", ts); }
                        continue;
                    }
                    if (root_full) continue;
                    perch_damage(&eye, d->area.x, d->area.y, d->area.x + d->area.width, d->area.y + d->area.height);
                    continue;
                }
                Win *v = win_find(d->drawable);
                if (!v || !v->mapped) continue;
                int ax0 = v->x + d->area.x, ay0 = v->y + d->area.y, ax1 = ax0 + d->area.width, ay1 = ay0 + d->area.height;
                long area = (long)d->area.width * d->area.height, whole = (long)v->wd * v->ht;
                v->full_hits = whole > 0 && area * 10 >= whole * 9 ? v->full_hits + 1 : 0;
                if (v->full_hits >= 3) {                              /* a graphics-card window: hold its damage for the slow look */
                    eye.ev_total++; eye.w_ev++;
                    if (!v->pend) { v->px0 = ax0; v->py0 = ay0; v->px1 = ax1; v->py1 = ay1; v->pend = 1; }
                    else {
                        if (ax0 < v->px0) v->px0 = ax0;
                        if (ay0 < v->py0) v->py0 = ay0;
                        if (ax1 > v->px1) v->px1 = ax1;
                        if (ay1 > v->py1) v->py1 = ay1;
                    }
                    continue;
                }
                perch_damage(&eye, ax0, ay0, ax1, ay1);
            } else if (e.type == CreateNotify) {
                if (e.xcreatewindow.parent == root) win_add(e.xcreatewindow.window);
            } else if (e.type == DestroyNotify) {
                win_drop(e.xdestroywindow.window); eye.pending = 1;
            } else if (e.type == ReparentNotify) {
                if (e.xreparent.parent == root) win_add(e.xreparent.window); else win_drop(e.xreparent.window);
                eye.pending = 1;
            } else if (e.type == MapNotify || e.type == UnmapNotify) {
                Win *v = win_find(e.type == MapNotify ? e.xmap.window : e.xunmap.window);
                if (v) { v->mapped = e.type == MapNotify; mark(v->x, v->y, v->x + v->wd, v->y + v->ht); eye.pending = 1; }
            } else if (e.type == ConfigureNotify) {
                Win *v = win_find(e.xconfigure.window);
                if (v) {
                    if (v->mapped) mark(v->x, v->y, v->x + v->wd, v->y + v->ht);           /* where it was */
                    v->x = e.xconfigure.x; v->y = e.xconfigure.y;
                    v->wd = e.xconfigure.width + 2 * e.xconfigure.border_width; v->ht = e.xconfigure.height + 2 * e.xconfigure.border_width;
                    if (v->mapped) { mark(v->x, v->y, v->x + v->wd, v->y + v->ht); eye.pending = 1; }   /* where it is now */
                }
            }
        }
        t = perch_now();
        for (int i = 0; i < nwins; i++)                                /* graphics-card windows: their held damage, 4 times a second */
            if (wins[i].pend && t - wins[i].last >= GPU_LOOK) {
                if (mark(wins[i].px0, wins[i].py0, wins[i].px1, wins[i].py1)) eye.pending = 1;
                wins[i].pend = 0; wins[i].last = t;
            }
        perch_look(&eye, t, grab);                                     /* the look: read only the dirty cells, vectors, strike, zoom */
        perch_stats(&eye, perch_now(), 0);
    }
    perch_stats(&eye, perch_now(), 1);
    perch_close(&eye);
    if (use_shm) { XShmDetach(dpy, &shm); shmdt(shm.shmaddr); }
    XCloseDisplay(dpy);
    return 0;
}
