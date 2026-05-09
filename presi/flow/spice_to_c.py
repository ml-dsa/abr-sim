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


# ---------- Emit-time peephole folding ----------
#
# When Yosys ties a cell input to a constant 0/1, the cell's bitwise
# output simplifies algebraically.  Folding at emit time turns lines
# like `Q = PRESI_0 | ((PRESI_0 ^ PRESI_1) & ~R & ...)` into the
# equivalent shorter `Q = ~R & ...`, which roughly halves the source
# size of DFFSR-heavy chunks (the common case is S=PRESI_0 with R
# either PRESI_0 or a real reset signal).
#
# Inputs are C-expression strings produced by NameMap.ref() or by
# previous fold steps.  The literals "PRESI_0" / "PRESI_1" are
# recognized; everything else is treated as a runtime expression and
# left wrapped in parentheses.

_K0 = "PRESI_0"
_K1 = "PRESI_1"


def _is0(e): return e == _K0
def _is1(e): return e == _K1


def fold_not(a):
    if _is0(a):
        return _K1
    if _is1(a):
        return _K0
    # `~x` over a uint8_t promotes to int (-Woverflow trips on `~PRESI_1`
    # as a constant, but the folder above eliminates the only constant
    # case before this branch is reached, so `~<expr>` here is always
    # over a runtime presi_t expression and the truncation back to
    # uint8_t is what gcc actually emits).  Cheaper than `^ PRESI_1`
    # because it doesn't materialise the 0xFF constant on the right.
    return "~%s" % a if a.startswith(("(", "s[", "~")) else "~(%s)" % a


def fold_and(a, b):
    if _is0(a) or _is0(b):
        return _K0
    if _is1(a):
        return b
    if _is1(b):
        return a
    return "(%s & %s)" % (a, b)


def fold_or(a, b):
    if _is1(a) or _is1(b):
        return _K1
    if _is0(a):
        return b
    if _is0(b):
        return a
    return "(%s | %s)" % (a, b)


def fold_xor(a, b):
    if _is0(a):
        return b
    if _is0(b):
        return a
    if _is1(a):
        return fold_not(b)
    if _is1(b):
        return fold_not(a)
    return "(%s ^ %s)" % (a, b)


# Templates: (cell_name, n_pins) -> (output_pin_index, lambda(c_refs) -> C stmt).
#
# All cells emit branchless straight-line C: each statement is a single
# bitwise expression on `presi_t` operands.  presi_t is uint8_t and the
# whole flow maintains the all-bits-set / all-bits-cleared invariant
# (PRESI_1 = 0xFF, PRESI_0 = 0x00), so AND/OR/XOR/etc. compose without
# `& 1` masks and bit 0 of every result is always the logical value.
#
# `~x` is the inversion form (one machine NOT, no immediate load),
# applied only to runtime expressions; the peephole folder collapses
# `~PRESI_0` / `~PRESI_1` to the opposite constant before emit, so
# the `-Woverflow` case (~ over a known-0xFF constant trips int
# promotion) never reaches the source.
#
# write_spice port order (Yosys "guessed" order = reverse cell-connection
# iteration; output first, then inputs reversed):
#
#   $_NOT_(A,Y)              SPICE (Y, A)
#   $_AND_/$_OR_/$_NAND_/    SPICE (Y, B, A)         -- symmetric, irrelevant
#     $_NOR_/$_XOR_/$_XNOR_
#   $_ANDNOT_(A,B,Y) = A&~B  SPICE (Y, B, A)
#   $_ORNOT_(A,B,Y)  = A|~B  SPICE (Y, B, A)
#   $_MUX_(A,B,S,Y) = S?B:A  SPICE (Y, S, B, A)
#
# Each entry: (output_pin_index, lambda(n) -> stmt).  The output index
# tells the topo sort which net is driven.
COMBINATIONAL = {
    # BUF/NOT/NAND/NOR with the original cmos_cells.v declarations follow
    # input-first order (those modules are not pruned because dfflibmap+abc
    # emit them).  Kept for backward compatibility with the abc-based flow.
    ("BUF",  2):       (1, lambda n: "%s = %s;" % (n[1], n[0])),
    ("NOT",  2):       (1, lambda n: "%s = %s;" % (n[1], fold_not(n[0]))),
    ("NAND", 3):       (2, lambda n: "%s = %s;" %
                                     (n[2], fold_not(fold_and(n[0], n[1])))),
    ("NOR",  3):       (2, lambda n: "%s = %s;" %
                                     (n[2], fold_not(fold_or(n[0], n[1])))),
    # Yosys gate primitives.  Output first, then inputs in reverse insertion
    # order.  For symmetric operators the input order is irrelevant.
    ("__NOT_",     2): (0, lambda n: "%s = %s;" % (n[0], fold_not(n[1]))),
    ("__AND_",     3): (0, lambda n: "%s = %s;" %
                                     (n[0], fold_and(n[2], n[1]))),
    ("__OR_",      3): (0, lambda n: "%s = %s;" %
                                     (n[0], fold_or(n[2], n[1]))),
    ("__NAND_",    3): (0, lambda n: "%s = %s;" %
                                     (n[0], fold_not(fold_and(n[2], n[1])))),
    ("__NOR_",     3): (0, lambda n: "%s = %s;" %
                                     (n[0], fold_not(fold_or(n[2], n[1])))),
    ("__XOR_",     3): (0, lambda n: "%s = %s;" %
                                     (n[0], fold_xor(n[2], n[1]))),
    ("__XNOR_",    3): (0, lambda n: "%s = %s;" %
                                     (n[0], fold_not(fold_xor(n[2], n[1])))),
    # SPICE (Y, B, A) maps to Y = A & ~B / Y = A | ~B (B is the inverted leg).
    ("__ANDNOT_",  3): (0, lambda n: "%s = %s;" %
                                     (n[0], fold_and(n[2], fold_not(n[1])))),
    ("__ORNOT_",   3): (0, lambda n: "%s = %s;" %
                                     (n[0], fold_or(n[2], fold_not(n[1])))),
    # Branchless mux: Y = (S & B) | (~S & A).  Folded so that a
    # constant select collapses to one branch's expression.
    ("__MUX_",     4): (0, lambda n: "%s = %s;" %
                                     (n[0],
                                      fold_or(fold_and(n[1], n[2]),
                                              fold_and(fold_not(n[1]),
                                                       n[3])))),
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
    """Assigns each SPICE net a stable integer index in a flat
    `<prefix>presi_s[]` array.  ref() returns a C expression of the
    form 's[<idx>]' (or the constant 'PRESI_0' / 'PRESI_1' for
    tied-off pins).  The leading `s` matches the parameter name of
    the generated step / chunk functions:
        static void chunk_NNN(presi_t *s) { s[i] = ...; }
    Each part .c gets `presi_t *s` as a function arg; chunked helpers
    pass it through.  Editing every reference into the array as
    `s[idx]` instead of `<prefix>presi_s[idx]` cuts source size
    ~50-60 % (the prefix vanished on every reference) and lets gcc
    keep the array pointer in a register for the whole function --
    no relocation per access.

    The C name is preserved separately for the map CSV and the
    optional index header.

    This is the central architectural change vs. the previous "one
    extern per net" scheme: with millions of nets, a single contiguous
    array is dramatically friendlier to gcc (collapses the symbol
    table and relocations), to the cache (sequential indices stream),
    and to TVLA (per-cycle delta = byte-wise XOR + popcount over the
    whole array)."""
    def __init__(self, prefix=""):
        # `prefix` is the symbol prefix used by the caller for cross-
        # netlist link safety, e.g. "ntt_top__".  Empty for abr_wrap.
        # The array name itself is `<prefix>presi_s`.
        self.prefix = prefix
        self.names = {}        # spice_name -> idx
        self.cnames = {}       # spice_name -> c_name (after cname() + prefix + uniquify)
        self.order = []        # list of c_names, indexed by idx
        self.set = set()       # for c_name uniqueness

    def array(self):
        return self.prefix + "presi_s"

    def _intern(self, name):
        idx = self.names.get(name)
        if idx is not None:
            return idx
        c = self.prefix + cname(name)
        base = c
        i = 1
        while c in self.set:
            c = "%s_%d" % (base, i)
            i += 1
        idx = len(self.order)
        self.names[name] = idx
        self.cnames[name] = c
        self.order.append(c)
        self.set.add(c)
        return idx

    def ref(self, name):
        """C expression for the value of net `name`.  Returns 's[<idx>]'
        so the emitted statements drop into a function with a
        `presi_t *s` parameter; constants stay symbolic."""
        if name == "0s":
            return "PRESI_0"
        if name == "1s":
            return "PRESI_1"
        return "s[%d]" % self._intern(name)

    def idx(self, name):
        """Integer index of `name` (must be a real net, not a constant).
        For consumers (e.g. gen_engine_glue.py) that read the map CSV
        and need to substitute literal indices into generated code."""
        return self._intern(name)


def parse_instance(line):
    fields = line.split()
    if len(fields) < 3 or not fields[0].startswith("X"):
        return None
    return fields


def emit_dff(kind, names, pins, flops, clk_prev):
    """Branchless edge-triggered D flip-flop.

    SPICE port order:
      "DFF"        (C, D, Q)
      "__DFF_P_"   (Q, C, D)  -- simplemap output, reverse of insertion

    Semantics:
      edge = clk & ~clk_prev   (1 on rising edge, 0 otherwise)
      Q    = (edge & D) | (~edge & Q)

    NOT is expressed as `^ PRESI_1` to keep the all-bits invariant
    intact under integer promotion (and to dodge -Woverflow).  No
    conditional branch: each emitted statement is a single bitwise
    expression on `presi_t` operands.  gcc -O0 lowers it to a few
    AND/OR/XOR machine instructions per flop with no jumps.

    The harness keeps `<prefix>presi_clk_prev` mirroring the clock
    value from the previous presi_step_netlist call; multiple settle
    passes within one logical cycle leave the flop unchanged because
    `clk & ~clk_prev` is 0 outside the rising edge.

    Returns a metadata dict (lhs, rhs, stmt, is_flop) so the topo
    sort over comb cells can ignore flops (flop outputs are stable
    during a comb pass).
    """
    if kind == "DFF":
        clk, d, q = pins
    else:  # "__DFF_P_": SPICE (Q, C, D)
        q, clk, d = pins
    qref = names.ref(q)
    dref = names.ref(d)
    cref = names.ref(clk)
    # The harness calls `*_step_part_NNN_flop()` only on the rising edge
    # (presi_cycle phase 1; never phase 0 or settle), so `_edge` is
    # always 1 when the body executes.  Specialise: substitute PRESI_1
    # for `_edge` and let the peephole folder collapse the multiplexer
    # to `Q = D` (or, for DFFSR, `Q = S | (~S & ~R & D)`).  Saves ~6
    # operations per flop and keeps the chunk free of the `_edge` local.
    body = fold_or(fold_and(_K1, dref),
                   fold_and(fold_not(_K1), qref))
    stmt = "%s = %s;" % (qref, body)
    flops.add(qref)
    return {"stmt": stmt, "lhs": qref, "rhs": [cref, dref], "is_flop": True}


def emit_dffsr(kind, names, pins, flops, clk_prev):
    """Branchless DFFSR: active-high level-sensitive S/R, rising-edge D->Q.

    SPICE port order:
      "DFFSR"        (C, D, Q, S, R)
      "__DFFSR_PPP_" (Q, D, R, S, C)  -- dfflegalize output reversed

    Semantics (S beats R beats edge transfer, matching the active-high
    PPP cell):
      edge = clk & ~clk_prev
      Q    = S | (~S & ~R & ((edge & D) | (~edge & Q)))

    Single bitwise expression per cell, no branches.  When the user's
    Yosys flow ties S or R to PRESI_0 (the common "set unused" / "reset
    unused" case), the corresponding mask `(PRESI_0 ^ PRESI_1) = PRESI_1`
    is computed at runtime; gcc could fold it at -O1+ but we accept
    the small runtime cost in exchange for a uniform emit shape.
    Treated as a flop for topo-sort purposes.
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
    # See emit_dff: the harness calls _flop only on the rising edge,
    # so `_edge` is always 1.  Substitute PRESI_1 and fold; the
    # transfer collapses to D, leaving body = S | (~S & ~R & D).
    transfer = fold_or(fold_and(_K1, dref),
                       fold_and(fold_not(_K1), qref))
    body = fold_or(sref,
                   fold_and(fold_not(sref),
                            fold_and(fold_not(rref), transfer)))
    stmt = "%s = %s;" % (qref, body)
    flops.add(qref)
    return {"stmt": stmt, "lhs": qref,
            "rhs": [sref, rref, cref, dref], "is_flop": True}


def write_var_header(path, top, num_nets, prefix, clk_prev):
    """The var.h is now tiny: a typedef, two constants, the array
    declaration, the clk-prev scalar, and the size constant.  No more
    millions of `extern presi_t <name>;` lines."""
    guard = "PRESI_%s_VAR_H" % top.upper()
    array_name = prefix + "presi_s"
    nets_macro = ("%sPRESI_NETS" % prefix.upper()) if prefix else "PRESI_NETS"
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
        f.write("\n")
        f.write("/* %d nets in this netlist; flat byte array indexed by\n"
                " * the integer keys recorded in <top>.presi_map.csv.\n"
                " * For named-port access, see <top>.presi_idx.h. */\n" %
                num_nets)
        f.write("#define %s %d\n" % (nets_macro, num_nets))
        f.write("extern presi_t %s[%s];\n" % (array_name, nets_macro))
        f.write("\n")
        f.write("/* Last-clock snapshot for edge-triggered DFFs.  The\n"
                " * harness updates this just before each step_netlist\n"
                " * call so the rising-edge predicate `(clk & ~%s)`\n"
                " * fires exactly once per logical cycle. */\n" % clk_prev)
        f.write("extern presi_t %s;\n" % clk_prev)
        f.write("#endif\n")


def write_var_definitions(path, header_basename, num_nets, prefix, clk_prev):
    """The var.c is now a single array allocation plus the clk-prev
    scalar.  Compiles in seconds vs. minutes for the millions-of-
    globals layout."""
    array_name = prefix + "presi_s"
    nets_macro = ("%sPRESI_NETS" % prefix.upper()) if prefix else "PRESI_NETS"
    with open(path, "w", encoding="utf-8") as f:
        f.write("/* Generated by spice_to_c.py.  Do not edit. */\n")
        f.write('#include "%s"\n' % header_basename)
        f.write("presi_t %s[%s];\n" % (array_name, nets_macro))
        f.write("presi_t %s;\n" % clk_prev)


def write_idx_header(path, top, name_map, prefix):
    """Emit `#define IDX_<c_name> <int>` for every named net.  Big file
    (one line per net) but cheap for gcc to preprocess; consumers
    (presi.c, gen_engine_glue.py output) pick out the few hundred
    names they need.  Could be split or trimmed later if compile time
    of consumers grows."""
    guard = "PRESI_%s_IDX_H" % top.upper()
    # name_map.cnames: spice_name -> c_name; name_map.names: spice_name -> idx
    items = sorted(name_map.cnames.items(), key=lambda x: name_map.names[x[0]])
    with open(path, "w", encoding="utf-8") as f:
        f.write("/* Generated by spice_to_c.py.  Do not edit. */\n")
        f.write("#ifndef %s\n#define %s\n" % (guard, guard))
        f.write("/* Index of each named net into %spresi_s[]. */\n" % prefix)
        for spice_name, c_name in items:
            f.write("#define IDX_%s %d\n" %
                    (c_name, name_map.names[spice_name]))
        f.write("#endif\n")


def write_part_files(parts_dir, top, header_basename, statements, num_parts,
                     step_fn_prefix, chunk_size, array_name, manifest_path,
                     symbol_prefix, clk_pin_idx, clk_prev):
    """Per-part code emission with comb/flop kind separation.

    Each part's slice of the topo-sorted statement list is split into
    two **kinds** by the `is_flop` flag (already correctly grouped:
    topo_order_comb output first, flop_items last):

    * **comb chunks** -- `<top>.presi_clk_part_NNN_comb_MMM.c`
      Pure combinational evaluation; no `_edge`, no clk_prev read.
      Called on every step_netlist phase (falling-edge, rising-edge,
      and settle) since comb wires must always be re-evaluated.

    * **flop chunks** -- `<top>.presi_clk_part_NNN_flop_MMM.c`
      Pure flop tick statements (DFF + DFFSR).  These chunks are
      called only on the rising-edge phase, so `emit_dff` /
      `emit_dffsr` substitute PRESI_1 for the edge mask and the
      peephole folder reduces each cell to its edge=1 specialisation
      (`Q = D` for DFF; `Q = S | (~S & ~R & D)` for DFFSR).  No
      `_edge` local, no `clk_prev` read, no edge multiplexer in
      the source.

    Each part also emits a small dispatcher file
    (`<top>.presi_clk_part_NNN.c`) with two public functions:
      `<prefix>presi_step_part_NNN_comb(presi_t *s)`
      `<prefix>presi_step_part_NNN_flop(presi_t *s)`
    that call the comb / flop chunks of that part in order.  If a
    part has no flops the flop dispatcher is still emitted but with
    an empty body, so the harness can call it unconditionally without
    a per-part check.

    `manifest_path` receives a Make snippet listing every emitted .c
    basename in `<TOP>_CHUNK_C`; presi/Makefile -includes it so the
    build system can list every .o without knowing the chunk counts
    a priori.

    Why one TU per chunk: gcc parses, types, and codegens an entire
    .c file as one unit, so a 42 MB monolith dominates wall time
    even with -O0.  Per-chunk TUs let `make -j` parallelize the
    compile and keep each chunk's symbol table small enough that
    -O1 is also tractable.
    """
    n = len(statements)
    if n == 0:
        per_part = 0
    else:
        per_part = (n + num_parts - 1) // num_parts

    emitted = []  # basenames of every .c file we wrote (for the manifest)

    def emit_chunks(kind, slice_lo, slice_hi, idx):
        """Emit one .c file per chunk_size slice of `kind` ('comb' or
        'flop').  Returns the list of (basename, func_name) tuples
        that the dispatcher will reference.  `kind` controls whether
        the body needs the `_edge` local (only flops do)."""
        chunk_funcs = []
        n_kind = slice_hi - slice_lo
        if n_kind == 0:
            return chunk_funcs
        if chunk_size > 0:
            n_chunks = (n_kind + chunk_size - 1) // chunk_size
        else:
            n_chunks = 1
        for c in range(n_chunks):
            cstart = slice_lo + c * chunk_size if chunk_size > 0 else slice_lo
            cend = min(cstart + chunk_size, slice_hi) if chunk_size > 0 else slice_hi
            chunk_basename = ("%s.presi_clk_part_%03d_%s_%03d.c" %
                              (top, idx, kind, c))
            chunk_path = os.path.join(parts_dir, chunk_basename)
            func_name = ("%schunk_%03d_%s_%03d" %
                         (symbol_prefix, idx, kind, c))
            with open(chunk_path, "w", encoding="utf-8") as f:
                f.write("/* Generated by spice_to_c.py.  Do not edit. */\n")
                f.write('#include "%s"\n' % header_basename)
                f.write("\nvoid %s(presi_t *s)\n{\n" % func_name)
                # No `_edge` local: emit_dff / emit_dffsr substitute
                # PRESI_1 for the mask (flop chunks only run on the
                # rising edge), so the peephole folder produced
                # branch-free, edge-free statements.
                for i in range(cstart, cend):
                    f.write("\t%s\n" % statements[i][0])
                f.write("}\n")
            emitted.append(chunk_basename)
            chunk_funcs.append((chunk_basename, func_name))
        return chunk_funcs

    for idx in range(num_parts):
        start = idx * per_part
        end = min(start + per_part, n) if per_part else 0

        # Split the part's slice into comb/flop runs.  Topo order
        # already groups comb_ordered first then flop_items, so the
        # `is_flop` flag transitions at most once per part; the split
        # point is the first flop entry (if any).
        comb_lo = start
        comb_hi = start
        for i in range(start, end):
            if statements[i][1]:  # is_flop
                break
            comb_hi = i + 1
        flop_lo = comb_hi
        flop_hi = end
        # Sanity: there should be no comb stmt after a flop stmt within
        # a single part.
        for i in range(flop_lo, flop_hi):
            if not statements[i][1]:
                raise SystemExit("comb stmt after flop stmt in part %d "
                                 "(topo order broken)" % idx)

        dispatcher_basename = "%s.presi_clk_part_%03d.c" % (top, idx)
        dispatcher_path = os.path.join(parts_dir, dispatcher_basename)

        comb_funcs = emit_chunks("comb", comb_lo, comb_hi, idx)
        flop_funcs = emit_chunks("flop", flop_lo, flop_hi, idx)

        with open(dispatcher_path, "w", encoding="utf-8") as f:
            f.write("/* Generated by spice_to_c.py.  Do not edit. */\n")
            f.write('#include "%s"\n' % header_basename)
            for _bn, fn in comb_funcs + flop_funcs:
                f.write("extern void %s(presi_t *);\n" % fn)
            # Always emit both step_part_NNN_{comb,flop}, even when
            # one is empty -- the harness calls them unconditionally
            # and the empty-body inline cost at -O0 is one ret.
            f.write("\nvoid %spresi_step_part_%03d_comb(presi_t *s)\n{\n" %
                    (step_fn_prefix, idx))
            if not comb_funcs:
                f.write("\t(void) s;\n")
            for _bn, fn in comb_funcs:
                f.write("\t%s(s);\n" % fn)
            f.write("}\n")
            f.write("\nvoid %spresi_step_part_%03d_flop(presi_t *s)\n{\n" %
                    (step_fn_prefix, idx))
            if not flop_funcs:
                f.write("\t(void) s;\n")
            for _bn, fn in flop_funcs:
                f.write("\t%s(s);\n" % fn)
            f.write("}\n")
        emitted.append(dispatcher_basename)

    # Manifest: a Make snippet `-include`d by presi/Makefile.  The
    # variable name is uppercased <TOP>_CHUNK_C; values are basenames
    # only (Makefile prepends $(BUILD)/).
    var_name = "%s_CHUNK_C" % top.upper()
    with open(manifest_path, "w", encoding="utf-8") as f:
        f.write("# Generated by spice_to_c.py.  Do not edit.\n")
        f.write("%s := %s\n" % (var_name, " ".join(emitted)))


def write_clk_dispatch(path, num_parts, step_fn_prefix, array_name, kind):
    """Generated dispatcher header for one phase kind ("comb" or
    "flop").  Each part has two public step functions; this header
    emits the externs + call sequence for the requested kind.

    Included from inside `presi_step_netlist_comb()` /
    `presi_step_netlist_flop()` (or the per-engine glue's matching
    function).  Block-scope extern decls are legal C99 and stay
    local to that function.

    The flop header is still emitted for netlists that have no
    flops (the per-part dispatcher's flop body is just empty), so
    the harness can call either flavor unconditionally.
    """
    with open(path, "w", encoding="utf-8") as f:
        f.write("/* Generated by spice_to_c.py.  Do not edit. */\n")
        for i in range(num_parts):
            f.write("extern void %spresi_step_part_%03d_%s(presi_t *);\n" %
                    (step_fn_prefix, i, kind))
        for i in range(num_parts):
            f.write("%spresi_step_part_%03d_%s(%s);\n" %
                    (step_fn_prefix, i, kind, array_name))


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


def translate(spice_file, var_header, var_c, clk_comb, clk_flop, parts_dir,
              num_parts, map_file, bb_file, top, manifest, symbol_prefix="",
              chunk_size=8192):
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
            #
            # Record both the c_name (debug-friendly identifier) and
            # the integer idx (-1 for tied-off constants) so consumers
            # can emit `presi_s[<idx>]` directly.
            pin_recs = []
            for p in pins:
                # Force ref() to assign indices for real nets; we
                # ignore its return value here.
                names.ref(p)
                if p == "0s":
                    pin_recs.append(("PRESI_0", -1))
                elif p == "1s":
                    pin_recs.append(("PRESI_1", -1))
                else:
                    pin_recs.append((names.cnames[p], names.names[p]))
            bb_instances.append((inst, module, list(zip(pins, pin_recs))))
            blackbox_modules[module] = blackbox_modules.get(module, 0) + 1
            items.append({"stmt": "/* blackbox %s %s pins=%d */" %
                          (inst, module, len(pins)),
                          "lhs": None, "rhs": [], "is_flop": False})

    # Filter constants and the clk_prev scalar out of every rhs list:
    # the topo sort only needs to track real-net dependencies.  rhs
    # values are now C expressions like "presi_s[123]" or "PRESI_0";
    # exclude the latter and the clk_prev scalar.
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
    ordered = list(comb_ordered) + list(flop_items)
    # Pass (stmt, is_flop) tuples through to the chunk emitter so it can
    # decide whether a chunk needs a `_edge` local at its top.
    statements = [(it["stmt"], it["is_flop"]) for it in ordered]

    num_nets = len(names.order)
    header_basename = os.path.basename(var_header)

    # The flop emit references a chunk-local `_edge` computed from the
    # netlist's clk pin and clk_prev global.  Resolve the clk pin's
    # index once here so the chunk emitter can bake a literal `s[<N>]`
    # into the declaration.  Netlists without flops never declare _edge.
    clk_pin_idx = None
    if flop_items:
        spice_clk = "clk"
        if spice_clk not in names.names:
            raise SystemExit("emit_dff/emit_dffsr: no `clk` net in "
                             "namemap for %s; rename or extend the "
                             "lookup" % top)
        clk_pin_idx = names.names[spice_clk]

    write_var_header(var_header, top, num_nets, symbol_prefix, clk_prev)
    write_var_definitions(var_c, header_basename, num_nets,
                          symbol_prefix, clk_prev)
    os.makedirs(parts_dir, exist_ok=True)
    array_name = symbol_prefix + "presi_s"
    write_part_files(parts_dir, top, header_basename, statements, num_parts,
                     step_fn_prefix, chunk_size, array_name, manifest,
                     symbol_prefix, clk_pin_idx, clk_prev)
    write_clk_dispatch(clk_comb, num_parts, step_fn_prefix, array_name, "comb")
    write_clk_dispatch(clk_flop, num_parts, step_fn_prefix, array_name, "flop")

    # The index header lives next to the var header; consumers
    # (presi.c, gen_engine_glue.py output) include it to look up
    # named-net indices.
    idx_header = var_header.replace(".presi_var.h", ".presi_idx.h", 1)
    if idx_header == var_header:
        idx_header = var_header + ".idx.h"
    write_idx_header(idx_header, top, names, symbol_prefix)

    # Map CSV: idx, spice_name, c_name.  This is the canonical lookup
    # for tools that need to substitute literal indices into generated
    # code (gen_engine_glue.py, future debug aids).
    with open(map_file, "w", encoding="utf-8") as f:
        f.write("idx,spice_name,c_name\n")
        for spice_name, idx in sorted(names.names.items(),
                                      key=lambda x: x[1]):
            f.write("%d,%s,%s\n" % (idx, spice_name, names.cnames[spice_name]))

    with open(bb_file, "w", encoding="utf-8") as f:
        f.write("instance,module,pin_index,spice_name,c_name,idx\n")
        for inst, module, pin_list in bb_instances:
            for pi, (spice, (c, sidx)) in enumerate(pin_list):
                f.write("%s,%s,%d,%s,%s,%d\n" %
                        (inst, module, pi, spice, c, sidx))

    cells = len(statements)
    print("translated %d statements into %d parts, %d nets, "
          "%d blackbox instances (%d distinct modules)" %
          (cells, num_parts, num_nets, len(bb_instances),
           len(blackbox_modules)))
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
    ap.add_argument("--clock-comb", required=True,
                    help="dispatch header for the comb phase "
                         "(extern decls + calls of step_part_NNN_comb)")
    ap.add_argument("--clock-flop", required=True,
                    help="dispatch header for the flop (rising edge) "
                         "phase (extern decls + calls of step_part_NNN_flop)")
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
    ap.add_argument("--chunk-size", type=int, default=8192,
                    help="split each part body into helper functions "
                         "of this many statements each, each in its "
                         "own .c file (one TU per chunk so make -j "
                         "parallelizes them).  0 disables chunking "
                         "(one body inline in the part .c).")
    ap.add_argument("--manifest", required=True,
                    help="path of the Make snippet listing every "
                         "emitted .c basename; -include'd by the "
                         "Makefile so it can list the chunk .o "
                         "targets without knowing the count a priori.")
    args = ap.parse_args()
    if args.num_parts < 1:
        raise SystemExit("--num-parts must be >= 1")
    translate(args.spice, args.vars, args.vars_c, args.clock_comb,
              args.clock_flop, args.parts_dir, args.num_parts, args.map,
              args.bb, args.top, args.manifest,
              symbol_prefix=args.symbol_prefix,
              chunk_size=args.chunk_size)


if __name__ == "__main__":
    main()
