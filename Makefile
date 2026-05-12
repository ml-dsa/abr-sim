#	Makefile
#	2024-11-27	Markku-Juhani O. Saarinen <mjos@iki.fi>.  See LICENSE.

#	build directory for verilator
ABR_SRC		=	adams-bridge
ABR_ROOT_ABS	=	$(abspath $(ABR_SRC))
BUILD		=	_build
MASKING		=	1
JOBS		?=	$(shell nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)
CCACHE		?=	1
CCACHE_DIR	?=	$(abspath $(BUILD)/ccache)

#	separate binaries
READVCD		=	readvcd
MLDSA_WRAP	=	mldsa_wrap
ABR_WRAP	=	abr_wrap

#	
VERILATOR	=	verilator

ifeq ($(CCACHE),1)
OBJCACHE	=	ccache
else
OBJCACHE	=
endif

VFLAGS	=	-Wno-WIDTH -Wno-UNOPTFLAT -Wno-LITENDIAN -Wno-CMPCONST \
			-Wno-MULTIDRIVEN -Wno-UNPACKED \
			--timescale 1ns/100ps
VFLAGS	+=	--trace -CFLAGS "-DPRESI_TRACE"

RTLDEP	=	rtl/abr_seq.sv rtl/abr_seq_decode.sv rtl/abr_wrap.sv \
			$(ABR_SRC)/src/abr_top/config/abr_top.vf
			
all:	$(READVCD) $(ABR_WRAP)

#	verilator

$(ABR_WRAP):	$(BUILD)/Vabr_wrap
	cp -p $(BUILD)/Vabr_wrap $(ABR_WRAP)

$(BUILD)/Vabr_wrap: $(BUILD)/Vabr_wrap.mk src/abr_wrap.cpp
	CCACHE_DIR=$(CCACHE_DIR) $(MAKE) -j$(JOBS) -C $(BUILD) -f Vabr_wrap.mk \
		CC=gcc CXX=g++ LINK=g++ OBJCACHE="$(OBJCACHE)" LDFLAGS=""

$(BUILD)/Vabr_wrap.mk: $(BUILD) $(BUILD)/xabr_wrap.vf src/abr_wrap.cpp
	$(VERILATOR) $(VFLAGS) -Mdir $(BUILD) -cc --exe \
		--top-module abr_wrap -f $(BUILD)/xabr_wrap.vf src/abr_wrap.cpp

#	Standalone ntt_top unit testbench for diagnosing the engine
#	co-sim divergence.  Same .vf compile env, just a different
#	--top-module that wraps ntt_top with stimulus.

NTT_TB	=	ntt_top_tb

$(NTT_TB):	$(BUILD)/V$(NTT_TB)
	cp -p $(BUILD)/V$(NTT_TB) $(NTT_TB)

$(BUILD)/V$(NTT_TB): $(BUILD)/V$(NTT_TB).mk src/$(NTT_TB).cpp
	CCACHE_DIR=$(CCACHE_DIR) $(MAKE) -j$(JOBS) -C $(BUILD) -f V$(NTT_TB).mk \
		CC=gcc CXX=g++ LINK=g++ OBJCACHE="$(OBJCACHE)" LDFLAGS=""

$(BUILD)/V$(NTT_TB).mk: $(BUILD)/x$(NTT_TB).vf src/$(NTT_TB).cpp
	$(VERILATOR) $(VFLAGS) -Mdir $(BUILD) -cc --exe \
		--top-module $(NTT_TB) -f $(BUILD)/x$(NTT_TB).vf src/$(NTT_TB).cpp

#	Reuse xabr_wrap.vf but swap in our local TB at the end (the
#	stock TB pulls in everything we need anyway -- ntt_top + its
#	subtree + the packages that define mode_t / mem_if_t / etc.).
$(BUILD)/x$(NTT_TB).vf: $(BUILD)/xabr_wrap.vf rtl/$(NTT_TB).sv
	cp $< $@
	printf '\nrtl/$(NTT_TB).sv\n' >> $@

$(BUILD)/xabr_wrap.vf: $(BUILD) $(RTLDEP)
	sed -e 's@$${ADAMSBRIDGE_ROOT}/src/abr_top/rtl/abr_seq.sv@rtl/abr_seq.sv@' \
		-e 's@$${ADAMSBRIDGE_ROOT}@$(ABR_ROOT_ABS)@' \
		$(ABR_SRC)/src/abr_top/config/abr_top.vf > $@
	printf '\nrtl/abr_seq_decode.sv\nrtl/abr_wrap.sv\n' >> $@

#	patch to create progress info

rtl/abr_seq.sv:	adams-bridge/src/abr_top/rtl/abr_seq.sv rtl/abr_seq.sv.patch
	cp $< $@
	patch -p0 $@ < $@.patch

lint-abr: $(BUILD) $(BUILD)/xabr_wrap.vf
	$(VERILATOR) $(VFLAGS) -Mdir $(BUILD) --lint-only \
		--top-module abr_wrap -f $(BUILD)/xabr_wrap.vf

mock-netlist: $(BUILD)/xabr_wrap.vf
	bash flow/abr-yosys-mock.sh coarse

mock-netlist-gates: $(BUILD)/xabr_wrap.vf
	bash flow/abr-yosys-mock.sh gates
	
#	separate binaries

$(READVCD):	src/readvcd.c
	gcc -O2 -Wall -Wextra -o $@ $<

$(BUILD):
	mkdir -p $(BUILD)

#       cleanup

clean:
	$(RM)   -f	$(READVCD) $(MLDSA_WRAP) $(ABR_WRAP) *.vcd *.dat *.log
	$(RM)   -rf $(BUILD) _tr* */__pycache__
	cd plot && $(MAKE) clean
	cd presi && $(MAKE) clean
