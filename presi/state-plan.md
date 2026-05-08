# presi state-snapshot + abr_wrap-aligned CLI

## Why

The cosim runs at order-of-magnitude 5 cyc/s.  A full Dilithium keygen
is 20-50 k cycles; pk/sk readout via AHB is another 5 k+ cycles; so an
end-to-end run is 1-3 hours.  We do not want any single experiment in
the iteration loop to run longer than 5 minutes.

State snapshotting cuts this two ways:

- **Skip the AHB load.**  Inputs like seed/entropy land in registers
  via 24 AHB writes (~75 cycles).  An offline tool that builds a
  *post-load* state lets the main `run` loop start from the moment of
  `MLDSA_CTRL` write.  Saves seconds, not hours, but the *real* win is
  separation of concerns: the heavy logic-stepping core has no AHB
  driver baked in.
- **Skip the AHB readout.**  pk/sk live in `pk_mem` and `sk_mem_bank0/1`
  -- those are C-side `presi_sram_t` arrays.  Once a snapshot captures
  them, a separate `dump-pk` / `dump-sk` reads them directly without
  spinning the RTL another 5 k cycles.
- **TVLA (future).**  Generate N fixed-input snapshots + M
  random-input snapshots once (cheap, AHB-only).  Each `run` is
  embarrassingly parallel; toggle counting is one
  `__builtin_popcount(presi_s[i] ^ snapshot[i])` loop per cycle.

## CLI: mirror abr_wrap

The existing project-root tool `abr_wrap` (`src/abr_wrap.cpp`) has a
mature CLI:

```
abr_wrap [options] [operation]

operation := mldsa-keygen | mldsa-sign | mldsa-verify | mldsa-kgsign |
             mldsa-sign-extmu | mldsa-sign-stream |
             mlkem-keygen | mlkem-encaps | mlkem-decaps | mlkem-kgdecaps |
             keygen | sign | verify | kgsign  (aliases)

options    := -t <n>      cycle timeout
              -vcd <fn>   (Verilator-only, ignored by presi)
              -pk <fn>    pubkey  (in/out)
              -sk <fn>    privkey (in/out)
              -sig <fn>   signature (in/out)
              -hash <fn>  message hash (in)
              -seed <fn>  ML-DSA seed (in)
              -rnd <fn>   sign randomness (in)
              -ent <fn>   entropy (in, optional)
              -mu <fn>    external mu (in)
              -strm <fn>  stream-msg payload (in)
              -d / -z / -msg / -ek / -dk / -ct / -ss   ML-KEM file slots
```

The presi CLI mirrors this exactly so the same command lines work
(modulo `-vcd` which is Verilator-only):

```
presi-gates-cosim [options] <operation>

operation := same set as abr_wrap, plus presi-only:
             smoke    (default; existing 256-cycle FSM probe)
             init     (reset + AHB load + CTRL write; save snapshot; exit)
             run      (load snapshot, step cycles, save snapshot)
             dump-pk  (load snapshot, AHB-read MLDSA_PUBKEY, write file)
             dump-sk  (load snapshot, AHB-read MLDSA_PRIVKEY_OUT, write file)

options    := all abr_wrap options above (-vcd silently accepted+ignored)
              plus:
              -load <fn>      load snapshot (skips reset+AHB-init)
              -save <fn>      save snapshot at end
              -init-only      same as `init` form, but as a flag on a real op
              -no-output      skip writing pk/sk/sig output files
```

Examples:

```sh
# Today's flow, abr_wrap-equivalent invocation:
./presi-gates-cosim -seed seed_in.dat -ent ent_in.dat \
                    -pk pk_out.dat -sk sk_out.dat -t 200000 mldsa-keygen

# Build a snapshot at the moment of CTRL=KEYGEN:
./presi-gates-cosim -seed seed_in.dat -ent ent_in.dat \
                    -save kg-init.bin mldsa-keygen -init-only

# Advance a snapshot by 1000 cycles:
./presi-gates-cosim -load kg-init.bin -save kg-1k.bin -t 1000 run

# Fast pk readout from a finished snapshot:
./presi-gates-cosim -load kg-done.bin -pk pk_out.dat dump-pk
```

`abr_wrap` uses positional `<operation>` last; we follow that.  The
back-compat current `./presi-gates-cosim keygen 200000` form keeps
working since `-t` is honoured even as a positional shortcut (TBD;
either keep the second-positional shortcut or drop it).

## Snapshot file format

Bit-packed, fixed layout for the linked netlist.  Layout-hash
mismatches abort `load` to prevent reading a snapshot built against a
different netlist build.

```
+-------------------------------------------------------------+
| 8B  magic   = "PRESI001"                                    |
| 4B  version = 1                                             |
| 4B  layout_hash (CRC32 of:                                  |
|        PRESI_NETS,                                          |
|        NTT_TOP__PRESI_NETS (or 0),                          |
|        ABR_SAMPLER_TOP__PRESI_NETS (or 0),                  |
|        PRESI_ABR_SRAM_COUNT,                                |
|        for each SRAM: depth, data_width, byte_enable, name) |
+-------------------------------------------------------------+
| 8B  cycle counter                                           |
| sizeof(struct presi_ports)  port snapshot (model.p)         |
+-------------------------------------------------------------+
| sections (each: 4B tag + 4B length + payload):              |
|   "WRAP"  bit-packed presi_s[PRESI_NETS] + 1B clk_prev      |
|   "NTT_"  ntt_top__presi_s[]            + 1B clk_prev (opt) |
|   "SAMP"  abr_sampler_top__presi_s[]    + 1B clk_prev (opt) |
|   "SRAM"  per-SRAM block:                                   |
|              4B depth                                       |
|              4B data_width                                  |
|              depth * ((data_width+31)/32) * 4 raw bytes     |
|           (sram count is implicit from the descs[] table)   |
|   "END_"  zero-length terminator                            |
+-------------------------------------------------------------+
```

Endianness: little-endian on x86; values written verbatim.  Snapshot
files are not portable across architectures, which is fine since the
linked binary ties them anyway.

Bit packing: byte `i` bit `b` carries `presi_s[8*i + b] & 1`.

Estimated size:
- abr_wrap: ~3-5 M nets ⇒ 400-600 KB
- ntt_top:  ~2 M nets   ⇒ ~250 KB
- sampler:  ~2 M nets   ⇒ ~250 KB
- SRAMs (10 instances, ~150 KB total)
- Total: ~1-1.5 MB per snapshot.

## Subcommand semantics

`init <op>`
:   Equivalent to `<op> -init-only`.  Allocate model, run reset,
    apply AHB writes for `<op>`'s inputs (from `<x>_in.dat` files
    or `-<x>` overrides), AHB-write CTRL register.  Then save state
    via `-save` and exit.

`run`
:   Requires `-load`.  Loads state, advances `-t` cycles via
    `presi_cycle()` (no AHB driver, `hsel_i=0`, `htrans_i=IDLE`),
    optionally saves via `-save`.  Used to chunk a long operation
    into 5-minute windows or to drive TVLA snapshots.

`dump-pk` / `dump-sk` / etc.
:   Requires `-load`.  Loads state and uses AHB to read out the
    relevant register region, writing to the file from
    `-pk` / `-sk` / etc.  AHB-readout speeds:
      pk = 648 words ≈ 2 k cycles ≈ 7 min @ 5 cyc/s (slow)
      sk = 1224 words ≈ 4 k cycles ≈ 13 min (slow)
    A future optimisation reads pk_mem / sk_mem_bank0/1 directly
    from `model.srams[]`.  Out of scope for this round; we settle
    on getting the snapshot mechanism right first.

`<op>` (no -init-only, no -load)
:   Today's flow: reset + AHB inputs + CTRL + wait + AHB outputs.
    Plus optional `-save` at end.

`<op> -load <fn>`
:   Skip reset + AHB-input-load.  Resume from snapshot.  Useful to
    run from a snapshot that already has `CTRL=KEYGEN` written and
    finish the wait + readout.

## Implementation phases

1. ~~**Snapshot save/load**~~ — done.  `presi_state.c` + header in the
   library; bit-packed format with FNV-1a layout hash.

2. ~~**CLI refactor**~~ — done.  abr_wrap-style argv parser in
   `presi.c`; mldsa-keygen wired, other abr_wrap ops recognised
   with "not yet wired" message.

3. ~~**Phase split for mldsa_keygen**~~ — done in `presi_gates.c`:
   `mldsa_keygen_init` / `_run` / `_finish` callable independently.

4. ~~**`-load` / `-save` hooks**~~ — done.  `-load` replaces reset;
   `-save` is called after the op (only on rc==0).
   `presi_settle_after_load()` runs one comb pass post-load so
   combinational wires are consistent with the loaded flop state.

5. **Library + single binary** — done.  `_build/libpresi_gates.a`
   bundles all gate-netlist .o files and the cosim-flavor harness
   .o files (`presi_gates.cosim.o`, `presi_state.cosim.o`,
   `presi_sram.o`).  `presi-cosim` links against the archive;
   editing `presi.c` only triggers a single .o recompile + relink.

6. **Smoke validation** — pending first cosim build with the
   refactor.  Three round-trip tests:
   a. Save → load → save: byte-identical second save.
   b. `init` then `run -t N` ≡ a one-shot run of N+init cycles.
   c. `run -t 0` immediately after `load` is a no-op (model state
      unchanged).

## Risks / gotchas

- **Layout hash invalidation.**  Rebuild netlist with different
  `PRESI_NETS` ⇒ old snapshots reject cleanly.
- **Phase boundary.**  Always save at the end of the `clk=1` step
  in `presi_cycle()` (after `presi_capture_outputs`).  Document so
  TVLA workers don't accidentally save mid-phase.
- **Mid-AHB.**  `model.p` snapshot captures AHB ports too, so a
  snapshot taken mid-`ahb_write` resumes mid-write.  Untested, but
  no reason it shouldn't work.
- **Cycle counter.**  Saved + restored verbatim so `[STAT]` /
  `[FSM]` log lines stay numbered consistently across save/load.
- **Bit-packing portability.**  Files are little-endian, x86-only.
  Embed enough headers that future portability fix-ups have
  something to dispatch on.

## Where this sits relative to plan.md

Replaces "5. Stage 5 — first end-to-end Dilithium keygen byte-compare"
as the next milestone.  Once snapshots work, the byte-compare
becomes:

```sh
# Reference (Verilator):  ~minutes
./abr_wrap mldsa-keygen
mv pk_out.dat sk_out.dat verilator/

# Snapshot-driven path:
./presi-gates-cosim -seed seed_in.dat -ent ent_in.dat \
                    -save kg-init.bin mldsa-keygen -init-only
# loop in 5-min chunks until READY|VALID:
./presi-gates-cosim -load kg-init.bin   -save kg-1.bin -t 5000 run
./presi-gates-cosim -load kg-1.bin      -save kg-2.bin -t 5000 run
...
./presi-gates-cosim -load kg-done.bin -pk pk_out.dat dump-pk
./presi-gates-cosim -load kg-done.bin -sk sk_out.dat dump-sk
diff pk_out.dat verilator/pk_out.dat
diff sk_out.dat verilator/sk_out.dat
```
