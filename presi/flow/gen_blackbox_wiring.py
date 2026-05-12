#!/usr/bin/env python3

# Read presi_bb.csv (per-pin blackbox subcircuit registry) and emit a C
# header that wires each blackbox instance to a model.  For now only the
# SRAMs are wired; engine modules and the abr_seq sequencer ROM (`_mem_v2`)
# get a `/* TODO */` placeholder so the harness compiles.
#
# The generated header is included from inside `presi_sram_tick_all()` in
# presi.c.  For each SRAM instance it samples we/waddr/wdata/re/raddr/
# wstrobe (write-strobe for the byte-enable variant), invokes the
# matching presi_sram_* helper, and distributes the read result back over
# the rdata_o bits.
#
# Pin matching note: Yosys's `opt` may prune unused address/data bits, so
# we cannot assume the declared port widths from sram.json.  Instead we
# walk the per-instance pin list (already in declaration order because
# write_spice keeps the blackboxed module's port_id ordering) and group
# consecutive pins by the suffix of their spice net name:
#   * exactly one pin named `..._<port>` for single-bit logical ports
#     (clk_i, we_i, re_i)
#   * a run of pins named `..._<port>.<bit>` for multi-bit ports
#     (waddr_i, wdata_i, raddr_i, rdata_o, wstrobe_i)

import argparse
import csv
import json
import re


SRAM_MODULES = ("abr_1r1w_ram", "abr_1r1w_be_ram")

# Paramod variants written by Yosys's write_spice as
# `_paramod_<hex>_<base_module>` (the original RTLIL name
# `$paramod$<hex>\<base>` gets `$` and `\` mangled to `_`).  Each
# paramod variant carries the per-instance widths so write_spice
# emits all addr/data bits instead of the default-truncated 6/32.
SRAM_PARAMOD_RE = re.compile(
    r"^_paramod_[0-9a-f]+_(abr_1r1w(?:_be)?_ram)$")


def is_sram_module(module):
    return module in SRAM_MODULES or SRAM_PARAMOD_RE.match(module) is not None

# abr_seq is blackboxed at the SV-module boundary in the gates flow (so
# `proc` doesn't burn 10+ minutes elaborating its 1024-way unique case).
# bb.csv records its 99 pins (clk, en_i, addr_i[10], data_o[87]) in SV
# port order, and we drive data_o each cycle from the ROM table emitted
# by extract_seq_rom.py.
SEQ_MODULE = "abr_seq"

# Logical abr_seq port suffixes -- these match the port declarations in
# adams-bridge/src/abr_top/rtl/abr_seq.sv.
SEQ_PORT_SUFFIXES = (
    "clk",
    "en_i",
    "addr_i",
    "data_o",
)

# Logical SRAM ports recognised by suffix.  Order matches the SystemVerilog
# module declaration in adams-bridge/src/abr_libs/rtl/abr_1r1w*ram.sv.
SRAM_PORT_SUFFIXES = (
    "clk_i",
    "we_i",
    "wstrobe_i",
    "waddr_i",
    "wdata_i",
    "re_i",
    "raddr_i",
    "rdata_o",
)
SRAM_PORT_RE = {
    name: re.compile(r"(?:.*[_.])?" + name + r"$") for name in SRAM_PORT_SUFFIXES
}



def c_ident(name):
    out = []
    for ch in name:
        if ch.isalnum():
            out.append(ch)
        else:
            out.append("_")
    s = "".join(out).strip("_")
    if not s or s[0].isdigit():
        s = "sram_" + s
    return s


def parse_bb(path):
    """Return [(instance, module, [(spice_name, c_name, idx), ...]), ...] in
    csv-file row order; pins per instance are ordered by pin_index.
    `idx` is the integer index into presi_s[] for that bit (or -1 for
    constants like PRESI_0/PRESI_1 that aren't in the array)."""
    rows = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append(r)
    # group by instance, preserving first-seen order
    seen = {}
    order = []
    for r in rows:
        inst = r["instance"]
        if inst not in seen:
            seen[inst] = (r["module"], [])
            order.append(inst)
        seen[inst][1].append(
            (int(r["pin_index"]), r["spice_name"], r["c_name"],
             int(r["idx"])))
    out = []
    for inst in order:
        module, pins = seen[inst]
        pins.sort()
        out.append((inst, module,
                    [(s, c, i) for _, s, c, i in pins]))
    return out


def parse_sram_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)["srams"]


def split_port(spice):
    """Return (base, bit) where bit is None for single-bit ports."""
    last_dot = spice.rfind(".")
    if last_dot == -1:
        return spice, None
    suffix = spice[last_dot + 1:]
    if suffix.isdigit():
        return spice[:last_dot], int(suffix)
    return spice, None


def classify_port(base):
    """Map a spice base name like 'abr_memory_export.sk_mem_bank1_we_i' to
    its logical SRAM port (we_i, waddr_i, ...).  Tries the longest suffix
    first so that 'we_i' does not steal 'wstrobe_i'."""
    for name in sorted(SRAM_PORT_SUFFIXES, key=len, reverse=True):
        if base == "clk" and name == "clk_i":
            return "clk_i"
        if base.endswith(name) and (
                len(base) == len(name)
                or base[-len(name) - 1] in "._"):
            return name
    return None


def group_pins(pins):
    """Return {port_name: [(c_name, idx), ...]} for one SRAM instance,
    with bits in LSB-first order."""
    ports = {}
    cur_base = None
    cur_port = None
    for spice, c_name, idx in pins:
        base, _bit = split_port(spice)
        if base != cur_base:
            cur_base = base
            cur_port = classify_port(base)
        if cur_port is None:
            continue
        ports.setdefault(cur_port, []).append((c_name, idx))
    return ports


def lookup_sram_index(srams, we_spice_base):
    """Match the bb.csv we_i wire base against sram.json's per-instance
    we_i port to find the array index used by presi_sram_descs[]."""
    for idx, s in enumerate(srams):
        if s["ports"]["we_i"] == we_spice_base:
            return idx, s
    raise SystemExit("no SRAM in sram.json matches we_i=%r" % we_spice_base)


# All SRAM bb signals live in the abr_wrap netlist, so we always read
# them via `presi_s[<idx>]`.  Indices are baked in directly from bb.csv
# (the integer column emitted by spice_to_c.py).

def _ref(rec):
    """Return the C expression for a (c_name, idx) record: array index
    if the bit is in presi_s[], else PRESI_0/PRESI_1 literal."""
    c_name, idx = rec
    if idx < 0:
        return c_name  # "PRESI_0" / "PRESI_1"
    return "presi_s[%d]" % idx


def _lhs_ref(rec):
    """Same as _ref but for assignment-target context.  Constants are
    not assignable, so the caller must check idx < 0 separately."""
    return _ref(rec)


def emit_in_word(f, dst, recs):
    """Build a uint32_t value from up to 32 single-bit netlist signals."""
    if not recs:
        f.write("\t\t%s = 0u;\n" % dst)
        return
    f.write("\t\t%s = 0u" % dst)
    for i, rec in enumerate(recs):
        f.write(" | ((uint32_t)(%s & 1) << %d)" % (_ref(rec), i))
    f.write(";\n")


def emit_in_words(f, dst_array, recs, word_count):
    for w in range(word_count):
        chunk = recs[w * 32:(w + 1) * 32]
        if not chunk:
            f.write("\t\t%s[%d] = 0u;\n" % (dst_array, w))
            continue
        f.write("\t\t%s[%d] = 0u" % (dst_array, w))
        for i, rec in enumerate(chunk):
            f.write(" | ((uint32_t)(%s & 1) << %d)" % (_ref(rec), i))
        f.write(";\n")


def emit_in_strobes(f, dst_array, recs, byte_count):
    """Each strobe netlist signal becomes one whole byte (0xFF or 0x00)."""
    for i in range(byte_count):
        if i < len(recs):
            f.write("\t\t%s[%d] = (%s & 1) ? 0xFFu : 0x00u;\n" %
                    (dst_array, i, _ref(recs[i])))
        else:
            f.write("\t\t%s[%d] = 0x00u;\n" % (dst_array, i))


def emit_out_bits(f, src_array, recs):
    """Write engine-output bits back to abr_wrap-side presi_s[] entries.
    Skip bits whose abr_wrap-side connection is a constant (Yosys folded
    the unused fanout)."""
    for i, rec in enumerate(recs):
        c_name, idx = rec
        if idx < 0:
            continue  # constant on abr_wrap side; nothing to drive
        word = i // 32
        bit = i % 32
        f.write("\t\tpresi_s[%d] = ((%s[%d] >> %d) & 1) ? PRESI_1 : PRESI_0;\n" %
                (idx, src_array, word, bit))


def emit_sram_block(f, inst_xname, sram_idx, sram, ports):
    instance = sram["instance"]
    depth = sram["depth"]
    data_w = sram["data_width"]
    addr_w = sram["addr_width"]
    byte_en = sram["byte_enable"]
    sram_macro = "PRESI_SRAM_" + c_ident(instance).upper()

    we = ports.get("we_i", [None])[0]
    re_ = ports.get("re_i", [None])[0]
    waddr = ports.get("waddr_i", [])
    wdata = ports.get("wdata_i", [])
    raddr = ports.get("raddr_i", [])
    rdata = ports.get("rdata_o", [])
    wstrobe = ports.get("wstrobe_i", [])

    if we is None or re_ is None:
        raise SystemExit("%s (%s): missing we_i/re_i pin" %
                         (instance, inst_xname))

    # KNOWN LIMITATION: Yosys's pre-hierarchy `blackbox abr_1r1w_ram` keeps
    # all SRAM cells at the module's *default* port widths (DEPTH=64,
    # DATA_WIDTH=32) and write_spice silently truncates wider connections.
    # We therefore use the bit counts that actually appear in the SPICE
    # output, not the per-instance widths from sram.json.  The harness's
    # SRAM storage stays at the full data_width so reads/writes are
    # internally well-defined; only the low <=data_width bits are ever
    # exercised because the netlist doesn't connect anything else.
    eff_data_w = max(len(wdata), len(rdata))
    eff_addr_w = max(len(waddr), len(raddr))
    words = (eff_data_w + 31) // 32 if eff_data_w else 0
    strobe_bytes = eff_data_w // 8 if byte_en else 0

    full_words = (data_w + 31) // 32
    full_strobe_bytes = data_w // 8 if byte_en else 0

    f.write("\t/* %s (%s)  declared depth=%u width=%u addr=%u%s\n"
            "\t * netlist exposes addr=%u (w%u/r%u) data=%u (w%u/r%u) */\n" %
            (instance, inst_xname, depth, data_w, addr_w,
             "  byte-enable" if byte_en else "",
             eff_addr_w, len(waddr), len(raddr),
             eff_data_w, len(wdata), len(rdata)))
    f.write("\t{\n")
    f.write("\t\tunsigned _we = %s & 1;\n" % _ref(we))
    f.write("\t\tunsigned _re = %s & 1;\n" % _ref(re_))
    f.write("\t\tuint32_t _waddr;\n")
    emit_in_word(f, "_waddr", waddr)
    f.write("\t\tuint32_t _raddr;\n")
    emit_in_word(f, "_raddr", raddr)
    # Storage is sized to the full declared data_width so reads/writes are
    # well-defined even when the netlist truncates the connection.
    f.write("\t\tuint32_t _wdata[%u] = {0};\n" % full_words)
    emit_in_words(f, "_wdata", wdata, full_words)
    if byte_en:
        f.write("\t\tuint8_t _wstrobe[%u] = {0};\n" % full_strobe_bytes)
        emit_in_strobes(f, "_wstrobe", wstrobe, full_strobe_bytes)
    f.write("\t\tuint32_t _rdata[%u];\n" % full_words)
    f.write("\t\tif (_we) {\n")
    if byte_en:
        f.write("\t\t\tpresi_sram_write_be(&m->srams[%s], _waddr, _wdata, _wstrobe);\n"
                % sram_macro)
    else:
        f.write("\t\t\tpresi_sram_write(&m->srams[%s], _waddr, _wdata);\n"
                % sram_macro)
    f.write("\t\t}\n")
    f.write("\t\tif (_re) {\n")
    f.write("\t\t\tpresi_sram_read(&m->srams[%s], _raddr, _rdata);\n"
            % sram_macro)
    emit_out_bits(f, "_rdata", rdata)
    f.write("\t\t}\n")
    f.write("\t}\n")


def split_seq_pins_positional(pins, abits, full_width):
    """abr_seq is blackboxed at the SV-module boundary, so write_spice
    emits its pins in SV declaration order: clk, en_i, addr_i[ABITS],
    data_o[FULL_WIDTH].  Walk the pin list positionally."""
    ports = {}
    expect = [
        ("clk", 1),
        ("en_i", 1),
        ("addr_i", abits),
        ("data_o", full_width),
    ]
    pidx = 0
    for name, count in expect:
        chunk = []
        for _ in range(count):
            if pidx >= len(pins):
                break
            spice, c_name, idx = pins[pidx]
            chunk.append((c_name, idx))
            pidx += 1
        if chunk:
            ports[name] = chunk
    return ports


def emit_seq_block(f, inst_xname, ports, full_width):
    """Emit the cycle body for the abr_seq blackbox.

    abr_seq's behavior (rtl/abr_seq.sv):
      always_ff @(posedge clk) begin
          if (en_i) data_o_rom <= ROM[addr_i];   // 1-cycle synchronous read
      end
      assign data_o = data_o_rom;

    **Critical:** data_o_rom is a FLOP, so data_o has a 1-cycle latency
    from any change in addr_i.  Reading addr_i from current presi_s and
    immediately driving data_o (the previous design) gives a 0-cycle
    combinational ROM, which exposes any cycle where `abr_prog_cntr_nxt`
    momentarily transients to the next PC under feedback from
    `ntt_busy_i` -- the abr_seq blackbox then races to ROM[next PC],
    `abr_instr.opcode.mode.ntt_mode` changes, and the engine sees mode
    flip mid-op.  This is the engine_cosim_divergence root cause for
    mlkem-keygen pc=462 (NTT(e[3])) and pc=467 (PWA) shortening.

    Fix: latch addr_i / en_i in a per-instance static state, sampled
    at the end of every presi_cycle (presi_sram_tick_all is the
    moment-just-before-the-next-rising-edge in the cycle model).
    Drive data_o each cycle from the PREVIOUSLY latched value, which
    gives the correct one-cycle latency Q := D@edge semantics.

    presi_abr_seq_rom[] holds the full SV-width (87-bit) values
    reassembled from the proc_rom-stripped INIT data plus the bit-map
    in abr_wrap.seq_rom.json."""
    addr_pins = ports.get("addr_i", [])
    data_pins = ports.get("data_o", [])
    en_pins = ports.get("en_i", [])

    # Make a per-instance suffix so multiple abr_seq blackboxes
    # (currently unlikely, but cheap insurance) wouldn't collide.
    sfx = inst_xname.replace(".", "_").replace("$", "_")

    f.write("\t/* abr_seq blackbox (%s)\n"
            "\t * netlist exposes addr=%u data=%u (full width %u);\n"
            "\t * driven from presi_abr_seq_rom[] in abr_wrap.seq_rom.h.\n"
            "\t * data_o is modeled as a 1-cycle synchronous read: this\n"
            "\t * call drives data_o from the addr that was latched at the\n"
            "\t * end of the *previous* cycle, then samples the current\n"
            "\t * addr_i for use at the next cycle. */\n" %
            (inst_xname, len(addr_pins), len(data_pins), full_width))
    f.write("\t{\n")
    f.write("\t\tstatic uint32_t _seq_addr_q_%s = 0u;\n" % sfx)
    f.write("\t\tstatic unsigned _seq_en_q_%s = 0u;\n" % sfx)
    f.write("\t\tuint32_t _addr_cur;\n")
    f.write("\t\tunsigned _en_cur;\n")

    # Drive data_o from the latched address (the value of addr_i
    # that was sampled at the last rising edge).
    for i, (c_name, idx) in enumerate(data_pins):
        if idx < 0:
            continue  # data_o bit is tied off in abr_wrap (unused)
        word = i // 32
        bit = i % 32
        f.write("\t\tpresi_s[%d] = (_seq_en_q_%s && ((presi_abr_seq_rom[_seq_addr_q_%s][%d] >> %d) & 1u)) ? PRESI_1 : PRESI_0;\n" %
                (idx, sfx, sfx, word, bit))

    # Sample current addr_i / en_i for use at the next rising edge.
    # If en_i is low at the sample point the flop holds its previous Q,
    # so we keep _seq_addr_q_<sfx> unchanged (and just record en_q).
    f.write("\t\t_en_cur = ")
    if en_pins:
        f.write("%s & 1;\n" % _ref(en_pins[0]))
    else:
        f.write("0u;  /* no en_i pin */\n")
    f.write("\t\t_addr_cur = 0u")
    for i, rec in enumerate(addr_pins):
        f.write(" | ((uint32_t)(%s & 1) << %d)" % (_ref(rec), i))
    f.write(";\n")
    f.write("\t\t_addr_cur &= (PRESI_ABR_SEQ_ROM_SIZE - 1u);\n")
    f.write("\t\tif (_en_cur) {\n"
            "\t\t\t_seq_addr_q_%s = _addr_cur;\n"
            "\t\t}\n"
            "\t\t_seq_en_q_%s = _en_cur;\n" % (sfx, sfx))
    f.write("\t}\n")


def emit(out_path, instances, srams, seq_meta):
    sram_blocks = []
    seq_blocks = []
    other = []
    seq_full_width = seq_meta["full_width"] if seq_meta is not None else 87
    seq_abits = seq_meta["abits"] if seq_meta is not None else 10
    for inst, module, pins in instances:
        if is_sram_module(module):
            ports = group_pins(pins)
            we_pins = [(s, c, i) for s, c, i in pins
                       if classify_port(split_port(s)[0]) == "we_i"]
            if not we_pins:
                raise SystemExit("%s: no we_i pin found" % inst)
            we_base = split_port(we_pins[0][0])[0]
            idx, sram = lookup_sram_index(srams, we_base)
            sram_blocks.append((inst, idx, sram, ports))
            continue
        if module == SEQ_MODULE:
            ports = split_seq_pins_positional(pins, seq_abits, seq_full_width)
            seq_blocks.append((inst, ports))
            continue
        other.append((inst, module))

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("/* Generated by gen_blackbox_wiring.py.  Do not edit.\n"
                " *\n"
                " * Body of presi_sram_tick_all().  Each block samples one\n"
                " * SRAM blackbox instance's input pins from the netlist,\n"
                " * calls the matching presi_sram_* helper, and writes the\n"
                " * resulting rdata_o back over the netlist bits. */\n")
        for inst, idx, sram, ports in sorted(sram_blocks, key=lambda b: b[1]):
            emit_sram_block(f, inst, idx, sram, ports)
        if other:
            f.write("\n\t/* Unwired blackboxes (engines awaiting models):\n")
            for inst, module in other:
                f.write("\t *   %-12s  %s\n" % (inst, module))
            f.write("\t */\n")

    # Emit the abr_seq tick into a separate header so it can be called at
    # the flop-tick boundary in presi_cycle, NOT post-settle.  Putting
    # the seq ROM read at post-settle (as part of sram_tick_all) gave
    # subtle races: addr_i recomputed during the settle pass differs
    # from the value that abr_prog_cntr.D was sampled with at the rising
    # edge, so the registered seq ROM output disagreed with abr_prog_cntr.
    # See engine_cosim_divergence for the manifestation.
    seq_out_path = out_path.replace("presi_bb_wiring.h", "presi_seq_tick.h")
    if seq_out_path == out_path:
        # Fallback: stick a `.seq` suffix in front of `.h`.
        seq_out_path = out_path[:-2] + ".seq.h" if out_path.endswith(".h") \
                       else out_path + ".seq"
    with open(seq_out_path, "w", encoding="utf-8") as f:
        f.write("/* Generated by gen_blackbox_wiring.py.  Do not edit.\n"
                " *\n"
                " * Body of presi_seq_tick().  Models the abr_seq ROM\n"
                " * (one synchronous-read flop per data-bit) as a tick\n"
                " * that fires at the rising edge of clk: reads the\n"
                " * current addr_i / en_i from presi_s[] (which at the\n"
                " * call site reflects PRE-flop-tick comb output), and\n"
                " * drives data_o = ROM[addr] when en_i is high.  Must be\n"
                " * called between phase-1 comb and step_netlist_flop so\n"
                " * abr_prog_cntr.D and the seq ROM sample the same\n"
                " * abr_prog_cntr_nxt comb value. */\n")
        for inst, ports in seq_blocks:
            emit_seq_block(f, inst, ports, seq_full_width)
    return len(sram_blocks), len(seq_blocks), len(other)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bb", required=True)
    ap.add_argument("--sram-json", required=True)
    ap.add_argument("--seq-rom-json", default=None,
                    help="optional: extract_seq_rom.py JSON for "
                         "positional pin fallback")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    instances = parse_bb(args.bb)
    srams = parse_sram_json(args.sram_json)
    seq_meta = None
    if args.seq_rom_json:
        try:
            with open(args.seq_rom_json, encoding="utf-8") as f:
                seq_meta = json.load(f)
        except FileNotFoundError:
            seq_meta = None
    sram_count, seq_count, other_count = emit(
        args.out, instances, srams, seq_meta)
    print("blackbox-wiring: %d SRAMs wired, %d seq ROM(s) wired, "
          "%d engine blackboxes still TODO" %
          (sram_count, seq_count, other_count))


if __name__ == "__main__":
    main()
