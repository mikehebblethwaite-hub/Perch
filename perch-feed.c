/* Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md. */
/* perch-feed: draws a machine's memory onto a Perch display, live, as raw blocks (no text needed to see it change).
 *
 * Reads blocks from stdin, one per second, as sent by perch-feed-two:
 *   /proc/meminfo lines, then "--", then "RSS_KB NAME" lines for the biggest processes, then "==" to end the block.
 * Draws on the display (default :2), a 1280x800 window over the whole screen:
 *   top:    the memory map, one block per slice of RAM: used (red), buffers/cache (blue), free (dark)
 *   bottom: the 16 biggest processes as bars, length by RSS
 *   corner: a ticking seconds square (the eye should learn it as a habit and tune it out)
 * Build: make     Run: perch-feed-two | perch-feed [-d :2]
 */
#define _GNU_SOURCE
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define MAPCOLS 64
#define MAPROWS 32
#define NPROC 16

static unsigned long rgb(int r, int g, int b) { return ((unsigned long)r << 16) | ((unsigned long)g << 8) | (unsigned long)b; }

int main(int argc, char **argv) {
    const char *disp = ":2";
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "-d") && i + 1 < argc) disp = argv[++i];
        else { fprintf(stderr, "usage: perch-feed-two | %s [-d :2]\n", argv[0]); return 2; }
    }
    Display *dpy = XOpenDisplay(disp);
    if (!dpy) { fprintf(stderr, "perch-feed: cannot open display %s\n", disp); return 1; }
    int scr = DefaultScreen(dpy);
    int W = DisplayWidth(dpy, scr), H = DisplayHeight(dpy, scr);
    Window win = XCreateSimpleWindow(dpy, RootWindow(dpy, scr), 0, 0, (unsigned)W, (unsigned)H, 0, 0, rgb(12, 12, 16));
    XStoreName(dpy, win, "perch-feed: memory");
    XMapWindow(dpy, win);
    GC gc = XCreateGC(dpy, win, 0, NULL);
    XFontStruct *font = XLoadQueryFont(dpy, "fixed");      /* labels are a bonus; a display without fonts still gets the blocks */
    if (font) XSetFont(dpy, gc, font->fid);
    XSync(dpy, False);

    int mapH = H / 2, bw = W / MAPCOLS, bh = mapH / MAPROWS;
    int prev_state[MAPCOLS * MAPROWS];
    for (int i = 0; i < MAPCOLS * MAPROWS; i++) prev_state[i] = -1;
    char line[512];
    long total = 0, freek = 0, buffers = 0, cached = 0, sreclaim = 0;
    long prss[NPROC];
    char pname[NPROC][64];
    int np = 0, part = 0;
    long prev_rss[NPROC];
    char prev_name[NPROC][64];
    for (int i = 0; i < NPROC; i++) { prev_rss[i] = -1; prev_name[i][0] = 0; }

    while (fgets(line, sizeof line, stdin)) {
        if (!strncmp(line, "--", 2) && line[2] != '-') { part = 1; np = 0; continue; }
        if (!strncmp(line, "==", 2)) {
            /* draw this block: only what changed (that is what the display reports as damage) */
            if (total > 0) {
                long used = total - freek - buffers - cached - sreclaim, cache = buffers + cached + sreclaim;
                int n = MAPCOLS * MAPROWS;
                int nu = (int)((double)used / total * n + 0.5), nc = (int)((double)cache / total * n + 0.5);
                for (int i = 0; i < n; i++) {
                    int st = i < nu ? 0 : (i < nu + nc ? 1 : 2);
                    if (st == prev_state[i]) continue;
                    prev_state[i] = st;
                    XSetForeground(dpy, gc, st == 0 ? rgb(200, 60, 50) : st == 1 ? rgb(50, 90, 190) : rgb(28, 28, 34));
                    XFillRectangle(dpy, win, gc, (i % MAPCOLS) * bw + 1, (i / MAPCOLS) * bh + 1, (unsigned)(bw - 2), (unsigned)(bh - 2));
                }
            }
            long maxr = 1;
            for (int i = 0; i < np; i++) if (prss[i] > maxr) maxr = prss[i];
            int rowh = (H - mapH - 20) / NPROC;
            for (int i = 0; i < NPROC; i++) {
                long r = i < np ? prss[i] : 0;
                const char *nm = i < np ? pname[i] : "";
                if (r == prev_rss[i] && !strcmp(nm, prev_name[i])) continue;
                prev_rss[i] = r;
                snprintf(prev_name[i], sizeof prev_name[i], "%s", nm);
                int y = mapH + 10 + i * rowh;
                XSetForeground(dpy, gc, rgb(12, 12, 16));
                XFillRectangle(dpy, win, gc, 0, y, (unsigned)W, (unsigned)rowh);
                XSetForeground(dpy, gc, rgb(70, 160, 90));
                XFillRectangle(dpy, win, gc, 200, y + 3, (unsigned)((double)r / maxr * (W - 320)), (unsigned)(rowh - 6));
                if (font) {
                    char lab[120];
                    XSetForeground(dpy, gc, rgb(220, 220, 220));
                    XDrawString(dpy, win, gc, 8, y + rowh - 6, nm, (int)strlen(nm));
                    snprintf(lab, sizeof lab, "%ld MB", r / 1024);
                    XDrawString(dpy, win, gc, W - 110, y + rowh - 6, lab, (int)strlen(lab));
                }
            }
            /* the habit: a square that flips every second */
            XSetForeground(dpy, gc, (time(NULL) & 1) ? rgb(230, 200, 60) : rgb(60, 50, 20));
            XFillRectangle(dpy, win, gc, W - 30, H - 30, 20, 20);
            XFlush(dpy);
            part = 0; total = freek = buffers = cached = sreclaim = 0;
            continue;
        }
        if (part == 0) {
            char key[64];
            long val;
            if (sscanf(line, "%63[^:]: %ld", key, &val) == 2) {
                if (!strcmp(key, "MemTotal")) total = val;
                else if (!strcmp(key, "MemFree")) freek = val;
                else if (!strcmp(key, "Buffers")) buffers = val;
                else if (!strcmp(key, "Cached")) cached = val;
                else if (!strcmp(key, "SReclaimable")) sreclaim = val;
            }
        } else if (np < NPROC) {
            long r;
            char nm[64];
            if (sscanf(line, "%ld %63s", &r, nm) == 2) { prss[np] = r; snprintf(pname[np], sizeof pname[np], "%s", nm); np++; }
        }
    }
    XCloseDisplay(dpy);
    return 0;
}
