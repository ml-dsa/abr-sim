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
# Each entry: (output_pin_index, lambda(n) -> stmt_string).  The output
# index is what the topo sort needs to know which net the cell drives;
# we keep the lambdas verbatim so the emitted C is unchanged.
COMBINATIONAL = {
    # BUF/NOT/NAND/NOR with the original cmos_cells.v declarations follow
    # input-first order (those modules are not pruned because dfflibmap+abc
    # emit them).  Kept for backward compatibility with the abc-based flow.
    ("BUF",  2):       (1, lambda n: "%s = %s;" % (n[1], n[0])),
    ("NOT",  2):       (1, lambda n: "%s = ~%s;" % (n[1], n[0])),
    ("NAND", 3):       (2, lambda n: "%s = ~(%s & %s);" % (n[2], n[0], n[1])),
    ("NOR",  3):       (2, lambda n: "%s = ~(%s | %s);" % (n[2], n[0], n[1])),
    # Yosys gate primitives.  Output first, then inputs in reverse insertion
    # order.  For symmetric operators the input order is irrelevant.
    ("__NOT_",     2): (0, lambda n: "%s = ~%s;" % (n[0], n[1])),
    ("__AND_",     3): (0, lambda n: "%s = %s & %s;" % (n[0], n[2], n[1])),
    ("__OR_",      3): (0, lambda n: "%s = %s | %s;" % (n[0], n[2], n[1])),
    ("__NAND_",    3): (0, lambda n: "%s = ~(%s & %s);" % (n[0], n[2], n[1])),
    ("__NOR_",     3): (0, lambda n: "%s = ~(%s | %s);" % (n[0], n[2], n[1])),
    ("__XOR_",     3): (0, lambda n: "%s = %s ^ %s;" % (n[0], n[2], n[1])),
    ("__XNOR_",    3): (0, lambda n: "%s = ~(%s ^ %s);" % (n[0], n[2], n[1])),
    # SPICE (Y, B, A) maps to Y = A & ~B / Y = A | ~B.
    ("__ANDNOT_",  3): (0, lambda n: "%s = %s & ~%s;" % (n[0], n[2], n[1])),
    ("__ORNOT_",   3): (0, lambda n: "%s = %s | ~%s;" % (n[0], n[2], n[1])),
    # SPICE (Y, S, B, A): Y = S ? B : A.
    ("__MUX_",     4): (0, lambda n:
        "%s = (%s & 1) ? %s : %s;" % (n[0], n[1], n[2], n[3])),
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
    def __init__(self, prefix=""):
        # prefix is prepended to every C identifier emitted by ref().
        # Use this to compile multiple gate netlists into one binary
        # without symbol collisions (e.g. ntt_top__n_1234 alongside
        # abr_wrap__n_1234).  Empty default keeps the single-netlist
        # behavior unchanged.
        self.prefix = prefix
        self.names = {}
        self.order = []
        self.set = set()

    def ref(self, name):
        if name in ("0s", "1s"):
            return cname(name)
        c = self.names.get(name)
        if c is not None:
            return c
        c = self.prefix + cname(name)
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


def emit_dff(kind, names, pins, flops, clk_prev):
    """Sequential cell handlers.

    For "DFF" (the original library), SPICE order is (C, D, Q) per the
    cmos_cells.v declaration.  For "__DFF_P_" (Yosys $_DFF_P_), simplemap
    sets ports in (D, C, Q) order, so the guessed write_spice order is the
    reverse (Q, C, D).

    Edge-triggered semantics: the harness owns a `presi_clk_prev` flag
    that mirrors the clock value from the *previous* presi_step_netlist
    call.  We capture D->Q only on a 0->1 transition (`!clk_prev & clk`),
    so multiple step_netlist calls within one logical cycle settle
    combinational without re-clocking the flop.

    Returns a metadata dict (lhs, rhs, stmt, is_flop) so the
    translator can topologically sort comb statements without disturbing
    flop ordering.
    """
    if kind == "DFF":
        clk, d, q = pins
    else:  # "__DFF_P_": SPICE (Q, C, D)
        q, clk, d = pins
    qref = names.ref(q)
    dref = names.ref(d)
    cref = names.ref(clk)
    edge = "(%s & ~%s & 1)" % (cref, clk_prev)
    stmt = "if %s %s = %s;" % (edge, qref, dref)
    flops.add(qref)
    return {"stmt": stmt, "lhs": qref, "rhs": [cref, dref], "is_flop": True}


def emit_dffsr(kind, names, pins, flops, clk_prev):
    """$_DFFSR_PPP_: active-high S/R, rising-edge D->Q.

    SPICE order for the original "DFFSR" library cell is (C, D, Q, S, R)
    per its cmos_cells.v declaration.  For "__DFFSR_PPP_" (Yosys
    $_DFFSR_PPP_), dfflegalize inserts ports in (C, S, R, D, Q) order, so
    the guessed write_spice order is the reverse (Q, D, R, S, C).

    Set/reset are level-sensitive (PPP = pos/pos/pos), so they apply
    regardless of clock.  Only the D->Q transfer is edge-triggered.
    We treat DFFSR as a flop for topo-sort purposes (Q is "stable
    during comb"); the level-sensitive S/R override is handled by
    emitting the DFFSR statement at the end of the cycle, so any
    comb that reads Q sees the previous cycle's settled value.
    """
    if kind == "DFFSR":
        clk, d, q, s, r = pins
    else:  # "__DFFSR_PPP_": SPICE (Q, D, R, S, C)
        q, d, r, s, clk = pins
    qref = names.ref(q)
    sref = names.ref(s)
    rref = names.ref(r)
    dref = names.ref(d)
    cref = names.ref(clk)
    flops.add(qref)
    edge = "(%s & ~%s & 1)" % (cref, clk_prev)
    stmt = ("if (%s & 1) %s = PRESI_1; else if (%s & 1) %s = PRESI_0; "
            "else if %s %s = %s;" %
            (sref, qref, rref, qref, edge, qref, dref))
    return {"stmt": stmt, "lhs": qref,
            "rhs": [sref, rref, cref, dref], "is_flop": True}


def write_var_header(path, top, ordered_names, clk_prev):
    guard = "PRESI_%s_VAR_H" % top.upper()
    with open(path, "w", encoding="utf-8") as f:
        f.write("/* Generated by spice_to_c.py.  Do not edit. */\n")
        f.write("#ifndef %s\n#define %s\n" % (guard, guard))
        f.write("#include <stdint.h>\n")
        f.write("#ifndef PRESI_T_DEFINED\n")
        f.write("#define PRESI_T_DEFINED\n")
        f.write("typedef uint8_t presi_t;\n")
        f.write("#define PRESI_0 ((presi_t) 0)\n")
        f.write("#define PRESI_1 ((presi_t) ~0)\n")
        f.write("#endif\n")
        f.write("/* Last-clock snapshot for edge-triggered DFFs.  The\n"
                " * harness updates this just before each step_netlist call\n"
                " * so the rising-edge predicate `(clk & ~%s)` fires\n"
                " * exactly once per logical cycle. */\n" % clk_prev)
        f.write("extern presi_t %s;\n" % clk_prev)
        for c in ordered_names:
            f.write("extern presi_t %s;\n" % c)
        f.write("#endif\n")


def write_var_definitions(path, header_basename, ordered_names, clk_prev):
    with open(path, "w", encoding="utf-8") as f:
        f.write("/* Generated by spice_to_c.py.  Do not edit. */\n")
        f.write('#include "%s"\n' % header_basename)
        f.write("presi_t %s;\n" % clk_prev)
        for c in ordered_names:
            f.write("presi_t %s;\n" % c)


def write_part_files(parts_dir, top, header_basename, statements, num_parts,
                     step_fn_prefix):
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
            f.write("void %spresi_step_part_%03d(void)\n{\n" %
                    (step_fn_prefix, idx))
            for i in range(start, end):
                f.write("\t%s\n" % statements[i])
            f.write("}\n")


def write_clk_dispatch(path, num_parts, step_fn_prefix):
    with open(path, "w", encoding="utf-8") as f:
        f.write("/* Generated by spice_to_c.py.  Do not edit.\n"
                " *\n"
                " * Included from the harness step function.  The block-scope\n"
                " * extern declarations are legal C99 and stay local to that\n"
                " * function. */\n")
        for i in range(num_parts):
            f.write("extern void %spresi_step_part_%03d(void);\n" %
                    (step_fn_prefix, i))
        for i in range(num_parts):
            f.write("%spresi_step_part_%03d();\n" % (step_fn_prefix, i))


def topo_order_comb(items, flop_outputs):
    """Kahn's algorithm: order combinational items so each consumer runs
    after the producer of every wire it reads.  Reads of flop outputs
    don't create a constraint -- those wires are stable during a comb
    pass since DFF assignments run at the end of the cycle.

    Items must be the *combinational* subset already (caller filters
    out is_flop).  Each item has `lhs` (one c_name) and `rhs` (list of
    c_names; constants and presi_clk_prev should already be filtered).
    """
    n = len(items)
    writer = {}
    for i, it in enumerate(items):
        if it["lhs"] is not None:
            if it["lhs"] in writer:
                # Multiple drivers for the same net is invalid -- keep
                # the first writer to make topology deterministic.
                continue
            writer[it["lhs"]] = i

    edges_out = [[] for _ in range(n)]
    in_degree = [0] * n
    for i, it in enumerate(items):
        for r in it["rhs"]:
            if r in flop_outputs:
                continue
            j = writer.get(r)
            if j is None or j == i:
                continue
            edges_out[j].append(i)
            in_degree[i] += 1

    ready = [i for i in range(n) if in_degree[i] == 0]
    order = []
    while ready:
        i = ready.pop()
        order.append(i)
        for j in edges_out[i]:
            in_degree[j] -= 1
            if in_degree[j] == 0:
                ready.append(j)

    if len(order) < n:
        # Combinational cycle: append the remaining items in their
        # original order (the simulation will need extra settle passes
        # for these, but we don't refuse to emit).
        seen = set(order)
        leftovers = [i for i in range(n) if i not in seen]
        print("WARNING: %d combinational statements form a cycle, "
              "appending in original order" % len(leftovers))
        order.extend(leftovers)

    return [items[i] for i in order]


def translate(spice_file, var_header, var_c, clk_dispatch, parts_dir,
              num_parts, map_file, bb_file, top, symbol_prefix=""):
    names = NameMap(prefix=symbol_prefix)
    clk_prev = symbol_prefix + "presi_clk_prev"
    step_fn_prefix = symbol_prefix
    items = []  # list of dicts: {stmt, lhs, rhs, is_flop}
    flops = set()  # c_names of flop outputs
    bb_instances = []  # list of (inst, module, [(spice, c_name)])
    blackbox_modules = {}  # module -> instance count

    def add_comb(lhs, rhs, stmt):
        items.append({"stmt": stmt, "lhs": lhs, "rhs": rhs,
                      "is_flop": False})

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
                    add_comb(dst, [src], "%s = %s;" % (dst, src))
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
            tmpl_entry = COMBINATIONAL.get((module, len(pins)))
            if tmpl_entry is not None:
                out_idx, tmpl = tmpl_entry
                refs = [names.ref(p) for p in pins]
                stmt = tmpl(refs)
                lhs = refs[out_idx]
                rhs = [r for j, r in enumerate(refs) if j != out_idx]
                add_comb(lhs, rhs, stmt)
                continue

            # Sequential primitives.
            if module in ("DFF", "__DFF_P_") and len(pins) == 3:
                items.append(emit_dff(module, names, pins, flops, clk_prev))
                continue
            if module in ("DFFSR", "__DFFSR_PPP_") and len(pins) == 5:
                items.append(emit_dffsr(module, names, pins, flops, clk_prev))
                continue

            # Anything else: blackbox subcircuit.  Engines, SRAMs, the twiddle
            # ROM, and the surviving $mem_v2 sequencer ROM all land here.  Pins
            # are recorded in their declaration order; the harness writer
            # cross-references presi_bb.csv to wire them up.  Blackbox cells
            # don't drive any nets in this pass (the bb-wiring header drives
            # rdata_o etc. separately), so they emit only a comment.
            pin_refs = [names.ref(p) for p in pins]
            bb_instances.append((inst, module,
                                 list(zip(pins, pin_refs))))
            blackbox_modules[module] = blackbox_modules.get(module, 0) + 1
            items.append({"stmt": "/* blackbox %s %s pins=%d */" %
                          (inst, module, len(pins)),
                          "lhs": None, "rhs": [], "is_flop": False})

    # Filter constants out of every rhs list.
    nondep = ("PRESI_0", "PRESI_1", clk_prev)
    for it in items:
        it["rhs"] = [r for r in it["rhs"] if r not in nondep]

    # Split into comb and flop, topologically order the comb half so that
    # within a single presi_step_netlist call every signal is read after
    # its driver has been evaluated.  Flop assignments run at the end so
    # any comb that reads Q sees the previous cycle's settled value.
    comb = [it for it in items if not it["is_flop"]]
    flop_items = [it for it in items if it["is_flop"]]
    comb_ordered = topo_order_comb(comb, flops)
    statements = [it["stmt"] for it in comb_ordered] + \
                 [it["stmt"] for it in flop_items]

    ordered = [c for c in sorted(names.order)
               if c not in ("PRESI_0", "PRESI_1")]
    header_basename = os.path.basename(var_header)

    write_var_header(var_header, top, ordered, clk_prev)
    write_var_definitions(var_c, header_basename, ordered, clk_prev)
    os.makedirs(parts_dir, exist_ok=True)
    write_part_files(parts_dir, top, header_basename, statements, num_parts,
                     step_fn_prefix)
    write_clk_dispatch(clk_dispatch, num_parts, step_fn_prefix)

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
    ap.add_argument("--symbol-prefix", default="",
                    help="prepended to every C identifier emitted (net "
                         "names, presi_clk_prev, presi_step_part_NNN); "
                         "use to compile multiple netlists into one binary "
                         "without symbol collisions")
    args = ap.parse_args()
    if args.num_parts < 1:
        raise SystemExit("--num-parts must be >= 1")
    translate(args.spice, args.vars, args.vars_c, args.clock, args.parts_dir,
              args.num_parts, args.map, args.bb, args.top,
              symbol_prefix=args.symbol_prefix)


if __name__ == "__main__":
    main()
