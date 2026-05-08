# Adams Bridge presilicon netlist simulator prototype.

TOP             ?= abr_wrap
# Hard cap on Yosys gates elaboration.  Measured 2026-05-07 with the
# per-coefficient engines + ntt_twiddle_lookup un-blackboxed and the
# heavy hitters (abr_sampler_top, ntt_top, abr_seq) still blackboxed:
# yosys ~3m17s, full clean rebuild ~5m18s.  300s gives a small safety
# margin and forces a hard stop if a future change pushes Yosys past
# what the budget allows.  Override on the command line for one-off
# experiments only.
GATE_TIMEOUT    ?= 300s
ROOT            := ..
BUILD           := _build
FLOW            := flow
ROOT_BUILD      := $(ROOT)/_build
VF              := $(ROOT_BUILD)/xabr_wrap.vf

SV2V            := $(BUILD)/$(TOP).sv2v.v
COARSE_YS       := $(BUILD)/$(TOP).coarse.ys
BLACKBOX_YS     := $(BUILD)/$(TOP).blackbox.ys
GATES_YS        := $(BUILD)/$(TOP).gates.ys
COARSE_NETLIST  := $(BUILD)/$(TOP).coarse.v
BLACKBOX_NETLIST:= $(BUILD)/$(TOP).blackbox.v
GATES_NETLIST   := $(BUILD)/$(TOP).gates.v
GATES_SPICE     := $(BUILD)/$(TOP).gates.sp
BLACKBOX_JSON   := $(BUILD)/$(TOP).blackbox.json
GATES_JSON      := $(BUILD)/$(TOP).gates.json
COARSE_STAT     := $(BUILD)/$(TOP).coarse.stat.rpt
BLACKBOX_STAT   := $(BUILD)/$(TOP).blackbox.stat.rpt
GATES_STAT      := $(BUILD)/$(TOP).gates.stat.rpt
SRAM_JSON       := $(BUILD)/$(TOP).sram.json
SRAM_H          := $(BUILD)/$(TOP).sram.h
SEQ_ROM_H       := $(BUILD)/$(TOP).seq_rom.h
SEQ_ROM_JSON    := $(BUILD)/$(TOP).seq_rom.json
ABR_SEQ_V       := $(BUILD)/abr_seq.standalone.v
ABR_SEQ_YS      := $(BUILD)/abr_seq.standalone.ys
ABR_SEQ_JSON    := $(BUILD)/abr_seq.standalone.json
INVENTORY       := $(BUILD)/$(TOP).inventory.txt
GATES_VAR_H     := $(BUILD)/$(TOP).presi_var.h
GATES_VAR_C     := $(BUILD)/$(TOP).presi_var.c
GATES_IDX_H     := $(BUILD)/$(TOP).presi_idx.h
GATES_CLK_H     := $(BUILD)/$(TOP).presi_clk.h
GATES_MAP       := $(BUILD)/$(TOP).presi_map.csv
GATES_BB        := $(BUILD)/$(TOP).presi_bb.csv
GATES_BB_WIRING := $(BUILD)/$(TOP).presi_bb_wiring.h
GATES_STAMP     := $(BUILD)/$(TOP).gate-c.stamp
# Cycle-update body is split across this many TUs so gcc never compiles a
# single multi-million-statement function.  Bump if compile time per TU
# starts to dominate; lower if link time dominates.
GATE_C_PARTS    ?= 32
# Within each part .c, the body is further sliced into static helper
# functions of CHUNK_SIZE statements each, all called in topo order from
# the public step_part_NNN function.  This caps gcc's per-basic-block
# work (var-tracking, liveness, register allocation) at a tractable
# size.  Empirically: -O0 cost is roughly linear with chunk size for
# the small-chunk regime, and -O1 becomes affordable.  CHUNK_SIZE=0
# disables chunking (one body, useful for diff against the pre-refactor
# layout).
CHUNK_SIZE      ?= 8192
GATE_PART_IDX   := $(shell python3 -c "print(' '.join(f'{i:03d}' for i in range($(GATE_C_PARTS))))")
GATES_PART_C    := $(foreach i,$(GATE_PART_IDX),$(BUILD)/$(TOP).presi_clk_part_$(i).c)
GATES_PART_O    := $(GATES_PART_C:.c=.o)
GATES_VAR_O     := $(GATES_VAR_C:.c=.o)
PRESI           := $(BUILD)/presi
PRESI_GATES     := $(BUILD)/presi-gates
# Harness .c sources.  Compiled into per-flavor .o files
# (.gates.o for the no-engines build, .cosim.o for the cosim build).
# The cosim flavor's .o files plus all gate-netlist .o files are
# bundled into libpresi_gates.a so iterating on presi.c only triggers
# a relink, not a recompile of the netlist code.
HARNESS_SRC     := presi.c presi_gates.c presi_state.c presi_sram.c
HARNESS_HDRS    := presi_model.h presi_gates.h presi_state.h presi_sram.h

.PHONY: all sv2v netlist-coarse netlist-blackbox netlist-gates gate-c sram-meta seq-rom ntt-top abr-sampler-top abr-seq-core engine-glue cosim lib run run-gates run-cosim run-cosim-keygen inventory clean

all: $(PRESI)

$(BUILD):
	mkdir -p $(BUILD)

$(VF):
	$(MAKE) -C $(ROOT) _build/xabr_wrap.vf

sv2v: $(SV2V)

$(SV2V): $(VF) $(FLOW)/vf_to_sv2v.py | $(BUILD)
	python3 $(FLOW)/vf_to_sv2v.py --top $(TOP) --vf $(VF) --out $@

$(COARSE_YS): $(FLOW)/gen_yosys.py $(SV2V) | $(BUILD)
	python3 $(FLOW)/gen_yosys.py --top $(TOP) --mode coarse \
		--in $(SV2V) --out $(COARSE_NETLIST) --stat $(COARSE_STAT) \
		--script $@

$(BLACKBOX_YS): $(FLOW)/gen_yosys.py $(SV2V) | $(BUILD)
	python3 $(FLOW)/gen_yosys.py --top $(TOP) --mode blackbox-sram \
		--in $(SV2V) --out $(BLACKBOX_NETLIST) --stat $(BLACKBOX_STAT) \
		--json-out $(BLACKBOX_JSON) --script $@

$(GATES_YS): $(FLOW)/gen_yosys.py $(FLOW)/cmos_cells.v $(SV2V) | $(BUILD)
	python3 $(FLOW)/gen_yosys.py --top $(TOP) --mode gates \
		--in $(SV2V) --out $(GATES_NETLIST) --stat $(GATES_STAT) \
		--json-out $(GATES_JSON) --spice-out $(GATES_SPICE) \
		--flow-dir $(FLOW) --script $@

netlist-coarse: $(COARSE_NETLIST)

$(COARSE_NETLIST) $(COARSE_STAT): $(COARSE_YS)
	yosys -q $(COARSE_YS) 2>&1 | tee $(BUILD)/$(TOP).coarse.yosys.log

netlist-blackbox: $(BLACKBOX_NETLIST)

$(BLACKBOX_NETLIST) $(BLACKBOX_STAT) $(BLACKBOX_JSON): $(BLACKBOX_YS)
	yosys -q $(BLACKBOX_YS) 2>&1 | tee $(BUILD)/$(TOP).blackbox.yosys.log

netlist-gates: $(GATES_SPICE)

$(GATES_NETLIST) $(GATES_STAT) $(GATES_JSON) $(GATES_SPICE): $(GATES_YS)
	timeout $(GATE_TIMEOUT) yosys -q $(GATES_YS) 2>&1 | \
		grep -v "no (blackbox) module for cell type" | \
		tee $(BUILD)/$(TOP).gates.yosys.log

gate-c: $(GATES_STAMP) $(GATES_BB_WIRING)

$(GATES_STAMP): $(GATES_SPICE) $(FLOW)/spice_to_c.py
	python3 $(FLOW)/spice_to_c.py --spice $(GATES_SPICE) \
		--top $(TOP) --num-parts $(GATE_C_PARTS) \
		--chunk-size $(CHUNK_SIZE) \
		--vars $(GATES_VAR_H) --vars-c $(GATES_VAR_C) \
		--clock $(GATES_CLK_H) --parts-dir $(BUILD) \
		--map $(GATES_MAP) --bb $(GATES_BB)
	@touch $@

# All gate-c outputs are produced atomically by the stamp recipe above.
$(GATES_VAR_H) $(GATES_VAR_C) $(GATES_IDX_H) $(GATES_CLK_H) $(GATES_MAP) $(GATES_BB) $(GATES_PART_C): $(GATES_STAMP)
	@:

# Blackbox wiring header: turns presi_bb.csv pin lists into the body of
# presi_sram_tick_all(), one block per SRAM instance plus an abr_seq
# $mem_v2 ROM block.
$(GATES_BB_WIRING): $(GATES_BB) $(SRAM_JSON) $(SEQ_ROM_JSON) \
                    $(FLOW)/gen_blackbox_wiring.py
	python3 $(FLOW)/gen_blackbox_wiring.py --bb $(GATES_BB) \
		--sram-json $(SRAM_JSON) --seq-rom-json $(SEQ_ROM_JSON) --out $@

sram-meta: $(SRAM_JSON) $(SRAM_H)

$(SRAM_JSON) $(SRAM_H): $(BLACKBOX_NETLIST) $(FLOW)/extract_sram_meta.py
	python3 $(FLOW)/extract_sram_meta.py --netlist $(BLACKBOX_NETLIST) \
		--json $(SRAM_JSON) --header $(SRAM_H)

# Extract the abr_seq sequencer ROM from a *standalone* Yosys build of
# just the abr_seq module.  The gates flow blackboxes abr_seq, so the
# gates JSON does not contain a $mem_v2 cell for the sequencer ROM --
# we run Yosys on abr_seq alone (slice extracted from sv2v.v) and feed
# the resulting JSON to extract_seq_rom.py.  This finishes in seconds
# instead of the 10+ minutes `proc` takes on the giant unique case
# inside abr_seq when elaborated as part of the full design.
seq-rom: $(SEQ_ROM_H) $(SEQ_ROM_JSON)

$(ABR_SEQ_V): $(SV2V) $(FLOW)/extract_abr_seq.py | $(BUILD)
	python3 $(FLOW)/extract_abr_seq.py --in $(SV2V) --top abr_seq --out $@

$(ABR_SEQ_YS): $(ABR_SEQ_V) | $(BUILD)
	@printf '%s\n' \
		'read_verilog $(ABR_SEQ_V)' \
		'hierarchy -check -top abr_seq' \
		'proc' \
		'memory -nomap' \
		'opt_clean' \
		'write_json $(ABR_SEQ_JSON)' > $@

$(ABR_SEQ_JSON): $(ABR_SEQ_YS)
	yosys -q $(ABR_SEQ_YS) 2>&1 | tee $(BUILD)/abr_seq.standalone.yosys.log

$(SEQ_ROM_H) $(SEQ_ROM_JSON): $(ABR_SEQ_JSON) $(FLOW)/extract_seq_rom.py
	python3 $(FLOW)/extract_seq_rom.py --standalone-json $(ABR_SEQ_JSON) \
		--header $(SEQ_ROM_H) --json $(SEQ_ROM_JSON)

# Per-engine gate flow.  Targets: ntt-top, abr-sampler-top (when needed).
# Each runs Yosys with `engine-gates` mode (no blackboxing) and emits
# its own `<engine>.gates.{sp,v,json,stat.rpt}`, then translates the
# SPICE to C via `spice_to_c.py`.  These are independent of the main
# abr_wrap flow and exist so engines that don't fit in the unified
# gates flow's 5-min budget can still be brought up for leakage
# analysis on their own.  Pipeline: gates.sp -> presi_var.{h,c},
# presi_clk.h, presi_clk_part_NNN.c (split into NTT_GATE_C_PARTS files
# for parallel compile), presi_bb.csv, presi_map.csv.  The harness
# that consumes these is per-engine and lives outside this Makefile.

NTT_GATE_C_PARTS    ?= 8
NTT_GATE_PART_IDX   := $(shell python3 -c "print(' '.join(f'{i:03d}' for i in range($(NTT_GATE_C_PARTS))))")
NTT_PART_C          := $(foreach i,$(NTT_GATE_PART_IDX),$(BUILD)/ntt_top.presi_clk_part_$(i).c)

ntt-top: $(BUILD)/ntt_top.gate-c.stamp

$(BUILD)/ntt_top.gates.ys: $(FLOW)/gen_yosys.py $(FLOW)/cmos_cells.v $(SV2V) | $(BUILD)
	python3 $(FLOW)/gen_yosys.py --top ntt_top --mode engine-gates \
		--in $(SV2V) --out $(BUILD)/ntt_top.gates.v \
		--stat $(BUILD)/ntt_top.gates.stat.rpt \
		--json-out $(BUILD)/ntt_top.gates.json \
		--spice-out $(BUILD)/ntt_top.gates.sp \
		--flow-dir $(FLOW) --script $@

$(BUILD)/ntt_top.gates.sp $(BUILD)/ntt_top.gates.v $(BUILD)/ntt_top.gates.json \
$(BUILD)/ntt_top.gates.stat.rpt: $(BUILD)/ntt_top.gates.ys
	timeout $(GATE_TIMEOUT) yosys -q $(BUILD)/ntt_top.gates.ys 2>&1 | \
		tee $(BUILD)/ntt_top.gates.yosys.log

$(BUILD)/ntt_top.gate-c.stamp: $(BUILD)/ntt_top.gates.sp $(FLOW)/spice_to_c.py
	python3 $(FLOW)/spice_to_c.py --spice $(BUILD)/ntt_top.gates.sp \
		--top ntt_top --num-parts $(NTT_GATE_C_PARTS) \
		--chunk-size $(CHUNK_SIZE) \
		--symbol-prefix 'ntt_top__' \
		--vars $(BUILD)/ntt_top.presi_var.h \
		--vars-c $(BUILD)/ntt_top.presi_var.c \
		--clock $(BUILD)/ntt_top.presi_clk.h --parts-dir $(BUILD) \
		--map $(BUILD)/ntt_top.presi_map.csv \
		--bb $(BUILD)/ntt_top.presi_bb.csv
	@touch $@

# All ntt_top gate-c outputs are produced atomically by the stamp.
$(BUILD)/ntt_top.presi_var.h $(BUILD)/ntt_top.presi_var.c \
$(BUILD)/ntt_top.presi_idx.h \
$(BUILD)/ntt_top.presi_clk.h $(BUILD)/ntt_top.presi_map.csv \
$(BUILD)/ntt_top.presi_bb.csv $(NTT_PART_C): $(BUILD)/ntt_top.gate-c.stamp
	@:

# abr_sampler_top per-engine flow.  Includes the SHA3/Keccak hierarchy
# plus the rejection / bounded / SIB / CBD samplers + an internal sib_mem.
# Standalone (without the abr_wrap control plane), it is the most
# TVLA-relevant engine of all.  Same machinery as ntt-top above.
SAMPLER_GATE_C_PARTS    ?= 8
SAMPLER_GATE_PART_IDX   := $(shell python3 -c "print(' '.join(f'{i:03d}' for i in range($(SAMPLER_GATE_C_PARTS))))")
SAMPLER_PART_C          := $(foreach i,$(SAMPLER_GATE_PART_IDX),$(BUILD)/abr_sampler_top.presi_clk_part_$(i).c)

abr-sampler-top: $(BUILD)/abr_sampler_top.gate-c.stamp

$(BUILD)/abr_sampler_top.gates.ys: $(FLOW)/gen_yosys.py $(FLOW)/cmos_cells.v $(SV2V) | $(BUILD)
	python3 $(FLOW)/gen_yosys.py --top abr_sampler_top --mode engine-gates \
		--in $(SV2V) --out $(BUILD)/abr_sampler_top.gates.v \
		--stat $(BUILD)/abr_sampler_top.gates.stat.rpt \
		--json-out $(BUILD)/abr_sampler_top.gates.json \
		--spice-out $(BUILD)/abr_sampler_top.gates.sp \
		--flow-dir $(FLOW) --script $@

$(BUILD)/abr_sampler_top.gates.sp $(BUILD)/abr_sampler_top.gates.v \
$(BUILD)/abr_sampler_top.gates.json $(BUILD)/abr_sampler_top.gates.stat.rpt: \
		$(BUILD)/abr_sampler_top.gates.ys
	timeout $(GATE_TIMEOUT) yosys -q $(BUILD)/abr_sampler_top.gates.ys 2>&1 | \
		tee $(BUILD)/abr_sampler_top.gates.yosys.log

$(BUILD)/abr_sampler_top.gate-c.stamp: $(BUILD)/abr_sampler_top.gates.sp $(FLOW)/spice_to_c.py
	python3 $(FLOW)/spice_to_c.py --spice $(BUILD)/abr_sampler_top.gates.sp \
		--top abr_sampler_top --num-parts $(SAMPLER_GATE_C_PARTS) \
		--chunk-size $(CHUNK_SIZE) \
		--symbol-prefix 'abr_sampler_top__' \
		--vars $(BUILD)/abr_sampler_top.presi_var.h \
		--vars-c $(BUILD)/abr_sampler_top.presi_var.c \
		--clock $(BUILD)/abr_sampler_top.presi_clk.h --parts-dir $(BUILD) \
		--map $(BUILD)/abr_sampler_top.presi_map.csv \
		--bb $(BUILD)/abr_sampler_top.presi_bb.csv
	@touch $@

# All abr_sampler_top gate-c outputs are produced atomically by the stamp.
$(BUILD)/abr_sampler_top.presi_var.h $(BUILD)/abr_sampler_top.presi_var.c \
$(BUILD)/abr_sampler_top.presi_idx.h \
$(BUILD)/abr_sampler_top.presi_clk.h $(BUILD)/abr_sampler_top.presi_map.csv \
$(BUILD)/abr_sampler_top.presi_bb.csv $(SAMPLER_PART_C): \
		$(BUILD)/abr_sampler_top.gate-c.stamp
	@:

# abr_seq controller per-engine flow.  abr_seq is blackboxed in the
# unified abr_wrap flow because its 1024-way `unique case` ROM
# dispatch stalled `proc` for >25 min on Yosys 0.36 -- documented
# but possibly fixed on 0.64+ since it brought a `proc_mux`
# regression fix.  Standalone (no abr_wrap surrounding logic),
# `make abr-seq-core` runs the same engine-gates pipeline as
# ntt-top / abr-sampler-top.  If the 5-min cap holds, this gives
# us the sequencer FSM + ROM combined as gates so the controller
# can co-simulate alongside abr_wrap.
SEQ_GATE_C_PARTS    ?= 8
SEQ_GATE_PART_IDX   := $(shell python3 -c "print(' '.join(f'{i:03d}' for i in range($(SEQ_GATE_C_PARTS))))")
SEQ_PART_C          := $(foreach i,$(SEQ_GATE_PART_IDX),$(BUILD)/abr_seq.presi_clk_part_$(i).c)

abr-seq-core: $(BUILD)/abr_seq.gate-c.stamp

$(BUILD)/abr_seq.gates.ys: $(FLOW)/gen_yosys.py $(FLOW)/cmos_cells.v $(SV2V) | $(BUILD)
	python3 $(FLOW)/gen_yosys.py --top abr_seq --mode engine-gates \
		--in $(SV2V) --out $(BUILD)/abr_seq.gates.v \
		--stat $(BUILD)/abr_seq.gates.stat.rpt \
		--json-out $(BUILD)/abr_seq.gates.json \
		--spice-out $(BUILD)/abr_seq.gates.sp \
		--flow-dir $(FLOW) --script $@

$(BUILD)/abr_seq.gates.sp $(BUILD)/abr_seq.gates.v $(BUILD)/abr_seq.gates.json \
$(BUILD)/abr_seq.gates.stat.rpt: $(BUILD)/abr_seq.gates.ys
	timeout $(GATE_TIMEOUT) yosys -q $(BUILD)/abr_seq.gates.ys 2>&1 | \
		tee $(BUILD)/abr_seq.gates.yosys.log

$(BUILD)/abr_seq.gate-c.stamp: $(BUILD)/abr_seq.gates.sp $(FLOW)/spice_to_c.py
	python3 $(FLOW)/spice_to_c.py --spice $(BUILD)/abr_seq.gates.sp \
		--top abr_seq --num-parts $(SEQ_GATE_C_PARTS) \
		--chunk-size $(CHUNK_SIZE) \
		--symbol-prefix 'abr_seq__' \
		--vars $(BUILD)/abr_seq.presi_var.h \
		--vars-c $(BUILD)/abr_seq.presi_var.c \
		--clock $(BUILD)/abr_seq.presi_clk.h --parts-dir $(BUILD) \
		--map $(BUILD)/abr_seq.presi_map.csv \
		--bb $(BUILD)/abr_seq.presi_bb.csv
	@touch $@

# Per-engine glue: links each engine's standalone gate netlist to its
# blackbox pin connections in the unified abr_wrap netlist.  Glue C
# files copy abr_wrap-side bb pin values onto the engine's input
# externs, step the engine, then copy engine outputs back to the
# abr_wrap-side externs.  Generated from:
#   - abr_wrap.presi_bb.csv (pin list per blackbox cell instance)
#   - <engine>.gates.v       (engine's port directions/widths)
NTT_GLUE_C      := $(BUILD)/ntt_top.glue.c
SAMPLER_GLUE_C  := $(BUILD)/abr_sampler_top.glue.c
NTT_GLUE_O      := $(BUILD)/ntt_top.glue.o
SAMPLER_GLUE_O  := $(BUILD)/abr_sampler_top.glue.o

engine-glue: $(NTT_GLUE_C) $(SAMPLER_GLUE_C)

$(NTT_GLUE_C): $(GATES_BB) $(GATES_MAP) $(BUILD)/ntt_top.gates.v \
               $(BUILD)/ntt_top.presi_map.csv $(FLOW)/gen_engine_glue.py
	python3 $(FLOW)/gen_engine_glue.py --engine ntt_top \
		--abr-wrap-bb $(GATES_BB) \
		--abr-wrap-map $(GATES_MAP) \
		--engine-gates-v $(BUILD)/ntt_top.gates.v \
		--engine-map $(BUILD)/ntt_top.presi_map.csv \
		--engine-prefix 'ntt_top__' \
		--engine-num-parts $(NTT_GATE_C_PARTS) \
		--out $@
	# Note: per-engine `--gate-on-port` was removed 2026-05-07.  Gating
	# inside the glue had a subtle clk_prev staleness bug -- when the
	# gate skipped a step, the engine's `presi_clk_prev` stayed at the
	# previous-step's clk, so when the gate next fired the rising-edge
	# predicate `(clk & ~clk_prev)` could be 0 and miss an edge.  The
	# outer `busy_o`-based gate in `presi_engines_step()` covers the
	# common idle case (reset, post-completion).  Re-introduce
	# per-engine gating only with explicit clk_prev maintenance.

$(SAMPLER_GLUE_C): $(GATES_BB) $(GATES_MAP) $(BUILD)/abr_sampler_top.gates.v \
                   $(BUILD)/abr_sampler_top.presi_map.csv $(FLOW)/gen_engine_glue.py
	python3 $(FLOW)/gen_engine_glue.py --engine abr_sampler_top \
		--abr-wrap-bb $(GATES_BB) \
		--abr-wrap-map $(GATES_MAP) \
		--engine-gates-v $(BUILD)/abr_sampler_top.gates.v \
		--engine-map $(BUILD)/abr_sampler_top.presi_map.csv \
		--engine-prefix 'abr_sampler_top__' \
		--engine-num-parts $(SAMPLER_GATE_C_PARTS) \
		--out $@
	# See ntt_top above re: --gate-on-port removed.

$(NTT_GLUE_O): $(NTT_GLUE_C)
	gcc $(CFLAGS_GATES) -c -o $@ $<

$(SAMPLER_GLUE_O): $(SAMPLER_GLUE_C)
	gcc $(CFLAGS_GATES) -c -o $@ $<

inventory: $(INVENTORY)

$(INVENTORY): $(BLACKBOX_JSON) $(FLOW)/netlist_inventory.py
	python3 $(FLOW)/netlist_inventory.py --json $(BLACKBOX_JSON) --out $@

$(PRESI): $(HARNESS_SRC) $(HARNESS_HDRS) $(SRAM_H) | $(BUILD)
	gcc -std=c99 -Wall -Wextra -O0 -I$(BUILD) -I. -o $@ $(HARNESS_SRC)

# Optimization level for the giant generated gate-update files.
# Measurements 2026-05-07 on the 49 MB ntt_top.presi_clk_part_001.c:
#   gcc -O0    17 s   <-- default
#   clang -O0  13 s
#   gcc -Og    48 s
#   gcc -O1   ~5 min
#   gcc -O1 -fno-tree-pre -fno-tree-fre -fno-tree-coalesce-vars  4m45s
#   clang -O1  8m44s   (worse than gcc at this size)
#   gcc -Os    8m34s
# At 48 part files in the cosim binary, only -O0 (and marginally -Og)
# fit the 5-minute clean-rebuild budget.  The per-cycle slowdown vs
# -O0 wasn't measured for -Og since the compile budget is tight.
# Override with `make ... GATES_OPT=-Og` for a long-running build
# where you don't mind a 10 min clean compile.
GATES_OPT       ?= -O0
CFLAGS_GATES    := -std=c99 -Wall -Wextra $(GATES_OPT) -DPRESI_HAVE_NETLIST \
                   -I$(BUILD) -I.

# Per-part and var.c objects are compiled in parallel via `make -j`.
# These are flavor-independent (only need PRESI_HAVE_NETLIST).
$(BUILD)/$(TOP).presi_clk_part_%.o: $(BUILD)/$(TOP).presi_clk_part_%.c $(GATES_VAR_H)
	gcc $(CFLAGS_GATES) -c -o $@ $<

$(GATES_VAR_O): $(GATES_VAR_C) $(GATES_VAR_H)
	gcc $(CFLAGS_GATES) -c -o $@ $<

# Engine .o files (also flavor-independent: each is a self-contained
# block of cell statements that doesn't reference engine-glue externs).
NTT_VAR_O           := $(BUILD)/ntt_top.presi_var.o
SAMPLER_VAR_O       := $(BUILD)/abr_sampler_top.presi_var.o
NTT_PART_O          := $(foreach i,$(NTT_GATE_PART_IDX),$(BUILD)/ntt_top.presi_clk_part_$(i).o)
SAMPLER_PART_O      := $(foreach i,$(SAMPLER_GATE_PART_IDX),$(BUILD)/abr_sampler_top.presi_clk_part_$(i).o)
PRESI_GATES_COSIM   := $(BUILD)/presi-gates-cosim
LIB_PRESI_GATES     := $(BUILD)/libpresi_gates.a
CFLAGS_COSIM        := $(CFLAGS_GATES) -DPRESI_HAVE_ENGINE_NETLISTS

$(NTT_VAR_O): $(BUILD)/ntt_top.presi_var.c $(BUILD)/ntt_top.gate-c.stamp
	gcc $(CFLAGS_GATES) -c -o $@ $<
$(BUILD)/ntt_top.presi_clk_part_%.o: $(BUILD)/ntt_top.presi_clk_part_%.c \
                                     $(BUILD)/ntt_top.gate-c.stamp
	gcc $(CFLAGS_GATES) -c -o $@ $<

$(SAMPLER_VAR_O): $(BUILD)/abr_sampler_top.presi_var.c $(BUILD)/abr_sampler_top.gate-c.stamp
	gcc $(CFLAGS_GATES) -c -o $@ $<
$(BUILD)/abr_sampler_top.presi_clk_part_%.o: \
                $(BUILD)/abr_sampler_top.presi_clk_part_%.c \
                $(BUILD)/abr_sampler_top.gate-c.stamp
	gcc $(CFLAGS_GATES) -c -o $@ $<

# ---- Harness object files, two flavors.
#
# .gates.o : compiled with CFLAGS_GATES (no engine references)
# .cosim.o : compiled with CFLAGS_COSIM (engine externs visible)
#
# We build distinct .o paths because presi_gates.c and presi_state.c
# branch on PRESI_HAVE_ENGINE_NETLISTS to pull in the per-engine
# glue + extra snapshot sections; same .c file, two object files.
# presi.c also gets two flavors so it picks up the right state code.
# presi_sram.c is flag-agnostic and only needs one .o.

PRESI_C_HDR_DEPS    := $(HARNESS_HDRS) $(SRAM_H) $(GATES_IDX_H) $(GATES_VAR_H)
PRESI_GATES_C_HDRS  := $(HARNESS_HDRS) $(SRAM_H) $(GATES_IDX_H) $(GATES_VAR_H) \
                       $(GATES_CLK_H) $(GATES_BB_WIRING) $(SEQ_ROM_H)
PRESI_STATE_C_HDRS  := presi_state.h presi_model.h $(GATES_VAR_H)

$(BUILD)/presi.gates.o: presi.c $(PRESI_C_HDR_DEPS)
	gcc $(CFLAGS_GATES) -c -o $@ presi.c
$(BUILD)/presi.cosim.o: presi.c $(PRESI_C_HDR_DEPS)
	gcc $(CFLAGS_COSIM) -c -o $@ presi.c

$(BUILD)/presi_gates.gates.o: presi_gates.c $(PRESI_GATES_C_HDRS)
	gcc $(CFLAGS_GATES) -c -o $@ presi_gates.c
$(BUILD)/presi_gates.cosim.o: presi_gates.c $(PRESI_GATES_C_HDRS)
	gcc $(CFLAGS_COSIM) -c -o $@ presi_gates.c

$(BUILD)/presi_state.gates.o: presi_state.c $(PRESI_STATE_C_HDRS)
	gcc $(CFLAGS_GATES) -c -o $@ presi_state.c
$(BUILD)/presi_state.cosim.o: presi_state.c $(PRESI_STATE_C_HDRS) \
                              $(BUILD)/ntt_top.presi_var.h \
                              $(BUILD)/abr_sampler_top.presi_var.h
	gcc $(CFLAGS_COSIM) -c -o $@ presi_state.c

$(BUILD)/presi_sram.o: presi_sram.c presi_sram.h $(SRAM_H)
	gcc $(CFLAGS_GATES) -c -o $@ presi_sram.c

PRESI_GATES_HARNESS_O   := $(BUILD)/presi.gates.o $(BUILD)/presi_gates.gates.o \
                           $(BUILD)/presi_state.gates.o $(BUILD)/presi_sram.o
PRESI_COSIM_HARNESS_O   := $(BUILD)/presi.cosim.o $(BUILD)/presi_gates.cosim.o \
                           $(BUILD)/presi_state.cosim.o $(BUILD)/presi_sram.o
# Harness .o files that go INTO libpresi_gates.a (everything except
# presi.cosim.o, which is the binary's main TU and stays out of the
# archive so multiple binaries can link the same library).
PRESI_LIB_HARNESS_O     := $(BUILD)/presi_gates.cosim.o \
                           $(BUILD)/presi_state.cosim.o $(BUILD)/presi_sram.o

# ---- presi-gates: no-engines binary (control-plane smoke).
$(PRESI_GATES): $(PRESI_GATES_HARNESS_O) $(GATES_VAR_O) $(GATES_PART_O) | $(BUILD)
	gcc $(CFLAGS_GATES) -o $@ $(PRESI_GATES_HARNESS_O) \
		$(GATES_VAR_O) $(GATES_PART_O)

# ---- libpresi_gates.a: cosim flavor.  Bundles all gate-netlist .o
# files plus the cosim-flavor harness .o files (presi_gates / state /
# sram).  Iterating on presi.c only relinks; nothing in the archive
# is touched.  Archive size is whatever the constituent .o files
# total -- ~258 MB for the current build.
LIB_PRESI_GATES_OBJS    := $(GATES_VAR_O) $(GATES_PART_O) \
                           $(NTT_VAR_O) $(NTT_PART_O) $(NTT_GLUE_O) \
                           $(SAMPLER_VAR_O) $(SAMPLER_PART_O) $(SAMPLER_GLUE_O) \
                           $(PRESI_LIB_HARNESS_O)

$(LIB_PRESI_GATES): $(LIB_PRESI_GATES_OBJS) | $(BUILD)
	rm -f $@
	ar rcs $@ $(LIB_PRESI_GATES_OBJS)

# Standalone library build (`make lib`) -- useful when iterating on
# generated headers / netlist code without producing a binary.
lib: $(LIB_PRESI_GATES)

cosim: $(PRESI_GATES_COSIM)

# Cosim binary: presi.cosim.o + libpresi_gates.a.  Editing presi.c
# triggers exactly one .o recompile + one link (~30 s vs the ~12 min
# clean cosim rebuild).
$(PRESI_GATES_COSIM): $(BUILD)/presi.cosim.o $(LIB_PRESI_GATES) | $(BUILD)
	gcc $(CFLAGS_COSIM) -o $@ $(BUILD)/presi.cosim.o \
		-L$(BUILD) -lpresi_gates

# Smoke run -- kicks the FSM with the existing 256-cycle poll trace.
run-cosim: $(PRESI_GATES_COSIM)
	./$(PRESI_GATES_COSIM)

# Real Dilithium keygen.  Generate inputs first via the project root
# (`flow/mldsa-gen.py keygen`), then run from this directory so the
# binary picks up `ent_in.dat` / `seed_in.dat` from the current dir
# and writes `pk_out.dat` / `sk_out.dat`.  KEYGEN_MAX_CYCLES caps
# the run time -- bump if the run hits the timeout.
KEYGEN_MAX_CYCLES   ?= 200000

run-cosim-keygen: $(PRESI_GATES_COSIM)
	./$(PRESI_GATES_COSIM) -t $(KEYGEN_MAX_CYCLES) mldsa-keygen

run: $(PRESI)
	./$(PRESI)

run-gates: $(PRESI_GATES)
	./$(PRESI_GATES)

clean:
	$(RM) -rf $(BUILD)
