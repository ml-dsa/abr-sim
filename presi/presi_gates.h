/*
 * presi_gates.h -- public API of libpresi_gates.a.
 *
 * The library bundles the gate-level netlist code (millions of cell
 * statements compiled into 32+8+8 .o files) with the cycle-stepping,
 * AHB-driver, and snapshot save/load primitives.  Iterating on a
 * harness binary that links this archive only recompiles the harness
 * .c files -- the slow gate .o's stay cached in the archive.
 *
 * struct presi_model lives in presi_model.h.
 * Snapshot save/load is in presi_state.h.
 */

#ifndef PRESI_GATES_H
#define PRESI_GATES_H

#include <stdint.h>
#include <stddef.h>

#include "presi_model.h"

/* ---- Adams Bridge AHB register map (abr_reg.rdl). */
#define ABR_NAME                0x0000u
#define ABR_VERSION             0x0008u
#define ABR_CTRL                0x0010u
#define ABR_STATUS              0x0014u
#define ABR_ENTROPY             0x0018u

#define MLDSA_SEED              0x0058u
#define MLDSA_PUBKEY            0x1000u
#define MLDSA_PRIVKEY_OUT       0x4000u

#define MLKEM_CTRL              0x9010u
#define MLKEM_STATUS            0x9014u
#define MLKEM_SEED_D            0x9018u
#define MLKEM_SEED_Z            0x9038u
#define MLKEM_DECAPS_KEY        0xa000u
#define MLKEM_ENCAPS_KEY        0xb000u

/* Byte sizes of register regions (matches src/abr_wrap.cpp). */
#define MLDSA_PUBKEY_SZ         2592u
#define MLDSA_PRIVKEY_SZ        4896u
#define MLDSA_SEED_SZ           0x20u
#define ENTROPY_SZ              0x40u

#define MLKEM_SEED_SZ           0x20u
#define MLKEM_EK_SZ             1568u
#define MLKEM_DK_SZ             3168u

/* CTRL command codes (same encoding for both engines: bit 0 = keygen). */
#define CTRL_KEYGEN             1u

/* STATUS bits. */
#define ABR_STATUS_READY        0x00000001u
#define ABR_STATUS_VALID        0x00000002u
#define ABR_STATUS_MLDSA_ERROR  0x00000008u
#define ABR_STATUS_MLKEM_ERROR  0x00000004u

/* AHB-Lite transaction encodings (htrans_i). */
#define AHB_TRANS_IDLE          0u
#define AHB_TRANS_NONSEQ        2u

#define SZ_U32(x) (((x) + 3) / 4)

/* ---- Bit-vector helpers (LSB-first packing into / out of presi_t). */
uint32_t presi_bits_to_u32(const presi_t *v, int n);
void     presi_u32_to_bits(presi_t *v, int n, uint32_t x);
uint64_t presi_bits_to_u64(const presi_t *v, int n);
void     presi_u64_to_bits(presi_t *v, int n, uint64_t x);

/* Read N bits from presi_s[] at the indices in `bits[]`, LSB-first. */
unsigned presi_read_bits(const int *bits, int n);

/* ---- Model lifecycle. */
int  presi_model_init(struct presi_model *m);
void presi_model_free(struct presi_model *m);

/* ---- Cycle stepping.
 * presi_cycle() runs one logical clock edge: clk=0 step + clk=1 step
 * (with engine glues + SRAM tick at the rising edge).  presi_reset()
 * holds rst_b low for `cycles`, then high for `cycles`.  drive_idle()
 * parks the AHB master at hsel=0 / htrans=IDLE. */
void presi_cycle(struct presi_model *m);
void presi_reset(struct presi_model *m, unsigned cycles);
void presi_drive_idle(struct presi_model *m);

/* After loading a snapshot, run one combinational settle pass so any
 * comb wires not captured in the snapshot become consistent with the
 * loaded flop / port / SRAM state.  No flops tick: we leave clk
 * unchanged across the call.  Cheap (~1 step). */
void presi_settle_after_load(struct presi_model *m);

/* ---- AHB transactions. */
void     ahb_write(struct presi_model *m, uint32_t addr, uint32_t data);
uint32_t ahb_read(struct presi_model *m, uint32_t addr);
void     ahb_write_block(struct presi_model *m, uint32_t addr,
                         const uint32_t *data, size_t words);
void     ahb_read_block(struct presi_model *m, uint32_t addr,
                        uint32_t *data, size_t words);

/* Spin presi_cycle until ABR_STATUS satisfies want_mask (or any error
 * bit in error_mask trips, or we hit max_cycles).  Returns final
 * STATUS value, or -1 on timeout.  If verbose != 0, logs each STATUS
 * transition. */
int wait_for_status(struct presi_model *m, uint32_t want_mask,
                    uint32_t error_mask, uint64_t max_cycles, int verbose);

/* ---- Binary file helpers (little-endian uint32 chunks).
 * read_dat zero-pads buf if the file is shorter; if optional != 0,
 * a missing file is silently treated as zero-fill (returns 0). */
size_t read_dat(uint32_t *buf, size_t bufsz, const char *fn, int optional);
size_t write_dat(const uint32_t *buf, size_t bufsz, const char *fn);

/* ---- FSM trace.  When `presi_fsm_trace_enabled` is non-zero,
 * `wait_for_status()` (and any caller that opts in) prints the
 * controller's program-counter and a few status flags on every PC
 * transition.  Output format mirrors `[seq] cyc: NAME +offs` from
 * the Verilator wrapper so logs are diff-able by milestone.
 * Returns 0 on PC unchanged, 1 on transition (and printed). */
extern int presi_fsm_trace_enabled;
int presi_fsm_trace_step(struct presi_model *m, int *prev_pc);

/* ---- High-level orchestration: ML-DSA-87 keygen.
 * Each phase callable independently, so one binary can run only the
 * AHB-init phase (snapshot save), or only the wait phase (snapshot
 * step), or only the readout phase (snapshot dump). */
int mldsa_keygen_init(struct presi_model *m,
                      const char *ent_fn, const char *seed_fn);
int mldsa_keygen_run(struct presi_model *m, uint64_t max_cycles);
int mldsa_keygen_finish(struct presi_model *m,
                        const char *pk_fn, const char *sk_fn);
int mldsa_keygen(struct presi_model *m, uint64_t max_cycles,
                 const char *ent_fn, const char *seed_fn,
                 const char *pk_fn, const char *sk_fn);

/* ---- High-level orchestration: ML-KEM-1024 keygen.  Same shape as
 * mldsa_keygen but with seed_d_in.dat / seed_z_in.dat as inputs and
 * ek_out.dat / dk_out.dat as outputs.  Drives MLKEM_CTRL (0x9010)
 * with bit 0 set to start, then polls MLKEM_STATUS until READY|VALID. */
int mlkem_keygen_init(struct presi_model *m,
                      const char *ent_fn,
                      const char *seed_d_fn, const char *seed_z_fn);
int mlkem_keygen_run(struct presi_model *m, uint64_t max_cycles);
int mlkem_keygen_finish(struct presi_model *m,
                        const char *ek_fn, const char *dk_fn);
int mlkem_keygen(struct presi_model *m, uint64_t max_cycles,
                 const char *ent_fn,
                 const char *seed_d_fn, const char *seed_z_fn,
                 const char *ek_fn, const char *dk_fn);

#endif
