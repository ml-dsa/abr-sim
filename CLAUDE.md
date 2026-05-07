# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project purpose

Pre-silicon trace generator for the [Adam's Bridge](https://github.com/chipsalliance/adams-bridge) PQC hardware accelerator (FIPS 204 ML-DSA / Dilithium and FIPS 203 ML-KEM / Kyber). It runs a full Verilator RTL simulation of an ML-DSA-87 or ML-KEM-1024 operation and emits VCD toggle traces suitable for TVLA-style side-channel leakage analysis.

## Baseline

The repo is on `adams-bridge` v2.0.3 (commit `b77e3d8`). The v2 migration is complete: `abr_top` replaced `mldsa_top`, the split `mldsa_seq_prim`/`mldsa_seq_sec` sequencers were collapsed into a single `abr_seq`, ML-KEM acquisition was added, and the new ML-DSA modes `mldsa-sign-extmu` and `mldsa-sign-stream` are wired up. `v2-plan.md` was the migration tracker and has been removed; `git show 4cdbb3a^:v2-plan.md` recovers the historical record if needed.

Known upstream limitation: there is no `MASKING_EN` build switch. In `v2.0.3`'s `abr_top`, masking is encoded in sequencer opcodes and internal control rather than exposed as a top-level parameter, so a masked-vs-unmasked build matrix would require an RTL-level patch rather than a parameter override.

## Prerequisites

- Verilator >= 5.037 (development version; older versions may not work)
- Standard C/C++ toolchain (`gcc`, `g++`)
- Python 3 with `pycryptodome` (provides `Crypto.Hash`) — used by `flow/fips204.py`, `flow/fips203.py`, `flow/mldsa-gen.py`, `flow/mlkem-gen.py`. Bash trace scripts honor `PYTHON=...` and default to `python3`.
- The `adams-bridge` git submodule must be checked out at the pinned release (currently v2.0.3, commit `b77e3d8`). After fresh clone: `git submodule update --init --recursive`.
- **For the `presi/` flow only**: Yosys ≥ 0.64 and sv2v ≥ 0.0.11. Older Yosys 0.36 has a `proc_mux` regression that takes 25+ minutes on `abr_ctrl`'s FSM and effectively never finishes the gates flow.

## Build

```
make            # builds both readvcd and abr_wrap (Verilator step takes a minute or two)
make abr_wrap   # just the wrapped DUT binary
make lint-abr   # Verilator lint-only check
make clean      # also removes *.vcd, *.dat, _build/, _tr*/, plot/ artifacts
```

The Makefile build does three non-obvious things:

1. **Patches upstream RTL.** `rtl/abr_seq.sv` is *generated* by copying `adams-bridge/src/abr_top/rtl/abr_seq.sv` and applying `rtl/abr_seq.sv.patch`. The patch inserts a hook that `$display`s the current FSM state each cycle, decoded via `rtl/abr_seq_decode.sv` (covers both `MLDSA_*` and `MLKEM_*` symbols). The decoded output appears as `[seq]` lines in run output. If you bump the `adams-bridge` submodule, the patch will likely need to be regenerated.
2. **Verilator file list is generated** at `_build/xabr_wrap.vf` from upstream `adams-bridge/src/abr_top/config/abr_top.vf`. The Makefile materializes `${ADAMSBRIDGE_ROOT}` to the local submodule path, swaps in the patched `rtl/abr_seq.sv`, and appends `rtl/abr_seq_decode.sv` and `rtl/abr_wrap.sv`. Adding new local RTL means editing the Makefile's append step.
3. **Local wrapper.** `rtl/abr_wrap.sv` instantiates `abr_top` + `abr_mem_top` with `AHB_ADDR_WIDTH=32` and CALIPTRA disabled. This is the top-level module that `abr_wrap` (the C++ binary) drives.

## End-to-end trace flow

The pipeline has four stages, each producing inputs for the next:

1. **Vector generators.**
   - `flow/mldsa-gen.py <message> <xi> <rho'>` — pure-Python ML-DSA reference (`flow/fips204.py`) generates test vectors and writes `hash_in.dat`, `seed_in.dat`, `pk_in.dat`, `sk_in.dat`, `rnd_in.dat`, `mu_in.dat` (for external-µ), plus an "expected" `sig_in.dat` for cross-check. All args optional.
   - `flow/mlkem-gen.py {all|keygen|encaps|decaps} <d> <z> <m>` — pure-Python ML-KEM reference (`flow/fips203.py`) writes `seed_d_in.dat`, `seed_z_in.dat`, `msg_in.dat`, `ek_in.dat`, `dk_in.dat`, `ct_in.dat`, `ss_in.dat`.
2. **`./abr_wrap [options] <operation>`** — Verilated RTL sim with a descriptor-driven C++ AHB testbench (`src/abr_wrap.cpp`). Reads the `*_in.dat` files via the AHB register map (constants near the top of `abr_wrap.cpp`, mirrors `adams-bridge/src/abr_top/rtl/abr_reg.rdl`), runs the operation, writes the corresponding `*_out.dat`, and dumps a VCD trace (none by default). Operations:
   - ML-DSA: `mldsa-keygen`, `mldsa-sign`, `mldsa-verify`, `mldsa-kgsign`, `mldsa-sign-extmu` (caller-supplied µ), `mldsa-sign-stream` (variable-length byte stream via `MSG_STROBE`). The bare names `keygen` / `sign` / `verify` / `kgsign` are aliases for the `mldsa-*` versions.
   - ML-KEM: `mlkem-keygen`, `mlkem-encaps`, `mlkem-decaps`, `mlkem-kgdecaps`.
   After signing, a successful run satisfies `cmp sig_out.dat sig_in.dat`. Engine-specific status error masks live in the `Operation` table (`STATUS_MLDSA_ERROR=0x8`, `STATUS_MLKEM_ERROR=0x4`).
3. **`./readvcd <trace.vcd> <time-signal> [threshold] [filters/report-cycles]`** — single-pass C VCD parser. The `<time-signal>` is matched as a substring against signal names; the canonical choice (set in `flow/readvcd.prm`) is `dec.cyc` (the cycle counter inserted by `rtl/abr_seq_decode.sv`). Output is one `# <cycle> [togd] <count>` line per cycle. Optional hierarchy filters `-i <glob>` / `-e <glob>` restrict which VCD signals contribute to the toggle count; the timing signal is still used even if it is outside the selected hierarchy.
4. **`flow/tvla.py`** — Welch t-test over toggle counts from many fixed/random runs, using the streaming `fdist` accumulator (no full data array kept in memory).

VCD traces are huge (~25 GB for one signing op). The `flow/gen-*.sh` scripts avoid writing them to disk by piping through a named pipe:

```
mkfifo trace.vcd
( set -f; ./readvcd trace.vcd $vcdprm > trace.log ) &
./abr_wrap -t $maxcyc -vcd trace.vcd mldsa-sign | tee run.log
```

The `set -f` subshell is load-bearing: the prm files contain literal `*` glob patterns (e.g. `-i *top0.ntt_gen*.ntt_top_inst*`), and the unquoted `$vcdprm` expansion would pathname-glob them against files in the trace dir's cwd otherwise. Word splitting still happens inside the subshell so multi-token prms tokenize correctly. Don't replace the subshell with a plain `&` background — and don't quote `$vcdprm`, that would defeat the word splitting.

Trace scripts:

- ML-DSA: `gen-fix.sh` (fixed key, random rnd), `gen-rnd.sh` (fully random sign), `gen-kgr.sh` (keygen+sign combo).
- ML-KEM: `gen-kem-enc-fix.sh`, `gen-kem-enc-rnd.sh`, `gen-kem-dec-fix.sh`, `gen-kem-dec-rnd.sh`, `gen-kem-kg.sh`.

Each script spawns `n` runs into `_tr_<kind>-<id>-<x>/` subdirs (gitignored); `gen-sum.sh` then concatenates `*.log.gz` from each into a single sorted `.dat`. `flow/tvla.py` consumes those.

Hierarchy-focused `readvcd` presets can be passed anywhere a script asks for `<readvcd.prm>`:

- `flow/readvcd.prm` or `flow/readvcd-full.prm`: full-core baseline.
- `flow/readvcd-control.prm`: ABR control/register block.
- `flow/readvcd-sampler.prm`: sampler top, including SHA3.
- `flow/readvcd-sha3.prm`: SHA3 below sampler.
- `flow/readvcd-keccak.prm`: Keccak round/storage below SHA3.
- `flow/readvcd-ntt.prm`: generated NTT instances.
- `flow/readvcd-mldsa-aux.prm`: ML-DSA auxiliary encode/decode/check blocks.
- `flow/readvcd-mlkem-codec.prm`: ML-KEM compress/decompress blocks.
- `flow/readvcd-memory.prm`: wrapper memory/export signals.

The `plot/` directory has a `plot.sh` that turns a tvla output (e.g. `tvla11k.txt`) into the trace/avg/std/tvla gnuplot figures used in `doc/20250530-hardwear-abr.pdf`.

## Reference docs

- `doc/mldsa_fsm.md` and `doc/mlkem_fsm.md` annotate every `[seq]` marker emitted by `rtl/abr_seq_decode.sv`, mapping each FSM address (and its range-decoded `+offs` form) to the executed `ABR_UOP_*` opcode and the matching FIPS 204 / FIPS 203 algorithm + line. Use these when interpreting `[seq]` lines in a run log or relating an `abr_seq.sv` ROM slot to the standardized pseudocode. Cycle counts in the example traces are illustrative — `abr_wrap`'s AHB-setup window varies the absolute values; addresses and names are the stable part.
- `doc/nist.fips.204.pdf` and `doc/nist.fips.203.pdf` are the official ML-DSA and ML-KEM specifications (cited by algorithm number and line in the FSM docs).
- `doc/20250530-hardwear-abr.pdf` is the prior-art TVLA presentation that drove the trace generator's design.

## Presi: presilicon netlist simulator (in progress)

`presi/` is an `xpresi`-style netlist-derived C simulator for `abr_wrap`. The Verilator flow above stays the primary trace generator; presi targets a self-contained ANSI-C executable that drives the same AHB transactions against a gate-level translation, with the heavy engines (`abr_sampler_top` with SHA3, `ntt_top`, `abr_seq` ROM) co-simulated as separate gate netlists.  Behavioural modelling is **not** the design point — leakage analysis (TVLA) requires real-gate toggle activity, so cryptographic engines are kept as gates.

The plan and current status live in `presi/plan.md`; this section just sketches build entry points.

**State representation: flat byte array per netlist (2026-05-08).** Each gate netlist allocates one `presi_t <prefix>presi_s[N]` array; cells emit `presi_s[<idx>] = ...`.  Indices are stable, recorded in `<top>.presi_map.csv` (idx, spice_name, c_name).  Named-port access via `<top>.presi_idx.h` (`#define IDX_<c_name> <idx>`).  The earlier "one extern per net" layout produced 191 MB var.h files and crippled gcc -O1; the array layout shrinks the cosim binary from 786 MB → 258 MB and brings -O1 within budget.  Per-cycle TVLA toggle counting is `__builtin_popcount(s[i] ^ s_prev[i])` — trivial.

Build entry points:

- `make -C presi sv2v` — sv2v normalize the upstream + local RTL into `_build/abr_wrap.sv2v.v`.
- `make -C presi netlist-blackbox` — Yosys hierarchical netlist with the ten ABR SRAMs blackboxed; feeds `extract_sram_meta.py` → `_build/abr_wrap.sram.h`.
- `make -C presi netlist-gates` — abr_wrap gates SPICE (`_build/abr_wrap.gates.sp`, ~250 MB, ~4.79 M cells).  Three engines stay blackboxed at the abr_wrap level (`abr_sampler_top`, `ntt_top`, `abr_seq`); the other 12 plus `ntt_twiddle_lookup` are gate-mapped inline.  Yosys ≥ 0.64, ~3 min wall, ~7 GiB peak.
- `make -C presi seq-rom` — slice `abr_seq` from sv2v.v (`flow/extract_abr_seq.py`), run Yosys, reassemble the full 87-bit ROM table (`flow/extract_seq_rom.py`).  Output: `_build/abr_wrap.seq_rom.h` (1024 × 87-bit table).
- `make -C presi ntt-top` / `make -C presi abr-sampler-top` / `make -C presi abr-seq-core` — per-engine gate flows.  Each runs Yosys with `engine-gates` mode (no blackboxing -- the engine itself is gate-mapped) on `--top <engine>`, then `spice_to_c.py` with `--symbol-prefix='<engine>__'` so the per-engine arrays are link-compatible with abr_wrap.  ntt_top: 2.07 M cells, 1m06s; abr_sampler_top: 2.06 M cells, 1m32s; abr_seq: trivial (1 NOT + 1 $mem_v2).
- `make -C presi engine-glue` — generates `<engine>.glue.c` files via `gen_engine_glue.py`; each file copies abr_wrap-side bb-pin slots ↔ engine-side port slots through `presi_s[<idx>]` ↔ `<engine>__presi_s[<idx>]`, with literal indices baked in from the map CSVs.  ntt_top: 1934 bits (1484 in / 450 out), abr_sampler_top: 2021 bits (114 in / 1907 out -- sampler_state_data_o alone is 1600 bits of SHA3 squeeze state).
- `make -C presi gate-c` — translates the SPICE into C: a 12-line `<top>.presi_var.h`, ~150-byte `<top>.presi_var.c`, a `<top>.presi_idx.h` (named-port `#define`s), 32 `presi_clk_part_NNN.c` TUs (8 for engines), `presi_map.csv`, `presi_bb.csv`, and `presi_clk.h` dispatcher.
- `make -C presi -j 4 cosim` — clean rebuild ~12 min: builds **`presi-gates-cosim`** (258 MB, links abr_wrap + ntt_top + abr_sampler_top netlists + glue).  After 64 reset cycles plus 64 idle cycles, AHB NAME/VERSION/STATUS reads return the expected hardwired constants.  Writing `MLDSA_CTRL=1` walks the abr_seq ROM through real Dilithium-keygen UOPs (LD_SHAKE256 → SHAKE256 → LFSR → SHAKE256 → REJB s1[0..1]) within a 256-cycle smoke window -- engine handshakes (sampler_busy_o, SHA3 absorb/squeeze, rejection sampling) all running through gates.
- `make -C presi run-cosim` — runs the smoke harness.  ~5 cyc/s with all three netlists co-stepping at -O0.
- `make -C presi run-cosim-keygen [KEYGEN_MAX_CYCLES=N]` — drives full Dilithium keygen: load `seed_in.dat` + optional `ent_in.dat`, write CTRL, poll STATUS for READY|VALID, dump `pk_out.dat` / `sk_out.dat`.  ~1-3 hours wall for a real keygen.
- `make -C presi -j 4 run-gates` — abr_wrap-only build (no engines).  Stalls at MLDSA_KG_S waiting for the unwired engines, but useful as a control-plane smoke test.
- `make -C presi run` — non-netlist harness for sub-second sanity.

`GATES_OPT` Makefile knob defaults to `-O0` (5.7 s per part .c, ~12 min clean rebuild).  `-O1` is tractable post-array-layout (1m57s per part .c, ~24 min rebuild) but isn't the default.

Five load-bearing details that are easy to break:

1. **Yosys ≥ 0.64 is required.**  Older 0.36's `proc` is 5–10× slower on this design (25+ minute hangs on the `proc_mux` step over abr_ctrl's FSM).  The newer release also has lower peak memory.
2. **Use `opt`, not `opt_clean`, after `proc` in the gates flow.** sv2v leaves parameter expressions like `$clog2(MLDSA_Q)+1` as runtime arithmetic.  Without full `opt`, abr_wrap carries ~1200 spurious `$mul` cells that techmap then expands to millions of gates and OOMs the box.
3. **sv2v `--top abr_wrap` inlines `abr_top` + `abr_ctrl` + `abr_mem_top`** into one flat `abr_wrap` module (they communicate via SV interfaces `abr_mem_if` / `abr_sram_if`, which sv2v collapses).  They cannot be blackboxed individually.  This is fine for presi — that's the layer we want gate-mapped — but it's why `gen_yosys.py`'s `ENGINE_MODULES` blackboxes engines at the `abr_top`-instantiation boundary instead.  `abr_seq` is on that list too because `proc` doesn't terminate on its 1024-way `unique case`; ROM contents come from a separate `make seq-rom` target.
4. **No ABC, no BUF/NAND lowering.** Both ran the inlined abr_wrap into swap.  `spice_to_c.py` handles Yosys's gate primitives directly.
5. **DFFs are edge-triggered with `presi_clk_prev`, comb is topo-sorted.**  Each `$_DFF_P_`/`$_DFFSR_PPP_` cell emits as `if ((presi_s[<clk>] & ~presi_clk_prev & 1)) presi_s[<Q>] = presi_s[<D>];` so the flop only ticks on a true 0→1 transition regardless of how many `presi_step_netlist()` calls happen per phase.  Combinational statements within a part file are ordered by dataflow (Kahn's topo sort over (writer, reader) of comb nets, with reads of flop outputs treated as stable inputs).  Together these give a correct one-step-per-phase cycle without re-clocking the flops or reading stale comb values.

Don't add an `if (!busy_o) return;` skip to `presi_engines_step()`.  That gate stalls the engines on the cycle after `MLDSA_CTRL=1` was written -- `busy_o` lags by one cycle, so the engines miss the first edge where the controller drives sampler inputs.  Re-introduce only with explicit clk_prev maintenance during the skipped phases.

## Default file conventions

`abr_wrap` uses fixed default filenames so scripts can chain without flags. Override any of them with the matching flag.

ML-DSA: `pk_in.dat`/`pk_out.dat` (`-pk`), `sk_in.dat`/`sk_out.dat` (`-sk`), `sig_in.dat`/`sig_out.dat` (`-sig`), `hash_in.dat` (`-hash`), `seed_in.dat` (`-seed`), `rnd_in.dat` (`-rnd`), `mu_in.dat` (`-mu`, external-µ), `strm_in.dat` (`-strm`, stream-msg variable-length payload), `-vfy` (verify result block, default off).

ML-KEM: `seed_d_in.dat` (`-d`), `seed_z_in.dat` (`-z`), `msg_in.dat` (`-msg`), `ek_in.dat`/`ek_out.dat` (`-ek`), `dk_in.dat`/`dk_out.dat` (`-dk`), `ct_in.dat`/`ct_out.dat` (`-ct`), `ss_out.dat` (`-ss`).

Shared: `ent_in.dat` (`-ent`, optional masking entropy at `ABR_ENTROPY=0x18`), VCD output via `-vcd <fn>` (off by default), cycle timeout via `-t <n>`.
