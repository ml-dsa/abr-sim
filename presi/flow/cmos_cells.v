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

// Yosys-internal gate primitives.  Declared (* blackbox *) so write_spice
// emits them with their canonical port order instead of falling back to
// "Guessing order of ports" warnings.

(* blackbox *) module \$_NOT_ (A, Y);
input A;
output Y;
endmodule

(* blackbox *) module \$_AND_ (A, B, Y);
input A, B;
output Y;
endmodule

(* blackbox *) module \$_OR_ (A, B, Y);
input A, B;
output Y;
endmodule

(* blackbox *) module \$_NAND_ (A, B, Y);
input A, B;
output Y;
endmodule

(* blackbox *) module \$_NOR_ (A, B, Y);
input A, B;
output Y;
endmodule

(* blackbox *) module \$_XOR_ (A, B, Y);
input A, B;
output Y;
endmodule

(* blackbox *) module \$_XNOR_ (A, B, Y);
input A, B;
output Y;
endmodule

(* blackbox *) module \$_ANDNOT_ (A, B, Y);
input A, B;
output Y;
endmodule

(* blackbox *) module \$_ORNOT_ (A, B, Y);
input A, B;
output Y;
endmodule

(* blackbox *) module \$_MUX_ (A, B, S, Y);
input A, B, S;
output Y;
endmodule

(* blackbox *) module \$_DFF_P_ (C, D, Q);
input C, D;
output Q;
endmodule

(* blackbox *) module \$_DFFSR_PPP_ (C, S, R, D, Q);
input C, S, R, D;
output Q;
endmodule
