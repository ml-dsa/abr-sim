#!/usr/bin/env python3

import argparse


SRAM_MODULES = [
    "abr_1r1w_ram",
    "abr_1r1w_be_ram",
]

# Modules blackboxed only in `gates` mode.  Keeping these as RTL would push the
# gate netlist past available memory during ABC.  The presi C harness models
# them behaviorally; per-engine gate-level builds (for leakage analysis of one
# engine at a time) are a separate target.
#
# The list mirrors abr_top's submodule instantiations: every engine instantiated
# directly by abr_top is here, plus ntt_twiddle_lookup (a 4 x 85-entry ROM
# expanded inside ntt_top).  What stays gate-mapped: abr_ctrl, abr_seq, abr_reg,
# abr_prim_lfsr, and the abr_top dispatcher logic itself.
ENGINE_MODULES = [
    "ntt_twiddle_lookup",
    "abr_sampler_top",
    "ntt_top",
    "power2round_top",
    "decompose",
    "skencode",
    "skdecode_top",
    "makehint",
    "norm_check_top",
    "sigencode_z_top",
    "pkdecode",
    "sigdecode_z_top",
    "sigdecode_h",
    "compress_top",
    "decompress_top",
]


def emit_common(f, args):
    if args.mode == "gates":
        f.write("read_verilog -lib %s/cmos_cells.v\n" % args.flow_dir)
    f.write("read_verilog -sv %s\n" % args.in_file)
    if args.mode in ("blackbox-sram", "gates"):
        for mod in SRAM_MODULES:
            f.write("blackbox %s\n" % mod)
    if args.mode == "gates":
        for mod in ENGINE_MODULES:
            f.write("blackbox %s\n" % mod)
    f.write("hierarchy -check -top %s\n" % args.top)
    f.write("proc\n")
    # opt (not opt_clean) folds parameter expressions like $clog2(Q)+1 that
    # sv2v leaves as runtime arithmetic.  Without this, the gates flow
    # techmaps thousands of $mul/$shift/$neg cells that should have been
    # constants, blowing past available RAM during ABC.
    f.write("opt\n")


def emit_finish(f, args):
    if args.mode == "gates":
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
    ap.add_argument("--mode", choices=["coarse", "blackbox-sram", "gates"],
                    default="coarse")
    ap.add_argument("--in", dest="in_file", required=True)
    ap.add_argument("--out", dest="out_file", required=True)
    ap.add_argument("--stat", required=True)
    ap.add_argument("--json-out", default=None)
    ap.add_argument("--spice-out", default=None)
    ap.add_argument("--flow-dir", default="flow")
    ap.add_argument("--script", required=True)
    args = ap.parse_args()
    if args.mode == "gates" and args.spice_out is None:
        raise SystemExit("--spice-out is required for gates mode")

    with open(args.script, "w", encoding="utf-8") as f:
        emit_common(f, args)
        emit_finish(f, args)


if __name__ == "__main__":
    main()
