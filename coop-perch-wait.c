/* Copyright (c) 2026 Mike J Hebblethwaite. All rights reserved. See COPYRIGHT. */
/* coop-perch-wait: sleep until the screen changes, then say where, in one line. For bots (Claude) instead of screenshots.
 *
 * Reads the eye's plane in the box of bot vectors (the eye must be running on the display). Sleeps on the plane's wake word; on the
 * first strike it keeps collecting until the screen has been still for the settle time, then prints the box around everything
 * that changed, and the boxes per area:
 *     S x,y wxh n=CELLS | x,y wxh x,y wxh ...
 * Nothing changed before the timeout: prints "quiet" and exits 1.
 * Animation is left out: the eye marks cells that change nonstop (strike 3); they never hold up "settled" or make a line.
 *
 *   coop-perch-wait [-t SECONDS(10)] [-q SETTLE_MS(400)] [-r x,y,w,h (only changes touching this region)]
 *   -f  follow: keep going for -t seconds, one line per settled change, each starting "first_seen_time printed_time"
 */
#include "coop-perch-box.h"

typedef struct { int x0, y0, x1, y1; } Rect;

static double now_s(void) { struct timespec t; clock_gettime(CLOCK_REALTIME, &t); return (double)t.tv_sec + t.tv_nsec / 1e9; }

int main(int argc, char **argv) {
    const char *plane = "eye";
    double timeout = 10;
    int settle = 400, rx = 0, ry = 0, rw = 0, rh = 0, follow = 0;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "-t") && i + 1 < argc) timeout = atof(argv[++i]);
        else if (!strcmp(argv[i], "-q") && i + 1 < argc) settle = atoi(argv[++i]);
        else if (!strcmp(argv[i], "-f")) follow = 1;
        else if (!strcmp(argv[i], "-p") && i + 1 < argc) plane = argv[++i];   /* e.g. vm-guest: a Perch VM's screen */
        else if (!strcmp(argv[i], "-r") && i + 1 < argc) { if (sscanf(argv[++i], "%d,%d,%d,%d", &rx, &ry, &rw, &rh) != 4) return 2; }
        else { fprintf(stderr, "usage: %s [-p PLANE] [-t SECONDS] [-q SETTLE_MS] [-r x,y,w,h]\n", argv[0]); return 2; }
    }
    CoopBox b;
    if (coop_box_open(&b, plane) != 0) { fprintf(stderr, "no %s plane in %s (start its eye: coop-perch-display or coop-perch-vmrun)\n", plane, coop_box_dir()); return 2; }
    if (strcmp(b.h->dtype, "u16") || b.h->ndim != 3 || b.h->dim[2] != 9) { fprintf(stderr, "eye plane: unexpected layout\n"); return 2; }
    uint32_t ncell = b.h->dim[0] * b.h->dim[1];
    Rect *cells = calloc(ncell, sizeof *cells);                         /* per cell: everything that changed in it so far */
    if (!cells) return 2;
    Rect *area = calloc(ncell, sizeof *area);
    if (!area) return 2;
    uint64_t seen = __atomic_load_n(&b.h->count, __ATOMIC_ACQUIRE);
    double total_end = now_s() + timeout;
    for (;;) {
    for (uint32_t i = 0; i < ncell; i++) cells[i].x1 = -1;
    double end = follow ? total_end : now_s() + timeout, last = 0, first = 0;
    int got = 0;
    for (;;) {
        double t = now_s();
        double left = got && last + settle / 1000.0 < end ? last + settle / 1000.0 - t : end - t;   /* settle, but never past the timeout */
        if (left <= 0) break;
        uint64_t c = coop_box_wait(&b, seen, (int)(left * 1000) + 1);
        for (uint64_t k = seen; k < c; k++) {
            const void *fv;
            if (coop_box_frame(&b, k, &fv) < 0) continue;
            const uint16_t *f = fv;
            for (uint32_t i = 0; i < ncell; i++) {
                const uint16_t *v = f + i * 9;
                if (v[4] != 1 || v[7] == 0) continue;                    /* strikes only (tuned-out beats and animation ignored) */
                int x0 = v[5], y0 = v[6], x1 = v[5] + v[7], y1 = v[6] + v[8];
                if (rw > 0 && (x1 <= rx || x0 >= rx + rw || y1 <= ry || y0 >= ry + rh)) continue;
                Rect *r = &cells[i];
                if (r->x1 < 0) { r->x0 = x0; r->y0 = y0; r->x1 = x1; r->y1 = y1; }
                else { if (x0 < r->x0) r->x0 = x0; if (y0 < r->y0) r->y0 = y0; if (x1 > r->x1) r->x1 = x1; if (y1 > r->y1) r->y1 = y1; }
                if (!got) first = now_s();
                got = 1; last = now_s();
            }
        }
        seen = c;
    }
    if (!got) { if (follow) break; printf("quiet\n"); return 1; }
    /* join touching cells into areas, so one change spread over cells is one box */
    int n = 0;
    for (uint32_t i = 0; i < ncell; i++) if (cells[i].x1 >= 0) area[n++] = cells[i];
    for (int merged = 1; merged;) {
        merged = 0;
        for (int i = 0; i < n && !merged; i++)
            for (int j = i + 1; j < n && !merged; j++)
                if (area[i].x0 <= area[j].x1 + 2 && area[j].x0 <= area[i].x1 + 2 && area[i].y0 <= area[j].y1 + 2 && area[j].y0 <= area[i].y1 + 2) {
                    if (area[j].x0 < area[i].x0) area[i].x0 = area[j].x0;
                    if (area[j].y0 < area[i].y0) area[i].y0 = area[j].y0;
                    if (area[j].x1 > area[i].x1) area[i].x1 = area[j].x1;
                    if (area[j].y1 > area[i].y1) area[i].y1 = area[j].y1;
                    area[j] = area[--n]; merged = 1;
                }
    }
    Rect all = area[0];
    for (int i = 1; i < n; i++) {
        if (area[i].x0 < all.x0) all.x0 = area[i].x0;
        if (area[i].y0 < all.y0) all.y0 = area[i].y0;
        if (area[i].x1 > all.x1) all.x1 = area[i].x1;
        if (area[i].y1 > all.y1) all.y1 = area[i].y1;
    }
    if (follow) printf("%.3f %.3f ", first, now_s());
    printf("S %d,%d %dx%d n=%d |", all.x0, all.y0, all.x1 - all.x0, all.y1 - all.y0, n);
    for (int i = 0; i < n; i++) printf(" %d,%d %dx%d", area[i].x0, area[i].y0, area[i].x1 - area[i].x0, area[i].y1 - area[i].y0);
    printf("\n");
    fflush(stdout);
    if (!follow) return 0;
    }
    return 0;
}
