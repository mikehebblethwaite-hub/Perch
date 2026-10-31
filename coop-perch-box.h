/* coop-perch-box.h: the box of bot vectors. Raw numbers for bots, no text, no human view.
 *
 * The box is a folder in shared memory (default /dev/shm/coop-perch). Each plane is one file: a 4096-byte header, then a ring of
 * frames. A frame = one double (unix time) + frame_floats floats, laid out by dim[] (last dim fastest). A bot mmaps the file and
 * reads the numbers in place: no parsing, no copies, no syscalls. To wait for the next frame without polling it sleeps on the
 * header's `wake` word (futex, shared), which the writer bumps and wakes after every frame.
 *
 *   frame k (k counts from 0, ever) lives at slot k % ring.  count = frames written so far.
 *   reader: c = count (acquire); read slot (c-1) % ring; if count moved by >= ring meanwhile, the frame was overwritten: re-read.
 *
 * The header says what it holds (dims, field names), so a bot needs no code from here to read it.
 * Ring sizes are only paging (how many stay in memory); the full history stays on the tape (vectors.bin) on disk.
 */
#ifndef COOP_PERCH_BOX_H
#define COOP_PERCH_BOX_H
#include <errno.h>
#include <fcntl.h>
#include <linux/futex.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

#define COOP_BOX_MAGIC "PERCHBOX"
#define COOP_BOX_VERSION 1
#define COOP_BOX_HEADER 4096

typedef struct {
    char magic[8];             /* "PERCHBOX" */
    uint32_t version;          /* 1 */
    uint32_t header_bytes;     /* 4096: frames start here */
    uint32_t ring;             /* frames kept */
    uint32_t ndim;             /* dims of one frame (time not counted) */
    uint32_t dim[4];           /* e.g. eye: rows, cols, fields per cell */
    uint32_t frame_floats;     /* product of dims */
    uint32_t frame_bytes;      /* 8 (time) + 4 * frame_floats, rounded up to 8 */
    uint64_t count;            /* frames written, ever */
    uint32_t wake;             /* futex word: changes after every frame */
    uint32_t writer_pid;
    char plane[32];            /* "eye", "line" */
    char fields[1024];         /* what the numbers mean, space separated, for the last dim; extra notes after ';' */
} CoopBoxHeader;

typedef struct {
    CoopBoxHeader *h;
    unsigned char *base;
    size_t bytes;
} CoopBox;

static inline const char *coop_box_dir(void) {
    const char *d = getenv("PERCH_BOX");
    return d && *d ? d : "/dev/shm/coop-perch";
}

static inline int coop_box_ring(void) {
    const char *r = getenv("PERCH_RING");
    int n = r ? atoi(r) : 0;
    return n > 0 ? n : 600;
}

/* writer: make (or remake) the plane file and map it */
static inline int coop_box_create(CoopBox *b, const char *plane, int ndim, const uint32_t *dim, const char *fields) {
    memset(b, 0, sizeof *b);
    char path[512];
    mkdir(coop_box_dir(), 0750);
    snprintf(path, sizeof path, "%s/%s.box", coop_box_dir(), plane);
    uint32_t ff = 1;
    for (int i = 0; i < ndim && i < 4; i++) ff *= dim[i];
    uint32_t fb = (8u + 4u * ff + 7u) & ~7u;
    uint32_t ring = (uint32_t)coop_box_ring();
    size_t bytes = COOP_BOX_HEADER + (size_t)fb * ring;
    char tmp[540];
    snprintf(tmp, sizeof tmp, "%s.%d", path, (int)getpid());
    int fd = open(tmp, O_RDWR | O_CREAT | O_TRUNC, 0640);
    if (fd < 0) return -1;
    if (ftruncate(fd, (off_t)bytes) != 0) { close(fd); unlink(tmp); return -1; }
    void *m = mmap(NULL, bytes, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    close(fd);
    if (m == MAP_FAILED) { unlink(tmp); return -1; }
    CoopBoxHeader *h = (CoopBoxHeader *)m;
    memcpy(h->magic, COOP_BOX_MAGIC, 8);
    h->version = COOP_BOX_VERSION; h->header_bytes = COOP_BOX_HEADER; h->ring = ring; h->ndim = (uint32_t)ndim;
    for (int i = 0; i < ndim && i < 4; i++) h->dim[i] = dim[i];
    h->frame_floats = ff; h->frame_bytes = fb; h->writer_pid = (uint32_t)getpid();
    snprintf(h->plane, sizeof h->plane, "%s", plane);
    snprintf(h->fields, sizeof h->fields, "%s", fields);
    if (rename(tmp, path) != 0) { munmap(m, bytes); unlink(tmp); return -1; }   /* readers only ever see a whole header */
    b->h = h; b->base = (unsigned char *)m; b->bytes = bytes;
    return 0;
}

/* writer: the slot for the next frame (fill its floats, then coop_box_commit) */
static inline float *coop_box_next(CoopBox *b, double t) {
    if (!b->h) return NULL;
    unsigned char *f = b->base + b->h->header_bytes + (size_t)(b->h->count % b->h->ring) * b->h->frame_bytes;
    memcpy(f, &t, 8);
    return (float *)(f + 8);
}
static inline void coop_box_commit(CoopBox *b) {
    if (!b->h) return;
    __atomic_store_n(&b->h->count, b->h->count + 1, __ATOMIC_RELEASE);
    __atomic_add_fetch(&b->h->wake, 1, __ATOMIC_RELEASE);
    syscall(SYS_futex, &b->h->wake, FUTEX_WAKE, 0x7fffffff, NULL, NULL, 0);
}

/* reader: map a plane read-only */
static inline int coop_box_open(CoopBox *b, const char *plane) {
    memset(b, 0, sizeof *b);
    char path[512];
    snprintf(path, sizeof path, "%s/%s.box", coop_box_dir(), plane);
    int fd = open(path, O_RDONLY);
    if (fd < 0) return -1;
    struct stat st;
    if (fstat(fd, &st) != 0 || (size_t)st.st_size < COOP_BOX_HEADER) { close(fd); return -1; }
    void *m = mmap(NULL, (size_t)st.st_size, PROT_READ, MAP_SHARED, fd, 0);
    close(fd);
    if (m == MAP_FAILED) return -1;
    CoopBoxHeader *h = (CoopBoxHeader *)m;
    if (memcmp(h->magic, COOP_BOX_MAGIC, 8) || h->version != COOP_BOX_VERSION) { munmap(m, (size_t)st.st_size); return -1; }
    b->h = h; b->base = (unsigned char *)m; b->bytes = (size_t)st.st_size;
    return 0;
}
static inline void coop_box_close(CoopBox *b) { if (b->h) munmap(b->base, b->bytes); memset(b, 0, sizeof *b); }

/* reader: sleep until the plane has a frame newer than `seen` (count value), or ms passes (ms < 0: forever) */
static inline uint64_t coop_box_wait(CoopBox *b, uint64_t seen, int ms) {
    for (;;) {
        uint32_t w = __atomic_load_n(&b->h->wake, __ATOMIC_ACQUIRE);
        uint64_t c = __atomic_load_n(&b->h->count, __ATOMIC_ACQUIRE);
        if (c != seen) return c;
        struct timespec ts = {ms / 1000, (long)(ms % 1000) * 1000000L};
        long r = syscall(SYS_futex, &b->h->wake, FUTEX_WAIT, w, ms < 0 ? NULL : &ts, NULL, 0);
        if (r != 0 && errno == ETIMEDOUT) return __atomic_load_n(&b->h->count, __ATOMIC_ACQUIRE);
    }
}

/* reader: frame k (count-based). Returns time, or -1 if k is not in the ring any more (or not written yet). */
static inline double coop_box_frame(CoopBox *b, uint64_t k, const float **out) {
    uint64_t c = __atomic_load_n(&b->h->count, __ATOMIC_ACQUIRE);
    if (k >= c || c - k > b->h->ring) return -1;
    const unsigned char *f = b->base + b->h->header_bytes + (size_t)(k % b->h->ring) * b->h->frame_bytes;
    double t;
    memcpy(&t, f, 8);
    *out = (const float *)(f + 8);
    return t;
}
#endif
