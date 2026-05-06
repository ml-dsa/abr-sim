#!/usr/bin/env python3

import argparse
import os
import subprocess


def resolve_path(path, base_dir):
    if os.path.isabs(path):
        return path
    return os.path.normpath(os.path.join(base_dir, path))


def parse_vf(path, base_dir):
    incdirs = []
    files = []
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            if line.startswith("+incdir+"):
                incdirs.append(resolve_path(line[len("+incdir+"):], base_dir))
            else:
                # The abr-sim Verilator file list swaps in a local abr_seq.sv
                # with debug-print tasks. Those tasks are useful for RTL runs
                # but are not accepted by Yosys, so presi uses the upstream
                # synthesizable source and drops the matching decoder hook.
                if line == "rtl/abr_seq.sv":
                    line = "adams-bridge/src/abr_top/rtl/abr_seq.sv"
                elif line == "rtl/abr_seq_decode.sv":
                    continue
                files.append(resolve_path(line, base_dir))
    return incdirs, files


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", default="abr_wrap")
    ap.add_argument("--vf", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--base", default=None,
                    help="Directory for repo-relative file-list entries")
    args = ap.parse_args()

    if args.base is None:
        base_dir = os.path.normpath(os.path.join(os.path.dirname(args.vf), ".."))
    else:
        base_dir = args.base

    incdirs, files = parse_vf(args.vf, base_dir)
    cmd = ["sv2v", "-D", "SYNTHESIS", "-D", "YOSYS", "--top", args.top]
    for incdir in incdirs:
        cmd.append("-I" + incdir)
    cmd.extend(files)
    cmd.extend(["-w", args.out])

    print("sv2v: %d include dirs, %d files -> %s" %
          (len(incdirs), len(files), args.out))
    subprocess.check_call(cmd)


if __name__ == "__main__":
    main()
