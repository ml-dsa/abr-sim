#!/usr/bin/env python3

# Generate per-engine "glue" C code that ties an engine's standalone
# gate netlist to its blackbox pin connections in the unified abr_wrap
# netlist.  Concretely, for each cycle the unified harness calls
# `<engine>_step_glue(void)`, which:
#
#   1. Copies abr_wrap-side bb pin values onto the engine's input ports
#      (engine extern presi_t = abr_wrap extern presi_t).
#   2. Calls each `<prefix>presi_step_part_NNN()` function so the engine's
#      gates evaluate one logical phase.
#   3. Updates `<prefix>presi_clk_prev = <prefix>clk` so the next call
#      sees the right edge.
#   4. Copies the engine's output port values back onto abr_wrap's bb
#      pins (where downstream comb logic in abr_wrap can read them).
#
# Inputs:
#   --engine <module>        engine module name (e.g. ntt_top)
#   --instance <inst>        which abr_wrap bb instance to wire (cell name
#                            from abr_wrap.presi_bb.csv 'instance' column)
#   --abr-wrap-bb <path>     abr_wrap.presi_bb.csv (one row per pin)
#   --engine-gates-v <path>  engine's <engine>.gates.v (read for module
#                            decl + per-port input/output/[msb:lsb] info)
#   --engine-prefix <str>    symbol prefix for the engine flow
#                            (e.g. 'ntt_top__')
#   --engine-num-parts <n>   number of <prefix>presi_step_part_NNN funcs
#   --out <path>             output C file
#
# Pin matching: abr_wrap.presi_bb.csv lists pins in `pin_index` order,
# which matches Yosys's port_id iteration over the engine module --
# bit 0 of port 0, bit 1 of port 0, ..., bit 0 of port 1, etc.  The
# engine's standalone gates flow uses the same SV input, so the same
# pin_index sequence corresponds to the same port-bit on both sides.

import argparse
import csv
import re


def cname(name):
    """Mirror of spice_to_c.py's cname(): mangle a SPICE-style net name
    into a valid C identifier.  Used here only to synthesize the
    expected c_name of an engine port-bit (e.g. 'mode.2' -> 'mode_2')."""
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


# `input` / `output` declarations Yosys emits in gates.v after `module`.
# We expect ANSI-style port lists in the module head followed by
# explicit `input`/`output [msb:lsb] <name>;` lines in the body.
PORTLIST_RE = re.compile(r"^\s*module\s+(\w+)\s*\(([^)]*)\)\s*;",
                         re.MULTILINE | re.DOTALL)
DIR_RE = re.compile(
    r"^\s*(input|output|inout)"
    r"(?:\s+(?:wire|reg))?"
    r"(?:\s+\[\s*(\d+)\s*:\s*(\d+)\s*\])?"
    r"\s+(\w+)\s*;",
    re.MULTILINE)


def parse_engine_ports(gates_v_path, engine_module):
    """Return list of (port_name, msb, lsb, direction, bit_count) in
    declaration order.  Reads only enough of gates.v to find the module
    header and per-port input/output declarations."""
    # The module declaration is at the start of the file; the body
    # following it has the per-port direction declarations interleaved
    # with `wire`/`reg` declarations.  We can stop reading once we've
    # seen all ports declared.
    text = []
    with open(gates_v_path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            text.append(line)
            # Heuristic: stop after we've seen ~500 lines of header,
            # which is enough for any reasonable engine.
            if i > 5000:
                break
    body = "".join(text)

    m = PORTLIST_RE.search(body)
    if m is None:
        raise SystemExit("module declaration not found in %s" % gates_v_path)
    if m.group(1) != engine_module:
        raise SystemExit("expected module %r, found %r" %
                         (engine_module, m.group(1)))
    raw_ports = [p.strip() for p in m.group(2).split(",")]
    port_order = [p for p in raw_ports if p]

    # Map port -> (direction, msb, lsb) by scanning input/output lines.
    info = {}
    for dm in DIR_RE.finditer(body):
        direction, msb, lsb, name = dm.group(1), dm.group(2), dm.group(3), dm.group(4)
        if name not in port_order:
            continue
        if msb is None:
            info[name] = (direction, 0, 0, 1)
        else:
            msb = int(msb)
            lsb = int(lsb)
            count = abs(msb - lsb) + 1
            info[name] = (direction, msb, lsb, count)

    out = []
    for p in port_order:
        if p not in info:
            raise SystemExit("port %r in module head but not declared" % p)
        d, msb, lsb, count = info[p]
        out.append((p, msb, lsb, d, count))
    return out


def engine_pin_seq(ports):
    """Expand the port list into the per-bit pin sequence Yosys uses
    when emitting blackbox cell connections in SPICE: port 0 bit 0,
    port 0 bit 1, ..., port 1 bit 0, etc.  Bit ordering within a port
    is LSB-first: bit lsb is pin index 0 of the port, bit lsb+1 is
    pin 1, ... ."""
    seq = []
    for name, msb, lsb, direction, count in ports:
        for b in range(count):
            # b=0 corresponds to the LSB regardless of [msb:lsb] order.
            seq.append((name, b, direction, count))
    return seq


def engine_c_name(prefix, port_name, bit, count):
    """Compute the engine-side extern presi_t identifier for one
    port-bit.  Mirrors spice_to_c.py's cname() applied to Yosys's
    SPICE form (`<port>` for 1-bit, `<port>.<bit>` for multi-bit)."""
    if count == 1:
        spice = port_name
    else:
        spice = "%s.%d" % (port_name, bit)
    return prefix + cname(spice)


EXTERN_RE = re.compile(r"^extern presi_t (\w+);")


def parse_engine_externs(var_h_path):
    """Return set of c_name symbols declared in <engine>.presi_var.h.
    Yosys's engine-standalone flow drops port bits that have no fanout
    inside the engine, so this set is the actual ground truth for which
    engine-side externs are linkable."""
    syms = set()
    with open(var_h_path, "r", encoding="utf-8") as f:
        for line in f:
            m = EXTERN_RE.match(line)
            if m:
                syms.add(m.group(1))
    return syms


def parse_bb(path, instance, module):
    """Return (resolved_instance, [(pin_index, abr_wrap_c_name), ...]).
    If `instance` is None, autoselect when exactly one bb instance
    matches the module."""
    by_inst = {}
    with open(path, "r", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["module"] != module:
                continue
            if instance is not None and r["instance"] != instance:
                continue
            by_inst.setdefault(r["instance"], []).append(
                (int(r["pin_index"]), r["c_name"]))

    if not by_inst:
        raise SystemExit("no bb rows in %s for module=%r%s" %
                         (path, module,
                          (" instance=%r" % instance) if instance else ""))
    if instance is None and len(by_inst) > 1:
        raise SystemExit(
            "module %r has %d bb instances %s; pass --instance to choose" %
            (module, len(by_inst), sorted(by_inst.keys())))
    inst = instance if instance is not None else next(iter(by_inst))
    rows = sorted(by_inst[inst])
    return inst, rows


CONSTANT_C_NAMES = ("PRESI_0", "PRESI_1")


def emit_glue(out_path, engine, instance, prefix, num_parts,
              pin_seq, bb_rows, engine_syms):
    """Emit a single glue C file with extern decls and the
    `<engine>_step_glue` function."""
    # Sanity-check: pin_seq and bb_rows must have the same length.
    if len(pin_seq) != len(bb_rows):
        raise SystemExit(
            "pin count mismatch for %s/%s: engine has %d port bits, "
            "abr_wrap.presi_bb.csv has %d pin rows for instance %r" %
            (engine, instance, len(pin_seq), len(bb_rows), instance))

    skipped_outputs = []
    constant_inputs = 0
    skipped_inputs_engine_optimized = 0
    skipped_outputs_engine_optimized = 0

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("/* Generated by gen_engine_glue.py.  Do not edit. */\n")
        f.write("#include <stdint.h>\n")
        f.write("#ifndef PRESI_T_DEFINED\n")
        f.write("#define PRESI_T_DEFINED\n")
        f.write("typedef uint8_t presi_t;\n")
        f.write("#define PRESI_0 ((presi_t) 0)\n")
        f.write("#define PRESI_1 ((presi_t) ~0)\n")
        f.write("#endif\n\n")

        # Engine-side externs.  Yosys may have optimized away input
        # port bits with no fanout (or output bits with no driver)
        # inside the engine; engine_syms is the ground-truth set of
        # symbols actually defined in <engine>.presi_var.c.  Only emit
        # extern decls for symbols that exist.
        f.write("/* engine-side externs (from %s.presi_var.h) */\n" % engine)
        f.write("extern presi_t %spresi_clk_prev;\n" % prefix)
        eng_externs = sorted({engine_c_name(prefix, p, b, c)
                              for p, b, _, c in pin_seq
                              if engine_c_name(prefix, p, b, c) in engine_syms})
        for n in eng_externs:
            f.write("extern presi_t %s;\n" % n)

        # Engine step functions.
        for i in range(num_parts):
            f.write("extern void %spresi_step_part_%03d(void);\n" %
                    (prefix, i))

        # abr_wrap-side externs.  Constants don't get extern decls
        # (PRESI_0/PRESI_1 are macros).
        f.write("\n/* abr_wrap-side externs (from abr_wrap.presi_var.h) */\n")
        abr_externs = sorted({c for _, c in bb_rows
                              if c not in CONSTANT_C_NAMES})
        for n in abr_externs:
            f.write("extern presi_t %s;\n" % n)

        # Find the engine's clk port-bit pair (used for clk_prev update).
        clk_pin_idx = None
        for i, (port, bit, direction, count) in enumerate(pin_seq):
            if port == "clk" and bit == 0:
                clk_pin_idx = i
                break

        # Emit the step glue.
        f.write("\nvoid %s_step_glue(void)\n{\n" % engine)
        f.write("\t/* abr_wrap -> engine inputs */\n")
        for (port, bit, direction, count), (pin_idx, abr_c) in zip(pin_seq,
                                                                    bb_rows):
            if direction != "input":
                continue
            eng_c = engine_c_name(prefix, port, bit, count)
            if eng_c not in engine_syms:
                # Yosys optimized this input bit away (no fanout in
                # engine).  Skip the assignment -- the bit doesn't
                # affect engine behavior.
                skipped_inputs_engine_optimized += 1
                continue
            # PRESI_0/PRESI_1 expand to (presi_t)<const>; assignment is
            # legal C in either direction here -- the compiler folds it.
            f.write("\t%s = %s;\n" % (eng_c, abr_c))
            if abr_c in CONSTANT_C_NAMES:
                constant_inputs += 1

        f.write("\n\t/* step engine */\n")
        for i in range(num_parts):
            f.write("\t%spresi_step_part_%03d();\n" % (prefix, i))
        if clk_pin_idx is not None:
            clk_eng_c = engine_c_name(prefix, "clk", 0, 1)
            f.write("\t%spresi_clk_prev = %s;\n" % (prefix, clk_eng_c))
        else:
            f.write("\t/* no clk port found in engine -- check ports */\n")

        f.write("\n\t/* engine outputs -> abr_wrap */\n")
        for (port, bit, direction, count), (pin_idx, abr_c) in zip(pin_seq,
                                                                    bb_rows):
            if direction != "output":
                continue
            # An output pin connected to a constant in abr_wrap means
            # abr_wrap doesn't read this output (Yosys constant-folded
            # the unused fanout).  Skip the write -- there's nothing to
            # store in.
            if abr_c in CONSTANT_C_NAMES:
                skipped_outputs.append((port, bit))
                continue
            eng_c = engine_c_name(prefix, port, bit, count)
            if eng_c not in engine_syms:
                # Yosys optimized this output bit away (no driver
                # inside the engine).  Skip -- abr_wrap's pin will
                # stay at its initial 0 (matches Yosys's view that
                # this bit is dead).
                skipped_outputs_engine_optimized += 1
                continue
            f.write("\t%s = %s;\n" % (abr_c, eng_c))

        if skipped_outputs:
            f.write("\n\t/* outputs tied to constants in abr_wrap (unused "
                    "downstream): */\n")
            for port, bit in skipped_outputs:
                f.write("\t/*   %s[%d] */\n" % (port, bit))

        f.write("}\n")

    inputs = sum(1 for _, _, d, _ in pin_seq if d == "input")
    outputs = sum(1 for _, _, d, _ in pin_seq if d == "output")
    return (len(pin_seq), inputs, outputs, len(skipped_outputs),
            constant_inputs, skipped_inputs_engine_optimized,
            skipped_outputs_engine_optimized)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", required=True,
                    help="engine module name (e.g. ntt_top)")
    ap.add_argument("--instance", default=None,
                    help="abr_wrap bb instance name to wire (from "
                         "presi_bb.csv 'instance' column).  Optional "
                         "when the module has exactly one instance.")
    ap.add_argument("--abr-wrap-bb", required=True,
                    help="path to abr_wrap.presi_bb.csv")
    ap.add_argument("--engine-gates-v", required=True,
                    help="path to engine's <engine>.gates.v "
                         "(used to read port directions)")
    ap.add_argument("--engine-prefix", required=True,
                    help="engine symbol prefix (e.g. 'ntt_top__')")
    ap.add_argument("--engine-num-parts", type=int, required=True,
                    help="number of <prefix>presi_step_part_NNN functions")
    ap.add_argument("--engine-var-h", required=True,
                    help="path to engine's <engine>.presi_var.h "
                         "(used to filter out port bits Yosys "
                         "optimized away from the engine netlist)")
    ap.add_argument("--out", required=True,
                    help="output C file (the per-engine glue)")
    args = ap.parse_args()

    ports = parse_engine_ports(args.engine_gates_v, args.engine)
    pin_seq = engine_pin_seq(ports)
    instance, bb_rows = parse_bb(args.abr_wrap_bb, args.instance, args.engine)
    engine_syms = parse_engine_externs(args.engine_var_h)

    (total, ins, outs, skipped, const_in,
     dead_in, dead_out) = emit_glue(
        args.out, args.engine, instance, args.engine_prefix,
        args.engine_num_parts, pin_seq, bb_rows, engine_syms)
    print("engine-glue: %s/%s -> %s (%d bits, %d in (%d const-tied, "
          "%d dead-in-engine), %d out (%d const-in-abr_wrap, "
          "%d dead-in-engine))" %
          (args.engine, instance, args.out, total, ins, const_in, dead_in,
           outs, skipped, dead_out))


if __name__ == "__main__":
    main()
