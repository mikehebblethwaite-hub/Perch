/* coop-perch-tap: sits on the data line of the Perch display. Apps connect to :2 as usual; the tap passes every byte on to the
 * real X server (Xvfb on :12, started by coop-perch-display) and reads the requests as they go by.
 *   - no bytes on the line = nothing changed: the tap writes nothing, does nothing (it sleeps in poll)
 *   - each second that had traffic: one line per app (named from its process): bytes, requests, the kinds of drawing
 *   - text drawn with X text requests (PolyText8 / ImageText8) arrives as text: the tap keeps what is at each spot and writes only
 *     what changed ("text 200,415 'firefox' was 'chrome'"). Apps that draw text as glyph images (Xft, browsers) send no text here.
 * Out: the box of bot vectors, plane "line" (shared memory, see coop-perch-box.h): a frame per second with traffic = request counts
 *      by kind (256), bytes in, bytes out, apps on the line. Raw, for bots. The text bottom layer goes to tape/tape.txt, "line:" lines.
 * Build: make     Run: coop-perch-tap [-d 2] [-s 12] [-o DIR]
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <stddef.h>
#include <time.h>
#include <unistd.h>
#include "coop-perch-box.h"

typedef struct {
    int c, s;                         /* app side, server side */
    int setup;                        /* 0 = connection setup not read yet */
    int big;                          /* client byte order is MSB first */
    unsigned char hdr[12]; int nhdr;  /* the setup (12 bytes) or request header (4, or 8 with BIG-REQUESTS) being read */
    size_t left;                      /* bytes still to pass of the current request */
    unsigned char *req; size_t nreq, capreq, want;   /* a drawing request kept (want = bytes to keep; the rest only passed) */
    size_t total;                     /* the kept request's full length */
    int keep;
    int pid;
    char name[32];
    long bytes_in, bytes_out, reqs, kinds[256];        /* this second */
    long tot_in;
} Conn;

typedef struct { uint32_t draw; int x, y; char *text; } Spot;   /* what text is at each place */

static Conn *conns = NULL; static int nconn = 0, capconn = 0;
static Spot *spots = NULL; static int nspot = 0, capspot = 0;
static FILE *tape = NULL;
static volatile sig_atomic_t stop_now = 0;
static void on_stop(int s) { (void)s; stop_now = 1; }

static void now_txt(char *o, size_t n) { time_t t = time(NULL); struct tm tm; localtime_r(&t, &tm); strftime(o, n, "%H:%M:%S", &tm); }
static uint32_t rd16(const unsigned char *p, int big) { return big ? (uint32_t)(p[0] << 8 | p[1]) : (uint32_t)(p[1] << 8 | p[0]); }
static uint32_t rd32(const unsigned char *p, int big) {
    return big ? ((uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 8 | p[3]) : ((uint32_t)p[3] << 24 | (uint32_t)p[2] << 16 | (uint32_t)p[1] << 8 | p[0]);
}

static const char *kind_name(int op) {
    switch (op) {
    case 1: return "CreateWindow"; case 8: return "MapWindow"; case 12: return "ConfigureWindow"; case 53: return "CreatePixmap";
    case 55: return "CreateGC"; case 56: return "ChangeGC"; case 61: return "ClearArea"; case 62: return "CopyArea";
    case 64: return "PolyPoint"; case 65: return "PolyLine"; case 66: return "PolySegment"; case 67: return "PolyRectangle";
    case 69: return "FillPoly"; case 70: return "PolyFillRectangle"; case 72: return "PutImage"; case 73: return "GetImage";
    case 74: return "PolyText8"; case 76: return "ImageText8"; case 43: return "GetInputFocus";
    default: return op >= 128 ? "extension" : NULL;
    }
}

static void text_at(Conn *k, uint32_t draw, int x, int y, const char *s) {
    for (int i = 0; i < nspot; i++)
        if (spots[i].draw == draw && spots[i].x == x && spots[i].y == y) {
            if (!strcmp(spots[i].text, s)) return;                   /* same text redrawn: nothing to say */
            char ts[16]; now_txt(ts, sizeof ts);
            fprintf(tape, "%s line: %s text %d,%d '%s' was '%s'\n", ts, k->name, x, y, s, spots[i].text);
            free(spots[i].text); spots[i].text = strdup(s ? s : "");
            return;
        }
    if (nspot == capspot) {
        int nc = capspot ? capspot * 2 : 256;
        Spot *ns = realloc(spots, (size_t)nc * sizeof(Spot));
        if (!ns) return;
        spots = ns; capspot = nc;
    }
    spots[nspot].draw = draw; spots[nspot].x = x; spots[nspot].y = y; spots[nspot].text = strdup(s ? s : "");
    nspot++;
    char ts[16]; now_txt(ts, sizeof ts);
    fprintf(tape, "%s line: %s text %d,%d '%s'\n", ts, k->name, x, y, s);
}

/* ---- the interpreter: what each app is drawing, where on the screen, in what colour ---- */
typedef struct { uint32_t id, parent; int x, y, w, h, mapped; } XWin;   /* windows the apps made (parent not here = the root) */
typedef struct { uint32_t id, fg; } XGc;                                 /* graphics contexts: the drawing colour */
static XWin *xw = NULL; static int nxw = 0, capxw = 0;
static XGc *xg = NULL; static int nxg = 0, capxg = 0;
static CoopBox dbox; static int have_dbox = 0;
#define DRAW_FIELDS 76
#define DRAW_CHARS 64

static XWin *xw_find(uint32_t id) { for (int i = 0; i < nxw; i++) if (xw[i].id == id) return &xw[i]; return NULL; }
static XWin *xw_add(uint32_t id) {
    XWin *w = xw_find(id);
    if (w) return w;
    if (nxw == capxw) { int nc = capxw ? capxw * 2 : 128; XWin *n = realloc(xw, (size_t)nc * sizeof *n); if (!n) return NULL; xw = n; capxw = nc; }
    w = &xw[nxw++]; memset(w, 0, sizeof *w); w->id = id;
    return w;
}
static XGc *xg_find(uint32_t id) { for (int i = 0; i < nxg; i++) if (xg[i].id == id) return &xg[i]; return NULL; }
static XGc *xg_add(uint32_t id) {
    XGc *g = xg_find(id);
    if (g) return g;
    if (nxg == capxg) { int nc = capxg ? capxg * 2 : 128; XGc *n = realloc(xg, (size_t)nc * sizeof *n); if (!n) return NULL; xg = n; capxg = nc; }
    g = &xg[nxg++]; g->id = id; g->fg = 0;
    return g;
}
/* where a drawable's (0,0) is on the screen, and whether it can be seen (window + every parent we know mapped). Pixmaps: off screen. */
static int screen_pos(uint32_t d, int *sx, int *sy) {
    XWin *w = xw_find(d);
    if (!w) { *sx = 0; *sy = 0; return d ? -1 : 0; }               /* not a window we know: a pixmap (or another app's window) */
    int x = 0, y = 0, on = 1, hops = 0;
    while (w && hops++ < 64) { x += w->x; y += w->y; if (!w->mapped) on = 0; w = xw_find(w->parent); }
    *sx = x; *sy = y;
    return on;
}
static void draw_event(Conn *k, int op, uint32_t d, int x, int y, int w, int h, uint32_t colour, const char *text, int nchars) {
    if (!have_dbox) return;
    int sx, sy, on = screen_pos(d, &sx, &sy);
    float *f = coop_box_next(&dbox, (double)time(NULL));
    if (!f) return;
    memset(f, 0, DRAW_FIELDS * sizeof(float));
    f[0] = (float)k->pid; f[1] = (float)op; f[2] = (float)(sx + x); f[3] = (float)(sy + y); f[4] = (float)w; f[5] = (float)h;
    f[6] = (float)(colour & 0xffffff); f[7] = (float)(d >> 16); f[8] = (float)(d & 0xffff); f[9] = (float)(on > 0 ? 1 : on < 0 ? -1 : 0);
    f[10] = (float)nchars;
    for (int i = 0; i < nchars && i < DRAW_CHARS && text; i++) f[12 + i] = (float)(unsigned char)text[i];
    coop_box_commit(&dbox);
}
static uint32_t gc_colour(uint32_t gc) { XGc *g = xg_find(gc); return g ? g->fg : 0; }
/* GC value list: bit 0 function, bit 1 plane-mask, bit 2 foreground: the foreground is after the set bits below it */
static void gc_values(uint32_t gc, uint32_t mask, const unsigned char *v, size_t n, int big) {
    if (!(mask & 4)) return;
    size_t at = (size_t)(((mask & 1) ? 1 : 0) + ((mask & 2) ? 1 : 0)) * 4;
    if (at + 4 > n) return;
    XGc *g = xg_add(gc);
    if (g) g->fg = rd32(v + at, big);
}
static void bbox_points(const unsigned char *p, size_t n, int big, int *x0, int *y0, int *x1, int *y1, int relative) {
    int cx = 0, cy = 0, first = 1;
    for (size_t i = 0; i + 4 <= n; i += 4) {
        int x = (int16_t)rd16(p + i, big), y = (int16_t)rd16(p + i + 2, big);
        if (relative && !first) { x += cx; y += cy; }
        cx = x; cy = y;
        if (first || x < *x0) *x0 = x;
        if (first || y < *y0) *y0 = y;
        if (first || x > *x1) *x1 = x;
        if (first || y > *y1) *y1 = y;
        first = 0;
    }
}
static void read_text(Conn *k, const unsigned char *r, size_t n);
/* one whole (or head-kept) request: update the windows and colours, and send what was drawn to the box */
static void interpret(Conn *k, const unsigned char *r, size_t n) {
    int big = k->big, op = r[0];
    if (n < 8) return;
    uint32_t a = rd32(r + 4, big);
    switch (op) {
    case 1: if (n >= 24) {                                            /* CreateWindow: wid, parent, x, y, w, h */
            XWin *w = xw_add(a); /* wid is at 4 */
            if (w) { w->parent = rd32(r + 8, big); w->x = (int16_t)rd16(r + 12, big); w->y = (int16_t)rd16(r + 14, big); w->w = rd16(r + 16, big); w->h = rd16(r + 18, big); w->mapped = 0; }
        } break;
    case 4: { for (int i = 0; i < nxw; i++) if (xw[i].id == a) { xw[i] = xw[--nxw]; break; } } break;   /* DestroyWindow */
    case 7: if (n >= 16) { XWin *w = xw_add(a); if (w) { w->parent = rd32(r + 8, big); w->x = (int16_t)rd16(r + 12, big); w->y = (int16_t)rd16(r + 14, big); } } break;
    case 8: case 9: { XWin *w = xw_find(a); if (w) w->mapped = 1; draw_event(k, op, a, 0, 0, w ? w->w : 0, w ? w->h : 0, 0, NULL, 0); } break;   /* Map(Subwindows) */
    case 10: { XWin *w = xw_find(a); if (w) w->mapped = 0; draw_event(k, op, a, 0, 0, w ? w->w : 0, w ? w->h : 0, 0, NULL, 0); } break;      /* Unmap */
    case 12: if (n >= 12) {                                           /* ConfigureWindow: mask bits x y w h ... */
            XWin *w = xw_find(a);
            uint32_t m = rd16(r + 8, big);
            size_t at = 12;
            for (int bit = 0; bit < 7 && w; bit++)
                if (m & (1u << bit)) {
                    if (at + 4 > n) break;
                    int v = (int)rd32(r + at, big);
                    if (bit == 0) w->x = (int16_t)v; else if (bit == 1) w->y = (int16_t)v; else if (bit == 2) w->w = v; else if (bit == 3) w->h = v;
                    at += 4;
                }
            if (w) draw_event(k, op, a, 0, 0, w->w, w->h, 0, NULL, 0);
        } break;
    case 55: if (n >= 16) gc_values(a, rd32(r + 12, big), r + 16, n - 16, big); break;   /* CreateGC */
    case 56: if (n >= 12) gc_values(a, rd32(r + 8, big), r + 12, n - 12, big); break;    /* ChangeGC */
    case 61: if (n >= 16) draw_event(k, op, a, (int16_t)rd16(r + 8, big), (int16_t)rd16(r + 10, big), rd16(r + 12, big), rd16(r + 14, big), 0, NULL, 0); break;  /* ClearArea */
    case 62: if (n >= 28) draw_event(k, op, rd32(r + 8, big), (int16_t)rd16(r + 20, big), (int16_t)rd16(r + 22, big), rd16(r + 24, big), rd16(r + 26, big), 0, NULL, 0); break; /* CopyArea */
    case 72: if (n >= 20) draw_event(k, op, a, (int16_t)rd16(r + 16, big), (int16_t)rd16(r + 18, big), rd16(r + 12, big), rd16(r + 14, big), 0, NULL, 0); break; /* PutImage */
    case 67: case 70: if (n >= 12) {                                  /* PolyRectangle / PolyFillRectangle: one event per rectangle */
            uint32_t col = gc_colour(rd32(r + 8, big));
            for (size_t i = 12; i + 8 <= n; i += 8)
                draw_event(k, op, a, (int16_t)rd16(r + i, big), (int16_t)rd16(r + i + 2, big), rd16(r + i + 4, big), rd16(r + i + 6, big), col, NULL, 0);
        } break;
    case 64: case 65: case 66: case 69: if (n >= 16) {                /* points, lines, segments, polygons: their bounding box */
            int x0 = 0, y0 = 0, x1 = 0, y1 = 0;
            size_t at = op == 69 ? 16 : 12;
            bbox_points(r + at, n - at, big, &x0, &y0, &x1, &y1, (op == 64 || op == 65 || op == 69) && r[op == 69 ? 13 : 1] == 1);
            draw_event(k, op, a, x0, y0, x1 - x0 + 1, y1 - y0 + 1, gc_colour(rd32(r + 8, big)), NULL, 0);
        } break;
    case 74: case 76: read_text(k, r, n); break;
    default: break;
    }
}

/* a whole PolyText8 (74) or ImageText8 (76) request: pull out the string(s) */
static void read_text(Conn *k, const unsigned char *r, size_t n) {
    if (n < 16) return;
    uint32_t draw = rd32(r + 4, k->big);
    int x = (int16_t)rd16(r + 12, k->big), y = (int16_t)rd16(r + 14, k->big);
    char out[1024]; size_t o = 0;
    if (r[0] == 76) {
        size_t len = r[1];
        for (size_t i = 0; i < len && 16 + i < n && o < sizeof out - 1; i++) out[o++] = (r[16 + i] >= 32 && r[16 + i] < 127) ? (char)r[16 + i] : '?';
    } else {
        size_t p = 16;
        while (p + 2 <= n) {
            unsigned len = r[p];
            if (len == 0) break;                                      /* padding */
            if (len == 255) { p += 5; continue; }                     /* font change */
            for (size_t i = 0; i < len && p + 2 + i < n && o < sizeof out - 1; i++) out[o++] = (r[p + 2 + i] >= 32 && r[p + 2 + i] < 127) ? (char)r[p + 2 + i] : '?';
            p += 2 + len;
        }
    }
    out[o] = 0;
    if (o) {
        text_at(k, draw, x, y, out);                                  /* the tape: only text that changed */
        draw_event(k, r[0], draw, x, y, 0, 0, gc_colour(rd32(r + 8, k->big)), out, (int)o);   /* the box: every text drawn, x,y = baseline start */
    }
}

/* follow the request stream (a copy; the bytes themselves are passed on untouched) */
static void follow(Conn *k, const unsigned char *b, size_t n) {
    size_t i = 0;
    if (!k->setup) {                                                  /* setup: order, pad, major, minor, nlen, dlen, pad, name, data */
        while (i < n && k->nhdr < 12) k->hdr[k->nhdr++] = b[i++];
        if (k->nhdr < 12) return;
        k->big = k->hdr[0] == 'B';
        size_t nl = rd16(k->hdr + 6, k->big), dl = rd16(k->hdr + 8, k->big);   /* auth name and data lengths, each padded to 4 */
        k->setup = 1; k->nhdr = 0; k->keep = 0;
        k->left = ((nl + 3) & ~(size_t)3) + ((dl + 3) & ~(size_t)3);
    }
    while (i < n) {
        if (k->left) {
            size_t take = n - i < k->left ? n - i : k->left;
            if (k->keep && k->nreq < k->want) {                      /* keep only what the interpreter reads (an image: its head) */
                size_t cp = k->want - k->nreq < take ? k->want - k->nreq : take;
                memcpy(k->req + k->nreq, b + i, cp); k->nreq += cp;
            }
            i += take; k->left -= take;
            if (!k->left && k->keep) { interpret(k, k->req, k->nreq); k->keep = 0; }
            continue;
        }
        k->hdr[k->nhdr++] = b[i++];
        if (k->nhdr < 4) continue;
        size_t len = rd16(k->hdr + 2, k->big);
        size_t hlen = 4;
        if (len == 0) { if (k->nhdr < 8) continue; len = rd32(k->hdr + 4, k->big); hlen = 8; }   /* BIG-REQUESTS */
        int op = k->hdr[0];
        k->reqs++; k->kinds[op]++;
        size_t total = len * 4;
        k->left = total > hlen ? total - hlen : 0;
        switch (op) {                                                 /* the requests the interpreter reads */
        case 1: case 4: case 7: case 8: case 9: case 10: case 12: case 55: case 56: case 61: case 62:
        case 64: case 65: case 66: case 67: case 69: case 70: case 72: case 74: case 76: k->keep = 1; break;
        default: k->keep = 0;
        }
        if (k->keep) {
            size_t body = total - (hlen - 4);                         /* a BIG request is kept without its extra length word */
            k->want = op == 72 && body > 24 ? 24 : body;
            if (k->want > k->capreq) { unsigned char *nr = realloc(k->req, k->want); if (nr) { k->req = nr; k->capreq = k->want; } else k->keep = 0; }
            if (k->keep) { memcpy(k->req, k->hdr, 4); k->nreq = 4; }
        }
        k->nhdr = 0;
        if (!k->left && k->keep) { interpret(k, k->req, k->nreq); k->keep = 0; }
    }
}

static void name_of(int fd, char *out, size_t n, int *pid) {
    struct ucred cr; socklen_t l = sizeof cr;
    snprintf(out, n, "app");
    *pid = 0;
    if (getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &cr, &l) != 0) return;
    *pid = (int)cr.pid;
    char p[64]; snprintf(p, sizeof p, "/proc/%d/comm", (int)cr.pid);
    FILE *f = fopen(p, "r");
    if (!f) { snprintf(out, n, "pid%d", (int)cr.pid); return; }
    if (fgets(out, (int)n, f)) out[strcspn(out, "\n")] = 0;
    fclose(f);
}

static void drop(int i) {
    close(conns[i].c); close(conns[i].s); free(conns[i].req);
    char ts[16]; now_txt(ts, sizeof ts);
    fprintf(tape, "%s line: %s left (%ld bytes in all)\n", ts, conns[i].name, conns[i].tot_in);
    conns[i] = conns[--nconn];
}

static int pass(int from, int to, long *count, Conn *k) {
    unsigned char buf[65536];
    ssize_t r = recv(from, buf, sizeof buf, 0);
    if (r <= 0) return -1;
    if (k) follow(k, buf, (size_t)r);
    ssize_t o = 0;
    while (o < r) {                                                   /* blocking write: the line keeps X's own back-pressure */
        ssize_t w = send(to, buf + o, (size_t)(r - o), MSG_NOSIGNAL);
        if (w < 0) { if (errno == EINTR) continue; return -1; }
        o += w;
    }
    *count += r;
    return 0;
}

int main(int argc, char **argv) {
    int disp = 2, srv = 12;
    const char *outdir = NULL;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "-d") && i + 1 < argc) disp = atoi(argv[++i]);
        else if (!strcmp(argv[i], "-s") && i + 1 < argc) srv = atoi(argv[++i]);
        else if (!strcmp(argv[i], "-o") && i + 1 < argc) outdir = argv[++i];
        else { fprintf(stderr, "usage: %s [-d 2] [-s 12] [-o DIR]\n", argv[0]); return 2; }
    }
    char dir[1024], path[1200];
    if (outdir) snprintf(dir, sizeof dir, "%s", outdir); else snprintf(dir, sizeof dir, "%s/coop-perch/tape", getenv("HOME") ? getenv("HOME") : ".");
    mkdir(dir, 0750);
    snprintf(path, sizeof path, "%s/tape.txt", dir);
    tape = fopen(path, "a");
    if (!tape) { fprintf(stderr, "coop-perch-tap: cannot write %s\n", path); return 1; }
    setvbuf(tape, NULL, _IOLBF, 0);

    /* listen where apps look for display :disp: the abstract socket and the file in /tmp/.X11-unix */
    int ls[2] = {-1, -1};
    for (int a = 0; a < 2; a++) {
        struct sockaddr_un sa = {.sun_family = AF_UNIX};
        socklen_t sl;
        if (a == 0) { sa.sun_path[0] = 0; snprintf(sa.sun_path + 1, sizeof sa.sun_path - 1, "/tmp/.X11-unix/X%d", disp); sl = (socklen_t)(offsetof(struct sockaddr_un, sun_path) + 1 + strlen(sa.sun_path + 1)); }
        else { snprintf(sa.sun_path, sizeof sa.sun_path, "/tmp/.X11-unix/X%d", disp); unlink(sa.sun_path); sl = sizeof sa; }
        ls[a] = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
        if (ls[a] < 0 || bind(ls[a], (struct sockaddr *)&sa, sl) != 0 || listen(ls[a], 64) != 0) {
            fprintf(stderr, "coop-perch-tap: cannot listen as :%d (%s)\n", disp, strerror(errno));
            if (ls[a] >= 0) close(ls[a]);
            ls[a] = -1;
        }
    }
    if (ls[0] < 0 && ls[1] < 0) return 1;
    snprintf(path, sizeof path, "/tmp/.X%d-lock", disp);              /* claim the display number like an X server does */
    FILE *lk = fopen(path, "w");
    if (lk) { fprintf(lk, "%10d\n", (int)getpid()); fclose(lk); }

    signal(SIGINT, on_stop); signal(SIGTERM, on_stop); signal(SIGPIPE, SIG_IGN);
    char ts[16]; now_txt(ts, sizeof ts);
    fprintf(tape, "%s line: tap open, :%d -> :%d\n", ts, disp, srv);
    CoopBox box;
    uint32_t bdim[1] = {259};
    char bf[300];
    snprintf(bf, sizeof bf, "op0 .. op255 bytes_in bytes_out apps; requests per X opcode in the second (op128+ extensions), bytes apps sent, bytes "
                            "the server sent back, apps connected; display :%d; a frame per second with traffic, none when the line is quiet", disp);
    int have_box = coop_box_create(&box, "line", 1, bdim, bf) == 0;
    uint32_t ddim[1] = {DRAW_FIELDS};
    have_dbox = coop_box_create(&dbox, "draw", 1, ddim,
        "app_pid op x y w h colour drawable_hi drawable_lo onscreen nchars spare char0 .. char63; one frame per thing drawn; "
        "x y w h in screen pixels (text: x y = baseline start, w h 0); colour 0xRRGGBB as a number (0 = not known); onscreen 1 seen, 0 hidden "
        "(unmapped), -1 off screen (pixmap); chars are byte codes; op: 8 map 9 map-subwindows 10 unmap 12 move/resize 61 clear 62 copy "
        "64 points 65 lines 66 segments 67 rectangle 69 polygon 70 filled rectangle 72 image 74 text 76 text-on-background") == 0;
    if (!have_box) fprintf(stderr, "coop-perch-tap: no box in %s (going on without it)\n", coop_box_dir());
    time_t last = time(NULL);
    while (!stop_now) {
        int np = 2 + nconn * 2;
        struct pollfd *pf = calloc((size_t)np, sizeof *pf);
        if (!pf) break;
        pf[0].fd = ls[0]; pf[0].events = POLLIN; pf[1].fd = ls[1]; pf[1].events = POLLIN;
        for (int i = 0; i < nconn; i++) { pf[2 + 2 * i].fd = conns[i].c; pf[2 + 2 * i].events = POLLIN; pf[3 + 2 * i].fd = conns[i].s; pf[3 + 2 * i].events = POLLIN; }
        int any = 0;
        for (int i = 0; i < nconn; i++) if (conns[i].bytes_in || conns[i].bytes_out) any = 1;
        int pr = poll(pf, (nfds_t)np, any ? 1000 : -1);              /* nothing on the line and nothing to report: sleep until bytes come */
        if (pr < 0 && errno != EINTR) { free(pf); break; }
        for (int a = 0; a < 2 && pr > 0; a++)
            if (pf[a].fd >= 0 && (pf[a].revents & POLLIN)) {
                int c = accept4(pf[a].fd, NULL, NULL, SOCK_CLOEXEC);
                if (c < 0) continue;
                int s = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
                struct sockaddr_un sa = {.sun_family = AF_UNIX};
                snprintf(sa.sun_path, sizeof sa.sun_path, "/tmp/.X11-unix/X%d", srv);
                if (s < 0 || connect(s, (struct sockaddr *)&sa, sizeof sa) != 0) { close(c); if (s >= 0) close(s); continue; }
                if (nconn == capconn) {
                    int nc = capconn ? capconn * 2 : 16;
                    Conn *n2 = realloc(conns, (size_t)nc * sizeof(Conn));
                    if (!n2) { close(c); close(s); continue; }
                    conns = n2; capconn = nc;
                }
                Conn *k = &conns[nconn++];
                memset(k, 0, sizeof *k);
                k->c = c; k->s = s;
                name_of(c, k->name, sizeof k->name, &k->pid);
                now_txt(ts, sizeof ts);
                fprintf(tape, "%s line: %s joined\n", ts, k->name);
            }
        for (int i = nconn - 1; i >= 0 && pr > 0; i--) {
            short rc = pf[2 + 2 * i].revents, rs = pf[3 + 2 * i].revents;
            int bad = 0;
            if (rc & (POLLIN | POLLHUP | POLLERR)) bad |= pass(conns[i].c, conns[i].s, &conns[i].bytes_in, &conns[i]) != 0;
            if (!bad && (rs & (POLLIN | POLLHUP | POLLERR))) bad |= pass(conns[i].s, conns[i].c, &conns[i].bytes_out, NULL) != 0;
            if (bad) drop(i);
        }
        free(pf);
        time_t t = time(NULL);
        if (t != last) {                                              /* one line per app per second that had traffic; silence writes nothing */
            last = t;
            now_txt(ts, sizeof ts);
            float *lf = NULL;
            for (int i = 0; i < nconn; i++)
                if (conns[i].bytes_in || conns[i].bytes_out) {
                    if (!lf && have_box) { lf = coop_box_next(&box, (double)t); if (lf) memset(lf, 0, 259 * sizeof(float)); }
                    if (lf) {
                        for (int op = 0; op < 256; op++) lf[op] += (float)conns[i].kinds[op];
                        lf[256] += (float)conns[i].bytes_in; lf[257] += (float)conns[i].bytes_out;
                    }
                }
            if (lf) { lf[258] = (float)nconn; coop_box_commit(&box); }
            for (int i = 0; i < nconn; i++) {
                Conn *k = &conns[i];
                if (!k->bytes_in && !k->bytes_out) continue;
                char kinds[300]; size_t o = 0;
                for (int pass2 = 0; pass2 < 3; pass2++) {                 /* the 3 busiest kinds of request */
                    int best = -1;
                    for (int op = 0; op < 256; op++) if (k->kinds[op] && (best < 0 || k->kinds[op] > k->kinds[best])) best = op;
                    if (best < 0) break;
                    const char *nm = kind_name(best);
                    o += (size_t)snprintf(kinds + o, sizeof kinds - o, "%s%s %ld", o ? ", " : "", nm ? nm : "op", k->kinds[best]);
                    if (!nm) o += (size_t)snprintf(kinds + o, sizeof kinds - o, "(%d)", best);
                    k->kinds[best] = 0;
                }
                fprintf(tape, "%s line: %s %ld bytes in, %ld out, %ld requests (%s)\n", ts, k->name, k->bytes_in, k->bytes_out, k->reqs, o ? kinds : "replies only");
                k->tot_in += k->bytes_in;
                k->bytes_in = k->bytes_out = k->reqs = 0;
                memset(k->kinds, 0, sizeof k->kinds);
            }
        }
    }
    while (nconn) drop(nconn - 1);
    snprintf(path, sizeof path, "/tmp/.X11-unix/X%d", disp); unlink(path);
    snprintf(path, sizeof path, "/tmp/.X%d-lock", disp); unlink(path);
    now_txt(ts, sizeof ts);
    fprintf(tape, "%s line: tap closed\n", ts);
    fclose(tape);
    return 0;
}
