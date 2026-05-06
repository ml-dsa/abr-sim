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
    """Return [(instance, module, [(spice_name, c_name), ...]), ...] in
    csv-file row order; pins per instance are ordered by pin_index."""
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
        seen[inst][1].append((int(r["pin_index"]), r["spice_name"], r["c_name"]))
    out = []
    for inst in order:
        module, pins = seen[inst]
        pins.sort()
        out.append((inst, module, [(s, c) for _, s, c in pins]))
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
    """Return {port_name: [c_names_lsb_first]} for one SRAM instance."""
    ports = {}
    cur_base = None
    cur_port = None
    for spice, c_name in pins:
        base, _bit = split_port(spice)
        if base != cur_base:
            cur_base = base
            cur_port = classify_port(base)
        if cur_port is None:
            continue
        ports.setdefault(cur_port, []).append(c_name)
    return ports


def lookup_sram_index(srams, we_spice_base):
    """Match the bb.csv we_i wire base against sram.json's per-instance
    we_i port to find the array index used by presi_sram_descs[]."""
    for idx, s in enumerate(srams):
        if s["ports"]["we_i"] == we_spice_base:
            return idx, s
    raise SystemExit("no SRAM in sram.json matches we_i=%r" % we_spice_base)


def emit_in_word(f, dst, names):
    """Build a uint32_t value from up to 32 single-bit C variables."""
    if not names:
        f.write("\t\t%s = 0u;\n" % dst)
        return
    f.write("\t\t%s = 0u" % dst)
    for i, n in enumerate(names):
        f.write(" | ((uint32_t)(%s & 1) << %d)" % (n, i))
    f.write(";\n")


def emit_in_words(f, dst_array, names, word_count):
    for w in range(word_count):
        chunk = names[w * 32:(w + 1) * 32]
        if not chunk:
            f.write("\t\t%s[%d] = 0u;\n" % (dst_array, w))
            continue
        f.write("\t\t%s[%d] = 0u" % (dst_array, w))
        for i, n in enumerate(chunk):
            f.write(" | ((uint32_t)(%s & 1) << %d)" % (n, i))
        f.write(";\n")


def emit_in_strobes(f, dst_array, names, byte_count):
    """Each strobe variable becomes one whole byte (0xFF or 0x00)."""
    for i in range(byte_count):
        if i < len(names):
            f.write("\t\t%s[%d] = (%s & 1) ? 0xFFu : 0x00u;\n" %
                    (dst_array, i, names[i]))
        else:
            f.write("\t\t%s[%d] = 0x00u;\n" % (dst_array, i))


def emit_out_bits(f, src_array, names):
    for i, n in enumerate(names):
        word = i // 32
        bit = i % 32
        f.write("\t\t%s = ((%s[%d] >> %d) & 1) ? PRESI_1 : PRESI_0;\n" %
                (n, src_array, word, bit))


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
        raise SystemExit("%s (%s): missing we_i/re_i pin" % (instance, inst_xname))

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
    f.write("\t\tunsigned _we = %s & 1;\n" % we)
    f.write("\t\tunsigned _re = %s & 1;\n" % re_)
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


def emit(out_path, instances, srams):
    sram_blocks = []
    other = []
    for inst, module, pins in instances:
        if module not in SRAM_MODULES:
            other.append((inst, module))
            continue
        ports = group_pins(pins)
        we_pins = [(s, c) for s, c in pins if classify_port(split_port(s)[0]) == "we_i"]
        if not we_pins:
            raise SystemExit("%s: no we_i pin found" % inst)
        we_base = split_port(we_pins[0][0])[0]
        idx, sram = lookup_sram_index(srams, we_base)
        sram_blocks.append((inst, idx, sram, ports))

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
            f.write("\n\t/* Unwired blackboxes (engine, ROM, sequencer ROM):\n")
            for inst, module in other:
                f.write("\t *   %-12s  %s\n" % (inst, module))
            f.write("\t */\n")
    return len(sram_blocks), len(other)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bb", required=True)
    ap.add_argument("--sram-json", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    instances = parse_bb(args.bb)
    srams = parse_sram_json(args.sram_json)
    sram_count, other_count = emit(args.out, instances, srams)
    print("blackbox-wiring: %d SRAMs wired, %d engine/ROM blackboxes still TODO"
          % (sram_count, other_count))


if __name__ == "__main__":
    main()
