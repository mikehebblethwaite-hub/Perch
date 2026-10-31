/* Copyright (c) 2026 Mike J Hebblethwaite. All rights reserved. See COPYRIGHT. */
/* coop-perch-shotbot: the way bots look at screens today, for the bench. Every PERIOD ms it takes a full screenshot and encodes it
 * as PNG (what would be sent to the model), and logs "unix_time png_bytes w h". Connect it to the real X server (:12), not the tap.
 *   coop-perch-shotbot [-d :12] [-p 1000] [-t 32] [-o shots.txt]
 */
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <zlib.h>

static double wall(void) { struct timespec t; clock_gettime(CLOCK_REALTIME, &t); return (double)t.tv_sec + t.tv_nsec / 1e9; }

int main(int argc, char **argv) {
    const char *disp = ":12", *out = "shots.txt";
    int period = 1000;
    double run = 32;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "-d") && i + 1 < argc) disp = argv[++i];
        else if (!strcmp(argv[i], "-p") && i + 1 < argc) period = atoi(argv[++i]);
        else if (!strcmp(argv[i], "-t") && i + 1 < argc) run = atof(argv[++i]);
        else if (!strcmp(argv[i], "-o") && i + 1 < argc) out = argv[++i];
    }
    Display *dpy = XOpenDisplay(disp);
    if (!dpy) { fprintf(stderr, "coop-perch-shotbot: cannot open %s\n", disp); return 1; }
    FILE *log = fopen(out, "w");
    if (!log) return 1;
    int scr = DefaultScreen(dpy), W = DisplayWidth(dpy, scr), H = DisplayHeight(dpy, scr);
    size_t raw_n = (size_t)H * (1 + 3 * (size_t)W);
    unsigned char *raw = malloc(raw_n);
    uLongf zcap = compressBound((uLong)raw_n);
    unsigned char *z = malloc(zcap);
    if (!raw || !z) return 1;
    double t0 = wall(), next = t0;
    while (wall() - t0 < run) {
        XImage *img = XGetImage(dpy, RootWindow(dpy, scr), 0, 0, (unsigned)W, (unsigned)H, AllPlanes, ZPixmap);
        if (!img) return 1;
        for (int r = 0; r < H; r++) {
            unsigned char *row = raw + (size_t)r * (1 + 3 * (size_t)W);
            const uint32_t *src = (const uint32_t *)(img->data + (size_t)r * img->bytes_per_line);
            row[0] = 0;
            for (int c = 0; c < W; c++) { uint32_t p = src[c]; row[1 + 3 * c] = (p >> 16) & 255; row[2 + 3 * c] = (p >> 8) & 255; row[3 + 3 * c] = p & 255; }
        }
        XDestroyImage(img);
        uLongf zn = zcap;
        compress2(z, &zn, raw, (uLong)raw_n, 6);                     /* PNG's deflate, the usual level */
        fprintf(log, "%.3f %lu %d %d\n", wall(), (unsigned long)zn, W, H);
        fflush(log);
        next += period / 1000.0;
        double w = next - wall();
        if (w > 0) usleep((useconds_t)(w * 1e6));
    }
    fclose(log);
    return 0;
}
