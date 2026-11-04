/* Copyright (c) 2026 Mike J Hebblethwaite. See LICENSE.md. */
/* coop-perch-vm: the Perch eye on a virtual machine's screen.
 *
 * QEMU (-display dbus) is the guest's monitor. This eye registers as that monitor's listener on a private peer-to-peer
 * D-Bus line: QEMU sends the whole screen once (Scanout), then only each rectangle the guest changed (Update), with its pixels.
 * The hypervisor already knows what changed, so there is nothing to capture and no screenshot: the eye only does Perch's part
 * (cells, strikes, zoom, habits: coop-perch-core.h) on the rectangles it is handed.
 *
 * Run: coop-perch-vm -a unix:path=BUS [-n NAME] [-g 16x10] [-o DIR] [-t SECONDS] [-v]
 *      BUS = the private bus QEMU was started on (-display dbus,addr=...). Console 0.
 * Out: plane "vm-NAME" in the box of bot vectors; tape in DIR (default ~/coop-perch/tape/vm-NAME).
 */
#define _GNU_SOURCE
#include <gio/gio.h>
#include <gio/gunixfdlist.h>
#include <glib-unix.h>
#include <sys/socket.h>
#include "coop-perch-core.h"

static const char LISTENER_XML[] =
    "<node>"
    " <interface name='org.qemu.Display1.Listener'>"
    "  <method name='Scanout'><arg type='u' direction='in'/><arg type='u' direction='in'/><arg type='u' direction='in'/>"
    "   <arg type='u' direction='in'/><arg type='ay' direction='in'/></method>"
    "  <method name='Update'><arg type='i' direction='in'/><arg type='i' direction='in'/><arg type='i' direction='in'/>"
    "   <arg type='i' direction='in'/><arg type='u' direction='in'/><arg type='u' direction='in'/><arg type='ay' direction='in'/></method>"
    "  <method name='ScanoutDMABUF'><arg type='h' direction='in'/><arg type='u' direction='in'/><arg type='u' direction='in'/>"
    "   <arg type='u' direction='in'/><arg type='u' direction='in'/><arg type='t' direction='in'/><arg type='b' direction='in'/></method>"
    "  <method name='UpdateDMABUF'><arg type='i' direction='in'/><arg type='i' direction='in'/><arg type='i' direction='in'/>"
    "   <arg type='i' direction='in'/></method>"
    "  <method name='Disable'/>"
    "  <method name='MouseSet'><arg type='i' direction='in'/><arg type='i' direction='in'/><arg type='i' direction='in'/></method>"
    "  <method name='CursorDefine'><arg type='i' direction='in'/><arg type='i' direction='in'/><arg type='i' direction='in'/>"
    "   <arg type='i' direction='in'/><arg type='ay' direction='in'/></method>"
    "  <property name='Interfaces' type='as' access='read'/>"
    " </interface>"
    "</node>";

/* pixman format codes QEMU uses for the guest screen */
#define PIX_X8R8G8B8 0x20020888u
#define PIX_A8R8G8B8 0x20028888u
#define PIX_R5G6B5   0x10020565u
#define PIX_X1R5G5B5 0x10020555u

static PerchEye eye;
static int verbose = 0, have_screen = 0;
static long n_updates = 0, n_scanouts = 0, n_bytes = 0, n_unknown_fmt = 0, n_outside = 0;
static int max_x = 0, max_y = 0;
static GMainLoop *loop = NULL;
static GDBusConnection *bus = NULL;
static long n_resizes = 0;
static const char *dump_path = NULL;
static double run_for = 0, t_start = 0;

/* copy a rectangle of guest pixels into the eye's current screen, as x8r8g8b8 */
static int put_rect(int x, int y, int w, int h, uint32_t stride, uint32_t fmt, const uint8_t *data, gsize len) {
    if (x + w > max_x) max_x = x + w;
    if (y + h > max_y) max_y = y + h;
    if (x < 0 || y < 0 || w <= 0 || h <= 0 || x + w > W || y + h > H) { n_outside++; return 0; }
    int bpp = fmt == PIX_R5G6B5 || fmt == PIX_X1R5G5B5 ? 2 : 4;
    if (fmt != PIX_X8R8G8B8 && fmt != PIX_A8R8G8B8 && bpp == 4) { n_unknown_fmt++; return 0; }
    if ((gsize)stride * (h - 1) + (gsize)w * bpp > len) return 0;
    for (int r = 0; r < h; r++) {
        uint32_t *dst = (uint32_t *)(cur + (size_t)(y + r) * cur_stride) + x;
        const uint8_t *src = data + (size_t)r * stride;
        if (bpp == 4) { memcpy(dst, src, (size_t)w * 4); continue; }
        const uint16_t *s = (const uint16_t *)src;
        for (int i = 0; i < w; i++) {
            uint32_t p = s[i], R, G, B;
            if (fmt == PIX_R5G6B5) { R = (p >> 11) & 31; G = (p >> 5) & 63; B = p & 31; R = R << 3 | R >> 2; G = G << 2 | G >> 4; B = B << 3 | B >> 2; }
            else { R = (p >> 10) & 31; G = (p >> 5) & 31; B = p & 31; R = R << 3 | R >> 2; G = G << 3 | G >> 2; B = B << 3 | B >> 2; }
            dst[i] = R << 16 | G << 8 | B;
        }
    }
    return 1;
}

/* QEMU 8.2 sends the whole screen once; when the guest changes mode (BIOS 640x480 -> text 720x400 -> desktop) no new Scanout comes,
   only the console's Width/Height properties change and Updates arrive in the new size. So the size is taken from there. */
static int console_size(int *w, int *h) {
    GVariant *r = g_dbus_connection_call_sync(bus, "org.qemu", "/org/qemu/Display1/Console_0", "org.freedesktop.DBus.Properties", "GetAll",
                                              g_variant_new("(s)", "org.qemu.Display1.Console"), G_VARIANT_TYPE("(a{sv})"),
                                              G_DBUS_CALL_FLAGS_NONE, 2000, NULL, NULL);
    if (!r) return -1;
    GVariant *d = g_variant_get_child_value(r, 0);
    guint32 ww = 0, hh = 0;
    g_variant_lookup(d, "Width", "u", &ww);
    g_variant_lookup(d, "Height", "u", &hh);
    g_variant_unref(d); g_variant_unref(r);
    if (ww == 0 || hh == 0 || ww > 16384 || hh > 16384) return -1;
    *w = (int)ww; *h = (int)hh;
    return 0;
}
static void resize_to(int w, int h) {
    if (have_screen && w == W && h == H) return;
    if (perch_screen(&eye, w, h, 1) != 0) return;
    perch_first_look(&eye, perch_now());          /* blank until the guest's own repaint arrives; that repaint is the strike */
    have_screen = 1; n_resizes++;
    if (verbose) fprintf(stderr, "screen now %dx%d\n", w, h);
}
static void on_props(GDBusConnection *c, const gchar *sender, const gchar *path, const gchar *iface, const gchar *sig, GVariant *p, gpointer u) {
    (void)c; (void)sender; (void)path; (void)iface; (void)sig; (void)u;
    const gchar *which = NULL;
    GVariant *ch = NULL;
    g_variant_get(p, "(&s@a{sv}@as)", &which, &ch, NULL);
    guint32 ww = 0, hh = 0;
    int any = g_variant_lookup(ch, "Width", "u", &ww) | g_variant_lookup(ch, "Height", "u", &hh);
    g_variant_unref(ch);
    if (!any || strcmp(which, "org.qemu.Display1.Console")) return;
    int w, h;
    if (console_size(&w, &h) == 0) resize_to(w, h);
}

static void on_call(GDBusConnection *c, const gchar *sender, const gchar *path, const gchar *iface, const gchar *method,
                    GVariant *p, GDBusMethodInvocation *inv, gpointer ud) {
    (void)c; (void)sender; (void)path; (void)iface; (void)ud;
    if (!strcmp(method, "Scanout")) {
        guint32 w, h, stride, fmt;
        GVariant *d;
        g_variant_get(p, "(uuuu@ay)", &w, &h, &stride, &fmt, &d);
        gsize len = 0;
        const uint8_t *px = g_variant_get_fixed_array(d, &len, 1);
        n_scanouts++; n_bytes += (long)len;
        if (verbose) fprintf(stderr, "scanout %ux%u stride %u fmt %08x bytes %zu\n", w, h, stride, fmt, (size_t)len);
        if (w > 0 && h > 0 && w <= 16384 && h <= 16384) {
            if (!have_screen || (int)w != W || (int)h != H) perch_screen(&eye, (int)w, (int)h, 1);
            put_rect(0, 0, (int)w, (int)h, stride, fmt, px, len);
            perch_first_look(&eye, perch_now());
            have_screen = 1;
        }
        g_variant_unref(d);
    } else if (!strcmp(method, "Update")) {
        gint32 x, y, w, h;
        guint32 stride, fmt;
        GVariant *d;
        g_variant_get(p, "(iiiiuu@ay)", &x, &y, &w, &h, &stride, &fmt, &d);
        gsize len = 0;
        const uint8_t *px = g_variant_get_fixed_array(d, &len, 1);
        n_updates++; n_bytes += (long)len;
        if (verbose > 1) fprintf(stderr, "update %d,%d %dx%d\n", x, y, w, h);
        if (have_screen && (x + w > W || y + h > H)) {                   /* the guest changed mode: take the new size */
            int nw, nh;
            if (console_size(&nw, &nh) == 0) resize_to(nw, nh);
        }
        if (have_screen && put_rect(x, y, w, h, stride, fmt, px, len)) perch_damage(&eye, x, y, x + w, y + h);
        g_variant_unref(d);
    } else if (!strcmp(method, "ScanoutDMABUF") || !strcmp(method, "UpdateDMABUF")) {
        /* GPU-side buffers: not used (QEMU is started without gl); answered so QEMU carries on */
        if (verbose) fprintf(stderr, "%s ignored (start QEMU without gl)\n", method);
    } else if (!strcmp(method, "Disable")) {
        if (verbose) fprintf(stderr, "display disabled\n");
    }
    /* MouseSet, CursorDefine: the pointer is not part of the screen's pixels here */
    g_dbus_method_invocation_return_value(inv, NULL);
}

static GVariant *on_get(GDBusConnection *c, const gchar *sender, const gchar *path, const gchar *iface, const gchar *prop,
                        GError **err, gpointer ud) {
    (void)c; (void)sender; (void)path; (void)iface; (void)err; (void)ud;
    if (!strcmp(prop, "Interfaces")) return g_variant_new_strv(NULL, 0);   /* no shared-memory extras: pixels come in the calls */
    return NULL;
}

static const GDBusInterfaceVTable VT = {on_call, on_get, NULL, {0}};

static void on_closed(GDBusConnection *c, gboolean remote, GError *e, gpointer ud) {
    (void)c; (void)remote; (void)ud;
    fprintf(stderr, "coop-perch-vm: QEMU closed the display line%s%s\n", e ? ": " : "", e ? e->message : "");
    g_main_loop_quit(loop);
}

/* ======== the meaning layer (text from the guest helper) and actions (a bot's keys and mouse) ======== */

/* a line reader on a stream: lines over `cap` bytes are cut and the rest of them skipped (the guest is not trusted) */
typedef struct LineConn LineConn;
struct LineConn {
    GIOStream *io;
    GInputStream *in;
    GOutputStream *out;
    char rbuf[4096];
    char line[4097];
    int len, cap, skipping;
    int refs, closed;                   /* queued replies hold a ref; freed when closed and no refs left */
    void (*on_line)(LineConn *, char *);
};
static void lc_read(LineConn *c);
static void lc_free(LineConn *c) { if (c->io) { g_io_stream_close(c->io, NULL, NULL); g_object_unref(c->io); } g_free(c); }
static void lc_unref(LineConn *c) { if (--c->refs <= 0 && c->closed) lc_free(c); }
static void lc_got(GObject *o, GAsyncResult *r, gpointer u) {
    LineConn *c = u;
    gssize n = g_input_stream_read_finish(G_INPUT_STREAM(o), r, NULL);
    if (n <= 0) { c->closed = 1; if (c->refs <= 0) lc_free(c); return; }
    for (gssize i = 0; i < n; i++) {
        char ch = c->rbuf[i];
        if (ch == '\n') {
            if (!c->skipping) { c->line[c->len] = 0; c->on_line(c, c->line); }
            c->len = 0; c->skipping = 0;
        } else if (!c->skipping) {
            if (c->len < c->cap) c->line[c->len++] = ch;
            else { c->line[c->len] = 0; c->on_line(c, c->line); c->len = 0; c->skipping = 1; }   /* too long: cut, skip the rest */
        }
    }
    lc_read(c);
}
static void lc_read(LineConn *c) { g_input_stream_read_async(c->in, c->rbuf, sizeof c->rbuf, G_PRIORITY_DEFAULT, NULL, lc_got, c); }
static LineConn *lc_new(GIOStream *io, int cap, void (*on_line)(LineConn *, char *)) {
    LineConn *c = g_new0(LineConn, 1);
    c->io = io; c->in = g_io_stream_get_input_stream(io); c->out = g_io_stream_get_output_stream(io);
    c->cap = cap > 4096 ? 4096 : cap; c->on_line = on_line;
    lc_read(c);
    return c;
}

/* --- reading the helper's lines: parsed field by field, then written out again by us (no guest bytes pass through raw) --- */
static void skip_sp(const char **p) { while (**p == ' ') (*p)++; }
static int rd_word(const char **p, char *out, int n) {           /* [A-Za-z0-9._-]+ */
    skip_sp(p);
    int k = 0;
    while (**p && **p != ' ') {
        char ch = **p;
        if (!((ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z') || (ch >= '0' && ch <= '9') || ch == '.' || ch == '_' || ch == '-' || ch == '?')) return -1;
        if (k < n - 1) out[k++] = ch;
        (*p)++;
    }
    out[k] = 0;
    return k > 0 ? 0 : -1;
}
static int rd_quoted(const char **p, char *out, int n) {         /* '...' with \\ \' \n \t; returns length */
    skip_sp(p);
    if (**p != '\'') return -1;
    (*p)++;
    int k = 0;
    while (**p && **p != '\'') {
        char ch = **p;
        if (ch == '\\' && (*p)[1]) { (*p)++; ch = **p == 'n' ? '\n' : **p == 't' ? '\t' : **p; }
        if (k < n - 1) out[k++] = ch;
        (*p)++;
    }
    if (**p != '\'') return -1;
    (*p)++;
    out[k] = 0;
    return k;
}
static int rd_box(const char **p, int *b) {                      /* "x,y wxh" or "?" */
    skip_sp(p);
    if (**p == '?') { (*p)++; b[0] = b[1] = b[2] = b[3] = -1; return 0; }
    int used = 0;
    if (sscanf(*p, "%d,%d %dx%d%n", &b[0], &b[1], &b[2], &b[3], &used) != 4) return -1;
    if (b[2] < 0 || b[3] < 0 || b[2] > 65535 || b[3] > 65535 || b[0] < -65535 || b[1] < -65535 || b[0] > 65535 || b[1] > 65535) return -1;
    *p += used;
    return 0;
}
static void quote_out(const char *s, char *out, int n) {         /* our own quoting: printable UTF-8 kept, controls as \n \t \xHH */
    gchar *v = g_utf8_make_valid(s, -1);
    int k = 0;
    out[k++] = '\'';
    for (const unsigned char *q = (const unsigned char *)v; *q && k < n - 6; q++) {
        if (*q == '\\' || *q == '\'') { out[k++] = '\\'; out[k++] = (char)*q; }
        else if (*q == '\n') { out[k++] = '\\'; out[k++] = 'n'; }
        else if (*q == '\t') { out[k++] = '\\'; out[k++] = 't'; }
        else if (*q < 0x20 || *q == 0x7f) k += snprintf(out + k, (size_t)(n - k), "\\x%02x", *q);
        else out[k++] = (char)*q;
    }
    out[k++] = '\'';
    out[k] = 0;
    g_free(v);
}

static CoopBox textbox;
static int have_textbox = 0;
static long txt_lines = 0, txt_bad = 0, txt_dropped = 0;
static double tb_tokens = 100, tb_last = 0;                      /* 50 lines a second, 100 at once */

static void text_line(LineConn *c, char *l) {
    (void)c;
    double t = perch_now();
    tb_tokens += (t - tb_last) * 50.0; tb_last = t;
    if (tb_tokens > 100) tb_tokens = 100;
    if (tb_tokens < 1) { txt_dropped++; return; }
    tb_tokens -= 1;
    const char *p = l + 1;
    char app[32], role[32], text[1100], qt[2300], out[2600], v1[32], v2[32];
    int b[4] = {-1, -1, -1, -1}, kind = 0, tl = 0;
    if (l[0] == 'O' && l[1] == ' ' && rd_word(&p, v1, sizeof v1) == 0 && rd_word(&p, v2, sizeof v2) == 0) {
        snprintf(out, sizeof out, "O guest %s %s", v1, v2); kind = 5; text[0] = 0;
    } else if (l[0] == 'T' && l[1] == ' ' && rd_word(&p, app, sizeof app) == 0 && rd_box(&p, b) == 0) {
        skip_sp(&p);
        char sign = *p;
        if ((sign != '+' && sign != '-') || (p++, (tl = rd_quoted(&p, text, sizeof text)) < 0)) { txt_bad++; return; }
        quote_out(text, qt, sizeof qt);
        snprintf(out, sizeof out, "T %s %d,%d %dx%d %c%s", app, b[0], b[1], b[2], b[3], sign, qt);
        kind = sign == '+' ? 1 : 2;
    } else if (l[0] == 'F' && l[1] == ' ' && rd_word(&p, app, sizeof app) == 0 && rd_word(&p, role, sizeof role) == 0 &&
               (tl = rd_quoted(&p, text, sizeof text)) >= 0 && rd_box(&p, b) == 0) {
        quote_out(text, qt, sizeof qt);
        snprintf(out, sizeof out, "F %s %s %s %d,%d %dx%d", app, role, qt, b[0], b[1], b[2], b[3]);
        kind = 3;
    } else if (l[0] == 'W' && l[1] == ' ' && rd_word(&p, app, sizeof app) == 0 && (tl = rd_quoted(&p, text, sizeof text)) >= 0 &&
               rd_box(&p, b) == 0) {
        quote_out(text, qt, sizeof qt);
        snprintf(out, sizeof out, "W %s %s %d,%d %dx%d", app, qt, b[0], b[1], b[2], b[3]);
        kind = 4;
    } else { txt_bad++; return; }
    txt_lines++;
    char ts[16];
    clock_txt(t, ts, sizeof ts);
    int n = fprintf(eye.tape, "%s %s\n", ts, out);
    if (n > 0) { eye.tape_bytes += n; eye.w_tape += n; }
    snprintf(eye.last_line, sizeof eye.last_line, "%s %.300s", ts, out);
    if (have_textbox && kind != 5) {                              /* raw for bots: kind x y w h nbytes, then up to 72 bytes of text */
        int32_t *f = coop_box_next(&textbox, t);
        f[0] = kind; f[1] = b[0]; f[2] = b[1]; f[3] = b[2]; f[4] = b[3];
        int nb = (int)strlen(text);
        f[5] = nb;
        memcpy(f + 6, text, (size_t)(nb < 72 ? nb : 72));
        coop_box_commit(&textbox);
    }
}

static const char *text_path = NULL;
static gboolean text_connect(gpointer u) {
    (void)u;
    GSocketClient *cl = g_socket_client_new();
    GSocketAddress *a = g_unix_socket_address_new(text_path);
    GSocketConnection *sc = g_socket_client_connect(cl, G_SOCKET_CONNECTABLE(a), NULL, NULL);
    g_object_unref(a); g_object_unref(cl);
    if (!sc) return G_SOURCE_CONTINUE;                                /* QEMU not up yet: try again */
    lc_new(G_IO_STREAM(sc), 1024, text_line);
    if (verbose) fprintf(stderr, "text line from the guest helper: %s\n", text_path);
    return G_SOURCE_REMOVE;
}

/* --- actions: a bot's keys and mouse, through QEMU's own keyboard and mouse on the bus --- */
typedef struct { const char *name; unsigned code; } KeyName;
static const KeyName KEYS[] = {
    {"esc", 0x01}, {"escape", 0x01}, {"1", 0x02}, {"2", 0x03}, {"3", 0x04}, {"4", 0x05}, {"5", 0x06}, {"6", 0x07}, {"7", 0x08}, {"8", 0x09},
    {"9", 0x0a}, {"0", 0x0b}, {"minus", 0x0c}, {"equal", 0x0d}, {"backspace", 0x0e}, {"tab", 0x0f}, {"q", 0x10}, {"w", 0x11}, {"e", 0x12},
    {"r", 0x13}, {"t", 0x14}, {"y", 0x15}, {"u", 0x16}, {"i", 0x17}, {"o", 0x18}, {"p", 0x19}, {"bracketleft", 0x1a}, {"bracketright", 0x1b},
    {"return", 0x1c}, {"enter", 0x1c}, {"ctrl", 0x1d}, {"a", 0x1e}, {"s", 0x1f}, {"d", 0x20}, {"f", 0x21}, {"g", 0x22}, {"h", 0x23},
    {"j", 0x24}, {"k", 0x25}, {"l", 0x26}, {"semicolon", 0x27}, {"apostrophe", 0x28}, {"grave", 0x29}, {"shift", 0x2a}, {"backslash", 0x2b},
    {"z", 0x2c}, {"x", 0x2d}, {"c", 0x2e}, {"v", 0x2f}, {"b", 0x30}, {"n", 0x31}, {"m", 0x32}, {"comma", 0x33}, {"period", 0x34},
    {"slash", 0x35}, {"alt", 0x38}, {"space", 0x39}, {"capslock", 0x3a}, {"f1", 0x3b}, {"f2", 0x3c}, {"f3", 0x3d}, {"f4", 0x3e},
    {"f5", 0x3f}, {"f6", 0x40}, {"f7", 0x41}, {"f8", 0x42}, {"f9", 0x43}, {"f10", 0x44}, {"f11", 0x57}, {"f12", 0x58},
    {"home", 0xc7}, {"up", 0xc8}, {"pageup", 0xc9}, {"left", 0xcb}, {"right", 0xcd}, {"end", 0xcf}, {"down", 0xd0}, {"pagedown", 0xd1},
    {"insert", 0xd2}, {"delete", 0xd3}, {"super", 0xdb}, {"altgr", 0xb8}, {"rctrl", 0x9d}, {"less", 0x56}, {NULL, 0}};
static unsigned key_code(const char *n) {
    for (const KeyName *k = KEYS; k->name; k++) if (!g_ascii_strcasecmp(k->name, n)) return k->code;
    return 0;
}
/* the guest's keyboard layout: PERCH_VM_KEYMAP=gb (UK) or us (default). QEMU sends key positions, the guest decides the character,
 * so typing must use the guest's layout: on a UK keyboard " @ # ~ \ | sit on other keys than on a US one */
static int keymap_gb = 0;
/* a character on the guest's keyboard: the key, and whether shift is held */
static unsigned char_key(gunichar ch, int *shift) {
    if (keymap_gb) {
        switch (ch) {
        case '"': *shift = 1; return 0x03;            /* shift+2 */
        case '@': *shift = 1; return 0x28;            /* shift+' */
        case '#': *shift = 0; return 0x2b;            /* the key US calls backslash */
        case '~': *shift = 1; return 0x2b;
        case '\\': *shift = 0; return 0x56;          /* the key left of Z */
        case '|': *shift = 1; return 0x56;
        case 0xa3: *shift = 1; return 0x04;           /* pound sign: shift+3 */
        case 0xac: *shift = 1; return 0x29;           /* not sign: shift+` */
        }
    }
    static const char *lower = "`1234567890-=qwertyuiop[]\\asdfghjkl;'zxcvbnm,./ \n\t";
    static const char *upper = "~!@#$%^&*()_+QWERTYUIOP{}|ASDFGHJKL:\"ZXCVBNM<>?";
    static const char *names[] = {"grave", "1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "minus", "equal", "q", "w", "e", "r", "t", "y", "u",
                                  "i", "o", "p", "bracketleft", "bracketright", "backslash", "a", "s", "d", "f", "g", "h", "j", "k", "l",
                                  "semicolon", "apostrophe", "z", "x", "c", "v", "b", "n", "m", "comma", "period", "slash", "space", "return", "tab"};
    if (ch > 127) return 0;
    const char *at = strchr(lower, (int)ch);
    if (at && ch) { *shift = 0; return key_code(names[at - lower]); }
    at = strchr(upper, (int)ch);
    if (at && ch) { *shift = 1; return key_code(names[at - upper]); }
    return 0;
}
static int qcall(const char *iface, const char *method, GVariant *args) {
    GError *e = NULL;
    GVariant *r = g_dbus_connection_call_sync(bus, "org.qemu", "/org/qemu/Display1/Console_0", iface, method, args, NULL,
                                              G_DBUS_CALL_FLAGS_NONE, 2000, NULL, &e);
    if (!r) { if (verbose) fprintf(stderr, "%s.%s: %s\n", iface, method, e->message); g_clear_error(&e); return -1; }
    g_variant_unref(r);
    return 0;
}
/* actions go out from a queue on a timer, so the eye keeps seeing while a bot types, and replies come back in order.
   Unpaced by default (measured 31 Oct: 640 characters in 0.3-0.5 s, screen quiet or busy, every key read by the guest, text exact);
   at most 64 device events a tick, so the main loop never stalls. PERCH_TICK_MS / "pace MS" set a gap between events (tests). */
#define TICK_MS 0
#define BURST 64
#define EVQ_MAX 20000
typedef struct { int kind; unsigned a, b; LineConn *c; char *reply; } ActEv;   /* 0 key 1 button 2 move 3 wait(a ticks) 4 reply */
static GQueue evq = G_QUEUE_INIT;
static guint ev_timer = 0;
static int tick_ms = TICK_MS;                                     /* ms between device events; 0 = unpaced (64 a tick) */
static gboolean ev_tick(gpointer u) {
    (void)u;
    int burst = 0;
    for (;;) {
        ActEv *e = g_queue_peek_head(&evq);
        if (!e) { ev_timer = 0; return G_SOURCE_REMOVE; }
        if (e->kind == 3) { if (e->a > 0) { e->a--; return G_SOURCE_CONTINUE; } g_free(g_queue_pop_head(&evq)); continue; }
        g_queue_pop_head(&evq);
        if (e->kind == 4) {
            if (!e->c->closed) g_output_stream_write_all(e->c->out, e->reply, strlen(e->reply), NULL, NULL, NULL);
            lc_unref(e->c); g_free(e->reply); g_free(e);
            continue;                                                  /* a reply is not a device event: no tick used */
        }
        if (e->kind == 0) qcall("org.qemu.Display1.Keyboard", e->b ? "Press" : "Release", g_variant_new("(u)", e->a));
        else if (e->kind == 1) qcall("org.qemu.Display1.Mouse", e->b ? "Press" : "Release", g_variant_new("(u)", e->a));
        else if (e->kind == 2) { qcall("org.qemu.Display1.Mouse", "SetAbsPosition", g_variant_new("(uu)", e->a, e->b)); perch_pointer(&eye, (int)e->a, (int)e->b, perch_now()); }
        g_free(e);
        if (tick_ms > 0 || ++burst >= BURST) return G_SOURCE_CONTINUE;
    }
}
static void ev_push(int kind, unsigned a, unsigned b, LineConn *c, const char *reply) {
    ActEv *e = g_new0(ActEv, 1);
    e->kind = kind; e->a = a; e->b = b; e->c = c;
    if (c) { c->refs++; e->reply = g_strdup(reply); }
    g_queue_push_tail(&evq, e);
    if (!ev_timer) ev_timer = g_timeout_add(tick_ms > 0 ? (guint)tick_ms : 1, ev_tick, NULL);
}
static void key_press(unsigned c, int down) { ev_push(0, c, (unsigned)down, NULL, NULL); }
static void mouse_button(unsigned b, int down) { ev_push(1, b, (unsigned)down, NULL, NULL); }
static void act_wait_ms(int ms) { int t = tick_ms > 0 ? tick_ms : 1; ev_push(3, (unsigned)((ms + t - 1) / t), 0, NULL, NULL); }   /* in ticks of the current pace (1 ms unpaced) */
static int mouse_to(int x, int y) {
    if (x < 0 || y < 0 || x >= W || y >= H) return -1;
    ev_push(2, (unsigned)x, (unsigned)y, NULL, NULL);
    return 0;
}
static void act_tape(const char *what) {
    char ts[16];
    clock_txt(perch_now(), ts, sizeof ts);
    int n = fprintf(eye.tape, "%s A %s\n", ts, what);
    if (n > 0) { eye.tape_bytes += n; eye.w_tape += n; }
}
/*  type TEXT | key ctrl+alt+t | move X Y | click X Y [left|middle|right] [2] | scroll X Y up|down [N]   -> "ok" or "err why" */
static void act_line(LineConn *c, char *l) {
    char reply[200] = "ok", what[700];
    const char *rest = NULL;
    int x = 0, y = 0, n = 1;
    char a1[16] = "left";
    while (*l == ' ') l++;
    if (g_queue_get_length(&evq) > EVQ_MAX) { ev_push(4, 0, 0, c, "err busy: too much queued, wait for the replies\n"); return; }
    if (!strncmp(l, "type ", 5)) {
        rest = l + 5;
        gchar *v = g_utf8_make_valid(rest, -1);
        int bad = 0;
        for (const gchar *q = v; *q; q = g_utf8_next_char(q)) {
            int sh = 0;
            unsigned k = char_key(g_utf8_get_char(q), &sh);
            if (!k) { bad++; continue; }
            if (sh) key_press(0x2a, 1);
            key_press(k, 1); key_press(k, 0);
            if (sh) key_press(0x2a, 0);
        }
        char qt[600];
        quote_out(v, qt, sizeof qt);
        snprintf(what, sizeof what, "type %s", qt);
        if (bad) snprintf(reply, sizeof reply, "ok (%d characters not on a US keyboard left out)", bad);
        g_free(v);
    } else if (sscanf(l, "pace %d", &n) == 1 && n >= 0 && n <= 200) {   /* tests only: ms between device events, 0 = no pacing */
        tick_ms = n;
        if (ev_timer) { g_source_remove(ev_timer); ev_timer = g_timeout_add(tick_ms > 0 ? (guint)tick_ms : 1, ev_tick, NULL); }
        snprintf(what, sizeof what, "pace %d", n);
    } else if (!strncmp(l, "key ", 4)) {
        unsigned ks[6];
        int nk = 0, bad = 0;
        gchar **parts = g_strsplit(l + 4, "+", 6);
        for (int i = 0; parts[i] && nk < 6; i++) { g_strstrip(parts[i]); unsigned k = key_code(parts[i]); if (k) ks[nk++] = k; else bad = 1; }
        g_strfreev(parts);
        if (bad || !nk) snprintf(reply, sizeof reply, "err unknown key in '%.60s'", l + 4);
        else {
            for (int i = 0; i < nk; i++) key_press(ks[i], 1);
            for (int i = nk - 1; i >= 0; i--) key_press(ks[i], 0);
        }
        snprintf(what, sizeof what, "key %.60s", l + 4);
    } else if (sscanf(l, "move %d %d", &x, &y) == 2) {
        if (mouse_to(x, y)) snprintf(reply, sizeof reply, "err %d,%d is off the screen (%dx%d)", x, y, W, H);
        snprintf(what, sizeof what, "move %d,%d", x, y);
    } else if (sscanf(l, "click %d %d %15s %d", &x, &y, a1, &n) >= 2) {
        unsigned b = !strcmp(a1, "left") ? 0 : !strcmp(a1, "middle") ? 1 : !strcmp(a1, "right") ? 2 : 9;
        if (b == 9 || n < 1 || n > 3) snprintf(reply, sizeof reply, "err click X Y [left|middle|right] [1-3]");
        else if (mouse_to(x, y)) snprintf(reply, sizeof reply, "err %d,%d is off the screen (%dx%d)", x, y, W, H);
        else { act_wait_ms(20); for (int i = 0; i < n; i++) { mouse_button(b, 1); mouse_button(b, 0); act_wait_ms(30); } }
        snprintf(what, sizeof what, "click %d,%d %s%s", x, y, a1, n > 1 ? " x2" : "");
    } else if (sscanf(l, "scroll %d %d %15s %d", &x, &y, a1, &n) >= 3) {
        unsigned b = !strcmp(a1, "up") ? 3 : !strcmp(a1, "down") ? 4 : 9;
        if (b == 9 || n < 1 || n > 50) snprintf(reply, sizeof reply, "err scroll X Y up|down [1-50]");
        else if (mouse_to(x, y)) snprintf(reply, sizeof reply, "err %d,%d is off the screen (%dx%d)", x, y, W, H);
        else for (int i = 0; i < n; i++) { mouse_button(b, 1); mouse_button(b, 0); }
        snprintf(what, sizeof what, "scroll %d,%d %s %d", x, y, a1, n);
    } else {
        snprintf(reply, sizeof reply, "err say: type TEXT | key ctrl+c | move X Y | click X Y [left|middle|right] [N] | scroll X Y up|down [N]");
        what[0] = 0;
    }
    if (what[0] && !strncmp(reply, "ok", 2)) act_tape(what);
    strcat(reply, "\n");
    ev_push(4, 0, 0, c, reply);                                    /* answered when its events have gone out */
}
static gboolean act_incoming(GSocketService *s, GSocketConnection *sc, GObject *src, gpointer u) {
    (void)s; (void)src; (void)u;
    lc_new(G_IO_STREAM(g_object_ref(sc)), 4096, act_line);
    return TRUE;
}
static const char *act_path = NULL;
static int act_listen(void) {
    unlink(act_path);
    GSocketService *svc = g_socket_service_new();
    GSocketAddress *a = g_unix_socket_address_new(act_path);
    mode_t old = umask(077);                                      /* this user only */
    gboolean ok = g_socket_listener_add_address(G_SOCKET_LISTENER(svc), a, G_SOCKET_TYPE_STREAM, G_SOCKET_PROTOCOL_DEFAULT, NULL, NULL, NULL);
    umask(old);
    g_object_unref(a);
    if (!ok) return -1;
    g_signal_connect(svc, "incoming", G_CALLBACK(act_incoming), NULL);
    g_socket_service_start(svc);
    return 0;
}

static GDBusMessage *spy(GDBusConnection *c, GDBusMessage *m, gboolean incoming, gpointer u) {
    (void)c; (void)u;
    if (incoming && verbose > 2 && g_dbus_message_get_message_type(m) == G_DBUS_MESSAGE_TYPE_METHOD_CALL && strcmp(g_dbus_message_get_member(m), "Update"))
        fprintf(stderr, "call %s.%s\n", g_dbus_message_get_interface(m), g_dbus_message_get_member(m));
    return m;
}

static gboolean on_tick(gpointer ud) {
    (void)ud;
    double t = perch_now();
    if (have_screen) perch_look(&eye, t, NULL);
    perch_stats(&eye, t, 0);
    if (run_for > 0 && t - t_start >= run_for) g_main_loop_quit(loop);
    return G_SOURCE_CONTINUE;
}

static void got_result(GObject *o, GAsyncResult *r, gpointer u) { (void)o; *(GAsyncResult **)u = g_object_ref(r); }

/* tests only (-s FILE): on SIGUSR1 write the eye's own copy of the screen as PPM, to check it against the guest's real screen */
static gboolean on_dump(gpointer ud) {
    (void)ud;
    if (!dump_path || !have_screen) return G_SOURCE_CONTINUE;
    FILE *f = fopen(dump_path, "wb");
    if (!f) return G_SOURCE_CONTINUE;
    fprintf(f, "P6\n%d %d\n255\n", W, H);
    for (int y = 0; y < H; y++)
        for (int x = 0; x < W; x++) { uint32_t p = px(x, y); unsigned char rgb[3] = {p >> 16 & 255, p >> 8 & 255, p & 255}; fwrite(rgb, 3, 1, f); }
    fclose(f);
    return G_SOURCE_CONTINUE;
}

static gboolean on_stop_sig(gpointer ud) { (void)ud; g_main_loop_quit(loop); return G_SOURCE_REMOVE; }

int main(int argc, char **argv) {
    const char *addr = NULL, *name = "guest", *outdir = NULL;
    int gx = 16, gy = 10;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "-a") && i + 1 < argc) addr = argv[++i];
        else if (!strcmp(argv[i], "-n") && i + 1 < argc) name = argv[++i];
        else if (!strcmp(argv[i], "-g") && i + 1 < argc) { if (sscanf(argv[++i], "%dx%d", &gx, &gy) != 2 || gx < 1 || gy < 1) { fprintf(stderr, "bad -g\n"); return 2; } }
        else if (!strcmp(argv[i], "-o") && i + 1 < argc) outdir = argv[++i];
        else if (!strcmp(argv[i], "-t") && i + 1 < argc) run_for = atof(argv[++i]);
        else if (!strcmp(argv[i], "-v")) verbose++;
        else if (!strcmp(argv[i], "-s") && i + 1 < argc) dump_path = argv[++i];
        else if (!strcmp(argv[i], "-x") && i + 1 < argc) text_path = argv[++i];
        else if (!strcmp(argv[i], "-c") && i + 1 < argc) act_path = argv[++i];
        else { fprintf(stderr, "usage: %s -a unix:path=BUS [-n NAME] [-g 16x10] [-o DIR] [-t SECONDS] [-v] [-x TEXTSOCKET] [-c ACTSOCKET] [-s TESTDUMP.ppm]\n", argv[0]); return 2; }
    }
    if (!addr) { fprintf(stderr, "coop-perch-vm: -a BUS needed (the address QEMU's -display dbus,addr= uses)\n"); return 2; }
    for (const char *p = name; *p; p++)
        if (!((*p >= 'a' && *p <= 'z') || (*p >= '0' && *p <= '9') || *p == '-')) { fprintf(stderr, "coop-perch-vm: -n takes a-z 0-9 -\n"); return 2; }

    char plane[32], dir[1024];
    snprintf(plane, sizeof plane, "vm-%.20s", name);
    if (outdir) snprintf(dir, sizeof dir, "%s", outdir);
    else snprintf(dir, sizeof dir, "%s/coop-perch/tape/%s", getenv("HOME") ? getenv("HOME") : ".", plane);
    if (perch_open(&eye, dir, "vm", name, plane, gx, gy) != 0) return 1;
    if (text_path) {
        char tplane[40];
        snprintf(tplane, sizeof tplane, "vmtext-%.20s", name);
        uint32_t tdim[1] = {24};
        have_textbox = coop_box_create(&textbox, tplane, "i32", 1, tdim,
            "kind x y w h nbytes text[18]; kind 1 text inserted 2 text deleted 3 focus (text = name) 4 window active (text = title); "
            "box in guest screen pixels (-1 unknown); text = up to 72 bytes UTF-8 packed in the last 18 values, nbytes = its full length; "
            "from the guest's own accessibility tree via the Perch guest helper, checked and re-quoted by the host") == 0;
    }

    loop = g_main_loop_new(NULL, FALSE);
    GError *err = NULL;
    for (int tries = 0; tries < 100 && !bus; tries++) {               /* QEMU may still be starting */
        bus = g_dbus_connection_new_for_address_sync(addr, G_DBUS_CONNECTION_FLAGS_AUTHENTICATION_CLIENT |
                                                     G_DBUS_CONNECTION_FLAGS_MESSAGE_BUS_CONNECTION, NULL, NULL, &err);
        if (!bus) { g_clear_error(&err); g_usleep(100000); }
    }
    if (!bus) { fprintf(stderr, "coop-perch-vm: cannot reach bus %s\n", addr); return 1; }

    /* wait for QEMU to own its name on the bus (it may still be starting) */
    for (int tries = 0;; tries++) {
        GVariant *o = g_dbus_connection_call_sync(bus, "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                                  "NameHasOwner", g_variant_new("(s)", "org.qemu"), G_VARIANT_TYPE("(b)"),
                                                  G_DBUS_CALL_FLAGS_NONE, 5000, NULL, NULL);
        gboolean has = FALSE;
        if (o) { g_variant_get(o, "(b)", &has); g_variant_unref(o); }
        if (has) break;
        if (tries >= 600) { fprintf(stderr, "coop-perch-vm: no QEMU (org.qemu) on %s\n", addr); return 1; }
        g_usleep(100000);
    }

    g_dbus_connection_signal_subscribe(bus, "org.qemu", "org.freedesktop.DBus.Properties", "PropertiesChanged", "/org/qemu/Display1/Console_0",
                                       NULL, G_DBUS_SIGNAL_FLAGS_NONE, on_props, NULL, NULL);

    /* the private line: a socket pair, one end handed to QEMU. QEMU authenticates as the server on it while it handles our
       RegisterListener call, so our side (client) runs at the same time: both async, then wait for both. */
    int sv[2];
    if (socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, sv) != 0) { perror("socketpair"); return 1; }
    GUnixFDList *fds = g_unix_fd_list_new();
    int idx = g_unix_fd_list_append(fds, sv[1], &err);
    close(sv[1]);
    if (idx < 0) { fprintf(stderr, "coop-perch-vm: %s\n", err->message); return 1; }
    GSocket *sock = g_socket_new_from_fd(sv[0], &err);
    if (!sock) { fprintf(stderr, "coop-perch-vm: %s\n", err->message); return 1; }
    GSocketConnection *sc = g_socket_connection_factory_create_connection(sock);

    GAsyncResult *reg_res = NULL, *line_res = NULL;
    g_dbus_connection_call_with_unix_fd_list(bus, "org.qemu", "/org/qemu/Display1/Console_0", "org.qemu.Display1.Console",
                                             "RegisterListener", g_variant_new("(h)", idx), NULL, G_DBUS_CALL_FLAGS_NONE, 15000,
                                             fds, NULL, got_result, &reg_res);
    g_dbus_connection_new(G_IO_STREAM(sc), NULL, G_DBUS_CONNECTION_FLAGS_AUTHENTICATION_CLIENT | G_DBUS_CONNECTION_FLAGS_DELAY_MESSAGE_PROCESSING,
                          NULL, NULL, got_result, &line_res);
    while (!line_res) g_main_context_iteration(NULL, TRUE);
    GDBusConnection *line = g_dbus_connection_new_finish(line_res, &err);
    g_object_unref(line_res);
    if (!line) { fprintf(stderr, "coop-perch-vm: display line: %s\n", err->message); return 1; }
    GDBusNodeInfo *ni = g_dbus_node_info_new_for_xml(LISTENER_XML, &err);
    if (!ni || !g_dbus_connection_register_object(line, "/org/qemu/Display1/Listener", ni->interfaces[0], &VT, NULL, NULL, &err)) {
        fprintf(stderr, "coop-perch-vm: %s\n", err->message); return 1;
    }
    g_signal_connect(line, "closed", G_CALLBACK(on_closed), NULL);
    g_dbus_connection_add_filter(line, spy, NULL, NULL);
    g_dbus_connection_start_message_processing(line);
    while (!reg_res) g_main_context_iteration(NULL, TRUE);
    GVariant *reply = g_dbus_connection_call_with_unix_fd_list_finish(bus, NULL, reg_res, &err);
    g_object_unref(reg_res);
    if (!reply) { fprintf(stderr, "coop-perch-vm: QEMU did not take the listener: %s\n", err->message); return 1; }
    g_variant_unref(reply);
    if (getenv("PERCH_TICK_MS")) tick_ms = atoi(getenv("PERCH_TICK_MS"));
    if (getenv("PERCH_VM_KEYMAP") && !g_ascii_strcasecmp(getenv("PERCH_VM_KEYMAP"), "gb")) keymap_gb = 1;
    if (text_path) g_timeout_add(500, text_connect, NULL);           /* until QEMU opens the port; then it stops */
    if (act_path && act_listen() != 0) { fprintf(stderr, "coop-perch-vm: cannot listen on %s\n", act_path); return 1; }
    if (verbose) fprintf(stderr, "listening to QEMU's console 0 on %s\n", addr);

    t_start = perch_now();
    g_timeout_add(FRAME_MS, on_tick, NULL);
    g_unix_signal_add(SIGINT, on_stop_sig, NULL);
    g_unix_signal_add(SIGTERM, on_stop_sig, NULL);
    g_unix_signal_add(SIGUSR1, on_dump, NULL);
    g_main_loop_run(loop);

    perch_stats(&eye, perch_now(), 1);
    if (verbose && text_path) fprintf(stderr, "text lines %ld bad %ld dropped (over 50/s) %ld\n", txt_lines, txt_bad, txt_dropped);
    if (act_path) unlink(act_path);
    if (verbose) fprintf(stderr, "scanouts %ld updates %ld bytes %ld unknown-format %ld outside-screen %ld furthest %d,%d resizes %ld\n", n_scanouts, n_updates, n_bytes, n_unknown_fmt, n_outside, max_x, max_y, n_resizes);
    perch_close(&eye);
    return 0;
}
