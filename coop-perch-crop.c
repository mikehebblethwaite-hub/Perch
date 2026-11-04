/* Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md. */
/* coop-perch-crop: save one box of the screen as a small PNG (the fovea: only where it changed, never the whole screen).
 *   coop-perch-crop x,y,w,h [-d :2] [-o FILE.png] [-p PAD]      (default out: ~/coop-perch/tape/crop.png, pad 4 px)
 * Prints the file path and its size.
 */
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <zlib.h>

static void put32(unsigned char *p, uint32_t v) { p[0] = v >> 24; p[1] = v >> 16; p[2] = v >> 8; p[3] = (unsigned char)v; }
static void chunk(FILE *f, const char *type, const unsigned char *data, uint32_t len) {
    unsigned char b[4];
    put32(b, len); fwrite(b, 1, 4, f);
    fwrite(type, 1, 4, f);
    if (len) fwrite(data, 1, len, f);
    uLong crc = crc32(0, (const Bytef *)type, 4);
    if (len) crc = crc32(crc, data, len);
    put32(b, (uint32_t)crc); fwrite(b, 1, 4, f);
}

int main(int argc, char **argv) {
    int x = -1, y = -1, w = 0, h = 0, pad = 4;
    const char *disp = ":2", *out = NULL;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "-d") && i + 1 < argc) disp = argv[++i];
        else if (!strcmp(argv[i], "-o") && i + 1 < argc) out = argv[++i];
        else if (!strcmp(argv[i], "-p") && i + 1 < argc) pad = atoi(argv[++i]);
        else if (sscanf(argv[i], "%d,%d,%d,%d", &x, &y, &w, &h) == 4) {}
        else if (sscanf(argv[i], "%d,%d %dx%d", &x, &y, &w, &h) == 4) {}
        else { fprintf(stderr, "usage: %s x,y,w,h [-d :2] [-o FILE.png] [-p PAD]\n", argv[0]); return 2; }
    }
    if (x < 0 || w <= 0 || h <= 0) { fprintf(stderr, "coop-perch-crop: give the box as x,y,w,h\n"); return 2; }
    Display *dpy = XOpenDisplay(disp);
    if (!dpy) { fprintf(stderr, "coop-perch-crop: cannot open %s\n", disp); return 1; }
    int scr = DefaultScreen(dpy), SW = DisplayWidth(dpy, scr), SH = DisplayHeight(dpy, scr);
    x -= pad; y -= pad; w += 2 * pad; h += 2 * pad;
    if (x < 0) { w += x; x = 0; }
    if (y < 0) { h += y; y = 0; }
    if (x + w > SW) w = SW - x;
    if (y + h > SH) h = SH - y;
    if (w <= 0 || h <= 0) { fprintf(stderr, "coop-perch-crop: box is off the screen\n"); return 1; }
    XImage *img = XGetImage(dpy, RootWindow(dpy, scr), x, y, (unsigned)w, (unsigned)h, AllPlanes, ZPixmap);
    if (!img) { fprintf(stderr, "coop-perch-crop: cannot read the screen\n"); return 1; }
    size_t raw_n = (size_t)h * (1 + 3 * (size_t)w);
    unsigned char *raw = malloc(raw_n);
    if (!raw) return 1;
    for (int r = 0; r < h; r++) {
        unsigned char *row = raw + (size_t)r * (1 + 3 * (size_t)w);
        row[0] = 0;
        for (int c = 0; c < w; c++) {
            unsigned long p = XGetPixel(img, c, r);
            row[1 + 3 * c] = (p >> 16) & 255; row[2 + 3 * c] = (p >> 8) & 255; row[3 + 3 * c] = p & 255;
        }
    }
    uLongf zn = compressBound((uLong)raw_n);
    unsigned char *z = malloc(zn);
    if (!z || compress2(z, &zn, raw, (uLong)raw_n, 9) != Z_OK) return 1;
    char def[256];
    if (!out) { snprintf(def, sizeof def, "%s/coop-perch/tape", getenv("HOME") ? getenv("HOME") : "."); mkdir(def, 0750); strncat(def, "/crop.png", sizeof def - strlen(def) - 1); out = def; }
    FILE *f = fopen(out, "wb");
    if (!f) { perror(out); return 1; }
    fwrite("\x89PNG\r\n\x1a\n", 1, 8, f);
    unsigned char ihdr[13];
    put32(ihdr, (uint32_t)w); put32(ihdr + 4, (uint32_t)h);
    ihdr[8] = 8; ihdr[9] = 2; ihdr[10] = 0; ihdr[11] = 0; ihdr[12] = 0;
    chunk(f, "IHDR", ihdr, 13);
    chunk(f, "IDAT", z, (uint32_t)zn);
    chunk(f, "IEND", NULL, 0);
    fclose(f);
    printf("%s %dx%d\n", out, w, h);
    return 0;
}
