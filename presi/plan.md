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
- Stage 2 is implemented. `make -C presi netlist-gates` produces `_build/abr_wrap.gates.sp` (≈250 MB, 4.79 M cells) in ≈3 min with ≈7 GB peak RAM on Yosys 0.64+ (older 0.36 was 5–10× slower on `proc` -- a prerequisite bump).  Three changes were required to fit in the 30 GB box and finish in budget:
  - Run `proc; opt` (with full `opt`, not just `opt_clean`) before `techmap`. Without this fold, sv2v leaves parameter expressions like `$clog2(MLDSA_Q)+1` as runtime arithmetic, generating ~1200 spurious `$mul` cells in `abr_wrap` that techmap then expands to millions of gates.
  - Skip ABC and the BUF/NOT/NAND/NOR rewrite. Both ran out of memory on the inlined `abr_wrap`. Instead, `simplemap` + `dfflegalize` produce Yosys's gate primitives (`$_AND_`, `$_OR_`, `$_NOT_`, `$_XOR_`, `$_MUX_`, `$_DFF_P_`, `$_DFFSR_PPP_`), which `spice_to_c.py` translates directly. The presi target needs a correct gate netlist, not optimal area.
  - Three engines blackboxed at their `abr_top`-instantiation boundary: `abr_sampler_top` (SHA3+samplers, too heavy combined with the rest), `ntt_top` (butterfly network, also too heavy combined), and `abr_seq` (1024-way `unique case` stalls `proc`).  These are co-simulated via per-engine flows; see Stage 3 + Where to pick up next.  The other 12 engines (`power2round_top`, `decompose`, `skencode`, `skdecode_top`, `makehint`, `norm_check_top`, `sigencode_z_top`, `pkdecode`, `sigdecode_z_top`, `sigdecode_h`, `compress_top`, `decompress_top`) plus `ntt_twiddle_lookup` are gate-mapped inline in abr_wrap.
- Stage 3 is implemented. `make -C presi gate-c` runs `spice_to_c.py` over the full SPICE in ≈30 s and emits the netlist as a set of small ANSI-C translation units in `_build/`:
  - `abr_wrap.presi_var.h` — tiny self-contained header (~750 B): `presi_t` typedef, `PRESI_0`/`PRESI_1` macros, the array decl `extern presi_t presi_s[PRESI_NETS]`, and the `presi_clk_prev` extern.  No more per-net externs (used to be 154 MB).
  - `abr_wrap.presi_var.c` — single-array allocation (~150 B).
  - `abr_wrap.presi_idx.h` — `#define IDX_<c_name> <idx>` for every named net (200 MB).  Consumers (`presi.c`, generated bb-wiring) include this for named-port access.
  - `abr_wrap.presi_clk_part_NNN.c` — 32 per-part TUs, each holding `void presi_step_part_NNN(void)` with cell-update statements **in topological order**: comb statements first (each consumer runs after its driver), DFF/DFFSR assignments grouped at the end.  Statements are now `presi_s[<idx>] = ...` form -- gcc -O0 compiles each part .c in ~5.7 s (down from 17 s under the old per-net-extern layout); -O1 in ~2 min (was 5+ min).
  - `abr_wrap.presi_clk.h` — block-scope `extern` declarations + ordered `presi_step_part_NNN()` calls; included from inside the harness step function.
  - `abr_wrap.presi_map.csv` — `idx,spice_name,c_name`, the canonical lookup for tools that bake literal indices into generated code.
  - `abr_wrap.presi_bb.csv` — one row per pin of every blackbox subcircuit instance (`instance, module, pin_index, spice_name, c_name, idx`).  15 instances: 8 `abr_1r1w_ram`, 2 `abr_1r1w_be_ram`, 3 engine blackboxes (sampler, ntt, abr_seq), and 2 redundant entries.

  Pin orders for the gate primitives match Yosys's "Guessing order of ports" output (output first, then inputs in reverse insertion order); see the comments in `spice_to_c.py`.  The `(* blackbox *)` declarations one might add to `cmos_cells.v` to avoid the warnings do not work — they get pruned by `hierarchy -check` before simplemap creates the cells.  The Makefile filters those warnings out of the gates log via `grep -v`.
- Stage 4 is implemented.  `make -C presi -j 4 cosim` builds the **co-simulation binary** `_build/presi-gates-cosim` (~480 MB at -O0; clean rebuild ~8-12 min) which links the abr_wrap netlist + standalone gate netlists for `ntt_top` and `abr_sampler_top` plus per-engine glue (`<engine>.glue.c`, generated by `gen_engine_glue.py`).  `make run-cosim` runs the smoke harness; after 64 reset cycles plus 64 idle cycles the harness reads `MLDSA_CORE_NAME[31:0]=0x44534d4c`, `MLDSA_CORE_NAME[63:32]=0x3837412d`, `MLDSA_CORE_VERSION[31:0]=0x302e322e`, `MLDSA_CORE_VERSION[63:32]=0x00003300`, and `STATUS=1` (READY bit set).  Writing `MLDSA_CTRL=1` then walks the abr_seq ROM through real Dilithium-keygen UOPs (`pc=2 LD_SHAKE256 → pc=3 SHAKE256 → pc=4 LFSR → pc=5 SHAKE256 → pc=6 REJB s1[0] → pc=7 REJB s1[1]`) within the 256-cycle smoke poll window; engine handshakes (sampler_busy_o, SHA3 absorb/squeeze, rejection-sampling) all execute through gate-level netlists.  Run cost **~2.2 cyc/s** at -O0 (smoke 384 cy in ~2:50 wall).

  Pieces that landed in this stage:
  - `presi/flow/gen_blackbox_wiring.py` reads `presi_bb.csv` plus `sram.json` plus the abr_seq ROM JSON and generates `_build/abr_wrap.presi_bb_wiring.h`, the body of `presi_sram_tick_all()`.  10 SRAMs and the abr_seq ROM are wired by direct `presi_s[<idx>]` indexing.  Engines are listed in the trailing comment but driven by per-engine glue rather than this header.
  - `presi/flow/gen_engine_glue.py` reads `abr_wrap.presi_bb.csv` + both maps + the engine `gates.v` port list and emits `<engine>.glue.c`: `void <engine>_step_glue(void)` copies abr_wrap bb-pin slots → engine input slots, runs engine step parts, updates engine `presi_clk_prev`, copies engine output slots → abr_wrap.  Literal integer indices baked in from the maps (no idx-header include needed).  Skips bits whose abr_wrap-side or engine-side connection is constant-folded.
  - `abr_seq` is blackboxed at the SV-module boundary (`gen_yosys.py`'s `ENGINE_MODULES`), since `proc` choked on its 1024-way `unique case` for >25 minutes.  ROM contents come from a quick standalone Yosys run on abr_seq alone (extracted from sv2v.v by `flow/extract_abr_seq.py`); `flow/extract_seq_rom.py` walks the `$mem_v2` cell + the `proc_rom` bit-map net to reconstruct the *full* 87-bit `data_o_rom` value at each of the 1024 ROM addresses (Yosys's proc_rom strips 26 always-zero bit positions, leaving 61 bits in the cell INIT).
  - `presi.c`'s `presi_apply_inputs` / `presi_capture_outputs` copy each abr_wrap top-level port bit via `presi_s[IDX_<port>]` lookups (multi-bit ports use small static index arrays built with X-macros + the `IDX_<port>_<bit>` macros from the idx header).
  - `presi_cycle()` is a three-pass cycle.  Phase 0 (clk=0): `step_netlist_comb()` + `engines_step_comb()` settle comb on the falling edge.  Phase 1 (clk=1): `step_netlist_comb()` + `engines_step_comb()` (pre-tick comb), `capture_outputs()` (AHB master sampling -- see below), then `step_netlist_flop()` + `engines_step_flop()` tick all flops on the rising edge.  Settle: `step_netlist_comb()` + `engines_step_comb()` refresh comb post-tick so SRAM-tick samples consistent registered inputs.  `presi_clk_prev` is updated explicitly by the harness between phases so the rising-edge predicate `(clk & ~clk_prev)` fires exactly once per cycle.
  - **AHB output capture happens BEFORE the flop tick**, in phase 1 between `_comb` and `_flop`.  This matches what an AHB master sees on a rising edge: the slave's combinational `hrdata_o` / `hreadyout_o` is computed from values registered on the *previous* edge, just before the about-to-occur tick replaces them.  Capturing post-tick (or post-settle) makes `dv` (cpuif_req) deassert and the abr_reg readback mux gates to '0 -- a hidden bug for several commits before it was caught by NAME / VERSION reads coming back as 0.
  - `presi_engines_step_*()` is called every phase and unconditionally invokes both engine step glues.  Two earlier gating attempts have been reverted (see [busy_o gate trap](../../.claude/projects/-home-mjos-ai-rv-abr-sim/memory/busy_o_gate_trap.md) and [Engine engagement gate dead end](../../.claude/projects/-home-mjos-ai-rv-abr-sim/memory/engine_gate_dead_end.md)).  Both broke because the gate signal lags the controller by one cycle (read pre-flop-tick), so a one-cycle start pulse is missed entirely.  Re-introducing engine gating requires either reading the gate post-flop-tick (one-cycle skew between abr_wrap and engine timing) or gating on a stable-throughout-engagement signal that gives no savings inside keygen.
  - `ahb_read()` and `ahb_write()` use the textbook 1-cycle address + 1-cycle data phase.  `abr_ahb_slv_sif` registers (addr, dv, write) at posedge; abr_reg's readback mux is fully combinational from those, and the AHB-side wdata mux is combinational from `hwdata_i` with the lane chosen by registered `addr[2]`.  `ahb_read()` additionally spins on `hreadyout_o` so external regions (PUBKEY/PRIVKEY) which abr_reg stalls via `external_pending` are handled correctly.
  - Known limitation: **SRAM port-width truncation.**  The pre-hierarchy `blackbox abr_1r1w_ram` keeps every SRAM cell at the *default* port widths (DEPTH=64, DATA_WIDTH=32) regardless of per-instance overrides.  `write_spice` then truncates the wider connections, so the netlist exposes only addr=6 / data=32 to each SRAM (instead of e.g. addr=10 / data=96 for `mem_inst0_bank0`).  The harness's SRAM storage stays at the full declared width so nothing is lost on reads/writes inside the C model, but the netlist itself can only exercise the low 32 data bits and 6 address bits.  Two known-correct fixes (post-hierarchy `blackbox m:*<mod>*` to keep paramod variants, or skipping the SRAM blackbox entirely so memory pass infers `$mem_v2` cells) both push Yosys past the 5-minute build budget; documented in `gen_yosys.py` for follow-up.

  What remains:
  - End-to-end Dilithium keygen byte-compare against Verilator wrapper (Stage 5, `make run-cosim-keygen`; multi-hour wall via snapshot-chained chunks).
  - SRAM port-width truncation fix (known dead end without a different strategy).
  - TVLA toggle hook (now a one-liner with the array layout; see "Where to pick up next" item 9).

## Simulator semantics

`spice_to_c.py` translates a Yosys gates SPICE deck into ANSI C as a
flat list of statements, then distributes them across `--num-parts`
part files (default 32 for abr_wrap, 8 for engines).  Three
load-bearing properties keep the resulting simulator cycle-accurate
without an event-driven scheduler:

1. **State as a flat byte array.**  Each netlist allocates one
   `presi_t <prefix>presi_s[N]` array; every wire is a stable integer
   index.  Cells emit `presi_s[<idx>] = ...`.  Bit-level operations are
   just byte-level operations on the array elements (presi_t is
   `uint8_t`, so logical 0/1 sit in the low bit).

   This replaces the earlier "one extern per net" layout (millions of
   `extern presi_t <name>;` decls), which produced 191 MB var.h files
   and crippled gcc -O1 (>5 min per part .c).  The array layout
   shrinks the cosim binary from 786 MB → 258 MB, makes gcc -O1
   tractable (~2 min per part .c), and -- importantly for TVLA -- per-
   cycle delta + popcount over `presi_s` vs a snapshot is the entire
   toggle-counting kernel:

   ```c
   for (i = 0; i < PRESI_NETS; i++)
       toggles += __builtin_popcount(presi_s[i] ^ presi_s_prev[i]);
   ```

2. **Edge-triggered DFFs.**  Each `$_DFF_P_` and `$_DFFSR_PPP_` cell
   emits as
   ```c
   if ((presi_s[<clk>] & ~presi_clk_prev & 1)) presi_s[<Q>] = presi_s[<D>];
   ```
   (DFFSR adds an `if (S) Q=1; else if (R) Q=0;` prefix for the
   level-sensitive set/reset).  `presi_clk_prev` is a per-netlist
   scalar that the harness snapshots at the end of every
   `presi_step_netlist()` call (and each `<engine>_step_glue()` does
   the same for its engine).  So a DFF only ticks once per logical
   cycle -- the first `clk=1` step after a `clk=0` step.  Multiple
   `step_netlist()` calls inside one phase settle combinational
   without re-clocking the flops.

   Without this, the original "Q = D" emission ticked every flop on
   every step, doubling the effective clock rate.  That bug let
   register reads work (the slave's pipeline was tolerant) but
   corrupted any FSM that depended on edge timing.

3. **Combinational statements ordered by dataflow.**  Every cell's
   emit function returns a `(stmt, lhs, rhs, is_flop)` dict, where
   `lhs`/`rhs` are the cell's input/output references as `presi_s[<i>]`
   strings.  `topo_order_comb` runs Kahn's algorithm over the comb
   subset: edges go from "writer of net X" to "reader of net X", with
   reads of *flop outputs* skipped (those are stable through a cycle
   since DFF assignments run last).  After topo sort, comb statements
   are followed by all DFF/DFFSR statements as a single block at the
   end.

   With this ordering, a single `presi_step_netlist()` call propagates
   every comb signal through every level of logic in one pass, so a
   read after the call sees consistent post-edge values.  Combinational
   cycles in the netlist (rare but possible after opt) trigger a
   warning and the cyclic statements emit in original order; the build
   doesn't fail.

4. **Co-simulation through engine glue.**  In the cosim binary, the
   abr_wrap netlist is one array (`presi_s[]`), each engine has its
   own (`<prefix>presi_s[]`).  For each clock phase the harness:
   - Steps abr_wrap (`presi_step_netlist()`).
   - Calls each engine's `<engine>_step_glue(void)`, which reads
     abr_wrap's bb-pin slots (the wires connecting to the engine's
     input ports), copies them to the engine's input-port slots in
     its own array, runs the engine's step parts, updates the
     engine's `presi_clk_prev`, and copies the engine's output-port
     slots back to abr_wrap's bb-pin slots.

   abr_wrap's main step has already settled this phase before the
   engine runs, so the engine sees the right inputs.  The engine's
   outputs become inputs to abr_wrap's combinational logic on the
   *next* step (one-phase lag).  This matches the registered
   handshake conventions between abr_ctrl and the engines.

## Initial Implementation Order

1. Add `presi/Makefile` and `presi/flow/` skeleton.
2. Generate sv2v and coarse netlist from `abr_wrap`.
3. Create an SRAM-blackbox Yosys mode.
4. Generate a small-cell netlist for non-SRAM logic.
5. Write the first translator and compile a reset-only C simulator.
6. Implement the AHB harness and SRAM models.
7. Run one deterministic AB operation end-to-end.

Items 1–6 are done as of the current commit; the engines + abr_seq ROM
are now wired through co-simulation with per-engine gate netlists, and
the `mldsa-keygen` driver is wired source-side.  Item 7 (running a
deterministic AB operation end-to-end and comparing against Verilator)
is the final remaining item; see "Where to pick up next" below.

## Library + snapshot architecture (2026-05-08)

The cosim flow now builds a static archive `_build/libpresi_gates.a`
that bundles all the gate-netlist .o files (32 abr_wrap + 8 ntt_top
+ 8 abr_sampler_top part files, plus var.o and glue.o per netlist)
together with the cosim flavor of `presi_gates.o` / `presi_state.o` /
`presi_sram.o`.  The `presi-cosim` binary links against the archive:

```
$(LIB_PRESI_GATES) ←  GATES_*.o + NTT_*.o + SAMPLER_*.o + GLUE_*.o
                    + presi_gates.cosim.o + presi_state.cosim.o
                    + presi_sram.o            (~258 MB once built)

presi-cosim       ←  presi.cosim.o + libpresi_gates.a
```

Iterating on the harness CLI (`presi.c`) only triggers one .o
recompile and a relink (~30 s); the archive stays cached.  `make lib`
builds just the archive without producing the binary.

### Snapshot save/load

Full simulator state can be checkpointed to / restored from a binary
file via `presi_state.{c,h}` (in the library):

- bit-packed `presi_s[]` for abr_wrap + each engine
- per-netlist `presi_clk_prev`
- `model.p` (AHB port mirror) + `model.cycle`
- All C-side SRAM contents (raw uint32 arrays)
- Layout-hash header rejects snapshots built against a different
  netlist

After `presi_state_load()`, the harness calls
`presi_settle_after_load()` (one comb-only pass) so any combinational
wires not captured in the saved state become consistent with the
loaded flop / port / SRAM state.  See `state-plan.md` for format
details.

### CLI (mirrors abr_wrap)

`presi-cosim` now uses `src/abr_wrap.cpp`'s flag style:

```
presi-cosim [options] [operation]

operation := smoke (default), mldsa-keygen / keygen,
             run, dump-pk, dump-sk
             (other mldsa-* / mlkem-* names recognised, "not yet
             wired" message)

options   := -t <n>     max cycles (default 200000)
             -seed/-ent/-pk/-sk/-sig/-hash/-rnd/-mu/-strm
             -d/-z/-msg/-ek/-dk/-ct/-ss      (file slots)
             -vcd <fn>  accepted+ignored (compat)
             -load <fn> load snapshot before running
             -save <fn> save snapshot at end
             -init-only stop after CTRL write (snapshot-friendly)
             -no-output skip writing pk/sk output files
```

Examples:

```sh
# Build a "moment of CTRL=KEYGEN" snapshot:
./presi-cosim -seed seed_in.dat -ent ent_in.dat \
              -save kg-init.bin -init-only mldsa-keygen

# Advance a snapshot 1000 cycles:
./presi-cosim -load kg-init.bin -save kg-1k.bin -t 1000 run

# Dump pk from a finished snapshot:
./presi-cosim -load kg-done.bin -pk pk_out.dat dump-pk
```

## Performance evolution (build-time + runtime, folded from former perf-plan.md)

Each row is a cold cosim rebuild (Yosys cached, gate-c regenerated,
all .o recompiled, libpresi_gates.a rebuilt, presi-gates-cosim
linked).  All measured on the dev workstation, gcc 15.2 at -O0.

| Refactor                                     | Heavy chunk | Cold wall | Warnings |
|----------------------------------------------|------------:|----------:|---------:|
| Per-net globals + monolithic part TUs        |    42 MB    |  ~14:00   |   2428   |
| Array layout (`presi_s[]` indexed)           |    42 MB    |   ~12:00  |   2428   |
| One TU per chunk                             |   1.34 MB   |    8:48   |   2428   |
| + Branchless DFF/DFFSR/MUX                   |   1.78 MB   |    9:24   |      0   |
| + Emit-time constant folding (per cell)      |   1.52 MB   |    9:35   |      0   |
| + Chunk-local `_edge` hoist                  |   819 KB    |    8:12   |      0   |
| + Comb/flop dispatcher split                 |   819 KB    |    7:59   |      0   |
| + Edge-1 specialised flops (drop edge mux)   |   434 KB    |    8:48   |      0   |
| + `~x` form (drop `^ PRESI_1`)               |   408 KB    |  ~8:00    |      0   |

Source-size change for a representative DFFSR chunk:
- Per-net globals + non-folded:  `<top>__Q_q = ((PRESI_0) | ((PRESI_0 ^ PRESI_1) & ((<top>__R_q ^ PRESI_1) & ((edge & <top>__D) | ((edge ^ PRESI_1) & <top>__Q_q)))));`
- After all the above:  `s[Q] = (~s[R] & s[D]);` (edge=1 is the only context the chunk runs in, so the `(_edge & D) | (~_edge & Q)` mux folds to `D` and the S/R prefix simplifies on the common `S=PRESI_0` case).

Three small correctness wins fixed alongside the source shrink:
- **AHB output capture moved to PRE-flop-tick** in `presi_cycle()`.  Capturing post-settle made `dv` (cpuif_req) deassert and the abr_reg readback mux returned 0 -- NAME / VERSION reads were silently broken.
- **Snapshot save canonicalises** by calling `presi_settle_after_load()` before writing, and `presi_cycle()` runs (settle, sram_tick, settle) so engine output paste + SRAM rdata_o are fresh at cycle end.  Together these make `kg-1.bin == kg-1again.bin` byte-identical via `tools/snapshot-roundtrip.sh`.
- **Edge-triggered DFFs from the start** (`(clk & ~clk_prev)` predicate, harness-owned `presi_clk_prev`) -- without this, `presi_step_netlist*()` could be called multiple times per phase without re-clocking the flops.

## Open ideas (not yet attempted)

These are sound runtime / build-time wins that the engine engagement
gate (a less-sound win) was a wrong attempt at.  Listed in rough
order of cost-vs-risk:

- **Drop Ubuntu hardening flags** (`-D_FORTIFY_SOURCE=3`,
  `-fstack-protector-strong`, `-fstack-clash-protection`,
  `-fasynchronous-unwind-tables`, `-fzero-init-padding-bits=all`)
  for the gate-c .o compiles.  Estimated 25-40 % wall-time
  reduction on the cold build; these are auto-injected via
  dpkg-buildflags and add nothing to a sandboxed simulator.

- **`-O1` measurement.**  With every chunk capped at ~1 MB and no
  branches in the cell statements, gcc's per-BB passes should be
  tractable now.  Worth re-measuring; expected ~5-10× runtime
  speedup at the cost of 2-3× build wall.  The `GATES_OPT ?= -O0`
  knob is in the Makefile.

- **Constant propagation across cells.**  We constant-fold per
  cell; when a folded cell becomes `s[Y] = PRESI_0` (or PRESI_1),
  downstream cells reading `s[Y]` could fold further.  Requires
  iterating the topo order (each pass a single sweep), but the
  whole flow already does that for ordering.

- **Engine snapshot-and-diff gating.**  Snapshot the engine's
  bb-input bytes at the end of each cycle; on entry to the next
  cycle do a `memcmp(prev, cur, n_input_bytes)` and skip step +
  reuse cached outputs if equal.  Sampler is 114 input bits
  (≈14 bytes), ntt_top 1484 (≈186 bytes) -- sub-µs compare.
  Sound for blocks with no free-running internal state, which
  both engines satisfy (start/done handshake driven).  Doesn't
  have the one-cycle-pulse problem of the engagement gate
  because it's based on actual bit-level change, not control
  semantics.

- **Bytecode VM** as a future direction if compile time is still
  the limiting factor: spice_to_c.py emits a binary opcode table
  per chunk; a ~50-line interpreter loops over it.  Compile time
  drops to seconds (just compile the interpreter once).  Runtime
  per cycle is slower than compiled C but the table can be
  mmap'd, regenerated independently of the binary, and the
  iteration speed for development would be transformed.

- **`abr_seq` as a real engine.**  Currently the abr_seq sequencer
  ROM is extracted by `make seq-rom` and the FSM is blackboxed
  inside abr_wrap; with the comb/flop split's per-engine glue
  pattern, abr_seq could be wired in like ntt_top / abr_sampler_top
  if we ever want fully gate-level controller behavior in the
  cosim binary (not just `abr_wrap` + the two heavy datapath
  engines).

## Where to pick up next

The cosim binary co-simulates abr_wrap + ntt_top + abr_sampler_top as
real gate netlists with engine glue (`<engine>.glue.c`).  Smoke run
of `mldsa-keygen` walks through the first 6 ROM UOPs in 256 cycles
(LD_SHAKE256 → SHAKE256 → LFSR → SHAKE256 → REJB s1[0..1]); see
Stage 4 above for the cycle counts.  Real engine gates running
through real handshakes -- nothing behaviourally modelled.

Build-time architecture (measured 2026-05-08, post-array-layout):

| Configuration                            | Yosys | Cells   | Clean rebuild |
|------------------------------------------|-------|---------|---------------|
| baseline (control plane only, archive)   | 2:30  | 4.14 M  | ~5 min        |
| **+ 12 per-coeff engines + twiddle ROM** | 3:17  | 4.79 M  | 5–8 min       |
| + ntt_top inline (don't do this)         | 4:09  | 6.86 M  | 17 min ✗      |
| + abr_sampler_top inline (don't do this) | >5:00 ✗| –      | –             |
| **standalone ntt_top (per-engine)**      | 1:06  | 2.07 M  | 1m30s         |
| **standalone abr_sampler_top (per-eng)** | 1:32  | 2.06 M  | 1m52s         |
| **`make cosim` (full co-sim build)**     | -     | 9.4 M   | 12m17s        |

Current config: `gen_yosys.py`'s `ENGINE_MODULES` blackboxes
`abr_sampler_top`, `ntt_top`, `abr_seq` *in abr_wrap*.  The other 12
plus `ntt_twiddle_lookup` are gate-mapped inline.  `make ntt-top`
and `make abr-sampler-top` produce the per-engine standalone gate
netlists; `make engine-glue` produces the bridges; `make cosim`
links everything.  Co-simulation gives toggle activity from real
gates everywhere.

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

       **Speedup attempts since:** Two engine-gating attempts have
       been tried and reverted (`busy_o`-gated `presi_engines_step()`
       and `--gate-on-port` per-engine gating in `gen_engine_glue.py`).
       Both broke functional execution because the gate signal lags
       the controller by one cycle (read pre-flop-tick) and missed
       one-cycle start pulses.  See "What remains in performance
       (open ideas)" near the end of this file for sound directions
       that haven't been tried.

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

       Expected run cost: at ~5 cyc/s, full keygen (probably 20-50 K
       cycles) is ~1-3 hours.  Bump `KEYGEN_MAX_CYCLES` if it
       timeouts; hopefully not needed.

   3f. ~~**Array-layout refactor (one byte per net, indexed).**~~
       Done 2026-05-08.  spice_to_c.py used to emit one
       `extern presi_t <name>;` per netlist wire (millions of
       globals; var.h was 191 MB for ntt_top alone, var.c 173 MB).
       That symbol-table size made gcc -O1 unaffordable
       (5+ min/file).  Refactored so each netlist allocates a
       single `presi_t <prefix>presi_s[<N>]` array; cells emit
       `presi_s[<idx>] = ...`.  Indices come from
       `<top>.presi_map.csv` (idx,spice,c_name).  A separate
       `<top>.presi_idx.h` defines `IDX_<c_name>` macros for the
       harness and bb-wiring code.

       Wins:
       - var.h shrank from 191 MB → 800 B, var.c from 173 MB → 200 B.
       - Cosim binary 786 MB → **258 MB** (3x smaller).
       - gcc -O0 part .c: 17s → 5.7s (3x faster).
       - gcc -O1 part .c: 5+ min → 1m57s (now affordable).
       - Per-cycle delta + popcount = trivial XOR over presi_s
         vs a snapshot.  TVLA toggle counting becomes a one-liner.

       The "speedup work" commit had also added `if (!busy_o) return;`
       in `presi_engines_step()`; that gate caused the engines to
       miss the rising edge on the cycle the controller began
       driving sampler inputs (busy_o lags by 1 cycle), stalling
       the FSM at MLDSA_KG_S forever.  Removed in this same commit.

4. **Snapshot-driven workflow (5-minute experiments).**
   `presi_state.{c,h}` lands snapshots; CLI mirrors abr_wrap.
   Workflow that lets each individual run finish under 5 min wall
   even though full keygen is multi-hour:

       # Build "moment of CTRL=KEYGEN" snapshot once (fast: ~30 s):
       ./presi-cosim -seed seed_in.dat -ent ent_in.dat \
                     -save kg-init.bin -init-only mldsa-keygen
       # Advance in 5-min chunks (~1500 cy each):
       ./presi-cosim -load kg-init.bin -save kg-1.bin -t 1500 run
       ./presi-cosim -load kg-1.bin    -save kg-2.bin -t 1500 run
       ...
       # Or compare two snapshots byte-for-byte to confirm round-trip:
       cmp kg-1.bin kg-1again.bin

   Validate end-to-end byte-compare against Verilator (`./abr_wrap
   mldsa-keygen`) once a finished snapshot exists.

5. **Python snapshot writer (skip AHB-init step).**  spice_to_c.py
   exposes `is_flop` per emitted cell; with that we can dump a
   `<top>.flop_idx.csv` listing just the flop indices, plus a
   register-map CSV mapping each AHB byte offset to the relevant
   flop indices.  A Python tool then writes a snapshot for any (op,
   seed, entropy) tuple instantly without any gate-stepping.
   `presi_settle_after_load()` already runs a comb pass post-load,
   so a flop-only snapshot is sufficient.

6. **VCD-driven snapshot for cross-validation.**  Verilator dumps
   source-level RTL signal names like `top0.abr_ctrl_inst.abr_prog_cntr[3]`;
   Yosys flatten preserves these as `top0_abr_ctrl_inst_abr_prog_cntr_3`
   in `presi_idx.h` with consistent mangling (`.` → `_`, `[N]` → `_N`).
   A Python tool can name-mangle VCD signals to IDX entries and write
   a presi snapshot at a given Verilator cycle.  Excellent debugging:
   if the gate netlist diverges from RTL, snapshot at successive
   cycles and bisect to find the divergence point.  Requires
   abr_wrap.cpp to also dump the SRAM contents (Verilator doesn't
   include them in VCD by default).

7. **Stage 5 — end-to-end Dilithium keygen byte-compare.**
   Generate inputs (`python3 flow/mldsa-gen.py keygen <message> <xi>
   <rho'>`), run cosim (~1-3 hour wall at 5 cyc/s), compare
   `pk_out.dat` / `sk_out.dat` byte-for-byte against `./abr_wrap
   mldsa-keygen`'s reference output.  Snapshot chaining (item 4)
   makes this practical: chunk the run into pieces that fit a
   single iteration window, and rejoin them in a final `dump-pk`
   / `dump-sk` from the last snapshot.  Record cycle count.

8. **SRAM port-width truncation.**  The naive fix -- defer
   `blackbox m:*abr_1r1w_ram*` until *after* `hierarchy -check -top`
   so paramod variants are made with per-instance widths -- was tried
   on 2026-05-07 and abandoned.  Yosys ran past 25 min / 17 GB RSS in
   `proc`/`opt` and was killed; the whole gates flow is held to a
   5-minute budget (`GATE_TIMEOUT=300s` in the Makefile).  Cheaper
   alternatives to consider before retrying: `chparam` on the original
   blackbox, manually-named paramod blackboxes in `cmos_cells.v`, or
   skipping the SRAM blackbox entirely and letting `memory -nomap`
   infer `$mem_v2` cells.

9. **TVLA toggle hook.**  Now that state lives in flat byte arrays,
   the per-cycle delta is one `__builtin_popcount(presi_s[i] ^
   presi_s_prev[i])` loop.  Hook this into the harness alongside the
   FSM trace; output should slot into the existing `tvla.py` /
   `readvcd` post-processing pipeline.  The snapshot mechanism makes
   this trivially parallelisable: build N fixed + M random `init`
   snapshots, run each through a `presi-run` invocation, accumulate
   toggle counts.

10. **Stages 6–7.**  Wider trace hooks and leakage instrumentation,
    building on (6) and (9).
