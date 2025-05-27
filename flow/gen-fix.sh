#!/bin/bash
if [ "$#" -ne 4 ]; then
    echo "Usage: gen-fix <maxcyc> <readvcd.prm> <id> <n>"
    exit
fi

maxcyc="$1"
vcdprm="$(cat $2)"

#   make _build/Vmldsa_wrap readvcd
for x in `seq $4`; do
    tmpdir="_tr_fix-$3-$x"
    echo "=== $tmpdir ==="
    mkdir -p $tmpdir
    cd $tmpdir
    echo "tmpdir=${tmpdir}" | tee param.txt
    echo "maxcyc=${maxcyc}" | tee -a param.txt
    echo "vcdprm=${vcdprm}" | tee -a param.txt
    dd if=/dev/urandom of=ent_in.dat bs=1 count=64
    randxi=`cat /dev/urandom | tr -dc '0-9A-F' | head -c 64`
    echo "randxi=${randxi}" | tee -a param.txt
    fixkey=00
    echo "fixkey=${fixkey}" | tee -a param.txt
    python3 ../flow/mldsa-gen.py $tmpdir $randxi $fixkey
    mkfifo trace.vcd
    ../readvcd trace.vcd $vcdprm > trace.log &
    ../mldsa_wrap -t $maxcyc -vcd trace.vcd sign | tee run.log
    gzip *.log
    cd ..
done

