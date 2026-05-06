#!/usr/bin/env python3

import argparse
import collections
import json


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    with open(args.json, "r", encoding="utf-8") as f:
        design = json.load(f)

    modules = design.get("modules", {})
    cell_counts = collections.Counter()
    top_name = None

    for name, module in modules.items():
        attrs = module.get("attributes", {})
        if attrs.get("top") in (1, "1", "00000000000000000000000000000001"):
            top_name = name
        for cell in module.get("cells", {}).values():
            cell_counts[cell.get("type", "<unknown>")] += 1

    if top_name is None and "abr_wrap" in modules:
        top_name = "abr_wrap"

    with open(args.out, "w", encoding="utf-8") as f:
        f.write("modules: %d\n" % len(modules))
        f.write("top: %s\n\n" % (top_name or "<unknown>"))
        if top_name in modules:
            f.write("top ports:\n")
            ports = modules[top_name].get("ports", {})
            for pname in sorted(ports):
                p = ports[pname]
                f.write("  %-24s %-6s %d\n" %
                        (pname, p.get("direction", "?"),
                         len(p.get("bits", []))))
            f.write("\n")

        f.write("cell types:\n")
        for ctype, count in cell_counts.most_common():
            f.write("  %-64s %d\n" % (ctype, count))

    print("inventory: %s, %d cell types" % (args.out, len(cell_counts)))


if __name__ == "__main__":
    main()
