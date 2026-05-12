//  ntt_top_tb.cpp -- minimal Verilator driver for the standalone
//  ntt_top testbench.  Wiggles clk + reset and lets the SV side drive
//  the rest; the $display in ntt_top_tb.sv produces the [ntt_tb]
//  trace that pairs with the presi gates-C standalone harness.

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <verilated.h>
#include "Vntt_top_tb.h"

int main(int argc, char **argv)
{
    int64_t max_cyc = 256;
    for (int i = 1; i < argc;) {
        if (i + 1 < argc && strcmp(argv[i], "-t") == 0) {
            max_cyc = strtoll(argv[i + 1], NULL, 0);
            i += 2;
        } else {
            fprintf(stderr, "usage: %s [-t <cycles>]\n", argv[0]);
            return 1;
        }
    }

    Verilated::commandArgs(argc, argv);
    Vntt_top_tb *top = new Vntt_top_tb;

    //  Reset for 5 cycles, then run free.
    top->clk     = 0;
    top->reset_n = 0;
    for (int i = 0; i < 10; i++) {
        top->clk = 0; top->eval();
        top->clk = 1; top->eval();
    }
    top->reset_n = 1;
    for (int64_t i = 0; i < max_cyc; i++) {
        top->clk = 0; top->eval();
        top->clk = 1; top->eval();
    }

    delete top;
    return 0;
}
