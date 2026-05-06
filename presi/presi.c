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
#endif
}

static void presi_apply_inputs(struct presi_model *m)
{
    (void) m;
    /*
     * The generated SPICE-name map is emitted as abr_wrap.presi_map.csv.
     * The next stage wires these stable harness ports to the generated
     * scalar variables once the gate netlist completes.
     */
}

static void presi_capture_outputs(struct presi_model *m)
{
    (void) m;
}

static void presi_sram_tick_all(struct presi_model *m)
{
    (void) m;
    /*
     * SRAM descriptors are available now, but the port-variable binding
     * depends on the generated flattened names.  Keep this as a single hook
     * so SRAM timing is ordered consistently with presi_step_netlist().
     */
}

static void presi_drive_idle(struct presi_model *m)
{
    m->p.hsel_i = PRESI_0;
    m->p.hwrite_i = PRESI_0;
    out_word(m->p.htrans_i, 2, AHB_TRANS_IDLE);
    out_word(m->p.hsize_i, 3, 2u);
}

static void presi_half_cycle(struct presi_model *m)
{
    m->p.clk = (m->p.clk & 1) ? PRESI_0 : PRESI_1;
    presi_apply_inputs(m);
    presi_sram_tick_all(m);
    presi_step_netlist();
    presi_capture_outputs(m);
}

static void presi_cycle(struct presi_model *m)
{
    presi_half_cycle(m);
    presi_half_cycle(m);
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

static void ahb_clear(struct presi_model *m)
{
    presi_drive_idle(m);
    out_dword(m->p.hwdata_i, 64, 0);
}

static void ahb_write(struct presi_model *m, uint32_t addr, uint32_t data)
{
    uint64_t lane_data;

    lane_data = (addr & 4u) ? ((uint64_t) data << 32) : (uint64_t) data;
    m->p.hsel_i = PRESI_1;
    m->p.hwrite_i = PRESI_1;
    out_word(m->p.htrans_i, 2, AHB_TRANS_NONSEQ);
    out_word(m->p.hsize_i, 3, 2u);
    out_word(m->p.haddr_i, 32, addr);
    out_dword(m->p.hwdata_i, 64, lane_data);
    presi_cycle(m);
    ahb_clear(m);
}

static uint32_t ahb_read(struct presi_model *m, uint32_t addr)
{
    uint64_t data;

    m->p.hsel_i = PRESI_1;
    m->p.hwrite_i = PRESI_0;
    out_word(m->p.htrans_i, 2, AHB_TRANS_NONSEQ);
    out_word(m->p.hsize_i, 3, 2u);
    out_word(m->p.haddr_i, 32, addr);
    presi_cycle(m);
    data = in_dword(m->p.hrdata_o, 64);
    ahb_clear(m);
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

    presi_reset(&model, 5);
    printf("[BUS]\tcycle=%llu name=%08x version=%08x status=%08x\n",
           (unsigned long long) model.cycle,
           ahb_read(&model, ABR_NAME),
           ahb_read(&model, ABR_VERSION),
           ahb_read(&model, ABR_STATUS));
    ahb_write(&model, ABR_ENTROPY, 0);
    ahb_write(&model, ABR_CTRL, 0);

#ifndef PRESI_HAVE_NETLIST
    printf("[INFO]\tgenerated netlist C is not wired yet\n");
#else
    printf("[INFO]\tgenerated netlist C compiled; port binding is next\n");
#endif

    presi_free(&model);

    return 0;
}
