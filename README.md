#   abr-sim

2025-05-27  Markku-Juhani O. Saarinen

Pre-silicon trace generator for the
[Adam's Bridge](https://github.com/chipsalliance/adams-bridge)
PQC (Dilithium) hardware accelerator from Caliptra 2.0 / Chips Alliance.
Generates a "toggle trace" for the 40000-cycle signing ML-DSA-87 signing
operation in about 1 minute.

Dilithium is another name for
[FIPS 204 ML-DSA](https://doi.org/10.6028/NIST.FIPS.204)
(The Module-Lattice-Based Digital Signature Standard.)

What's here:
```
abr-sim
├── Makefile      # Main makefile
├── adams-bridge  # Submodule: Adam's Bridge RTL repo (v1.0.1)
├── doc           # misc documentation; hardwear.io presentation
├── flow          # Python and shell scripts
├── plot          # Gnuplot scripts for TVLA traces
├── rtl           # Hook for printing "line number" of execution
├── src           # Verilator C and python
└── README.md     # this file
```

### Building

As a prerequisite, you will need standard C toolchains and
[verilator](https://github.com/verilator/verilator)
(Tested using the dev version 5.037.)

The python3 parts use pycryptodome; you may have to install it
(`pip3 install pycryptodome`).

Fetch the repo. Note that the `adams-bridge' submodule directory needs to point
to a correct release, currently v1.0.1 (May 16, 2025):
```
$ git clone --recurse-submodules https://github.com/ml-dsa/abr-sim.git
...
Submodule path 'adams-bridge': checked out '1cad0334eebf66173f80500f4fc0b628f7cf3335'
```

You're ready to build the main binaries, `readvcd` and `mldsa_wrap` using
the Makefile. Verilator build will take a minute or two.
```
$ make
gcc -O2 -Wall -Wextra -o readvcd src/readvcd.c
mkdir -p _build
(..)
g++  mldsa_wrap.o verilated.o verilated_vcd_c.o verilated_threads.o Vmldsa_wrap__ALL.a    -pthread -lpthread -latomic   -o Vmldsa_wrap
rm Vmldsa_wrap__ALL.verilator_deplist.tmp
make[1]: Leaving directory '/home/mjos/src/abr-sim/_build'
cp -p _build/Vmldsa_wrap mldsa_wrap
```

##  mldsa_wrap

The executable `mldsa_wrap` provides full RTL simulation of Adam's Bridge,
together with a wrapper that allows one to load/store files from command line.
```
$ ./mldsa_wrap
USAGE: mldsa_wrap [options] [operation]

Operation is one of: keygen, sign, verify, kgsign

Options (with default values):
    -t      <n>     timeout in cycles (none)
    -vcd    <fn>    vcd output file (trace.vcd)
    -pk     <fn>    public/verification key (pk_in.dat, pk_out.dat)
    -sk     <fn>    private/signing key (sk_in.dat, sk_out.dat)
    -sig    <fn>    signature (sig_in.dat, sig_out.dat)
    -hash   <fn>    message hash (hash_in.dat)
    -seed   <fn>    key generation seed (seed_in.dat)
    -rnd    <fn>    signing rnd input (rnd_in.dat)
    -ent    <fn>    signing sca entropy input (ent_in.dat)
    -vfy    <fn>    verify result output block (none)
```

#### Example: mldsa_wrap

For example, we may start the keygen + signing operation without any
parameters (in this case, the code will assume that the keygen seed is zero, and the message hash being signed is also zero:
```
./mldsa_wrap kgsign
[INIT]  1
[STAT]  4   fsm= 1  status= 1 <READY>
[XFER]  14  fsm= 2
[INFO]  name + ver: 00000000000000000000000000000000
[INIT]  kgsign
seed_in.dat: No such file or directory
hash_in.dat: No such file or directory
rnd_in.dat: No such file or directory
ent_in.dat: No such file or directory
[XFER]  34  fsm= 402
[XFER]  68  fsm= 403
[XFER]  86  fsm= 404
[XFER]  120 fsm= 405
[KGSG]  121 start
#     122 [prim]    2: MLDSA_KG_S +  0
#     122 [sec ]    0: MLDSA_RESET +  0
[STAT]  124 fsm= 406    status= 0
#     135 [prim]    3: MLDSA_KG_S +  1
#     167 [prim]    4: MLDSA_KG_S +  2
...
#  124152 [prim]  207: MLDSA_SIGN_E +  0
#  124153 [prim]    0: MLDSA_RESET +  0
#  124153 [sec ]    0: MLDSA_RESET +  0
[STAT]  124156  fsm= 406    status= 3 <READY> <VALID>
[KGSG]  124157  done
[XFER]  127629  fsm= 407
[SAVE]  sig_out.dat (wrote 4627 bytes)
[EXIT]  127630
```
As can be seen, there are default filenames for most inputs and outputs,
but they can also be specified from the command line. The wrapper
implements a finite state machine that transfers ("XFER") data in/out
from files to the RTL model.
Our modified RTL has a "hook" to display the finite state machine state
(e.g. `MLDSA_SIGN_E`). After 127,630 cycles (in this case, three signing "rounds") the model finished creating the signature, and the C wrapper wrote the resulting signature into the file `sig_out.dat`.

##  mldsa-gen.py

The Python program `flow/mldsa-gen.py` uses a full FIPS 204 ML-DSA implementation (in `flow/fips204.py`) to generate test cases and verify the correctness of the operation of the model. The code requires hash functions from cryptodome; `pip3 install cryptodome`.

The command line arguments are:
```
python3 flow/mldsa-gen.py <message> <xi> <rho'>
```
All parameters are optional:

*   `<message>`: Message to be signed (hashed with SHA512 even though
    "PURE" ML-DSA is being used, as per CNSA 2.0 practices). This is
    written out into 64-byte `hash_in.dat`.
*   `<xi>`: This is the 32-byte key seed for ML-DSA keypair generation
    ( Algorithm 6: ML-DSA.KeyGen_internal() in FIPS 204. ), specified in HEX.
*   `<rho'>`: If present, this 64-byte hex value sets the secret key (s1,s2)
    seed "rho prime" directly, overriding seed expansion on line 1 of
    ML-DSA.KeyGen_internal(). This allows fix-random TVLA testing.

The Python program will create the following files, which can be
directly used as inputs for `mldsa_wrap`:

*   `hash_in.dat`: 64-byte message hash to be signed.
*   `pk_in.dat`: 2592-byte ML-DSA-87 public (verification) key.
*   `sk_in.dat`: 4896-byte ML-DSA-87 secret (signing) key.
*   `rnd_in.dat`: 32-byte rnd input parameter for ML-DSA-87 signing.
    It can be all zeros, signifying deterministic signing.

Additionally, the program creates 4627-byte "expected" signature `sig_in.dat`,
which is not read by `mldsa_wrap`, but can be used to compare to `sig_out.dat`
created by the RTL model. They should match exactly if the DUT is operating
correctly.

#### Example: mldsa-gen.py

```
$ python3 flow/mldsa-gen.py 3
# msg: 33
# kg_seed: 0000000000000000000000000000000000000000000000000000000000000000
# kappa 0
# verify True
$ ls *.dat
hash_in.dat  pk_in.dat  rnd_in.dat  seed_in.dat  sig_in.dat  sk_in.dat
```
Here the message `3` was chosen so that signature is found on the first
iteartion (counter `kappa 0`).

Given that all default filenames are used, we may generate a VCD trace
`trace.vcd` with these inputs:
```
./mldsa_wrap -vcd trace.vcd sign
[INIT]  1
[STAT]  4   fsm= 1  status= 1 <READY>
[XFER]  14  fsm= 2
[INFO]  name + ver: 00000000000000000000000000000000
[INIT]  sign
[LOAD]  hash_in.dat (read 64 bytes)
[LOAD]  sk_in.dat (read 4896 bytes)
[LOAD]  rnd_in.dat (read 32 bytes)
...
[STAT]  2556    fsm= 206    status= 0
```
There is an additional input, `ent_in.dat` which can provide randomizing
entropy for the LFSR masking random number generators; we will ignore
it in this example.

We see that the actual operation starts after data transfers at cycle 2556 and finishes cycle 39192; requiring 36636 cycles in this case (transfer cycles are not counted).
```
#   39190 [sec ]    0: MLDSA_RESET +  0
[STAT]  39192   fsm= 206    status= 3 <READY> <VALID>
[SIGN]  39193   done
[XFER]  42665   fsm= 207
[SAVE]  sig_out.dat (wrote 4627 bytes)
[EXIT]  42666
```
We observe that the signature written out by the model matches the
one created by the Python implementation:
```
$ cmp sig_out.dat sig_in.dat
```

Also a 24 GB (!!) tracefile is generated:
```
$ ls -l trace.vcd
-rw-rw-r-- 1 mjos mjos 25759748411 May 27 19:09 trace.vcd
```

##  readvcd

**readvcd** is a C program that parses the ascii VCD file and counts
the total number of signal toggles at each time step:
```
$ ./readvcd trace.vcd
Usage: readvcd <file.vcd> <time signal> [threshold] [report cycles]
```
Two first arguments are needed; in addition to the VCD file, the "time signal" is some cycle counter contained in the design itself; partial string
matching is used to find it.

We can create a 900 kB `toggle.txt` output from the `trace.vcd` file created
in the previous example. The cycle-counting timing signal `dec_prim.cyc` from our
instrumentation in `rtl/mldsa_seq_decode.sv` is used:


#### Example: readvcd
```
$./readvcd trace.vcd dec_prim.cyc | tee toggle.txt
[info] toggle threshold: 1
trace.vcd preamble: 286124 lines, 165733 signames, 100160 ids, max var 135168, tot 2595037 bits.
[info] timing signal: TOP.mldsa_wrap.top0.mldsa_ctrl_inst.mldsa_seq_prim_inst.dec_prim.cyc[25:0]
#       0 [togd]  16
#       1 [togd]  8561
#       2 [togd]  28240
#       3 [togd]  1721
#       4 [togd]  19954
#    2553 [togd]  138185
#    2554 [togd]  213
...
#   39190 [togd]  29942
#   39191 [togd]  2456
trace.vcd total: 646608046 lines, last time 391930  cycle 39192.
```
Here each `# c [togd] n` line simply signfies that there were n toggles at
cycle interval c.


##  Further processing

The rough scripts in flow directory
(`gen-fix.sh`, `gen-kgr.sh`, `gen-rnd.sh`, `gen-sum.sh`) can be used
to set up parallel trace acquisition in a Linux system, and collecting
of the data in appropriate forum.

The toggle files can be further processed into TVLA data with
`flow/tvla.py`. An example of output of a total of 11,042 fixed+random
signing traces can be found in `plot/tvla11k.txt`. You can display the
results with `grep '# f:' plot/tvla11k.txt | less`. A line of tvla script output can be interpreted as follows:
```
 2557   77.4110 # f:( 5519,    220.0,     0.00) r:( 5523,    217.8,     2.11) [t]
   ^       ^           ^         ^         ^         ^        ^         ^
   |       |           |         |         |         |        |         |
 cycle   t-value     fix.n     fix.avg  fix.std   rand.n   rand.avg  rand.std
```
In this case, the t-value is large (77.4) as the fixed traces have zero standard deviation at that early time point (cycle 2557), while the random traces have variation. They are hence easily distinguishable.

The `plot` directory contains a script `plot.sh` that was used to create
the trace and tvla plots in the presentation.


