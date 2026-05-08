/*
 * presi_model.h -- shared type definitions for the presi harness.
 *
 * Lifted out of presi.c so presi_state.c can see `struct presi_ports`
 * and `struct presi_model`.  Both TUs define / dereference the same
 * memory layout; nothing else uses these types directly.
 */

#ifndef PRESI_MODEL_H
#define PRESI_MODEL_H

#include <stdint.h>

#include "abr_wrap.sram.h"
#include "presi_sram.h"

#ifdef PRESI_HAVE_NETLIST
#include "abr_wrap.presi_var.h"
#else
#ifndef PRESI_T_DEFINED
#define PRESI_T_DEFINED
typedef uint8_t presi_t;
#define PRESI_0 ((presi_t) 0)
#define PRESI_1 ((presi_t) ~0)
#endif
#endif

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

#endif
