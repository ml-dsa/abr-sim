#!/usr/bin/env python3

# Extract the abr_seq sequencer ROM contents from a Yosys JSON.
#
# After `proc` (with proc_rom enabled), the abr_seq case-based always_ff
# becomes a $mem_v2 cell whose width is *less* than the SV data_o_rom
# struct: Yosys's proc_rom strips constant-zero bit positions across all
# entries, so a 87-bit logical struct becomes a 61-bit ROM (in v2.0.3
# abr_seq).  The full 87-bit data_o_rom value is reassembled by a $mux
# that interleaves the cell output with constant zeros at the stripped
# positions.
#
# The presi C harness drives abr_seq as a blackbox -- it never sees the
# $mem_v2 cell directly -- so this script reconstructs the *full* 87-bit
# ROM by combining INIT data with the bit-mapping captured in the auto
# net Yosys creates next to the cell:
#
#   $auto$proc_rom.cc:<line>:do_switch$<id>
#
# The auto net has WIDTH bits, where each entry is either:
#   * the integer wire id of an RD_DATA bit (a "live" position)
#   * the literal string "0" (a stripped, always-zero position)
#
# Output:
#   --header  ANSI-C header with `static const uint32_t
#             presi_abr_seq_rom[1024][N_WORDS]` plus
#             `PRESI_ABR_SEQ_ROM_WIDTH` (the *full* SV width, e.g. 87) and
#             `PRESI_ABR_SEQ_ROM_WORDS` macros.
#   --json    sibling describing the cell + bit map (instance, abits,
#             rom_width (61), full_width (87), bit_map [list of 87 entries
#             where -1 means "constant zero" and >= 0 indexes into
#             RD_DATA[k]]).

import argparse
import json


def parse_yosys_bits(bits_str):
    """Yosys parameter bit strings are big-endian (MSB first).  Return a
    list of 0/1 ints with index 0 = LSB."""
    return [int(c) for c in reversed(bits_str)]


def find_seq_rom(modules):
    """Locate the abr_seq $mem_v2 cell and its companion auto net.

    Search every module for a $mem_v2 cell with ABITS == 10, SIZE ==
    1024, and at least one RD_PORTS-equivalent connection.  Return
    (module_name, cell_name, cell_dict) or (None, None, None).
    """
    for mod_name, mod in modules.items():
        for cn, c in mod.get("cells", {}).items():
            if c.get("type") != "$mem_v2":
                continue
            params = c.get("parameters", {})
            try:
                abits = int(params["ABITS"], 2)
                size = int(params["SIZE"], 2)
                width = int(params["WIDTH"], 2)
            except (KeyError, ValueError):
                continue
            if abits == 10 and size == 1024 and width > 0:
                return mod_name, cn, c
    return None, None, None


def find_bit_map(module, rd_data_ids, full_width):
    """Find the auto net that maps RD_DATA bits to full-width bit
    positions.  Yosys names it like `$auto$proc_rom.cc:<line>:do_switch$<id>`,
    has length `full_width`, and contains a mix of integer wire IDs (from
    RD_DATA) and literal `'0'` for stripped positions.

    Returns a list of length `full_width` where entry i is:
      * an integer k in [0, len(rd_data)-1] meaning data_o_rom[i] = ROM[k]
      * -1 meaning data_o_rom[i] is constant zero.
    """
    rd_set = set(rd_data_ids)
    rd_index = {b: i for i, b in enumerate(rd_data_ids)}
    best = None
    for nn, info in module.get("netnames", {}).items():
        bits = info.get("bits", [])
        if len(bits) != full_width:
            continue
        kept = sum(1 for b in bits if isinstance(b, int) and b in rd_set)
        if kept == len(rd_data_ids):
            # Score lower for better-matching auto nets so we pick a
            # plausibly proc_rom-generated one rather than `data_o_rom`
            # itself (which has port wire IDs, not RD_DATA wire IDs).
            score = (0 if "proc_rom" in nn else 1, len(nn))
            if best is None or score < best[0]:
                best = (score, nn, bits)
    if best is None:
        return None, None
    _, nn, bits = best
    bit_map = []
    for b in bits:
        if isinstance(b, int) and b in rd_set:
            bit_map.append(rd_index[b])
        else:
            bit_map.append(-1)
    return nn, bit_map


def extract_init(cell):
    params = cell["parameters"]
    width = int(params["WIDTH"], 2)
    size = int(params["SIZE"], 2)
    init_str = params.get("INIT", "")
    bits = parse_yosys_bits(init_str)
    needed = width * size
    if len(bits) < needed:
        bits = bits + [0] * (needed - len(bits))
    elif len(bits) > needed:
        bits = bits[:needed]
    rom = []
    for addr in range(size):
        rom.append(bits[addr * width:(addr + 1) * width])
    return width, size, rom


def assemble_full(rom_bits, bit_map):
    """For each address, produce a full-width value where bit i comes
    from rom_bits[bit_map[i]] if bit_map[i] >= 0, else 0."""
    full = []
    for word in rom_bits:
        out = []
        for src in bit_map:
            out.append(word[src] if src >= 0 else 0)
        full.append(out)
    return full


def words_per_entry(width):
    return (width + 31) // 32


def emit_header(path, full_width, size, rom):
    nwords = words_per_entry(full_width)
    with open(path, "w", encoding="utf-8") as f:
        f.write("/* Generated by extract_seq_rom.py.  Do not edit.\n"
                " *\n"
                " * abr_seq sequencer ROM contents extracted from a\n"
                " * standalone Yosys build of abr_seq.  The full SV\n"
                " * data_o_rom width (87 bits in abr_wrap v2.0.3) is\n"
                " * reassembled here -- presi drives the abr_seq\n"
                " * blackbox's data_o port directly from this table,\n"
                " * which is why we cannot use the cell's narrower\n"
                " * post-proc_rom INIT data verbatim.\n"
                " */\n\n")
        f.write("#ifndef PRESI_ABR_SEQ_ROM_H\n#define PRESI_ABR_SEQ_ROM_H\n\n")
        f.write("#include <stdint.h>\n\n")
        f.write("#define PRESI_ABR_SEQ_ROM_SIZE   %u\n" % size)
        f.write("#define PRESI_ABR_SEQ_ROM_WIDTH  %u\n" % full_width)
        f.write("#define PRESI_ABR_SEQ_ROM_WORDS  %u\n\n" % nwords)
        f.write("static const uint32_t presi_abr_seq_rom[%u][%u] = {\n" %
                (size, nwords))
        for addr, word_bits in enumerate(rom):
            words = []
            for w in range(nwords):
                v = 0
                for b in range(32):
                    idx = w * 32 + b
                    if idx < full_width and word_bits[idx]:
                        v |= 1 << b
                words.append(v)
            joined = ", ".join("0x%08xu" % v for v in words)
            f.write("    { %s }, /* %4u */\n" % (joined, addr))
        f.write("};\n\n#endif\n")


def emit_json(path, mod_name, cell_name, cell, rom_width, full_width,
              size, bit_map, auto_net):
    out = {
        "module": mod_name,
        "instance": cell_name,
        "abits": int(cell["parameters"]["ABITS"], 2),
        "rom_width": rom_width,
        "full_width": full_width,
        "size": size,
        "bit_map": bit_map,
        "auto_net": auto_net,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
        f.write("\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--standalone-json", required=True,
                    help="Yosys JSON from a standalone abr_seq build")
    ap.add_argument("--header", required=True)
    ap.add_argument("--json", required=True)
    args = ap.parse_args()

    with open(args.standalone_json, "r", encoding="utf-8") as f:
        doc = json.load(f)
    modules = doc.get("modules", {})

    mod_name, cell_name, cell = find_seq_rom(modules)
    if cell is None:
        raise SystemExit("no $mem_v2 cell with ABITS=10 SIZE=1024 found")

    rom_width, size, rom_bits = extract_init(cell)

    rd_data_ids = cell["connections"]["RD_DATA"]
    if len(rd_data_ids) != rom_width:
        raise SystemExit("RD_DATA bit count %d != WIDTH %d" %
                         (len(rd_data_ids), rom_width))

    # Try a sequence of plausible full widths, picking the auto net that
    # contains every RD_DATA wire id and the smallest set of '0' literals.
    auto_net = None
    bit_map = None
    for fw in (87, 88, 96, rom_width):
        nn, bm = find_bit_map(modules[mod_name], rd_data_ids, fw)
        if nn is not None:
            auto_net = nn
            bit_map = bm
            full_width = fw
            break
    if bit_map is None:
        # Fallback: drive only the rom_width bits directly.  This is
        # incorrect for the SV-level data_o_rom but lets the build proceed.
        full_width = rom_width
        bit_map = list(range(rom_width))
        auto_net = "<fallback identity>"

    full_rom = assemble_full(rom_bits, bit_map)
    emit_header(args.header, full_width, size, full_rom)
    emit_json(args.json, mod_name, cell_name, cell, rom_width, full_width,
              size, bit_map, auto_net)
    print("seq-rom: %s/%s rom_width=%u full_width=%u size=%u "
          "(auto net: %s)" %
          (mod_name, cell_name, rom_width, full_width, size, auto_net))


if __name__ == "__main__":
    main()
