# presi flow scripts

Small Python tools for the Adams Bridge presilicon simulator.  The
architecture mirrors `../xpresi`: checked-in scripts stay small and
inspectable, generated artifacts land in `presi/_build/` (gitignored).

## Pipeline (driven by `presi/Makefile`)

```
adams-bridge SV
    │ vf_to_sv2v.py
    ▼
sv2v.v ─┬───► gen_yosys.py (mode=blackbox-sram) ──► yosys ──► blackbox.{v,json}
        │                                                       │
        │                                                       ▼
        │                                          extract_sram_meta.py ──► sram.json / sram.h
        │
        ├───► gen_yosys.py (mode=gates, --top abr_wrap) ──► yosys ──► gates.{sp,v,json}
        │                                                              │
        │                                                              ▼
        │                                            spice_to_c.py ──► presi_var.{h,c}
        │                                                              presi_idx.h
        │                                                              presi_clk.h
        │                                                              presi_clk_part_NNN.c (32 parts)
        │                                                              presi_map.csv
        │                                                              presi_bb.csv
        │                                                              │
        │                                                              ▼
        │                                       gen_blackbox_wiring.py ──► presi_bb_wiring.h
        │
        ├───► gen_yosys.py (mode=engine-gates, --top ntt_top)         ──► ntt_top.gates.* + spice_to_c
        ├───► gen_yosys.py (mode=engine-gates, --top abr_sampler_top) ──► abr_sampler_top.gates.* + spice_to_c
        ├───► gen_yosys.py (mode=engine-gates, --top abr_seq)         ──► abr_seq.gates.*    + spice_to_c
        │                                                              │
        │                                                              ▼
        │                                       gen_engine_glue.py ──► <engine>.glue.c
        │
        └───► extract_abr_seq.py ──► abr_seq.standalone.v ──► yosys ──► abr_seq.standalone.json
                                                                        │
                                                                        ▼
                                                       extract_seq_rom.py ──► seq_rom.h + seq_rom.json

      presi.c + presi_sram.c + abr_wrap parts                                  ──► presi-gates
      (above) + ntt_top + abr_sampler_top parts + glue.o                       ──► presi-gates-cosim
```

## Architectural overview: array-layout C model

After 2026-05-08 the netlist is no longer represented as millions of
named C globals.  Each netlist allocates **one flat byte array**:

```c
extern presi_t presi_s[5123909];                    /* abr_wrap */
extern presi_t ntt_top__presi_s[2780254];           /* ntt_top  */
extern presi_t abr_sampler_top__presi_s[2527743];   /* sampler  */
```

Every netlist wire is one byte at a stable integer index; cell
statements are `presi_s[<idx>] = presi_s[<a>] op presi_s[<b>];`.  This
collapses the gcc symbol table from millions of entries to a handful
and makes per-cycle TVLA toggle counting trivial:

```c
for (i = 0; i < N; i++) toggles += __builtin_popcount(s[i] ^ s_prev[i]);
```

Indices are recorded in `<top>.presi_map.csv` (idx, spice_name,
c_name).  Named access from C uses the macros in `<top>.presi_idx.h`,
e.g. `presi_s[IDX_clk]` or `presi_s[IDX_top0_abr_ctrl_inst_busy_o]`.

Effects on the build:

| metric                           | named globals | array layout |
|----------------------------------|--------------:|-------------:|
| ntt_top.presi_var.h size         | 191 MB        | 800 B        |
| presi-gates-cosim binary size    | 786 MB        | 258 MB       |
| gcc -O0 per part .c              | 17 s          | 5.7 s        |
| gcc -O1 per part .c              | ~5 min        | 1m57s        |
| clean cosim rebuild              | 13m31s        | 12m17s       |

The engine `step_glue` files reference both arrays directly, e.g.
`ntt_top__presi_s[594] = presi_s[7509];` -- no `extern` walls,
no idx-header indirection.

## What each script does

- **`vf_to_sv2v.py`** — parses the Verilator file list
  (`_build/xabr_wrap.vf`, inherited from the top-level `Makefile`) and
  runs `sv2v -D SYNTHESIS -D YOSYS --top abr_wrap`.  The `--top` flag
  makes sv2v collapse `abr_top`, `abr_ctrl`, and `abr_mem_top` into the
  `abr_wrap` body because they communicate via SystemVerilog interfaces
  (`abr_mem_if`, `abr_sram_if`); no way around this without rewriting
  the interfaces.

- **`gen_yosys.py`** — emits one of four Yosys scripts:
  - `coarse`: read sv2v + `proc; opt`. Diagnostic snapshot only.
  - `blackbox-sram`: blackbox the ten ABR SRAMs (`abr_1r1w_ram`,
    `abr_1r1w_be_ram`) and emit `abr_wrap.blackbox.{v,json,stat.rpt}`.
    `extract_sram_meta.py` reads this to learn each SRAM instance's
    declared geometry.
  - `gates` (used with `--top abr_wrap`): blackbox the ten SRAMs *and*
    three remaining engines (`abr_sampler_top`, `ntt_top`, `abr_seq`).
    The 13 smaller engines (`power2round_top`, `decompose`, `skencode`,
    `skdecode_top`, `makehint`, `norm_check_top`, `sigencode_z_top`,
    `pkdecode`, `sigdecode_z_top`, `sigdecode_h`, `compress_top`,
    `decompress_top`, `ntt_twiddle_lookup`) gate-map directly inside
    abr_wrap.  Pipeline: `proc; opt; memory -nomap; opt; techmap;
    simplemap; dfflegalize` -- no ABC, no BUF/NOT/NAND/NOR rewrite
    (both blew the 30 GiB box).  The output is a SPICE deck of Yosys
    gate primitives (`$_AND_`, `$_OR_`, `$_NOT_`, `$_XOR_`, `$_MUX_`,
    `$_DFF_P_`, `$_DFFSR_PPP_`) plus blackbox instance lines.
  - `engine-gates` (used with `--top <engine>`): same gates pipeline
    but with no blackboxing (everything inside the engine is
    gate-mapped, so leakage analysis sees real gates everywhere).

  Three load-bearing details — see comments in the file:
    1. `proc; opt` (full `opt`, not `opt_clean`) is mandatory before
       techmap. sv2v leaves parameter expressions like `$clog2(MLDSA_Q)+1`
       as runtime arithmetic; without folding them, abr_wrap carries
       ~1200 spurious `$mul` cells that techmap then expands to millions
       of gates.
    2. The blackbox happens *before* `hierarchy`. That keeps Yosys fast,
       but pins every SRAM cell at the *default* port widths (DEPTH=64,
       DATA_WIDTH=32) regardless of per-instance overrides — so
       `write_spice` truncates the wider connections (the netlist
       exposes only addr=6 / data=32 to each SRAM).  The two known
       fixes (post-hierarchy `blackbox m:*<mod>*` to keep paramod
       variants, or skipping the SRAM blackbox so memory pass infers
       `$mem_v2`) both run Yosys past the 5-min budget; documented as
       a known limitation.
    3. The Makefile filters per-cell `no (blackbox) module for cell
       type \`$_AND_'` warnings out of the gates log via `grep -v`;
       otherwise the log is ~580 MB of identical noise.

- **`extract_sram_meta.py`** — parses the `blackbox-sram` Verilog
  netlist to recover each SRAM instance's `DEPTH`, `DATA_WIDTH`,
  `byte_enable` flags. Emits both a JSON (`sram.json`) and a C header
  (`abr_wrap.sram.h`) with `presi_sram_desc` entries indexed by
  `PRESI_SRAM_<INST>` macros.

- **`netlist_inventory.py`** — diagnostic. Reads the JSON netlist and
  prints a cell-type histogram + top-port summary into
  `_build/abr_wrap.inventory.txt`. Useful to see where heavy arithmetic
  lives before deciding what to blackbox.

- **`spice_to_c.py`** — translates the gates SPICE deck into ANSI C.
  Outputs (per netlist top, with optional `--symbol-prefix=<top>__`):
  - `<top>.presi_var.h` — tiny header (~800 B): typedef, PRESI_0/1
    constants, the array decl `extern presi_t <prefix>presi_s[N]` and
    the `<prefix>presi_clk_prev` extern.  No more per-net externs.
  - `<top>.presi_var.c` — single-array allocation, ~150 B.
  - `<top>.presi_idx.h` — `#define IDX_<c_name> <idx>` for every
    named net.  Consumers (presi.c, gen_blackbox_wiring.py output,
    glue) include this for named-port access.
  - `<top>.presi_clk_part_NNN.c` — N (default 32 for abr_wrap, 8 for
    engines) per-part TUs holding cycle-update statements **in
    topological order**: combinational statements first (each consumer
    runs after its driver), DFF/DFFSR assignments grouped at the end.
    The split keeps gcc's working set per file at ~3 GB instead of
    >12 GB for a monolithic 4.3 M-statement function.
  - `<top>.presi_clk.h` — block-scope `extern` decls + ordered
    `<prefix>presi_step_part_NNN()` calls; included from inside the
    harness step function.
  - `<top>.presi_map.csv` — `idx,spice_name,c_name` triples, the
    canonical lookup for tools that need to substitute literal indices
    into generated code.
  - `<top>.presi_bb.csv` — one row per pin of every blackbox cell:
    `instance,module,pin_index,spice_name,c_name,idx`.

  Pin-order note: Yosys's gate primitives (`$_NOT_`, `$_AND_`,
  `$_MUX_`, `$_DFFSR_PPP_`, …) come out of `write_spice` with
  "Guessing order of ports" — the `(* blackbox *)` declarations one
  might add to `cmos_cells.v` get pruned by `hierarchy -check` before
  simplemap creates the cells, so the ordering we get is "output
  first, then inputs in reverse insertion order".  Empirically
  verified, documented in the script.

  Three load-bearing semantics, all in `spice_to_c.py`:
    1. **Edge-triggered DFFs.**  Each `$_DFF_P_` and `$_DFFSR_PPP_`
       cell emits as
       `if ((presi_s[<clk>] & ~presi_clk_prev & 1)) presi_s[<Q>] = presi_s[<D>];`.
       The harness sets `presi_clk_prev = presi_s[IDX_clk]` at the
       end of every `presi_step_netlist()` call, so the flop only
       ticks once per logical cycle no matter how many step calls
       happen.  Without this, `Q = D` (level-sensitive) ticked on
       every step call and doubled the effective clock rate.
    2. **Topological order.**  `topo_order_comb()` runs Kahn's
       algorithm over the comb subset, with edges from "writer of net
       X" to "reader of net X".  Reads of flop outputs skip the
       constraint -- those wires are stable through a cycle since DFF
       assignments run last.  Single-pass propagation through all
       part files gives consistent post-edge values.
    3. **Stable-index allocation.**  `NameMap` assigns each unique
       SPICE net a stable integer index in `<prefix>presi_s[]`.
       Cells emit `presi_s[<idx>] = ...` with literal integers; gcc
       sees one big array, not a million globals.  This is what makes
       -O1 feasible on the 49 MB part files.

- **`gen_blackbox_wiring.py`** — reads `presi_bb.csv` + `sram.json` +
  `seq_rom.json` and emits `_build/abr_wrap.presi_bb_wiring.h`, the
  body of `presi_sram_tick_all()`.  Three kinds of block:
  - **SRAM**: aggregate we/waddr/wdata/re/raddr (and `wstrobe` for
    be-rams) from `presi_s[<idx>]` netlist slots, call
    `presi_sram_*`, distribute rdata back over rdata_o.  Robust to
    Yosys's pin-pruning -- uses the bit counts that actually appear
    in the SPICE rather than sram.json's declared widths.
  - **`abr_seq` blackbox**: walk the 99 pins (clk, en_i, addr_i[10],
    data_o[87]) in SV declaration order, build the address from
    `abr_prog_cntr_nxt`, look up `presi_abr_seq_rom[addr]` and drive
    the 87 `data_o` bits.  When `en_i=0` the cell drives all-zero
    (matches the `else` branch in `rtl/abr_seq.sv`).
  - Other engine blackboxes get a TODO comment so the harness still
    compiles -- but `ntt_top` and `abr_sampler_top` are wired via the
    per-engine glue (see below) instead of behavioural stubs.

- **`gen_engine_glue.py`** — generates per-engine `<engine>.glue.c`
  files that bridge an engine's standalone gate netlist to its
  blackbox-pin connections in the unified abr_wrap netlist.  Reads
  `abr_wrap.presi_bb.csv` + `abr_wrap.presi_map.csv` (for the
  abr_wrap-side indices) + `<engine>.gates.v` (port directions and
  widths) + `<engine>.presi_map.csv` (engine-side indices).
  Emits `<engine>_step_glue(void)` that:
    1. copies abr_wrap-side bb-pin values onto the engine's
       input-port slots;
    2. calls each `<prefix>presi_step_part_NNN()`;
    3. updates `<prefix>presi_clk_prev = <prefix>presi_s[<clk_idx>]`;
    4. copies the engine's output-port slots back onto abr_wrap.
  Skips bits whose abr_wrap-side or engine-side connection is a
  constant (Yosys constant-folds dead fanout / unused inputs).
  Optional `--gate-on-port <name>` for runtime input-gating; not
  enabled in the canonical Makefile (the gate's clk_prev semantics
  need more work -- see plan.md 3f).

- **`extract_abr_seq.py`** — slices the `abr_seq` SystemVerilog module
  out of `_build/abr_wrap.sv2v.v` into a self-contained `.v` file
  (sv2v has already inlined every package localparam, so the slice
  is closed under reference).  Used by the standalone abr_seq build
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

## Targets in `presi/Makefile`

| target              | what it produces                                              |
|---------------------|---------------------------------------------------------------|
| `sv2v`              | `_build/abr_wrap.sv2v.v` (and depends only on RTL)            |
| `netlist-coarse`    | diagnostic post-`proc; opt` snapshot                          |
| `netlist-blackbox`  | sram-blackboxed netlist + sram.json/sram.h                    |
| `netlist-gates`     | abr_wrap gates SP/V/JSON                                      |
| `gate-c`            | abr_wrap presi_var.{h,c}, presi_clk*, presi_idx.h, maps       |
| `seq-rom`           | abr_seq.standalone.{v,json} → seq_rom.h                       |
| `ntt-top`           | per-engine gates + spice_to_c for ntt_top                     |
| `abr-sampler-top`   | per-engine gates + spice_to_c for abr_sampler_top             |
| `abr-seq-core`      | per-engine gates + spice_to_c for abr_seq (essentially ROM)   |
| `engine-glue`       | ntt_top.glue.c + abr_sampler_top.glue.c                       |
| `run-gates`         | builds + runs `presi-gates` (abr_wrap only, engines stubbed)  |
| `cosim`             | `presi-gates-cosim` -- abr_wrap + ntt_top + abr_sampler_top   |
| `run-cosim`         | runs the smoke harness in cosim                               |
| `run-cosim-keygen`  | runs `mldsa-keygen` end-to-end (KEYGEN_MAX_CYCLES default 200000)|

## Current end-to-end status

`make -C presi -j 4 cosim` builds the **co-simulation binary**
`_build/presi-gates-cosim` (258 MB; clean rebuild ~12 min) which links
abr_wrap's gate netlist + the standalone gate netlists for `ntt_top`
and `abr_sampler_top`.  After 64 reset cycles plus 64 idle cycles, AHB
reads of `MLDSA_NAME[0..1]`, `MLDSA_VERSION[0..1]`, and `MLDSA_STATUS`
return the expected hardwired constants from `abr_params_pkg.sv` and
the READY status bit.  Writing `MLDSA_CTRL = 1` then dispatches the
abr_seq FSM; the controller walks through real Dilithium keygen UOPs
(LD_SHAKE256 → SHAKE256 → LFSR → SHAKE256 → REJB s1[0..1]) within the
256-cycle smoke poll window.  Real engine handshakes (sampler_busy_o,
SHA3 absorb/squeeze, rejection sampling) all work through gate-level
netlists -- no behavioural models.

End-to-end Dilithium keygen (`make run-cosim-keygen`) is wired source-
side: load `seed_in.dat` + optional `ent_in.dat`, write `MLDSA_CTRL`,
poll `STATUS` for `READY|VALID`, dump `pk_out.dat` / `sk_out.dat`.
Run cost ~5 cyc/s, so a full keygen takes 1-3 hours wall.

## Prerequisites

- Yosys ≥ 0.64 (older 0.36 is 5–10× slower on `proc` and times out on
  `abr_wrap`).  Built from source if needed; the upstream
  `chipsalliance/adams-bridge v2.0.3` flow has been smoke-tested
  against 0.64+195.
- sv2v ≥ 0.0.11 (the `--top abr_wrap` interface-collapsing behavior is
  what makes the gate flow fit in memory).
- gcc ≥ 12 (the array-layout part .c files build at -O0 in seconds and
  -O1 in ~2 min/file; bump `GATES_OPT=-O1` in the Makefile for a
  longer one-off rebuild that produces a faster runtime binary).
