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

`presi/` is a parallel work-in-progress: an `xpresi`-style netlist-derived C simulator for `abr_wrap`. The Verilator flow above stays the primary trace generator; presi targets a self-contained ANSI-C executable that drives the same AHB transactions against a gate-level translation of the ABR control plane, with the heavy engines (NTT, sampler, encoders/decoders, etc.) and SRAMs modelled behaviorally in C.

The plan and current status live in `presi/plan.md` (read it before extending the flow). Build entry points:

- `make -C presi sv2v` — sv2v normalize the upstream + local RTL into `_build/abr_wrap.sv2v.v`.
- `make -C presi netlist-blackbox` — Yosys hierarchical netlist with the ten ABR SRAMs blackboxed; feeds `extract_sram_meta.py` → `_build/abr_wrap.sram.h`.
- `make -C presi netlist-gates` — full gate-level SPICE (`_build/abr_wrap.gates.sp`, ~209 MB, ~4.1 M cells) of `abr_wrap`'s control plane. Takes ~150 s, peaks ~10 GiB. Runs `proc; opt; … ; simplemap; dfflegalize` and emits Yosys's gate primitives (`$_AND_`, `$_OR_`, `$_NOT_`, `$_XOR_`, `$_MUX_`, `$_DFF_P_`, `$_DFFSR_PPP_`) directly — no ABC, no BUF/NAND lowering.
- `make -C presi gate-c` — translates the SPICE into C: a self-contained `presi_var.h`/`.c` pair (one extern + one definition per net), 32 `presi_clk_part_NNN.c` translation units splitting the 4.3 M-statement cycle update, a `presi_bb.csv` pin map for the 25 blackbox subcircuit instances (10 SRAMs, 14 engines, 1 sequencer ROM), and `presi_bb_wiring.h` (one block per SRAM, samples we/addr/data from netlist → calls `presi_sram_*` → writes rdata back).
- `make -C presi -j 4 run-gates` — compiles + links the full netlist binary (≈270 MB `presi-gates`) and runs it. ≈3 min on -j 4; needs ≈3 GB per parallel cc1 (a single-function compile is impractical, hence the 32-part split). After 64 reset cycles + 64 idle cycles, AHB reads of MLDSA_NAME[0..1], MLDSA_VERSION[0..1], and MLDSA_STATUS return the expected hardwired constants and the READY bit — end-to-end signal path through the netlist (top-level ports → control plane → register file → AHB slave → harness) works.  The 14 engine blackboxes and the abr_seq sequencer ROM (`_mem_v2`) are still stubs, so anything that needs an opcode fetch (writes to MLDSA_CTRL etc.) still has no behaviour.
- `make -C presi run` — builds and runs the harness without the netlist (faster smoke test).

Three load-bearing details that are easy to break:

1. **Use `opt`, not `opt_clean`, after `proc`.** sv2v leaves parameter expressions like `$clog2(MLDSA_Q)+1` as runtime arithmetic. Without full `opt`, abr_wrap carries ~1200 spurious `$mul` cells that techmap then expands to millions of gates and OOMs the box.
2. **sv2v `--top abr_wrap` inlines `abr_top` + `abr_ctrl` + `abr_mem_top`** into one flat `abr_wrap` module (they communicate via SV interfaces `abr_mem_if` / `abr_sram_if`, which sv2v collapses). They cannot be blackboxed individually. This is fine for presi — that's the layer we want gate-mapped — but it is the reason `presi/flow/gen_yosys.py`'s `ENGINE_MODULES` blackboxes engines at the `abr_top`-instantiation boundary instead.
3. **No ABC, no BUF/NAND lowering.** Both ran the inlined abr_wrap into swap. The translator (`presi/flow/spice_to_c.py`) is expected to handle Yosys's gate primitives directly.

## Default file conventions

`abr_wrap` uses fixed default filenames so scripts can chain without flags. Override any of them with the matching flag.

ML-DSA: `pk_in.dat`/`pk_out.dat` (`-pk`), `sk_in.dat`/`sk_out.dat` (`-sk`), `sig_in.dat`/`sig_out.dat` (`-sig`), `hash_in.dat` (`-hash`), `seed_in.dat` (`-seed`), `rnd_in.dat` (`-rnd`), `mu_in.dat` (`-mu`, external-µ), `strm_in.dat` (`-strm`, stream-msg variable-length payload), `-vfy` (verify result block, default off).

ML-KEM: `seed_d_in.dat` (`-d`), `seed_z_in.dat` (`-z`), `msg_in.dat` (`-msg`), `ek_in.dat`/`ek_out.dat` (`-ek`), `dk_in.dat`/`dk_out.dat` (`-dk`), `ct_in.dat`/`ct_out.dat` (`-ct`), `ss_out.dat` (`-ss`).

Shared: `ent_in.dat` (`-ent`, optional masking entropy at `ABR_ENTROPY=0x18`), VCD output via `-vcd <fn>` (off by default), cycle timeout via `-t <n>`.
