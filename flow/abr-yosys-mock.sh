#!/usr/bin/env bash

set -euo pipefail

mode="${1:-coarse}"
top="${ABR_SYNTH_TOP:-abr_wrap}"
out_dir="${ABR_SYNTH_OUT:-_build/yosys-mock/${top}-${mode}}"
vf="${ABR_SYNTH_VF:-_build/xabr_wrap.vf}"
timeout_s="${ABR_SYNTH_TIMEOUT:-}"

case "$mode" in
  coarse|gates)
    ;;
  *)
    echo "usage: $0 [coarse|gates]" >&2
    exit 2
    ;;
esac

if [[ ! -f "$vf" ]]; then
  make "$vf"
fi

mkdir -p "$out_dir"

sv2v_out="$out_dir/${top}.sv2v.v"
yosys_script="$out_dir/${top}.${mode}.ys"
netlist_out="$out_dir/${top}.${mode}.v"
stat_out="$out_dir/${top}.${mode}.stat.rpt"
log_out="$out_dir/${top}.${mode}.yosys.log"

incdirs=()
files=()
while IFS= read -r line; do
  [[ -z "$line" ]] && continue
  case "$line" in
    +incdir+*)
      incdirs+=("-I${line#+incdir+}")
      ;;
    *)
      files+=("$line")
      ;;
  esac
done < "$vf"

echo "sv2v: ${#incdirs[@]} include dirs, ${#files[@]} files -> $sv2v_out"
sv2v -D SYNTHESIS -D YOSYS --top="$top" "${incdirs[@]}" "${files[@]}" -w "$sv2v_out"

{
  echo "read_verilog -sv $sv2v_out"
  echo "hierarchy -check -top $top"
  echo "proc"
  echo "opt_clean"
  echo "memory -nomap"
  echo "opt_clean"
  if [[ "$mode" == "gates" ]]; then
    echo "techmap"
    echo "opt_clean"
    echo "simplemap"
    echo "opt_clean"
    echo "write_verilog -noattr -noexpr -nohex -nodec $netlist_out"
  else
    echo "write_verilog -noattr $netlist_out"
  fi
  echo "tee -o $stat_out stat"
} > "$yosys_script"

echo "yosys: $mode -> $netlist_out"
if [[ -n "$timeout_s" ]]; then
  timeout "$timeout_s" yosys -q "$yosys_script" 2>&1 | tee "$log_out"
else
  yosys -q "$yosys_script" 2>&1 | tee "$log_out"
fi

echo "wrote $netlist_out"
echo "wrote $stat_out"
