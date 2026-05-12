//  abr_wrap.cpp
//  2024-11-26  Markku-Juhani O. Saarinen <mjos@iki.fi>.  See LICENSE.

//  === verilator main for abr_wrap

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <vector>
#include <verilated.h>
#include "verilated_vcd_c.h"
#include "Vabr_wrap.h"

//  Adams Bridge v2.0.3 AHB register map.

#define ABR_ENTROPY             0x0018

#define MLDSA_NAME              0x0000
#define MLDSA_VERSION           0x0008
#define MLDSA_CTRL              0x0010
#define MLDSA_STATUS            0x0014
#define MLDSA_SEED              0x0058
#define MLDSA_SIGN_RND          0x0078
#define MLDSA_MSG               0x0098
#define MLDSA_VERIFY_RES        0x00d8
#define MLDSA_EXTERNAL_MU       0x0118
#define MLDSA_MSG_STROBE        0x0158
#define MLDSA_PUBKEY            0x1000
#define MLDSA_SIGNATURE         0x2000
#define MLDSA_PRIVKEY_OUT       0x4000
#define MLDSA_PRIVKEY_IN        0x6000

#define MLKEM_NAME              0x9000
#define MLKEM_VERSION           0x9008
#define MLKEM_CTRL              0x9010
#define MLKEM_STATUS            0x9014
#define MLKEM_SEED_D            0x9018
#define MLKEM_SEED_Z            0x9038
#define MLKEM_SHARED_KEY        0x9058
#define MLKEM_MSG               0x9080
#define MLKEM_DECAPS_KEY        0xa000
#define MLKEM_ENCAPS_KEY        0xb000
#define MLKEM_CIPHERTEXT        0xb800

#define NAME_SZ                 0x0010
#define SEED_SZ                 0x0020
#define ENTROPY_SZ              0x0040
#define MLDSA_SIGN_RND_SZ       0x0020
#define MLDSA_MSG_SZ            0x0040
#define MLDSA_VERIFY_RES_SZ     0x0040
#define MLDSA_EXTERNAL_MU_SZ    0x0040
#define MLKEM_MSG_SZ            0x0020
#define MLKEM_SHARED_KEY_SZ     0x0020

#define STATUS_READY            1
#define STATUS_VALID            2
#define STATUS_MLKEM_ERROR      4
#define STATUS_MLDSA_ERROR      8
#define STATUS_MLDSA_STREAM_RDY 4   /* MLDSA_STATUS[2] (engine-specific use) */

#define CTRL_KEYGEN             1
#define CTRL_SIGN_ENCAPS        2
#define CTRL_VERIFY_DECAPS      3
#define CTRL_KG_COMBO           4
#define CTRL_PCR_SIGN           0x10
#define CTRL_EXTERNAL_MU        0x20
#define CTRL_STREAM_MSG         0x40

//  ML-DSA-87 / ML-KEM-1024 byte sizes.
#define MLDSA_PUBKEY_SZ         2592
#define MLDSA_PRIVKEY_SZ        4896
#define MLDSA_SIGNATURE_SZ      4627
#define MLKEM_EK_SZ             1568
#define MLKEM_DK_SZ             3168
#define MLKEM_CT_SZ             1568

#define SZ_U32(x)               (((x) + 3) / 4)

struct Io {
    const char  *label;
    uint32_t    addr;
    size_t      size;
    uint32_t   *data;
    const char  *fn;
    bool        optional;
};

struct Operation {
    const char          *name;
    const char          *tag;
    uint32_t            ctrl_addr;
    uint32_t            status_addr;
    uint32_t            status_error_mask;
    uint32_t            ctrl;
    std::vector<Io>     inputs;
    std::vector<Io>     outputs;
    bool                verify_result;
    bool                stream_input;
};

static void ahb_clear(Vabr_wrap *dut)
{
    dut->rst_b = 1;
    dut->hsel_i = 0;
    dut->hwrite_i = 0;
    dut->htrans_i = 0;
}

static void ahb_write(Vabr_wrap *dut, uint32_t addr, uint32_t data)
{
    dut->hsel_i = 1;
    dut->hwrite_i = 1;
    dut->htrans_i = 2;
    dut->hsize_i = 2;
    dut->haddr_i = addr;
    dut->hwdata_i = (addr & 4) ? ((uint64_t) data) << 32 : data;
}

static void ahb_read(Vabr_wrap *dut, uint32_t addr)
{
    dut->hsel_i = 1;
    dut->hwrite_i = 0;
    dut->htrans_i = 2;
    dut->hsize_i = 2;
    dut->haddr_i = addr;
}

static size_t read_fn(void *buf, size_t buf_sz, const char *fn, bool optional)
{
    memset(buf, 0, buf_sz);
    if (fn == NULL)
        return 0;

    FILE *fp = fopen(fn, "rb");
    if (fp == NULL) {
        if (optional)
            return 0;
        perror(fn);
        exit(1);
    }

    size_t n = fread(buf, 1, buf_sz, fp);
    printf("[LOAD]\t%s (read %zu bytes)\n", fn, n);
    fclose(fp);
    return n;
}

static size_t write_fn(const void *buf, size_t buf_sz, const char *fn)
{
    if (fn == NULL)
        return 0;

    FILE *fp = fopen(fn, "wb");
    if (fp == NULL) {
        perror(fn);
        exit(1);
    }

    size_t n = fwrite(buf, 1, buf_sz, fp);
    printf("[SAVE]\t%s (wrote %zu bytes)\n", fn, n);
    fclose(fp);
    return n;
}

static void dump_hex(const void *buf, size_t buf_sz)
{
    for (size_t i = 0; i < buf_sz; i++)
        printf("%02x", ((const uint8_t *) buf)[i]);
    printf("\n");
}

static uint32_t read_lane(Vabr_wrap *dut, uint32_t addr)
{
    return (uint32_t) (dut->hrdata_o >> ((addr & 4) ? 32 : 0));
}

static const char usage[] =
    "USAGE: abr_wrap [options] [operation]\n\n"
    "Operation is one of:\n"
    "\tmldsa-keygen, mldsa-sign, mldsa-verify, mldsa-kgsign\n"
    "\tmldsa-sign-extmu (external-mu sign: caller supplies precomputed mu)\n"
    "\tmldsa-sign-stream (stream-msg sign: byte-strobed message stream)\n"
    "\tmlkem-keygen, mlkem-encaps, mlkem-decaps, mlkem-kgdecaps\n"
    "\tkeygen, sign, verify, kgsign are aliases for the mldsa-* operations\n\n"
    "Options (with default values):\n"
    "\t-t\t<n>\ttimeout in cycles (none)\n"
    "\t-vcd\t<fn>\tvcd output file (none)\n"
    "\t-pk\t<fn>\tML-DSA public key (pk_in.dat, pk_out.dat)\n"
    "\t-sk\t<fn>\tML-DSA private key (sk_in.dat, sk_out.dat)\n"
    "\t-sig\t<fn>\tML-DSA signature (sig_in.dat, sig_out.dat)\n"
    "\t-hash\t<fn>\tML-DSA message hash (hash_in.dat)\n"
    "\t-seed\t<fn>\tML-DSA key generation seed (seed_in.dat)\n"
    "\t-rnd\t<fn>\tML-DSA signing randomness (rnd_in.dat)\n"
    "\t-ent\t<fn>\tmasking entropy input (ent_in.dat, optional)\n"
    "\t-vfy\t<fn>\tML-DSA verify result output block (none)\n"
    "\t-mu\t<fn>\tML-DSA external mu input (mu_in.dat)\n"
    "\t-strm\t<fn>\tML-DSA stream-msg input bytes (strm_in.dat)\n"
    "\t-d\t<fn>\tML-KEM seed d (seed_d_in.dat)\n"
    "\t-z\t<fn>\tML-KEM seed z (seed_z_in.dat)\n"
    "\t-msg\t<fn>\tML-KEM message/randomness (msg_in.dat)\n"
    "\t-ek\t<fn>\tML-KEM encapsulation key (ek_in.dat, ek_out.dat)\n"
    "\t-dk\t<fn>\tML-KEM decapsulation key (dk_in.dat, dk_out.dat)\n"
    "\t-ct\t<fn>\tML-KEM ciphertext (ct_in.dat, ct_out.dat)\n"
    "\t-ss\t<fn>\tML-KEM shared secret (ss_out.dat)\n";

int main(int argc, char **argv)
{
    const char *vcd_out_fn = NULL;
    const char *seed_in_fn = "seed_in.dat";
    const char *hash_in_fn = "hash_in.dat";
    const char *ent_in_fn = "ent_in.dat";
    const char *sk_in_fn = "sk_in.dat";
    const char *sk_out_fn = "sk_out.dat";
    const char *pk_in_fn = "pk_in.dat";
    const char *pk_out_fn = "pk_out.dat";
    const char *rnd_in_fn = "rnd_in.dat";
    const char *sig_in_fn = "sig_in.dat";
    const char *sig_out_fn = "sig_out.dat";
    const char *vfy_out_fn = NULL;
    const char *mu_in_fn = "mu_in.dat";
    const char *strm_in_fn = "strm_in.dat";

    const char *seed_d_in_fn = "seed_d_in.dat";
    const char *seed_z_in_fn = "seed_z_in.dat";
    const char *msg_in_fn = "msg_in.dat";
    const char *ek_in_fn = "ek_in.dat";
    const char *ek_out_fn = "ek_out.dat";
    const char *dk_in_fn = "dk_in.dat";
    const char *dk_out_fn = "dk_out.dat";
    const char *ct_in_fn = "ct_in.dat";
    const char *ct_out_fn = "ct_out.dat";
    const char *ss_out_fn = "ss_out.dat";

    uint32_t pk_in[SZ_U32(MLDSA_PUBKEY_SZ)] = {0};
    uint32_t pk_out[SZ_U32(MLDSA_PUBKEY_SZ)] = {0};
    uint32_t sk_in[SZ_U32(MLDSA_PRIVKEY_SZ)] = {0};
    uint32_t sk_out[SZ_U32(MLDSA_PRIVKEY_SZ)] = {0};
    uint32_t rnd_in[SZ_U32(MLDSA_SIGN_RND_SZ)] = {0};
    uint32_t hash_in[SZ_U32(MLDSA_MSG_SZ)] = {0};
    uint32_t ent_in[SZ_U32(ENTROPY_SZ)] = {0};
    uint32_t seed_in[SZ_U32(SEED_SZ)] = {0};
    uint32_t sig_in[SZ_U32(MLDSA_SIGNATURE_SZ)] = {0};
    uint32_t sig_out[SZ_U32(MLDSA_SIGNATURE_SZ)] = {0};
    uint32_t vfy_out[SZ_U32(MLDSA_VERIFY_RES_SZ)] = {0};
    uint32_t mu_in[SZ_U32(MLDSA_EXTERNAL_MU_SZ)] = {0};

    uint32_t seed_d_in[SZ_U32(SEED_SZ)] = {0};
    uint32_t seed_z_in[SZ_U32(SEED_SZ)] = {0};
    uint32_t msg_in[SZ_U32(MLKEM_MSG_SZ)] = {0};
    uint32_t ek_in[SZ_U32(MLKEM_EK_SZ)] = {0};
    uint32_t ek_out[SZ_U32(MLKEM_EK_SZ)] = {0};
    uint32_t dk_in[SZ_U32(MLKEM_DK_SZ)] = {0};
    uint32_t dk_out[SZ_U32(MLKEM_DK_SZ)] = {0};
    uint32_t ct_in[SZ_U32(MLKEM_CT_SZ)] = {0};
    uint32_t ct_out[SZ_U32(MLKEM_CT_SZ)] = {0};
    uint32_t ss_out[SZ_U32(MLKEM_SHARED_KEY_SZ)] = {0};
    uint32_t name_out[SZ_U32(NAME_SZ)] = {0};

    int64_t max_cycle = 0;
    const char *op_name = NULL;

    if (argc < 2) {
        puts(usage);
        return 1;
    }

    //  pass plusargs (e.g. +trace-eng) through to Verilator so
    //  $test$plusargs works inside the SV.
    Verilated::commandArgs(argc, argv);

    for (int i = 1; i < argc;) {
        if (argv[i][0] == '+') {
            //  consumed by Verilated::commandArgs above
            i += 1;
            continue;
        }
        if (i + 1 < argc && strcmp(argv[i], "-t") == 0) {
            max_cycle = strtoll(argv[i + 1], NULL, 0);
            i += 2;
        } else if (i + 1 < argc && strcmp(argv[i], "-vcd") == 0) {
            vcd_out_fn = argv[i + 1];
            i += 2;
        } else if (i + 1 < argc && strcmp(argv[i], "-pk") == 0) {
            pk_in_fn = pk_out_fn = argv[i + 1];
            i += 2;
        } else if (i + 1 < argc && strcmp(argv[i], "-sk") == 0) {
            sk_in_fn = sk_out_fn = argv[i + 1];
            i += 2;
        } else if (i + 1 < argc && strcmp(argv[i], "-sig") == 0) {
            sig_in_fn = sig_out_fn = argv[i + 1];
            i += 2;
        } else if (i + 1 < argc && strcmp(argv[i], "-seed") == 0) {
            seed_in_fn = argv[i + 1];
            i += 2;
        } else if (i + 1 < argc && strcmp(argv[i], "-rnd") == 0) {
            rnd_in_fn = argv[i + 1];
            i += 2;
        } else if (i + 1 < argc && strcmp(argv[i], "-ent") == 0) {
            ent_in_fn = argv[i + 1];
            i += 2;
        } else if (i + 1 < argc && strcmp(argv[i], "-vfy") == 0) {
            vfy_out_fn = argv[i + 1];
            i += 2;
        } else if (i + 1 < argc && strcmp(argv[i], "-hash") == 0) {
            hash_in_fn = argv[i + 1];
            i += 2;
        } else if (i + 1 < argc && strcmp(argv[i], "-mu") == 0) {
            mu_in_fn = argv[i + 1];
            i += 2;
        } else if (i + 1 < argc && strcmp(argv[i], "-strm") == 0) {
            strm_in_fn = argv[i + 1];
            i += 2;
        } else if (i + 1 < argc && strcmp(argv[i], "-d") == 0) {
            seed_d_in_fn = argv[i + 1];
            i += 2;
        } else if (i + 1 < argc && strcmp(argv[i], "-z") == 0) {
            seed_z_in_fn = argv[i + 1];
            i += 2;
        } else if (i + 1 < argc && strcmp(argv[i], "-msg") == 0) {
            msg_in_fn = argv[i + 1];
            i += 2;
        } else if (i + 1 < argc && strcmp(argv[i], "-ek") == 0) {
            ek_in_fn = ek_out_fn = argv[i + 1];
            i += 2;
        } else if (i + 1 < argc && strcmp(argv[i], "-dk") == 0) {
            dk_in_fn = dk_out_fn = argv[i + 1];
            i += 2;
        } else if (i + 1 < argc && strcmp(argv[i], "-ct") == 0) {
            ct_in_fn = ct_out_fn = argv[i + 1];
            i += 2;
        } else if (i + 1 < argc && strcmp(argv[i], "-ss") == 0) {
            ss_out_fn = argv[i + 1];
            i += 2;
        } else if (strcmp(argv[i], "-h") == 0 || strcmp(argv[i], "--help") == 0) {
            puts(usage);
            return 0;
        } else if (op_name == NULL) {
            op_name = argv[i++];
        } else {
            fprintf(stderr, "%s: invalid flag or missing parameter: %s\n", argv[0], argv[i]);
            return 2;
        }
    }

    Operation ops[] = {
        {"mldsa-keygen", "KGEN", MLDSA_CTRL, MLDSA_STATUS, STATUS_MLDSA_ERROR, CTRL_KEYGEN,
            {{"ent", ABR_ENTROPY, ENTROPY_SZ, ent_in, ent_in_fn, true},
             {"seed", MLDSA_SEED, SEED_SZ, seed_in, seed_in_fn, false}},
            {{"sk", MLDSA_PRIVKEY_OUT, MLDSA_PRIVKEY_SZ, sk_out, sk_out_fn, false},
             {"pk", MLDSA_PUBKEY, MLDSA_PUBKEY_SZ, pk_out, pk_out_fn, false}},
            false, false},
        {"mldsa-sign", "SIGN", MLDSA_CTRL, MLDSA_STATUS, STATUS_MLDSA_ERROR, CTRL_SIGN_ENCAPS,
            {{"hash", MLDSA_MSG, MLDSA_MSG_SZ, hash_in, hash_in_fn, false},
             {"sk", MLDSA_PRIVKEY_IN, MLDSA_PRIVKEY_SZ, sk_in, sk_in_fn, false},
             {"rnd", MLDSA_SIGN_RND, MLDSA_SIGN_RND_SZ, rnd_in, rnd_in_fn, false},
             {"ent", ABR_ENTROPY, ENTROPY_SZ, ent_in, ent_in_fn, true}},
            {{"sig", MLDSA_SIGNATURE, MLDSA_SIGNATURE_SZ, sig_out, sig_out_fn, false}},
            false, false},
        {"mldsa-verify", "VRFY", MLDSA_CTRL, MLDSA_STATUS, STATUS_MLDSA_ERROR, CTRL_VERIFY_DECAPS,
            {{"hash", MLDSA_MSG, MLDSA_MSG_SZ, hash_in, hash_in_fn, false},
             {"pk", MLDSA_PUBKEY, MLDSA_PUBKEY_SZ, pk_in, pk_in_fn, false},
             {"sig", MLDSA_SIGNATURE, MLDSA_SIGNATURE_SZ, sig_in, sig_in_fn, false}},
            {{"vfy", MLDSA_VERIFY_RES, MLDSA_VERIFY_RES_SZ, vfy_out, vfy_out_fn, true}},
            true, false},
        {"mldsa-kgsign", "KGSG", MLDSA_CTRL, MLDSA_STATUS, STATUS_MLDSA_ERROR, CTRL_KG_COMBO,
            {{"seed", MLDSA_SEED, SEED_SZ, seed_in, seed_in_fn, false},
             {"hash", MLDSA_MSG, MLDSA_MSG_SZ, hash_in, hash_in_fn, false},
             {"rnd", MLDSA_SIGN_RND, MLDSA_SIGN_RND_SZ, rnd_in, rnd_in_fn, false},
             {"ent", ABR_ENTROPY, ENTROPY_SZ, ent_in, ent_in_fn, true}},
            {{"sig", MLDSA_SIGNATURE, MLDSA_SIGNATURE_SZ, sig_out, sig_out_fn, false}},
            false, false},
        {"mldsa-sign-extmu", "MUSGN", MLDSA_CTRL, MLDSA_STATUS, STATUS_MLDSA_ERROR,
            CTRL_SIGN_ENCAPS | CTRL_EXTERNAL_MU,
            {{"mu", MLDSA_EXTERNAL_MU, MLDSA_EXTERNAL_MU_SZ, mu_in, mu_in_fn, false},
             {"sk", MLDSA_PRIVKEY_IN, MLDSA_PRIVKEY_SZ, sk_in, sk_in_fn, false},
             {"rnd", MLDSA_SIGN_RND, MLDSA_SIGN_RND_SZ, rnd_in, rnd_in_fn, false},
             {"ent", ABR_ENTROPY, ENTROPY_SZ, ent_in, ent_in_fn, true}},
            {{"sig", MLDSA_SIGNATURE, MLDSA_SIGNATURE_SZ, sig_out, sig_out_fn, false}},
            false, false},
        {"mldsa-sign-stream", "STMSG", MLDSA_CTRL, MLDSA_STATUS, STATUS_MLDSA_ERROR,
            CTRL_SIGN_ENCAPS | CTRL_STREAM_MSG,
            {{"sk", MLDSA_PRIVKEY_IN, MLDSA_PRIVKEY_SZ, sk_in, sk_in_fn, false},
             {"rnd", MLDSA_SIGN_RND, MLDSA_SIGN_RND_SZ, rnd_in, rnd_in_fn, false},
             {"ent", ABR_ENTROPY, ENTROPY_SZ, ent_in, ent_in_fn, true}},
            {{"sig", MLDSA_SIGNATURE, MLDSA_SIGNATURE_SZ, sig_out, sig_out_fn, false}},
            false, true},
        {"mlkem-keygen", "KEMKG", MLKEM_CTRL, MLKEM_STATUS, STATUS_MLKEM_ERROR, CTRL_KEYGEN,
            {{"d", MLKEM_SEED_D, SEED_SZ, seed_d_in, seed_d_in_fn, false},
             {"z", MLKEM_SEED_Z, SEED_SZ, seed_z_in, seed_z_in_fn, false},
             {"ent", ABR_ENTROPY, ENTROPY_SZ, ent_in, ent_in_fn, true}},
            {{"dk", MLKEM_DECAPS_KEY, MLKEM_DK_SZ, dk_out, dk_out_fn, false},
             {"ek", MLKEM_ENCAPS_KEY, MLKEM_EK_SZ, ek_out, ek_out_fn, false}},
            false, false},
        {"mlkem-encaps", "ENCAP", MLKEM_CTRL, MLKEM_STATUS, STATUS_MLKEM_ERROR, CTRL_SIGN_ENCAPS,
            {{"ek", MLKEM_ENCAPS_KEY, MLKEM_EK_SZ, ek_in, ek_in_fn, false},
             {"msg", MLKEM_MSG, MLKEM_MSG_SZ, msg_in, msg_in_fn, false},
             {"ent", ABR_ENTROPY, ENTROPY_SZ, ent_in, ent_in_fn, true}},
            {{"ct", MLKEM_CIPHERTEXT, MLKEM_CT_SZ, ct_out, ct_out_fn, false},
             {"ss", MLKEM_SHARED_KEY, MLKEM_SHARED_KEY_SZ, ss_out, ss_out_fn, false}},
            false, false},
        {"mlkem-decaps", "DECAP", MLKEM_CTRL, MLKEM_STATUS, STATUS_MLKEM_ERROR, CTRL_VERIFY_DECAPS,
            {{"dk", MLKEM_DECAPS_KEY, MLKEM_DK_SZ, dk_in, dk_in_fn, false},
             {"ct", MLKEM_CIPHERTEXT, MLKEM_CT_SZ, ct_in, ct_in_fn, false},
             {"ent", ABR_ENTROPY, ENTROPY_SZ, ent_in, ent_in_fn, true}},
            {{"ss", MLKEM_SHARED_KEY, MLKEM_SHARED_KEY_SZ, ss_out, ss_out_fn, false}},
            false, false},
        {"mlkem-kgdecaps", "KGDEC", MLKEM_CTRL, MLKEM_STATUS, STATUS_MLKEM_ERROR, CTRL_KG_COMBO,
            {{"d", MLKEM_SEED_D, SEED_SZ, seed_d_in, seed_d_in_fn, false},
             {"z", MLKEM_SEED_Z, SEED_SZ, seed_z_in, seed_z_in_fn, false},
             {"ct", MLKEM_CIPHERTEXT, MLKEM_CT_SZ, ct_in, ct_in_fn, false},
             {"ent", ABR_ENTROPY, ENTROPY_SZ, ent_in, ent_in_fn, true}},
            {{"dk", MLKEM_DECAPS_KEY, MLKEM_DK_SZ, dk_out, dk_out_fn, false},
             {"ek", MLKEM_ENCAPS_KEY, MLKEM_EK_SZ, ek_out, ek_out_fn, false},
             {"ss", MLKEM_SHARED_KEY, MLKEM_SHARED_KEY_SZ, ss_out, ss_out_fn, false}},
            false, false},
    };

    if (op_name == NULL) {
        puts(usage);
        return 1;
    }
    if (strcmp(op_name, "keygen") == 0)
        op_name = "mldsa-keygen";
    else if (strcmp(op_name, "sign") == 0)
        op_name = "mldsa-sign";
    else if (strcmp(op_name, "verify") == 0)
        op_name = "mldsa-verify";
    else if (strcmp(op_name, "kgsign") == 0)
        op_name = "mldsa-kgsign";

    Operation *op = NULL;
    for (size_t i = 0; i < sizeof(ops) / sizeof(ops[0]); i++) {
        if (strcmp(op_name, ops[i].name) == 0) {
            op = &ops[i];
            break;
        }
    }
    if (op == NULL) {
        fprintf(stderr, "%s: unknown operation: %s\n", argv[0], op_name);
        return 2;
    }

    for (const Io &io : op->inputs)
        read_fn(io.data, io.size, io.fn, io.optional);

    uint8_t *stream_owned = NULL;
    const uint8_t *stream_buf = NULL;
    size_t stream_len = 0;
    if (op->stream_input) {
        FILE *fp = fopen(strm_in_fn, "rb");
        if (fp == NULL) {
            perror(strm_in_fn);
            return 1;
        }
        fseek(fp, 0, SEEK_END);
        long sz = ftell(fp);
        fseek(fp, 0, SEEK_SET);
        stream_len = (sz < 0) ? 0 : (size_t) sz;
        stream_owned = (uint8_t *) calloc(stream_len + 1, 1);
        if (stream_len > 0 &&
            fread(stream_owned, 1, stream_len, fp) != stream_len) {
            perror(strm_in_fn);
            fclose(fp);
            return 1;
        }
        fclose(fp);
        stream_buf = stream_owned;
        printf("[LOAD]\t%s (read %zu bytes)\n", strm_in_fn, stream_len);
    }

    uint64_t hclk = 0;
    int64_t cycle = 0;
    int main_fsm = 0;
    int wait_ready = 0;
    uint32_t wait_complete_mask = STATUS_READY | STATUS_VALID;
    int prev_status = -1;
    bool dump_trace = false;
    bool timed_out = false;
    bool status_error = false;

    int xfer_fsm = 0;
    bool xfer_write = false;
    uint32_t xfer_addr = 0;
    uint32_t xfer_stop = 0;
    uint32_t *xfer_data = NULL;
    size_t xfer_index = 0;
    const std::vector<Io> *xfer_list = NULL;

    int stream_fsm = 0;
    int stream_phase = 0;
    size_t stream_pos = 0;
    bool stream_done = false;

    Vabr_wrap *dut = new Vabr_wrap;
    VerilatedVcdC *tfp = NULL;

    if (vcd_out_fn != NULL) {
        Verilated::traceEverOn(true);
        tfp = new VerilatedVcdC;
        dut->trace(tfp, 99);
        tfp->open(vcd_out_fn);
    }

    ahb_clear(dut);

    while (main_fsm >= 0 && !Verilated::gotFinish()) {
        hclk++;
        dut->clk = !dut->clk;
        dut->eval();
        if (tfp != NULL && dump_trace)
            tfp->dump(5 * hclk);
        if (dut->clk)
            continue;

        cycle++;
        if (max_cycle > 0 && cycle > max_cycle) {
            timed_out = true;
            break;
        }

        if ((cycle % 100) == 0) {
            printf(" %ld \r", cycle);
            fflush(stdout);
        }

        dut->hready_i = dut->hreadyout_o;

        if (stream_fsm) {
            switch (stream_fsm) {
                case 1: {
                    size_t remaining = stream_len - stream_pos;
                    if (stream_phase == 1) {
                        if (remaining >= 4) {
                            uint32_t w =
                                  (uint32_t) stream_buf[stream_pos]
                                | ((uint32_t) stream_buf[stream_pos + 1] << 8)
                                | ((uint32_t) stream_buf[stream_pos + 2] << 16)
                                | ((uint32_t) stream_buf[stream_pos + 3] << 24);
                            ahb_write(dut, MLDSA_MSG, w);
                            stream_fsm = 2;
                        } else {
                            stream_phase = 2;
                            // No write this cycle; re-enter case 1 next cycle.
                        }
                    } else if (stream_phase == 2) {
                        uint32_t strobe = (remaining == 0)
                            ? 0u
                            : (uint32_t) ((1u << remaining) - 1);
                        printf("[STRM]\t%ld\tstrobe %u, partial %zu\n",
                               cycle, strobe, remaining);
                        ahb_write(dut, MLDSA_MSG_STROBE, strobe);
                        stream_fsm = 2;
                    } else if (stream_phase == 3) {
                        uint32_t w = 0;
                        for (size_t k = 0; k < remaining; k++)
                            w |= (uint32_t) stream_buf[stream_pos + k] << (8 * k);
                        ahb_write(dut, MLDSA_MSG, w);
                        stream_fsm = 2;
                    }
                    break;
                }
                case 2:
                    ahb_clear(dut);
                    if (dut->hreadyout_o) {
                        if (stream_phase == 1) {
                            stream_pos += 4;
                            stream_fsm = 1;
                        } else if (stream_phase == 2) {
                            stream_phase = 3;
                            stream_fsm = 1;
                        } else if (stream_phase == 3) {
                            stream_fsm = 0;
                            stream_phase = 0;
                            stream_done = true;
                            wait_complete_mask = STATUS_READY | STATUS_VALID;
                            wait_ready = 1;
                            printf("[STRM]\t%ld\tstream done (%zu bytes)\n",
                                   cycle, stream_len);
                        }
                    }
                    break;
            }
            continue;
        }

        if (xfer_fsm) {
            switch (xfer_fsm) {
                case 1:
                    if (xfer_addr < xfer_stop) {
                        if (xfer_write) {
                            uint32_t x = xfer_data == NULL ? 0 : *xfer_data;
                            ahb_write(dut, xfer_addr, x);
                        } else {
                            ahb_read(dut, xfer_addr);
                        }
                        xfer_fsm = 2;
                    } else {
                        const char *label = "info";
                        if (xfer_list != NULL)
                            label = (*xfer_list)[xfer_index].label;
                        printf("[XFER]\t%ld\t%s %s\n", cycle,
                               xfer_write ? "write" : "read", label);
                        xfer_index++;
                        if (xfer_list != NULL && xfer_index < xfer_list->size()) {
                            const Io &io = (*xfer_list)[xfer_index];
                            xfer_addr = io.addr;
                            xfer_stop = io.addr + io.size;
                            xfer_data = io.data;
                            xfer_fsm = 1;
                        } else {
                            xfer_fsm = 0;
                            xfer_list = NULL;
                        }
                    }
                    break;

                case 2:
                    ahb_clear(dut);
                    if (dut->hreadyout_o) {
                        if (!xfer_write)
                            *xfer_data = read_lane(dut, xfer_addr);
                        xfer_addr += 4;
                        xfer_data++;
                        xfer_fsm = 1;
                    }
                    break;
            }
            continue;
        }

        ahb_clear(dut);

        if (wait_ready == 1) {
            prev_status = -1;
            wait_ready = 2;
            continue;
        } else if (wait_ready == 2) {
            ahb_read(dut, op->status_addr);
            wait_ready = 3;
            continue;
        } else if (wait_ready == 3) {
            int status = read_lane(dut, op->status_addr);
            if (status != prev_status) {
                printf("[STAT]\t%ld\tstatus= %d%s%s%s\n",
                       cycle, status,
                       status & STATUS_READY ? " <READY>" : "",
                       status & STATUS_VALID ? " <VALID>" : "",
                       status & op->status_error_mask ? " <ERROR>" : "");
                prev_status = status;
            }
            if (status & op->status_error_mask) {
                status_error = true;
                wait_ready = 0;
                main_fsm = -1;
            } else {
                wait_ready = (status & wait_complete_mask) ? 0 : 2;
            }
            continue;
        }

        switch (main_fsm) {
            case 0:
                printf("[INIT]\t%ld\n", cycle);
                dut->rst_b = 0;
                dut->hsize_i = 3;
                dut->haddr_i = 0;
                dut->hwdata_i = 0;
                wait_ready = 1;
                main_fsm++;
                break;

            case 1:
                xfer_write = false;
                xfer_addr = (op->ctrl_addr == MLKEM_CTRL) ? MLKEM_NAME : MLDSA_NAME;
                xfer_stop = xfer_addr + NAME_SZ;
                xfer_data = name_out;
                xfer_fsm = 1;
                xfer_list = NULL;
                main_fsm++;
                break;

            case 2:
                printf("[INFO]\tname + ver: ");
                dump_hex(name_out, NAME_SZ);
                main_fsm++;
                break;

            case 3:
                printf("[INIT]\t%s\n", op->name);
                xfer_write = true;
                xfer_index = 0;
                xfer_list = &op->inputs;
                if (xfer_list->empty()) {
                    main_fsm++;
                    break;
                }
                xfer_addr = (*xfer_list)[0].addr;
                xfer_stop = xfer_addr + (*xfer_list)[0].size;
                xfer_data = (*xfer_list)[0].data;
                xfer_fsm = 1;
                main_fsm++;
                break;

            case 4:
                printf("[%s]\t%ld\tstart\n", op->tag, cycle);
                dump_trace = true;
                ahb_write(dut, op->ctrl_addr, op->ctrl);
                wait_complete_mask = op->stream_input
                    ? STATUS_MLDSA_STREAM_RDY
                    : (STATUS_READY | STATUS_VALID);
                wait_ready = 1;
                main_fsm++;
                break;

            case 5:
                if (op->stream_input && !stream_done) {
                    printf("[STRM]\t%ld\tstream start (%zu bytes)\n",
                           cycle, stream_len);
                    stream_pos = 0;
                    stream_phase = 1;
                    stream_fsm = 1;
                    break;
                }
                printf("[%s]\t%ld\tdone\n", op->tag, cycle);
                dump_trace = false;
                xfer_write = false;
                xfer_index = 0;
                xfer_list = &op->outputs;
                if (xfer_list->empty()) {
                    main_fsm = -1;
                    break;
                }
                xfer_addr = (*xfer_list)[0].addr;
                xfer_stop = xfer_addr + (*xfer_list)[0].size;
                xfer_data = (*xfer_list)[0].data;
                xfer_fsm = 1;
                main_fsm++;
                break;

            case 6:
                for (const Io &io : op->outputs)
                    write_fn(io.data, io.size, io.fn);
                if (op->verify_result) {
                    // VERIFY_RES is the recomputed c_tilde; compare it against
                    // the first 64 bytes of the ML-DSA-87 signature.
                    if (memcmp(vfy_out, sig_in, MLDSA_VERIFY_RES_SZ) == 0)
                        printf("[INFO]\tSignature verify OK\n");
                    else
                        printf("[INFO]\tSignature verify BAD\n");
                }
                main_fsm = -1;
                break;

            default:
                printf("[INFO]\tInvalid state %d.\n", main_fsm);
                main_fsm = -1;
                break;
        }
    }

    printf("[EXIT]\t%ld%s%s\n", cycle,
           timed_out ? " <TIMEOUT>" : "",
           status_error ? " <ERROR>" : "");

    dut->final();
    if (tfp != NULL)
        tfp->close();
    delete dut;
    free(stream_owned);

    return (timed_out || status_error) ? 1 : 0;
}
