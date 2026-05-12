//  ntt_top_tb.sv -- minimal standalone TB for ntt_top
//  Drives a single PWA opcode against an otherwise quiescent ntt_top
//  and logs ntt_busy each cycle.  Reference for the
//  presi/standalone-gates-C comparison harness.

`include "abr_config_defines.svh"

module ntt_top_tb
    import abr_params_pkg::*;
    import ntt_defines_pkg::*;
(
    input  wire clk,
    input  wire reset_n
);

    //  Stimulus state -- packed into a single struct so the driver
    //  doesn't have to know every internal port.  All sized to match
    //  ntt_top's port widths.
    mode_t                                       mode;
    logic                                        ntt_enable;
    logic                                        mlkem;
    logic                                        accumulate;
    logic                                        sampler_valid;
    logic                                        shuffle_en;
    logic                                        masking_en;
    logic                                        zeroize;
    logic [5:0]                                  random;
    logic [4:0][45:0]                            rnd_i;
    ntt_mem_addr_t                               ntt_mem_base_addr;
    pwo_mem_addr_t                               pwo_mem_base_addr;
    logic [ABR_MEM_MASKED_DATA_WIDTH-1:0]        mem_rd_data;
    logic [ABR_MEM_MASKED_DATA_WIDTH-1:0]        pwm_a_rd_data;
    logic [ABR_MEM_MASKED_DATA_WIDTH-1:0]        pwm_b_rd_data;

    mem_if_t                                     mem_wr_req;
    mem_if_t                                     mem_rd_req;
    logic [ABR_MEM_MASKED_DATA_WIDTH-1:0]        mem_wr_data;
    mem_if_t                                     pwm_a_rd_req;
    mem_if_t                                     pwm_b_rd_req;
    logic                                        ntt_busy;
    logic                                        ntt_done;

    ntt_top dut (
        .clk                (clk),
        .reset_n            (reset_n),
        .zeroize            (zeroize),
        .mode               (mode),
        .ntt_enable         (ntt_enable),
        .mlkem              (mlkem),
        .ntt_mem_base_addr  (ntt_mem_base_addr),
        .pwo_mem_base_addr  (pwo_mem_base_addr),
        .accumulate         (accumulate),
        .sampler_valid      (sampler_valid),
        .shuffle_en         (shuffle_en),
        .masking_en         (masking_en),
        .random             (random),
        .rnd_i              (rnd_i),
        .mem_wr_req         (mem_wr_req),
        .mem_rd_req         (mem_rd_req),
        .mem_wr_data        (mem_wr_data),
        .mem_rd_data        (mem_rd_data),
        .pwm_a_rd_req       (pwm_a_rd_req),
        .pwm_b_rd_req       (pwm_b_rd_req),
        .pwm_a_rd_data      (pwm_a_rd_data),
        .pwm_b_rd_data      (pwm_b_rd_data),
        .ntt_busy           (ntt_busy),
        .ntt_done           (ntt_done)
    );

    //  Drive the stimulus from the SV side.  Reset asserted by the
    //  C++ driver via the rst_b/reset_n pin; we just pick the
    //  operation we want here.
    logic [31:0] cyc = 0;
    logic        started = 0;

    always_ff @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            mode              <= 3'd0;
            ntt_enable        <= 1'b0;
            mlkem             <= 1'b1;       //  PWA in MLKEM context
            accumulate        <= 1'b0;
            sampler_valid     <= 1'b1;       //  pretend data is always valid
            shuffle_en        <= 1'b0;
            masking_en        <= 1'b0;
            zeroize           <= 1'b0;
            random            <= 6'd0;
            rnd_i             <= '0;
            ntt_mem_base_addr <= '0;
            pwo_mem_base_addr <= '0;
            mem_rd_data       <= '0;
            pwm_a_rd_data     <= '0;
            pwm_b_rd_data     <= '0;
            cyc               <= 32'd0;
            started           <= 1'b0;
        end else begin
            cyc <= cyc + 32'd1;
            //  Kick off a single PWA op after the first 5 cycles
            //  post-reset (matches Verilator post-reset behaviour
            //  in abr_top.sv -- the controller observes a few
            //  housekeeping cycles before issuing the first ntt op).
            if (cyc == 32'd5 && !started) begin
                mode       <= pwa;            //  3'd3
                ntt_enable <= 1'b1;
                started    <= 1'b1;
            end else if (cyc == 32'd6) begin
                ntt_enable <= 1'b0;           //  one-cycle pulse
            end
        end
    end

    //  Per-cycle trace.  Format matches abr_wrap's [eng] line so the
    //  shared diff helper can be reused.
    always_ff @(posedge clk) begin
        if (reset_n) begin
            $display("# %0d [ntt_tb] ntt_busy=%b ntt_done=%b ntt_enable=%b mode=%0d",
                cyc, ntt_busy, ntt_done, ntt_enable, mode);
            $fflush();
        end
    end

endmodule
