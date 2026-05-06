# Adams Bridge presilicon netlist simulator prototype.

TOP             ?= abr_wrap
GATE_TIMEOUT    ?= 600s
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
INVENTORY       := $(BUILD)/$(TOP).inventory.txt
GATES_VAR_H     := $(BUILD)/$(TOP).presi_var.h
GATES_CLK_H     := $(BUILD)/$(TOP).presi_clk.h
GATES_MAP       := $(BUILD)/$(TOP).presi_map.csv
PRESI           := $(BUILD)/presi
PRESI_GATES     := $(BUILD)/presi-gates
CSRC            := presi.c presi_sram.c

.PHONY: all sv2v netlist-coarse netlist-blackbox netlist-gates gate-c sram-meta inventory run run-gates clean

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
	timeout $(GATE_TIMEOUT) yosys -q $(GATES_YS) 2>&1 | tee $(BUILD)/$(TOP).gates.yosys.log

gate-c: $(GATES_VAR_H) $(GATES_CLK_H)

$(GATES_VAR_H) $(GATES_CLK_H) $(GATES_MAP): $(GATES_SPICE) $(FLOW)/spice_to_c.py
	python3 $(FLOW)/spice_to_c.py --spice $(GATES_SPICE) \
		--vars $(GATES_VAR_H) --clock $(GATES_CLK_H) \
		--map $(GATES_MAP)

sram-meta: $(SRAM_JSON) $(SRAM_H)

$(SRAM_JSON) $(SRAM_H): $(BLACKBOX_NETLIST) $(FLOW)/extract_sram_meta.py
	python3 $(FLOW)/extract_sram_meta.py --netlist $(BLACKBOX_NETLIST) \
		--json $(SRAM_JSON) --header $(SRAM_H)

inventory: $(INVENTORY)

$(INVENTORY): $(BLACKBOX_JSON) $(FLOW)/netlist_inventory.py
	python3 $(FLOW)/netlist_inventory.py --json $(BLACKBOX_JSON) --out $@

$(PRESI): $(CSRC) presi_sram.h $(SRAM_H) | $(BUILD)
	gcc -std=c99 -Wall -Wextra -O0 -I$(BUILD) -I. -o $@ $(CSRC)

$(PRESI_GATES): $(CSRC) presi_sram.h $(SRAM_H) $(GATES_VAR_H) $(GATES_CLK_H) | $(BUILD)
	gcc -std=c99 -Wall -Wextra -O0 -DPRESI_HAVE_NETLIST \
		-I$(BUILD) -I. -o $@ $(CSRC)

run: $(PRESI)
	./$(PRESI)

run-gates: $(PRESI_GATES)
	./$(PRESI_GATES)

clean:
	$(RM) -rf $(BUILD)
