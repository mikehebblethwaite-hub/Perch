/* coop-perch-box: a reader of the box of bot vectors (see coop-perch-box.h), for testing and for bots that take a pipe.
 *   coop-perch-box info                 each plane: dims, fields, frames written, ring, last frame time
 *   coop-perch-box raw PLANE [N]        the last N frames (default 1) as raw bytes: double time + floats, little-endian
 *   coop-perch-box stream PLANE         every new frame as raw bytes, as it lands (sleeps on the futex between frames)
 *   coop-perch-box check PLANE SECONDS  read every frame for SECONDS: frames, frames lost to the ring, own CPU
 * Bots that can mmap read /dev/shm/coop-perch/PLANE.box directly instead; this tool is only the pipe for those that cannot.
 */
#include "coop-perch-box.h"
#include <dirent.h>
#include <sys/resource.h>

static double cpu_s(void) {
    struct rusage r;
    getrusage(RUSAGE_SELF, &r);
    return (double)r.ru_utime.tv_sec + r.ru_utime.tv_usec / 1e6 + (double)r.ru_stime.tv_sec + r.ru_stime.tv_usec / 1e6;
}
static double now_s(void) { struct timespec t; clock_gettime(CLOCK_REALTIME, &t); return (double)t.tv_sec + t.tv_nsec / 1e9; }

int main(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "usage: %s info | raw PLANE [N] | stream PLANE | check PLANE SECONDS\n", argv[0]); return 2; }
    if (!strcmp(argv[1], "info")) {
        DIR *d = opendir(coop_box_dir());
        if (!d) { printf("no box at %s (is the eye or the tap running?)\n", coop_box_dir()); return 1; }
        struct dirent *e;
        while ((e = readdir(d))) {
            size_t n = strlen(e->d_name);
            if (n < 5 || strcmp(e->d_name + n - 4, ".box")) continue;
            char plane[64];
            snprintf(plane, sizeof plane, "%.*s", (int)(n - 4), e->d_name);
            CoopBox b;
            if (coop_box_open(&b, plane) != 0) continue;
            const float *f;
            double t = b.h->count ? coop_box_frame(&b, b.h->count - 1, &f) : -1;
            printf("%s: dims", plane);
            for (uint32_t i = 0; i < b.h->ndim; i++) printf("%s%u", i ? "x" : " ", b.h->dim[i]);
            printf(" (%u floats, %u bytes a frame), ring %u, frames %llu, last %.0fs ago, writer pid %u\n  fields: %s\n",
                   b.h->frame_floats, b.h->frame_bytes, b.h->ring, (unsigned long long)b.h->count, t > 0 ? now_s() - t : -1.0, b.h->writer_pid, b.h->fields);
            coop_box_close(&b);
        }
        closedir(d);
        return 0;
    }
    if (argc < 3) return 2;
    CoopBox b;
    if (coop_box_open(&b, argv[2]) != 0) { fprintf(stderr, "no plane %s in %s\n", argv[2], coop_box_dir()); return 1; }
    size_t fl = (size_t)b.h->frame_floats * 4;
    const float *f;
    if (!strcmp(argv[1], "raw")) {
        uint64_t n = argc > 3 ? strtoull(argv[3], NULL, 10) : 1, c = b.h->count;
        for (uint64_t k = c > n ? c - n : 0; k < c; k++) {
            double t = coop_box_frame(&b, k, &f);
            if (t < 0) continue;
            fwrite(&t, 8, 1, stdout); fwrite(f, fl, 1, stdout);
        }
        return 0;
    }
    if (!strcmp(argv[1], "stream")) {
        uint64_t seen = b.h->count;
        for (;;) {
            uint64_t c = coop_box_wait(&b, seen, -1);
            for (uint64_t k = seen; k < c; k++) {
                double t = coop_box_frame(&b, k, &f);
                if (t < 0) continue;
                if (fwrite(&t, 8, 1, stdout) != 1 || fwrite(f, fl, 1, stdout) != 1) return 0;
            }
            fflush(stdout);
            seen = c;
        }
    }
    if (!strcmp(argv[1], "check") && argc > 3) {
        double secs = atof(argv[3]), t0 = now_s(), c0 = cpu_s(), sum = 0;
        uint64_t seen = b.h->count, got = 0, lost = 0, wakes = 0;
        while (now_s() - t0 < secs) {
            int left = (int)((secs - (now_s() - t0)) * 1000);
            uint64_t c = coop_box_wait(&b, seen, left > 0 ? left : 1);
            if (c == seen) continue;
            wakes++;
            for (uint64_t k = seen; k < c; k++) {
                double t = coop_box_frame(&b, k, &f);
                if (t < 0) { lost++; continue; }
                for (uint32_t i = 0; i < b.h->frame_floats; i++) sum += f[i];   /* touch every number, like a bot would */
                got++;
            }
            seen = c;
        }
        double dt = now_s() - t0;
        printf("%s: %.0fs, %llu frames read (%llu floats each), %llu lost, woken %llu times, reader cpu %.3f%%  (sum %.0f)\n",
               argv[2], dt, (unsigned long long)got, (unsigned long long)b.h->frame_floats, (unsigned long long)lost,
               (unsigned long long)wakes, 100.0 * (cpu_s() - c0) / dt, sum);
        return 0;
    }
    return 2;
}
