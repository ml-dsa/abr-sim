#	Makefile
#	2024-11-27	Markku-Juhani O. Saarinen <mjos@iki.fi>.  See LICENSE.

#	build directory for verilator
ABR_SRC		=	adams-bridge
BUILD		=	_build

#	separate binaries
READVCD		=	readvcd
MLDSA_WRAP	=	mldsa_wrap

#	
VERILATOR	=	verilator

VFLAGS	=	-Wno-WIDTH -Wno-UNOPTFLAT -Wno-LITENDIAN -Wno-CMPCONST \
			-Wno-MULTIDRIVEN -Wno-UNPACKED \
			--timescale 1ns/100ps
VFLAGS	+=	--trace -CFLAGS "-DPRESI_TRACE"

RTLDEP	=	rtl/mldsa_seq_prim.sv rtl/mldsa_seq_sec.sv \
			$(wildcard $(ABR_SRC)/*/rtl/*.sv)
			
all:	$(READVCD) $(MLDSA_WRAP)

#	verilator

$(MLDSA_WRAP):	$(BUILD)/Vmldsa_wrap
	cp -p $(BUILD)/Vmldsa_wrap $(MLDSA_WRAP)

$(BUILD)/Vmldsa_wrap: $(BUILD)/Vmldsa_wrap.mk src/mldsa_wrap.cpp
	$(MAKE) -C $(BUILD) -f Vmldsa_wrap.mk CC=gcc LDFLAGS=""

$(BUILD)/Vmldsa_wrap.mk: $(BUILD) $(RTLDEP) src/mldsa_wrap.cpp
	$(VERILATOR) $(VFLAGS) -Mdir $(BUILD) -cc --exe \
		--top-module mldsa_wrap -f flow/xabr_wrap.vf src/mldsa_wrap.cpp

#	patch to create progress info

rtl/mldsa_seq_prim.sv:	adams-bridge/src/mldsa_top/rtl/mldsa_seq_prim.sv
	cp $< $@
	patch -p0 $@ < $@.patch
	
rtl/mldsa_seq_sec.sv:	adams-bridge/src/mldsa_top/rtl/mldsa_seq_sec.sv
	cp $< $@
	patch -p0 $@ < $@.patch
	
#	separate binaries

$(READVCD):	src/readvcd.c
	gcc -O2 -Wall -Wextra -o $@ $<

$(BUILD):
	mkdir -p $(BUILD)

#       cleanup

clean:
	$(RM)   -f	$(READVCD) $(MLDSA_WRAP) *.vcd *.dat
	$(RM)   -rf $(BUILD) _tr* */__pycache__
	cd plot && $(MAKE) clean
