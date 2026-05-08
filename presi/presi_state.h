/*
 * presi_state.h -- snapshot save/load for the presi simulator.
 *
 * Captures the full visible state of the simulator: every linked
 * netlist's flat `presi_s[]` array, the per-netlist `presi_clk_prev`
 * scalars, the model.p AHB port mirror, model.cycle, and the C-side
 * SRAM contents.  Restoring this state is sufficient to resume any
 * cycle-accurate simulation from the snapshot.
 *
 * File format and rationale: see presi/state-plan.md.
 *
 * The save/load functions are stubs (returning -1) when built without
 * PRESI_HAVE_NETLIST so the smaller `presi` binary still links.
 */

#ifndef PRESI_STATE_H
#define PRESI_STATE_H

#include <stdio.h>

struct presi_model;

/*
 * Save the simulator state to `fp`.  Returns 0 on success, -1 on I/O
 * error or unsupported build (no netlist linked).  `fp` is left open
 * (caller closes).
 */
int presi_state_save(FILE *fp, const struct presi_model *m);

/*
 * Load simulator state from `fp` into `m`.  The model must already
 * be initialised (presi_init called, SRAMs allocated).  Returns 0 on
 * success, -1 on I/O error / format / hash mismatch / unsupported
 * build.  On error, `m`'s state is undefined and should be discarded.
 */
int presi_state_load(FILE *fp, struct presi_model *m);

#endif
