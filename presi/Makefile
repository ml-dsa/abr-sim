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
GATES_CLK_H     := $(BUILD)/$(TOP).presi_clk.h
GATES_MAP       := $(BUILD)/$(TOP).presi_map.csv
GATES_BB        := $(BUILD)/$(TOP).presi_bb.csv
GATES_BB_WIRING := $(BUILD)/$(TOP).presi_bb_wiring.h
GATES_STAMP     := $(BUILD)/$(TOP).gate-c.stamp
# Cycle-update body is split across this many TUs so gcc never compiles a
# single multi-million-statement function.  Bump if compile time per TU
# starts to dominate; lower if link time dominates.
GATE_C_PARTS    ?= 32
GATE_PART_IDX   := $(shell python3 -c "print(' '.join(f'{i:03d}' for i in range($(GATE_C_PARTS))))")
GATES_PART_C    := $(foreach i,$(GATE_PART_IDX),$(BUILD)/$(TOP).presi_clk_part_$(i).c)
GATES_PART_O    := $(GATES_PART_C:.c=.o)
GATES_VAR_O     := $(GATES_VAR_C:.c=.o)
PRESI           := $(BUILD)/presi
PRESI_GATES     := $(BUILD)/presi-gates
CSRC            := presi.c presi_sram.c

.PHONY: all sv2v netlist-coarse netlist-blackbox netlist-gates gate-c sram-meta seq-rom inventory run run-gates clean

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
		--vars $(GATES_VAR_H) --vars-c $(GATES_VAR_C) \
		--clock $(GATES_CLK_H) --parts-dir $(BUILD) \
		--map $(GATES_MAP) --bb $(GATES_BB)
	@touch $@

# All gate-c outputs are produced atomically by the stamp recipe above.
$(GATES_VAR_H) $(GATES_VAR_C) $(GATES_CLK_H) $(GATES_MAP) $(GATES_BB) $(GATES_PART_C): $(GATES_STAMP)
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

inventory: $(INVENTORY)

$(INVENTORY): $(BLACKBOX_JSON) $(FLOW)/netlist_inventory.py
	python3 $(FLOW)/netlist_inventory.py --json $(BLACKBOX_JSON) --out $@

$(PRESI): $(CSRC) presi_sram.h $(SRAM_H) | $(BUILD)
	gcc -std=c99 -Wall -Wextra -O0 -I$(BUILD) -I. -o $@ $(CSRC)

CFLAGS_GATES    := -std=c99 -Wall -Wextra -O0 -DPRESI_HAVE_NETLIST \
                   -I$(BUILD) -I.

# Per-part and var.c objects are compiled in parallel via `make -j`.
$(BUILD)/$(TOP).presi_clk_part_%.o: $(BUILD)/$(TOP).presi_clk_part_%.c $(GATES_VAR_H)
	gcc $(CFLAGS_GATES) -c -o $@ $<

$(GATES_VAR_O): $(GATES_VAR_C) $(GATES_VAR_H)
	gcc $(CFLAGS_GATES) -c -o $@ $<

$(PRESI_GATES): $(CSRC) presi_sram.h $(SRAM_H) $(SEQ_ROM_H) $(GATES_VAR_H) \
                $(GATES_CLK_H) $(GATES_BB_WIRING) $(GATES_VAR_O) \
                $(GATES_PART_O) | $(BUILD)
	gcc $(CFLAGS_GATES) -o $@ $(CSRC) $(GATES_VAR_O) $(GATES_PART_O)

run: $(PRESI)
	./$(PRESI)

run-gates: $(PRESI_GATES)
	./$(PRESI_GATES)

clean:
	$(RM) -rf $(BUILD)
