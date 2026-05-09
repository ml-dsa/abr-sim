#!/usr/bin/env python3

import argparse
import re
import subprocess


SRAM_MODULES = [
    "abr_1r1w_ram",
    "abr_1r1w_be_ram",
]


def discover_sram_paramods(in_file, flow_dir, top):
    """Run a quick Yosys probe to elaborate paramod variants of the
    SRAM modules.  Returns a list of fully-qualified paramod module
    names (e.g. `$paramod$<hash>\\abr_1r1w_ram`) corresponding to the
    actual SRAM instance widths used in the design.

    Used by `gates` mode: hierarchy creates per-instance paramod
    variants of `abr_1r1w_ram` / `abr_1r1w_be_ram` with proper
    DEPTH/DATA_WIDTH; we then blackbox each variant by exact name in
    the main pass so subsequent passes see SRAM port stubs at full
    widths instead of the default-truncated DEPTH=64/DATA_WIDTH=32.

    The probe takes ~7 s (read_verilog + hierarchy only).  Hashes are
    deterministic for a given SystemVerilog source, so they're stable
    across runs as long as the upstream RTL doesn't change.
    """
    probe = (
        "read_verilog -lib %s/cmos_cells.v\n"
        "read_verilog -sv %s\n"
        # Engines blackboxed pre-hierarchy (matches gates mode below).
        "blackbox abr_sampler_top\n"
        "blackbox ntt_top\n"
        "blackbox abr_seq\n"
        "hierarchy -check -top %s\n"
        "ls\n"
    ) % (flow_dir, in_file, top)
    # No `-q`: with -q yosys silences log output, including the
    # `ls` command's module list, leaving us with nothing to grep.
    out = subprocess.run(
        ["yosys", "-p", probe],
        capture_output=True, text=True, check=False,
    )
    if out.returncode != 0:
        raise SystemExit("yosys probe failed:\n" + out.stderr[-2000:])
    pat = re.compile(
        r"^\s+(\$paramod\$[0-9a-f]+\\(?:abr_1r1w_ram|abr_1r1w_be_ram))$",
        re.M,
    )
    names = pat.findall(out.stdout)
    if not names:
        raise SystemExit(
            "yosys probe found no abr_1r1w_ram* paramod variants -- "
            "the SRAM-truncation post-hierarchy blackbox approach "
            "requires hierarchy to specialise per-instance widths.")
    return sorted(set(names))

# Modules blackboxed only in `gates` mode.  Keeping these as RTL would push the
# gate netlist past available memory during ABC, or stall `proc` for tens of
# minutes (abr_seq's 1024-way unique case statement).  The presi C harness
# models them behaviorally; per-engine gate-level builds (for leakage analysis
# of one engine at a time) are a separate target.
#
# What stays gate-mapped: abr_ctrl, abr_reg, abr_prim_lfsr, and the abr_top
# dispatcher logic itself.  `abr_seq` is blackboxed because `proc` choked on
# its giant case statement; ROM contents come from a quick standalone Yosys
# run on abr_seq alone (extract_seq_rom.py reads its $mem_v2 INIT param) and
# the gates flow drives the abr_seq blackbox's 87-bit data_o port from the
# extracted table.
ENGINE_MODULES = [
    # Engines un-blackboxed 2026-05-07 (gate-mapped for real-engine
    # leakage analysis -- the project's actual goal):
    #   ntt_twiddle_lookup  (4 x 85 x 23-bit ROM)
    #   power2round_top, decompose, skencode, skdecode_top, makehint,
    #   norm_check_top, sigencode_z_top, pkdecode, sigdecode_z_top,
    #   sigdecode_h, compress_top, decompress_top
    # These are 120-400 LoC per-coefficient functions / bit packers
    # and stay cheap to elaborate.
    #
    # Still blackboxed:
    #   abr_sampler_top -- contains SHA3/Keccak + samplers.  Measured
    #     2026-05-07: yosys netlist-gates over the 5-min hard cap.
    #   ntt_top -- NTT butterfly network.  Yosys fits (4m10s) but
    #     measured 2026-05-07: adding it brings the netlist from 4.79 M
    #     to 6.86 M cells (+43%), which inflates spice_to_c output to
    #     ~30-50 MB per part .c file and blows the gcc -O0 compile
    #     past the 5-min budget (full clean rebuild ~17 min).
    #   abr_seq -- 1024-way unique-case ROM that stalls `proc` for
    #     >25 min.  ROM contents come from a separate `make seq-rom`
    #     standalone Yosys pass.
    # Use per-engine gate flows (analogous to `make seq-rom`) for SHA3
    # and NTT leakage analysis -- they are too heavy for the unified
    # abr_wrap flow on a 5-min iteration budget.
    "abr_sampler_top",
    "ntt_top",
    "abr_seq",
]


def emit_common(f, args):
    if args.mode in ("gates", "engine-gates"):
        f.write("read_verilog -lib %s/cmos_cells.v\n" % args.flow_dir)
    f.write("read_verilog -sv %s\n" % args.in_file)

    # blackbox-sram mode: pre-hierarchy SRAM blackbox (works for the
    # netlist-blackbox flow where `extract_sram_meta.py` reads the
    # SystemVerilog parameter expressions directly off the cell
    # instantiation).  No paramod specialisation is needed here.
    if args.mode == "blackbox-sram":
        for mod in SRAM_MODULES:
            f.write("blackbox %s\n" % mod)
        for mod in ENGINE_MODULES:
            f.write("blackbox %s\n" % mod)

    # gates mode: blackbox engines pre-hierarchy (their bodies aren't
    # needed and would push proc/opt past budget) and then run
    # hierarchy so it creates per-instance paramod variants of
    # abr_1r1w_ram / abr_1r1w_be_ram with their proper widths.  Each
    # paramod variant gets blackboxed by exact name afterwards (the
    # names were probed via discover_sram_paramods() before the
    # script was emitted).  This unwinds the historical "SRAM port-
    # width truncation" limitation: we now expose addr=10/11 and
    # data=96 etc. instead of the default DEPTH=64/DATA_WIDTH=32.
    # Pre-hierarchy `blackbox abr_1r1w_ram` would have prevented
    # specialisation; post-hierarchy `blackbox m:*abr_1r1w_ram*`
    # didn't work because Yosys's m: selection language doesn't
    # match `$paramod$<hash>\\<name>` module names with the leading
    # `$paramod$` segment.
    if args.mode == "gates":
        for mod in ENGINE_MODULES:
            f.write("blackbox %s\n" % mod)
    f.write("hierarchy -check -top %s\n" % args.top)
    if args.mode == "gates":
        for nm in args.sram_paramods:
            # Yosys's command tokeniser uses `\\` to escape special
            # chars; we duplicate to match the actual module name.
            f.write("blackbox %s\n" % nm.replace("\\", "\\\\"))
        # The originals are now unused (every cell instance was
        # specialised to a paramod variant).  Delete them so Yosys
        # doesn't carry their bodies through subsequent passes.
        for mod in SRAM_MODULES:
            f.write("delete %s\n" % mod)

    f.write("proc\n")
    if args.mode in ("gates", "engine-gates"):
        # opt (full, not -fast or opt_clean) folds parameter expressions like
        # $clog2(Q)+1 that sv2v leaves as runtime arithmetic.  Without this,
        # the gates flow techmaps thousands of $mul/$shift/$neg cells that
        # should have been constants, blowing past available RAM during ABC.
        f.write("opt\n")
    # blackbox-sram and coarse modes skip the full `opt` -- it can take 15+
    # minutes on the inlined abr_wrap and isn't needed when there's no
    # techmap.  extract_sram_meta.py and extract_seq_rom.py both work off
    # the post-`proc; memory -nomap; opt_clean` netlist.


def emit_finish(f, args):
    if args.mode in ("gates", "engine-gates"):
        # Custom mapping path that skips ABC and the BUF/NOT/NAND/NOR
        # rewrite.  Both blew the 30 GiB RAM budget on the inlined abr_wrap
        # module (sv2v collapses abr_top + abr_ctrl + abr_mem_top into one
        # giant module because they communicate via SV interfaces).  The
        # presi target only needs a correct gate netlist; spice_to_c.py
        # handles Yosys's $_AND_/$_OR_/$_NOT_/$_XOR_/$_XNOR_/$_NAND_/$_NOR_/
        # $_ANDNOT_/$_ORNOT_/$_MUX_/$_DFF_P_/$_DFFSR_PPP_ primitives
        # directly:
        #   memory -nomap, opt - operator-level fold (the critical step)
        #   techmap            - bit-level operator expansion
        #   simplemap          - normalize to gate primitives
        #   dfflegalize        - canonicalize flops to {$_DFF_P_, $_DFFSR_PPP_}
        f.write("memory -nomap\n")
        f.write("opt\n")
        f.write("techmap\n")
        f.write("opt_clean\n")
        f.write("simplemap\n")
        f.write("dfflegalize -cell $_DFF_P_ x -cell $_DFFSR_PPP_ x\n")
        f.write("opt_clean\n")
        f.write("flatten\n")
        f.write("opt_clean\n")
        f.write("write_spice -neg 0s -pos 1s -top %s %s\n" %
                (args.top, args.spice_out))
    else:
        f.write("memory -nomap\n")
        f.write("opt_clean\n")
    f.write("write_verilog -noattr %s\n" % args.out_file)
    if args.json_out:
        f.write("write_json %s\n" % args.json_out)
    f.write("tee -o %s stat\n" % args.stat)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", default="abr_wrap")
    ap.add_argument("--mode",
                    choices=["coarse", "blackbox-sram", "gates",
                             "engine-gates"],
                    default="coarse")
    ap.add_argument("--in", dest="in_file", required=True)
    ap.add_argument("--out", dest="out_file", required=True)
    ap.add_argument("--stat", required=True)
    ap.add_argument("--json-out", default=None)
    ap.add_argument("--spice-out", default=None)
    ap.add_argument("--flow-dir", default="flow")
    ap.add_argument("--script", required=True)
    args = ap.parse_args()
    if args.mode in ("gates", "engine-gates") and args.spice_out is None:
        raise SystemExit("--spice-out is required for %s mode" % args.mode)

    args.sram_paramods = []
    if args.mode == "gates":
        args.sram_paramods = discover_sram_paramods(
            args.in_file, args.flow_dir, args.top)
        print("gen-yosys: discovered %d SRAM paramod variants:" %
              len(args.sram_paramods))
        for n in args.sram_paramods:
            print("  " + n)

    with open(args.script, "w", encoding="utf-8") as f:
        emit_common(f, args)
        emit_finish(f, args)


if __name__ == "__main__":
    main()
