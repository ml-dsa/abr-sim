#!/bin/bash

#	2553 -- 39196

#	just the signing time range
cat tvla11k.txt | grep -v read | grep -v tag | sort -n > sign.dat

#	raw trace
zcat trace.log.gz | grep togd | colrm 1 1 | sed 's/\[togd\]//g' > trace.dat
gnuplot -c gnuplot.trace

#	tvla
cat sign.dat | colrm 16 > sign-tvla.dat
gnuplot -c gnuplot.tvla
gnuplot -c gnuplot.tvla2

#	average avtivities of fix and rnd
cat sign.dat | awk '{print $1 "\t" $6 $10}' | tr ',' '\t' > avg-fixrnd.dat
gnuplot -c gnuplot.avg2

#	standard deviation
cat sign.dat | awk '{print $1 "\t" $7 $11}' | tr ')' '\t' > std-fixrnd.dat 
gnuplot -c gnuplot.std2


