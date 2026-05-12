//  abr_wrap.sv
//  2026-05-01  Markku-Juhani O. Saarinen <mjos@iki.fi>

`include "abr_config_defines.svh"
`include "abr_prim_assert.sv"

module abr_wrap
    #(  // top level params
        parameter AHB_ADDR_WIDTH = 32,
        parameter AHB_DATA_WIDTH = 64,
        parameter CLIENT_DATA_WIDTH = 32
    )
    (
        input wire  clk,
        input wire  rst_b,

`ifdef RV_FPGA_SCA
        output wire                         NTT_trigger,
        output wire                         PWM_trigger,
        output wire                         PWA_trigger,
        output wire                         INTT_trigger,
`endif

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

    abr_mem_if abr_memory_export();

    abr_mem_top mem_top_inst
    (
        .clk_i(clk),
        .abr_memory_export
    );

    abr_top #(
        .AHB_ADDR_WIDTH(AHB_ADDR_WIDTH),
        .AHB_DATA_WIDTH(AHB_DATA_WIDTH),
        .CLIENT_DATA_WIDTH(CLIENT_DATA_WIDTH)
    ) top0 (
        .clk            (   clk         ),
        .rst_b          (   rst_b       ),
`ifdef RV_FPGA_SCA
        .NTT_trigger    (   NTT_trigger ),
        .PWM_trigger    (   PWM_trigger ),
        .PWA_trigger    (   PWA_trigger ),
        .INTT_trigger   (   INTT_trigger),
`endif
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
        .abr_memory_export,
        .debugUnlock_or_scan_mode_switch ( '0 ),
        .busy_o         (   busy_o      ),
        .error_intr     (   error_intr  ),
        .notif_intr     (   notif_intr  )
    );

`ifndef SYNTHESIS
    //  --- engine-signal trace, simulation-only.
    //      gate-synthesis flow (sv2v -D SYNTHESIS) strips this block.
    //      Reads engine handshakes via hierarchical reference into
    //      top0; opt-in via +trace-eng on the Verilator binary.
    logic eng_trace_en = 1'b0;
    logic [25:0] eng_cyc = 0;
    initial begin
        if ($test$plusargs("trace-eng")) eng_trace_en = 1'b1;
    end
    always_ff @(posedge clk) begin
        if (rst_b && eng_trace_en) begin
            $display("#%d [eng]  ntt_busy=%b sampler_busy=%b sampler_dv=%b sha3_dv=%b busy_o=%b",
                eng_cyc, top0.ntt_busy, top0.sampler_busy,
                top0.sampler_top_inst.sampler_state_dv_o,
                top0.sampler_top_inst.sha3_state_dv,
                busy_o);
        end
        eng_cyc <= eng_cyc + 1;
    end
`endif

endmodule
