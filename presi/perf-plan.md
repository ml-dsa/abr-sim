# Branchless cells + comb/flop split (2026-05-08, in flight)

Continues the work in `chunked-plan.md`.  That refactor moved each
part's body into static helpers in one TU; this one goes further:
each chunk gets its own TU, cells are emitted as branchless bitwise
expressions, the per-flop edge mask is hoisted to a chunk-local,
and dispatchers split into comb-only and flop-only flavours so the
heavy flop chunks run only on the rising-edge phase.

## What's done and verified

All five build-system / generator changes below are committed in the
working tree (uncommitted to `dev`).  Smoke + snapshot save->load->save
round-trip pass after each step that has been validated end-to-end.

1. **One TU per chunk** (`presi/flow/spice_to_c.py`,
   `presi/Makefile`).  `write_part_files()` writes each chunk to its
   own .c file (`<top>.presi_clk_part_PPP_chunk_NNN.c` -> later
   `<top>.presi_clk_part_PPP_<kind>_NNN.c`) with extern-linkage
   functions.  The Makefile uses GNU Make's auto-rebuild pattern
   with `-include $(BUILD)/<top>.chunks.mk` so the snippet emitted
   by spice_to_c.py drives the chunk .o list without the build
   system needing to predict the chunk count.
   - Verified: cold cosim build 14:00 -> 8:48; smoke + round-trip pass.

2. **Two correctness fixes for snapshot round-trip**
   (`presi/presi_gates.c`, `presi/presi.c`):
   - `presi_cycle` now runs an extra (settle, sram_tick, settle)
     sequence so abr_wrap comb downstream of engine output paste
     and SRAM rdata_o is fresh at cycle end.
   - `save_snapshot` now calls `presi_settle_after_load(m)` before
     writing, so the on-disk format always represents a canonical
     post-settle state.
   - Verified: kg-1.bin == kg-1again.bin byte-identically.

3. **Branchless cell encoding** (`presi/flow/spice_to_c.py`).
   - `__MUX_` rewritten as `Y = (S & B) | (~S & A)`; no ternary,
     no conditional branch.
   - `__NOT_` and the NAND/NOR/XNOR/ANDNOT/ORNOT family use
     `x ^ PRESI_1` instead of `~x` to avoid the int-promotion
     `-Woverflow` warning that fires when Yosys ties an input to a
     constant.  Same semantics under the all-bits invariant
     (`PRESI_1 = (presi_t)~0 = 0xFF`).
   - `emit_dff` and `emit_dffsr` rewritten as a single bitwise
     expression each:
     ```
     DFF:    Q = (edge & D) | (~edge & Q)
     DFFSR:  Q = S | (~S & ~R & ((edge & D) | (~edge & Q)))
     ```
     All `if/else if` chains gone.
   - Verified: cold build 9:24, 0 warnings.

4. **Emit-time peephole constant folding**
   (`presi/flow/spice_to_c.py`, helpers `fold_not / fold_and /
   fold_or / fold_xor`).  When Yosys ties a cell input to PRESI_0
   or PRESI_1, the cell expression simplifies algebraically.  The
   COMBINATIONAL templates and emit_dff / emit_dffsr now use these
   helpers, so e.g. a DFFSR with S=PRESI_0, R=variable collapses
   from
   ```
   Q = PRESI_0 | ((PRESI_0 ^ PRESI_1) & (R ^ PRESI_1) & ...);
   ```
   to
   ```
   Q = (R ^ PRESI_1) & ...;
   ```
   - Verified: heavy chunk 1.78 MB -> 1.52 MB, build 9:35.

5. **Chunk-local `_edge` hoist**
   (`presi/flow/spice_to_c.py`, `write_part_files`).  Every flop
   in a netlist shares the same clk pin and the same clk_prev
   global, so the rising-edge mask is computed once at the top of
   each chunk that contains flops:
   ```
   void <top>__chunk_PPP_NNN(presi_t *s) {
       presi_t _edge = s[<clk_idx>] & (<top>__presi_clk_prev ^ PRESI_1);
       s[Q0] = (s[R0] ^ PRESI_1) & ((_edge & s[D0]) | ((_edge ^ PRESI_1) & s[Q0]));
       ...
   }
   ```
   `translate()` looks up the netlist's clk pin index from the
   namemap and passes it through to the chunk emitter.  Statements
   are now `(stmt, is_flop)` tuples so the chunk emitter knows
   when to declare `_edge`.
   - Verified: heavy chunk 1.52 MB -> 819 KB, build 8:12, smoke +
     round-trip pass.

## Done but not yet end-to-end validated (the in-flight piece)

6. **Comb / flop dispatcher split** -- the change that should buy
   the biggest runtime win on flop-heavy parts.

   Statements are already separated by topo order (`comb_ordered`
   first, `flop_items` last), so `write_part_files` slices each
   part into:
   - `<top>.presi_clk_part_PPP_comb_NNN.c` -- pure comb chunks.
   - `<top>.presi_clk_part_PPP_flop_NNN.c` -- pure flop chunks
     (with `_edge` declared at top).

   Each part dispatcher emits two functions:
   `<prefix>presi_step_part_PPP_comb(presi_t *s)` and
   `<prefix>presi_step_part_PPP_flop(presi_t *s)` (the latter has an
   empty body when the part has no flops, so the harness can call
   either flavour unconditionally).

   `spice_to_c.py` now writes two clk-dispatch headers
   (`--clock-comb`, `--clock-flop`) instead of one.  `Makefile`
   passes both paths and renames `GATES_CLK_H` ->
   `GATES_CLK_COMB_H` / `GATES_CLK_FLOP_H`.

   `gen_engine_glue.py` emits two glue flavours per engine:
   - `<engine>_step_glue_comb(void)` -- input copy + comb step +
     output copy.  No clk_prev update.
   - `<engine>_step_glue_flop(void)` -- flop step + clk_prev update
     + output copy.  No input copy (relies on _comb just having
     run with the same clk).

   `presi_gates.c`:
   - `presi_step_netlist()` -> `_comb()` and `_flop()` (each
     `#include`s the matching dispatch header; clk_prev no longer
     touched here -- harness owns that update).
   - `presi_engines_step()` -> `_comb()` and `_flop()` calling the
     two glue flavours.
   - `presi_cycle()` reorganised:
     ```
     phase 0 (clk=0):  apply, step_comb, engines_comb;
                       set all clk_prev = 0;
     phase 1 (clk=1):  apply, step_comb, engines_comb,
                       step_flop, engines_flop;
                       set all clk_prev = 1;
     settle:           step_comb, engines_comb;
     sram_tick;
     capture;
     ```
     `presi_clk_prev`, `ntt_top__presi_clk_prev`, and
     `abr_sampler_top__presi_clk_prev` are explicitly assigned by
     the harness; engine glue's _flop function still updates the
     engine clk_prev as a side effect (idempotent with the
     harness-side assignment).
   - `presi_settle_after_load()` now uses _comb only (no rising
     edge to process when restoring a settled snapshot).
   - Engine var headers (`ntt_top.presi_var.h`,
     `abr_sampler_top.presi_var.h`) are included so the engine
     clk_prev externs are visible.

   **Build status:** cold rebuild succeeds in 7:59 with the split
   in place.  45 -Wunused-parameter warnings on the empty
   step_part_NNN_comb / _flop bodies (parts where one side has no
   statements) -- already fixed in spice_to_c.py: dispatcher emits
   `(void) s;` when the chunk-list is empty, so these will go to
   zero on the next regenerate.

   **Validation status:**
   - Smoke run **passes** -- FSM walks through the same PCs in the
     same cycle counts as before (pc=2 for ~13 cy, pc=3 for 32,
     pc=4 for 2, pc=5 for 41, pc=6 for 107, pc=7 ongoing;
     final-busy=1 status=00000000 after 256 cycles).
   - Snapshot round-trip **was killed before completion** (user
     stopped for the day after smoke passed); the round-trip needs
     to re-run to confirm save -> load -> save is byte-identical
     under the new structure.

## Resume here tomorrow

The working tree has all of (1)..(6) applied.  No changes are
committed; `git status -s` shows:

```
 M presi/Makefile
 M presi/flow/gen_engine_glue.py
 M presi/flow/spice_to_c.py
 M presi/presi.c
 M presi/presi_gates.c
```

Plus this file (`presi/perf-plan.md`) is new.

Steps to resume:

```sh
# 1. Regenerate everything (the (void) s; fix in spice_to_c.py
#    needs a fresh chunk emit; otherwise stale .c files will
#    still warn).  Rebuilds in ~8 min cold:
rm -f presi/_build/*.gate-c.stamp \
      presi/_build/*_comb_*.[co] presi/_build/*_flop_*.[co] \
      presi/_build/*.presi_clk_part_*.[co] \
      presi/_build/*.chunks.mk \
      presi/_build/*.presi_clk_comb.h presi/_build/*.presi_clk_flop.h \
      presi/_build/libpresi_gates.a presi/_build/presi-gates-cosim
PATH=/home/mjos-ai/rv/src/sv2v/bin:$PATH \
    make -C presi -j 4 cosim
# Expect 0 warnings now.

# 2. Smoke (validates the new dispatcher ABI end-to-end).  ~75s.
make -C presi run-cosim

# 3. Snapshot round-trip (validates the comb/flop split + harness
#    cycle reorg + clk_prev book-keeping).  ~5 min.
presi/tools/snapshot-roundtrip.sh
# OR manual (no pycryptodome on this host):
( cd presi/_build/snap-test && \
  python3 -c 'open("seed_in.dat","wb").write(bytes(range(32)))' && \
  ../../_build/presi-gates-cosim -seed seed_in.dat \
      -save kg-init.bin -init-only mldsa-keygen && \
  ../../_build/presi-gates-cosim -load kg-init.bin \
      -save kg-1.bin -t 200 run && \
  ../../_build/presi-gates-cosim -load kg-1.bin \
      -save kg-1again.bin -t 0 run && \
  cmp kg-1.bin kg-1again.bin && echo PASS )
```

## Build-time progression on this branch

Each row is a cold cosim rebuild (Yosys cached, gate-c regenerated,
all .o recompiled, libpresi_gates.a rebuilt, presi-gates-cosim
linked).

| Stage                                  | Heavy .c | Cold wall | Warnings |
|----------------------------------------|---------:|----------:|---------:|
| Static helpers in one TU (chunked-plan)|  42 MB   |  ~14:00   |   2428   |
| One TU per chunk                       | 1.34 MB  |   8:48    |   2428   |
| + branchless DFF/DFFSR/MUX             | 1.78 MB  |   9:24    |      0   |
| + emit-time const folding              | 1.52 MB  |   9:35    |      0   |
| + chunk-local `_edge` hoist            | 819 KB   |   8:12    |      0   |
| + comb/flop split                      | 819 KB   |   7:59    |     45 * |

\* The 45 warnings are -Wunused-parameter on empty step_part bodies;
fixed in `write_part_files` to emit `(void) s;` -- waiting on regen.

## Open ideas (not yet attempted)

- **Drop Ubuntu hardening flags** (`-D_FORTIFY_SOURCE=3`,
  `-fstack-protector-strong`, `-fstack-clash-protection`,
  `-fasynchronous-unwind-tables`, `-fzero-init-padding-bits=all`)
  for the gate-c .o compiles.  Estimated 25-40% wall-time
  reduction; these are auto-injected via dpkg-buildflags and add
  nothing to a sandboxed simulator.

- **`-O1` should be tractable** now.  With every chunk capped at
  ~1 MB and no branches in the cell statements, gcc's per-BB
  passes should finish in seconds rather than the unbounded
  17+ min we observed pre-refactor.  Worth measuring once the
  comb/flop split is fully validated.

- **Bytecode VM** as a future direction if compile time is still
  the limiting factor: spice_to_c.py emits a binary opcode table
  per chunk; a ~50-line interpreter loops over it.  Compile time
  drops to seconds (just compile the interpreter once).  Runtime
  per cycle is slower than compiled C but the table can be
  mmap'd, regenerated independently of the binary, and the
  iteration speed for development would be transformed.

- **Constant propagation across cells.**  We constant-fold per
  cell; when a folded cell becomes `s[Y] = PRESI_0` (or PRESI_1),
  downstream cells reading `s[Y]` could fold further.  Requires
  iterating the topo order, which is doable but more invasive
  than the per-cell folding already in place.

- **`abr_seq` as a real engine.**  Currently the abr_seq sequencer
  ROM is extracted by `make seq-rom` and the FSM is blackboxed
  inside abr_wrap; with the comb/flop split's per-engine glue
  pattern, abr_seq could be wired in like ntt_top / abr_sampler_top
  if we ever want fully gate-level controller behavior in the
  cosim binary (not just `abr_wrap` + the two heavy datapath
  engines).  Out of scope for this branch.
