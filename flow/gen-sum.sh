#!/bin/bash

for x in _tr_*
do
    echo $x
    zcat $x/*.log.gz | tr '\r' '\n' | grep '#' | sort > $x.dat
done
