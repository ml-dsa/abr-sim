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
- Stage 3 is implemented. `make -C presi gate-c` runs `spice_to_c.py` over the full SPICE in ≈30 s and emits four files into `_build/`:
  - `abr_wrap.presi_var.h` (≈125 MB) — `static presi_t <name>;` declaration per net.
  - `abr_wrap.presi_clk.h` (≈202 MB) — one cycle update body, ≈4.3 M C statements.
  - `abr_wrap.presi_map.csv` (≈100 MB) — SPICE→C name mapping for debugging.
  - `abr_wrap.presi_bb.csv` (≈800 KB) — blackbox subcircuit instances and their pin connections, one row per pin (`instance, module, pin_index, spice_name, c_name`).  25 instances total: 8 `abr_1r1w_ram`, 2 `abr_1r1w_be_ram`, 14 engines (one each), and 1 `_mem_v2` (the abr_seq sequencer ROM).
  Pin orders for the gate primitives match Yosys's "Guessing order of ports" output (output first, then inputs in reverse insertion order); see the comments in `spice_to_c.py`.  The `(* blackbox *)` declarations one might add to `cmos_cells.v` to avoid the warnings do not work — they get pruned by `hierarchy -check` before simplemap creates the cells.  The Makefile filters those warnings out of the gates log via `grep -v`.
- Stage 4 is started: `presi/presi.c` has reset/cycle/AHB driver scaffolding and C SRAM allocation; what remains is wiring `abr_wrap.presi_var.h`/`.presi_clk.h` into the harness, mapping the C-name handles in `presi_bb.csv` to the SRAM and engine models, and confirming the C compiles (4.3 M statements is well past gcc's comfortable scale, so a SoT compile experiment is the next blocker).

## Initial Implementation Order

1. Add `presi/Makefile` and `presi/flow/` skeleton.
2. Generate sv2v and coarse netlist from `abr_wrap`.
3. Create an SRAM-blackbox Yosys mode.
4. Generate a small-cell netlist for non-SRAM logic.
5. Write the first translator and compile a reset-only C simulator.
6. Implement the AHB harness and SRAM models.
7. Run one deterministic AB operation end-to-end.
