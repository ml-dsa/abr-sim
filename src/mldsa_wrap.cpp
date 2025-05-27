//  mldsa_wrap.cpp
//  2024-11-26  Markku-Juhani O. Saarinen <mjos@iki.fi>

//  === verilator main for mldsa_wrap

#include <stdio.h>
#include <stdbool.h>
#include <verilated.h>
#include "verilated_vcd_c.h"
#include "Vmldsa_wrap.h"

//#define PRESI_TRACE

//  register map
//  adams-bridge/src/mldsa_top/rtl/mldsa_reg.sv

//  https://github.com/chipsalliance/adams-bridge/blob/main/docs/AdamsBridgeHardwareSpecification.md#api

#define MLDSA_NAME              0x0000
#define MLDSA_VERSION           0x0008
#define MLDSA_CTRL              0x0010
#define MLDSA_STATUS            0x0014
#define MLDSA_ENTROPY           0x0018
#define MLDSA_SEED              0x0058
#define MLDSA_SIGN_RND          0x0078
#define MLDSA_MSG               0x0098
#define MLDSA_VERIFY_RES        0x00d8
#define MLDSA_EXTERNAL_MU       0x0118
#define MLDSA_MSG_STROBE        0x0158
#define MLDSA_CTX_CONFIG        0x015c
#define MLDSA_CTX               0x0160
#define MLDSA_PUBKEY            0x1000
#define MLDSA_SIGNATURE         0x2000
#define MLDSA_PRIVKEY_OUT       0x4000
#define MLDSA_PRIVKEY_IN        0x6000

#define MLDSA_NAME_SZ           0x0010
#define MLDSA_SEED_SZ           0x0020
#define MLDSA_SIGN_RND_SZ       0x0020
#define MLDSA_MSG_SZ            0x0040
#define MLDSA_ENTROPY_SZ        0x0040
#define MLDSA_VERIFY_RES_SZ     0x0040
#define MLDSA_EXTERNAL_MU_SZ    0x0040
#define MLDSA_CTX_SZ            0x0100

#define STATUS_READY    1
#define STATUS_VALID    3

#define CTRL_KEYGEN     1
#define CTRL_SIGN       2
#define CTRL_VERIFY     3
#define CTRL_KG_SIGN    4

//  Table 2 FIPS 204, ML-DSA-87
#define PUBKEY_SZ       2592
#define PRIVKEY_SZ      4896
#define SIGNATURE_SZ    4627

//  adams bridge ahb

void ahb_clear(Vmldsa_wrap* mldsa_wrap)
{
    mldsa_wrap->rst_b   =   1;  //  no reset
    mldsa_wrap->hsel_i  =   0;
    mldsa_wrap->hwrite_i =  0;
    mldsa_wrap->htrans_i =  0;
}

void ahb_write(Vmldsa_wrap* mldsa_wrap, uint32_t addr, uint32_t data)
{
    mldsa_wrap->hsel_i  =   1;
    mldsa_wrap->hwrite_i =  1;
    mldsa_wrap->htrans_i =  2;
    mldsa_wrap->hsize_i =   2;
    mldsa_wrap->haddr_i =   addr;
    mldsa_wrap->hwdata_i =  addr & 4 ? ((uint64_t) data) << 32 : data;
}

void ahb_read(Vmldsa_wrap* mldsa_wrap, uint32_t addr)
{
    mldsa_wrap->hsel_i  =   1;
    mldsa_wrap->hwrite_i =  0;
    mldsa_wrap->htrans_i =  2;
    mldsa_wrap->hsize_i =   2;
    mldsa_wrap->haddr_i =   addr;
}

//  read a file to buffer

size_t read_fn(void *buf, size_t buf_sz, const char *fn)
{
    FILE *fp;
    size_t n;

    memset(buf, 0, buf_sz);
    if (fn == NULL)         //  do not read -- just zeroize the buffer
        return 0;

    fp = fopen(fn, "r");
    if (fp == NULL) {
        perror(fn);
        return 0;
    }
    n = fread(buf, 1, buf_sz, fp);
    printf("[LOAD]\t%s (read %zu bytes)\n", fn, n);
    fclose(fp);

    return n;
}

//  write a file

size_t write_fn(const void *buf, size_t buf_sz, const char *fn)
{
    FILE *fp;
    size_t n;

    if (fn == NULL)         //  not written
        return 0;

    fp = fopen(fn, "w");
    if (fp == NULL) {
        perror(fn);
        return 0;
    }
    n = fwrite(buf, 1, buf_sz, fp);
    printf("[SAVE]\t%s (wrote %zu bytes)\n", fn, n);
    fclose(fp);

    return n;

}

//  dump bytes in hex to stdout

void dump_hex(const void *buf, size_t buf_sz)
{
    size_t i;

    for (i = 0; i < buf_sz; i++) {
        printf("%02x", ((const uint8_t *) buf)[i]);
    }
    printf("\n");
}

const char usage[] =
    "USAGE: mldsa_wrap [options] [operation]\n\n"
    "Operation is one of: keygen, sign, verify, kgsign\n\n"
    "Options (with default values):\n"
    "\t-t\t<n>\ttimeout in cycles (none)\n"
    "\t-vcd\t<fn>\tvcd output file (trace.vcd)\n"
    "\t-pk\t<fn>\tpublic/verification key (pk_in.dat, pk_out.dat)\n"
    "\t-sk\t<fn>\tprivate/signing key (sk_in.dat, sk_out.dat)\n"
    "\t-sig\t<fn>\tsignature (sig_in.dat, sig_out.dat)\n"
    "\t-hash\t<fn>\tmessage hash (hash_in.dat)\n"
    "\t-seed\t<fn>\tkey generation seed (seed_in.dat)\n"
    "\t-rnd\t<fn>\tsigning rnd input (rnd_in.dat)\n"
    "\t-ent\t<fn>\tsigning sca entropy input (ent_in.dat)\n"
    "\t-vfy\t<fn>\tverify result output block (none)\n";

//  how many 32-bit words needed for x bytes
#define SZ_U32(x)  (((x) + 3) / 4)

int main(int argc, char **argv)
{
    //  default file names
    const char  *vcd_out_fn     = NULL; //  "trace.vcd";
    const char  *seed_in_fn     = "seed_in.dat";
    const char  *hash_in_fn     = "hash_in.dat";
    const char  *ent_in_fn      = "ent_in.dat";
    const char  *sk_in_fn       = "sk_in.dat";
    const char  *sk_out_fn      = "sk_out.dat";
    const char  *pk_in_fn       = "pk_in.dat";
    const char  *pk_out_fn      = "pk_out.dat";
    const char  *rnd_in_fn      = "rnd_in.dat";
    const char  *sig_in_fn      = "sig_in.dat";
    const char  *sig_out_fn     = "sig_out.dat";
    const char  *vfy_out_fn     = NULL; //  "vfy_out.dat";

    //  buffers
    uint32_t    pk_in[      SZ_U32( PUBKEY_SZ )         ] = { 0 };
    uint32_t    pk_out[     SZ_U32( PUBKEY_SZ )         ] = { 0 };
    uint32_t    sk_in[      SZ_U32( PRIVKEY_SZ )        ] = { 0 };
    uint32_t    sk_out[     SZ_U32( PRIVKEY_SZ )        ] = { 0 };
    uint32_t    rnd_in[     SZ_U32( MLDSA_SIGN_RND_SZ ) ] = { 0 };
    uint32_t    hash_in[    SZ_U32( MLDSA_MSG_SZ )      ] = { 0 };
    uint32_t    ent_in[     SZ_U32( MLDSA_ENTROPY_SZ )  ] = { 0 };
    uint32_t    seed_in[    SZ_U32( MLDSA_SEED_SZ )     ] = { 0 };
    uint32_t    sig_in[     SZ_U32( SIGNATURE_SZ )      ] = { 0 };
    uint32_t    sig_out[    SZ_U32( SIGNATURE_SZ )      ] = { 0 };
    uint32_t    vfy_out[    SZ_U32( MLDSA_VERIFY_RES_SZ)] = { 0 };
    uint32_t    name_out[   SZ_U32( MLDSA_NAME_SZ )     ] = { 0 };

    int64_t     max_cycle   = 0;
    bool        get_cycle   = false;
    int         main_op     = -1;
    int         i;

    if (argc < 2) {
        puts(usage);
        return 1;
    }
    i = 1;
    while (i < argc) {

        //  options require 1 parameter
        if (i + 1 < argc && strcmp(argv[i], "-t") == 0) {
            max_cycle = strtoll(argv[i + 1], NULL, 0);
            i += 2;
            continue;

        } else if (i + 1 < argc && strcmp(argv[i], "-vcd") == 0) {
            vcd_out_fn = argv[i + 1];
            i += 2;
            continue;

        } else if (i + 1 < argc && strcmp(argv[i], "-pk") == 0) {
            pk_in_fn = pk_out_fn = argv[i + 1];
            i += 2;
            continue;

        } else if (i + 1 < argc && strcmp(argv[i], "-sk") == 0) {
            sk_in_fn = sk_out_fn = argv[i + 1];
            i += 2;
            continue;

        } else if (i + 1 < argc && strcmp(argv[i], "-sig") == 0) {
            sig_in_fn = sig_out_fn = argv[i + 1];
            i += 2;
            continue;

        } else if (i + 1 < argc && strcmp(argv[i], "-seed") == 0) {
            seed_in_fn = argv[i + 1];
            i += 2;
            continue;

        } else if (i + 1 < argc && strcmp(argv[i], "-rnd") == 0) {
            rnd_in_fn = argv[i + 1];
            i += 2;
            continue;

        } else if (i + 1 < argc && strcmp(argv[i], "-ent") == 0) {
            ent_in_fn = argv[i + 1];
            i += 2;
            continue;

        } else if (i + 1 < argc && strcmp(argv[i], "-vfy") == 0) {
            vfy_out_fn = argv[i + 1];
            i += 2;
            continue;

        } else if (i + 1 < argc && strcmp(argv[i], "-hash") == 0) {
            hash_in_fn = argv[i + 1];
            i += 2;
            continue;

        //  operations have no parameters
        } else if (strcmp(argv[i], "keygen") == 0) {
            main_op   = 100;
            i++;
            continue;

        } else if (strcmp(argv[i], "sign") == 0) {
            main_op   = 200;
            i++;
            continue;

        } else if (strcmp(argv[i], "verify") == 0) {
            main_op   = 300;
            i++;
            continue;

        } else if (strcmp(argv[i], "kgsign") == 0) {
            main_op   = 400;
            i++;
            continue;

        } else if ( strcmp(argv[i], "-h") == 0 ||
                    strcmp(argv[i], "--help") == 0) {
            puts(usage);
            return 0;

        } else {
            fprintf(stderr, "%s: invalid flag or missing parameter: %s\n",
                            argv[0], argv[i]);
            return 2;
        }
    }

    //  === test fsm

    //  status
    uint64_t    hclk        = 0;
    int64_t     cycle       = 0;
    int         status      = 0;
    uint32_t    x;

    //  testing fsm
    int         main_fsm    = 0;
    int         wait_ready  = 0;
    int         prev_status = -1;
    bool        dump_trace  = true;     //  for no trace during xfer

    //  for the data transfer fsm
    int         xfer_fsm    = 0;
    bool        xfer_write  = false;
    uint32_t    xfer_addr   = 0;
    uint32_t    xfer_stop   = 0;
    uint32_t    *xfer_data  = NULL;

    //  trace on
    Vmldsa_wrap* mldsa_wrap = new Vmldsa_wrap;
    VerilatedVcdC* tfp = NULL;

    if (vcd_out_fn != NULL) {
        Verilated::traceEverOn(true);
        tfp = new VerilatedVcdC;
        mldsa_wrap->trace(tfp, 99);
        tfp->open(vcd_out_fn);
    }

    ahb_clear(mldsa_wrap);

    while (main_fsm >= 0 && !Verilated::gotFinish()) {
        hclk++;
        mldsa_wrap->clk = !mldsa_wrap->clk;

        //  Evaluate model
        mldsa_wrap->eval();
        if  (vcd_out_fn != NULL && dump_trace) {
            tfp->dump(5 * hclk);
        }
        if (mldsa_wrap->clk)
            continue;

        cycle++;
        if (max_cycle > 0 && cycle > max_cycle)
            break;

        //  progress..
        if ((cycle % 100) == 0) {
            printf(" %ld \r", cycle);
            fflush(stdout);
        }

        //  "ahb" data transfer fsm
        mldsa_wrap->hready_i = mldsa_wrap->hreadyout_o;

        if (xfer_fsm) {
            switch (xfer_fsm) {
                case 1:
                    if (xfer_addr < xfer_stop) {
                        if (xfer_write) {
                            if (xfer_data == NULL)
                                x = 0;
                            else
                                x = *xfer_data;
                            ahb_write(mldsa_wrap, xfer_addr, x);
                        } else {
                            ahb_read(mldsa_wrap, xfer_addr);
                        }
                        xfer_fsm = 2;
                    } else {
                        xfer_fsm = 0;
                        printf("[XFER]\t%ld\tfsm= %d\n",
                            cycle, main_fsm);
                    }
                    break;

                case 2:
                    ahb_clear(mldsa_wrap);
                    if (mldsa_wrap->hreadyout_o) {
                        if (!xfer_write) {
                            x = (uint32_t) ((mldsa_wrap->hrdata_o) >>
                                            (xfer_addr & 4 ? 32 : 0));
                            *xfer_data = x;
                            //printf("%04x:%08x\n", xfer_addr, x);
                        }
                        xfer_addr += 4;
                        xfer_data++;
                        xfer_fsm = 1;
                    }
                    break;
            }
            continue;
        }

        //  always clear
        ahb_clear(mldsa_wrap);

        //  wait until done
        if (wait_ready == 1) {
            prev_status = -1;
            wait_ready = 2;
            continue;
        } else if (wait_ready == 2) {
            ahb_read(mldsa_wrap, MLDSA_STATUS);
            wait_ready = 3;
            continue;
        } else if (wait_ready == 3) {
            status  =   mldsa_wrap->hrdata_o >> 32;
            if (status != prev_status) {
                printf("[STAT]\t%ld\tfsm= %d\tstatus= %d%s%s\n",
                        cycle, main_fsm, status,
                        status & 1 ? " <READY>" : "",
                        status & 2 ? " <VALID>" : "");
                prev_status = status;
            }

            if ((status & 1) != 0) {
                wait_ready = 0;
            } else {
                wait_ready = 2;
            }
            continue;
        }

        //  general fsm steps
        switch(main_fsm) {

            case 0:         //  reset
                printf("[INIT]\t%ld\n", cycle);
                mldsa_wrap->rst_b   =   0;  //  reset
                mldsa_wrap->hsize_i =   3;
                mldsa_wrap->haddr_i =   0;
                mldsa_wrap->hwdata_i =  0;
                wait_ready  = 1;
                main_fsm++;
                break;

            case 1:         //  read name and version
                dump_trace  = false;
                xfer_write  = false;
                xfer_addr   = MLDSA_NAME;
                xfer_stop   = MLDSA_NAME + MLDSA_NAME_SZ;
                xfer_data   = name_out;
                xfer_fsm    = 1;
                main_fsm++;
                break;

            case 2:         //  print the type
                printf("[INFO]\tname + ver: ");
                dump_hex(name_out, MLDSA_NAME_SZ);
                main_fsm    =   main_op;
                break;

            //  === keygen

            case 100:
                printf("[INIT]\tkeygen\n");

                //  key generation seed
                read_fn(seed_in, sizeof(seed_in), seed_in_fn);

                //  masking entropy
                read_fn(ent_in, sizeof(ent_in), ent_in_fn);

                main_fsm++;
                break;

            case 101:       //  keygen: write seed to device
                xfer_write  = true;
                xfer_addr   = MLDSA_SEED;
                xfer_stop   = MLDSA_SEED + MLDSA_SEED_SZ;
                xfer_data   = seed_in;
                xfer_fsm    = 1;
                main_fsm++;
                break;

            case 102:       //  keygen: write entropy to device
                xfer_write  = true;
                xfer_addr   = MLDSA_ENTROPY;
                xfer_stop   = MLDSA_ENTROPY + MLDSA_ENTROPY_SZ;
                xfer_data   = ent_in;
                xfer_fsm    = 1;
                main_fsm++;
                break;

            case 103:       //  keygen: start oprations
                printf("[KGEN]\t%ld\tstart\n", cycle);
                ahb_write(mldsa_wrap, MLDSA_CTRL, CTRL_KEYGEN);
                wait_ready  = 1;
                main_fsm++;
                break;

            case 104:       //  keygen: read secret key from iut
                printf("[KGEN]\t%ld\tdone\n", cycle);
                xfer_write  = false;
                xfer_addr   = MLDSA_PRIVKEY_OUT;
                xfer_stop   = MLDSA_PRIVKEY_OUT + PRIVKEY_SZ;
                xfer_data   = sk_out;
                xfer_fsm    = 1;
                main_fsm++;
                break;

            case 105:       //  keygen: save secret key
                write_fn(sk_out, PRIVKEY_SZ, sk_out_fn);
                main_fsm++;
                break;

            case 106:       //  keygen: read public key from iut
                xfer_write  = false;
                xfer_addr   = MLDSA_PUBKEY;
                xfer_stop   = MLDSA_PUBKEY + PUBKEY_SZ;
                xfer_data   = pk_out;
                xfer_fsm    = 1;
                main_fsm++;
                break;

            case 107:       //  keygen: save public key
                write_fn(pk_out, PUBKEY_SZ, pk_out_fn);
                main_fsm    = -1;   //  done
                break;

            //  === sign

            case 200:
                printf("[INIT]\tsign\n");

                //  message hash
                read_fn(hash_in, sizeof(hash_in), hash_in_fn);

                //  secret key
                read_fn(sk_in, sizeof(sk_in), sk_in_fn);

                //  signing randomness
                read_fn(rnd_in, sizeof(rnd_in), rnd_in_fn);

                //  masking entropy
                read_fn(ent_in, sizeof(ent_in), ent_in_fn);

                main_fsm++;
                break;

            case 201:       //  sign: write msg (hash) to device
                xfer_write  = true;
                xfer_addr   = MLDSA_MSG;
                xfer_stop   = MLDSA_MSG + MLDSA_MSG_SZ;
                xfer_data   = hash_in;
                xfer_fsm    = 1;
                main_fsm++;
                break;

            case 202:       //  sign: write secret key to device
                xfer_write  = true;
                xfer_addr   = MLDSA_PRIVKEY_IN;
                xfer_stop   = MLDSA_PRIVKEY_IN + PRIVKEY_SZ;
                xfer_data   = sk_in;
                xfer_fsm    = 1;
                main_fsm++;
                break;

            case 203:       //  sign: write rnd to device
                xfer_write  = true;
                xfer_addr   = MLDSA_SIGN_RND;
                xfer_stop   = MLDSA_SIGN_RND + MLDSA_SIGN_RND_SZ;
                xfer_data   = rnd_in;
                xfer_fsm    = 1;
                main_fsm++;
                break;

            case 204:       //  sign: write entropy to device
                xfer_write  = true;
                xfer_addr   = MLDSA_ENTROPY;
                xfer_stop   = MLDSA_ENTROPY + MLDSA_ENTROPY_SZ;
                xfer_data   = ent_in;
                xfer_fsm    = 1;
                main_fsm++;
                break;

            case 205:       //  sign: start signing operation
                printf("[SIGN]\t%ld\tstart\n", cycle);
                dump_trace  = true;
                ahb_write(mldsa_wrap, MLDSA_CTRL, CTRL_SIGN);
                wait_ready  = 1;
                main_fsm++;
                break;

            case 206:       //  sign: read signature from device
                printf("[SIGN]\t%ld\tdone\n", cycle);
                dump_trace  = false;
                xfer_write  = false;
                xfer_addr   = MLDSA_SIGNATURE;
                xfer_stop   = MLDSA_SIGNATURE + SIGNATURE_SZ;
                xfer_data   = sig_out;
                xfer_fsm    = 1;
                main_fsm++;
                break;

            case 207:       //  sign: save signature
                write_fn(sig_out, SIGNATURE_SZ, sig_out_fn);
                main_fsm    = -1;   //  done
                break;

            //  === verify

            case 300:
                printf("[INIT]\tverify\n");

                //  message hash
                read_fn(hash_in, sizeof(hash_in), hash_in_fn);

                //  public key
                read_fn(pk_in, sizeof(pk_in), pk_in_fn);

                //  signature
                read_fn(sig_in, sizeof(sig_in), sig_in_fn);

                main_fsm++;
                break;

            case 301:       //  verify: write msg to device
                xfer_write  = true;
                xfer_addr   = MLDSA_MSG;
                xfer_stop   = MLDSA_MSG + MLDSA_MSG_SZ;
                xfer_data   = hash_in;
                xfer_fsm    = 1;
                main_fsm++;
                break;

            case 302:       //  verify: write public key to device
                xfer_write  = true;
                xfer_addr   = MLDSA_PUBKEY;
                xfer_stop   = MLDSA_PUBKEY + PUBKEY_SZ;
                xfer_data   = pk_in;
                xfer_fsm    = 1;
                main_fsm++;
                break;

            case 303:       //  verify: write signature to device
                xfer_write  = true;
                xfer_addr   = MLDSA_SIGNATURE;
                xfer_stop   = MLDSA_SIGNATURE + SIGNATURE_SZ;
                xfer_data   = sig_in;
                xfer_fsm    = 1;
                main_fsm++;
                break;

            case 304:       //  verify: start verification operation
                printf("[VRFY]\t%ld\tstart\n", cycle);
                ahb_write(mldsa_wrap, MLDSA_CTRL, CTRL_VERIFY);
                wait_ready  = 1;
                main_fsm++;
                break;

            case 305:       //  verify: read verify data from device
                printf("[VRFY]\t%ld\tdone\n", cycle);
                xfer_write  = false;
                xfer_addr   = MLDSA_VERIFY_RES;
                xfer_stop   = MLDSA_VERIFY_RES + MLDSA_VERIFY_RES_SZ;
                xfer_data   = vfy_out;
                xfer_fsm    = 1;
                main_fsm++;
                break;

            case 306:       //  verify: save verification result
                write_fn(vfy_out, MLDSA_VERIFY_RES_SZ, vfy_out_fn);
                if (memcmp(vfy_out, sig_in, MLDSA_VERIFY_RES_SZ) == 0) {
                    printf("[INFO]\tSignature verify OK\n");
                } else {
                    printf("[INFO]\tSignature verify BAD\n");
                }
                main_fsm    = -1;   //  done
                break;

            //  === kg + sign

            case 400:
                printf("[INIT]\tkgsign\n");

                //  key generation seed
                read_fn(seed_in, sizeof(seed_in), seed_in_fn);

                //  message hash
                read_fn(hash_in, sizeof(hash_in), hash_in_fn);

                //  signing randomness
                read_fn(rnd_in, sizeof(rnd_in), rnd_in_fn);

                //  masking entropy
                read_fn(ent_in, sizeof(ent_in), ent_in_fn);

                main_fsm++;
                break;

            case 401:       //  kgseed: write seed to device
                xfer_write  = true;
                xfer_addr   = MLDSA_SEED;
                xfer_stop   = MLDSA_SEED + MLDSA_SEED_SZ;
                xfer_data   = seed_in;
                xfer_fsm    = 1;
                main_fsm++;
                break;

            case 402:       //  kgsign: write msg (hash) to device
                xfer_write  = true;
                xfer_addr   = MLDSA_MSG;
                xfer_stop   = MLDSA_MSG + MLDSA_MSG_SZ;
                xfer_data   = hash_in;
                xfer_fsm    = 1;
                main_fsm++;
                break;

            case 403:       //  kgsign: write rnd to device
                xfer_write  = true;
                xfer_addr   = MLDSA_SIGN_RND;
                xfer_stop   = MLDSA_SIGN_RND + MLDSA_SIGN_RND_SZ;
                xfer_data   = rnd_in;
                xfer_fsm    = 1;
                main_fsm++;
                break;

            case 404:       //  kgsign: write entropy to device
                xfer_write  = true;
                xfer_addr   = MLDSA_ENTROPY;
                xfer_stop   = MLDSA_ENTROPY + MLDSA_ENTROPY_SZ;
                xfer_data   = ent_in;
                xfer_fsm    = 1;
                main_fsm++;
                break;

            case 405:       //  kgsign: start signing operation
                printf("[KGSG]\t%ld\tstart\n", cycle);
                dump_trace  = true;
                ahb_write(mldsa_wrap, MLDSA_CTRL, CTRL_KG_SIGN);
                wait_ready  = 1;
                main_fsm++;
                break;

            case 406:       //  kgsign: read signature from device
                printf("[KGSG]\t%ld\tdone\n", cycle);
                dump_trace  = false;
                xfer_write  = false;
                xfer_addr   = MLDSA_SIGNATURE;
                xfer_stop   = MLDSA_SIGNATURE + SIGNATURE_SZ;
                xfer_data   = sig_out;
                xfer_fsm    = 1;
                main_fsm++;
                break;

            case 407:       //  kgsign: save signature
                write_fn(sig_out, SIGNATURE_SZ, sig_out_fn);
                main_fsm    = -1;   //  done
                break;

            //  === invalid operand

            default:
                printf("[INFO]\tInvalid state %d.\n", main_fsm);
                main_fsm    = -1;   //  done
                break;
        }
    }
    printf("[EXIT]\t%ld\n", cycle);

    //  Final model cleanup
    mldsa_wrap->final();

    if (tfp != NULL) {
        tfp->close();
    }

    //  Destroy model
    delete mldsa_wrap;

    return 0;
}
