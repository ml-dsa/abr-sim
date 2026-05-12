#!/usr/bin/env python3
"""Diff two [seq]/[eng] trace logs (Verilator vs presi).

Both inputs should be the output of `<binary> ... mlkem-keygen` filtered
with `grep -E '\\[(seq|eng)\\]'`.

Reports:
  * cycle alignment table for each FSM PC transition
  * for any pc transition with a non-zero Δcycle gap, dumps the [eng]
    line at each side's transition cycle so the proximate-cause
    signals can be eyeballed.
"""

import argparse
import re
import sys

SEQ_RE = re.compile(r"#\s*(\d+)\s+\[seq\]\s+(\d+):")
SEQ_PRESI_RE = re.compile(r"\[seq\]\s+cyc=(\d+)\s+pc=(\d+)")
ENG_RE = re.compile(
    r"#?\s*(\d+)\s+\[eng\]\s+"
    r"ntt_busy=(\d).*sampler_busy=(\d).*sampler_dv=(\d).*"
    r"sha3_dv=(\d).*busy_o=(\d)")
ENG_PRESI_RE = re.compile(
    r"\[eng\]\s+cyc=(\d+)\s+"
    r"ntt_busy=(\d)\s+sampler_busy=(\d)\s+sampler_dv=(\d)\s+"
    r"sha3_dv=(\d)\s+busy_o=(\d)")


def parse_log(path):
    seqs = []  # list of (cyc, pc)
    engs = {}  # cyc -> (ntt_busy, smp_busy, smp_dv, sha3_dv, busy_o)
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            m = SEQ_RE.search(line)
            if m:
                seqs.append((int(m.group(1)), int(m.group(2))))
                continue
            m = SEQ_PRESI_RE.search(line)
            if m:
                seqs.append((int(m.group(1)), int(m.group(2))))
                continue
            m = ENG_RE.search(line)
            if m:
                engs[int(m.group(1))] = tuple(int(m.group(i)) for i in range(2, 7))
                continue
            m = ENG_PRESI_RE.search(line)
            if m:
                engs[int(m.group(1))] = tuple(int(m.group(i)) for i in range(2, 7))
    return seqs, engs


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("verilator")
    ap.add_argument("presi")
    ap.add_argument("--pc-range", default="443-486",
                    help="pc range to show (start-end)")
    args = ap.parse_args()

    lo, hi = (int(x) for x in args.pc_range.split("-"))

    v_seqs, v_engs = parse_log(args.verilator)
    p_seqs, p_engs = parse_log(args.presi)

    # Build pc -> first transition cycle.
    def first_pc(seqs):
        out = {}
        for cyc, pc in seqs:
            if pc not in out:
                out[pc] = cyc
        return out

    v_first = first_pc(v_seqs)
    p_first = first_pc(p_seqs)

    print("# pc transition cycles (cycle of first appearance of pc)")
    print("# %-3s  %-8s  %-8s  %-8s  %-8s" %
          ("pc", "V_cyc", "P_cyc", "Δabs", "Δdur"))
    prev_v = prev_p = None
    for pc in sorted(set(v_first) | set(p_first)):
        if not (lo <= pc <= hi):
            continue
        vc = v_first.get(pc, -1)
        pc2 = p_first.get(pc, -1)
        dab = (pc2 - vc) if (vc >= 0 and pc2 >= 0) else None
        ddur = None
        if prev_v is not None and prev_p is not None and vc >= 0 and pc2 >= 0:
            dv = vc - prev_v
            dp = pc2 - prev_p
            ddur = dp - dv
        print("  %-3d  %-8s  %-8s  %-8s  %-8s" %
              (pc, vc if vc >= 0 else "-",
               pc2 if pc2 >= 0 else "-",
               dab if dab is not None else "-",
               ddur if ddur is not None else "-"))
        prev_v, prev_p = vc, pc2

    print()
    print("# engine-handshake values at pc transitions (column order: "
          "ntt_busy sampler_busy sampler_dv sha3_dv busy_o)")

    def show(cyc, engs):
        if cyc not in engs:
            # try nearest <= cyc
            cands = [c for c in engs if c <= cyc]
            if not cands:
                return "(no eng line)"
            cyc = max(cands)
        e = engs[cyc]
        return "ntt=%d smp=%d smp_dv=%d sha3_dv=%d busy=%d" % e

    print("# %-3s  %-30s  %-30s" % ("pc", "Verilator @V_cyc", "presi @P_cyc"))
    for pc in sorted(set(v_first) | set(p_first)):
        if not (lo <= pc <= hi):
            continue
        vc = v_first.get(pc, -1)
        pc2 = p_first.get(pc, -1)
        v_str = show(vc, v_engs) if vc >= 0 else "(no V trans)"
        p_str = show(pc2, p_engs) if pc2 >= 0 else "(no P trans)"
        print("  %-3d  %-30s  %-30s" % (pc, v_str, p_str))


if __name__ == "__main__":
    sys.exit(main())
