# Adams Bridge Presilicon Netlist Simulator Plan

## Goal

Build an `xpresi`-style presilicon simulator for Adams Bridge that can execute at least one complete AB operation end-to-end from a netlist-derived C model. The first milestone is functional execution, not leakage analysis.

The intended architecture is:

- Yosys/sv2v translate Adams Bridge RTL into a restricted structural netlist.
- A Python translator converts that netlist into ANSI C update code.
- A C harness drives `abr_wrap`-equivalent AHB transactions.
- Large AB SRAMs are not synthesized into gates; the C harness models them cycle-by-cycle at the SRAM port boundary.
- Later stages add leakage/toggle collection once functional execution is stable.

## Design Principles

- Keep the coding style close to `../xpresi`: plain Makefile, ANSI C, small Python generators, simple generated headers.
- Keep generated files under `presi/_build` or another ignored build directory.
- Prefer a deterministic, debuggable simulator over an optimized one.
- Treat SRAMs and large ROM/table memories deliberately. Full gate expansion is allowed only as an experiment, not the default path.
- Keep the first operation narrow. A single ML-KEM or ML-DSA operation that can be compared against the existing Verilator wrapper is enough.

## Stage 0: Source Closure And Baseline

Goal: reproduce the currently working Adams Bridge source closure in the `presi` flow.

Tasks:

- Reuse `_build/xabr_wrap.vf` generation from the top-level Makefile.
- Add `presi/Makefile` targets to generate:
  - the sv2v-converted Verilog,
  - a coarse Yosys netlist,
  - logs and statistics.
- Confirm the `sv2v` conversion succeeds for `abr_wrap`.
- Confirm Yosys can elaborate `abr_wrap`.

Exit criteria:

- `make -C presi sv2v` succeeds.
- `make -C presi netlist-coarse` emits a structural Verilog netlist and a Yosys stat report.

## Stage 1: Define The Simulation Boundary

Goal: avoid synthesizing AB SRAMs into gates and expose them to the C harness.

Tasks:

- Identify all SRAM instances reachable through `abr_mem_top`:
  - `w1_mem`
  - `mem_inst0_bank0`
  - `mem_inst0_bank1`
  - `mem_inst1`
  - `mem_inst2`
  - `mem_inst3`
  - `sk_mem_bank0`
  - `sk_mem_bank1`
  - `sig_z_mem`
  - `pk_mem`
- Decide whether to:
  - blackbox `abr_1r1w_ram` and `abr_1r1w_be_ram`, or
  - replace `abr_mem_top` with a presi-specific wrapper exposing flat memory ports.
- Preserve synchronous read semantics:
  - writes occur on clock edge when `we_i` is asserted,
  - reads update `rdata_o` on clock edge when `re_i` is asserted,
  - byte-enable memories update only selected byte lanes.

Exit criteria:

- Generated netlist no longer contains expanded storage arrays for the main AB SRAMs.
- C-visible signal names for each SRAM port are documented.

## Stage 2: Restricted Gate Netlist

Goal: map the non-SRAM logic into a small cell set that the C translator can handle.

Tasks:

- Start with an `xpresi`-like cell library:
  - `BUF`
  - `NOT`
  - `NAND`
  - `NOR`
  - `DFF`
- Add only the minimum required cells if Yosys cannot map cleanly to that set.
- Handle active-low reset explicitly. Options:
  - map reset flops to a reset-capable primitive,
  - or keep reset logic in front of plain `DFF` cells.
- Generate a SPICE or simple structural netlist suitable for translation.

Exit criteria:

- A full AB logic netlist is generated without main SRAM expansion.
- The resulting primitive set is small and explicitly listed.
- The gate netlist generation completes in a practical time budget.

## Stage 3: Python Netlist-To-C Translator

Goal: create generated C headers similar to `xpresi`'s `presi_clk.h` and `presi_var.h`.

Tasks:

- Create a Python translator under `presi/flow/`.
- Support the chosen gate-netlist format.
- Emit:
  - variable declarations,
  - one cycle-update function or included header,
  - optional signal-name metadata for debugging.
- Implement stable name mangling from netlist names to C identifiers.
- Preserve flop update semantics using temporaries where needed.

Exit criteria:

- Generated C compiles with a minimal harness.
- A reset-only simulation can toggle clock for a few cycles without crashing.

## Stage 4: C Harness And AHB Driver

Goal: drive Adams Bridge through the same external bus-level behavior as `src/abr_wrap.cpp`.

Tasks:

- Port only the needed pieces from `src/abr_wrap.cpp` into ANSI C style.
- Implement helpers for:
  - reset sequencing,
  - clock stepping,
  - AHB read/write transactions,
  - polling status,
  - loading input files,
  - saving output files.
- Implement C SRAM models for all blackboxed AB memories.
- Keep the SRAM update ordered consistently with the generated clock update.

Exit criteria:

- The harness can read AB name/version/status registers.
- The harness can perform a simple command and observe `busy_o`, `notif_intr`, or status changes.

## Stage 5: First End-To-End Operation

Goal: complete one AB operation and compare against the existing RTL/Verilator wrapper.

Preferred first candidates:

- ML-KEM keygen or encapsulation if it has shorter runtime and simpler I/O.
- ML-DSA sign only if input/output parity with existing files is more useful.

Tasks:

- Generate or reuse deterministic input `.dat` files.
- Run the same operation with:
  - existing `./abr_wrap`,
  - new `presi` simulator.
- Compare output files byte-for-byte where possible.
- Record cycle count and runtime.

Exit criteria:

- One operation completes end-to-end.
- Output matches the existing wrapper or a known reference.
- Any intentional behavioral differences are documented.

## Stage 6: Debug And Trace Hooks

Goal: make failures diagnosable before adding leakage logic.

Tasks:

- Add optional tracing for:
  - top-level AHB pins,
  - command/status registers,
  - SRAM port activity,
  - `busy_o`, `error_intr`, `notif_intr`,
  - selected FSM/debug signals if they survive name translation.
- Add a small signal lookup/reporting mechanism for generated variables.
- Add cycle limits and fail-fast diagnostics.

Exit criteria:

- A stuck operation reports the last useful bus, status, and SRAM activity.
- Trace output can be enabled without changing generated C.

## Stage 7: Leakage Instrumentation

Goal: add first-order leakage observables after the simulator is functionally useful.

Tasks:

- Add optional per-cycle counters for:
  - toggles,
  - Hamming weight,
  - selected module or signal groups.
- Keep leakage output format compatible with existing `readvcd`/TVLA tooling where practical.
- Add a fixed-vs-random smoke test over a small number of traces.

Exit criteria:

- The simulator can emit a compact leakage trace for one completed operation.
- Trace post-processing can consume that output.

## Known Risks

- Yosys may introduce cells not supported by the first translator. The flow should fail with a clear unsupported-cell list.
- Active-low reset and resettable flops need explicit treatment.
- SRAM read timing must match the RTL exactly, or end-to-end AB operations will diverge.
- Generated signal names may be unstable across Yosys versions; debug hooks should avoid depending on too many internal names.
- sv2v with `--top abr_wrap` inlines `abr_top`, `abr_ctrl`, and `abr_mem_top` into a single flat `abr_wrap` module, because they communicate via SystemVerilog interfaces (`abr_mem_if`, `abr_sram_if`) that sv2v translates by collapsing the connected hierarchy. Per-module blackboxing of those three is therefore not possible without first reworking the interfaces. This is acceptable since they are the control plane we *want* gate-mapped; the engines (NTT, sampler, encoders/decoders) are blackboxed at their RTL boundary.

## Implementation Status

- Stage 0 is implemented: `make -C presi sv2v` and `make -C presi netlist-blackbox` produce the current artifacts. The `netlist-coarse` target exists for diagnostic dumps but is not built by the default flow.
- Stage 1 is implemented for the SRAM list: the ten main AB SRAM instances are preserved as black boxes and emitted as C descriptors in `presi/_build/abr_wrap.sram.h`. Per-port C-name binding waits on Stage 4.
- Stage 2 is implemented. `make -C presi netlist-gates` produces `_build/abr_wrap.gates.sp` (≈209 MB, 4.14 M cells) in ≈3 min with ≈7 GB peak RAM on Yosys 0.64+ (older 0.36 was 5–10× slower on `proc` -- a prerequisite bump).  Three changes were required to fit in the 30 GB box and finish in budget:
  - Run `proc; opt` (with full `opt`, not just `opt_clean`) before `techmap`. Without this fold, sv2v leaves parameter expressions like `$clog2(MLDSA_Q)+1` as runtime arithmetic, generating ~1200 spurious `$mul` cells in `abr_wrap` that techmap then expands to millions of gates.
  - Skip ABC and the BUF/NOT/NAND/NOR rewrite. Both ran out of memory on the inlined `abr_wrap`. Instead, `simplemap` + `dfflegalize` produce Yosys's gate primitives (`$_AND_`, `$_OR_`, `$_NOT_`, `$_XOR_`, `$_MUX_`, `$_DFF_P_`, `$_DFFSR_PPP_`), which `spice_to_c.py` translates directly. The presi target needs a correct gate netlist, not optimal area.
  - Engines + abr_seq blackboxed at their `abr_top`-instantiation boundary: `abr_sampler_top`, `ntt_top`, `power2round_top`, `decompose`, `skencode`, `skdecode_top`, `makehint`, `norm_check_top`, `sigencode_z_top`, `pkdecode`, `sigdecode_z_top`, `sigdecode_h`, `compress_top`, `decompress_top`, `abr_seq`, plus `ntt_twiddle_lookup` (4 × 85-entry ROM). The C harness models these behaviorally; per-engine gate-level builds for leakage analysis of one engine at a time are a separate target.  abr_seq specifically was added because `proc` choked on its 1024-way `unique case` (>25 minutes at 100% CPU); a separate `make seq-rom` target runs Yosys on a sliced abr_seq alone (≈10 s) and `extract_seq_rom.py` reassembles the 87-bit ROM table from the post-`proc_rom` `$mem_v2` cell.
- Stage 3 is implemented. `make -C presi gate-c` runs `spice_to_c.py` over the full SPICE in ≈30 s and emits the netlist as a set of small ANSI-C translation units in `_build/`:
  - `abr_wrap.presi_var.h` — self-contained header: `presi_t` typedef, `PRESI_0`/`PRESI_1` macros, the `presi_clk_prev` extern (see "Simulator semantics" below), and one `extern presi_t <name>;` per net.
  - `abr_wrap.presi_var.c` — definitions of every netlist wire (compiled into a single ≈154 MB `.o`).
  - `abr_wrap.presi_clk_part_NNN.c` — 32 per-part TUs, each holding `void presi_step_part_NNN(void)` with ≈135 k statements **in topological order**: comb statements first, ordered so each consumer runs after its driver; DFF/DFFSR assignments grouped at the end.  The split keeps gcc's working set per file at ≈3 GB instead of >12 GB for a monolithic 4.3 M-statement function (which still hadn't produced an `.o` after 5 minutes when interrupted).  Adjust via `GATE_C_PARTS` in the Makefile.
  - `abr_wrap.presi_clk.h` — block-scope `extern` declarations + ordered `presi_step_part_NNN()` calls; included from inside the harness step function.
  - `abr_wrap.presi_map.csv` — SPICE→C name map for debugging.
  - `abr_wrap.presi_bb.csv` — one row per pin of every blackbox subcircuit instance (`instance, module, pin_index, spice_name, c_name`).  26 instances: 8 `abr_1r1w_ram`, 2 `abr_1r1w_be_ram`, 14 engines, 1 `abr_seq` (sequencer ROM blackbox), 1 `ntt_twiddle_lookup`.
  Pin orders for the gate primitives match Yosys's "Guessing order of ports" output (output first, then inputs in reverse insertion order); see the comments in `spice_to_c.py`.  The `(* blackbox *)` declarations one might add to `cmos_cells.v` to avoid the warnings do not work — they get pruned by `hierarchy -check` before simplemap creates the cells.  The Makefile filters those warnings out of the gates log via `grep -v`.
- Stage 4 is implemented.  `make -C presi -j 4 run-gates` builds the full netlist binary and runs it.  After 64 reset cycles plus 64 idle cycles the harness reads `MLDSA_CORE_NAME[31:0]=0x44534d4c`, `MLDSA_CORE_NAME[63:32]=0x3837412d`, `MLDSA_CORE_VERSION[31:0]=0x302e322e`, `MLDSA_CORE_VERSION[63:32]=0x00003300`, and `STATUS=1` (READY bit set).  Writing `MLDSA_CTRL=1` then triggers `MLDSA_KEYGEN` and the controller correctly stalls at MLDSA_KG_S waiting for the (still-stubbed) sampler to acknowledge the first UOP -- proves end-to-end signal flow from harness → top-level ports → netlist → register file → AHB slave → controller → ROM dispatch → engine handshake.

  Pieces that landed in this stage:
  - `presi/flow/gen_blackbox_wiring.py` reads `presi_bb.csv` plus `sram.json` plus the abr_seq ROM JSON and generates `_build/abr_wrap.presi_bb_wiring.h`, the body of `presi_sram_tick_all()`.  10 SRAMs and the abr_seq ROM are wired; the 14 engines are listed in the trailing comment as TODO.
  - `abr_seq` is now blackboxed at the SV-module boundary (`gen_yosys.py`'s `ENGINE_MODULES`), since `proc` choked on its 1024-way `unique case` for >25 minutes.  ROM contents come from a quick standalone Yosys run on abr_seq alone (extracted from sv2v.v by `flow/extract_abr_seq.py`); `flow/extract_seq_rom.py` walks the `$mem_v2` cell + the `proc_rom` bit-map net to reconstruct the *full* 87-bit `data_o_rom` value at each of the 1024 ROM addresses (Yosys's proc_rom strips 26 always-zero bit positions, leaving 61 bits in the cell INIT).  `presi/tools/decode_seq_rom.py` validates the ROM against a hand-written reference for `MLDSA_KG_S+0..2`.
  - `presi.c`'s `presi_apply_inputs` / `presi_capture_outputs` copy each abr_wrap top-level port bit between `m->p.*` (32-bit harness representation) and the matching extern `presi_t` (one per netlist bit).  The bit-arrays use a small X-macro list per bus (`haddr_i`, `hwdata_i`, `htrans_i`, `hsize_i`, `hrdata_o`).
  - `presi_cycle()` is a two-step cycle: `presi_step_netlist()` once with `clk = PRESI_0` (settles combinational), then once with `clk = PRESI_1` (rising edge -- DFFs capture).  SRAM tick happens once after the clk=1 step, modelling the synchronous one-cycle read latency of `abr_1r1w_ram`.  Edge-triggered DFFs and topological-sort ordering (see "Simulator semantics" below) make a single step per phase sufficient for consistent reads.
  - `ahb_read()` and `ahb_write()` use the textbook 1-cycle address + 1-cycle data phase.  `abr_ahb_slv_sif` registers (addr, dv, write) at posedge; abr_reg's readback mux is fully combinational from those, and the AHB-side wdata mux is combinational from `hwdata_i` with the lane chosen by registered `addr[2]`.  Earlier code held the address phase for an extra cycle as an empirical workaround for the prior level-sensitive DFF emission, which double-clocked every flop; once `spice_to_c` started emitting the rising-edge predicate `(clk & ~presi_clk_prev)` per DFF, the extra cycle was no longer necessary.
  - Known limitation: **SRAM port-width truncation.**  The pre-hierarchy `blackbox abr_1r1w_ram` keeps every SRAM cell at the *default* port widths (DEPTH=64, DATA_WIDTH=32) regardless of per-instance overrides.  `write_spice` then truncates the wider connections, so the netlist exposes only addr=6 / data=32 to each SRAM (instead of e.g. addr=10 / data=96 for `mem_inst0_bank0`).  The harness's SRAM storage stays at the full declared width so nothing is lost on reads/writes inside the C model, but the netlist itself can only exercise the low 32 data bits and 6 address bits.  Two known-correct fixes (post-hierarchy `blackbox m:*<mod>*` to keep paramod variants, or skipping the SRAM blackbox entirely so memory pass infers `$mem_v2` cells) both push Yosys past the 10-minute build budget; documented in `gen_yosys.py` for follow-up.

  What remains:
  - Behavioral C models for the 14 engines (start with `abr_sampler_top` and `ntt_top`).  Without these the controller stalls at the first UOP that needs an engine handshake.  The pin lists are in `presi_bb.csv`.
  - Fix the SRAM port-width truncation so all bits exercise correctly, ideally without doubling Yosys runtime.

## Simulator semantics

`spice_to_c.py` translates a Yosys gates SPICE deck into ANSI C as a flat
list of statements, then distributes them across 32 part files.  Two
load-bearing properties keep the resulting simulator cycle-accurate
without an event-driven scheduler:

1. **Edge-triggered DFFs.**  Each `$_DFF_P_` and `$_DFFSR_PPP_` cell
   emits as
   ```c
   if ((clk & ~presi_clk_prev & 1)) Q = D;
   ```
   (DFFSR adds an `if (S) Q=1; else if (R) Q=0;` prefix for the
   level-sensitive set/reset).  `presi_clk_prev` is a globally-defined
   `presi_t` that the harness snapshots at the end of every
   `presi_step_netlist()` call.  So a DFF only ticks once per logical
   cycle -- the first `clk=1` step after a `clk=0` step.  Multiple
   `step_netlist()` calls inside one phase settle combinational without
   re-clocking the flops.

   Without this, the original "Q = D" emission ticked every flop on
   every step, doubling the effective clock rate.  That bug let
   register reads work (the slave's pipeline was tolerant) but corrupted
   any FSM that depended on edge timing -- abr_prog_cntr would
   "advance" two cycles per harness call.

2. **Combinational statements ordered by dataflow.**  Every cell's
   emit function returns a `(stmt, lhs, rhs, is_flop)` dict.
   `topo_order_comb` runs Kahn's algorithm over the comb subset:
   edges go from "writer of net X" to "reader of net X", with reads of
   *flop outputs* skipped (those are stable through a cycle since DFF
   assignments run last).  After topo sort, comb statements are
   followed by all DFF/DFFSR statements as a single block at the end.

   With this ordering, a single `presi_step_netlist()` call propagates
   every comb signal through every level of logic in one pass, so a
   read after the call sees consistent post-edge values.  Before the
   topo sort, comb signals scattered across the 32 part files were read
   stale (the part files run in fixed order, but Yosys emits cells in
   roughly cell-creation order, not dataflow order), which made
   probes like `top0_abr_ctrl_inst_abr_prog_cntr_nxt_X` and
   `top0_abr_ctrl_inst_abr_ready` show inconsistent values within the
   same cycle.

   Combinational cycles in the netlist (rare but possible after opt)
   trigger a warning and the cyclic statements emit in original order;
   simulation may need extra settle passes for those signals, but the
   build doesn't fail.

3. **No cascade-delay temporaries.**  The earlier
   `Q = delay; delay = D;` pipeline trick existed only to make
   non-edge-triggered DFFs handle cascaded flops correctly.  With
   edge-triggered DFFs + dataflow ordering, every D input is the
   correct combinational of the previous-cycle Q values when its
   flop ticks, so no temporaries are needed.

## Initial Implementation Order

1. Add `presi/Makefile` and `presi/flow/` skeleton.
2. Generate sv2v and coarse netlist from `abr_wrap`.
3. Create an SRAM-blackbox Yosys mode.
4. Generate a small-cell netlist for non-SRAM logic.
5. Write the first translator and compile a reset-only C simulator.
6. Implement the AHB harness and SRAM models.
7. Run one deterministic AB operation end-to-end.

Items 1–6 are done as of the current commit; item 7 is the Stage 5 goal
and is gated on the engine + abr_seq ROM models below.

## Where to pick up next

The harness dispatches a UOP from the abr_seq ROM and stalls at
MLDSA_KG_S waiting for the sampler.  Behavioural engine models would
defeat the purpose of *presilicon* leakage testing — the project's
goal is real-gate toggle traces of the cryptographic engines.  The
correct architecture is therefore "gate-map as much as fits in the
build budget; per-engine gate flows for what doesn't."  Measured
2026-05-07:

| Configuration                            | Yosys | Cells   | Clean rebuild |
|------------------------------------------|-------|---------|---------------|
| baseline (control plane only)            | 2:30  | 4.14 M  | ~5 min        |
| **+ 12 per-coeff engines + twiddle ROM** | 3:17  | 4.79 M  | 5–8 min       |
| + ntt_top                                | 4:09  | 6.86 M  | 17 min ✗      |
| + abr_sampler_top                        | >5:00 ✗| –      | –             |

Current config (the second row) is what `gen_yosys.py` ships:
`ntt_twiddle_lookup`, `power2round_top`, `decompose`, `skencode`,
`skdecode_top`, `makehint`, `norm_check_top`, `sigencode_z_top`,
`pkdecode`, `sigdecode_z_top`, `sigdecode_h`, `compress_top`,
`decompress_top` are all real gates.  Only `abr_sampler_top`,
`ntt_top`, and `abr_seq` are still blackboxed.

Priorities, in rough order:

1. **Per-engine gate flow for `ntt_top`.**  Yosys + `spice_to_c.py`
   pipeline implemented 2026-05-07.  `make ntt-top` produces:
   - `_build/ntt_top.gates.{sp,v,json,stat.rpt}` — Yosys gates output
     (2.07 M cells, 24 ports / 1934 port bits, 242 MB SP, ~1m06s).
   - `_build/ntt_top.presi_var.{h,c}` — extern decls + definitions
     (one `presi_t` per netlist bit, ~2.78 M lines).
   - `_build/ntt_top.presi_clk.h` + 8× `ntt_top.presi_clk_part_NNN.c`
     — topo-sorted cycle update body, ~340 k statements per part.
   - `_build/ntt_top.presi_{bb,map}.csv` — pin and name maps (bb.csv
     is empty since engine-gates mode does no blackboxing).
   Total clean rebuild: ~1m30s, 24s incremental.  Implementation: new
   `engine-gates` mode in `gen_yosys.py` (skips SRAM/engine blackbox
   lists, otherwise identical to `gates` mode).
   **Still needed:** a per-engine C harness that drives ntt_top's
   input ports cycle-by-cycle (test-vector load + start-pulse +
   memory-IF model + cycle/toggle observation).  ntt_top's interface
   is the 24-port set declared in `ntt_top.sv`; the harness needs to
   model `mem_rd_data` / `pwm_a_rd_data` / `pwm_b_rd_data` (an SRAM
   stand-in) and capture `mem_wr_req` + `ntt_busy` + `ntt_done`.

2. **Per-engine gate flow for `abr_sampler_top`.**  Yosys + spice_to_c
   pipeline implemented 2026-05-07 — *splitting was unnecessary*.
   Standalone, the whole abr_sampler_top hierarchy (SHA3 + Keccak +
   rejection / bounded / SIB / CBD samplers + sib_mem) gate-maps to
   only **2.06 M cells** in **1m52s** end-to-end (Yosys 1m32s +
   spice_to_c ~20s).  124 MB SPICE, 8 part .c files (~17 MB each),
   135 K nets reference SHA3/Keccak/sib_mem -- the full SHA3 logic is
   present in the netlist.  The reason this engine timed out in the
   *unified* abr_wrap flow was the combined 7 M cell count slowing
   `proc`/`opt`, not the engine's own size.  Standalone build is
   well under the 5-min budget.
   **Still needed:** per-engine C harness for cycle-driving + toggle
   capture (same shape as the ntt_top harness item above).

3. **End-to-end Dilithium keygen via co-simulated per-engine
   netlists.**  The unified abr_wrap flow stops fitting the budget
   above ~5 M cells, so the heavy engines (`abr_sampler_top`,
   `ntt_top`, plus an `abr_seq` core to be added) stay blackboxed
   *in* abr_wrap but get co-simulated against their own gate
   netlists.  Each cycle, the harness reads bb pin values from
   abr_wrap, writes them onto the engine's standalone netlist input
   ports, steps the engine one cycle, and writes the engine's outputs
   back onto abr_wrap's bb pins.  Toggle activity comes out of all
   netlists -- nothing is behaviourally modelled.

   Concrete sub-steps (in order):

   3a. ~~**Symbol-prefix in `spice_to_c.py`.**~~ Done 2026-05-07.
       New `--symbol-prefix=<str>` flag prepends to every emitted
       C identifier (net externs, `presi_clk_prev`, and the per-part
       `presi_step_part_NNN` functions).  `presi_t`/`PRESI_0`/`PRESI_1`
       are now under a `PRESI_T_DEFINED` guard so multiple netlist
       headers can be included by the same TU.  The Makefile per-engine
       targets pass `--symbol-prefix='<top>__'`; abr_wrap stays
       prefix-less.  Verified backward-compat (clean abr_wrap rebuild
       passes NAME/VERSION/STATUS reads) and that `gcc -c` on a
       prefixed part .c produces an .o whose nm output has only
       prefixed externs (no `clk`, `reset_n`, etc. at file scope).

   3b. ~~**Per-engine `<engine>.glue.c` generator.**~~ Done 2026-05-07.
       New `presi/flow/gen_engine_glue.py` reads:
       - `abr_wrap.presi_bb.csv` (one row per blackbox cell pin)
       - `<engine>.gates.v` (engine's port directions/widths)
       and emits a `<engine>.glue.c` containing extern decls for both
       sides plus `void <engine>_step_glue(void)` that:
         - copies abr_wrap → engine inputs (pin_index N → port-bit N)
         - calls `<prefix>presi_step_part_NNN()` for each part
         - updates `<prefix>presi_clk_prev = <prefix>clk`
         - copies engine outputs → abr_wrap
       Skips writes to outputs whose abr_wrap-side pin is `PRESI_0` /
       `PRESI_1` (Yosys constant-folded the unused fanout, e.g.
       ntt_top's `ntt_done` is unused by abr_ctrl).
       Auto-detects the abr_wrap bb instance when there's only one
       per module.  Verified for ntt_top (1934 bits, 1484 in / 450 out
       / 1 unused) and abr_sampler_top (2021 bits, 114 in / 1907 out /
       0 unused — sampler_state_data_o alone is 1600 bits).
       New `make engine-glue` target produces both .glue.c files in
       seconds; pattern rules build .glue.o.  Each compiles to a
       ~300 KB .o whose only T-symbol is `<engine>_step_glue`.

   3c. ~~**`abr_seq` controller per-engine flow.**~~ Investigated
       2026-05-07 -- structurally a no-op.  abr_seq has no FSM around
       its ROM; the entire module *is* the 1024-deep instruction
       table.  The sequencer FSM that *uses* the ROM lives in
       `abr_ctrl`, which is already gate-mapped in the abr_wrap
       unified flow.  Yosys on abr_seq alone produces 1 NOT + 1
       `$mem_v2` cell in 12 s and the ROM contents are already wired
       via `make seq-rom` + `gen_blackbox_wiring.py`.  A
       `make abr-seq-core` target was added for completeness (runs
       the same engine-gates pipeline so the artifact shape matches
       the other per-engine flows), but it is not load-bearing for
       keygen end-to-end.

   3d. ~~**Build-system wiring.**~~ Done 2026-05-07.  `make cosim`
       builds a new binary `_build/presi-gates-cosim` (~786 MB)
       linking abr_wrap's no-prefix flow + ntt_top's prefixed flow +
       abr_sampler_top's prefixed flow + the two `<engine>.glue.o`
       files.  `presi.c` calls `presi_engines_step()` after each
       abr_wrap `presi_step_netlist()`; that dispatches to
       `ntt_top_step_glue()` and `abr_sampler_top_step_glue()`.
       Compiled with `-DPRESI_HAVE_ENGINE_NETLISTS` so the no-engine
       `make run-gates` build keeps working.  Also added
       `parse_engine_externs()` to `gen_engine_glue.py` to filter out
       port bits that Yosys's standalone engine flow optimized away
       (no fanout inside the engine), preventing link-time undefined
       references for those bits.

       **Validation:** with both engines wired, the controller walks
       past MLDSA_KG_S into real Dilithium keygen UOPs:
         pc=2 (LD_SHAKE256 entropy)  -> 13 cy
         pc=3 (SHAKE256 squeeze)     -> 28 cy
         pc=4 (LFSR, gate-mapped)    -> 2 cy
         pc=5 (SHAKE256 of seed)     -> 41 cy
         pc=6 (REJB s1[0])           -> 107 cy
         pc=7 (REJB s1[1])           -> 57 cy
       Real engine handshakes (sampler_busy_o up/down, SHA3
       absorb/squeeze cycles, rejection-bounded sampling) all working
       through gate-level netlists.

       **Run cost:** ~1m17s wall for 384 cycles total (64+64 reset +
       256 poll) -- about 5 cyc/s.  Cost comes from stepping 3
       netlists x 2 phases per logical cycle vs. the original 1
       netlist x 2 phases.

       **Speedups added (in source, awaiting next build):**
       - `presi_engines_step()` early-returns when abr_wrap's
         top-level `busy_o == 0`, skipping both engine glues during
         reset (128 cy) and any post-completion idle.
       - Per-engine *internal* gates inside each `<engine>_step_glue`:
         skip the input copy + step + output copy when no gating
         signal is asserted.  Wired via new `gen_engine_glue.py
         --gate-on-port` flag (Makefile passes `ntt_enable` +
         `ntt_busy` for ntt_top, `sampler_start_i` + `sampler_busy_o`
         + `sha3_start_i` + `msg_valid_i` for abr_sampler_top).
         During keygen, the controller engages exactly one engine at
         a time, so each engine's gate misses ~half the active
         cycles -- net ~25 % wall-time savings on top of the busy_o
         gate.

       **Compile-time speedup investigation (gcc and clang -O1 both
       blow far past the 5-min budget at >5 min per part .c):** see
       `~/.claude/.../presi_build_env.md` for the full table.  -Og
       is the only middle-ground (48 s/file, ~10 min full build);
       added a `GATES_OPT ?= -O0` knob with a comment table.

   3e. **Drive Dilithium keygen.**  Source-side wired 2026-05-07
       (awaiting next rebuild for measurement).  Added to `presi.c`:
       - `read_dat()` / `write_dat()` helpers.
       - `ahb_write_block()` / `ahb_read_block()` for AHB sequences.
       - `wait_for_status()` polling helper that reads ABR_STATUS
         every ~32 cycles, prints transitions, and breaks on
         `READY|VALID` or error.
       - `mldsa_keygen()` driving the full sequence: load
         `ent_in.dat` (64 B) + `seed_in.dat` (32 B), write
         `MLDSA_CTRL = KEYGEN`, poll, then read `MLDSA_PUBKEY`
         (2592 B) and `MLDSA_PRIVKEY_OUT` (4896 B), dump to
         `pk_out.dat` / `sk_out.dat`.
       - `ahb_read()` now spins on `hreadyout_o` so external
         regions (PUBKEY / PRIVKEY) which abr_reg stalls via
         `external_pending` are handled correctly.
       - `main()` dispatches on `argv[1] = "keygen"` /
         `"mldsa-keygen"`, with `argv[2]` as max-cycles cap
         (default 200000).
       - `make run-cosim-keygen KEYGEN_MAX_CYCLES=...`.

       To run end-to-end:
         1. `python3 flow/mldsa-gen.py keygen <message> <xi> <rho'>`
            to produce `seed_in.dat` + `rnd_in.dat` (the harness
            reads `seed_in.dat` and treats `ent_in.dat` as
            optional, defaulting to all-zero entropy).
         2. `make -C presi cosim` (clean rebuild ~10-15 min).
         3. `cd presi && ./_build/presi-gates-cosim keygen 200000`
            (or `make run-cosim-keygen`).
         4. Compare `pk_out.dat` / `sk_out.dat` against a reference
            generated by `./abr_wrap mldsa-keygen` from the project
            root.

       Expected run cost: at ~3-5 cyc/s with the new gating, full
       keygen (probably 20-50 K cycles) is ~1-3 hours.  Bump
       `KEYGEN_MAX_CYCLES` if it timeouts; hopefully not needed.

4. **Stage 5 — first end-to-end operation comparison.**  Once items
   1–2 are in, run mldsa-keygen through the harness, compare
   `_out.dat` byte-for-byte against the existing `./abr_wrap`
   Verilator wrapper, record cycle count + runtime.

5. **SRAM port-width truncation** (still open from earlier session).
   Naive post-hierarchy blackbox approach was tried and abandoned
   2026-05-07; see comments in `gen_yosys.py` and
   `~/.claude/.../sram_truncation_dead_end.md`.

6. **Stages 6–7.**  Trace hooks and leakage instrumentation, after
   end-to-end works.

3. **SRAM port-width truncation.**  The naive fix -- defer
   `blackbox m:*abr_1r1w_ram*` until *after* `hierarchy -check -top`
   so paramod variants are made with per-instance widths -- was tried
   on 2026-05-07 and abandoned.  Yosys ran past 25 min / 17 GB RSS in
   `proc`/`opt` and was killed; the whole gates flow is held to a
   5-minute budget (`GATE_TIMEOUT=300s` in the Makefile).  Cheaper
   alternatives to consider before retrying: `chparam` on the original
   blackbox, manually-named paramod blackboxes in `cmos_cells.v`, or
   skipping the SRAM blackbox entirely and letting `memory -nomap`
   infer `$mem_v2` cells (also previously slow but might be retestable
   on Yosys 0.64+).

4. **Stage 5 — first end-to-end operation.**  Once items 1–2 are in,
   run `mldsa-keygen` (or the smaller `mlkem-keygen`) through the
   harness, compare `_out.dat` files byte-for-byte against the existing
   `./abr_wrap` Verilator wrapper, record cycle count + runtime.

5. **Stages 6–7.**  Trace hooks and leakage instrumentation, after
   Stage 5 lands.
