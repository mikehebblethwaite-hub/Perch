/* Copyright (c) 2026 Mike J Hebblethwaite. All rights reserved. See COPYRIGHT. */
/* coop-perch-cam: the Perch eye on a camera's video, in memory. No browser, no display, no screenshots.
 *
 * Frames come in as raw greyscale bytes (1 byte a pixel) on stdin, decoded from the camera's data by ffmpeg, e.g.
 *   ffmpeg -rtsp_transport tcp -i rtsp://... -f rawvideo -pix_fmt gray -s 640x360 - | coop-perch-cam -s 640x360
 * Each grid cell keeps a slow background (the scene when nothing moves). A cell strikes when enough of its pixels differ from the
 * background by more than the threshold, so compression shimmer and noise do not count. A struck cell is zoomed (quadtree) to the
 * box that moved. Bots get one line when movement starts or moves on:  M x,y wxh n=CELLS  (and Q when it goes quiet), on stdout
 * and the tape; and the plane "cam" in the box of bot vectors: rows x cols x 3 (u8: brightness, motion 0..255, strike 0/1).
 *   coop-perch-cam -s WxH [-g 16x9] [-T 18 (pixel threshold)] [-p 3 (percent of a cell)] [-o DIR]
 */
#define _GNU_SOURCE
#include "coop-perch-box.h"
#include <sys/resource.h>

static double now_s(void) { struct timespec t; clock_gettime(CLOCK_REALTIME, &t); return (double)t.tv_sec + t.tv_nsec / 1e9; }
static double cpu_s(void) {
    struct rusage r;
    getrusage(RUSAGE_SELF, &r);
    return (double)r.ru_utime.tv_sec + r.ru_utime.tv_usec / 1e6 + (double)r.ru_stime.tv_sec + r.ru_stime.tv_usec / 1e6;
}

static int W, H, T = 18;
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
    if (W <= 0 || H <= 0 || GX < 1 || GY < 1) { fprintf(stderr, "coop-perch-cam: give the frame size, -s WxH\n"); return 2; }
    size_t n = (size_t)W * H;
    fr = malloc(n); bg = malloc(n);
    unsigned char *acc = calloc(n, 2);                                  /* background as 8.8 fixed point, for a slow average */
    uint16_t *bgf = (uint16_t *)acc;
    if (!fr || !bg || !acc) return 1;
    char dir[1024], path[1200];
    if (outdir) snprintf(dir, sizeof dir, "%s", outdir); else snprintf(dir, sizeof dir, "%s/coop-perch/tape", getenv("HOME") ? getenv("HOME") : ".");
    mkdir(dir, 0750);
    snprintf(path, sizeof path, "%s/cam.txt", dir);
    FILE *tape = fopen(path, "a");
    if (tape) setvbuf(tape, NULL, _IOLBF, 0);
    CoopBox box;
    uint32_t bdim[3] = {(uint32_t)GY, (uint32_t)GX, 3};
    char bf[300];
    snprintf(bf, sizeof bf, "brightness motion strike; per grid cell, u8; motion = share of the cell's pixels that moved, 0..255; frame %dx%d; "
                            "a frame per video frame with movement", W, H);
    int have_box = coop_box_create(&box, "cam", "u8", 3, bdim, bf) == 0;
    int cw = W / GX, ch = H / GY;
    int need = cw * ch * pct / 100;
    if (need < 1) need = 1;
    long frames = 0, mframes = 0, lines = 0;
    double t0 = now_s(), c0 = cpu_s();
    int was = 0, lx0 = -1, ly0 = -1, lx1 = -1, ly1 = -1;
    setvbuf(stdout, NULL, _IOLBF, 0);
    while (fread(fr, 1, n, stdin) == n) {
        frames++;
        if (frames == 1) { memcpy(bg, fr, n); for (size_t i = 0; i < n; i++) bgf[i] = (uint16_t)(fr[i] << 8); continue; }
        int bx0 = W, by0 = H, bx1 = 0, by1 = 0, ncell = 0;
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
            cellv[c * 3 + 2] = cnt >= need;
            if (cnt >= need) { zoom(x, y, cw, ch, 0, &bx0, &by0, &bx1, &by1); ncell++; }
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
            if (have_box) { uint8_t *o = coop_box_next(&box, now_s()); if (o) { memcpy(o, cellv, (size_t)ncells * 3); coop_box_commit(&box); } }
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
    fprintf(stderr, "coop-perch-cam: %ld frames (%dx%d), %ld with movement, %ld lines, cpu %.2f s (%.2f%% of a core)\n",
            frames, W, H, mframes, lines, dc, dt > 0 ? 100 * dc / dt : 0);
    return 0;
}
