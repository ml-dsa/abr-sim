#abr-sign-tvla.dat:	trace.txt
#	cat trace.txt | colrm 16 > abr-sign-tvla.dat

#trace.pdf:	abr-sign-tvla.dat trace.gnuplot
#	gnuplot -c trace.gnuplot

plots:
	bash ./plot.sh
	
clean:
	rm -f *.dat *.pdf

