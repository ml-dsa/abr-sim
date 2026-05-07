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


def parse_idx_map(map_path):
    """Read a presi_map.csv (idx,spice_name,c_name) and return a dict
    c_name -> idx.  Used to substitute literal integer indices into
    the generated glue C, avoiding a giant idx-header include."""
    out = {}
    with open(map_path, "r", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out[r["c_name"]] = int(r["idx"])
    return out


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


def find_gate_pins(gates, pin_seq, bb_rows):
    """Return list of abr_wrap-side c_names for the requested gating
    ports.  `gates` is a list of port names (e.g. 'ntt_enable',
    'ntt_busy').  All bits of each gating port are included so
    multi-bit gates like a `mode` field also work."""
    out = []
    for gate in gates:
        matched = False
        for (port, bit, direction, count), (pin_idx, abr_c) in zip(
                pin_seq, bb_rows):
            if port != gate:
                continue
            matched = True
            if abr_c in CONSTANT_C_NAMES:
                # A gating port tied to a constant in abr_wrap is
                # always either 0 (effectively never gates on) or 1
                # (always gates on); skip -- the user's intent is
                # almost certainly the variable signal.
                continue
            out.append(abr_c)
        if not matched:
            raise SystemExit(
                "gate-on port %r not found in engine port list" % gate)
    return out


def emit_glue(out_path, engine, instance, prefix, num_parts,
              pin_seq, bb_rows, abr_idx, engine_idx, gate_pins):
    """Emit a single glue C file referencing both netlists' arrays via
    literal integer indices substituted from the map CSVs."""
    # Sanity-check: pin_seq and bb_rows must have the same length.
    if len(pin_seq) != len(bb_rows):
        raise SystemExit(
            "pin count mismatch for %s/%s: engine has %d port bits, "
            "abr_wrap.presi_bb.csv has %d pin rows for instance %r" %
            (engine, instance, len(pin_seq), len(bb_rows), instance))

    eng_array = "%spresi_s" % prefix
    eng_clk_prev = "%spresi_clk_prev" % prefix

    skipped_outputs = []
    constant_inputs = 0
    skipped_inputs_engine_optimized = 0
    skipped_outputs_engine_optimized = 0

    def abr_expr(c):
        """abr_wrap-side reference: constant pass-through, otherwise
        index into presi_s[]."""
        if c in CONSTANT_C_NAMES:
            return c
        if c not in abr_idx:
            return None
        return "presi_s[%d]" % abr_idx[c]

    def eng_expr(c):
        """engine-side reference: returns None if Yosys optimized the
        signal out of the engine netlist (no idx for it)."""
        if c not in engine_idx:
            return None
        return "%s[%d]" % (eng_array, engine_idx[c])

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("/* Generated by gen_engine_glue.py.  Do not edit.\n"
                " *\n"
                " * Glue between the abr_wrap netlist (presi_s[]) and the\n"
                " * standalone %s netlist (%s[]).  The integer\n"
                " * indices are baked in here from the two presi_map.csv\n"
                " * files; no idx-header include needed. */\n" %
                (engine, eng_array))
        f.write("#include <stdint.h>\n")
        f.write("#ifndef PRESI_T_DEFINED\n")
        f.write("#define PRESI_T_DEFINED\n")
        f.write("typedef uint8_t presi_t;\n")
        f.write("#define PRESI_0 ((presi_t) 0)\n")
        f.write("#define PRESI_1 ((presi_t) ~0)\n")
        f.write("#endif\n\n")

        # Array externs (one per netlist) and the engine's clk-prev
        # scalar.  Sizes don't matter at the call site; we just index.
        f.write("extern presi_t presi_s[];\n")
        f.write("extern presi_t %s[];\n" % eng_array)
        f.write("extern presi_t %s;\n" % eng_clk_prev)

        # Engine step functions.
        for i in range(num_parts):
            f.write("extern void %spresi_step_part_%03d(void);\n" %
                    (prefix, i))

        # Find the engine's clk port-bit pair (used for clk_prev update).
        clk_pin_idx = None
        for i, (port, bit, direction, count) in enumerate(pin_seq):
            if port == "clk" and bit == 0:
                clk_pin_idx = i
                break

        # Emit the step glue.
        f.write("\nvoid %s_step_glue(void)\n{\n" % engine)

        # Optional runtime gate: skip the entire glue + step when
        # none of the gating signals are asserted.  See gen_engine_glue
        # docstring; gate_pins is a list of abr_wrap-side c_names.
        if gate_pins:
            f.write("\t/* runtime gate: skip step when none of the\n"
                    "\t * named gating signals are asserted. */\n")
            gate_exprs = [abr_expr(p) for p in gate_pins]
            gate_exprs = [e for e in gate_exprs if e is not None]
            if gate_exprs:
                f.write("\tif (!(")
                for i, e in enumerate(gate_exprs):
                    if i:
                        f.write("\n\t      | ")
                    f.write("(%s & 1)" % e)
                f.write(")) return;\n\n")

        f.write("\t/* abr_wrap -> engine inputs */\n")
        for (port, bit, direction, count), (pin_idx, abr_c) in zip(pin_seq,
                                                                    bb_rows):
            if direction != "input":
                continue
            ee = eng_expr(engine_c_name(prefix, port, bit, count))
            if ee is None:
                # Yosys optimized this input bit away inside the engine.
                skipped_inputs_engine_optimized += 1
                continue
            ae = abr_expr(abr_c)
            if ae is None:
                # Should not happen for inputs (every bb pin has either a
                # real abr_wrap c_name or a constant), but guard anyway.
                continue
            f.write("\t%s = %s;\n" % (ee, ae))
            if abr_c in CONSTANT_C_NAMES:
                constant_inputs += 1

        f.write("\n\t/* step engine */\n")
        for i in range(num_parts):
            f.write("\t%spresi_step_part_%03d();\n" % (prefix, i))
        if clk_pin_idx is not None:
            clk_eng = eng_expr(engine_c_name(prefix, "clk", 0, 1))
            if clk_eng is not None:
                f.write("\t%s = %s;\n" % (eng_clk_prev, clk_eng))
        else:
            f.write("\t/* no clk port found in engine -- check ports */\n")

        f.write("\n\t/* engine outputs -> abr_wrap */\n")
        for (port, bit, direction, count), (pin_idx, abr_c) in zip(pin_seq,
                                                                    bb_rows):
            if direction != "output":
                continue
            if abr_c in CONSTANT_C_NAMES:
                skipped_outputs.append((port, bit))
                continue
            ee = eng_expr(engine_c_name(prefix, port, bit, count))
            if ee is None:
                skipped_outputs_engine_optimized += 1
                continue
            ae = abr_expr(abr_c)
            if ae is None:
                continue
            f.write("\t%s = %s;\n" % (ae, ee))

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
    ap.add_argument("--abr-wrap-map", required=True,
                    help="path to abr_wrap.presi_map.csv "
                         "(used to look up indices into presi_s[])")
    ap.add_argument("--engine-gates-v", required=True,
                    help="path to engine's <engine>.gates.v "
                         "(used to read port directions)")
    ap.add_argument("--engine-prefix", required=True,
                    help="engine symbol prefix (e.g. 'ntt_top__')")
    ap.add_argument("--engine-num-parts", type=int, required=True,
                    help="number of <prefix>presi_step_part_NNN functions")
    ap.add_argument("--engine-map", required=True,
                    help="path to engine's <engine>.presi_map.csv "
                         "(idx,spice,c_name) -- used both to look up "
                         "engine-side indices and to filter port bits "
                         "Yosys optimized out of the engine netlist")
    ap.add_argument("--gate-on-port", action="append", default=[],
                    help="port name whose value should be checked at "
                         "step_glue entry; if all `--gate-on-port` "
                         "values are zero, the step is skipped.  All "
                         "bits of the port are OR'd.  Repeat to gate "
                         "on multiple ports (e.g. an enable-input plus "
                         "the busy-output).")
    ap.add_argument("--out", required=True,
                    help="output C file (the per-engine glue)")
    args = ap.parse_args()

    ports = parse_engine_ports(args.engine_gates_v, args.engine)
    pin_seq = engine_pin_seq(ports)
    instance, bb_rows = parse_bb(args.abr_wrap_bb, args.instance, args.engine)
    abr_idx = parse_idx_map(args.abr_wrap_map)
    engine_idx = parse_idx_map(args.engine_map)
    gate_pins = find_gate_pins(args.gate_on_port, pin_seq, bb_rows)

    (total, ins, outs, skipped, const_in,
     dead_in, dead_out) = emit_glue(
        args.out, args.engine, instance, args.engine_prefix,
        args.engine_num_parts, pin_seq, bb_rows,
        abr_idx, engine_idx, gate_pins)
    print("engine-glue: %s/%s -> %s (%d bits, %d in (%d const-tied, "
          "%d dead-in-engine), %d out (%d const-in-abr_wrap, "
          "%d dead-in-engine), %d gate-bits)" %
          (args.engine, instance, args.out, total, ins, const_in, dead_in,
           outs, skipped, dead_out, len(gate_pins)))


if __name__ == "__main__":
    main()
