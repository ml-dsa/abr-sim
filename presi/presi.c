//  presi.c
//  Adams Bridge presilicon simulator harness prototype.

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "abr_wrap.sram.h"
#include "presi_sram.h"

#ifdef PRESI_HAVE_NETLIST
/*
 * Generated header: defines `presi_t`, the PRESI_0/PRESI_1 constants, and
 * declares every netlist wire as `extern presi_t <name>;`.  Definitions
 * live in abr_wrap.presi_var.c, compiled into a separate translation unit.
 */
#include "abr_wrap.presi_var.h"
/*
 * abr_seq sequencer ROM contents extracted from the Yosys gates JSON.
 * Defines `presi_abr_seq_rom[1024][N]`, `PRESI_ABR_SEQ_ROM_SIZE`,
 * `PRESI_ABR_SEQ_ROM_WIDTH`, `PRESI_ABR_SEQ_ROM_WORDS`.  The bb-wiring
 * block in presi_sram_tick_all() drives RD_DATA bits from this table.
 */
#include "abr_wrap.seq_rom.h"
#else
typedef uint8_t presi_t;
#define PRESI_0 ((presi_t) 0)
#define PRESI_1 ((presi_t) ~0)
#endif

#define ABR_NAME            0x0000u
#define ABR_VERSION         0x0008u
#define ABR_CTRL            0x0010u
#define ABR_STATUS          0x0014u
#define ABR_ENTROPY         0x0018u

#define ABR_STATUS_READY    0x00000001u
#define ABR_STATUS_VALID    0x00000002u

#define AHB_TRANS_IDLE      0u
#define AHB_TRANS_NONSEQ    2u

struct presi_ports {
    presi_t clk;
    presi_t rst_b;
    presi_t hsel_i;
    presi_t hwrite_i;
    presi_t hready_i;
    presi_t haddr_i[32];
    presi_t hwdata_i[64];
    presi_t hsize_i[3];
    presi_t htrans_i[2];
    presi_t busy_o;
    presi_t error_intr;
    presi_t notif_intr;
    presi_t hreadyout_o;
    presi_t hresp_o;
    presi_t hrdata_o[64];
};

struct presi_model {
    struct presi_ports p;
    struct presi_sram srams[PRESI_ABR_SRAM_COUNT];
    uint64_t cycle;
};

static uint32_t in_word(const presi_t *v, int n)
{
    int i;
    uint32_t x;

    x = 0;
    for (i = 0; i < n; i++) {
        x |= (uint32_t) (v[i] & 1) << i;
    }
    return x;
}

static void out_word(presi_t *v, int n, uint32_t x)
{
    int i;

    for (i = 0; i < n; i++) {
        v[i] = (x >> i) & 1 ? PRESI_1 : PRESI_0;
    }
}

static uint64_t in_dword(const presi_t *v, int n)
{
    int i;
    uint64_t x;

    x = 0;
    for (i = 0; i < n; i++) {
        x |= (uint64_t) (v[i] & 1) << i;
    }
    return x;
}

static void out_dword(presi_t *v, int n, uint64_t x)
{
    int i;

    for (i = 0; i < n; i++) {
        v[i] = (x >> i) & 1 ? PRESI_1 : PRESI_0;
    }
}

static void presi_step_netlist(void)
{
#ifdef PRESI_HAVE_NETLIST
#include "abr_wrap.presi_clk.h"
    /* After each step, snapshot clk so the next step's rising-edge
     * predicate (`clk & ~presi_clk_prev`) fires only on a real 0->1
     * transition.  Without this, repeated calls with clk=1 would
     * re-clock every flop and produce double-rate behavior. */
    presi_clk_prev = clk;
#endif
}

#ifdef PRESI_HAVE_NETLIST

/*
 * Tables that map each abr_wrap top-level port bit to its extern presi_t
 * in the generated netlist.  The bit order matches m->p.* (LSB at index 0)
 * and the rtl/abr_wrap.sv declarations.
 *
 * Single-bit ports are direct: `extern presi_t clk;`.  Bus ports get one
 * array per bus -- listed by name because C globals can't be indexed at
 * runtime through a single symbol.
 */

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

#define X(i) extern presi_t haddr_i_##i;
_PRESI_HADDR_BITS
#undef X
#define X(i) extern presi_t hwdata_i_##i;
_PRESI_HWDATA_BITS
#undef X
#define X(i) extern presi_t htrans_i_##i;
_PRESI_HTRANS_BITS
#undef X
#define X(i) extern presi_t hsize_i_##i;
_PRESI_HSIZE_BITS
#undef X
#define X(i) extern presi_t hrdata_o_##i;
_PRESI_HRDATA_BITS
#undef X

static presi_t *const presi_haddr_i_bits[32] = {
#define X(i) &haddr_i_##i,
    _PRESI_HADDR_BITS
#undef X
};
static presi_t *const presi_hwdata_i_bits[64] = {
#define X(i) &hwdata_i_##i,
    _PRESI_HWDATA_BITS
#undef X
};
static presi_t *const presi_htrans_i_bits[2] = {
#define X(i) &htrans_i_##i,
    _PRESI_HTRANS_BITS
#undef X
};
static presi_t *const presi_hsize_i_bits[3] = {
#define X(i) &hsize_i_##i,
    _PRESI_HSIZE_BITS
#undef X
};
static presi_t *const presi_hrdata_o_bits[64] = {
#define X(i) &hrdata_o_##i,
    _PRESI_HRDATA_BITS
#undef X
};

/*
 * Internal-state probes.  These reach into the flattened netlist by
 * extern reference -- the names come straight from
 * presi_map.csv (the SPICE->C name table emitted by spice_to_c.py) and
 * exist only as long as the abr_ctrl wires aren't optimised away.  Used
 * by the FSM-trace block in main() so we can watch abr_prog_cntr walk
 * the ROM after a MLDSA_CTRL write.
 */
#define _PRESI_PROG_CNTR_BITS \
    X(0) X(1) X(2) X(3) X(4) X(5) X(6) X(7) X(8) X(9)

#define _PRESI_CMD_REG_BITS \
    X(0) X(1) X(2)

#define X(i) extern presi_t top0_abr_ctrl_inst_abr_prog_cntr_##i;
_PRESI_PROG_CNTR_BITS
#undef X
#define X(i) extern presi_t top0_abr_ctrl_inst_abr_prog_cntr_nxt_##i;
_PRESI_PROG_CNTR_BITS
#undef X
#define X(i) extern presi_t top0_abr_ctrl_inst_mldsa_cmd_reg_##i;
_PRESI_CMD_REG_BITS
#undef X
#define X(i) extern presi_t top0_abr_ctrl_inst_mlkem_cmd_reg_##i;
_PRESI_CMD_REG_BITS
#undef X
extern presi_t top0_abr_ctrl_inst_abr_seq_en;
extern presi_t top0_abr_ctrl_inst_zeroize;
/* `top0_abr_ctrl_inst_zeroize` is the wire opt-merged with
 * stream_msg_buffer.zeroize -- it tracks the *output* of the OR that
 * feeds the abr_ctrl zeroize signal.  Per spice_to_c output:
 *   stream_msg_buffer_zeroize = field_storage_357 | field_storage_5095
 * which are the two ZEROIZE field flops (MLDSA / MLKEM). */
extern presi_t top0_abr_ctrl_inst_stream_msg_buffer_zeroize;
/* Higher-level state */
extern presi_t top0_abr_ctrl_inst_abr_ready;
extern presi_t top0_abr_ctrl_inst_abr_idle;
extern presi_t top0_abr_ctrl_inst_error_flag_reg;
extern presi_t top0_abr_ctrl_inst_subcomponent_busy;
extern presi_t top0_abr_ctrl_inst_clear_verify_valid;
extern presi_t top0_abr_ctrl_inst_mldsa_keygen_process;

static const presi_t *const presi_prog_cntr_bits[10] = {
#define X(i) &top0_abr_ctrl_inst_abr_prog_cntr_##i,
    _PRESI_PROG_CNTR_BITS
#undef X
};
static const presi_t *const presi_prog_cntr_nxt_bits[10] = {
#define X(i) &top0_abr_ctrl_inst_abr_prog_cntr_nxt_##i,
    _PRESI_PROG_CNTR_BITS
#undef X
};
static const presi_t *const presi_mldsa_cmd_reg_bits[3] = {
#define X(i) &top0_abr_ctrl_inst_mldsa_cmd_reg_##i,
    _PRESI_CMD_REG_BITS
#undef X
};
static const presi_t *const presi_mlkem_cmd_reg_bits[3] = {
#define X(i) &top0_abr_ctrl_inst_mlkem_cmd_reg_##i,
    _PRESI_CMD_REG_BITS
#undef X
};

static unsigned presi_read_bits(const presi_t *const *bits, int n)
{
    unsigned v = 0;
    int i;

    for (i = 0; i < n; i++) {
        v |= (unsigned) (*bits[i] & 1) << i;
    }
    return v;
}

#endif /* PRESI_HAVE_NETLIST */


static void presi_apply_inputs(struct presi_model *m)
{
#ifdef PRESI_HAVE_NETLIST
    int i;

    clk      = m->p.clk;
    rst_b    = m->p.rst_b;
    hsel_i   = m->p.hsel_i;
    hwrite_i = m->p.hwrite_i;
    hready_i = m->p.hready_i;
    for (i = 0; i < 32; i++) *presi_haddr_i_bits[i]  = m->p.haddr_i[i];
    for (i = 0; i < 64; i++) *presi_hwdata_i_bits[i] = m->p.hwdata_i[i];
    for (i = 0; i <  2; i++) *presi_htrans_i_bits[i] = m->p.htrans_i[i];
    for (i = 0; i <  3; i++) *presi_hsize_i_bits[i]  = m->p.hsize_i[i];
#else
    (void) m;
#endif
}

static void presi_capture_outputs(struct presi_model *m)
{
#ifdef PRESI_HAVE_NETLIST
    int i;

    m->p.hresp_o     = hresp_o;
    m->p.hreadyout_o = hreadyout_o;
    m->p.busy_o      = busy_o;
    m->p.error_intr  = error_intr;
    m->p.notif_intr  = notif_intr;
    for (i = 0; i < 64; i++) m->p.hrdata_o[i] = *presi_hrdata_o_bits[i];
#else
    (void) m;
#endif
}

static void presi_sram_tick_all(struct presi_model *m)
{
    (void) m;
#ifdef PRESI_HAVE_NETLIST
    /*
     * Generated body: one block per blackbox SRAM instance.  Each block
     * samples we_i/waddr_i/wdata_i/re_i/raddr_i (and wstrobe_i for the
     * byte-enable variant) from the netlist's extern presi_t variables,
     * calls the matching presi_sram_* helper, and writes the read result
     * back over the rdata_o bits.
     *
     * Ordering: invoked AFTER presi_step_netlist() in presi_cycle so that
     * the SRAM samples its inputs as they appear at the rising edge and
     * the rdata_o it writes is observed by combinational logic on the
     * NEXT cycle -- matching the synchronous one-cycle read latency of
     * abr_1r1w_ram / abr_1r1w_be_ram.
     */
#include "abr_wrap.presi_bb_wiring.h"
#endif
}

static void presi_drive_idle(struct presi_model *m)
{
    m->p.hsel_i = PRESI_0;
    m->p.hwrite_i = PRESI_0;
    out_word(m->p.htrans_i, 2, AHB_TRANS_IDLE);
    out_word(m->p.hsize_i, 3, 2u);
}

static void presi_cycle(struct presi_model *m)
{
    /*
     * One logical clock cycle.  spice_to_c.py emits each DFF with the
     * rising-edge predicate `(clk & ~presi_clk_prev)`, so flops only
     * tick on a 0->1 clock transition; subsequent step_netlist calls
     * settle combinational without re-clocking.
     *
     * We call step_netlist multiple times per phase to fully settle
     * the gate-level network: each call propagates roughly one "level"
     * of logic per part file, and the part files run in fixed order
     * across 32 TUs, so reading a signal before the cell driving it is
     * evaluated returns a stale value.  Three passes per phase
     * empirically settles abr_wrap.
     */
    m->p.clk = PRESI_0;
    presi_apply_inputs(m);
    presi_step_netlist();

    m->p.clk = PRESI_1;
    presi_apply_inputs(m);
    presi_step_netlist();
    presi_sram_tick_all(m);
    presi_capture_outputs(m);
    m->cycle++;
    m->p.hready_i = m->p.hreadyout_o;
}

static void presi_reset(struct presi_model *m, unsigned cycles)
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

/*
 * Textbook AHB-Lite, 1-cycle address phase + 1-cycle data phase.
 *
 * abr_ahb_slv_sif registers (addr, dv, write) at posedge from the
 * incoming haddr/hsel/htrans/hwrite.  The downstream abr_reg readback
 * mux is fully combinational from those registered values, and the
 * AHB-side wdata mux is combinational from hwdata_i with the lane
 * selected by the registered addr[2].  So a single-cycle address phase
 * is sufficient: at the next rising edge the slave latches (addr, dv),
 * during the data phase hrdata_o reflects this transaction, and
 * hwdata_i drives the registered field at the data-phase rising edge.
 *
 * The earlier 2-cycle address-phase hold was an empirical workaround
 * for the prior level-sensitive DFF emission, which double-clocked
 * every flop per harness call.  Now that spice_to_c emits each DFF
 * with the rising-edge predicate `(clk & ~presi_clk_prev)`, one
 * presi_cycle equals exactly one logical clock edge, and the textbook
 * protocol works directly.
 */
static void ahb_write(struct presi_model *m, uint32_t addr, uint32_t data)
{
    uint64_t lane_data;

    lane_data = (addr & 4u) ? ((uint64_t) data << 32) : (uint64_t) data;

    /* Address phase: master drives haddr/hsel/hwrite/htrans=NONSEQ. */
    m->p.hsel_i = PRESI_1;
    m->p.hwrite_i = PRESI_1;
    out_word(m->p.htrans_i, 2, AHB_TRANS_NONSEQ);
    out_word(m->p.hsize_i, 3, 2u);
    out_word(m->p.haddr_i, 32, addr);
    out_dword(m->p.hwdata_i, 64, 0);
    presi_cycle(m);

    /* Data phase: address goes IDLE, hwdata presents the payload.  At
     * the next rising edge the registered field_storage in abr_reg
     * samples wdata. */
    presi_drive_idle(m);
    out_dword(m->p.hwdata_i, 64, lane_data);
    presi_cycle(m);

    out_dword(m->p.hwdata_i, 64, 0);
}

static uint32_t ahb_read(struct presi_model *m, uint32_t addr)
{
    uint64_t data;

    /* Address phase: drive haddr/hsel/htrans=NONSEQ, hwrite=0. */
    m->p.hsel_i = PRESI_1;
    m->p.hwrite_i = PRESI_0;
    out_word(m->p.htrans_i, 2, AHB_TRANS_NONSEQ);
    out_word(m->p.hsize_i, 3, 2u);
    out_word(m->p.haddr_i, 32, addr);
    presi_cycle(m);

    /* Data phase: master drives IDLE; hrdata_o is combinational from
     * the registered addr and reflects this transaction. */
    presi_drive_idle(m);
    out_dword(m->p.hwdata_i, 64, 0);
    presi_cycle(m);
    data = in_dword(m->p.hrdata_o, 64);
    return (uint32_t) ((addr & 4u) ? (data >> 32) : data);
}

static int presi_init(struct presi_model *m)
{
    unsigned i;

    for (i = 0; i < PRESI_ABR_SRAM_COUNT; i++) {
        if (presi_sram_init(&m->srams[i], &presi_sram_descs[i]) != 0) {
            return -1;
        }
    }
    return 0;
}

static void presi_free(struct presi_model *m)
{
    unsigned i;

    for (i = 0; i < PRESI_ABR_SRAM_COUNT; i++) {
        presi_sram_free(&m->srams[i]);
    }
}

int main(int argc, char **argv)
{
    struct presi_model model;
    unsigned i;

    (void) argc;
    (void) argv;
    memset(&model, 0, sizeof(model));

    printf("[INIT]\tpresi harness\n");
    printf("[INFO]\tSRAM models: %u\n", (unsigned) PRESI_ABR_SRAM_COUNT);
    {
        presi_t tmp[32];
        out_word(tmp, 32, 0x12345678u);
        if (in_word(tmp, 32) != 0x12345678u) {
            fprintf(stderr, "bit-vector helper self-test failed\n");
            return 1;
        }
    }

    if (presi_init(&model) != 0) {
        for (i = 0; i < PRESI_ABR_SRAM_COUNT; i++) {
            fprintf(stderr, "could not allocate SRAM %s\n",
                    presi_sram_descs[i].name);
        }
        return 1;
    }

    for (i = 0; i < PRESI_ABR_SRAM_COUNT; i++) {
        printf("[SRAM]\t%u\t%s depth=%u width=%u%s\n",
               i, presi_sram_descs[i].name,
               presi_sram_descs[i].depth,
               presi_sram_descs[i].data_width,
               presi_sram_descs[i].byte_enable ? " be" : "");
    }

    presi_reset(&model, 64);
    printf("[BUS]\tcycle=%llu post-reset hreadyout=%u busy=%u\n",
           (unsigned long long) model.cycle,
           model.p.hreadyout_o & 1, model.p.busy_o & 1);
    /*
     * Sanity-check the abr_reg name/version/status layout.  Constants
     * come from abr_params_pkg.sv:
     *   MLDSA_CORE_NAME    = 64'h3837412D_44534D4C  ("MLDSA-87")
     *   MLDSA_CORE_VERSION = 64'h00003300_302E322E  ("2.0.3")
     */
    printf("[BUS]\tNAME[0]    =%08x  (want 44534d4c)\n",
           ahb_read(&model, 0x00));
    printf("[BUS]\tNAME[1]    =%08x  (want 3837412d)\n",
           ahb_read(&model, 0x04));
    printf("[BUS]\tVERSION[0] =%08x  (want 302e322e)\n",
           ahb_read(&model, 0x08));
    printf("[BUS]\tVERSION[1] =%08x  (want 00003300)\n",
           ahb_read(&model, 0x0c));
    printf("[BUS]\tSTATUS     =%08x  (READY bit expected = 1)\n",
           ahb_read(&model, 0x14));
    ahb_write(&model, ABR_ENTROPY, 0);

    /*
     * Smoke test: kick off MLDSA keygen and watch the controller walk
     * the abr_seq ROM for a few hundred cycles.  With only the ROM
     * wired (engines still stubbed out), the FSM should leave
     * ABR_RESET, advance MLDSA_KG_S, MLDSA_KG_S+1, ..., and stall once
     * it asks the (stub) sampler/SHA3 to acknowledge a UOP.  What we
     * want to confirm here is that abr_prog_cntr actually moves --
     * proves the ROM dispatch is correctly plumbed.
     *
     * Trace strategy: read abr_prog_cntr each cycle and print only on
     * change, so a single multi-cycle stall doesn't drown the log.
     */
    {
        unsigned poll;
        unsigned n_busy = 0;
        unsigned cycle_at_busy = 0;
        unsigned prev_pc = (unsigned) -1;
        unsigned same_count = 0;
        printf("[CTRL]\twriting MLDSA_CTRL = 1 (keygen)\n");
        ahb_write(&model, ABR_CTRL, 1);
        for (poll = 0; poll < 256; poll++) {
            presi_cycle(&model);
#ifdef PRESI_HAVE_NETLIST
            {
                unsigned pc = presi_read_bits(presi_prog_cntr_bits, 10);
                unsigned pc_nxt = presi_read_bits(presi_prog_cntr_nxt_bits, 10);
                unsigned en = top0_abr_ctrl_inst_abr_seq_en & 1;
                unsigned mldsa_cmd = presi_read_bits(presi_mldsa_cmd_reg_bits, 3);
                unsigned mlkem_cmd = presi_read_bits(presi_mlkem_cmd_reg_bits, 3);
                unsigned z = top0_abr_ctrl_inst_stream_msg_buffer_zeroize & 1;
                unsigned rdy = top0_abr_ctrl_inst_abr_ready & 1;
                unsigned idle = top0_abr_ctrl_inst_abr_idle & 1;
                unsigned err = top0_abr_ctrl_inst_error_flag_reg & 1;
                unsigned sub = top0_abr_ctrl_inst_subcomponent_busy & 1;
                unsigned cvv = top0_abr_ctrl_inst_clear_verify_valid & 1;
                unsigned kgp = top0_abr_ctrl_inst_mldsa_keygen_process & 1;
                if (poll < 16) {
                    printf("[FSM]\tc=%u pc=%u nxt=%u en=%u  "
                           "rdy=%u idle=%u err=%u sub=%u cvv=%u kgp=%u  "
                           "z=%u cmd=%u/%u\n",
                           poll, pc, pc_nxt, en, rdy, idle, err, sub, cvv,
                           kgp, z, mldsa_cmd, mlkem_cmd);
                } else if (pc != prev_pc) {
                    if (same_count > 0) {
                        printf("[FSM]\t  ... held for %u cycle%s\n",
                               same_count, same_count == 1 ? "" : "s");
                    }
                    printf("[FSM]\tcycle=%u  pc=%u  nxt=%u  en=%u  z=%u\n",
                           poll, pc, pc_nxt, en, z);
                    prev_pc = pc;
                    same_count = 0;
                } else {
                    same_count++;
                }
            }
            (void) prev_pc;
#endif
            if (model.p.busy_o & 1) {
                if (n_busy == 0) {
                    cycle_at_busy = poll;
                }
                n_busy++;
            }
        }
        if (same_count > 0) {
            printf("[FSM]\t  ... held for %u cycle%s\n",
                   same_count, same_count == 1 ? "" : "s");
        }
        printf("[CTRL]\tafter %u cycles: first-busy=%u busy-cycles=%u "
               "final-busy=%u status=%08x\n",
               poll, cycle_at_busy, n_busy, model.p.busy_o & 1,
               ahb_read(&model, ABR_STATUS));
    }

#ifndef PRESI_HAVE_NETLIST
    printf("[INFO]\tgenerated netlist C is not wired yet\n");
#else
    printf("[INFO]\tabr_seq ROM wired; engines still TODO\n");
#endif

    presi_free(&model);

    return 0;
}
