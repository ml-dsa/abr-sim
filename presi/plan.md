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
- Stage 2 is implemented. `make -C presi netlist-gates` produces `_build/abr_wrap.gates.sp` (≈209 MB, 4.14 M cells) in ≈150 s with ≈10 GB peak RAM. Two changes were required to fit in the 30 GB box:
  - Run `proc; opt` (with full `opt`, not just `opt_clean`) before `techmap`. Without this fold, sv2v leaves parameter expressions like `$clog2(MLDSA_Q)+1` as runtime arithmetic, generating ~1200 spurious `$mul` cells in `abr_wrap` that techmap then expands to millions of gates.
  - Skip ABC and the BUF/NOT/NAND/NOR rewrite. Both ran out of memory on the inlined `abr_wrap`. Instead, `simplemap` + `dfflegalize` produce Yosys's gate primitives (`$_AND_`, `$_OR_`, `$_NOT_`, `$_XOR_`, `$_MUX_`, `$_DFF_P_`, `$_DFFSR_PPP_`), which `spice_to_c.py` will translate directly. The presi target needs a correct gate netlist, not optimal area.
  - Engines blackboxed at their `abr_top`-instantiation boundary: `abr_sampler_top`, `ntt_top`, `power2round_top`, `decompose`, `skencode`, `skdecode_top`, `makehint`, `norm_check_top`, `sigencode_z_top`, `pkdecode`, `sigdecode_z_top`, `sigdecode_h`, `compress_top`, `decompress_top`, plus `ntt_twiddle_lookup` (4 × 85-entry ROM). The C harness models these behaviorally; per-engine gate-level builds for leakage analysis of one engine at a time are a separate target.
- Stage 3 is implemented. `make -C presi gate-c` runs `spice_to_c.py` over the full SPICE in ≈30 s and emits the netlist as a set of small ANSI-C translation units in `_build/`:
  - `abr_wrap.presi_var.h` — self-contained header: `presi_t` typedef, `PRESI_0`/`PRESI_1` macros, and one `extern presi_t <name>;` per net.
  - `abr_wrap.presi_var.c` — definitions of every netlist wire (compiled into a single ≈154 MB `.o`).
  - `abr_wrap.presi_clk_part_NNN.c` — 32 per-part TUs, each holding `void presi_step_part_NNN(void)` with ≈135 k statements.  The split keeps gcc's working set per file at ≈3 GB instead of >12 GB for a monolithic 4.3 M-statement function (which still hadn't produced an `.o` after 5 minutes when interrupted).  Adjust via `GATE_C_PARTS` in the Makefile.
  - `abr_wrap.presi_clk.h` — block-scope `extern` declarations + ordered `presi_step_part_NNN()` calls; included from inside the harness step function.
  - `abr_wrap.presi_map.csv` — SPICE→C name map for debugging.
  - `abr_wrap.presi_bb.csv` — one row per pin of every blackbox subcircuit instance (`instance, module, pin_index, spice_name, c_name`).  25 instances: 8 `abr_1r1w_ram`, 2 `abr_1r1w_be_ram`, 14 engines, 1 `_mem_v2` (the abr_seq sequencer ROM).
  Pin orders for the gate primitives match Yosys's "Guessing order of ports" output (output first, then inputs in reverse insertion order); see the comments in `spice_to_c.py`.  The `(* blackbox *)` declarations one might add to `cmos_cells.v` to avoid the warnings do not work — they get pruned by `hierarchy -check` before simplemap creates the cells.  The Makefile filters those warnings out of the gates log via `grep -v`.
- Stage 4 is largely implemented.  `make -C presi -j 4 run-gates` builds the full netlist binary and runs it; the abr_wrap NAME register reads back the expected `0x44534d4c` ("MLSD" = low half of "MLDSA-87"), proving end-to-end signal flow from harness → top-level ports → netlist → register file → AHB slave → harness.
  - `presi/flow/gen_blackbox_wiring.py` reads `presi_bb.csv` plus `sram.json` and generates `_build/abr_wrap.presi_bb_wiring.h`, the body of `presi_sram_tick_all()`.  Each SRAM block samples we/waddr/wdata/re/raddr (and wstrobe for `abr_1r1w_be_ram`) from extern netlist wires, calls the matching `presi_sram_*` helper, and distributes rdata back over the rdata_o bits.  All 10 SRAMs are wired; the 14 engines and the abr_seq sequencer ROM (`_mem_v2`) are listed in the trailing comment as TODO.
  - `presi.c`'s `presi_apply_inputs` / `presi_capture_outputs` copy each abr_wrap top-level port bit between `m->p.*` (32-bit harness representation) and the matching extern `presi_t` (one per netlist bit).  The bit-arrays use a small X-macro list per bus (`haddr_i`, `hwdata_i`, `htrans_i`, `hsize_i`, `hrdata_o`).
  - `presi_cycle()` is a two-step cycle: first `presi_step_netlist()` with `clk = PRESI_0` to settle combinational, then `clk = PRESI_1` for the rising-edge step that advances flops via the generated `_presi_delay_<n>` temporaries.  SRAM tick happens once after the clk=1 step, modelling the synchronous one-cycle read latency of `abr_1r1w_ram`.  A single-step cycle returned all-zero hrdata even after long resets, which is why we keep both halves.
  - Known limitations:
    1. **SRAM port-width truncation.**  The pre-hierarchy `blackbox abr_1r1w_ram` keeps every SRAM cell at the *default* port widths (DEPTH=64, DATA_WIDTH=32) regardless of per-instance overrides.  `write_spice` then truncates the wider connections, so the netlist exposes only addr=6 / data=32 to each SRAM (instead of e.g. addr=10 / data=96 for `mem_inst0_bank0`).  The harness's SRAM storage stays at the full declared width so nothing is lost on reads/writes inside the C model, but the netlist itself can only exercise the low 32 data bits and 6 address bits.  Two known-correct fixes (post-hierarchy `blackbox m:*<mod>*` to keep paramod variants, or skipping the SRAM blackbox entirely so memory pass infers `$mem_v2` cells) both push Yosys past the 10-minute build budget; documented in `gen_yosys.py` for follow-up.
    2. **AHB read latency.**  `ahb_read()` does an address-phase loop until `hreadyout_o` then samples hrdata over up to 8 idle cycles.  Reads at addresses with bit 2 = 0 sometimes return zero where the spec says the slave should drive the value; the read at addr 0 (NAME) and reads with bit 2 = 1 (e.g. STATUS at 0x14 returns the version-string bytes from MLDSA_VERSION) work.  Likely an extra cycle of pipelining inside `abr_ahb_slv_sif`'s registered address path that the harness doesn't yet wait out.
  What remains:
  - Behavioral C models for the 14 engines (start with `abr_sampler_top` and `ntt_top`).  The pin lists are in `presi_bb.csv`.
  - C model for the abr_seq sequencer ROM (`_mem_v2`); pins also in `presi_bb.csv`.
  - Track down the AHB read-latency quirk so all four basic registers (NAME/VERSION/CTRL/STATUS) read deterministically.
  - Fix the SRAM port-width truncation so all bits exercise correctly, ideally without doubling Yosys runtime.

## Initial Implementation Order

1. Add `presi/Makefile` and `presi/flow/` skeleton.
2. Generate sv2v and coarse netlist from `abr_wrap`.
3. Create an SRAM-blackbox Yosys mode.
4. Generate a small-cell netlist for non-SRAM logic.
5. Write the first translator and compile a reset-only C simulator.
6. Implement the AHB harness and SRAM models.
7. Run one deterministic AB operation end-to-end.
