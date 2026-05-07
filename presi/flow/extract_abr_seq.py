#!/usr/bin/env python3

# Slice the abr_seq SystemVerilog module out of the full sv2v output and
# write it to its own .v file.  Used by the Makefile's standalone abr_seq
# build, which runs Yosys on just abr_seq to extract the ROM contents
# without paying the cost of `proc`-elaborating the rest of abr_wrap.
#
# Why this is safe: sv2v inlines all package localparams and parameter
# expressions before emitting Verilog, so the abr_seq module in the sv2v
# output is fully self-contained -- no external imports or pkg references.
#
# Why we don't read the original RTL: abr_params_pkg.sv contains
# multi-dimensional packed parameter declarations that Yosys's Verilog
# frontend cannot parse without sv2v.

import argparse
import re


MODULE_RE = re.compile(r"^module\s+([A-Za-z_][A-Za-z_0-9]*)\b")
ENDMODULE_RE = re.compile(r"^endmodule\b")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_file", required=True,
                    help="full sv2v.v input")
    ap.add_argument("--top", default="abr_seq",
                    help="module to extract (default: abr_seq)")
    ap.add_argument("--out", required=True,
                    help="output .v file with just <top>")
    args = ap.parse_args()

    with open(args.in_file, "r", encoding="utf-8") as f:
        lines = f.readlines()

    start = None
    end = None
    for i, line in enumerate(lines):
        m = MODULE_RE.match(line)
        if m and m.group(1) == args.top:
            start = i
        elif start is not None and ENDMODULE_RE.match(line):
            end = i
            break

    if start is None:
        raise SystemExit("module %r not found in %s" % (args.top, args.in_file))
    if end is None:
        raise SystemExit("endmodule for %r not found" % args.top)

    with open(args.out, "w", encoding="utf-8") as f:
        f.write("// Sliced from %s by extract_abr_seq.py\n" % args.in_file)
        f.writelines(lines[start:end + 1])
    print("extract-abr-seq: %s lines %d..%d -> %s" %
          (args.top, start + 1, end + 1, args.out))


if __name__ == "__main__":
    main()
