/*
 * presi.c -- main() for presi-cosim.
 *
 * The cycle-stepping core, AHB driver, snapshot save/load, and SRAM
 * model live in libpresi_gates.a (presi_gates.c, presi_state.c,
 * presi_sram.c).  This file is pure CLI / harness orchestration:
 * argv parsing, subcommand dispatch, and the FSM-trace smoke run.
 *
 * CLI mirrors src/abr_wrap.cpp where possible; presi-only flags
 * (-load, -save, -init-only) layer on top.  See state-plan.md.
 */

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "presi_model.h"
#include "presi_state.h"
#include "presi_gates.h"

#ifdef PRESI_HAVE_NETLIST
#include "abr_wrap.presi_idx.h"
#endif

/* ---- FSM probes (smoke-test only).  Names in presi_idx.h come from
 * presi_map.csv; they exist as long as the abr_ctrl wires aren't
 * optimised away. */
#ifdef PRESI_HAVE_NETLIST

#define _PRESI_PROG_CNTR_BITS \
    X(0) X(1) X(2) X(3) X(4) X(5) X(6) X(7) X(8) X(9)

#define _PRESI_CMD_REG_BITS \
    X(0) X(1) X(2)

static const int presi_prog_cntr_idx[10] = {
#define X(i) IDX_top0_abr_ctrl_inst_abr_prog_cntr_##i,
    _PRESI_PROG_CNTR_BITS
#undef X
};
static const int presi_prog_cntr_nxt_idx[10] = {
#define X(i) IDX_top0_abr_ctrl_inst_abr_prog_cntr_nxt_##i,
    _PRESI_PROG_CNTR_BITS
#undef X
};
static const int presi_mldsa_cmd_reg_idx[3] = {
#define X(i) IDX_top0_abr_ctrl_inst_mldsa_cmd_reg_##i,
    _PRESI_CMD_REG_BITS
#undef X
};
static const int presi_mlkem_cmd_reg_idx[3] = {
#define X(i) IDX_top0_abr_ctrl_inst_mlkem_cmd_reg_##i,
    _PRESI_CMD_REG_BITS
#undef X
};

#endif /* PRESI_HAVE_NETLIST */

/* ---- File slots.  Defaults match src/abr_wrap.cpp. */
struct slots {
    /* ML-DSA */
    const char *seed_in_fn;
    const char *hash_in_fn;
    const char *ent_in_fn;
    const char *rnd_in_fn;
    const char *mu_in_fn;
    const char *strm_in_fn;
    const char *pk_in_fn;
    const char *pk_out_fn;
    const char *sk_in_fn;
    const char *sk_out_fn;
    const char *sig_in_fn;
    const char *sig_out_fn;
    const char *vfy_out_fn;
    /* ML-KEM */
    const char *seed_d_in_fn;
    const char *seed_z_in_fn;
    const char *msg_in_fn;
    const char *ek_in_fn;
    const char *ek_out_fn;
    const char *dk_in_fn;
    const char *dk_out_fn;
    const char *ct_in_fn;
    const char *ct_out_fn;
    const char *ss_out_fn;
    /* presi-only */
    const char *load_fn;
    const char *save_fn;
    /* misc */
    const char *vcd_out_fn;     /* accepted+ignored, abr_wrap compat */
    int64_t     max_cycle;
    int         init_only;
    int         no_output;
};

static void slots_defaults(struct slots *s)
{
    memset(s, 0, sizeof(*s));
    s->seed_in_fn   = "seed_in.dat";
    s->hash_in_fn   = "hash_in.dat";
    s->ent_in_fn    = "ent_in.dat";
    s->rnd_in_fn    = "rnd_in.dat";
    s->mu_in_fn     = "mu_in.dat";
    s->strm_in_fn   = "strm_in.dat";
    s->pk_in_fn     = "pk_in.dat";
    s->pk_out_fn    = "pk_out.dat";
    s->sk_in_fn     = "sk_in.dat";
    s->sk_out_fn    = "sk_out.dat";
    s->sig_in_fn    = "sig_in.dat";
    s->sig_out_fn   = "sig_out.dat";
    s->seed_d_in_fn = "seed_d_in.dat";
    s->seed_z_in_fn = "seed_z_in.dat";
    s->msg_in_fn    = "msg_in.dat";
    s->ek_in_fn     = "ek_in.dat";
    s->ek_out_fn    = "ek_out.dat";
    s->dk_in_fn     = "dk_in.dat";
    s->dk_out_fn    = "dk_out.dat";
    s->ct_in_fn     = "ct_in.dat";
    s->ct_out_fn    = "ct_out.dat";
    s->ss_out_fn    = "ss_out.dat";
    s->max_cycle    = 200000;
}

static const char usage[] =
    "USAGE: presi-cosim [options] [operation]\n\n"
    "Operations:\n"
    "  smoke                 256-cycle FSM probe (default if none given)\n"
    "  mldsa-keygen, keygen  ML-DSA-87 key generation\n"
    "  run                   load snapshot, step -t cycles, save snapshot\n"
    "  dump-pk               load snapshot, AHB-read MLDSA_PUBKEY, write -pk\n"
    "  dump-sk               load snapshot, AHB-read MLDSA_PRIVKEY, write -sk\n"
    "  (other mldsa-* / mlkem-* names are recognised but not yet wired)\n\n"
    "Options (matching abr_wrap):\n"
    "  -t <n>      max cycles (default 200000)\n"
    "  -vcd <fn>   accepted but ignored (abr_wrap compatibility)\n"
    "  -seed <fn>  ML-DSA seed input (seed_in.dat)\n"
    "  -ent <fn>   masking entropy input (ent_in.dat, optional)\n"
    "  -hash <fn>, -rnd <fn>, -mu <fn>, -strm <fn>\n"
    "  -pk <fn>    public key  (pk_in.dat, pk_out.dat)\n"
    "  -sk <fn>    private key (sk_in.dat, sk_out.dat)\n"
    "  -sig <fn>, -d <fn>, -z <fn>, -msg <fn>, -ek <fn>, -dk <fn>,\n"
    "  -ct <fn>, -ss <fn>\n\n"
    "Options (presi-only):\n"
    "  -load <fn>    load snapshot before running (skips reset+AHB-init)\n"
    "  -save <fn>    save snapshot at end\n"
    "  -init-only    stop after CTRL write (useful with -save)\n"
    "  -no-output    skip writing output .dat files\n"
    "  -h, --help    this message\n";

static int parse_argv(int argc, char **argv, struct slots *s,
                      const char **op_name)
{
    int i = 1;
    *op_name = NULL;
    while (i < argc) {
        const char *a = argv[i];
        if (i + 1 < argc && strcmp(a, "-t") == 0) {
            s->max_cycle = strtoll(argv[i + 1], NULL, 0); i += 2;
        } else if (i + 1 < argc && strcmp(a, "-vcd") == 0) {
            s->vcd_out_fn = argv[i + 1]; i += 2;
        } else if (i + 1 < argc && strcmp(a, "-pk") == 0) {
            s->pk_in_fn = s->pk_out_fn = argv[i + 1]; i += 2;
        } else if (i + 1 < argc && strcmp(a, "-sk") == 0) {
            s->sk_in_fn = s->sk_out_fn = argv[i + 1]; i += 2;
        } else if (i + 1 < argc && strcmp(a, "-sig") == 0) {
            s->sig_in_fn = s->sig_out_fn = argv[i + 1]; i += 2;
        } else if (i + 1 < argc && strcmp(a, "-seed") == 0) {
            s->seed_in_fn = argv[i + 1]; i += 2;
        } else if (i + 1 < argc && strcmp(a, "-rnd") == 0) {
            s->rnd_in_fn = argv[i + 1]; i += 2;
        } else if (i + 1 < argc && strcmp(a, "-ent") == 0) {
            s->ent_in_fn = argv[i + 1]; i += 2;
        } else if (i + 1 < argc && strcmp(a, "-vfy") == 0) {
            s->vfy_out_fn = argv[i + 1]; i += 2;
        } else if (i + 1 < argc && strcmp(a, "-hash") == 0) {
            s->hash_in_fn = argv[i + 1]; i += 2;
        } else if (i + 1 < argc && strcmp(a, "-mu") == 0) {
            s->mu_in_fn = argv[i + 1]; i += 2;
        } else if (i + 1 < argc && strcmp(a, "-strm") == 0) {
            s->strm_in_fn = argv[i + 1]; i += 2;
        } else if (i + 1 < argc && strcmp(a, "-d") == 0) {
            s->seed_d_in_fn = argv[i + 1]; i += 2;
        } else if (i + 1 < argc && strcmp(a, "-z") == 0) {
            s->seed_z_in_fn = argv[i + 1]; i += 2;
        } else if (i + 1 < argc && strcmp(a, "-msg") == 0) {
            s->msg_in_fn = argv[i + 1]; i += 2;
        } else if (i + 1 < argc && strcmp(a, "-ek") == 0) {
            s->ek_in_fn = s->ek_out_fn = argv[i + 1]; i += 2;
        } else if (i + 1 < argc && strcmp(a, "-dk") == 0) {
            s->dk_in_fn = s->dk_out_fn = argv[i + 1]; i += 2;
        } else if (i + 1 < argc && strcmp(a, "-ct") == 0) {
            s->ct_in_fn = s->ct_out_fn = argv[i + 1]; i += 2;
        } else if (i + 1 < argc && strcmp(a, "-ss") == 0) {
            s->ss_out_fn = argv[i + 1]; i += 2;
        } else if (i + 1 < argc && strcmp(a, "-load") == 0) {
            s->load_fn = argv[i + 1]; i += 2;
        } else if (i + 1 < argc && strcmp(a, "-save") == 0) {
            s->save_fn = argv[i + 1]; i += 2;
        } else if (strcmp(a, "-init-only") == 0) {
            s->init_only = 1; i += 1;
        } else if (strcmp(a, "-no-output") == 0) {
            s->no_output = 1; i += 1;
        } else if (strcmp(a, "-h") == 0 || strcmp(a, "--help") == 0) {
            puts(usage); return -1;
        } else if (*op_name == NULL && a[0] != '-') {
            *op_name = a; i += 1;
            /* Back-compat: `<op> <max-cycles>` positional form. */
            if (i < argc && argv[i][0] != '-' && (i + 1 == argc ||
                    argv[i + 1][0] == '-')) {
                /* Accept only if it parses as an integer. */
                char *end;
                long long v = strtoll(argv[i], &end, 0);
                if (*end == '\0') {
                    s->max_cycle = v;
                    i += 1;
                }
            }
        } else {
            fprintf(stderr, "%s: invalid flag or extra arg: %s\n", argv[0], a);
            return -2;
        }
    }
    return 0;
}

/* ---- snapshot bookends. */

static int load_snapshot(struct presi_model *m, const char *fn)
{
    FILE *fp = fopen(fn, "rb");
    if (fp == NULL) {
        perror(fn);
        return -1;
    }
    if (presi_state_load(fp, m) != 0) {
        fclose(fp);
        return -1;
    }
    fclose(fp);
    presi_settle_after_load(m);
    printf("[LOAD]\tstate from %s (cycle=%llu)\n",
           fn, (unsigned long long) m->cycle);
    return 0;
}

static int save_snapshot(const struct presi_model *m, const char *fn)
{
    FILE *fp = fopen(fn, "wb");
    if (fp == NULL) {
        perror(fn);
        return -1;
    }
    if (presi_state_save(fp, m) != 0) {
        fclose(fp);
        return -1;
    }
    fclose(fp);
    printf("[SAVE]\tstate to %s (cycle=%llu)\n",
           fn, (unsigned long long) m->cycle);
    return 0;
}

/* ---- subcommand handlers. */

static int run_smoke(struct presi_model *m)
{
    unsigned poll;
    unsigned n_busy = 0;
    unsigned cycle_at_busy = 0;
#ifdef PRESI_HAVE_NETLIST
    unsigned prev_pc = (unsigned) -1;
    unsigned same_count = 0;
#endif

    /* Sanity-read NAME / VERSION / STATUS via AHB. */
    printf("[BUS]\tNAME[0]    =%08x  (want 44534d4c)\n",
           ahb_read(m, 0x00));
    printf("[BUS]\tNAME[1]    =%08x  (want 3837412d)\n",
           ahb_read(m, 0x04));
    printf("[BUS]\tVERSION[0] =%08x  (want 302e322e)\n",
           ahb_read(m, 0x08));
    printf("[BUS]\tVERSION[1] =%08x  (want 00003300)\n",
           ahb_read(m, 0x0c));
    printf("[BUS]\tSTATUS     =%08x  (READY bit expected = 1)\n",
           ahb_read(m, ABR_STATUS));

    ahb_write(m, ABR_ENTROPY, 0);
    printf("[CTRL]\twriting MLDSA_CTRL = 1 (keygen)\n");
    ahb_write(m, ABR_CTRL, 1);
    for (poll = 0; poll < 256; poll++) {
        presi_cycle(m);
#ifdef PRESI_HAVE_NETLIST
        {
            unsigned pc = presi_read_bits(presi_prog_cntr_idx, 10);
            unsigned pc_nxt = presi_read_bits(presi_prog_cntr_nxt_idx, 10);
            unsigned en = presi_s[IDX_top0_abr_ctrl_inst_abr_seq_en] & 1;
            unsigned mldsa_cmd = presi_read_bits(presi_mldsa_cmd_reg_idx, 3);
            unsigned mlkem_cmd = presi_read_bits(presi_mlkem_cmd_reg_idx, 3);
            unsigned z = presi_s[IDX_top0_abr_ctrl_inst_stream_msg_buffer_zeroize] & 1;
            unsigned rdy = presi_s[IDX_top0_abr_ctrl_inst_abr_ready] & 1;
            unsigned idle = presi_s[IDX_top0_abr_ctrl_inst_abr_idle] & 1;
            unsigned err = presi_s[IDX_top0_abr_ctrl_inst_error_flag_reg] & 1;
            unsigned sub = presi_s[IDX_top0_abr_ctrl_inst_subcomponent_busy] & 1;
            unsigned cvv = presi_s[IDX_top0_abr_ctrl_inst_clear_verify_valid] & 1;
            unsigned kgp = presi_s[IDX_top0_abr_ctrl_inst_mldsa_keygen_process] & 1;
            if (poll < 16) {
                printf("[FSM]\tc=%u pc=%u nxt=%u en=%u  "
                       "rdy=%u idle=%u err=%u sub=%u cvv=%u kgp=%u  "
                       "z=%u cmd=%u/%u\n",
                       poll, pc, pc_nxt, en, rdy, idle, err, sub, cvv,
                       kgp, z, mldsa_cmd, mlkem_cmd);
            } else if (pc != prev_pc) {
                if (same_count > 0) {
                    printf("[FSM]\t  ... held for %u cycle%s\n",
                           same_count, same_count == 1 ? "" : "s");
                }
                printf("[FSM]\tcycle=%u  pc=%u  nxt=%u  en=%u  z=%u\n",
                       poll, pc, pc_nxt, en, z);
                prev_pc = pc;
                same_count = 0;
            } else {
                same_count++;
            }
        }
#endif
        if (m->p.busy_o & 1) {
            if (n_busy == 0) {
                cycle_at_busy = poll;
            }
            n_busy++;
        }
    }
#ifdef PRESI_HAVE_NETLIST
    if (same_count > 0) {
        printf("[FSM]\t  ... held for %u cycle%s\n",
               same_count, same_count == 1 ? "" : "s");
    }
#endif
    printf("[CTRL]\tafter %u cycles: first-busy=%u busy-cycles=%u "
           "final-busy=%u status=%08x\n",
           poll, cycle_at_busy, n_busy, m->p.busy_o & 1,
           ahb_read(m, ABR_STATUS));
    return 0;
}

static int run_mldsa_keygen(struct presi_model *m, const struct slots *s)
{
    int rc = mldsa_keygen_init(m, s->ent_in_fn, s->seed_in_fn);
    if (rc != 0 || s->init_only) {
        return rc;
    }
    rc = mldsa_keygen_run(m, (uint64_t) s->max_cycle);
    if (rc != 0) {
        return rc;
    }
    if (!s->no_output) {
        rc = mldsa_keygen_finish(m, s->pk_out_fn, s->sk_out_fn);
    }
    return rc;
}

static int run_steps(struct presi_model *m, const struct slots *s)
{
    int64_t i;
    if (s->load_fn == NULL) {
        fprintf(stderr, "run: -load <fn> required\n");
        return 1;
    }
    if (s->max_cycle <= 0) {
        printf("[RUN]\tno-op (-t %lld)\n", (long long) s->max_cycle);
        return 0;
    }
    /* Park the AHB master at IDLE; no transactions are issued. */
    presi_drive_idle(m);
    for (i = 0; i < s->max_cycle; i++) {
        presi_cycle(m);
    }
    printf("[RUN]\tstepped %lld cycles  (now cycle=%llu)\n",
           (long long) s->max_cycle, (unsigned long long) m->cycle);
    return 0;
}

static int run_dump_pk(struct presi_model *m, const struct slots *s)
{
    if (s->load_fn == NULL) {
        fprintf(stderr, "dump-pk: -load <fn> required\n");
        return 1;
    }
    return mldsa_keygen_finish(m, s->pk_out_fn, NULL);
}

static int run_dump_sk(struct presi_model *m, const struct slots *s)
{
    if (s->load_fn == NULL) {
        fprintf(stderr, "dump-sk: -load <fn> required\n");
        return 1;
    }
    return mldsa_keygen_finish(m, NULL, s->sk_out_fn);
}

static int known_unimpl_op(const char *op)
{
    static const char *const u[] = {
        "mldsa-sign", "mldsa-verify", "mldsa-kgsign",
        "mldsa-sign-extmu", "mldsa-sign-stream",
        "mlkem-keygen", "mlkem-encaps", "mlkem-decaps", "mlkem-kgdecaps",
        "sign", "verify", "kgsign",
    };
    unsigned i;
    for (i = 0; i < sizeof(u) / sizeof(u[0]); i++) {
        if (strcmp(op, u[i]) == 0) return 1;
    }
    return 0;
}

int main(int argc, char **argv)
{
    struct presi_model model;
    struct slots s;
    const char *op_name = NULL;
    unsigned i;
    int rc;

    slots_defaults(&s);
    rc = parse_argv(argc, argv, &s, &op_name);
    if (rc < 0) {
        return rc == -1 ? 0 : 2;
    }
    if (op_name == NULL) {
        op_name = "smoke";
    }
    /* abr_wrap-style aliases. */
    if (strcmp(op_name, "keygen") == 0)        op_name = "mldsa-keygen";
    else if (strcmp(op_name, "sign") == 0)     op_name = "mldsa-sign";
    else if (strcmp(op_name, "verify") == 0)   op_name = "mldsa-verify";
    else if (strcmp(op_name, "kgsign") == 0)   op_name = "mldsa-kgsign";

    /* ---- bring up the model. */
    printf("[INIT]\tpresi-cosim  (op=%s)\n", op_name);
    printf("[INFO]\tSRAM models: %u\n", (unsigned) PRESI_ABR_SRAM_COUNT);
    {
        presi_t tmp[32];
        presi_u32_to_bits(tmp, 32, 0x12345678u);
        if (presi_bits_to_u32(tmp, 32) != 0x12345678u) {
            fprintf(stderr, "bit-vector helper self-test failed\n");
            return 1;
        }
    }
    if (presi_model_init(&model) != 0) {
        for (i = 0; i < PRESI_ABR_SRAM_COUNT; i++) {
            fprintf(stderr, "could not allocate SRAM %s\n",
                    presi_sram_descs[i].name);
        }
        return 1;
    }
    for (i = 0; i < PRESI_ABR_SRAM_COUNT; i++) {
        printf("[SRAM]\t%u\t%s depth=%u width=%u%s\n",
               i, presi_sram_descs[i].name,
               presi_sram_descs[i].depth,
               presi_sram_descs[i].data_width,
               presi_sram_descs[i].byte_enable ? " be" : "");
    }

    /* ---- choose initial state: snapshot OR reset. */
    if (s.load_fn != NULL) {
        if (load_snapshot(&model, s.load_fn) != 0) {
            presi_model_free(&model);
            return 1;
        }
    } else {
        presi_reset(&model, 64);
        printf("[BUS]\tcycle=%llu post-reset hreadyout=%u busy=%u\n",
               (unsigned long long) model.cycle,
               model.p.hreadyout_o & 1, model.p.busy_o & 1);
    }

    /* ---- dispatch the operation. */
    if (strcmp(op_name, "smoke") == 0) {
        rc = run_smoke(&model);
    } else if (strcmp(op_name, "mldsa-keygen") == 0) {
        rc = run_mldsa_keygen(&model, &s);
    } else if (strcmp(op_name, "run") == 0) {
        rc = run_steps(&model, &s);
    } else if (strcmp(op_name, "dump-pk") == 0) {
        rc = run_dump_pk(&model, &s);
    } else if (strcmp(op_name, "dump-sk") == 0) {
        rc = run_dump_sk(&model, &s);
    } else if (known_unimpl_op(op_name)) {
        fprintf(stderr, "[INFO]\toperation '%s' not yet wired in presi\n",
                op_name);
        rc = 3;
    } else {
        fprintf(stderr, "%s: unknown operation: %s\n", argv[0], op_name);
        rc = 2;
    }

    /* ---- optional snapshot save. */
    if (rc == 0 && s.save_fn != NULL) {
        if (save_snapshot(&model, s.save_fn) != 0) {
            rc = 1;
        }
    }

#ifndef PRESI_HAVE_NETLIST
    printf("[INFO]\tgenerated netlist C is not wired (no PRESI_HAVE_NETLIST)\n");
#endif

    presi_model_free(&model);
    return rc;
}
