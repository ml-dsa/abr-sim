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
    `PRESI_0`/`PRESI_1` macros, and one `extern presi_t <name>;` per
    netlist wire.
  - `presi_var.c` — definitions of every wire (single TU, ~150 MB `.o`).
  - `presi_clk_part_NNN.c` — 32 per-part TUs holding ~135 k cycle-update
    statements each; tunable via `--num-parts` (default 32). The split
    keeps gcc's working set per file at ≈3 GB instead of >12 GB for a
    monolithic 4.3 M-statement function.
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

- **`gen_blackbox_wiring.py`** — reads `presi_bb.csv` + `sram.json` and
  emits `_build/abr_wrap.presi_bb_wiring.h`, the body of
  `presi_sram_tick_all()`. Per SRAM block: aggregate
  we/waddr/wdata/re/raddr (and `wstrobe` for be-rams) from extern
  netlist wires, call `presi_sram_*`, distribute rdata back over
  rdata_o. Robust to Yosys's pin-pruning — uses the bit counts that
  actually appear in the SPICE rather than sram.json's declared
  widths. Engine and `_mem_v2` blackboxes get a TODO comment so the
  harness still compiles.

## What's not here

- Engine/ROM behavioural models — those go into `presi/presi.c` (or
  separate C files) once we wire each blackbox. The 14 engine modules
  and the abr_seq sequencer ROM (`_mem_v2`) are listed at the bottom of
  `presi_bb_wiring.h` as TODO.

## Current end-to-end status

`make -C presi -j 4 run-gates` builds and runs `presi-gates`. After 64
reset cycles plus 64 idle cycles, AHB reads of `MLDSA_NAME[0..1]`,
`MLDSA_VERSION[0..1]`, and `MLDSA_STATUS` return the expected hardwired
constants from `abr_params_pkg.sv` and the READY status bit — the
top-level signal path through the netlist is fully functional. No
operations execute yet because the engines and the abr_seq ROM are
still stubbed; that's the next batch of blockers (see `../plan.md`).
