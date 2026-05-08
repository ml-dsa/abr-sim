# spice_to_c.py refactor: pointer-arg + chunked helpers

## Why

Compile-time data point from the 2026-05-08 cosim build:

| TU                                       | size  | stmts | -O0 wall (1 thread) |
|------------------------------------------|------:|------:|--------------------:|
| `abr_wrap.presi_clk_part_001.c`          | 5 MB  |  ~75 k |  ~6 s              |
| `ntt_top.presi_clk_part_007.c`           | 17 MB | 340 k |  ~5 min            |
| `abr_sampler_top.presi_clk_part_007.c`   | 40 MB | 296 k |  ~3 min            |

`-O0` over a 340 k-statement single basic block is slow because every
gcc back-end pass that's super-linear in BB size (var-tracking,
liveness, debug-info bookkeeping, RTL DAG construction) hits a wall.
This is also why `-O1` was unaffordable -- the same passes are even
worse with optimisation enabled.

Two changes to `spice_to_c.py` flatten the cost curve:

1. **Pointer-arg style.**  Emit `s[<idx>]` and take `presi_t *s` as a
   function parameter, instead of indexing the prefixed global
   `<prefix>presi_s[<idx>]` directly.  Source files shrink ~50-60 %
   because the long prefix vanishes from every reference.  The back
   end keeps `s` in a register; no relocation entries per access; less
   debug bookkeeping per use.

2. **Chunk the body into helper functions.**  Each part .c becomes:

   ```c
   /* generated */
   #include "ntt_top.presi_var.h"

   static void chunk_000(presi_t *s) { /* ~8 k stmts */
       s[<a>] = ...; ...
   }
   static void chunk_001(presi_t *s) { ... }
   ...
   static void chunk_039(presi_t *s) { ... }

   void ntt_top__presi_step_part_007(presi_t *s) {
       chunk_000(s); chunk_001(s); /* ... */ chunk_039(s);
   }
   ```

   gcc's per-BB algorithms now see ~8 k-statement basic blocks,
   tractable at any optimisation level.  Topo-sort correctness is
   preserved trivially: the linear topo order is sliced into
   contiguous chunks; each chunk runs to completion before the next,
   so producer-before-consumer is intact.

## Estimated wins

Source file size (rough, dominated by per-statement prefix savings):

| TU                               | current | refactored |
|----------------------------------|--------:|-----------:|
| ntt_top.presi_clk_part_007.c     | 17 MB   | ~7 MB      |
| abr_sampler_top.presi_clk_part_*.c | 40 MB | ~16 MB     |
| abr_wrap.presi_clk_part_*.c      | 5 MB    | ~3 MB      |

Compile time at -O0 (back of the envelope, expecting roughly linear
with BB size for the small-BB regime):

| TU                                        | current | refactored |
|-------------------------------------------|--------:|-----------:|
| ntt_top.presi_clk_part_007.c              | 5 min   | ~30 s      |
| abr_sampler_top.presi_clk_part_007.c      | 3 min   | ~30 s      |
| abr_wrap.presi_clk_part_001.c             | 6 s     | ~3 s       |

Total clean rebuild (-j 4): 18 min → expected ~6-8 min.

`-O1` becomes plausible too -- gcc -O1 on an 8 k-statement function is
sub-second on this machine.  Worth measuring as a follow-up.

## File-by-file changes

### `presi/flow/spice_to_c.py`

**New CLI flag:**
- `--chunk-size <N>` (default 8192).  N=0 disables chunking (one
  function body, for debugging).

**`NameMap.ref()` (bit-vector helper):**
- Currently returns `<prefix>presi_s[<idx>]` for any non-constant net.
- New behaviour: returns `s[<idx>]` (no prefix).  The function arg
  shadows the global, so within a part .c statements reference the
  parameter automatically.
- Constants (`PRESI_0`, `PRESI_1`) unchanged.
- DFF emit currently writes `if ((<prefix>presi_s[<clk>] & ~<prefix>presi_clk_prev & 1)) ...`.
  After the refactor: `if ((s[<clk>] & ~<prefix>presi_clk_prev & 1)) ...`.
  `<prefix>presi_clk_prev` stays a global (one byte, accessed once
  per flop, not per-statement) so DFF emit explicitly references the
  prefixed scalar.

**`write_part_files()`:**
- Slice the statement list into chunks of `chunk_size` each.
- Emit one `static void chunk_<MMM>(presi_t *s)` per chunk.
- Emit one public `<prefix>presi_step_part_<NNN>(presi_t *s)` that
  calls every chunk in order.
- Function name now takes a pointer arg.  Update the prototype-emit
  in `write_clk_dispatch()` accordingly.

**`write_clk_dispatch()`:**
- Currently emits `<prefix>presi_step_part_NNN();`.
- New: `<prefix>presi_step_part_NNN(<prefix>presi_s);`
- Externs change to take a `presi_t *` arg.

### `presi/flow/gen_engine_glue.py`

The glue calls each engine's part functions in sequence as part of
its `<engine>_step_glue()` body.  After the refactor, the call sites
pass the engine's array pointer:

```c
/* before */
ntt_top__presi_step_part_000();
/* after */
ntt_top__presi_step_part_000(ntt_top__presi_s);
```

The bb-pin copy code in glue keeps using full prefixed names since it
bridges two arrays in the same TU (e.g.,
`ntt_top__presi_s[594] = presi_s[7509];`).

### `presi/Makefile`

Pass `--chunk-size` to spice_to_c.py invocations.  Default 8192;
override via:

```
make ... CHUNK_SIZE=4096   # smaller chunks, more functions
make ... CHUNK_SIZE=0      # disable chunking (debug)
```

### `presi/presi.c` / `presi_gates.c`

No source-level change required: presi_clk.h is included from inside
`presi_step_netlist()`, and the dispatcher (now passing pointers) is
generated.  The `IDX_*` macros are unchanged.

## Risks / gotchas

1. **Static helper symbols clutter `nm` output**, but they're file-
   local so don't pollute the final binary's symbol table.
2. **Debug step-through lands in chunk_NNN frames** — slightly less
   pleasant than stepping in one giant function, but the giant
   function was already unstoppable in gdb.
3. **gcc may decide to inline chunks at -O1+** (since they're static
   single-callsite).  That defeats the size-of-BB win; if -O1
   measurements show this, slap `__attribute__((noinline))` on the
   chunks.
4. **Topo-sort still produces a single linear order**; chunking
   doesn't change correctness.  If we ever switch to a "level-based"
   schedule, the chunk boundaries would naturally align with levels;
   keep that option open.
5. **Existing `presi_idx.h` and `presi_map.csv` are unaffected.**
   The `IDX_<c_name>` constants are part of the public API used by
   harness code; they don't change.

## Validation steps

1. ~~Refactor spice_to_c.py + gen_engine_glue.py.~~ **Done.**
2. **Partial — see "Resume here" below.**  `make clean && make -C presi
   -j 4 cosim`.  First rebuild reached 56/57 .o files before being
   killed for context-management reasons; the only outstanding TU
   was `ntt_top.presi_clk_part_007.o` (the DFFSR-heavy tail).  Re-
   running `make -C presi -j 4 cosim` resumes from the .o cache.
3. **Pending.** `./_build/presi-gates-cosim` smoke run.  Compare FSM
   transitions against the pre-refactor reference (should be byte-
   identical at each cycle).
4. **Pending.** Save / load round-trip (`presi/tools/snapshot-roundtrip.sh`).
   Snapshots from before/after the refactor should NOT cross-load
   (different layout-hash if PRESI_NETS counts changed; same hash
   if unchanged) — they DO, however, share the same wire-level state,
   so a same-build save + load + save must round-trip.
5. **Pending.** Try `make GATES_OPT=-O1 cosim` and measure.  If still
   affordable, make -O1 the default for production builds.

## Observed effects so far (partial measurement)

Source-file size shrinkage (chunked + pointer-arg vs pre-refactor):

| TU                                    | pre-refactor | post-refactor | shrink |
|---------------------------------------|-------------:|--------------:|-------:|
| ntt_top.presi_clk_part_001.c          | 17 MB        |  9.3 MB       |  -45 % |
| ntt_top.presi_clk_part_007.c          | 17 MB *      | 42.9 MB **    |  +153 % |
| abr_sampler_top.presi_clk_part_007.c  | 40 MB        | 16.1 MB       |  -60 % |
| abr_wrap.presi_clk_part_001.c (est.)  |  5 MB        |  ~3 MB        |  -40 % |

  *  Estimated; we never measured the pre-refactor part_007 directly.
  ** Part_007 is dominated by DFFSR statements (240 k of them, ~161
     chars each post-refactor).  These statements still reference
     `<prefix>presi_clk_prev` explicitly (it's a global, not in the
     array), so the prefix savings is limited.  Comb-heavy parts
     (0-6) shrank as predicted.

Compile time (gcc -O0, -j 4 wall, partial):

| TU                                    | pre-refactor | post-refactor |
|---------------------------------------|-------------:|--------------:|
| abr_sampler_top.presi_clk_part_000.o  | ~3 min       | ~1 min        |
| abr_wrap.presi_clk_part_000.o         | ~6 s         | (similar)     |
| ntt_top.presi_clk_part_007.o          | 5+ min       | NOT MEASURED  |

Total wall to 56/57 .o: roughly 5 min in the partial rebuild vs ~22
min in the pre-refactor build.  Expect ~7-9 min for a clean rebuild
once part_007 measurement lands.

## Resume here

State as of pause:
- Refactor complete and committed in working tree (uncommitted):
    `presi/flow/spice_to_c.py`, `presi/flow/gen_engine_glue.py`,
    `presi/Makefile` (CHUNK_SIZE=8192 default).
- 56/57 .o files cached in `_build/`.  Last outstanding TU is
  `_build/ntt_top.presi_clk_part_007.o`.
- libpresi_gates.a not yet built.  presi-gates-cosim binary not yet
  built.
- Snapshot CLI (presi.c, presi_state.{c,h}, presi_gates.{c,h},
  presi_model.h) all in working tree, untracked + modified.

Steps to resume (in order):

```sh
# 1. Finish the build (resumes from .o cache, ~5 min):
make -C presi -j 4 cosim

# 2. Smoke run (validates the new step_part(presi_t *s) ABI works
#    end-to-end across abr_wrap + engine glue):
make -C presi run-cosim

# 3. Snapshot round-trip (validates presi_state save/load):
presi/tools/snapshot-roundtrip.sh

# 4. -O1 measurement: blow away the .o cache, rebuild with -O1
#    (chunked layout should make this affordable):
rm -f _build/*.o _build/libpresi_gates.a _build/presi-gates-cosim
time make -C presi -j 4 GATES_OPT=-O1 cosim
```

After validation, update plan.md tables and consider committing the
working-tree changes:

```
git status -s  # surveys what to commit
```

Untracked/modified files at pause time:
- M  CLAUDE.md
- M  presi/Makefile
- M  presi/flow/README.md
- M  presi/flow/gen_engine_glue.py
- M  presi/flow/spice_to_c.py
- M  presi/plan.md
- M  presi/presi.c
- ?  presi/chunked-plan.md     (this file)
- ?  presi/presi_gates.{c,h}
- ?  presi/presi_model.h
- ?  presi/presi_state.{c,h}
- ?  presi/state-plan.md
- ?  presi/tools/snapshot-roundtrip.sh

Suggested commit split:
1. Library + snapshot CLI (presi_gates, presi_state, presi_model,
   presi.c rewrite, Makefile library targets, state-plan.md, plan.md
   library section, CLAUDE.md update, flow/README.md update,
   tools/snapshot-roundtrip.sh).
2. spice_to_c.py refactor (pointer-arg + chunked helpers,
   gen_engine_glue.py call-site fix, Makefile CHUNK_SIZE knob,
   chunked-plan.md, plan.md timing table update).

## Future direction

With chunked helpers, the natural next step is to **emit toggle-
counting hooks** at the chunk boundary:

```c
static void chunk_005(presi_t *s) { ... }   /* original */
/* could become */
static void chunk_005(presi_t *s, uint64_t *toggles) {
    presi_t pre[N];
    memcpy(pre, &s[chunk_lo], chunk_hi - chunk_lo);
    /* statements ... */
    for (i = chunk_lo; i < chunk_hi; i++)
        *toggles += __builtin_popcount(s[i] ^ pre[i - chunk_lo]);
}
```

Or, simpler: run the whole part, snapshot before/after, popcount diff
in the harness.  With state already in a flat array, the diff is one
loop -- no need to instrument the chunks.  Either way, the chunked
layout makes future TVLA work easier to bolt on.
