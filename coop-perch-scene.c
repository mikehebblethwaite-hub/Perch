/* Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md. */
/* coop-perch-scene: a scripted 30 s screen for the bench (bots vs screenshots). Same events every run; each event's box and time
 * are written to the truth file as "unix_time x y w h name" so both bots can be scored against what really happened.
 *   coop-perch-scene [-d :2] [-o truth.txt]
 */
#include <X11/Xlib.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static Display *dpy; static Window win; static GC gc; static FILE *truth; static XFontStruct *font;
static double t0;
static double wall(void) { struct timespec t; clock_gettime(CLOCK_REALTIME, &t); return (double)t.tv_sec + t.tv_nsec / 1e9; }
static void at(double s) { double w = t0 + s - wall(); if (w > 0) usleep((useconds_t)(w * 1e6)); }
static void mark(int x, int y, int w, int h, const char *name) { XFlush(dpy); fprintf(truth, "%.3f %d %d %d %d %s\n", wall(), x, y, w, h, name); fflush(truth); }
static void fill(unsigned long c, int x, int y, int w, int h) { XSetForeground(dpy, gc, c); XFillRectangle(dpy, win, gc, x, y, (unsigned)w, (unsigned)h); }
static void text(unsigned long c, int x, int y, const char *s) { XSetForeground(dpy, gc, c); XDrawString(dpy, win, gc, x, y, s, (int)strlen(s)); }

static void page(int which) {                                         /* the whole "page": header, side menu, body */
    fill(0xf6f4ef, 0, 0, 1280, 800);
    fill(0x2d3a4a, 0, 0, 1280, 48); text(0xffffff, 20, 30, which ? "coop admin - orders" : "coop admin - home");
    fill(0xe6e1d6, 0, 48, 200, 752);
    const char *menu[] = {"Home", "Orders", "Pods", "Usage", "Settings"};
    for (int i = 0; i < 5; i++) text(0x2d3a4a, 20, 90 + i * 30, menu[i]);
    for (int i = 0; i < 12; i++) {
        char line[80];
        snprintf(line, sizeof line, which ? "Order %d   paid   12.%02d GBP" : "Note %d: everything quiet on the coop", i + 1, i * 7 % 100);
        text(0x333333, 240, 120 + i * 24, line);
    }
    fill(0xffffff, 240, 420, 600, 30); XSetForeground(dpy, gc, 0x999999); XDrawRectangle(dpy, win, gc, 240, 420, 600, 30);
    text(0x2d3a4a, 1000, 120, "Items: 3");
}

int main(int argc, char **argv) {
    const char *disp = ":2", *out = "truth.txt";
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "-d") && i + 1 < argc) disp = argv[++i];
        else if (!strcmp(argv[i], "-o") && i + 1 < argc) out = argv[++i];
    }
    dpy = XOpenDisplay(disp);
    if (!dpy) { fprintf(stderr, "coop-perch-scene: cannot open %s\n", disp); return 1; }
    truth = fopen(out, "w");
    if (!truth) { perror(out); return 1; }
    win = XCreateSimpleWindow(dpy, DefaultRootWindow(dpy), 0, 0, 1280, 800, 0, 0, 0xf6f4ef);
    XMapWindow(dpy, win);
    gc = XCreateGC(dpy, win, 0, NULL);
    font = XLoadQueryFont(dpy, "-misc-fixed-medium-r-normal--20-*-*-*-*-*-iso8859-1");
    if (!font) font = XLoadQueryFont(dpy, "fixed");
    if (font) XSetFont(dpy, gc, font->fid);
    XSync(dpy, False);
    usleep(500000);
    t0 = wall();
    page(0); XFlush(dpy);
    fprintf(truth, "%.3f 0 0 1280 800 start\n", wall());

    at(2);                                                            /* typing into the box, one key every 120 ms */
    const char *typed = "hello coop, deploy please";
    char buf[64] = "";
    for (int i = 0; typed[i]; i++) {
        at(2 + i * 0.12);
        buf[i] = typed[i]; buf[i + 1] = 0;
        fill(0xffffff, 241, 421, 598, 28); text(0x111111, 250, 442, buf);
        if (i == 0) mark(240, 420, 600, 30, "typing");
    }
    at(6);                                                            /* a dialog opens, then closes */
    fill(0xb0b0b0, 444, 304, 400, 200); fill(0xffffff, 440, 300, 400, 200); fill(0xc0392b, 440, 300, 400, 36);
    text(0xffffff, 456, 324, "Deploy failed"); text(0x333333, 460, 380, "exit 1: disk full on build-2");
    mark(440, 300, 404, 204, "dialog-open");
    at(9);
    page(0); fill(0xffffff, 241, 421, 598, 28); text(0x111111, 250, 442, buf);
    mark(440, 300, 404, 204, "dialog-close");
    at(11);                                                           /* a progress bar fills over 3 s */
    XSetForeground(dpy, gc, 0x999999); XDrawRectangle(dpy, win, gc, 240, 600, 600, 20);
    mark(240, 600, 601, 21, "progress");
    for (int i = 1; i <= 30; i++) { at(11 + i * 0.1); fill(0x27ae60, 241, 601, i * 20 - 1, 19); }
    at(16);                                                           /* one counter ticks once */
    fill(0xf6f4ef, 1000, 100, 160, 30); text(0x2d3a4a, 1000, 120, "Items: 4");
    mark(1000, 100, 160, 30, "counter");
    at(25);                                                           /* after a quiet spell, the whole page changes */
    page(1);
    mark(0, 0, 1280, 800, "page-load");
    at(28);                                                           /* a small icon blinks once */
    fill(0xe67e22, 1230, 10, 24, 24);
    mark(1230, 10, 24, 24, "blink-on");
    at(28.4);
    fill(0x2d3a4a, 1230, 10, 24, 24);
    mark(1230, 10, 24, 24, "blink-off");
    at(30);
    fprintf(truth, "%.3f 0 0 0 0 end\n", wall());
    fclose(truth);
    XCloseDisplay(dpy);
    return 0;
}
