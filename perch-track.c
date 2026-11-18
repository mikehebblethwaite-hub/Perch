/* Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md. */
/* perch-track: things in a camera's video become TRACKS, each with its own path over time. No grid, no snapshots.
 *
 * Frames come in raw on stdin, decoded by ffmpeg: I420 (-f i420: Y plane, then U and V at half size; colour is kept per track) or
 * grey (-f gray). Every PIXEL keeps its own background and its own restlessness (a running mean and variance): a swaying hedge
 * raises its own bar, still lawn keeps a low one, so a small bird on the lawn still shows while the hedge moves. Each frame the
 * pixels off their background (past 3 of their own deviations and past -T) join into blobs; each blob is matched to the track
 * whose predicted place (last place + its speed) is nearest, inside a gate that grows with its size and speed. A track not seen
 * for a second ends. The whole view jumping (most pixels at once: the camera turned) ends every track and starts the background
 * afresh.
 *
 * Out, on the frame's own clock (unix time, CLOCK_REALTIME: the same clock as the ear's lines):
 *   stdout   t T+ ID x,y wxh            a track is sure (seen 5 frames)
 *            t T- ID secs path_px       a track ended
 *            t C moved                  the view jumped
 *   DIR/tracks.jsonl  one line per ended track (seen >= 5 frames): every sample [t_rel, x, y, area, w, h] and what it moved like:
 *     secs, seen (share of its frames it was seen), path_px, net_px, straight (net/path), speed (mean px/s), speed_p90, still
 *     (share of samples not moving), zone (share of its pixels that were in a restless zone: a bush), stopgo (still<->moving changes a second), area (mean px), area_cv, flutter_hz + flutter
 *     (strongest repeat in its shape, 0..1, from the autocorrelation of its size), heading (deg, 0 = right, 90 = down), down
 *     (share of steps going down), scene (mean share of the whole view off its background while it lived: wind moves everything),
 *     yuv (mean colour), box (x0,y0,x1,y1 it covered).
 * The light changing over the whole view (a cloud, the camera's exposure) is taken off first: a gain (the ratio of all pixels).
 * A pixel off its background more than 12% of the last ~15 s is RESTLESS (a swaying bush); the 8x8 cells holding one, grown a
 * cell all round, are a restless ZONE: no new track is born there (a gust moves the bush's rim too), but a thing already followed
 * is still followed into it (a bird hopping into the hedge).
 * The view moving (a turn, even a slow slide): the frame matched against the last one, both shrunk 4x, shifted +-8 px there; a
 * shift that clearly beats standing still = the view moved: "C moved" once, every track ends, a fresh start.
 * Thin streaks (<= 3 px wide, 4x taller: rain) never start a track.
 * -r FPS: times from the frame count (t = start + frame/FPS) instead of the wall clock: for recorded video and benches.
 *   perch-track -s WxH [-f i420|gray] [-r FPS] [-T 12] [-k 3] [-o DIR] [-min 6 (px)]
 */
#define _GNU_SOURCE
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>

static double now_s(void) { struct timespec t; clock_gettime(CLOCK_REALTIME, &t); return (double)t.tv_sec + t.tv_nsec / 1e9; }

typedef struct { double t; float x, y, a, w, h, Y, U, V, calm; } Sample;
typedef struct { int id, n, cap, missed, sure; Sample *s; float vx, vy; double scene; long frames_alive; } Track;
typedef struct { int x0, y0, x1, y1; long a, calm; double sx, sy, sY, sU, sV; int used; } Blob;

static double RATE = 0; static float RT = 0.03f, RMUL = 1;
static int W, H, gray = 0, T = 12, MINA = 6; static float K = 3.0f;
static int *lab, *par;
/* the whole view moved between two frames? both shrunk 4x; the best shift in +-8 there must beat standing still clearly */
static unsigned char *sa, *sb;
static int view_moved(const unsigned char *a, const unsigned char *b) {
    int w = W / 4, h = H / 4;
    if (!sa) { sa = malloc((size_t)w * h); sb = malloc((size_t)w * h); }
    for (int y = 0; y < h; y++) for (int x = 0; x < w; x++) {
        int s1 = 0, s2 = 0; for (int j = 0; j < 4; j++) for (int i = 0; i < 4; i++) { s1 += a[(size_t)(y * 4 + j) * W + x * 4 + i]; s2 += b[(size_t)(y * 4 + j) * W + x * 4 + i]; }
        sa[y * w + x] = (unsigned char)(s1 / 16); sb[y * w + x] = (unsigned char)(s2 / 16);
    }
    double best = 1e18, still = 0; int bx = 0, by = 0, R = 8;
    for (int dy = -R; dy <= R; dy++) for (int dx = -R; dx <= R; dx++) {
        double e = 0; long c = 0;
        for (int y = R; y < h - R; y += 2) for (int x = R; x < w - R; x += 2) { e += abs(sb[y * w + x] - sa[(y + dy) * w + x + dx]); c++; }
        e /= c; if (!dx && !dy) still = e; if (e < best) { best = e; bx = dx; by = dy; }
    }
    return (bx || by) && best < 0.6 * still && still > 2.0;
}
static int find(int a) { while (par[a] != a) { par[a] = par[par[a]]; a = par[a]; } return a; }

static void add(Track *k, Sample s) {
    if (k->n == k->cap) { k->cap = k->cap ? k->cap * 2 : 64; k->s = realloc(k->s, (size_t)k->cap * sizeof(Sample)); }
    k->s[k->n++] = s;
}
static int cmpf(const void *a, const void *b) { float x = *(const float *)a, y = *(const float *)b; return (x > y) - (x < y); }

static void finish(Track *k, FILE *js, double tend) {
    if (k->n < 5) return;
    Sample *s = k->s; int n = k->n;
    double secs = s[n - 1].t - s[0].t; if (secs <= 0) secs = 1e-3;
    double calm = 0; for (int i = 0; i < n; i++) calm += s[i].calm;
    double path = 0, am = 0, a2 = 0, Y = 0, U = 0, V = 0, down = 0, hx = 0, hy = 0;
    float x0 = 1e9, y0 = 1e9, x1 = -1, y1 = -1;
    float *sp = malloc(sizeof(float) * (size_t)n); int ns = 0, still = 0, changes = 0, prev = -1;
    for (int i = 0; i < n; i++) {
        am += s[i].a; a2 += (double)s[i].a * s[i].a; Y += s[i].Y; U += s[i].U; V += s[i].V;
        if (s[i].x - s[i].w / 2 < x0) x0 = s[i].x - s[i].w / 2; if (s[i].y - s[i].h / 2 < y0) y0 = s[i].y - s[i].h / 2;
        if (s[i].x + s[i].w / 2 > x1) x1 = s[i].x + s[i].w / 2; if (s[i].y + s[i].h / 2 > y1) y1 = s[i].y + s[i].h / 2;
        if (i) {
            double dx = s[i].x - s[i - 1].x, dy = s[i].y - s[i - 1].y, d = sqrt(dx * dx + dy * dy), dt = s[i].t - s[i - 1].t;
            path += d; hx += dx; hy += dy; if (dy > 0.5) down++;
            float v = dt > 0 ? (float)(d / dt) : 0; sp[ns++] = v;
            int mv = v > 12.0f + 0.6f * sqrtf(s[i].a);           /* moving: faster than its own shimmer (bigger things wobble more) */
            if (!mv) still++; if (prev >= 0 && mv != prev) changes++; prev = mv;
        }
    }
    am /= n; double acv = sqrt(fmax(0, a2 / n - am * am)) / (am > 0 ? am : 1);
    double net = hypot(s[n - 1].x - s[0].x, s[n - 1].y - s[0].y);
    qsort(sp, (size_t)ns, sizeof(float), cmpf);
    double smean = 0; for (int i = 0; i < ns; i++) smean += sp[i]; smean = ns ? smean / ns : 0;
    double p90 = ns ? sp[(int)(0.9 * (ns - 1))] : 0;
    /* flutter: the size series (its slow trend taken off) against itself, shifted 2..20 frames: the strongest repeat */
    double fhz = 0, fpow = 0, fps = (n - 1) / secs;
    if (n >= 16) {
        double *d = malloc(sizeof(double) * (size_t)n);
        for (int i = 0; i < n; i++) { double m = 0; int c = 0; for (int j = i - 4; j <= i + 4; j++) if (j >= 0 && j < n) { m += s[j].a; c++; } d[i] = s[i].a - m / c; }
        double z = 0; for (int i = 0; i < n; i++) z += d[i] * d[i];
        double ac[23] = {0}; int bl_ = 0;
        if (z > 0) for (int L = 1; L <= 21 && L < n / 2; L++) {
            double c = 0; for (int i = 0; i + L < n; i++) c += d[i] * d[i + L]; ac[L] = c / (z * (double)(n - L) / n);
        }
        for (int L = 2; L <= 20 && L + 1 < n / 2; L++) if (ac[L] > ac[L - 1] && ac[L] >= ac[L + 1] && ac[L] > fpow) { fpow = ac[L]; bl_ = L; }
        if (bl_) { double a0 = ac[bl_ - 1], a1 = ac[bl_], a2 = ac[bl_ + 1], den = a0 - 2 * a1 + a2;
                   double off = den < 0 ? 0.5 * (a0 - a2) / den : 0; fhz = fps / (bl_ + off); }
        free(d);
    }
    fprintf(js, "{\"id\":%d,\"from\":%.3f,\"to\":%.3f,\"secs\":%.2f,\"seen\":%.2f,\"path_px\":%.1f,\"net_px\":%.1f,\"straight\":%.2f,"
                "\"speed\":%.1f,\"speed_p90\":%.1f,\"still\":%.2f,\"stopgo\":%.2f,\"area\":%.1f,\"area_cv\":%.2f,\"flutter_hz\":%.2f,\"flutter\":%.2f,"
                "\"heading\":%.0f,\"down\":%.2f,\"scene\":%.4f,\"zone\":%.2f,\"yuv\":[%.0f,%.0f,%.0f],\"box\":[%.0f,%.0f,%.0f,%.0f],\"ended\":%.3f,\"path\":[",
            k->id, s[0].t, s[n - 1].t, secs, k->frames_alive ? (double)n / k->frames_alive : 1, path, net, path > 0 ? net / path : 0,
            smean, p90, ns ? (double)still / ns : 1, changes / secs, am, acv, fhz, fpow,
            fmod(atan2(hy, hx) * 180 / M_PI + 360, 360), ns ? down / ns : 0, k->frames_alive ? k->scene / k->frames_alive : 0, 1 - calm / n,
            Y / n, U / n, V / n, x0, y0, x1, y1, tend);
    for (int i = 0; i < n; i++) fprintf(js, "%s[%.2f,%.1f,%.1f,%.0f,%.0f,%.0f]", i ? "," : "", s[i].t - s[0].t, s[i].x, s[i].y, s[i].a, s[i].w, s[i].h);
    fprintf(js, "]}\n"); fflush(js);
    printf("%.2f T- %d %.1f %.0f\n", tend, k->id, secs, path);
    free(sp);
}

int main(int argc, char **argv) {
    const char *outdir = NULL;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "-s") && i + 1 < argc) sscanf(argv[++i], "%dx%d", &W, &H);
        else if (!strcmp(argv[i], "-f") && i + 1 < argc) gray = !strcmp(argv[++i], "gray");
        else if (!strcmp(argv[i], "-r") && i + 1 < argc) RATE = atof(argv[++i]);
        else if (!strcmp(argv[i], "-R") && i + 1 < argc) RT = (float)atof(argv[++i]);
        else if (!strcmp(argv[i], "-M") && i + 1 < argc) RMUL = (float)atof(argv[++i]);
        else if (!strcmp(argv[i], "-T") && i + 1 < argc) T = atoi(argv[++i]);
        else if (!strcmp(argv[i], "-k") && i + 1 < argc) K = (float)atof(argv[++i]);
        else if (!strcmp(argv[i], "-min") && i + 1 < argc) MINA = atoi(argv[++i]);
        else if (!strcmp(argv[i], "-o") && i + 1 < argc) outdir = argv[++i];
        else { fprintf(stderr, "usage: ... | %s -s WxH [-f i420|gray] [-r FPS] [-T 12] [-k 3] [-min 6] [-o DIR]\n", argv[0]); return 2; }
    }
    if (W <= 0 || H <= 0 || (!gray && (W % 2 || H % 2))) { fprintf(stderr, "perch-track: give the frame size, -s WxH (even for i420)\n"); return 2; }
    size_t n = (size_t)W * H, fsz = gray ? n : n + n / 2;
    unsigned char *f = malloc(fsz), *fg = calloc(n, 1), *fg2 = calloc(n, 1);
    float *mu = malloc(n * sizeof(float)), *var = malloc(n * sizeof(float));
    float *rest = calloc(n, sizeof(float)); unsigned char *own = calloc(n, 1);
    unsigned char *zone = calloc((size_t)((W + 7) / 8) * ((H + 7) / 8), 1), *zone2 = calloc((size_t)((W + 7) / 8) * ((H + 7) / 8), 1);
    unsigned char *prev = malloc(n); unsigned short *steady = calloc(n, sizeof(unsigned short));
    lab = malloc(n * sizeof(int)); par = malloc((n / 2 + 2) * sizeof(int));
    char path[1200]; snprintf(path, sizeof path, "%s/tracks.jsonl", outdir ? outdir : ".");
    if (outdir) mkdir(outdir, 0750);
    FILE *js = fopen(path, "a"); if (!js) { perror(path); return 1; }
    setvbuf(stdout, NULL, _IOLBF, 0);
    int cap = 256, nt = 0, next_id = 1; Track *tr = calloc((size_t)cap, sizeof(Track));
    int bcap = 1024; Blob *bl = malloc((size_t)bcap * sizeof(Blob));
    long frames = 0, fall = 0; int vm_on = 0; long learnt = 0; double start = now_s(), warm = 0, settle = 0, lastt = 0;
    const float T2 = (float)T * T;
    const float RA = 1.0f / (float)(15 * (RATE > 0 ? RATE : 15));
    const int STEADY = (int)(8 * (RATE > 0 ? RATE : 15));
    while (fread(f, 1, fsz, stdin) == fsz) {
        double t = RATE > 0 ? start + fall / RATE : now_s(); frames++; fall++;
        const unsigned char *Yp = f, *Up = gray ? NULL : f + n, *Vp = gray ? NULL : f + n + n / 4;
        if (frames == 1 || t < settle) {
            for (size_t i = 0; i < n; i++) { mu[i] = Yp[i]; if (frames == 1) var[i] = T2; steady[i] = 0; }
            memcpy(prev, Yp, n); warm = t + 2; lastt = t; continue;
        }
        learnt++; float ra = learnt * RA * RMUL < 1 ? RMUL / (float)learnt : RA;
        if (t < warm) {                                        /* the first 2 s only learn the scene (a thing passing is averaged out) */
            for (size_t i = 0; i < n; i++) { float d = Yp[i] - mu[i]; mu[i] += 0.15f * d; var[i] += 0.15f * (d * d - var[i]); if (var[i] < 4) var[i] = 4;
                                             if (frames > 4) { float bar = K * K * var[i]; if (bar < T2) bar = T2; rest[i] += ((d * d > bar) - rest[i]) * ra; } }
            memcpy(prev, Yp, n);
            lastt = t; continue;
        }
        /* each pixel against its own background and its own restlessness */
        long nfg = 0; double sh = 0;
        double sm = 0;
        for (size_t i = 0; i < n; i += 7) { sh += Yp[i]; sm += mu[i]; }
        float gain = sm > 0 ? (float)(sh / sm) : 1;            /* the whole view's light change (a cloud): a ratio, not a thing moving */
        for (size_t i = 0; i < n; i++) {
            mu[i] *= gain;
            float d = Yp[i] - mu[i], d2 = d * d, bar = K * K * var[i]; if (bar < T2) bar = T2;
            int off = d2 > bar;
            if (!own[i]) rest[i] += (off - rest[i]) * ra;      /* a thing being followed does not make its own ground restless */
            fg[i] = off; nfg += off;
            /* a thing standing still fades into the background only slowly; but a pixel that has not changed at all for 8 s is
               the scene (a "ghost": where something stood when watching began, or a thing put down and left) and joins it fast */
            if (abs(Yp[i] - prev[i]) > T) steady[i] = 0; else if (steady[i] < 65535) steady[i]++;
            float a = !fg[i] ? 0.03f : steady[i] > STEADY ? 0.1f : 0.002f;
            mu[i] += a * d; var[i] += a * (d2 - var[i]); if (var[i] < 4) var[i] = 4;
        }
        double scene = (double)nfg / n;
        /* the restless ZONE: 8x8 cells holding a restless pixel, grown one cell all round (a bush's rim moves more in a gust) */
        int zw = (W + 7) / 8, zh = (H + 7) / 8;
        memset(zone, 0, (size_t)zw * zh);
        for (int y = 0; y < H; y++) for (int x = 0; x < W; x++) if (rest[(size_t)y * W + x] >= RT) zone[(y / 8) * zw + x / 8] = 1;
        memset(zone2, 0, (size_t)zw * zh);
        for (int y = 0; y < zh; y++) for (int x = 0; x < zw; x++) if (zone[y * zw + x])
            for (int dy = -1; dy <= 1; dy++) for (int dx = -1; dx <= 1; dx++) { int yy = y + dy, xx = x + dx; if (yy >= 0 && yy < zh && xx >= 0 && xx < zw) zone2[yy * zw + xx] = 1; }
        int vm = view_moved(prev, Yp);
        if (scene > 0.35 || vm) {                                    /* the view jumped (the camera turned): all tracks end, start afresh */
            if (!vm_on) printf("%.2f C moved\n", t);
            vm_on = 1;
            for (int k = 0; k < nt; k++) { finish(&tr[k], js, t); free(tr[k].s); }
            nt = 0; settle = t + 0.6; frames = 0; memcpy(prev, Yp, n); continue;
        }
        vm_on = 0; memcpy(prev, Yp, n);
        /* a speck alone is noise: a pixel counts when at least 3 of its 3x3 also do; then one pixel of growth joins near parts */
        memset(fg2, 0, n);
        for (int y = 1; y < H - 1; y++) for (int x = 1; x < W - 1; x++) {
            size_t i = (size_t)y * W + x; if (!fg[i]) continue;
            int c = fg[i - W - 1] + fg[i - W] + fg[i - W + 1] + fg[i - 1] + fg[i + 1] + fg[i + W - 1] + fg[i + W] + fg[i + W + 1];
            if (c >= 2) for (int dy = -1; dy <= 1; dy++) for (int dx = -1; dx <= 1; dx++) fg2[i + dy * W + dx] = 1;
        }
        /* blobs: two passes, 8-neighbours */
        int nl = 0;
        for (int y = 0; y < H; y++) for (int x = 0; x < W; x++) {
            size_t i = (size_t)y * W + x; lab[i] = -1; if (!fg2[i]) continue;
            int ns[4], c = 0;
            if (x > 0 && lab[i - 1] >= 0) ns[c++] = lab[i - 1];
            if (y > 0) { if (lab[i - W] >= 0) ns[c++] = lab[i - W]; if (x > 0 && lab[i - W - 1] >= 0) ns[c++] = lab[i - W - 1]; if (x < W - 1 && lab[i - W + 1] >= 0) ns[c++] = lab[i - W + 1]; }
            if (!c) { if (nl >= (int)(n / 2)) continue; par[nl] = nl; lab[i] = nl++; continue; }
            int r = find(ns[0]); for (int j = 1; j < c; j++) { int q = find(ns[j]); if (q != r) { if (q < r) { par[r] = q; r = q; } else par[q] = r; } }
            lab[i] = r;
        }
        int nb = 0; int *map = malloc((size_t)(nl + 1) * sizeof(int)); for (int j = 0; j < nl; j++) map[j] = -1;
        for (int y = 0; y < H; y++) for (int x = 0; x < W; x++) {
            size_t i = (size_t)y * W + x; if (lab[i] < 0) continue;
            int r = find(lab[i]);
            if (map[r] < 0) { if (nb == bcap) { bcap *= 2; bl = realloc(bl, (size_t)bcap * sizeof(Blob)); } map[r] = nb; bl[nb++] = (Blob){x, y, x, y, 0, 0, 0, 0, 0, 0, 0, 0}; }
            Blob *b = &bl[map[r]];
            if (!fg[i]) continue;                               /* sizes and places from the pixels really off, not the growth */
            b->a++; b->calm += !zone2[(y / 8) * ((W + 7) / 8) + x / 8]; b->sx += x; b->sy += y; b->sY += Yp[i];
            if (Up) { size_t c = (size_t)(y / 2) * (W / 2) + x / 2; b->sU += Up[c]; b->sV += Vp[c]; } else { b->sU += 128; b->sV += 128; }
            if (x < b->x0) b->x0 = x; if (x > b->x1) b->x1 = x; if (y < b->y0) b->y0 = y; if (y > b->y1) b->y1 = y;
        }
        free(map);
        /* blobs to tracks: every pair inside the gate, nearest first */
        double dt = t - lastt; lastt = t; if (dt <= 0 || dt > 1) dt = 1.0 / 15;
        typedef struct { float c; int k, b; } Pair;
        int pcap = 256, np = 0; Pair *pr = malloc((size_t)pcap * sizeof(Pair));
        for (int k = 0; k < nt; k++) {
            Sample *l = &tr[k].s[tr[k].n - 1]; double gap = t - l->t;
            float px = l->x + tr[k].vx * (float)gap, py = l->y + tr[k].vy * (float)gap;
            float gate = 10 + 1.5f * sqrtf(l->a) + 0.5f * sqrtf(tr[k].vx * tr[k].vx + tr[k].vy * tr[k].vy) * (float)gap;
            for (int b = 0; b < nb; b++) {
                if (bl[b].a < 2) continue;
                float bx = (float)(bl[b].sx / bl[b].a), by = (float)(bl[b].sy / bl[b].a), d = hypotf(bx - px, by - py);
                float ra = bl[b].a > l->a ? bl[b].a / (l->a + 1) : l->a / (bl[b].a + 1.0f);
                if (d < gate && ra < 8) { if (np == pcap) { pcap *= 2; pr = realloc(pr, (size_t)pcap * sizeof(Pair)); } pr[np++] = (Pair){d / gate + 0.1f * logf(ra), k, b}; }
            }
        }
        for (int i = 1; i < np; i++) { Pair p = pr[i]; int j = i - 1; while (j >= 0 && pr[j].c > p.c) { pr[j + 1] = pr[j]; j--; } pr[j + 1] = p; }
        int *tk = calloc((size_t)nt + 1, sizeof(int));
        for (int i = 0; i < np; i++) {
            Track *k = &tr[pr[i].k]; Blob *b = &bl[pr[i].b];
            if (tk[pr[i].k] || b->used) continue; tk[pr[i].k] = 1; b->used = 1;
            Sample s = {t, (float)(b->sx / b->a), (float)(b->sy / b->a), (float)b->a, (float)(b->x1 - b->x0 + 1), (float)(b->y1 - b->y0 + 1),
                        (float)(b->sY / b->a), (float)(b->sU / b->a), (float)(b->sV / b->a), (float)b->calm / b->a};
            Sample *l = &k->s[k->n - 1]; double g = t - l->t;
            if (g > 0) { float nvx = (s.x - l->x) / (float)g, nvy = (s.y - l->y) / (float)g; k->vx = 0.6f * k->vx + 0.4f * nvx; k->vy = 0.6f * k->vy + 0.4f * nvy; }
            add(k, s); k->missed = 0;
            if (!k->sure && k->n >= 5) { k->sure = 1; printf("%.2f T+ %d %.0f,%.0f %.0fx%.0f\n", t, k->id, s.x, s.y, s.w, s.h); }
        }
        free(pr);
        for (int k = 0; k < nt; k++) { tr[k].frames_alive++; tr[k].scene += scene; if (!tk[k]) tr[k].missed++; }
        free(tk);
        /* new tracks from blobs nobody took */
        for (int b = 0; b < nb; b++) {
            if (bl[b].used || bl[b].a < MINA) continue;
            int bw = bl[b].x1 - bl[b].x0 + 1, bh = bl[b].y1 - bl[b].y0 + 1;
            if (bw <= 3 && bh >= 4 * bw) continue;              /* a streak (rain): not a thing to follow */
            if (bl[b].calm * 2 < bl[b].a) continue;             /* born in a restless zone (a bush): not a new thing; a thing already
                                                                   followed may still go into the bush (it matches above) */
            if (nt == cap) { cap *= 2; tr = realloc(tr, (size_t)cap * sizeof(Track)); }
            Track *k = &tr[nt++]; memset(k, 0, sizeof *k); k->id = next_id++; k->frames_alive = 1; k->scene = scene;
            Blob *B = &bl[b];
            add(k, (Sample){t, (float)(B->sx / B->a), (float)(B->sy / B->a), (float)B->a, (float)(B->x1 - B->x0 + 1), (float)(B->y1 - B->y0 + 1),
                            (float)(B->sY / B->a), (float)(B->sU / B->a), (float)(B->sV / B->a), (float)B->calm / B->a});
        }
        /* the ground each followed thing covers now (its box, 4 px round): kept out of the restlessness next frame */
        memset(own, 0, n);
        for (int k = 0; k < nt; k++) { Track *q = &tr[k]; if (q->n < 3 || q->s[q->n - 1].t != t) continue; Sample *l = &q->s[q->n - 1];
            int x0 = (int)(l->x - l->w / 2) - 4, x1 = (int)(l->x + l->w / 2) + 4, y0 = (int)(l->y - l->h / 2) - 4, y1 = (int)(l->y + l->h / 2) + 4;
            if (x0 < 0) x0 = 0; if (y0 < 0) y0 = 0; if (x1 >= W) x1 = W - 1; if (y1 >= H) y1 = H - 1;
            for (int y = y0; y <= y1; y++) memset(own + (size_t)y * W + x0, 1, (size_t)(x1 - x0 + 1)); }
        /* a track unseen for a second ends */
        for (int k = 0; k < nt;) {
            if (t - tr[k].s[tr[k].n - 1].t > 1.0) { if (tr[k].sure) finish(&tr[k], js, t); free(tr[k].s); tr[k] = tr[--nt]; }
            else k++;
        }
    }
    double t = RATE > 0 ? start + fall / RATE : now_s();
    for (int k = 0; k < nt; k++) { if (tr[k].sure) finish(&tr[k], js, t); free(tr[k].s); }
    fprintf(stderr, "perch-track: %ld frames, %d tracks begun\n", frames, next_id - 1);
    return 0;
}
