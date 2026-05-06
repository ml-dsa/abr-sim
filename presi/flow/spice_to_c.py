#!/usr/bin/env python3

# Translate a Yosys SPICE netlist (write_spice -neg 0s -pos 1s) into ANSI-C
# include files for the presi harness.  Inputs:
#   --spice  netlist file
# Outputs:
#   --vars       header with one `extern presi_t <name>;` per net plus a
#                `presi_t` typedef and the PRESI_0/PRESI_1 constants
#   --vars-c     C file with one `presi_t <name>;` definition per net (all
#                variables share external linkage so they can be touched by
#                any of the per-part TUs below)
#   --clock      header included from the harness step function: declares the
#                per-part step functions (extern) and calls them in order
#   --parts-dir  directory for the per-part C files; this script writes
#                `<top>.presi_clk_part_NNN.c` files there
#   --num-parts  number of part files (default 32); the cycle-update body is
#                split evenly across them so gcc never sees a single function
#                with millions of statements
#   --map        CSV of <spice_name>,<c_name>
#   --bb         CSV of blackbox subcircuit instances and their pin
#                connections, one row per pin: <inst>,<module>,<pin_index>,
#                <spice_name>,<c_name>
#
# The presi flow (see flow/gen_yosys.py) emits Yosys's gate primitives directly
# instead of lowering to BUF/NOT/NAND/NOR via a custom techmap, so the
# translator handles the whole simplemap+dfflegalize cell set:
#   $_NOT_, $_AND_, $_OR_, $_NAND_, $_NOR_, $_XOR_, $_XNOR_,
#   $_ANDNOT_, $_ORNOT_, $_MUX_, $_DFF_P_, $_DFFSR_PPP_
# write_spice escapes `$_NAME_` to `__NAME_` (and `$mem_v2` to `_mem_v2`), so
# the SPICE form is what we match.  Anything else is treated as a blackbox
# subcircuit (engines, SRAMs, twiddle ROM, $mem_v2 sequencer ROM).

import argparse
import os


NUM_PARTS_DEFAULT = 32


# Templates: (cell_name, n_pins) -> lambda(pins_as_c_names) -> C statement.
#
# write_spice emits ports in the order it iterates `cell->connections()`.
# When a matching module is in the design (blackbox or library cell),
# write_spice uses the module's port_id order.  Without one it warns
# "Guessing order of ports" and falls back to the cell's own connection
# iteration order, which for simplemap- and dfflegalize-produced cells is
# the *reverse* of insertion order (output first, then inputs reversed).
#
# The presi flow's `(* blackbox *)` declarations in cmos_cells.v get pruned
# by `hierarchy -check` before simplemap runs (they have no instantiations
# at that point), so write_spice always uses the guessed order in practice.
# These templates therefore match the empirically-verified guessed orders:
#
#   $_NOT_(A,Y)              SPICE (Y, A)
#   $_AND_/$_OR_/$_NAND_/    SPICE (Y, B, A)         -- symmetric, irrelevant
#     $_NOR_/$_XOR_/$_XNOR_
#   $_ANDNOT_(A,B,Y) = A&~B  SPICE (Y, B, A)
#   $_ORNOT_(A,B,Y)  = A|~B  SPICE (Y, B, A)
#   $_MUX_(A,B,S,Y) = S?B:A  SPICE (Y, S, B, A)
#
# `__NAME_` is the SPICE form of `$_NAME_` (write_spice replaces the
# leading `$` with `_`).
COMBINATIONAL = {
    # BUF/NOT/NAND/NOR with the original cmos_cells.v declarations follow
    # input-first order (those modules are not pruned because dfflibmap+abc
    # emit them).  Kept for backward compatibility with the abc-based flow.
    ("BUF",  2):       lambda n: "%s = %s;" % (n[1], n[0]),
    ("NOT",  2):       lambda n: "%s = ~%s;" % (n[1], n[0]),
    ("NAND", 3):       lambda n: "%s = ~(%s & %s);" % (n[2], n[0], n[1]),
    ("NOR",  3):       lambda n: "%s = ~(%s | %s);" % (n[2], n[0], n[1]),
    # Yosys gate primitives.  Output first, then inputs in reverse insertion
    # order.  For symmetric operators the input order is irrelevant.
    ("__NOT_",     2): lambda n: "%s = ~%s;" % (n[0], n[1]),
    ("__AND_",     3): lambda n: "%s = %s & %s;" % (n[0], n[2], n[1]),
    ("__OR_",      3): lambda n: "%s = %s | %s;" % (n[0], n[2], n[1]),
    ("__NAND_",    3): lambda n: "%s = ~(%s & %s);" % (n[0], n[2], n[1]),
    ("__NOR_",     3): lambda n: "%s = ~(%s | %s);" % (n[0], n[2], n[1]),
    ("__XOR_",     3): lambda n: "%s = %s ^ %s;" % (n[0], n[2], n[1]),
    ("__XNOR_",    3): lambda n: "%s = ~(%s ^ %s);" % (n[0], n[2], n[1]),
    # SPICE (Y, B, A) maps to Y = A & ~B / Y = A | ~B.
    ("__ANDNOT_",  3): lambda n: "%s = %s & ~%s;" % (n[0], n[2], n[1]),
    ("__ORNOT_",   3): lambda n: "%s = %s | ~%s;" % (n[0], n[2], n[1]),
    # SPICE (Y, S, B, A): Y = S ? B : A.
    ("__MUX_",     4): lambda n:
        "%s = (%s & 1) ? %s : %s;" % (n[0], n[1], n[2], n[3]),
}


def cname(name):
    """Mangle a SPICE net name to a valid C identifier."""
    if name == "0s":
        return "PRESI_0"
    if name == "1s":
        return "PRESI_1"
    out = []
    for ch in name:
        if ch.isalnum():
            out.append(ch)
        else:
            out.append("_")
    s = "".join(out).strip("_")
    if not s:
        s = "unnamed"
    if s[0].isdigit():
        s = "n_" + s
    return s


class NameMap:
    def __init__(self):
        self.names = {}
        self.order = []
        self.set = set()

    def ref(self, name):
        if name in ("0s", "1s"):
            return cname(name)
        c = self.names.get(name)
        if c is not None:
            return c
        c = cname(name)
        base = c
        i = 1
        while c in self.set:
            c = "%s_%d" % (base, i)
            i += 1
        self.names[name] = c
        self.order.append(c)
        self.set.add(c)
        return c

    def add_synthetic(self, c):
        self.order.append(c)
        self.set.add(c)


def parse_instance(line):
    fields = line.split()
    if len(fields) < 3 or not fields[0].startswith("X"):
        return None
    return fields


def emit_dff(kind, names, pins, flops, delay_count_box):
    """Sequential cell handlers.

    For "DFF" (the original library), SPICE order is (C, D, Q) per the
    cmos_cells.v declaration.  For "__DFF_P_" (Yosys $_DFF_P_), simplemap
    sets ports in (D, C, Q) order, so the guessed write_spice order is the
    reverse (Q, C, D).

    The xpresi-style cascade trick: when D feeds from another flop's
    output, capture D in a temporary so cascaded flops still see the
    previous-cycle value before this cycle's update.
    """
    if kind == "DFF":
        clk, d, q = pins
    else:  # "__DFF_P_": SPICE (Q, C, D)
        q, clk, d = pins
    qref = names.ref(q)
    dref = names.ref(d)
    names.ref(clk)
    if d in flops:
        delay = "_presi_delay_%u" % delay_count_box[0]
        delay_count_box[0] += 1
        names.add_synthetic(delay)
        stmt = "%s = %s; %s = %s;" % (qref, delay, delay, dref)
    else:
        stmt = "%s = %s;" % (qref, dref)
    flops.add(q)
    return stmt


def emit_dffsr(kind, names, pins, flops):
    """$_DFFSR_PPP_: active-high S/R, rising-edge D->Q.

    SPICE order for the original "DFFSR" library cell is (C, D, Q, S, R)
    per its cmos_cells.v declaration.  For "__DFFSR_PPP_" (Yosys
    $_DFFSR_PPP_), dfflegalize inserts ports in (C, S, R, D, Q) order, so
    the guessed write_spice order is the reverse (Q, D, R, S, C).
    """
    if kind == "DFFSR":
        clk, d, q, s, r = pins
    else:  # "__DFFSR_PPP_": SPICE (Q, D, R, S, C)
        q, d, r, s, clk = pins
    qref = names.ref(q)
    sref = names.ref(s)
    rref = names.ref(r)
    dref = names.ref(d)
    names.ref(clk)
    flops.add(q)
    return ("if (%s & 1) %s = PRESI_1; else if (%s & 1) %s = PRESI_0; "
            "else %s = %s;" % (sref, qref, rref, qref, qref, dref))


def write_var_header(path, top, ordered_names):
    guard = "PRESI_%s_VAR_H" % top.upper()
    with open(path, "w", encoding="utf-8") as f:
        f.write("/* Generated by spice_to_c.py.  Do not edit. */\n")
        f.write("#ifndef %s\n#define %s\n" % (guard, guard))
        f.write("#include <stdint.h>\n")
        f.write("typedef uint8_t presi_t;\n")
        f.write("#define PRESI_0 ((presi_t) 0)\n")
        f.write("#define PRESI_1 ((presi_t) ~0)\n")
        for c in ordered_names:
            f.write("extern presi_t %s;\n" % c)
        f.write("#endif\n")


def write_var_definitions(path, header_basename, ordered_names):
    with open(path, "w", encoding="utf-8") as f:
        f.write("/* Generated by spice_to_c.py.  Do not edit. */\n")
        f.write('#include "%s"\n' % header_basename)
        for c in ordered_names:
            f.write("presi_t %s;\n" % c)


def write_part_files(parts_dir, top, header_basename, statements, num_parts):
    n = len(statements)
    if n == 0:
        chunk = 0
    else:
        chunk = (n + num_parts - 1) // num_parts
    for idx in range(num_parts):
        start = idx * chunk
        end = min(start + chunk, n) if chunk else 0
        path = os.path.join(parts_dir,
                            "%s.presi_clk_part_%03d.c" % (top, idx))
        with open(path, "w", encoding="utf-8") as f:
            f.write("/* Generated by spice_to_c.py.  Do not edit. */\n")
            f.write('#include "%s"\n' % header_basename)
            f.write("void presi_step_part_%03d(void)\n{\n" % idx)
            for i in range(start, end):
                f.write("\t%s\n" % statements[i])
            f.write("}\n")


def write_clk_dispatch(path, num_parts):
    with open(path, "w", encoding="utf-8") as f:
        f.write("/* Generated by spice_to_c.py.  Do not edit.\n"
                " *\n"
                " * Included from the harness step function.  The block-scope\n"
                " * extern declarations are legal C99 and stay local to that\n"
                " * function. */\n")
        for i in range(num_parts):
            f.write("extern void presi_step_part_%03d(void);\n" % i)
        for i in range(num_parts):
            f.write("presi_step_part_%03d();\n" % i)


def translate(spice_file, var_header, var_c, clk_dispatch, parts_dir,
              num_parts, map_file, bb_file, top):
    names = NameMap()
    statements = []
    flops = set()
    delay_count_box = [0]
    bb_instances = []  # list of (inst, module, [(spice, c_name)])
    blackbox_modules = {}  # module -> instance count

    with open(spice_file, "r", encoding="utf-8") as f:
        for lineno, raw in enumerate(f, 1):
            line = raw.strip()
            if not line or line.startswith("*") or line.startswith("."):
                continue

            # Voltage source: `V<n> <src> <dst> DC <value>` ties dst to src.
            if line.startswith("V"):
                fields = line.split()
                if len(fields) >= 4 and fields[3] == "DC":
                    src = names.ref(fields[1])
                    dst = names.ref(fields[2])
                    statements.append("%s = %s;" % (dst, src))
                    continue
                raise SystemExit("%s:%d: unsupported voltage source: %s" %
                                 (spice_file, lineno, raw.rstrip()))

            fields = parse_instance(line)
            if fields is None:
                raise SystemExit("%s:%d: unsupported SPICE line: %s" %
                                 (spice_file, lineno, raw.rstrip()))

            inst = fields[0]
            module = fields[-1]
            pins = fields[1:-1]

            # Combinational primitive.
            tmpl = COMBINATIONAL.get((module, len(pins)))
            if tmpl is not None:
                refs = [names.ref(p) for p in pins]
                statements.append(tmpl(refs))
                continue

            # Sequential primitives.
            if module in ("DFF", "__DFF_P_") and len(pins) == 3:
                statements.append(
                    emit_dff(module, names, pins, flops, delay_count_box))
                continue
            if module in ("DFFSR", "__DFFSR_PPP_") and len(pins) == 5:
                statements.append(emit_dffsr(module, names, pins, flops))
                continue

            # Anything else: blackbox subcircuit.  Engines, SRAMs, the twiddle
            # ROM, and the surviving $mem_v2 sequencer ROM all land here.  Pins
            # are recorded in their declaration order; the harness writer
            # cross-references presi_bb.csv to wire them up.
            pin_refs = [names.ref(p) for p in pins]
            bb_instances.append((inst, module,
                                 list(zip(pins, pin_refs))))
            blackbox_modules[module] = blackbox_modules.get(module, 0) + 1
            statements.append("/* blackbox %s %s pins=%d */" %
                              (inst, module, len(pins)))

    ordered = [c for c in sorted(names.order)
               if c not in ("PRESI_0", "PRESI_1")]
    header_basename = os.path.basename(var_header)

    write_var_header(var_header, top, ordered)
    write_var_definitions(var_c, header_basename, ordered)
    os.makedirs(parts_dir, exist_ok=True)
    write_part_files(parts_dir, top, header_basename, statements, num_parts)
    write_clk_dispatch(clk_dispatch, num_parts)

    with open(map_file, "w", encoding="utf-8") as f:
        f.write("spice_name,c_name\n")
        for spice_name, c_name in sorted(names.names.items()):
            f.write("%s,%s\n" % (spice_name, c_name))

    with open(bb_file, "w", encoding="utf-8") as f:
        f.write("instance,module,pin_index,spice_name,c_name\n")
        for inst, module, pin_list in bb_instances:
            for idx, (spice, c) in enumerate(pin_list):
                f.write("%s,%s,%d,%s,%s\n" % (inst, module, idx, spice, c))

    cells = len(statements)
    nets = len(ordered)
    print("translated %d statements into %d parts, %d nets, "
          "%d blackbox instances (%d distinct modules)" %
          (cells, num_parts, nets, len(bb_instances), len(blackbox_modules)))
    if blackbox_modules:
        for mod in sorted(blackbox_modules):
            print("  blackbox: %-32s x %d" % (mod, blackbox_modules[mod]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spice", required=True)
    ap.add_argument("--vars", required=True,
                    help="header with extern presi_t declarations")
    ap.add_argument("--vars-c", required=True,
                    help="C file with presi_t definitions")
    ap.add_argument("--clock", required=True,
                    help="dispatch header (extern decls + calls)")
    ap.add_argument("--parts-dir", required=True,
                    help="directory for per-part C files")
    ap.add_argument("--num-parts", type=int, default=NUM_PARTS_DEFAULT,
                    help="number of cycle-update parts to emit")
    ap.add_argument("--map", required=True)
    ap.add_argument("--bb", required=True,
                    help="CSV of blackbox subcircuit pin connections")
    ap.add_argument("--top", default="abr_wrap",
                    help="top module name (used for filename prefix)")
    args = ap.parse_args()
    if args.num_parts < 1:
        raise SystemExit("--num-parts must be >= 1")
    translate(args.spice, args.vars, args.vars_c, args.clock, args.parts_dir,
              args.num_parts, args.map, args.bb, args.top)


if __name__ == "__main__":
    main()
