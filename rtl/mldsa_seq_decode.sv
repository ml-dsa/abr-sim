//  mldsa_seq_decode.sv
//	2025-05-27	Markku-Juhani O. Saarinen <mjos@iki.fi>

//	===	hooks to print FSM state ("live address decoder")

/*

=== add to mldsa_seq_prim.sv:
adams-bridge/src/mldsa_top/rtl/mldsa_seq_prim.sv

module mldsa_seq_prim
  import mldsa_ctrl_pkg::*;
  (
  input logic clk,

  input  logic en_i,
  input  logic [MLDSA_PROG_ADDR_W-1 : 0] addr_i,
  output mldsa_seq_instr_t data_o
  );

    //  MJOS: debug address decoder
    mldsa_seq_decode_prim dec_prim(clk, en_i, addr_i);

*/

module mldsa_seq_decode_prim
    import mldsa_ctrl_pkg::*;
    (
        input logic clk,
        input logic en_i,
        input logic [MLDSA_PROG_ADDR_W-1 : 0] addr_i
    );
    logic [25 : 0] cyc = 0;
    logic [MLDSA_PROG_ADDR_W-1 : 0] addr_p = -1;

    always_ff @(posedge clk) begin
        if (en_i) begin
            if (addr_i != addr_p) begin
                if (addr_i == MLDSA_SIGN_SET_Y)         $display("#%d [prim]  %d: MLDSA_SIGN_SET_Y", cyc, addr_i); else
                if (addr_i < MLDSA_ZEROIZE)             $display("#%d [prim]  %d: MLDSA_RESET +%d", cyc, addr_i, addr_i - MLDSA_RESET); else
                if (addr_i < MLDSA_KG_S)                $display("#%d [prim]  %d: MLDSA_ZEROIZE +%d", cyc, addr_i, addr_i - MLDSA_ZEROIZE); else
                if (addr_i < MLDSA_KG_JUMP_SIGN)        $display("#%d [prim]  %d: MLDSA_KG_S +%d", cyc, addr_i, addr_i - MLDSA_KG_S); else
                if (addr_i < MLDSA_KG_E)                $display("#%d [prim]  %d: MLDSA_KG_JUMP_SIGN +%d", cyc, addr_i, addr_i - MLDSA_KG_JUMP_SIGN); else
                if (addr_i < MLDSA_SIGN_S)              $display("#%d [prim]  %d: MLDSA_KG_E +%d", cyc, addr_i, addr_i - MLDSA_KG_E); else
                if (addr_i < MLDSA_SIGN_CHECK_MODE)     $display("#%d [prim]  %d: MLDSA_SIGN_S +%d", cyc, addr_i, addr_i - MLDSA_SIGN_S); else
                if (addr_i < MLDSA_SIGN_H_MU)           $display("#%d [prim]  %d: MLDSA_SIGN_CHECK_MODE +%d", cyc, addr_i, addr_i - MLDSA_SIGN_CHECK_MODE); else
                if (addr_i < MLDSA_SIGN_H_RHO_P)        $display("#%d [prim]  %d: MLDSA_SIGN_H_MU +%d", cyc, addr_i, addr_i - MLDSA_SIGN_H_MU); else
                if (addr_i < MLDSA_SIGN_CHECK_Y_CLR)    $display("#%d [prim]  %d: MLDSA_SIGN_H_RHO_P +%d", cyc, addr_i, addr_i - MLDSA_SIGN_H_RHO_P); else
                if (addr_i < MLDSA_SIGN_LFSR_S)         $display("#%d [prim]  %d: MLDSA_SIGN_CHECK_Y_CLR +%d", cyc, addr_i, addr_i - MLDSA_SIGN_CHECK_Y_CLR); else
                if (addr_i < MLDSA_SIGN_MAKE_Y_S)       $display("#%d [prim]  %d: MLDSA_SIGN_LFSR_S +%d", cyc, addr_i, addr_i - MLDSA_SIGN_LFSR_S); else
                if (addr_i < MLDSA_SIGN_CHECK_W0_CLR)   $display("#%d [prim]  %d: MLDSA_SIGN_MAKE_Y_S +%d", cyc, addr_i, addr_i - MLDSA_SIGN_MAKE_Y_S); else
                if (addr_i < MLDSA_SIGN_MAKE_W_S)       $display("#%d [prim]  %d: MLDSA_SIGN_CHECK_W0_CLR +%d", cyc, addr_i, addr_i - MLDSA_SIGN_CHECK_W0_CLR); else
                if (addr_i < MLDSA_SIGN_MAKE_W)         $display("#%d [prim]  %d: MLDSA_SIGN_MAKE_W_S +%d", cyc, addr_i, addr_i - MLDSA_SIGN_MAKE_W_S); else
                if (addr_i < MLDSA_SIGN_SET_W0)         $display("#%d [prim]  %d: MLDSA_SIGN_MAKE_W +%d", cyc, addr_i, addr_i - MLDSA_SIGN_MAKE_W); else
                if (addr_i < MLDSA_SIGN_CHECK_C_CLR)    $display("#%d [prim]  %d: MLDSA_SIGN_SET_W0 +%d", cyc, addr_i, addr_i - MLDSA_SIGN_SET_W0); else
                if (addr_i < MLDSA_SIGN_MAKE_C)         $display("#%d [prim]  %d: MLDSA_SIGN_CHECK_C_CLR +%d", cyc, addr_i, addr_i - MLDSA_SIGN_CHECK_C_CLR); else
                if (addr_i < MLDSA_SIGN_SET_C)          $display("#%d [prim]  %d: MLDSA_SIGN_MAKE_C +%d", cyc, addr_i, addr_i - MLDSA_SIGN_MAKE_C); else
                if (addr_i < MLDSA_SIGN_CHL_E)          $display("#%d [prim]  %d: MLDSA_SIGN_SET_C +%d", cyc, addr_i, addr_i - MLDSA_SIGN_SET_C); else
                if (addr_i < MLDSA_SIGN_E)              $display("#%d [prim]  %d: MLDSA_SIGN_CHL_E +%d", cyc, addr_i, addr_i - MLDSA_SIGN_CHL_E); else
                if (addr_i < MLDSA_VERIFY_S)            $display("#%d [prim]  %d: MLDSA_SIGN_E +%d", cyc, addr_i, addr_i - MLDSA_SIGN_E); else
                if (addr_i < MLDSA_VERIFY_H_TR)         $display("#%d [prim]  %d: MLDSA_VERIFY_S +%d", cyc, addr_i, addr_i - MLDSA_VERIFY_S); else
                if (addr_i < MLDSA_VERIFY_CHECK_MODE)   $display("#%d [prim]  %d: MLDSA_VERIFY_H_TR +%d", cyc, addr_i, addr_i - MLDSA_VERIFY_H_TR); else
                if (addr_i < MLDSA_VERIFY_H_MU)         $display("#%d [prim]  %d: MLDSA_VERIFY_CHECK_MODE +%d", cyc, addr_i, addr_i - MLDSA_VERIFY_CHECK_MODE); else
                if (addr_i < MLDSA_VERIFY_MAKE_C)       $display("#%d [prim]  %d: MLDSA_VERIFY_H_MU +%d", cyc, addr_i, addr_i - MLDSA_VERIFY_H_MU); else
                if (addr_i < MLDSA_VERIFY_NTT_C)        $display("#%d [prim]  %d: MLDSA_VERIFY_MAKE_C +%d", cyc, addr_i, addr_i - MLDSA_VERIFY_MAKE_C); else
                if (addr_i < MLDSA_VERIFY_NTT_T1)       $display("#%d [prim]  %d: MLDSA_VERIFY_NTT_C +%d", cyc, addr_i, addr_i - MLDSA_VERIFY_NTT_C); else
                if (addr_i < MLDSA_VERIFY_NTT_Z)        $display("#%d [prim]  %d: MLDSA_VERIFY_NTT_T1 +%d", cyc, addr_i, addr_i - MLDSA_VERIFY_NTT_T1); else
                if (addr_i < MLDSA_VERIFY_EXP_A)        $display("#%d [prim]  %d: MLDSA_VERIFY_NTT_Z +%d", cyc, addr_i, addr_i - MLDSA_VERIFY_NTT_Z); else
                if (addr_i < MLDSA_VERIFY_RES)          $display("#%d [prim]  %d: MLDSA_VERIFY_EXP_A +%d", cyc, addr_i, addr_i - MLDSA_VERIFY_EXP_A); else
                if (addr_i < MLDSA_VERIFY_E)            $display("#%d [prim]  %d: MLDSA_VERIFY_RES +%d", cyc, addr_i, addr_i - MLDSA_VERIFY_RES); else
                if (addr_i < MLDSA_ERROR)               $display("#%d [prim]  %d: unknown; MLDSA_VERIFY_E + %d", cyc, addr_i, addr_i - MLDSA_VERIFY_E); else
                                                        $display("#%d [prim]  %d: ERROR; MLDSA_VERIFY_E + %d", cyc, addr_i, addr_i - MLDSA_VERIFY_E);
                $fflush();
            end
            addr_p  <=  addr_i;
        end
        cyc     <=  cyc + 1;
    end

endmodule

/*
=== add to mldsa_seq_sec.sv:
adams-bridge/src/mldsa_top/rtl/mldsa_seq_sec.sv

//Sequencer for MLDSA
//MLDSA functions
//  Signing initial steps
//  Signing validity checks
//  Signing signature generation

`include "mldsa_config_defines.svh"

module mldsa_seq_sec
  import mldsa_ctrl_pkg::*;
  (
  input logic clk,

  input  logic en_i,
  input  logic [MLDSA_PROG_ADDR_W-1 : 0] addr_i,
  output mldsa_seq_instr_t data_o
  );

    //  MJOS: debug address decoder
    mldsa_seq_decode_sec dec_sec(clk, en_i, addr_i);

*/

module mldsa_seq_decode_sec
    import mldsa_ctrl_pkg::*;
    (
        input logic clk,
        input logic en_i,
        input logic [MLDSA_PROG_ADDR_W-1 : 0] addr_i
    );
    logic [25 : 0] cyc = 0;
    logic [MLDSA_PROG_ADDR_W-1 : 0] addr_p = -1;

    always_ff @(posedge clk) begin
        if (en_i) begin
            if (addr_i != addr_p) begin
                //Signing Sequencer Subroutine listing
                if (addr_i == MLDSA_SIGN_CHECK_Y_VLD)   $display("#%d [sec ]  %d: MLDSA_SIGN_CHECK_Y_VLD", cyc, addr_i); else
                if (addr_i == MLDSA_SIGN_CLEAR_Y)       $display("#%d [sec ]  %d: MLDSA_SIGN_CLEAR_Y", cyc, addr_i); else
                if (addr_i == MLDSA_SIGN_CHECK_W0_VLD)  $display("#%d [sec ]  %d: MLDSA_SIGN_CHECK_W0_VLD", cyc, addr_i); else
                if (addr_i == MLDSA_SIGN_CLEAR_W0)      $display("#%d [sec ]  %d: MLDSA_SIGN_CLEAR_W0", cyc, addr_i); else
                if (addr_i == MLDSA_SIGN_CLEAR_C)       $display("#%d [sec ]  %d: MLDSA_SIGN_CLEAR_C", cyc, addr_i); else
                if (addr_i < MLDSA_ZEROIZE)             $display("#%d [sec ]  %d: MLDSA_RESET +%d", cyc, addr_i, addr_i - MLDSA_RESET); else
                if (addr_i < MLDSA_SIGN_INIT_S)         $display("#%d [sec ]  %d: MLDSA_ZEROIZE +%d", cyc, addr_i, addr_i - MLDSA_ZEROIZE); else
                if (addr_i < MLDSA_SIGN_CHECK_C_VLD)    $display("#%d [sec ]  %d: MLDSA_SIGN_INIT_S +%d", cyc, addr_i, addr_i - MLDSA_SIGN_INIT_S); else
                if (addr_i < MLDSA_SIGN_VALID_S)        $display("#%d [sec ]  %d: MLDSA_SIGN_CHECK_C_VLD +%d", cyc, addr_i, addr_i - MLDSA_SIGN_CHECK_C_VLD); else
                if (addr_i < MLDSA_SIGN_GEN_S)          $display("#%d [sec ]  %d: MLDSA_SIGN_VALID_S +%d", cyc, addr_i, addr_i - MLDSA_SIGN_VALID_S); else
                if (addr_i < MLDSA_SIGN_GEN_E)          $display("#%d [sec ]  %d: MLDSA_SIGN_GEN_S +%d", cyc, addr_i, addr_i - MLDSA_SIGN_GEN_S); else
                                                        $display("#%d [sec ]  %d: MLDSA_SIGN_GEN_E +%d", cyc, addr_i, addr_i - MLDSA_SIGN_GEN_E);
                $fflush();
            end
            addr_p  <=  addr_i;
        end
        cyc     <=  cyc + 1;
    end

endmodule


