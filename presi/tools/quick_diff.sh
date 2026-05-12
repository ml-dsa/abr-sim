#!/bin/bash
# Quick comparison: Verilator vs presi engine traces around pc 460-470.
set -e

V_LOG=${1:-/tmp/verilator_eng.log}
P_LOG=${2:-/tmp/presi_eng.log}

echo "## [seq] cycle table (pc 460-470)"
echo
echo "pc    V_cyc   V_dur   P_cyc   P_dur   Δdur(P-V)"
prev_v=0; prev_p=0
for pc in 460 461 462 463 464 465 466 467 468 469 470; do
  v_cyc=$(awk -v pc="$pc" '/\[seq\]/ && $0 ~ "[ \t]"pc":" {print $2; exit}' "$V_LOG")
  p_cyc=$(awk -v pc="$pc" '/\[seq\]/ && match($0, "pc=" pc "([^0-9]|$)") {sub(/^[^=]*=/, "", $2); split($2, a, " "); print a[1]; exit}' "$P_LOG")
  p_cyc=$(grep -E "\[seq\]\s+cyc=[0-9]+\s+pc=$pc(\$|[^0-9])" "$P_LOG" | head -1 | sed -E 's/^.*cyc=([0-9]+).*/\1/')
  if [ -n "$v_cyc" ] && [ -n "$p_cyc" ]; then
    v_dur=$([ $prev_v -gt 0 ] && echo $((v_cyc - prev_v)) || echo "-")
    p_dur=$([ $prev_p -gt 0 ] && echo $((p_cyc - prev_p)) || echo "-")
    if [ "$v_dur" != "-" ] && [ "$p_dur" != "-" ]; then
      delta=$((p_dur - v_dur))
    else
      delta="-"
    fi
    printf "%-5s %-7s %-7s %-7s %-7s %s\n" "$pc" "$v_cyc" "$v_dur" "$p_cyc" "$p_dur" "$delta"
    prev_v=$v_cyc; prev_p=$p_cyc
  else
    printf "%-5s %-7s -       %-7s -       -\n" "$pc" "${v_cyc:--}" "${p_cyc:--}"
  fi
done

echo
echo "## Engine signal snapshots around pc=462 transition"
echo "### Verilator (pc=462 at cyc=3207, pc=463 at cyc=3519)"
awk 'BEGIN{prev=""} /\[eng\]/ {curr=$0; sub(/^.*\[eng\]\s+/, "", curr); cyc=$2; if (curr != prev && cyc>=3200 && cyc<=3525) print; prev=curr}' "$V_LOG"
echo
echo "### presi pc=462 area (look up presi pc=462 cycle from above table)"
P462_CYC=$(grep -E "\[seq\]\s+cyc=[0-9]+\s+pc=462(\$|[^0-9])" "$P_LOG" | head -1 | sed -E 's/^.*cyc=([0-9]+).*/\1/')
P463_CYC=$(grep -E "\[seq\]\s+cyc=[0-9]+\s+pc=463(\$|[^0-9])" "$P_LOG" | head -1 | sed -E 's/^.*cyc=([0-9]+).*/\1/')
if [ -n "$P462_CYC" ]; then
  echo "P pc=462 at cyc=$P462_CYC, pc=463 at cyc=${P463_CYC:-?}"
  LO=$((P462_CYC - 5)); HI=${P463_CYC:-$((P462_CYC + 350))}
  awk -v lo="$LO" -v hi="$HI" 'BEGIN{prev=""} /\[eng\]/ {curr=$0; sub(/^.*\[eng\]\s+/, "", curr); split($2, a, "="); cyc=a[2]; if (curr != prev && cyc>=lo && cyc<=hi+5) print; prev=curr}' "$P_LOG"
else
  echo "(presi has not yet reached pc=462)"
fi

echo
echo "## pc=467 / 468 area"
echo "### Verilator (pc=467 cyc=3994, pc=468 cyc=4068, pc=469 cyc=4186)"
awk 'BEGIN{prev=""} /\[eng\]/ {curr=$0; sub(/^.*\[eng\]\s+/, "", curr); cyc=$2; if (curr != prev && cyc>=3990 && cyc<=4200) print; prev=curr}' "$V_LOG"
echo
echo "### presi pc=467/468 area"
P467_CYC=$(grep -E "\[seq\]\s+cyc=[0-9]+\s+pc=467(\$|[^0-9])" "$P_LOG" | head -1 | sed -E 's/^.*cyc=([0-9]+).*/\1/')
P468_CYC=$(grep -E "\[seq\]\s+cyc=[0-9]+\s+pc=468(\$|[^0-9])" "$P_LOG" | head -1 | sed -E 's/^.*cyc=([0-9]+).*/\1/')
if [ -n "$P467_CYC" ]; then
  echo "P pc=467 at cyc=$P467_CYC, pc=468 at cyc=${P468_CYC:-?}"
  LO=$((P467_CYC - 5)); HI=${P468_CYC:-$((P467_CYC + 300))}
  awk -v lo="$LO" -v hi="$HI" 'BEGIN{prev=""} /\[eng\]/ {curr=$0; sub(/^.*\[eng\]\s+/, "", curr); split($2, a, "="); cyc=a[2]; if (curr != prev && cyc>=lo && cyc<=hi+50) print; prev=curr}' "$P_LOG"
else
  echo "(presi has not yet reached pc=467)"
fi
