#!/bin/bash
# snapshot-roundtrip.sh -- quick smoke for the snapshot save/load path.
#
# Run from the project root: presi/tools/snapshot-roundtrip.sh
#
# Steps:
#   1. seed_in.dat from `flow/mldsa-gen.py keygen` (deterministic)
#   2. presi-cosim ... -init-only -save kg-init.bin mldsa-keygen
#      (lands the FSM at MLDSA_KG_S+0 with seed/entropy registers loaded)
#   3. presi-cosim -load kg-init.bin -save kg-1.bin -t 200 run
#      (advance 200 cycles)
#   4. presi-cosim -load kg-1.bin -save kg-1again.bin -t 0 run
#      (round-trip: save -> load -> save with no work in between)
#   5. cmp kg-1.bin kg-1again.bin  (must be byte-identical)
#
# This validates the save/load path without spending cycles on the
# full multi-hour keygen.

set -euo pipefail

cd "$(dirname "$0")/.."   # presi/

BIN=_build/presi-gates-cosim
WORK=_build/snap-test

if [[ ! -x "$BIN" ]]; then
    echo "missing $BIN -- run \`make -C presi cosim\` first" >&2
    exit 1
fi
mkdir -p "$WORK"
# Keep the binary path absolute so subshells in $WORK still see it.
BIN_ABS="$PWD/$BIN"
cd "$WORK"

# Generate deterministic test inputs.  ent_in.dat is optional; we let
# the harness default it to all-zero, so just produce seed_in.dat.
python3 ../../../flow/mldsa-gen.py keygen >/dev/null
cmp seed_in.dat <(printf '') 2>/dev/null && {
    echo "seed_in.dat is empty -- check flow/mldsa-gen.py" >&2
    exit 1
}

echo "==> 1. init"
"$BIN_ABS" -seed seed_in.dat \
                       -save kg-init.bin -init-only mldsa-keygen \
    | tee init.log | tail -3
test -s kg-init.bin
echo "    kg-init.bin: $(stat -c %s kg-init.bin) bytes"

echo "==> 2. step 200"
"$BIN_ABS" -load kg-init.bin -save kg-1.bin -t 200 run \
    | tee step.log | tail -3
test -s kg-1.bin
echo "    kg-1.bin: $(stat -c %s kg-1.bin) bytes"

echo "==> 3. round-trip"
"$BIN_ABS" -load kg-1.bin -save kg-1again.bin -t 0 run \
    | tee rt.log | tail -3

echo "==> 4. byte-compare"
if cmp kg-1.bin kg-1again.bin; then
    echo "PASS: snapshot round-trip byte-identical"
else
    echo "FAIL: snapshots differ" >&2
    exit 1
fi
