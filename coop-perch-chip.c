/* Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md. */
/* coop-perch-chip: the Perch eye on the video card's own RAM, as a 32x32 box of bits on the screen.
 *
 * The zoom (Mike, 31 Oct 2026):
 *   zoom out: look at all of the video RAM a few times; count the bit flips in each of 16 parts.
 *   something is changing: zoom into the part that changed most; it becomes the new window, from address X to Y.
 *   repeat on that window (16 parts again, each 16 times smaller) until the window is the 128 bytes the box shows.
 *   when the box goes quiet, zoom back out one level and look again (all the way out if need be).
 * The window (from - to, size, level) is written at the top of the box, live, in fixed columns.
 * The eye masks itself out first: it flashes its own window 3 times and notes the RAM that changes exactly then (its own picture,
 * and its patch of the screen's framebuffer). Masked blocks never attract the zoom.
 *
 * The box: 1024 bits, every bit an empty white circle (the template, always there), a blue dot when 1. A row of 4 bytes is one
 * pixel if the memory holds pixels (blue, green, red, spare). Beside it, "How a bot sees this": the 32 rows as the numbers a bot
 * gets (R G B, changed), also written to the box of bot vectors as plane "chip" (u8: 32 x R G B + 32 changed bits).
 *
 * The RAM is read straight from the GPU driver (amdgpu debugfs amdgpu_vram; on an AMD APU a 512 MB slice of main memory).
 * Needs root to open it: run with sudo; it opens the RAM, then drops to the user who ran sudo before it touches the screen.
 *
 *   sudo coop-perch-chip                  zoom (above)
 *   sudo coop-perch-chip -a 0x1234000     watch these 128 bytes, no zoom
 *   options: -d :0 (screen), -r 10 (reads a second at the bottom), -n 4 (looks per zoom level), -s 18 (pixels per bit)
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
#include <sys/select.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#include "coop-perch-box.h"

#define BYTES 128          /* the box: 1024 bits */
#define SIDE 32
#define PARTS 16           /* each zoom level splits its window into this many parts */
#define CHUNK (4u << 20)
#define QUIET_S 3.0        /* the box quiet this long: zoom out one level */
#define LEVEL_GAP 0.3      /* at least this long between looks at one level, so slow changes (a clock) are seen */

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

static void size_txt(size_t n, char *o, size_t on) {
    if (n >= (1u << 20)) snprintf(o, on, "%zu MB", n >> 20);
    else if (n >= 1024) snprintf(o, on, "%zu KB", n >> 10);
    else snprintf(o, on, "%zu bytes", n);
}

/* ---- the screen ---- */
static Display *dpy;
static Window win;
static GC gc;
static int cell = 18, pad, top, bitsW, botX, botW, W, H;
static const unsigned long white = 0xffffff, ring = 0xb8c4d6, blue = 0x1f5fd6, ink = 0x2a3140, soft = 0x8a93a6;
static char status[200] = "";

static void set_status(int level, size_t lo, size_t hi, const char *state) {
    char sz[40];
    size_txt(hi - lo, sz, sizeof sz);
    snprintf(status, sizeof status, "level %2d   from 0x%08zx   to 0x%08zx   %-9s  %-10s", level, lo, hi, sz, state);
}
static void draw_status(void) {
    XSetForeground(dpy, gc, white);
    XFillRectangle(dpy, win, gc, 0, 0, (unsigned)W, (unsigned)(cell + 12));
    XSetForeground(dpy, gc, ink);
    XDrawString(dpy, win, gc, pad, cell + 2, status, (int)strlen(status));
    XFlush(dpy);
}
static int pump(Atom del) {                                          /* window events; 1 = closed */
    while (XPending(dpy)) {
        XEvent e;
        XNextEvent(dpy, &e);
        if (e.type == ClientMessage && (Atom)e.xclient.data.l[0] == del) return 1;
        if (e.type == DestroyNotify) return 1;
    }
    return 0;
}

/* ---- the mask: the eye's own memory (its window's picture and its patch of the screen), never looked at ---- */
static unsigned char *self_mask = NULL;                              /* one byte per 128-byte block of the RAM, 1 = the eye itself */

/* ---- the zoom ---- */
typedef struct { size_t lo, hi; } Span;

/* look at [lo,hi) `looks` times; return the start of the part (of PARTS, fewer when the window is small) with the most bit flips.
   *flips gets that count (0 = nothing changed), *plen the part length. */
static long look(int fd, size_t lo, size_t hi, int looks, unsigned char *snap, unsigned char *cur, long *flips, size_t *plen_out,
                 Atom del, int *closed) {
    size_t len = hi - lo;
    size_t parts = len / BYTES < PARTS ? len / BYTES : PARTS;
    if (parts < 1) parts = 1;
    size_t plen = (len / parts + BYTES - 1) / BYTES * BYTES;
    uint64_t pf[PARTS];
    memset(pf, 0, sizeof pf);
    if (read_all(fd, snap, lo, len) != 0) return -1;
    for (int k = 1; k < looks; k++) {
        double t0 = now_s();
        for (size_t off = 0; off < len; off += CHUNK) {
            size_t n = len - off < CHUNK ? len - off : CHUNK;
            if (read_all(fd, cur, lo + off, n) != 0) return -1;
            if (memcmp(cur, snap + off, n)) {
                for (size_t i = 0; i + 8 <= n; i += 8) {
                    uint64_t a, b;
                    memcpy(&a, cur + i, 8); memcpy(&b, snap + off + i, 8);
                    if (a != b && !(self_mask && self_mask[(lo + off + i) / BYTES])) { size_t p = (off + i) / plen; pf[p < parts ? p : parts - 1] += (uint64_t)__builtin_popcountll(a ^ b); }
                }
                memcpy(snap + off, cur, n);
            }
            if (pump(del)) { *closed = 1; return -1; }
        }
        double w = LEVEL_GAP - (now_s() - t0);
        if (w > 0) usleep((useconds_t)(w * 1e6));
    }
    size_t best = 0;
    for (size_t p = 1; p < parts; p++) if (pf[p] > pf[best]) best = p;
    *flips = (long)pf[best];
    *plen_out = plen;
    return (long)(lo + best * plen);
}

int main(int argc, char **argv) {
    const char *disp = NULL;
    long fixed = -1;
    double rate = 10;
    int looks = 4;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "-a") && i + 1 < argc) fixed = strtol(argv[++i], NULL, 0);
        else if (!strcmp(argv[i], "-d") && i + 1 < argc) disp = argv[++i];
        else if (!strcmp(argv[i], "-r") && i + 1 < argc) rate = atof(argv[++i]);
        else if (!strcmp(argv[i], "-n") && i + 1 < argc) looks = atoi(argv[++i]);
        else if (!strcmp(argv[i], "-s") && i + 1 < argc) cell = atoi(argv[++i]);
        else { fprintf(stderr, "usage: sudo %s [-a ADDRESS] [-d :0] [-r READS_PER_SEC] [-n LOOKS_PER_LEVEL] [-s PIXELS_PER_BIT]\n", argv[0]); return 2; }
    }
    if (rate <= 0) rate = 10;
    if (looks < 2) looks = 2;
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
    if (fixed >= 0) { fixed &= ~(long)(BYTES - 1); if ((size_t)fixed + BYTES > size) { fprintf(stderr, "coop-perch-chip: address past the end\n"); return 1; } }

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

    dpy = XOpenDisplay(disp);
    if (!dpy) { fprintf(stderr, "coop-perch-chip: cannot open screen %s\n", disp); return 1; }
    int scr = DefaultScreen(dpy);
    pad = cell; top = cell + 40;
    XFontStruct *font = XLoadQueryFont(dpy, "-misc-fixed-medium-r-normal--13-*-*-*-*-*-iso8859-1");
    if (!font) font = XLoadQueryFont(dpy, "fixed");
    bitsW = SIDE * cell; botX = pad + bitsW + 2 * pad; botW = 26 * 7 + 40;
    W = botX + botW + pad; H = top + SIDE * cell + pad;
    win = XCreateSimpleWindow(dpy, RootWindow(dpy, scr), 60, 60, (unsigned)W, (unsigned)H, 0, 0, white);
    XStoreName(dpy, win, "coop-perch-chip: the eye on the video RAM");   /* fixed: the zoom is shown inside, not in the title */
    XSizeHints *sh = XAllocSizeHints();
    if (sh) { sh->flags = PMinSize | PMaxSize; sh->min_width = sh->max_width = W; sh->min_height = sh->max_height = H; XSetWMNormalHints(dpy, win, sh); XFree(sh); }
    Atom del = XInternAtom(dpy, "WM_DELETE_WINDOW", False);
    XSetWMProtocols(dpy, win, &del, 1);
    XSelectInput(dpy, win, ExposureMask | StructureNotifyMask);
    XMapWindow(dpy, win);
    gc = XCreateGC(dpy, win, 0, NULL);
    if (font) XSetFont(dpy, gc, font->fid);
    XSetLineAttributes(dpy, gc, cell > 14 ? 2 : 1, LineSolid, CapRound, JoinRound);

    CoopBox box;                                                      /* what the bot gets: 32 rows R G B as bytes + 32 changed bits */
    uint32_t bdim[1] = {SIDE * 3 + 4};
    int have_box = coop_box_create(&box, "chip", "u8", 1, bdim,
        "R0 G0 B0 .. R31 G31 B31 changed_bits(4 bytes, bit n = row n changed); the 128 bytes the eye is on in the video RAM, a row of 4 "
        "bytes read as blue green red spare; the eye zooms, so the address moves; a frame whenever a row changed") == 0;

    unsigned char *snap = malloc(size), *cur = malloc(CHUNK);
    if (!snap || !cur) { fprintf(stderr, "coop-perch-chip: out of memory\n"); return 1; }
    if (fixed < 0) {
        /* learn the mask: one sweep around a flash gives every block that changed (wide); then 3 quick flashes, reading only those
           blocks just before and just after each; what changes every time is the eye itself */
        XSync(dpy, False); usleep(300000);
        snprintf(status, sizeof status, "learning which memory is this box itself (a few seconds)");
        draw_status();
        size_t nb = size / BYTES, nc = 0;
        self_mask = calloc(nb, 1);
        if (!self_mask || read_all(fd, snap, 0, size) != 0) { fprintf(stderr, "coop-perch-chip: cannot read the video RAM\n"); return 1; }
        XSetForeground(dpy, gc, 0x000000); XFillRectangle(dpy, win, gc, 0, 0, (unsigned)W, (unsigned)H); XSync(dpy, False); usleep(150000);
        for (size_t off = 0; off < size; off += CHUNK) {
            size_t n = size - off < CHUNK ? size - off : CHUNK;
            if (read_all(fd, cur, off, n) != 0) return 1;
            for (size_t b = 0; b < n; b += BYTES) if (memcmp(cur + b, snap + off + b, BYTES)) { self_mask[(off + b) / BYTES] = 1; nc++; }
        }
        unsigned char before[BYTES], after[BYTES];
        for (int round = 0; round < 3; round++) {
            unsigned char *keep = calloc(nb, 1);
            if (!keep) return 1;
            for (size_t k = 0; k < nb; k++) if (self_mask[k]) { if (read_all(fd, snap + k * BYTES, k * BYTES, BYTES) != 0) return 1; }
            XSetForeground(dpy, gc, round % 2 ? 0x000000 : 0xffffff);
            XFillRectangle(dpy, win, gc, 0, 0, (unsigned)W, (unsigned)H); XSync(dpy, False); usleep(150000);
            for (size_t k = 0; k < nb; k++)
                if (self_mask[k]) {
                    memcpy(before, snap + k * BYTES, BYTES);
                    if (read_all(fd, after, k * BYTES, BYTES) != 0) return 1;
                    if (memcmp(before, after, BYTES)) keep[k] = 1;
                }
            memcpy(self_mask, keep, nb);
            free(keep);
        }
        size_t ns = 0;
        for (size_t k = 0; k < nb; k++) ns += self_mask[k];
        fprintf(stderr, "coop-perch-chip: mask: %zu blocks (%zu KB) are this box itself (%zu changed in the wide look)\n", ns, ns * BYTES / 1024, nc);
    }
    Span stack[16];
    int depth = 0;                                                    /* stack[depth] = current window; 0 = all of the RAM */
    stack[0].lo = 0; stack[0].hi = size;
    long addr = fixed >= 0 ? fixed : 0;
    int bottom = fixed >= 0;                                          /* at the bottom: watching the 128 bytes */

    unsigned char bits[BYTES], shown[BYTES];
    memset(shown, 0, sizeof shown);
    double lastchg[SIDE], box_changed = now_s();
    int marked[SIDE];
    for (int r = 0; r < SIDE; r++) { lastchg[r] = -10; marked[r] = 0; }
    int full = 1;
    double next = 0;
    int dot = cell - 6, inner = dot - (cell > 14 ? 6 : 4);
    if (inner < 2) inner = 2;
    double level_quiet_since = -1;                                    /* when this level's looks started coming back empty */
    for (;;) {
        if (pump(del)) { XCloseDisplay(dpy); return 0; }
        if (!bottom) {
            /* one zoom step: look at the window, go into its busiest part, or back out if nothing moved */
            Span w = stack[depth];
            set_status(depth, w.lo, w.hi, "looking");
            draw_status();
            long fl = 0;
            size_t plen = 0;
            int closed = 0;
            long p = look(fd, w.lo, w.hi, looks, snap, cur, &fl, &plen, del, &closed);
            if (closed) { XCloseDisplay(dpy); return 0; }
            if (p < 0) { fprintf(stderr, "coop-perch-chip: cannot read the video RAM\n"); return 1; }
            if (fl == 0) {                                             /* nothing moved: look again; quiet for QUIET_S = zoom out */
                double tq = now_s();
                if (level_quiet_since < 0) level_quiet_since = tq;
                if (tq - level_quiet_since >= QUIET_S) { if (depth > 0) depth--; level_quiet_since = -1; }
                continue;
            }
            level_quiet_since = -1;
            size_t nlo = (size_t)p, nhi = nlo + plen < w.hi ? nlo + plen : w.hi;
            if (nhi - nlo <= BYTES || depth + 1 >= 16) { addr = (long)nlo; bottom = 1; box_changed = now_s(); full = 1; }
            else { depth++; stack[depth].lo = nlo; stack[depth].hi = nhi; }
            continue;
        }
        double t = now_s();
        if (t >= next) {
            next = t + 1.0 / rate;
            if (read_all(fd, bits, (size_t)addr, BYTES) != 0) { fprintf(stderr, "coop-perch-chip: read failed\n"); return 1; }
            if (memcmp(bits, shown, BYTES)) box_changed = t;
            if (full) {
                XSetForeground(dpy, gc, white);
                XFillRectangle(dpy, win, gc, 0, 0, (unsigned)W, (unsigned)H);
                XSetForeground(dpy, gc, ink);
                const char *h1 = "The chip's bits", *h2 = "How a bot sees this";
                XDrawString(dpy, win, gc, pad, top - 10, h1, (int)strlen(h1));
                XDrawString(dpy, win, gc, botX, top - 22, h2, (int)strlen(h2));
                XSetForeground(dpy, gc, soft);
                const char *k1 = "1 row = 4 bytes = 1 pixel", *k2 = "px    R   G   B";
                XDrawString(dpy, win, gc, pad + bitsW - (int)strlen(k1) * 7, top - 10, k1, (int)strlen(k1));
                XDrawString(dpy, win, gc, botX + 28, top - 4, k2, (int)strlen(k2));
                set_status(fixed >= 0 ? 0 : depth + 1, (size_t)addr, (size_t)addr + BYTES, fixed >= 0 ? "fixed" : "locked on");
                draw_status();
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
            /* the bot's view: each row's 4 bytes as one pixel, the numbers it reads, a mark (held 1 s) when the row changed */
            uint8_t *bf = NULL;
            if (!full && memcmp(bits, shown, BYTES) && have_box) bf = coop_box_next(&box, (double)time(NULL));
            for (int r = 0; r < SIDE; r++) {
                const unsigned char *p = bits + r * 4, *q = shown + r * 4;
                int chg = !full && memcmp(p, q, 4) != 0;
                if (bf) { bf[r * 3] = p[2]; bf[r * 3 + 1] = p[1]; bf[r * 3 + 2] = p[0]; if (chg) bf[SIDE * 3 + r / 8] |= (uint8_t)(1u << (r % 8)); }
                if (chg) lastchg[r] = t;
                int mark = t - lastchg[r] < 1.0;
                if (!full && !chg && mark == marked[r]) continue;
                marked[r] = mark;
                int y = top + r * cell;
                XSetForeground(dpy, gc, white);
                XFillRectangle(dpy, win, gc, botX, y, (unsigned)botW, (unsigned)cell);
                XSetForeground(dpy, gc, (unsigned long)p[2] << 16 | (unsigned long)p[1] << 8 | p[0]);
                XFillRectangle(dpy, win, gc, botX, y + 3, 18, (unsigned)(cell - 6));
                XSetForeground(dpy, gc, ring);
                XDrawRectangle(dpy, win, gc, botX, y + 3, 18, (unsigned)(cell - 6));
                char line[64];
                snprintf(line, sizeof line, "%2d  %3d %3d %3d", r, p[2], p[1], p[0]);
                XSetForeground(dpy, gc, mark ? blue : ink);
                XDrawString(dpy, win, gc, botX + 28, y + cell - 5, line, (int)strlen(line));
                if (mark) { XSetForeground(dpy, gc, blue); XFillArc(dpy, win, gc, botX + 28 + 17 * 7, y + cell / 2 - 4, 8, 8, 0, 360 * 64); }
            }
            if (bf) coop_box_commit(&box);
            memcpy(shown, bits, sizeof shown);
            full = 0;
            XFlush(dpy);
            if (fixed < 0 && t - box_changed > QUIET_S) { bottom = 0; full = 1; }   /* quiet: zoom out to the window above */
        }
        double wait = next - now_s();
        if (wait > 0) {
            fd_set fs;
            FD_ZERO(&fs);
            FD_SET(ConnectionNumber(dpy), &fs);
            struct timeval tv = {(long)wait, (long)((wait - (long)wait) * 1e6)};
            select(ConnectionNumber(dpy) + 1, &fs, NULL, NULL, &tv);
        }
        while (XPending(dpy)) {
            XEvent e;
            XNextEvent(dpy, &e);
            if (e.type == Expose && e.xexpose.count == 0) full = 1;
            if (e.type == ClientMessage && (Atom)e.xclient.data.l[0] == del) { XCloseDisplay(dpy); return 0; }
            if (e.type == DestroyNotify) return 0;
        }
    }
}
