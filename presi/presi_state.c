/*
 * presi_state.c -- snapshot save/load for the presi simulator.
 *
 * Format (see state-plan.md for the full rationale):
 *   header   : 8B magic "PRESI001" + 4B version + 4B layout_hash
 *   cycle    : 8B model.cycle (little-endian)
 *   ports    : sizeof(struct presi_ports) bytes (model.p verbatim)
 *   sections : repeated 4B tag + 4B length + payload, terminated by
 *              "END_" / 0.  Tags:
 *                "WRAP"  packed presi_s[] for abr_wrap + 1B clk_prev
 *                "NTT_"  packed ntt_top__presi_s[]   + 1B clk_prev
 *                "SAMP"  packed abr_sampler_top__... + 1B clk_prev
 *                "SRAM"  per-SRAM block (depth, data_width, raw words)
 *
 * Layout hash covers PRESI_NETS, the engine PRESI_NETS macros, and the
 * SRAM descriptor table (depth, data_width, byte_enable, name).  A
 * mismatch on load aborts cleanly.
 *
 * Bit packing: byte i bit b carries presi_s[8*i + b] & 1.
 *
 * Files are little-endian / x86-only by design; the linked binary is
 * not portable across builds anyway because PRESI_NETS depends on the
 * exact synthesis run.
 */

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "presi_model.h"
#include "presi_state.h"

#ifdef PRESI_HAVE_NETLIST

#ifdef PRESI_HAVE_ENGINE_NETLISTS
#include "ntt_top.presi_var.h"
#include "abr_sampler_top.presi_var.h"
#endif

#define PRESI_STATE_MAGIC      "PRESI001"
#define PRESI_STATE_MAGIC_LEN  8
#define PRESI_STATE_VERSION    1u

/* FNV-1a 32-bit; stdlib-free, ABI-stable. */
static uint32_t fnv1a32(uint32_t h, const void *buf, size_t n)
{
    const unsigned char *p = (const unsigned char *) buf;
    size_t i;

    for (i = 0; i < n; i++) {
        h ^= p[i];
        h *= 0x01000193u;
    }
    return h;
}

static uint32_t layout_hash(void)
{
    uint32_t h = 0x811c9dc5u;
    uint32_t v;
    unsigned i;

    v = (uint32_t) PRESI_NETS;
    h = fnv1a32(h, &v, sizeof(v));
#ifdef PRESI_HAVE_ENGINE_NETLISTS
    v = (uint32_t) NTT_TOP__PRESI_NETS;
    h = fnv1a32(h, &v, sizeof(v));
    v = (uint32_t) ABR_SAMPLER_TOP__PRESI_NETS;
    h = fnv1a32(h, &v, sizeof(v));
#else
    v = 0;
    h = fnv1a32(h, &v, sizeof(v));
    h = fnv1a32(h, &v, sizeof(v));
#endif
    v = (uint32_t) PRESI_ABR_SRAM_COUNT;
    h = fnv1a32(h, &v, sizeof(v));
    for (i = 0; i < PRESI_ABR_SRAM_COUNT; i++) {
        const struct presi_sram_desc *d = &presi_sram_descs[i];
        v = d->depth;        h = fnv1a32(h, &v, sizeof(v));
        v = d->data_width;   h = fnv1a32(h, &v, sizeof(v));
        v = d->byte_enable;  h = fnv1a32(h, &v, sizeof(v));
        h = fnv1a32(h, d->name, strlen(d->name));
    }
    return h;
}

static void pack_bits(uint8_t *dst, const presi_t *src, size_t n)
{
    size_t i;
    size_t bytes = (n + 7) / 8;

    memset(dst, 0, bytes);
    for (i = 0; i < n; i++) {
        if (src[i] & 1) {
            dst[i >> 3] |= (uint8_t) (1u << (i & 7));
        }
    }
}

static void unpack_bits(presi_t *dst, const uint8_t *src, size_t n)
{
    size_t i;

    for (i = 0; i < n; i++) {
        dst[i] = (src[i >> 3] >> (i & 7)) & 1u ? PRESI_1 : PRESI_0;
    }
}

static int write_all(FILE *fp, const void *buf, size_t n)
{
    return fwrite(buf, 1, n, fp) == n ? 0 : -1;
}

static int read_all(FILE *fp, void *buf, size_t n)
{
    return fread(buf, 1, n, fp) == n ? 0 : -1;
}

static int write_u32(FILE *fp, uint32_t v)
{
    uint8_t b[4];
    b[0] = (uint8_t) v;
    b[1] = (uint8_t) (v >> 8);
    b[2] = (uint8_t) (v >> 16);
    b[3] = (uint8_t) (v >> 24);
    return write_all(fp, b, 4);
}

static int write_u64(FILE *fp, uint64_t v)
{
    uint8_t b[8];
    int i;
    for (i = 0; i < 8; i++) {
        b[i] = (uint8_t) (v >> (i * 8));
    }
    return write_all(fp, b, 8);
}

static int read_u32(FILE *fp, uint32_t *out)
{
    uint8_t b[4];
    if (read_all(fp, b, 4) != 0) {
        return -1;
    }
    *out = (uint32_t) b[0]
         | ((uint32_t) b[1] << 8)
         | ((uint32_t) b[2] << 16)
         | ((uint32_t) b[3] << 24);
    return 0;
}

static int read_u64(FILE *fp, uint64_t *out)
{
    uint8_t b[8];
    int i;
    uint64_t v = 0;
    if (read_all(fp, b, 8) != 0) {
        return -1;
    }
    for (i = 0; i < 8; i++) {
        v |= (uint64_t) b[i] << (i * 8);
    }
    *out = v;
    return 0;
}

static int write_tag(FILE *fp, const char tag[4], uint32_t length)
{
    if (write_all(fp, tag, 4) != 0) {
        return -1;
    }
    return write_u32(fp, length);
}

/*
 * write a "<tag>"-prefixed bit-packed netlist section: header, packed
 * bits for `nets` entries from `arr`, then 1 byte clk_prev.
 */
static int write_netlist_section(FILE *fp, const char tag[4],
                                 const presi_t *arr, size_t nets,
                                 presi_t clk_prev)
{
    size_t bytes = (nets + 7) / 8;
    uint8_t *packed;
    uint8_t cp = (uint8_t) (clk_prev & 1);
    int rc;

    packed = (uint8_t *) malloc(bytes);
    if (packed == NULL) {
        return -1;
    }
    pack_bits(packed, arr, nets);
    rc = write_tag(fp, tag, (uint32_t) (bytes + 1));
    if (rc == 0) {
        rc = write_all(fp, packed, bytes);
    }
    if (rc == 0) {
        rc = write_all(fp, &cp, 1);
    }
    free(packed);
    return rc;
}

static int read_netlist_section(FILE *fp, uint32_t length,
                                presi_t *arr, size_t nets,
                                presi_t *clk_prev_out)
{
    size_t bytes = (nets + 7) / 8;
    uint8_t *packed;
    uint8_t cp;
    int rc;

    if (length != bytes + 1) {
        return -1;
    }
    packed = (uint8_t *) malloc(bytes);
    if (packed == NULL) {
        return -1;
    }
    rc = read_all(fp, packed, bytes);
    if (rc == 0) {
        rc = read_all(fp, &cp, 1);
    }
    if (rc == 0) {
        unpack_bits(arr, packed, nets);
        *clk_prev_out = cp & 1 ? PRESI_1 : PRESI_0;
    }
    free(packed);
    return rc;
}

static int write_sram_section(FILE *fp, const struct presi_model *m)
{
    /* Each SRAM block: 4B depth, 4B data_width, depth*words*4 bytes. */
    uint32_t total = 0;
    unsigned i;
    int rc;

    for (i = 0; i < PRESI_ABR_SRAM_COUNT; i++) {
        const struct presi_sram *r = &m->srams[i];
        uint32_t dw = (uint32_t) r->desc->data_width;
        uint32_t depth = (uint32_t) r->desc->depth;
        uint32_t words = (dw + 31) / 32;
        total += 8 + depth * words * 4;
    }
    rc = write_tag(fp, "SRAM", total);
    if (rc != 0) {
        return -1;
    }
    for (i = 0; i < PRESI_ABR_SRAM_COUNT; i++) {
        const struct presi_sram *r = &m->srams[i];
        uint32_t dw = (uint32_t) r->desc->data_width;
        uint32_t depth = (uint32_t) r->desc->depth;
        uint32_t words = (dw + 31) / 32;
        if (write_u32(fp, depth) != 0 ||
            write_u32(fp, dw) != 0 ||
            write_all(fp, r->data, (size_t) depth * words * 4) != 0) {
            return -1;
        }
    }
    return 0;
}

static int read_sram_section(FILE *fp, uint32_t length,
                             struct presi_model *m)
{
    /* Length is the sum of all per-SRAM block sizes; verify as we go. */
    uint32_t consumed = 0;
    unsigned i;

    for (i = 0; i < PRESI_ABR_SRAM_COUNT; i++) {
        struct presi_sram *r = &m->srams[i];
        uint32_t dw_expect = (uint32_t) r->desc->data_width;
        uint32_t depth_expect = (uint32_t) r->desc->depth;
        uint32_t words = (dw_expect + 31) / 32;
        uint32_t depth, dw;
        size_t bytes = (size_t) depth_expect * words * 4;

        if (read_u32(fp, &depth) != 0 ||
            read_u32(fp, &dw) != 0) {
            return -1;
        }
        if (depth != depth_expect || dw != dw_expect) {
            return -1;
        }
        if (read_all(fp, r->data, bytes) != 0) {
            return -1;
        }
        consumed += 8 + (uint32_t) bytes;
    }
    if (consumed != length) {
        return -1;
    }
    return 0;
}

static int skip_section(FILE *fp, uint32_t length)
{
    if (fseek(fp, (long) length, SEEK_CUR) != 0) {
        /* fseek may fail on streams; fall back to reading and tossing. */
        char buf[4096];
        while (length > 0) {
            size_t take = length > sizeof(buf) ? sizeof(buf) : length;
            if (read_all(fp, buf, take) != 0) {
                return -1;
            }
            length -= (uint32_t) take;
        }
    }
    return 0;
}

int presi_state_save(FILE *fp, const struct presi_model *m)
{
    if (fp == NULL || m == NULL) {
        return -1;
    }
    /* Header. */
    if (write_all(fp, PRESI_STATE_MAGIC, PRESI_STATE_MAGIC_LEN) != 0) {
        return -1;
    }
    if (write_u32(fp, PRESI_STATE_VERSION) != 0) {
        return -1;
    }
    if (write_u32(fp, layout_hash()) != 0) {
        return -1;
    }
    /* Cycle counter + AHB port snapshot. */
    if (write_u64(fp, m->cycle) != 0) {
        return -1;
    }
    if (write_all(fp, &m->p, sizeof(m->p)) != 0) {
        return -1;
    }
    /* WRAP section: abr_wrap presi_s[] + clk_prev. */
    if (write_netlist_section(fp, "WRAP", presi_s, (size_t) PRESI_NETS,
                              presi_clk_prev) != 0) {
        return -1;
    }
#ifdef PRESI_HAVE_ENGINE_NETLISTS
    if (write_netlist_section(fp, "NTT_", ntt_top__presi_s,
                              (size_t) NTT_TOP__PRESI_NETS,
                              ntt_top__presi_clk_prev) != 0) {
        return -1;
    }
    if (write_netlist_section(fp, "SAMP", abr_sampler_top__presi_s,
                              (size_t) ABR_SAMPLER_TOP__PRESI_NETS,
                              abr_sampler_top__presi_clk_prev) != 0) {
        return -1;
    }
#endif
    /* SRAM section: per-instance raw word arrays. */
    if (write_sram_section(fp, m) != 0) {
        return -1;
    }
    /* Terminator. */
    if (write_tag(fp, "END_", 0) != 0) {
        return -1;
    }
    return 0;
}

int presi_state_load(FILE *fp, struct presi_model *m)
{
    char magic[PRESI_STATE_MAGIC_LEN];
    uint32_t version, hash, want_hash;
    uint64_t cycle;
    int saw_wrap = 0;
#ifdef PRESI_HAVE_ENGINE_NETLISTS
    int saw_ntt = 0, saw_samp = 0;
#endif
    int saw_sram = 0;

    if (fp == NULL || m == NULL) {
        return -1;
    }
    if (read_all(fp, magic, PRESI_STATE_MAGIC_LEN) != 0) {
        return -1;
    }
    if (memcmp(magic, PRESI_STATE_MAGIC, PRESI_STATE_MAGIC_LEN) != 0) {
        fprintf(stderr, "presi_state: magic mismatch (not a snapshot file)\n");
        return -1;
    }
    if (read_u32(fp, &version) != 0) {
        return -1;
    }
    if (version != PRESI_STATE_VERSION) {
        fprintf(stderr, "presi_state: version %u != %u\n",
                version, PRESI_STATE_VERSION);
        return -1;
    }
    if (read_u32(fp, &hash) != 0) {
        return -1;
    }
    want_hash = layout_hash();
    if (hash != want_hash) {
        fprintf(stderr, "presi_state: layout hash %08x != %08x "
                "(snapshot built against a different netlist)\n",
                hash, want_hash);
        return -1;
    }
    if (read_u64(fp, &cycle) != 0) {
        return -1;
    }
    if (read_all(fp, &m->p, sizeof(m->p)) != 0) {
        return -1;
    }
    m->cycle = cycle;

    for (;;) {
        char tag[4];
        uint32_t length;

        if (read_all(fp, tag, 4) != 0) {
            return -1;
        }
        if (read_u32(fp, &length) != 0) {
            return -1;
        }
        if (memcmp(tag, "END_", 4) == 0) {
            break;
        } else if (memcmp(tag, "WRAP", 4) == 0) {
            if (read_netlist_section(fp, length, presi_s,
                                     (size_t) PRESI_NETS,
                                     &presi_clk_prev) != 0) {
                return -1;
            }
            saw_wrap = 1;
#ifdef PRESI_HAVE_ENGINE_NETLISTS
        } else if (memcmp(tag, "NTT_", 4) == 0) {
            if (read_netlist_section(fp, length, ntt_top__presi_s,
                                     (size_t) NTT_TOP__PRESI_NETS,
                                     &ntt_top__presi_clk_prev) != 0) {
                return -1;
            }
            saw_ntt = 1;
        } else if (memcmp(tag, "SAMP", 4) == 0) {
            if (read_netlist_section(fp, length,
                                     abr_sampler_top__presi_s,
                                     (size_t) ABR_SAMPLER_TOP__PRESI_NETS,
                                     &abr_sampler_top__presi_clk_prev) != 0) {
                return -1;
            }
            saw_samp = 1;
#endif
        } else if (memcmp(tag, "SRAM", 4) == 0) {
            if (read_sram_section(fp, length, m) != 0) {
                return -1;
            }
            saw_sram = 1;
        } else {
            /* Unknown tag: skip forward; lets older binaries read newer
             * snapshots that add sections we don't understand yet. */
            fprintf(stderr, "presi_state: skipping unknown tag '%c%c%c%c' "
                    "(length=%u)\n", tag[0], tag[1], tag[2], tag[3], length);
            if (skip_section(fp, length) != 0) {
                return -1;
            }
        }
    }

    if (!saw_wrap || !saw_sram) {
        fprintf(stderr, "presi_state: missing required section(s)\n");
        return -1;
    }
#ifdef PRESI_HAVE_ENGINE_NETLISTS
    if (!saw_ntt || !saw_samp) {
        fprintf(stderr, "presi_state: missing engine netlist section(s)\n");
        return -1;
    }
#endif
    return 0;
}

#else /* !PRESI_HAVE_NETLIST */

int presi_state_save(FILE *fp, const struct presi_model *m)
{
    (void) fp;
    (void) m;
    fprintf(stderr, "presi_state_save: built without PRESI_HAVE_NETLIST\n");
    return -1;
}

int presi_state_load(FILE *fp, struct presi_model *m)
{
    (void) fp;
    (void) m;
    fprintf(stderr, "presi_state_load: built without PRESI_HAVE_NETLIST\n");
    return -1;
}

#endif /* PRESI_HAVE_NETLIST */
