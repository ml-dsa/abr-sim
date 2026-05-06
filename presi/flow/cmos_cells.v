module BUF(A, Y);
input A;
output Y;
assign Y = A;
endmodule

module NOT(A, Y);
input A;
output Y;
assign Y = ~A;
endmodule

module NAND(A, B, Y);
input A, B;
output Y;
assign Y = ~(A & B);
endmodule

module NOR(A, B, Y);
input A, B;
output Y;
assign Y = ~(A | B);
endmodule

module DFF(C, D, Q);
input C, D;
output reg Q;
always @(posedge C)
    Q <= D;
endmodule

module DFFSR(C, D, Q, S, R);
input C, D, S, R;
output reg Q;
always @(posedge C, posedge S, posedge R)
    if (S)
        Q <= 1'b1;
    else if (R)
        Q <= 1'b0;
    else
        Q <= D;
endmodule

// Note: Yosys's $_NOT_, $_AND_, $_OR_, $_XOR_, $_MUX_, $_DFF_P_,
// $_DFFSR_PPP_ etc. are produced by simplemap+dfflegalize in the gates
// flow.  Adding `(* blackbox *) module \$_NOT_ ...` declarations here does
// not help: they survive read_verilog as IdStrings prefixed with `\` while
// simplemap's cells use IdStrings prefixed with `$` (auto-generated form),
// so write_spice does not match them.  Instead `spice_to_c.py` matches the
// empirically-verified "Guessing order of ports" output (output first,
// then inputs in reverse insertion order).
