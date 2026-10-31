/* coop-perch-chip: 1024 bits (128 bytes) of the video card's own RAM, live, as a 32x32 box on the screen.
 * Every bit has its empty white circle (the template, always there); a 1 bit fills it with a blue dot. Rows are 4 bytes each,
 * the highest bit of each byte on the left. A row of 4 bytes is one pixel (blue, green, red, spare), so the box is 32 pixels.
 * Beside it, "How a bot sees this": the same 32 pixels as the numbers a bot gets (R G B 0..255, changed or not), row for row,
 * which also go to the box of bot vectors as plane "chip" (32 x 4: R G B changed), a frame whenever a pixel changed.
 *
 * The RAM is read straight from the GPU driver (amdgpu debugfs: amdgpu_vram, the card's whole VRAM; on the host's AMD APU that is
 * a 512 MB slice of main memory set aside for the GPU). Needs root to open it: run with sudo; it opens the RAM, finds a spot, then
 * drops to the user who ran sudo before it touches the screen.
 *
 *   sudo coop-perch-chip                  sweep the RAM 3 times, pick the 128 bytes that keep changing most, watch them
 *   sudo coop-perch-chip -c               find the panel clock (with seconds): bits that change once a second, on the second
 *   sudo coop-perch-chip -a 0x1234000     watch these 128 bytes
 *   options: -d :0 (screen), -r 10 (reads a second), -s 18 (pixels per bit)
 */
#define _GNU_SOURCE
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <fcntl.h>
#include <glob.h>
#include <grp.h>
#include <pwd.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#include "coop-perch-box.h"

#define BYTES 128
#define SIDE 32
#define CHUNK (4u << 20)

static double now_s(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return (double)t.tv_sec + t.tv_nsec / 1e9; }

static int read_all(int fd, unsigned char *buf, size_t off, size_t n) {
    size_t got = 0;
    while (got < n) {
        ssize_t r = pread(fd, buf + got, n - got, (off_t)(off + got));
        if (r <= 0) return -1;
        got += (size_t)r;
    }
    return 0;
}

/* three sweeps: a block that changed between 1-2 and again between 2-3 is live; the one with the most bit flips wins */
static long find_live(int fd, size_t size) {
    unsigned char *snap = malloc(size), *cur = malloc(CHUNK);
    uint16_t *flips = calloc(size / BYTES, sizeof(uint16_t));
    unsigned char *hit = calloc(size / BYTES, 1);
    if (!snap || !cur || !flips || !hit) { fprintf(stderr, "coop-perch-chip: out of memory for the sweep\n"); return -1; }
    fprintf(stderr, "coop-perch-chip: sweeping %zu MB of video RAM 3 times to find bits that move...\n", size >> 20);
    if (read_all(fd, snap, 0, size) != 0) { fprintf(stderr, "coop-perch-chip: cannot read the video RAM\n"); return -1; }
    for (int pass = 1; pass <= 2; pass++) {
        usleep(700000);
        for (size_t off = 0; off < size; off += CHUNK) {
            size_t n = size - off < CHUNK ? size - off : CHUNK;
            if (read_all(fd, cur, off, n) != 0) return -1;
            if (!memcmp(cur, snap + off, n)) continue;
            for (size_t b = 0; b < n; b += BYTES) {
                if (!memcmp(cur + b, snap + off + b, BYTES)) continue;
                size_t blk = (off + b) / BYTES;
                int f = 0;
                for (int i = 0; i < BYTES; i++) f += __builtin_popcount(cur[b + i] ^ snap[off + b + i]);
                hit[blk] |= (unsigned char)pass;
                flips[blk] = (uint16_t)(flips[blk] + f > 65535 ? 65535 : flips[blk] + f);
            }
            memcpy(snap + off, cur, n);
        }
    }
    long best = -1, live = 0;
    for (size_t blk = 0; blk < size / BYTES; blk++)
        if (hit[blk] == 3) { live++; if (best < 0 || flips[blk] > flips[best]) best = (long)blk; }
    if (best < 0)                                                     /* nothing moved twice: take anything that moved once */
        for (size_t blk = 0; blk < size / BYTES; blk++) if (hit[blk] && (best < 0 || flips[blk] > flips[best])) best = (long)blk;
    fprintf(stderr, "coop-perch-chip: %ld blocks of 128 bytes kept changing; watching 0x%lx\n", live, best < 0 ? 0L : best * (long)BYTES);
    free(snap); free(cur); free(flips); free(hit);
    return best < 0 ? 0 : best * BYTES;
}

/* one sweep: compare the whole RAM with snap, mark blocks that changed in `mark` (bit `flag`), count flips, then snap = now */
static int sweep(int fd, size_t size, unsigned char *snap, unsigned char *cur, unsigned char *mark, unsigned char flag, uint16_t *flips) {
    for (size_t off = 0; off < size; off += CHUNK) {
        size_t n = size - off < CHUNK ? size - off : CHUNK;
        if (read_all(fd, cur, off, n) != 0) return -1;
        if (!memcmp(cur, snap + off, n)) continue;
        for (size_t b = 0; b < n; b += BYTES) {
            if (!memcmp(cur + b, snap + off + b, BYTES)) continue;
            size_t blk = (off + b) / BYTES;
            int f = 0;
            for (int i = 0; i < BYTES; i++) f += __builtin_popcount(cur[b + i] ^ snap[off + b + i]);
            mark[blk] |= flag;
            if (flips) flips[blk] = (uint16_t)(flips[blk] + f > 65535 ? 65535 : flips[blk] + f);
        }
        memcpy(snap + off, cur, n);
    }
    return 0;
}
/* the clock (with seconds): blocks that change in nearly every second, but not much more often than once a second.
   Step 1: two sweeps of the whole RAM list every block that moves at all. Step 2: only those are read 10 times a second for
   SAMPLE_S seconds; a clock block changes in >= 75% of the seconds and at most 1.6 times a second (animations change far more). */
#define SAMPLE_S 12
static long span_lo = -1, span_hi = -1;                          /* the clock's stretch of RAM, found by find_clock */
static long find_clock(int fd, size_t size) {
    unsigned char *snap = malloc(size), *cur = malloc(CHUNK), *mark = calloc(size / BYTES, 1);
    if (!snap || !cur || !mark) { fprintf(stderr, "coop-perch-chip: out of memory for the sweep\n"); return -1; }
    fprintf(stderr, "coop-perch-chip: finding the clock in %zu MB of video RAM (about 25 seconds)...\n", size >> 20);
    if (read_all(fd, snap, 0, size) != 0) { fprintf(stderr, "coop-perch-chip: cannot read the video RAM\n"); return -1; }
    for (int i = 0; i < 2; i++) { usleep(400000); if (sweep(fd, size, snap, cur, mark, 1, NULL) != 0) return -1; }
    size_t nblk = size / BYTES, ncand = 0;
    for (size_t k = 0; k < nblk; k++) ncand += mark[k] != 0;
    fprintf(stderr, "coop-perch-chip: %zu blocks move at all; timing them for %d seconds\n", ncand, SAMPLE_S);
    if (!ncand) return -1;
    size_t *cand = malloc(ncand * sizeof *cand);
    uint16_t *changes = calloc(ncand, sizeof *changes), *flips = calloc(ncand, sizeof *flips);
    uint32_t *secs = calloc(ncand, sizeof *secs);
    unsigned char *last = malloc(ncand * BYTES), *now = malloc(BYTES);
    if (!cand || !changes || !flips || !secs || !last || !now) { fprintf(stderr, "coop-perch-chip: out of memory\n"); return -1; }
    size_t j = 0;
    for (size_t k = 0; k < nblk; k++) if (mark[k]) { cand[j] = k; memcpy(last + j * BYTES, snap + k * BYTES, BYTES); j++; }
    free(snap); free(cur); free(mark);
    time_t t0 = time(NULL);
    while (time(NULL) == t0) usleep(20000);                           /* start on a second boundary */
    t0 = time(NULL);
    double next = now_s();
    while (time(NULL) - t0 < SAMPLE_S) {
        int sec = (int)(time(NULL) - t0);
        for (size_t c = 0; c < ncand; c++) {
            if (read_all(fd, now, cand[c] * BYTES, BYTES) != 0) return -1;
            if (!memcmp(now, last + c * BYTES, BYTES)) continue;
            int f = 0;
            for (int i = 0; i < BYTES; i++) f += __builtin_popcount(now[i] ^ last[c * BYTES + i]);
            memcpy(last + c * BYTES, now, BYTES);
            changes[c]++; flips[c] = (uint16_t)(flips[c] + f > 65535 ? 65535 : flips[c] + f);
            secs[c] |= 1u << sec;
        }
        next += 0.1;
        double w = next - now_s();
        if (w > 0) usleep((useconds_t)(w * 1e6)); else next = now_s();
    }
    /* best: changed in the most seconds, then the fewest extra changes (once a second exactly), then the most bit flips.
       A block under the second hand's path ticks only while the hand passes; one by the hand's pivot ticks every second. */
    long best = -1; size_t n = 0;
    int bs = 0, bx = 0;
    for (size_t c = 0; c < ncand; c++) {
        int ns = __builtin_popcount(secs[c]), extra = changes[c] > ns ? changes[c] - ns : 0;
        if (ns * 4 < SAMPLE_S * 3 || changes[c] * 10 > SAMPLE_S * 16) continue;
        n++;
        if (best < 0 || ns > bs || (ns == bs && (extra < bx || (extra == bx && flips[c] > flips[best])))) { best = (long)c; bs = ns; bx = extra; }
    }
    long addr = best < 0 ? -1 : (long)(cand[best] * BYTES);
    if (best >= 0) {     /* the stretch: every block on the seconds beat (2+ seconds, at most 1.6 changes a second), within 4 KB of each other */
        long lo = addr, hi = addr + BYTES;
        for (int grew = 1; grew;) {
            grew = 0;
            for (size_t c = 0; c < ncand; c++) {
                int ns = __builtin_popcount(secs[c]);
                if (ns < 2 || changes[c] * 10 > ns * 16) continue;
                long a = (long)(cand[c] * BYTES);
                if (a < lo && a >= lo - 4096) { lo = a; grew = 1; }
                if (a + BYTES > hi && a <= hi + 4096) { hi = a + BYTES; grew = 1; }
            }
        }
        span_lo = lo; span_hi = hi;
        fprintf(stderr, "coop-perch-chip: the clock's stretch of RAM: 0x%lx..0x%lx (%ld KB)\n", lo, hi, (hi - lo) >> 10);
    }
    fprintf(stderr, "coop-perch-chip: %zu blocks tick with the seconds; watching 0x%lx\n", n, addr < 0 ? 0L : addr);
    free(cand); free(changes); free(flips); free(secs); free(last); free(now);
    return addr;
}

int main(int argc, char **argv) {
    int clock_mode = 0;
    const char *disp = NULL;
    long addr = -1;
    double rate = 10;
    int cell = 18;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "-a") && i + 1 < argc) addr = strtol(argv[++i], NULL, 0);
        else if (!strcmp(argv[i], "-c")) clock_mode = 1;
        else if (!strcmp(argv[i], "-d") && i + 1 < argc) disp = argv[++i];
        else if (!strcmp(argv[i], "-r") && i + 1 < argc) rate = atof(argv[++i]);
        else if (!strcmp(argv[i], "-s") && i + 1 < argc) cell = atoi(argv[++i]);
        else { fprintf(stderr, "usage: sudo %s [-c | -a ADDRESS] [-d :0] [-r READS_PER_SEC] [-s PIXELS_PER_BIT]\n", argv[0]); return 2; }
    }
    if (rate <= 0) rate = 10;
    if (cell < 6) cell = 6;

    glob_t g;
    if (glob("/sys/kernel/debug/dri/*/amdgpu_vram", 0, NULL, &g) != 0 || g.gl_pathc == 0) {
        fprintf(stderr, "coop-perch-chip: no amdgpu_vram (run with sudo; needs an AMD GPU and debugfs)\n");
        return 1;
    }
    char path[512];
    snprintf(path, sizeof path, "%s", g.gl_pathv[0]);
    globfree(&g);
    int fd = open(path, O_RDONLY);
    if (fd < 0) { perror(path); return 1; }
    struct stat st;
    size_t size = fstat(fd, &st) == 0 && st.st_size > 0 ? (size_t)st.st_size : 0;
    if (!size) { fprintf(stderr, "coop-perch-chip: video RAM size unknown\n"); return 1; }
    if (addr < 0 && clock_mode) {
        addr = find_clock(fd, size);
        if (addr < 0) { fprintf(stderr, "coop-perch-chip: no clock found\n"); return 1; }
        printf("0x%lx\n", addr);
        fflush(stdout);
    }
    if (addr < 0) addr = find_live(fd, size);
    if (addr < 0) return 1;
    addr &= ~(long)(BYTES - 1);
    if ((size_t)addr + BYTES > size) { fprintf(stderr, "coop-perch-chip: address past the end of the %zu MB\n", size >> 20); return 1; }

    /* the RAM is open: stop being root before going near the screen */
    const char *su = getenv("SUDO_UID"), *sg = getenv("SUDO_GID");
    if (getuid() == 0 && su && sg) {
        uid_t uid = (uid_t)atoi(su);
        gid_t gid = (gid_t)atoi(sg);
        struct passwd *pw = getpwuid(uid);
        if (setgroups(0, NULL) != 0 || setgid(gid) != 0 || setuid(uid) != 0) { fprintf(stderr, "coop-perch-chip: cannot drop root\n"); return 1; }
        if (pw) {
            setenv("HOME", pw->pw_dir, 1);
            char xa[600];
            snprintf(xa, sizeof xa, "%s/.Xauthority", pw->pw_dir);
            if (!getenv("XAUTHORITY") && access(xa, R_OK) == 0) setenv("XAUTHORITY", xa, 1);
        }
    }
    if (!disp) disp = getenv("DISPLAY") && *getenv("DISPLAY") ? getenv("DISPLAY") : ":0";

    Display *dpy = XOpenDisplay(disp);
    if (!dpy) { fprintf(stderr, "coop-perch-chip: cannot open screen %s\n", disp); return 1; }
    int scr = DefaultScreen(dpy), pad = cell, top = cell + 22;
    XFontStruct *font = XLoadQueryFont(dpy, "-misc-fixed-medium-r-normal--13-*-*-*-*-*-iso8859-1");
    if (!font) font = XLoadQueryFont(dpy, "fixed");
    int bitsW = SIDE * cell, botX = pad + bitsW + 2 * pad, botW = 26 * 7 + 40;
    int W = botX + botW + pad, H = top + SIDE * cell + pad;
    unsigned long white = 0xffffff, ring = 0xb8c4d6, blue = 0x1f5fd6, ink = 0x2a3140, soft = 0x8a93a6;
    Window win = XCreateSimpleWindow(dpy, RootWindow(dpy, scr), 60, 60, (unsigned)W, (unsigned)H, 0, 0, white);
    char title[160];
    snprintf(title, sizeof title, "coop-perch-chip: video RAM 0x%lx, 128 bytes, 1024 bits", addr);
    XStoreName(dpy, win, title);
    XSizeHints *sh = XAllocSizeHints();
    if (sh) { sh->flags = PMinSize | PMaxSize; sh->min_width = sh->max_width = W; sh->min_height = sh->max_height = H; XSetWMNormalHints(dpy, win, sh); XFree(sh); }
    Atom del = XInternAtom(dpy, "WM_DELETE_WINDOW", False);
    XSetWMProtocols(dpy, win, &del, 1);
    XSelectInput(dpy, win, ExposureMask | StructureNotifyMask);
    XMapWindow(dpy, win);
    GC gc = XCreateGC(dpy, win, 0, NULL);
    if (font) XSetFont(dpy, gc, font->fid);
    XSetLineAttributes(dpy, gc, cell > 14 ? 2 : 1, LineSolid, CapRound, JoinRound);

    CoopBox box;                                                      /* what the bot gets: 32 pixels x (R G B changed) */
    uint32_t bdim[2] = {SIDE, 4};
    char bfields[300];
    snprintf(bfields, sizeof bfields, "R G B changed; 32 pixels of video RAM at 0x%lx (4 bytes each: blue green red spare), colours 0..255, "
                                      "changed 1 if the pixel differs from the frame before; a frame whenever a pixel changed", addr);
    int have_box = coop_box_create(&box, "chip", 2, bdim, bfields) == 0;

    unsigned char bits[BYTES], shown[BYTES];
    memset(shown, 0, sizeof shown);
    long span_n = span_lo >= 0 ? (span_hi - span_lo) / BYTES : 0;
    unsigned char *span = span_n ? malloc((size_t)span_n * BYTES) : NULL, *span_prev = span_n ? malloc((size_t)span_n * BYTES) : NULL;
    double *span_t = span_n ? calloc((size_t)span_n, sizeof(double)) : NULL;
    if (span && span_prev && span_t && read_all(fd, span_prev, (size_t)span_lo, (size_t)span_n * BYTES) != 0) span_n = 0;
    if (!span || !span_prev || !span_t) span_n = 0;
    double watched_since = now_s();
    double lastchg[SIDE];
    int marked[SIDE];
    for (int r = 0; r < SIDE; r++) { lastchg[r] = -10; marked[r] = 0; }
    int full = 1;
    double next = 0;
    int dot = cell - 6, inner = dot - (cell > 14 ? 6 : 4);
    if (inner < 2) inner = 2;
    for (;;) {
        while (XPending(dpy)) {
            XEvent e;
            XNextEvent(dpy, &e);
            if (e.type == Expose && e.xexpose.count == 0) full = 1;
            if (e.type == ClientMessage && (Atom)e.xclient.data.l[0] == del) { XCloseDisplay(dpy); return 0; }
            if (e.type == DestroyNotify) return 0;
        }
        double t = now_s();
        if (t >= next) {
            next = t + 1.0 / rate;
            if (span_n && read_all(fd, span, (size_t)span_lo, (size_t)span_n * BYTES) == 0) {
                /* follow the hand: when blocks in the clock's stretch change this tick and the watched one has not changed for
                   half a second, jump to the one that changed most (the gaze goes to the strike) */
                long fresh = -1;
                int fresh_flips = 0;
                for (long k = 0; k < span_n; k++) {
                    if (!memcmp(span + k * BYTES, span_prev + k * BYTES, BYTES)) continue;
                    span_t[k] = t;
                    int f = 0;
                    for (int i = 0; i < BYTES; i++) f += __builtin_popcount(span[k * BYTES + i] ^ span_prev[k * BYTES + i]);
                    if (f > fresh_flips) { fresh = k; fresh_flips = f; }
                }
                memcpy(span_prev, span, (size_t)span_n * BYTES);
                long cur = (addr - span_lo) / BYTES;
                double quiet = cur >= 0 && cur < span_n && span_t[cur] > 0 ? t - span_t[cur] : t - watched_since;
                if (fresh >= 0 && fresh != cur && quiet > 0.5) {
                    addr = span_lo + fresh * BYTES;
                    watched_since = t;
                    snprintf(title, sizeof title, "coop-perch-chip: video RAM 0x%lx, 128 bytes, 1024 bits (following the clock)", addr);
                    XStoreName(dpy, win, title);
                }
            }
            if (read_all(fd, bits, (size_t)addr, BYTES) != 0) { fprintf(stderr, "coop-perch-chip: read failed\n"); return 1; }
            if (full) {
                XSetForeground(dpy, gc, white);
                XFillRectangle(dpy, win, gc, 0, 0, (unsigned)W, (unsigned)H);
                XSetForeground(dpy, gc, ink);
                const char *h1 = "The chip's bits", *h2 = "How a bot sees this";
                XDrawString(dpy, win, gc, pad, cell + 6, h1, (int)strlen(h1));
                XDrawString(dpy, win, gc, botX, cell + 6, h2, (int)strlen(h2));
                XSetForeground(dpy, gc, soft);
                const char *k1 = "1 row = 4 bytes = 1 pixel", *k2 = "px    R   G   B";
                XDrawString(dpy, win, gc, pad + bitsW - (int)strlen(k1) * 7, cell + 6, k1, (int)strlen(k1));
                XDrawString(dpy, win, gc, botX + 28, top - 4, k2, (int)strlen(k2));
            }
            for (int i = 0; i < SIDE * SIDE; i++) {
                int byte = i / 8, bit = 7 - (i % 8);
                int on = (bits[byte] >> bit) & 1, was = (shown[byte] >> bit) & 1;
                if (!full && on == was) continue;                     /* only bits that flipped get redrawn */
                int x = pad + (i % SIDE) * cell + 3, y = top + (i / SIDE) * cell + 3;
                XSetForeground(dpy, gc, white);
                XFillArc(dpy, win, gc, x, y, (unsigned)dot, (unsigned)dot, 0, 360 * 64);
                XSetForeground(dpy, gc, ring);                        /* the template: always there */
                XDrawArc(dpy, win, gc, x, y, (unsigned)dot, (unsigned)dot, 0, 360 * 64);
                if (on) {
                    XSetForeground(dpy, gc, blue);
                    XFillArc(dpy, win, gc, x + (dot - inner) / 2, y + (dot - inner) / 2, (unsigned)inner, (unsigned)inner, 0, 360 * 64);
                }
            }
            /* the bot's view: each row's 4 bytes as one pixel, the numbers it reads, a mark when the pixel changed */
            int anychg = 0;
            float *bf = NULL;
            for (int r = 0; r < SIDE; r++) {
                const unsigned char *p = bits + r * 4, *q = shown + r * 4;
                int chg = memcmp(p, q, 4) != 0;
                anychg |= chg;
            }
            if (anychg && have_box && !full) bf = coop_box_next(&box, (double)time(NULL));
            for (int r = 0; r < SIDE; r++) {
                const unsigned char *p = bits + r * 4, *q = shown + r * 4;
                int chg = !full && memcmp(p, q, 4) != 0;
                if (bf) { bf[r * 4] = p[2]; bf[r * 4 + 1] = p[1]; bf[r * 4 + 2] = p[0]; bf[r * 4 + 3] = (float)chg; }
                if (chg) lastchg[r] = t;
                int mark = t - lastchg[r] < 1.0;                      /* the mark stays a second so a person can see it */
                if (!full && !chg && mark == marked[r]) continue;     /* nothing new on this row */
                marked[r] = mark;
                int y = top + r * cell;
                XSetForeground(dpy, gc, white);
                XFillRectangle(dpy, win, gc, botX, y, (unsigned)botW, (unsigned)cell);
                XSetForeground(dpy, gc, (unsigned long)p[2] << 16 | (unsigned long)p[1] << 8 | p[0]);   /* the pixel's own colour */
                XFillRectangle(dpy, win, gc, botX, y + 3, 18, (unsigned)(cell - 6));
                XSetForeground(dpy, gc, ring);
                XDrawRectangle(dpy, win, gc, botX, y + 3, 18, (unsigned)(cell - 6));
                char line[64];
                snprintf(line, sizeof line, "%2d  %3d %3d %3d", r, p[2], p[1], p[0]);
                XSetForeground(dpy, gc, mark ? blue : ink);
                XDrawString(dpy, win, gc, botX + 28, y + cell - 5, line, (int)strlen(line));
                if (mark) {                                            /* changed: the bot's strike */
                    XSetForeground(dpy, gc, blue);
                    XFillArc(dpy, win, gc, botX + 28 + 17 * 7, y + cell / 2 - 4, 8, 8, 0, 360 * 64);
                }
            }
            if (bf) coop_box_commit(&box);
            memcpy(shown, bits, sizeof shown);
            full = 0;
            XFlush(dpy);
        }
        double wait = next - now_s();
        if (wait > 0) {
            fd_set fs;
            FD_ZERO(&fs);
            FD_SET(ConnectionNumber(dpy), &fs);
            struct timeval tv = {(long)wait, (long)((wait - (long)wait) * 1e6)};
            select(ConnectionNumber(dpy) + 1, &fs, NULL, NULL, &tv);
        }
    }
}
