/* Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md. */
/* perch-hen: one hen pecking in a small window, for testing the Perch eye. Drag it anywhere.
 * The peck is 11 fixed frames of 50 ms (0.55 s), drawn exactly the same every time, so its memory repeats exactly: the eye has
 * to find it by that beat alone, never by asking where the window is.
 * Build: make     Run: perch-hen [-d :0]
 */
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <stdio.h>
#include <string.h>
#include <sys/select.h>
#include <time.h>

#define FRAMES 11
#define FRAME_MS 50
static const int dip[FRAMES] = {0, 4, 9, 15, 21, 26, 21, 15, 9, 4, 0};   /* how far the head goes down, per frame */

static double now_s(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return (double)t.tv_sec + t.tv_nsec / 1e9; }

int main(int argc, char **argv) {
    const char *disp = ":0";
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "-d") && i + 1 < argc) disp = argv[++i];
        else { fprintf(stderr, "usage: %s [-d :0]\n", argv[0]); return 2; }
    }
    Display *dpy = XOpenDisplay(disp);
    if (!dpy) { fprintf(stderr, "perch-hen: cannot open screen %s\n", disp); return 1; }
    int scr = DefaultScreen(dpy), W = 160, H = 140;
    Window win = XCreateSimpleWindow(dpy, RootWindow(dpy, scr), 300, 300, (unsigned)W, (unsigned)H, 0, 0, 0xf4efe2);
    XStoreName(dpy, win, "the hen");
    XSizeHints *sh = XAllocSizeHints();
    if (sh) { sh->flags = PMinSize | PMaxSize; sh->min_width = sh->max_width = W; sh->min_height = sh->max_height = H; XSetWMNormalHints(dpy, win, sh); XFree(sh); }
    Atom del = XInternAtom(dpy, "WM_DELETE_WINDOW", False);
    XSetWMProtocols(dpy, win, &del, 1);
    XSelectInput(dpy, win, StructureNotifyMask);
    XMapWindow(dpy, win);
    GC gc = XCreateGC(dpy, win, 0, NULL);
    Pixmap buf = XCreatePixmap(dpy, win, (unsigned)W, (unsigned)H, (unsigned)DefaultDepth(dpy, scr));   /* each frame drawn whole, then shown */
    int f = 0;
    double next = now_s();
    for (;;) {
        while (XPending(dpy)) {
            XEvent e;
            XNextEvent(dpy, &e);
            if (e.type == ClientMessage && (Atom)e.xclient.data.l[0] == del) { XCloseDisplay(dpy); return 0; }
            if (e.type == DestroyNotify) return 0;
        }
        if (now_s() < next) goto wait;                                /* woken early by window events (a drag): not time yet */
        int d = dip[f];
        XSetForeground(dpy, gc, 0xf4efe2); XFillRectangle(dpy, buf, gc, 0, 0, (unsigned)W, (unsigned)H);          /* sky */
        XSetForeground(dpy, gc, 0x9c7a4a); XFillRectangle(dpy, buf, gc, 0, 118, (unsigned)W, 22);                  /* ground */
        XSetForeground(dpy, gc, 0xe0b23c); XFillArc(dpy, buf, gc, 112, 116, 6, 4, 0, 360 * 64);                    /* a grain */
        XSetForeground(dpy, gc, 0xd8892a);                                                                       /* legs */
        XFillRectangle(dpy, buf, gc, 58, 96, 4, 22); XFillRectangle(dpy, buf, gc, 74, 96, 4, 22);
        XSetForeground(dpy, gc, 0xb5562b); XFillArc(dpy, buf, gc, 30, 52, 76, 52, 0, 360 * 64);                    /* body */
        XSetForeground(dpy, gc, 0x8e3f1f); XFillArc(dpy, buf, gc, 22, 46, 30, 30, 0, 360 * 64);                    /* tail */
        XSetForeground(dpy, gc, 0xb5562b); XFillRectangle(dpy, buf, gc, 88, 48 + d / 2, 12, 22);                   /* neck */
        XFillArc(dpy, buf, gc, 86, 30 + d, 28, 26, 0, 360 * 64);                                                 /* head */
        XSetForeground(dpy, gc, 0xd8262a); XFillArc(dpy, buf, gc, 92, 22 + d, 14, 12, 0, 360 * 64);                /* comb */
        XSetForeground(dpy, gc, 0xe8a317);                                                                       /* beak */
        XPoint beak[3] = {{112, 40 + d}, {124, 45 + d}, {112, 49 + d}};
        XFillPolygon(dpy, buf, gc, beak, 3, Convex, CoordModeOrigin);
        XSetForeground(dpy, gc, 0x1a1a1a); XFillArc(dpy, buf, gc, 102, 37 + d, 4, 4, 0, 360 * 64);                 /* eye */
        XCopyArea(dpy, buf, win, gc, 0, 0, (unsigned)W, (unsigned)H, 0, 0);
        XFlush(dpy);
        f = (f + 1) % FRAMES;
        next += FRAME_MS / 1000.0;
        if (next < now_s() - 0.5) next = now_s();                     /* fell behind (screen busy): carry on from now */
    wait:;
        double w = next - now_s();
        if (w > 0) {
            fd_set fs;
            FD_ZERO(&fs);
            FD_SET(ConnectionNumber(dpy), &fs);
            struct timeval tv = {0, (long)(w * 1e6)};
            select(ConnectionNumber(dpy) + 1, &fs, NULL, NULL, &tv);
        }
    }
}
