/*
 * presi_gates.c -- gate-stepping + AHB-driver core for libpresi_gates.a.
 *
 * Lifted from presi.c so multiple harness binaries (presi-cosim,
 * future presi-init / presi-run / presi-dump or test tools) can share
 * the same cycle-stepping primitives without each pulling in a fresh
 * copy of the millions-of-cells gate code.  Iterating on a harness
 * .c file no longer touches anything in this archive.
 *
 * Public API in presi_gates.h.
 */

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "presi_gates.h"
#include "presi_model.h"

#ifdef PRESI_HAVE_NETLIST
#include "abr_wrap.presi_idx.h"
#include "abr_wrap.seq_rom.h"
#endif

#ifdef PRESI_HAVE_ENGINE_NETLISTS
/* Engine var headers expose `<prefix>presi_clk_prev` so presi_cycle
 * can reset them between phases. */
#include "ntt_top.presi_var.h"
#include "abr_sampler_top.presi_var.h"
#endif

/* ============================================================
 * Bit-vector helpers
 * ============================================================ */

uint32_t presi_bits_to_u32(const presi_t *v, int n)
{
    uint32_t x = 0;
    int i;
    for (i = 0; i < n; i++) {
        x |= (uint32_t) (v[i] & 1) << i;
    }
    return x;
}

void presi_u32_to_bits(presi_t *v, int n, uint32_t x)
{
    int i;
    for (i = 0; i < n; i++) {
        v[i] = (x >> i) & 1 ? PRESI_1 : PRESI_0;
    }
}

uint64_t presi_bits_to_u64(const presi_t *v, int n)
{
    uint64_t x = 0;
    int i;
    for (i = 0; i < n; i++) {
        x |= (uint64_t) (v[i] & 1) << i;
    }
    return x;
}

void presi_u64_to_bits(presi_t *v, int n, uint64_t x)
{
    int i;
    for (i = 0; i < n; i++) {
        v[i] = (x >> i) & 1 ? PRESI_1 : PRESI_0;
    }
}

unsigned presi_read_bits(const int *bits, int n)
{
#ifdef PRESI_HAVE_NETLIST
    unsigned v = 0;
    int i;
    for (i = 0; i < n; i++) {
        v |= (unsigned) (presi_s[bits[i]] & 1) << i;
    }
    return v;
#else
    (void) bits;
    (void) n;
    return 0u;
#endif
}

/* ============================================================
 * Netlist step + per-engine glues + SRAM tick
 * ============================================================ */

/*
 * Each step_netlist call is split by **kind**:
 *
 *   _comb : pure combinational pass; runs every step_netlist phase
 *           (falling-edge, rising-edge, settle) since comb wires
 *           always need re-evaluation.  Does NOT touch clk_prev.
 *
 *   _flop : pure flop tick (DFF/DFFSR), gated by `_edge =
 *           clk & ~clk_prev` inside the chunk.  Runs only on the
 *           rising-edge phase; on every other phase _edge would be
 *           0 and the cells degenerate to Q := Q, so skipping the
 *           call entirely saves the otherwise-wasted compute.
 *
 * `presi_clk_prev` is updated explicitly by the harness at the end
 * of each phase, not inside step_netlist itself, because the rising
 * edge requires `clk_prev=0` at the time _flop runs and we need
 * exact control over when that becomes 1.
 */
static void presi_step_netlist_comb(void)
{
#ifdef PRESI_HAVE_NETLIST
#include "abr_wrap.presi_clk_comb.h"
#endif
}

static void presi_step_netlist_flop(void)
{
#ifdef PRESI_HAVE_NETLIST
#include "abr_wrap.presi_clk_flop.h"
#endif
}

#if defined(PRESI_HAVE_NETLIST) && defined(PRESI_HAVE_ENGINE_NETLISTS)
extern void ntt_top_step_glue_comb(void);
extern void ntt_top_step_glue_flop(void);
extern void abr_sampler_top_step_glue_comb(void);
extern void abr_sampler_top_step_glue_flop(void);
#endif

static void presi_engines_step_comb(void)
{
#if defined(PRESI_HAVE_NETLIST) && defined(PRESI_HAVE_ENGINE_NETLISTS)
    ntt_top_step_glue_comb();
    abr_sampler_top_step_glue_comb();
#endif
}

static void presi_engines_step_flop(void)
{
#if defined(PRESI_HAVE_NETLIST) && defined(PRESI_HAVE_ENGINE_NETLISTS)
    ntt_top_step_glue_flop();
    abr_sampler_top_step_glue_flop();
#endif
}

#ifdef PRESI_HAVE_NETLIST

/* Map of each abr_wrap top-level port bit to its index into presi_s[].
 * Bit order matches m->p.* (LSB at index 0) and rtl/abr_wrap.sv. */

#define _PRESI_HADDR_BITS   \
    X(0)  X(1)  X(2)  X(3)  X(4)  X(5)  X(6)  X(7)   \
    X(8)  X(9)  X(10) X(11) X(12) X(13) X(14) X(15)  \
    X(16) X(17) X(18) X(19) X(20) X(21) X(22) X(23)  \
    X(24) X(25) X(26) X(27) X(28) X(29) X(30) X(31)

#define _PRESI_HWDATA_BITS  \
    X(0)  X(1)  X(2)  X(3)  X(4)  X(5)  X(6)  X(7)   \
    X(8)  X(9)  X(10) X(11) X(12) X(13) X(14) X(15)  \
    X(16) X(17) X(18) X(19) X(20) X(21) X(22) X(23)  \
    X(24) X(25) X(26) X(27) X(28) X(29) X(30) X(31)  \
    X(32) X(33) X(34) X(35) X(36) X(37) X(38) X(39)  \
    X(40) X(41) X(42) X(43) X(44) X(45) X(46) X(47)  \
    X(48) X(49) X(50) X(51) X(52) X(53) X(54) X(55)  \
    X(56) X(57) X(58) X(59) X(60) X(61) X(62) X(63)

#define _PRESI_HTRANS_BITS  X(0) X(1)
#define _PRESI_HSIZE_BITS   X(0) X(1) X(2)
#define _PRESI_HRDATA_BITS  _PRESI_HWDATA_BITS

static const int presi_haddr_i_idx[32] = {
#define X(i) IDX_haddr_i_##i,
    _PRESI_HADDR_BITS
#undef X
};
static const int presi_hwdata_i_idx[64] = {
#define X(i) IDX_hwdata_i_##i,
    _PRESI_HWDATA_BITS
#undef X
};
static const int presi_htrans_i_idx[2] = {
#define X(i) IDX_htrans_i_##i,
    _PRESI_HTRANS_BITS
#undef X
};
static const int presi_hsize_i_idx[3] = {
#define X(i) IDX_hsize_i_##i,
    _PRESI_HSIZE_BITS
#undef X
};
static const int presi_hrdata_o_idx[64] = {
#define X(i) IDX_hrdata_o_##i,
    _PRESI_HRDATA_BITS
#undef X
};

#endif /* PRESI_HAVE_NETLIST */


static void presi_apply_inputs(struct presi_model *m)
{
#ifdef PRESI_HAVE_NETLIST
    int i;
    presi_s[IDX_clk]      = m->p.clk;
    presi_s[IDX_rst_b]    = m->p.rst_b;
    presi_s[IDX_hsel_i]   = m->p.hsel_i;
    presi_s[IDX_hwrite_i] = m->p.hwrite_i;
    presi_s[IDX_hready_i] = m->p.hready_i;
    for (i = 0; i < 32; i++) presi_s[presi_haddr_i_idx[i]]  = m->p.haddr_i[i];
    for (i = 0; i < 64; i++) presi_s[presi_hwdata_i_idx[i]] = m->p.hwdata_i[i];
    for (i = 0; i <  2; i++) presi_s[presi_htrans_i_idx[i]] = m->p.htrans_i[i];
    for (i = 0; i <  3; i++) presi_s[presi_hsize_i_idx[i]]  = m->p.hsize_i[i];
#else
    (void) m;
#endif
}

static void presi_capture_outputs(struct presi_model *m)
{
#ifdef PRESI_HAVE_NETLIST
    int i;
    m->p.hresp_o     = presi_s[IDX_hresp_o];
    m->p.hreadyout_o = presi_s[IDX_hreadyout_o];
    m->p.busy_o      = presi_s[IDX_busy_o];
    m->p.error_intr  = presi_s[IDX_error_intr];
    m->p.notif_intr  = presi_s[IDX_notif_intr];
    for (i = 0; i < 64; i++) m->p.hrdata_o[i] = presi_s[presi_hrdata_o_idx[i]];
#else
    (void) m;
#endif
}

static void presi_sram_tick_all(struct presi_model *m)
{
    (void) m;
#ifdef PRESI_HAVE_NETLIST
    /*
     * Generated body: one block per blackbox SRAM instance plus an
     * abr_seq $mem_v2 ROM block.  Each block samples
     * we_i/waddr_i/wdata_i/re_i/raddr_i (and wstrobe_i for the
     * byte-enable variant) from presi_s[], calls the matching
     * presi_sram_* helper, and writes the read result back over the
     * rdata_o bits.
     *
     * Ordering: invoked AFTER the rising-edge step (_flop) and the
     * settle pass in presi_cycle, so SRAM samples its inputs as
     * they appear at the rising edge and the rdata_o is observed
     * by combinational logic on the NEXT cycle -- matching the
     * synchronous one-cycle read latency of abr_1r1w_ram /
     * abr_1r1w_be_ram.
     */
#include "abr_wrap.presi_bb_wiring.h"
#endif
}

void presi_drive_idle(struct presi_model *m)
{
    m->p.hsel_i = PRESI_0;
    m->p.hwrite_i = PRESI_0;
    presi_u32_to_bits(m->p.htrans_i, 2, AHB_TRANS_IDLE);
    presi_u32_to_bits(m->p.hsize_i, 3, 2u);
}

void presi_cycle(struct presi_model *m)
{
    /*
     * One logical clock cycle.  Comb chunks run on every phase; flop
     * chunks run only on the rising-edge phase (the only time
     * `_edge = clk & ~clk_prev` is non-zero).
     *
     * `presi_clk_prev` (and each engine's `<prefix>presi_clk_prev`)
     * is updated explicitly between phases so the rising-edge
     * predicate fires exactly once per cycle.  The engine glue's
     * _flop function updates the engine clk_prev itself; we update
     * abr_wrap's clk_prev here.
     */
#ifdef PRESI_HAVE_NETLIST
    /* Phase 0: falling edge.  Comb only; no flops would tick. */
    m->p.clk = PRESI_0;
    presi_apply_inputs(m);
    presi_step_netlist_comb();
    presi_engines_step_comb();
    presi_clk_prev = PRESI_0;
# if defined(PRESI_HAVE_ENGINE_NETLISTS)
    ntt_top__presi_clk_prev = PRESI_0;
    abr_sampler_top__presi_clk_prev = PRESI_0;
# endif

    /* Phase 1: rising edge.  Comb settles with clk=1, then flops
     * tick (their `_edge` reads clk_prev=0 from above), then engines
     * tick similarly.  After this, clk_prev becomes 1 (engine glue
     * _flop updates engine clk_prev as a side effect; we update
     * abr_wrap's here). */
    m->p.clk = PRESI_1;
    presi_apply_inputs(m);
    presi_step_netlist_comb();
    presi_engines_step_comb();
    presi_step_netlist_flop();
    presi_engines_step_flop();
    presi_clk_prev = PRESI_1;

    /* Settle pass: refresh abr_wrap comb downstream of the engine
     * output paste from _flop, and let engines re-derive comb from
     * refreshed abr_wrap inputs.  No flops tick (clk_prev=1=clk). */
    presi_step_netlist_comb();
    presi_engines_step_comb();

    presi_sram_tick_all(m);
    presi_capture_outputs(m);
#else
    (void) m;
#endif
    m->cycle++;
    m->p.hready_i = m->p.hreadyout_o;
}

void presi_reset(struct presi_model *m, unsigned cycles)
{
    unsigned i;
    m->p.rst_b = PRESI_0;
    m->p.hready_i = PRESI_1;
    presi_drive_idle(m);
    for (i = 0; i < cycles; i++) {
        presi_cycle(m);
    }
    m->p.rst_b = PRESI_1;
    for (i = 0; i < cycles; i++) {
        presi_cycle(m);
    }
}

void presi_settle_after_load(struct presi_model *m)
{
    /*
     * After loading flop / port / SRAM state from a snapshot, run one
     * combinational pass to refresh comb wires.  Comb-only: we do not
     * call _flop (no rising edge to process here -- the snapshot was
     * taken at a settled clk=1 / clk_prev=1 state).
     */
    presi_apply_inputs(m);
    presi_step_netlist_comb();
    presi_engines_step_comb();
    presi_capture_outputs(m);
}

/* ============================================================
 * AHB transactions
 * ============================================================
 *
 * Textbook AHB-Lite, 1-cycle address phase + 1-cycle data phase.
 * abr_ahb_slv_sif registers (addr, dv, write) at posedge from the
 * incoming haddr/hsel/htrans/hwrite.  abr_reg's readback mux is
 * combinational from those registered values; the AHB-side wdata
 * mux is combinational from hwdata_i with the lane chosen by the
 * registered addr[2].
 */

void ahb_write(struct presi_model *m, uint32_t addr, uint32_t data)
{
    uint64_t lane_data;
    lane_data = (addr & 4u) ? ((uint64_t) data << 32) : (uint64_t) data;

    /* Address phase. */
    m->p.hsel_i = PRESI_1;
    m->p.hwrite_i = PRESI_1;
    presi_u32_to_bits(m->p.htrans_i, 2, AHB_TRANS_NONSEQ);
    presi_u32_to_bits(m->p.hsize_i, 3, 2u);
    presi_u32_to_bits(m->p.haddr_i, 32, addr);
    presi_u64_to_bits(m->p.hwdata_i, 64, 0);
    presi_cycle(m);

    /* Data phase: address goes IDLE, hwdata presents the payload. */
    presi_drive_idle(m);
    presi_u64_to_bits(m->p.hwdata_i, 64, lane_data);
    presi_cycle(m);

    presi_u64_to_bits(m->p.hwdata_i, 64, 0);
}

uint32_t ahb_read(struct presi_model *m, uint32_t addr)
{
    uint64_t data;
    unsigned wait;

    m->p.hsel_i = PRESI_1;
    m->p.hwrite_i = PRESI_0;
    presi_u32_to_bits(m->p.htrans_i, 2, AHB_TRANS_NONSEQ);
    presi_u32_to_bits(m->p.hsize_i, 3, 2u);
    presi_u32_to_bits(m->p.haddr_i, 32, addr);
    presi_cycle(m);

    presi_drive_idle(m);
    presi_u64_to_bits(m->p.hwdata_i, 64, 0);
    presi_cycle(m);

    /* External-region reads (PUBKEY / PRIVKEY / SIGNATURE / etc.)
     * stall the AHB slave (hreadyout_o = 0) for several cycles via
     * abr_reg's external_pending logic. */
    for (wait = 0; wait < 1024 && !(m->p.hreadyout_o & 1); wait++) {
        presi_drive_idle(m);
        presi_u64_to_bits(m->p.hwdata_i, 64, 0);
        presi_cycle(m);
    }
    data = presi_bits_to_u64(m->p.hrdata_o, 64);
    return (uint32_t) ((addr & 4u) ? (data >> 32) : data);
}

void ahb_write_block(struct presi_model *m, uint32_t addr,
                     const uint32_t *data, size_t words)
{
    size_t i;
    for (i = 0; i < words; i++) {
        ahb_write(m, addr + (uint32_t) (i * 4), data[i]);
    }
}

void ahb_read_block(struct presi_model *m, uint32_t addr,
                    uint32_t *data, size_t words)
{
    size_t i;
    for (i = 0; i < words; i++) {
        data[i] = ahb_read(m, addr + (uint32_t) (i * 4));
    }
}

int wait_for_status(struct presi_model *m, uint32_t want_mask,
                    uint32_t error_mask, uint64_t max_cycles, int verbose)
{
    int prev = -1;
    uint64_t start = m->cycle;
    uint64_t deadline = start + max_cycles;

    for (;;) {
        uint32_t st;
        if (m->cycle >= deadline) {
            return -1;
        }
        st = ahb_read(m, ABR_STATUS);
        if (verbose && (int) st != prev) {
            printf("[STAT]\tcycle=%llu  status=%08x%s%s%s\n",
                   (unsigned long long) m->cycle, st,
                   (st & ABR_STATUS_READY) ? " READY" : "",
                   (st & ABR_STATUS_VALID) ? " VALID" : "",
                   (st & error_mask)       ? " ERROR" : "");
            prev = (int) st;
        }
        if (st & error_mask) {
            return (int) st;
        }
        if ((st & want_mask) == want_mask) {
            return (int) st;
        }
        /* Quietly advance a few cycles between status reads. */
        {
            int j;
            for (j = 0; j < 32 && m->cycle < deadline; j++) {
                presi_cycle(m);
            }
        }
    }
}

/* ============================================================
 * File helpers
 * ============================================================ */

size_t read_dat(uint32_t *buf, size_t bufsz, const char *fn, int optional)
{
    FILE *fp;
    size_t n;

    memset(buf, 0, bufsz);
    fp = fopen(fn, "rb");
    if (fp == NULL) {
        if (optional)
            return 0;
        perror(fn);
        exit(1);
    }
    n = fread(buf, 1, bufsz, fp);
    fclose(fp);
    printf("[LOAD]\t%s (read %zu bytes)\n", fn, n);
    return n;
}

size_t write_dat(const uint32_t *buf, size_t bufsz, const char *fn)
{
    FILE *fp;
    size_t n;

    fp = fopen(fn, "wb");
    if (fp == NULL) {
        perror(fn);
        exit(1);
    }
    n = fwrite(buf, 1, bufsz, fp);
    fclose(fp);
    printf("[SAVE]\t%s (wrote %zu bytes)\n", fn, n);
    return n;
}

/* ============================================================
 * Model lifecycle
 * ============================================================ */

int presi_model_init(struct presi_model *m)
{
    unsigned i;
    memset(m, 0, sizeof(*m));
    for (i = 0; i < PRESI_ABR_SRAM_COUNT; i++) {
        if (presi_sram_init(&m->srams[i], &presi_sram_descs[i]) != 0) {
            return -1;
        }
    }
    return 0;
}

void presi_model_free(struct presi_model *m)
{
    unsigned i;
    for (i = 0; i < PRESI_ABR_SRAM_COUNT; i++) {
        presi_sram_free(&m->srams[i]);
    }
}

/* ============================================================
 * ML-DSA-87 keygen, split into independently runnable phases
 * ============================================================ */

int mldsa_keygen_init(struct presi_model *m,
                      const char *ent_fn, const char *seed_fn)
{
    uint32_t entropy[SZ_U32(ENTROPY_SZ)] = {0};
    uint32_t seed[SZ_U32(MLDSA_SEED_SZ)] = {0};
    uint64_t c0, c1;

    read_dat(entropy, ENTROPY_SZ, ent_fn, 1);
    read_dat(seed,    MLDSA_SEED_SZ, seed_fn, 0);

    c0 = m->cycle;
    printf("[KGEN]\tcycle=%llu  loading entropy + seed\n",
           (unsigned long long) c0);
    ahb_write_block(m, ABR_ENTROPY, entropy, SZ_U32(ENTROPY_SZ));
    ahb_write_block(m, MLDSA_SEED,  seed,    SZ_U32(MLDSA_SEED_SZ));

    c1 = m->cycle;
    printf("[KGEN]\tcycle=%llu  writing CTRL=KEYGEN (load=%llu cy)\n",
           (unsigned long long) c1, (unsigned long long) (c1 - c0));
    ahb_write(m, ABR_CTRL, CTRL_KEYGEN);
    return 0;
}

int mldsa_keygen_run(struct presi_model *m, uint64_t max_cycles)
{
    uint64_t c0 = m->cycle;
    int st;

    st = wait_for_status(m, ABR_STATUS_READY | ABR_STATUS_VALID,
                         ABR_STATUS_MLDSA_ERROR, max_cycles, 1);
    if (st < 0) {
        printf("[KGEN]\tTIMEOUT after %llu cycles\n",
               (unsigned long long) (m->cycle - c0));
        return 1;
    }
    if (st & ABR_STATUS_MLDSA_ERROR) {
        printf("[KGEN]\tERROR status=%08x\n", (unsigned) st);
        return 2;
    }
    printf("[KGEN]\tREADY|VALID in %llu cycles\n",
           (unsigned long long) (m->cycle - c0));
    return 0;
}

int mldsa_keygen_finish(struct presi_model *m,
                        const char *pk_fn, const char *sk_fn)
{
    uint32_t pk[SZ_U32(MLDSA_PUBKEY_SZ)] = {0};
    uint32_t sk[SZ_U32(MLDSA_PRIVKEY_SZ)] = {0};

    if (pk_fn != NULL) {
        printf("[KGEN]\treading public key (%u bytes)\n", MLDSA_PUBKEY_SZ);
        ahb_read_block(m, MLDSA_PUBKEY, pk, SZ_U32(MLDSA_PUBKEY_SZ));
        write_dat(pk, MLDSA_PUBKEY_SZ, pk_fn);
    }
    if (sk_fn != NULL) {
        printf("[KGEN]\treading private key (%u bytes)\n", MLDSA_PRIVKEY_SZ);
        ahb_read_block(m, MLDSA_PRIVKEY_OUT, sk, SZ_U32(MLDSA_PRIVKEY_SZ));
        write_dat(sk, MLDSA_PRIVKEY_SZ, sk_fn);
    }
    return 0;
}

int mldsa_keygen(struct presi_model *m, uint64_t max_cycles,
                 const char *ent_fn, const char *seed_fn,
                 const char *pk_fn, const char *sk_fn)
{
    int rc;
    rc = mldsa_keygen_init(m, ent_fn, seed_fn);
    if (rc != 0) return rc;
    rc = mldsa_keygen_run(m, max_cycles);
    if (rc != 0) return rc;
    return mldsa_keygen_finish(m, pk_fn, sk_fn);
}
