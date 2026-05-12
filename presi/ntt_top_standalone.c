/*
 * ntt_top_standalone.c -- drive the gate-level ntt_top alone (no
 * abr_wrap, no engine glue) and observe ntt_busy / ntt_done cycle by
 * cycle.  Output format mirrors rtl/ntt_top_tb.sv so the two logs
 * can be diff'd line-by-line:
 *
 *   # <cyc> [ntt_gc] ntt_busy=B ntt_done=B ntt_enable=B mode=N
 *
 * This is the smallest possible reproducer for the engine co-sim
 * divergence -- if standalone gates-C diverges from standalone RTL
 * on the same PWA pulse, the bug is in our Yosys-synth / spice_to_c
 * pipeline (NOT in the engine glue / abr_wrap integration).
 */

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "ntt_top.presi_var.h"

extern presi_t ntt_top__presi_s[];
extern presi_t ntt_top__presi_clk_prev;

/* 8 step parts in ntt_top, each split into _comb / _flop. */
#define EACH(X) X(0) X(1) X(2) X(3) X(4) X(5) X(6) X(7)
#define DECL_COMB(i) extern void ntt_top__presi_step_part_00##i##_comb(presi_t *);
#define DECL_FLOP(i) extern void ntt_top__presi_step_part_00##i##_flop(presi_t *);
EACH(DECL_COMB)
EACH(DECL_FLOP)
#undef DECL_COMB
#undef DECL_FLOP

#define CALL_COMB(i) ntt_top__presi_step_part_00##i##_comb(ntt_top__presi_s);
#define CALL_FLOP(i) ntt_top__presi_step_part_00##i##_flop(ntt_top__presi_s);

/* Indices from _build/ntt_top.presi_map.csv (named control bits). */
#define IDX_clk            45687
#define IDX_reset_n        48012
#define IDX_mlkem          48016
#define IDX_zeroize        48023
#define IDX_accumulate     48168
#define IDX_mode_2       1385241
#define IDX_mode_1       1385243
#define IDX_mode_0       1385244
#define IDX_shuffle_en   2035411
#define IDX_masking_en   2035595
#define IDX_sampler_valid 2035762
#define IDX_ntt_enable   2035913
#define IDX_ntt_done     2078473
#define IDX_ntt_busy     2780252

static void step_one_cycle(void)
{
    /* Phase 0: clk=0; comb settles. */
    ntt_top__presi_s[IDX_clk] = PRESI_0;
    EACH(CALL_COMB)
    ntt_top__presi_clk_prev = PRESI_0;

    /* Phase 1: clk=1 PRE-flop; comb settles, then flops tick. */
    ntt_top__presi_s[IDX_clk] = PRESI_1;
    EACH(CALL_COMB)
    EACH(CALL_FLOP)
    ntt_top__presi_clk_prev = PRESI_1;

    /* Settle: comb only (no flops tick because clk_prev==1==clk). */
    EACH(CALL_COMB)
}

static void set_constants(void)
{
    /* Match the SV TB defaults so the comparison is apples-to-apples. */
    ntt_top__presi_s[IDX_mlkem]         = PRESI_1;  /* MLKEM context */
    ntt_top__presi_s[IDX_sampler_valid] = PRESI_1;  /* pretend data valid */
    ntt_top__presi_s[IDX_zeroize]       = PRESI_0;
    ntt_top__presi_s[IDX_accumulate]    = PRESI_0;
    ntt_top__presi_s[IDX_shuffle_en]    = PRESI_0;
    ntt_top__presi_s[IDX_masking_en]    = PRESI_0;
}

int main(int argc, char **argv)
{
    int max_cyc = 150;
    if (argc >= 3 && strcmp(argv[1], "-t") == 0) {
        max_cyc = atoi(argv[2]);
    }

    /* Zero the entire state.  DFFSR cells respond to reset_n=0
     * regardless of initial Q value, so this is sufficient. */
    memset(ntt_top__presi_s, 0, NTT_TOP__PRESI_NETS * sizeof(presi_t));
    ntt_top__presi_clk_prev = PRESI_0;

    set_constants();
    ntt_top__presi_s[IDX_reset_n]    = PRESI_0;
    ntt_top__presi_s[IDX_mode_0]     = PRESI_0;
    ntt_top__presi_s[IDX_mode_1]     = PRESI_0;
    ntt_top__presi_s[IDX_mode_2]     = PRESI_0;
    ntt_top__presi_s[IDX_ntt_enable] = PRESI_0;

    /* Reset: 10 cycles with reset_n=0. */
    for (int i = 0; i < 10; i++) step_one_cycle();

    /* Release reset, then run free.  Issue one PWA pulse at cyc=5
     * to match the SV TB.  Print after each cycle. */
    ntt_top__presi_s[IDX_reset_n] = PRESI_1;
    set_constants();

    for (int cyc = 0; cyc < max_cyc; cyc++) {
        if (cyc == 5) {
            /* mode = pwa = 3'd3 -> bits 1,0 set */
            ntt_top__presi_s[IDX_mode_0]     = PRESI_1;
            ntt_top__presi_s[IDX_mode_1]     = PRESI_1;
            ntt_top__presi_s[IDX_mode_2]     = PRESI_0;
            ntt_top__presi_s[IDX_ntt_enable] = PRESI_1;
        } else if (cyc == 6) {
            ntt_top__presi_s[IDX_ntt_enable] = PRESI_0;
        }

        step_one_cycle();

        unsigned busy   = ntt_top__presi_s[IDX_ntt_busy]   & 1;
        unsigned done   = ntt_top__presi_s[IDX_ntt_done]   & 1;
        unsigned enable = ntt_top__presi_s[IDX_ntt_enable] & 1;
        unsigned m0     = ntt_top__presi_s[IDX_mode_0]     & 1;
        unsigned m1     = ntt_top__presi_s[IDX_mode_1]     & 1;
        unsigned m2     = ntt_top__presi_s[IDX_mode_2]     & 1;
        unsigned mode   = m0 | (m1 << 1) | (m2 << 2);
        printf("# %d [ntt_gc] ntt_busy=%u ntt_done=%u ntt_enable=%u mode=%u\n",
               cyc, busy, done, enable, mode);
    }
    return 0;
}
