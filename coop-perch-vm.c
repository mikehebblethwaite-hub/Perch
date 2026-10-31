/* Copyright (c) 2026 Mike J Hebblethwaite. All rights reserved. See COPYRIGHT. */
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
        else { fprintf(stderr, "usage: %s -a unix:path=BUS [-n NAME] [-g 16x10] [-o DIR] [-t SECONDS] [-v] [-s TESTDUMP.ppm]\n", argv[0]); return 2; }
    }
    if (!addr) { fprintf(stderr, "coop-perch-vm: -a BUS needed (the address QEMU's -display dbus,addr= uses)\n"); return 2; }
    for (const char *p = name; *p; p++)
        if (!((*p >= 'a' && *p <= 'z') || (*p >= '0' && *p <= '9') || *p == '-')) { fprintf(stderr, "coop-perch-vm: -n takes a-z 0-9 -\n"); return 2; }

    char plane[32], dir[1024];
    snprintf(plane, sizeof plane, "vm-%.20s", name);
    if (outdir) snprintf(dir, sizeof dir, "%s", outdir);
    else snprintf(dir, sizeof dir, "%s/coop-perch/tape/%s", getenv("HOME") ? getenv("HOME") : ".", plane);
    if (perch_open(&eye, dir, "vm", name, plane, gx, gy) != 0) return 1;

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
    if (verbose) fprintf(stderr, "listening to QEMU's console 0 on %s\n", addr);

    t_start = perch_now();
    g_timeout_add(FRAME_MS, on_tick, NULL);
    g_unix_signal_add(SIGINT, on_stop_sig, NULL);
    g_unix_signal_add(SIGTERM, on_stop_sig, NULL);
    g_unix_signal_add(SIGUSR1, on_dump, NULL);
    g_main_loop_run(loop);

    perch_stats(&eye, perch_now(), 1);
    if (verbose) fprintf(stderr, "scanouts %ld updates %ld bytes %ld unknown-format %ld outside-screen %ld furthest %d,%d resizes %ld\n", n_scanouts, n_updates, n_bytes, n_unknown_fmt, n_outside, max_x, max_y, n_resizes);
    perch_close(&eye);
    return 0;
}
