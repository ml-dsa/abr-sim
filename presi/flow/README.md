# presi flow scripts

Small Python tools for the Adams Bridge presilicon simulator prototype.
The architecture mirrors `../xpresi`: checked-in scripts stay small and
inspectable, generated artifacts land in `presi/_build/` (gitignored).

## Pipeline (driven by `presi/Makefile`)

```
adams-bridge SV
    │ vf_to_sv2v.py
    ▼
sv2v.v ──────────────────────────────► gen_yosys.py ──► gates.ys ──► yosys ──► gates.sp / gates.v / gates.json
    │                                                                                │
    │ gen_yosys.py (blackbox-sram)                                                    │
    ▼                                                                                │
blackbox.v ──► extract_sram_meta.py ──► sram.json / sram.h                           │
                                                                                     │
                                                                                     ▼
                                                  spice_to_c.py ──► presi_var.h / presi_var.c
                                                                  / presi_clk_part_NNN.c (32 parts)
                                                                  / presi_clk.h
                                                                  / presi_map.csv
                                                                  / presi_bb.csv
                                                                                     │
                                                                                     ▼
                                  gen_blackbox_wiring.py ──► presi_bb_wiring.h
                                                                                     │
                                                                                     ▼
                                  presi.c + presi_sram.c + 32 part TUs ──► presi-gates
```

## What each script does

- **`vf_to_sv2v.py`** — parses the Verilator file list (`_build/xabr_wrap.vf`,
  inherited from the top-level `Makefile`) and runs `sv2v -D SYNTHESIS -D YOSYS
  --top abr_wrap`. The `--top` flag makes sv2v collapse `abr_top`,
  `abr_ctrl`, and `abr_mem_top` into the `abr_wrap` body because they
  communicate via SystemVerilog interfaces (`abr_mem_if`, `abr_sram_if`); no
  way around this without rewriting the interfaces.

- **`gen_yosys.py`** — emits one of three Yosys scripts:
  - `coarse`: read sv2v + `proc; opt`. Diagnostic snapshot only.
  - `blackbox-sram`: blackbox the ten ABR SRAMs (`abr_1r1w_ram`,
    `abr_1r1w_be_ram`) and emit `abr_wrap.blackbox.{v,json,stat.rpt}`.
    `extract_sram_meta.py` reads this to learn each SRAM instance's
    declared geometry.
  - `gates`: blackbox the ten SRAMs *and* the 14 engine modules
    (`abr_sampler_top`, `ntt_top`, `power2round_top`, …). Then `proc; opt;
    memory -nomap; opt; techmap; simplemap; dfflegalize` — no ABC, no
    BUF/NOT/NAND/NOR rewrite (both blew the 30 GiB box). The output is a
    SPICE deck of Yosys gate primitives (`$_AND_`, `$_OR_`, `$_NOT_`,
    `$_XOR_`, `$_MUX_`, `$_DFF_P_`, `$_DFFSR_PPP_`) plus the blackbox
    instance lines.
  - Three load-bearing details — see comments in the file:
    1. `proc; opt` (full `opt`, not `opt_clean`) is mandatory before
       techmap. sv2v leaves parameter expressions like `$clog2(MLDSA_Q)+1`
       as runtime arithmetic; without folding them, abr_wrap carries
       ~1200 spurious `$mul` cells that techmap then expands to millions
       of gates.
    2. The blackbox happens *before* `hierarchy`. That keeps Yosys fast,
       but pins every SRAM cell at the *default* port widths (DEPTH=64,
       DATA_WIDTH=32) regardless of per-instance overrides — so
       `write_spice` truncates the wider connections (the netlist
       exposes only addr=6 / data=32 to each SRAM). The two known fixes
       (post-hierarchy `blackbox m:*<mod>*` to keep paramod variants, or
       skipping the SRAM blackbox so memory pass infers `$mem_v2`) both
       run Yosys past the 10-minute budget; the comment in `emit_common`
       documents this trade-off.
    3. The Makefile filters per-cell `no (blackbox) module for cell type
       \`$_AND_'` warnings out of the gates log via `grep -v`; otherwise
       the log is ~580 MB of identical noise.

- **`extract_sram_meta.py`** — parses the `blackbox-sram` Verilog netlist
  to recover each SRAM instance's `DEPTH`, `DATA_WIDTH`, `byte_enable`
  flags. Emits both a JSON (`sram.json`) and a C header
  (`abr_wrap.sram.h`) with `presi_sram_desc` entries indexed by
  `PRESI_SRAM_<INST>` macros.

- **`netlist_inventory.py`** — diagnostic. Reads the JSON netlist and
  prints a cell-type histogram + top-port summary into
  `_build/abr_wrap.inventory.txt`. Useful to see where heavy arithmetic
  lives before deciding what to blackbox.

- **`spice_to_c.py`** — translates the gates SPICE deck into ANSI C:
  - `presi_var.h` — self-contained header with `presi_t` typedef, the
    `PRESI_0`/`PRESI_1` macros, the `presi_clk_prev` extern (snapshot
    of the previous step's `clk` so DFFs can detect a true rising
    edge), and one `extern presi_t <name>;` per netlist wire.
  - `presi_var.c` — definitions of every wire (single TU, ~150 MB `.o`).
  - `presi_clk_part_NNN.c` — 32 per-part TUs holding ~135 k cycle-update
    statements each, **in topological order**: combinational
    statements first (each consumer runs after its driver), DFF/DFFSR
    assignments grouped at the end of the cycle.  Tunable via
    `--num-parts` (default 32). The split keeps gcc's working set per
    file at ≈3 GB instead of >12 GB for a monolithic 4.3 M-statement
    function.
  - `presi_clk.h` — block-scope `extern` decls + ordered
    `presi_step_part_NNN()` calls; included from inside the harness step
    function.
  - `presi_map.csv` / `presi_bb.csv` — debugging aids and the per-pin
    blackbox subcircuit registry.
  - Pin-order note: Yosys's gate primitives (`$_NOT_`, `$_AND_`,
    `$_MUX_`, `$_DFFSR_PPP_`, …) come out of `write_spice` with
    "Guessing order of ports" — the `(* blackbox *)` declarations one
    might add to `cmos_cells.v` get pruned by `hierarchy -check` before
    simplemap creates the cells, so the ordering we get is "output
    first, then inputs in reverse insertion order". Empirically
    verified, documented in the script.
  - Three load-bearing semantics, all in `spice_to_c.py`:
    1. **Edge-triggered DFFs.**  Each `$_DFF_P_` and `$_DFFSR_PPP_`
       cell emits as `if ((clk & ~presi_clk_prev & 1)) Q = D;`.  The
       harness sets `presi_clk_prev = clk` at the end of every
       `presi_step_netlist()` call, so the flop only ticks once per
       logical cycle no matter how many step calls happen.  Without
       this, `Q = D` (level-sensitive) ticked on every step call and
       doubled the effective clock rate.
    2. **Topological order.**  `topo_order_comb()` runs Kahn's
       algorithm over the comb subset, with edges from "writer of net
       X" to "reader of net X".  Reads of flop outputs skip the
       constraint -- those wires are stable through a cycle since DFF
       assignments run last.  Single-pass propagation through all
       part files gives consistent post-edge values.
    3. **No cascade-delay temporaries.**  Earlier `Q = delay; delay =
       D;` was needed to make non-edge-triggered DFFs handle cascaded
       flops; with edge-triggered DFFs + topo-sorted comb every D
       value is the correct previous-cycle Q at flop-tick time, so no
       intermediates are needed.

- **`gen_blackbox_wiring.py`** — reads `presi_bb.csv` + `sram.json` +
  `seq_rom.json` and emits `_build/abr_wrap.presi_bb_wiring.h`, the
  body of `presi_sram_tick_all()`.  Three kinds of block:
  - **SRAM**: aggregate we/waddr/wdata/re/raddr (and `wstrobe` for
    be-rams) from extern netlist wires, call `presi_sram_*`,
    distribute rdata back over rdata_o.  Robust to Yosys's
    pin-pruning -- uses the bit counts that actually appear in the
    SPICE rather than sram.json's declared widths.
  - **`abr_seq` blackbox**: walk the 99 pins (clk, en_i, addr_i[10],
    data_o[87]) in SV declaration order, build the address from
    `abr_prog_cntr_nxt`, look up `presi_abr_seq_rom[addr]` and drive
    the 87 `data_o` bits.  When `en_i=0` the cell drives all-zero
    (matches the `else` branch in `rtl/abr_seq.sv`).
  - Other engine blackboxes get a TODO comment so the harness still
    compiles.

- **`extract_abr_seq.py`** — slices the `abr_seq` SystemVerilog module
  out of `_build/abr_wrap.sv2v.v` into a self-contained `.v` file
  (sv2v has already inlined every package localparam, so the slice is
  closed under reference).  Used by the standalone abr_seq build
  target so we can extract the ROM without paying the cost of running
  `proc` on the giant `unique case` while the rest of `abr_wrap` is
  also being elaborated.

- **`extract_seq_rom.py`** — reads the standalone abr_seq Yosys JSON,
  finds its `$mem_v2` cell (post-`memory -nomap`), pulls the cell's
  `INIT` parameter (61 bits per entry after `proc_rom` strips
  always-zero positions), and reassembles the *full* 87-bit
  `data_o_rom` value at each of 1024 addresses by walking the
  `$auto$proc_rom.cc:...` net that Yosys emits next to the cell to
  record which output positions were stripped.  Output: a static
  `presi_abr_seq_rom[1024][3]` table in `abr_wrap.seq_rom.h` plus a
  `seq_rom.json` sibling describing the cell + bit map.

## What's not here

- Engine behavioural models — those go into `presi/presi.c` (or
  separate C files) once we wire each blackbox.  The 14 engine modules
  are listed at the bottom of `presi_bb_wiring.h` as TODO.  Without
  them the controller stalls at the first UOP that needs a sampler /
  NTT / aux acknowledgement.

## Current end-to-end status

`make -C presi -j 4 run-gates` builds and runs `presi-gates`. After 64
reset cycles plus 64 idle cycles, AHB reads of `MLDSA_NAME[0..1]`,
`MLDSA_VERSION[0..1]`, and `MLDSA_STATUS` return the expected hardwired
constants from `abr_params_pkg.sv` and the READY status bit.  Writing
`MLDSA_CTRL = 1` then dispatches the abr_seq FSM to MLDSA_KG_S, the
abr_seq blackbox returns `ABR_UOP_LD_SHAKE256` on the next edge, and
the controller correctly stalls at MLDSA_KG_S waiting for the (still
stubbed) sampler to assert `sampler_busy_o`.  Top-level signal flow
through the netlist + ROM dispatch + engine-handshake interlock are
fully functional.  Real operation execution waits on the engine
behavioural models -- see `../plan.md`.

## Prerequisites

- Yosys ≥ 0.64 (older 0.36 is 5–10× slower on `proc` and times out on
  `abr_wrap`).  Built from source if needed; the upstream
  `chipsalliance/adams-bridge v2.0.3` flow has been smoke-tested
  against 0.64+195.
- sv2v ≥ 0.0.11 (the `--top abr_wrap` interface-collapsing behavior is
  what makes the gate flow fit in memory).
- gcc ≥ 12 with `-O0` — the gate-c part TUs are too large for `-O1+`
  to terminate in reasonable time.
