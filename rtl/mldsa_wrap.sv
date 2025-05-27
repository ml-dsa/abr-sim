//  mldsa_wrap.sv
//  2025-01-25  Markku-Juhani O. Saarinen <mjos@iki.fi>

`include "mldsa_config_defines.svh"
`include "abr_prim_assert.sv"

module mldsa_wrap
    #(  //top level params
        parameter   AHB_ADDR_WIDTH = 18,
        parameter   AHB_DATA_WIDTH = 64,
        parameter   CLIENT_DATA_WIDTH = 32
    )
    (
        input wire  clk,
        input wire  rst_b,

        //  ahb input
        input wire  [AHB_ADDR_WIDTH-1:0]    haddr_i,
        input wire  [AHB_DATA_WIDTH-1:0]    hwdata_i,
        input wire                          hsel_i,
        input wire                          hwrite_i,
        input wire                          hready_i,
        input wire  [1:0]                   htrans_i,
        input wire  [2:0]                   hsize_i,

        //  ahb output
        output wire                         hresp_o,
        output wire                         hreadyout_o,
        output wire [AHB_DATA_WIDTH-1:0]    hrdata_o,
        output wire                         busy_o,
        output wire                         error_intr,
        output wire                         notif_intr
    );

    mldsa_mem_if mldsa_memory_export();

    // SRAM module
    mldsa_mem_top
    mldsa_mem_top_inst
    (
        .clk_i(clk),
        .mldsa_memory_export
    );

    // DUT
    mldsa_top top0 (
        .clk            (   clk         ),
        .rst_b          (   rst_b       ),
        .haddr_i        (   haddr_i     ),
        .hwdata_i       (   hwdata_i    ),
        .hsel_i         (   hsel_i      ),
        .hwrite_i       (   hwrite_i    ),
        .hready_i       (   hready_i    ),
        .htrans_i       (   htrans_i    ),
        .hsize_i        (   hsize_i     ),
        .hresp_o        (   hresp_o     ),
        .hreadyout_o    (   hreadyout_o ),
        .hrdata_o       (   hrdata_o    ),
        .mldsa_memory_export,
        .debugUnlock_or_scan_mode_switch ( '0 ),
        .busy_o         (   busy_o      ),
        .error_intr     (   error_intr  ),
        .notif_intr     (   notif_intr  )
    );

endmodule

