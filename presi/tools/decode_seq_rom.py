#!/usr/bin/env python3

# Decode the abr_seq sequencer ROM and validate it against the SV source.
#
# Inputs:
#   - presi/_build/abr_seq.standalone.json   (Yosys output, has $mem_v2 INIT)
#   - presi/_build/abr_seq.standalone.v      (sv2v output, has the case bodies
#                                              with all localparams resolved)
#
# Reads the post-proc_rom INIT (61-bit per entry) plus the bit_map (which
# 26 of the 87 SV-level bits are constant zero), reassembles the full
# 87-bit data_o_rom value per address, then decodes each into:
#
#     opcode[86:76]  imm[75:60]  length[59:45]  operand1[44:30]
#     operand2[29:15]  operand3[14:0]
#
# A small set of named constants (parsed from abr_seq.standalone.v's
# `localparam` block) lets us pretty-print opcode and operand IDs.
#
# Validation table at the bottom checks a handful of well-known entries
# (MLDSA_KG_S+0..N) against expected operand IDs / lengths from the
# upstream RTL; mismatches print as `*** FAIL ***`.

import argparse
import json
import os
import re
import sys


HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_JSON = os.path.join(HERE, "..", "_build", "abr_seq.standalone.json")
DEFAULT_SV = os.path.join(HERE, "..", "_build", "abr_seq.standalone.v")


# ----------------------------------------------------------------------
# Bit layout of abr_seq_instr_t (87 bits, MSB first in SV packed order):
#   [86:76] opcode = {keccak_en, sampler_en, ntt_en, aux_en, mode[4:0],
#                     masking_en, shuffling_en}
#   [75:60] imm
#   [59:45] length
#   [44:30] operand1
#   [29:15] operand2
#   [14: 0] operand3
# ----------------------------------------------------------------------

OPR_W = 15
IMM_W = 16
OPC_W = 11
FULL_W = OPC_W + IMM_W + 4 * OPR_W


def field(value, hi, lo):
    width = hi - lo + 1
    return (value >> lo) & ((1 << width) - 1)


def split_instr(value):
    return {
        "opcode":   field(value, 86, 76),
        "imm":      field(value, 75, 60),
        "length":   field(value, 59, 45),
        "operand1": field(value, 44, 30),
        "operand2": field(value, 29, 15),
        "operand3": field(value, 14,  0),
    }


def split_opcode(opcode):
    """opcode bit layout, MSB to LSB:
       [10] keccak_en  [9] sampler_en  [8] ntt_en  [7] aux_en
       [6:2] mode      [1] masking_en  [0] shuffling_en"""
    return {
        "keccak_en":    (opcode >> 10) & 1,
        "sampler_en":   (opcode >>  9) & 1,
        "ntt_en":       (opcode >>  8) & 1,
        "aux_en":       (opcode >>  7) & 1,
        "mode":         (opcode >>  2) & 0x1f,
        "masking_en":   (opcode >>  1) & 1,
        "shuffling_en": (opcode >>  0) & 1,
    }


# ----------------------------------------------------------------------
# Lookup tables.  Names come from the local SV sources; we parse the
# standalone abr_seq.v for `localparam ... = <value>` to pick up the
# numeric constants Yosys ended up using, so the decoder stays in sync
# with whatever sv2v / abr_ctrl_pkg gave us.
# ----------------------------------------------------------------------


LOCALPARAM_RE = re.compile(
    r"^\s*localparam\s+(?:\[[^\]]+\]\s+)?"
    r"(?P<name>[A-Za-z_][A-Za-z_0-9]*)\s*="
    r"\s*(?P<expr>.+?);\s*$"
)


def parse_localparams(path):
    """Return {name: value} for every `localparam X = <numeric or name>;`
    line in `path`.  Resolves trivial `name + N` forms by lookup, plus
    sized-literals like `11'h604`.  Anything more complex stays unset."""
    raw = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = LOCALPARAM_RE.match(line)
            if m:
                raw[m.group("name")] = m.group("expr").strip()
    out = {}

    def resolve(name, depth=0):
        if name in out:
            return out[name]
        if depth > 32 or name not in raw:
            return None
        e = raw[name]
        # Sized or bare Verilog literal: [width]'<base><value>
        m = re.match(r"^(?:\d+)?'([hHdD])([0-9a-fA-F]+)$", e)
        if m:
            base = 16 if m.group(1) in "hH" else 10
            v = int(m.group(2), base)
            out[name] = v
            return v
        # Bare integer
        try:
            v = int(e)
            out[name] = v
            return v
        except ValueError:
            pass
        # Lookup of another name plus an integer offset (e.g. ABR_RESET + 1)
        m = re.match(r"^([A-Za-z_][A-Za-z_0-9]*)\s*([+-])\s*(\d+)$", e)
        if m:
            base = resolve(m.group(1), depth + 1)
            if base is not None:
                v = base + int(m.group(3)) * (1 if m.group(2) == "+" else -1)
                out[name] = v
                return v
        # Bare name
        m = re.match(r"^([A-Za-z_][A-Za-z_0-9]*)$", e)
        if m:
            v = resolve(m.group(1), depth + 1)
            if v is not None:
                out[name] = v
                return v
        return None

    for name in raw:
        resolve(name)
    return out


# Sampler/NTT/Aux mode names (5-bit field) in declared enum order.
SAMPLER_MODES = [
    "ABR_SAMPLER_NONE", "ABR_SHAKE256", "ABR_SHAKE128", "ABR_SHA512",
    "ABR_SHA256", "MLDSA_REJ_SAMPLER", "MLKEM_REJ_SAMPLER", "ABR_EXP_MASK",
    "ABR_REJ_BOUNDED", "ABR_SAMPLE_IN_BALL", "ABR_CBD_SAMPLER",
]
NTT_MODES = [
    "ABR_NTT_NONE", "MLDSA_NTT", "MLDSA_INTT", "MLDSA_PWM",
    "MLDSA_PWM_ACCUM", "MLDSA_PWM_SMPL", "MLDSA_PWM_ACCUM_SMPL",
    "MLDSA_PWA", "MLDSA_PWS", "MLDSA_PWM_INTT", "MLKEM_NTT", "MLKEM_INTT",
    "MLKEM_PWM", "MLKEM_PWM_ACCUM", "MLKEM_PWM_SMPL", "MLKEM_PWM_ACCUM_SMPL",
    "MLKEM_PWA", "MLKEM_PWS", "MLKEM_PWM_INTT",
]
AUX_MODES = [
    "ABR_AUX_NONE", "MLDSA_SKDECODE", "MLDSA_SKENCODE", "MLDSA_PKDECODE",
    "MLDSA_MAKEHINT", "MLDSA_USEHINT", "MLDSA_NORMCHK", "MLDSA_PWR2RND",
    "MLDSA_SIGENC", "MLDSA_SIGDEC_H", "MLDSA_SIGDEC_Z", "MLDSA_HINTSUM",
    "MLDSA_DECOMP", "MLDSA_LFSR", "MLKEM_COMPRESS", "MLKEM_DECOMPRESS",
]


def mode_name(opcode_parts):
    if opcode_parts["aux_en"]:
        table = AUX_MODES
    elif opcode_parts["ntt_en"]:
        table = NTT_MODES
    else:
        table = SAMPLER_MODES
    m = opcode_parts["mode"]
    return table[m] if 0 <= m < len(table) else "mode=%d" % m


def opcode_name_lookup(localparams):
    """Return {opcode_value: name} for every `ABR_UOP_*` localparam."""
    out = {}
    for name, val in localparams.items():
        if "_ABR_UOP_" in name and val is not None:
            short = name.split("_ABR_UOP_")[1]
            out.setdefault(val, "ABR_UOP_" + short)
    return out


def operand_name_lookup(localparams):
    """Return {operand_value: name} for every `ABR_*_ID`, `MLDSA_*_ID`,
    `MLKEM_*_ID` localparam.  Skip ones that overlap on the same value
    (we want the canonical name, e.g. ABR_NOP=0 and MLKEM_NOP=0)."""
    preferred_prefixes = ("ABR_", "MLDSA_", "MLKEM_")
    out = {}
    for name, val in localparams.items():
        if val is None:
            continue
        # Strip the abr_ctrl_pkg_ etc. prefix that sv2v adds.
        short = name.split("_pkg_", 1)[-1]
        if not (short.endswith("_ID") or short in ("ABR_NOP", "MLKEM_NOP")
                or "_BASE" in short or "_OFFSET" in short):
            continue
        if val in out:
            # Prefer ABR_ over MLDSA_ over MLKEM_ for shared values.
            for p in preferred_prefixes:
                if out[val].startswith(p):
                    break
                if short.startswith(p):
                    out[val] = short
                    break
        else:
            out[val] = short
    return out


# ----------------------------------------------------------------------
# Address label table: walks the case statement in abr_seq.standalone.v
# and records every `<name>: data_o_rom <= ...` label.  sv2v has
# resolved every prefix, so the names are unambiguous.
# ----------------------------------------------------------------------

LABEL_RE = re.compile(
    r"^\s*(?P<label>[A-Za-z_][A-Za-z_0-9]*"
    r"(?:\s*\+\s*\d+)?)\s*:\s*data_o_rom\s*<=\s*\{(?P<body>.+)\};\s*$"
)


def parse_address_labels(sv_path, localparams):
    """Return list of (addr, label, body_text)."""
    out = []
    with open(sv_path, encoding="utf-8") as f:
        for line in f:
            m = LABEL_RE.match(line)
            if not m:
                continue
            label = m.group("label")
            offset = 0
            base = label
            mm = re.match(r"^([A-Za-z_][A-Za-z_0-9]*)\s*\+\s*(\d+)$", label)
            if mm:
                base = mm.group(1)
                offset = int(mm.group(2))
            v = localparams.get(base)
            if v is None:
                continue
            out.append((v + offset, label, m.group("body")))
    return out


# ----------------------------------------------------------------------
# ROM reassembly: read INIT + bit_map, produce 1024 87-bit values.
# ----------------------------------------------------------------------


def reassemble_rom(standalone_json):
    with open(standalone_json, encoding="utf-8") as f:
        doc = json.load(f)
    for mod in doc.get("modules", {}).values():
        for cn, c in mod.get("cells", {}).items():
            if c.get("type") != "$mem_v2":
                continue
            params = c["parameters"]
            abits = int(params["ABITS"], 2)
            size = int(params["SIZE"], 2)
            width = int(params["WIDTH"], 2)
            if abits != 10 or size != 1024:
                continue
            init_str = params.get("INIT", "")
            init_bits = [int(c) for c in reversed(init_str)]
            init_bits = init_bits + [0] * (size * width - len(init_bits))
            rd_data = c["connections"]["RD_DATA"]
            rd_set = set(rd_data)
            rd_idx = {b: i for i, b in enumerate(rd_data)}

            # Find bit map net.
            for nn, info in mod.get("netnames", {}).items():
                bits = info.get("bits", [])
                if (len(bits) == FULL_W
                        and sum(1 for b in bits if isinstance(b, int)
                                and b in rd_set) == width
                        and "proc_rom" in nn):
                    bit_map = []
                    for b in bits:
                        if isinstance(b, int) and b in rd_set:
                            bit_map.append(rd_idx[b])
                        else:
                            bit_map.append(-1)
                    rom = []
                    for a in range(size):
                        word = init_bits[a * width:(a + 1) * width]
                        v = 0
                        for i, src in enumerate(bit_map):
                            if src >= 0 and word[src]:
                                v |= 1 << i
                        rom.append(v)
                    return rom, width, FULL_W
    raise SystemExit("could not locate $mem_v2 cell + bit map in %s" %
                     standalone_json)


# ----------------------------------------------------------------------


def fmt_operand(val, names):
    if val == 0:
        return "NOP"
    if val in names:
        # Strip a couple of long prefixes for readability.
        return names[val]
    return "0x%x" % val


def render_entry(addr, label, value, opname, opname_table, oprname_table):
    parts = split_instr(value)
    op = parts["opcode"]
    op_parts = split_opcode(op)
    mode_str = mode_name(op_parts)
    flags = []
    for f in ("keccak_en", "sampler_en", "ntt_en", "aux_en",
              "masking_en", "shuffling_en"):
        if op_parts[f]:
            flags.append(f.replace("_en", ""))
    flag_str = ",".join(flags) if flags else "-"
    op_text = opname_table.get(op, "0x%03x" % op)
    return ("addr=%4d  %-30s  %-22s mode=%-22s flags=%-32s "
            "imm=0x%04x  len=%-5d  op1=%-25s  op2=%-25s  op3=%-25s" % (
        addr, label[:30], op_text, mode_str, flag_str,
        parts["imm"], parts["length"],
        fmt_operand(parts["operand1"], oprname_table),
        fmt_operand(parts["operand2"], oprname_table),
        fmt_operand(parts["operand3"], oprname_table)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--standalone-json", default=DEFAULT_JSON)
    ap.add_argument("--sv-source", default=DEFAULT_SV,
                    help="standalone abr_seq.v from sv2v slice")
    ap.add_argument("--addrs", default=None,
                    help="comma-separated address list to print "
                         "(default: every non-NOP entry)")
    ap.add_argument("--from", dest="from_label", default=None,
                    help="symbolic FSM label to start the dump at "
                         "(e.g. MLDSA_KG_S).  Strips an `abr_ctrl_pkg_` "
                         "prefix automatically.")
    ap.add_argument("--count", type=int, default=20,
                    help="when --from is given, print this many "
                         "consecutive entries (default 20)")
    ap.add_argument("--show-nop", action="store_true",
                    help="include NOP entries in the listing")
    args = ap.parse_args()

    rom, rom_width, full_width = reassemble_rom(args.standalone_json)
    print("# ROM: %d entries, rom_width=%d full_width=%d" %
          (len(rom), rom_width, full_width))

    localparams = parse_localparams(args.sv_source)
    opname_table = opcode_name_lookup(localparams)
    oprname_table = operand_name_lookup(localparams)
    label_table = {}
    for addr, label, _ in parse_address_labels(args.sv_source, localparams):
        # Strip the sv2v `abr_ctrl_pkg_` prefix.
        short = label
        m = re.match(r"^([A-Za-z_][A-Za-z_0-9]*?)_pkg_(.+)$", label)
        if m:
            short = m.group(2)
        if addr not in label_table:
            label_table[addr] = short

    if args.from_label is not None:
        target = args.from_label
        candidates = [target,
                      "abr_ctrl_pkg_" + target,
                      target.replace("abr_ctrl_pkg_", "")]
        start = None
        for c in candidates:
            if c in localparams and localparams[c] is not None:
                start = localparams[c]
                break
        if start is None:
            raise SystemExit("unknown FSM label: %s" % target)
        wanted = list(range(start, min(start + args.count, len(rom))))
        # Force --show-nop so the FSM trace stays continuous.
        args.show_nop = True
    elif args.addrs is not None:
        wanted = [int(x.strip(), 0) for x in args.addrs.split(",")]
    else:
        wanted = range(len(rom))

    n_shown = 0
    for addr in wanted:
        v = rom[addr]
        if v == 0 and not args.show_nop:
            continue
        label = label_table.get(addr, "(unlabelled)")
        print(render_entry(addr, label, v, None, opname_table, oprname_table))
        n_shown += 1
    print("# %d entries shown (of %d total)" % (n_shown, len(rom)))

    # Spot-check: known MLDSA-KG entries from rtl/abr_seq.sv lines 58..60.
    failures = 0

    def expect(addr, exp_op, exp_imm, exp_len, exp_op1, exp_op2, exp_op3,
               note):
        nonlocal failures
        v = rom[addr]
        parts = split_instr(v)
        op_text = opname_table.get(parts["opcode"], "0x%03x" % parts["opcode"])
        ok = (parts["opcode"] in opname_table
              and op_text == exp_op
              and parts["imm"] == exp_imm
              and parts["length"] == exp_len
              and fmt_operand(parts["operand1"], oprname_table) == exp_op1
              and fmt_operand(parts["operand2"], oprname_table) == exp_op2
              and fmt_operand(parts["operand3"], oprname_table) == exp_op3)
        if not ok:
            failures += 1
            print("[FAIL] addr=%d (%s)" % (addr, note))
            print("       got: op=%s imm=%x len=%d op1=%s op2=%s op3=%s" % (
                op_text, parts["imm"], parts["length"],
                fmt_operand(parts["operand1"], oprname_table),
                fmt_operand(parts["operand2"], oprname_table),
                fmt_operand(parts["operand3"], oprname_table)))
            print("       want: op=%s imm=%x len=%d op1=%s op2=%s op3=%s" % (
                exp_op, exp_imm, exp_len, exp_op1, exp_op2, exp_op3))
        else:
            print("[ OK ] addr=%d (%s)" % (addr, note))

    print()
    print("# Spot checks (rtl/abr_seq.sv lines 58, 59, 60):")
    kg = localparams.get("abr_ctrl_pkg_MLDSA_KG_S")
    if kg is None:
        print("[WARN] could not resolve MLDSA_KG_S")
    else:
        expect(kg + 0, "ABR_UOP_LD_SHAKE256", 0, 64,
               "ABR_ENTROPY_ID", "NOP", "NOP",
               "MLDSA_KG_S+0: rnd_seed = SHAKE256 init")
        expect(kg + 1, "ABR_UOP_SHAKE256", 0, 8,
               "ABR_CNT_ID", "NOP", "ABR_DEST_LFSR_SEED_REG_ID",
               "MLDSA_KG_S+1: SHAKE256 squeeze 8 bytes")
        expect(kg + 2, "ABR_UOP_LFSR", 0, 0, "NOP", "NOP", "NOP",
               "MLDSA_KG_S+2: LFSR")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
