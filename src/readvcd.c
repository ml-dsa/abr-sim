//  readvcd.c
//  2024-11-24  Markku-Juhani O. Saarinen <mjos@iki.fi>
//  === Read a VCD file and try to create a power trace reasonably fast.

#include <stdio.h>
#include <ctype.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <stdlib.h>

#define LINE_SZ_MAX 1024
#define TOKEN_MAX   16
#define ID_SZ_MAX   8
#define SCOPE_MAX   100

typedef struct {
    char id[ID_SZ_MAX];     //  identifier
    int d;                  //  width
    int n;                  //  how many signal names
    size_t o;               //  first signal name
    size_t u;               //  how many times updated
    char *s;                //  pointer to state
} var_t;

char *signame = NULL;       //  buffer for signal names
size_t signame_sz = 0;      //  size
size_t signame_max = 0;     //  allocated


//  hash table; contains an index to *var array
#define ID_HASH_MAX (96 * 96 * 96)
size_t *id_hash = NULL;

static int id_hash3(const char *id)
{
    int a, b, c;

    a = id[0];
    if (a < 0x20 || a > 0x7F) {
        a = 0;
        b = 0;
        c = 0;
    } else {
        a -= 0x20;
        b = id[1];
        if (b < 0x20 || b > 0x7F) {
            b = 0;
            c = 0;
        } else {
            b -= 0x20;
            c = id[2];
            if (c < 0x20 || c > 0x7F) {
                c = 0;
            } else {
                c -= 0x20;
            }
        }
    }

    return (96 * a + b) * 96 + c;
}

//  comparator signal names via offs table
int offs_cmp(const void *pa, const void *pb)
{
    return strcmp( &signame[*((size_t *) pa)], &signame[*((size_t *) pb)] );
}

size_t *offs = NULL;    //  sorted offsets in signal names
size_t offs_n = 0;
size_t offs_max = 0;

//  signal states
var_t *var = NULL;
size_t var_n = 0;

int max_dim = 0;        //  largest signal width
size_t st_sz = 0;       //  total number of state bits

static var_t *find_id(const char *id)
{
    int x;
    size_t i;

    i = id_hash[id_hash3(id)];

    while (i < ID_HASH_MAX) {
        x = strcmp(var[i].id, id);
        if (x == 0)
            return &var[i];
        if (x > 0)
            return NULL;
        i++;
    }
    return NULL;
}

//  read a binary number

int64_t bin_to_int(const char *s, int d)
{
    int64_t i, x;

    x = 0;
    for (i = 0; i < d; i++) {
        if (s[i] != '0' && s[i] != '1')
            return -1;
        x = (2 * x) + (s[i] - '0');
    }

    return x;
}

char *get_signame(const var_t *v)
{
    int i;
    char *s;

    s = &signame[offs[v->o]];
    i = strlen(s);
    while (i > 0 && !isspace(s[i - 1])) {
        i--;
    }
    return &s[i];
}

int read_vcd(const char *fn, const char *timing,
                int64_t thresh, int64_t *dump_tim)
{
    FILE *fp = NULL;
    int     fail = 0;
    uint64_t line = 0;
    char    buf[LINE_SZ_MAX] = "";
    char    *tok[TOKEN_MAX];
    size_t  len[SCOPE_MAX];
    char    nam[LINE_SZ_MAX];
    char    tmp[2 * ID_SZ_MAX];

    char    *state = NULL;      //  state array
    char    *chg = NULL;        //  change line buffer

    //  read the actual changes
    int64_t tim = 0;            //  current time step
    int64_t cyc = 0, ncyc = 0;  //  cycle counter (from signals)
    int64_t hd = 0;             //  hamming distance at time step
    int64_t sd = 0;             //  hamming distance of signal
    int64_t bl = 0;             //  number of bits in time step

    bool    sigd = false;       //  dump signal changes?
    var_t   *cyc_v = NULL;      //  signal vith cycle counter

    size_t i, j, l, n;
    int x, y, k, d, scope;
    bool flag;

    char *s, *r;
    var_t *v;                   //  signal variable

    //  open file
    fp = fopen(fn, "r");
    if (fp == NULL) {
        perror(fn);
        exit(-1);
    }

    //  allocate buffers
    signame_max = 0x100000;     //  initial buffer size for signal names
    signame_sz = 0;
    signame = malloc(signame_max);
    if (signame == NULL)
        exit(-1);

    offs_max = 0x10000;         //  initiial number of signal names
    offs_n = 0;
    offs = calloc(offs_max, sizeof(size_t));
    if (offs == NULL)
        exit(-1);

    //  read the preamble
    scope = 0;
    k = 0;
    while(fgets(buf, sizeof(buf), fp) == buf) {
        line++;
        n = 0;
        flag = true;
        for (i = 0; i < (int) sizeof(buf); i++) {
            if (buf[i] == 0)
                break;
            if (isspace(buf[i])) {
                flag = true;
                buf[i] = 0;
                continue;
            }
            if (flag) {
                tok[n++] = &buf[i];
                if (n > TOKEN_MAX)
                    break;
                flag = false;
            }
        }
/*
        printf("%lu %d:", line, scope);
        for (i = 0; i < n; i++) {
            printf(" <%s>", tok[i]);
        }
        printf("\n");
*/
        if (n >= 1 && strcmp(tok[0], "$enddefinitions") == 0)
            break;
        if (n >= 3 && strcmp(tok[0], "$scope") == 0) {
            if (scope > SCOPE_MAX)
                goto wire_too_long;
            l = strlen(tok[2]);
            if (k + l + 1 >= LINE_SZ_MAX)
                goto wire_too_long;
            len[scope] = l;
            memcpy(nam + k, tok[2], l);
            k += l;
            nam[k++] = '.';
            nam[k] = 0;
            scope++;
            continue;
        }
        if (n >= 1 && strcmp(tok[0], "$upscope") == 0) {
            if (scope >= 1) {
                scope--;
                k -= len[scope] + 1;
                if (k < 0)
                    k = 0;
                nam[k] = 0;
            }
            continue;
        }
        if (n >= 6 && strcmp(tok[0], "$var") == 0) {

            d = atoi(tok[2]);
            if (d < 0)
                d = 0;
            j = k;
            l = strlen(tok[4]);
            if (j + l >= (int) sizeof(nam))
                goto wire_too_long;
            memcpy(&nam[j], tok[4], l);
            j += l;
            if (n >= ID_SZ_MAX - 1) {
                l = strlen(tok[5]);
                if (j + l >= (int) sizeof(nam))
                    goto wire_too_long;
                memcpy(&nam[j], tok[5], l);
                j += l;
            }
            nam[j] = 0;

            l = strlen(tok[3]);
            if (signame_sz + j + l + 20 >= signame_max) {
                signame_max <<= 1;
                signame = realloc(signame, signame_max);
                if (signame == NULL)
                    exit(-1);
            }

            if (offs_n + 1 >= offs_max) {
                offs_max <<= 1;
                offs = realloc(offs, offs_max * sizeof(size_t));
                if (offs == NULL)
                    exit(-1);
            }
            offs[offs_n++] = signame_sz;

            memcpy(&signame[signame_sz], tok[3], l);
            signame_sz += l;
            snprintf(tmp, sizeof(tmp), " %d ", d);
            l = strlen(tmp);
            memcpy(&signame[signame_sz], tmp, l);
            signame_sz += l;

            memcpy(&signame[signame_sz], nam, j);
            signame_sz += j;
            signame[signame_sz++] = 0;

            //  shorten the name again
            nam[k] = 0;
        }
    }

    //  sort it
    qsort(offs, offs_n, sizeof(size_t), offs_cmp);

    st_sz = 0;
    max_dim = 0;

    var_n = 0;
    var = calloc(offs_n, sizeof(var_t));

    for (i = 0; i < offs_n; i++) {
        s = &signame[offs[i]];
        for (l = 0; l < ID_SZ_MAX; l++) {
            if (s[l] == ' ' || s[l] == 0)
                break;
        }
        memcpy(tmp, s, l);
        memset(tmp + l, 0, 8 - l);
        d = atoi(&s[l]);
        if (d < 0)
            d = 0;
        if (d > max_dim)
            max_dim = d;

        if (var_n == 0 || strcmp(var[var_n-1].id, tmp) != 0) {
            memcpy(var[var_n].id, tmp, 8);
            var[var_n].n = 1;
            var[var_n].d = d;
            st_sz += d;
            var[var_n].o = i;
            var[var_n].u = 0;
            var[var_n].s = NULL;
            var_n++;
        } else {
            if (var[var_n - 1].d != d) {
                fprintf(stderr, "ERROR  Dimension mismatch: %s %d != %d\n",
                        tmp, d, var[var_n - 1].d);
            }
            var[var_n - 1].n++;
        }
    }

    //  create a hash table

    id_hash = calloc(ID_HASH_MAX, sizeof(size_t));
    if (id_hash == NULL)
        exit(-1);
    y = 0;
    j = 0;
    for (i = 0; i < var_n; i++) {
        x = id_hash3(var[i].id);
        if (x > y) {
            while (y < x) {
                id_hash[y++] = j;
            }
            id_hash[x] = i;
        }
        y = x;
        j = i;
    }
    while (y < ID_HASH_MAX) {
        id_hash[y++] = j;
    }

    printf("%s preamble: %lu lines, %lu signames, %lu ids, "
            "max var %d, tot %zu bits.\n",
            fn, line, offs_n, var_n, max_dim, st_sz);

    //  initialize state array
    state = malloc(st_sz);
    if (state == NULL)
        exit(-1);
    memset(state, 'x', st_sz);

    s = state;
    for (i = 0; i < var_n; i++) {
        var[i].s = s;
        s += var[i].d;
    }

    //  change line buffer
    max_dim += 64;
    chg = malloc(max_dim);
    if (chg == NULL)
        exit(-1);

    //  try to much the timing signal
    for (i = 0; i < var_n; i++) {
        s = get_signame(&var[i]);
        if (strstr(s, timing) != NULL) {
            printf("[info] timing signal: %s\n", s);
            cyc_v = &var[i];
            break;
        }
    }
    if (cyc_v == NULL) {
        printf("[info] timing signal not found; using ticks: %s\n", timing);
    }

    //  read the actual changes
    hd  = 0;        //  hamming distance
    tim = 0;
    cyc = -1;

    while (fgets(chg, max_dim, fp) == chg) {

        line++;
        if (chg[0] == 0 || chg[0] == '\n')
            continue;

        //  new time
        if (chg[0] == '#') {
            tim = (int64_t) atoll(&chg[1]);
            if (cyc_v == NULL) {
                ncyc    = tim;
            }
            goto new_time;
        }

        s = chg;        //  bit data
        r = s;          //  signal name
        d = 0;          //  length of bit data

        if (chg[0] == '0' || chg[0] == '1') {
            d = 1;
            s = chg;
            r = chg + 1;
        } else if (chg[0] == 'b' || chg[0] == 'B') {
            s++;
            d = 0;
            while(s[d] == '0' || s[d] == '1')
                d++;
            r = s + d;
            while(*r == ' ')
                r++;
        } else {
            fprintf(stderr, "%s:%lu ERROR  format: %s\n",
                    fn, line, chg);
            continue;
        }

        for (i = 0; i < ID_SZ_MAX - 1; i++) {
            if (isspace(r[i]))
                break;
        }
        r[i] = 0;

        v = find_id(r);
        if (v == NULL) {
            fprintf(stderr, "%s:%lu ERROR  id %s not found: %s\n",
                    fn, line, r, chg);
            continue;
        }
        if (d != v->d) {
            fprintf(stderr, "%s:%lu ERROR  wrong dimension (%d): %s\n",
                    fn, line, v->d, chg);
            continue;
        }

        if (v->u > 0) {
            sd = 0;
            for (i = 0; i < (size_t) d; i++) {
                if (v->s[i] != s[i]) {
                    sd++;
                }
                v->s[i] = s[i];
            }

            if (sigd && sd >= thresh) {
                printf("[sigd] %8ld  %ld_%s\n", sd, cyc, get_signame(v));
            }
            bl += d;
            hd += sd;
        } else {
            memcpy(v->s, s, d);
        }
        v->u++;

        //  a cycle counter signal?
        if (cyc_v != NULL && v == cyc_v) {
            ncyc    = bin_to_int(s, d);
        }

    new_time:

        if (ncyc > cyc) {
            if (cyc >= 0 && hd >= thresh) {
                printf("#%8ld [togd]  %ld\n", cyc, hd);
                hd = 0;
                bl = 0;
            }
            cyc = ncyc;

            //  is this one of the "dump cycles"
            if (dump_tim != NULL) {
                sigd = false;
                i = 0;
                while (dump_tim[i] >= 0 && !sigd) {
                    sigd = (dump_tim[i++] == cyc);
                }
            }
        }
    }
    printf("%s total: %lu lines, last time %ld  cycle %ld.\n",
        fn, line, tim, cyc);

    free(signame);
    free(offs);
    free(var);
    free(id_hash);
    free(state);
    free(chg);
    fclose(fp);

    return fail;

wire_too_long:
    fprintf(stderr, "%s:%lu  Parse error -- wire name too long.\n",
            fn, line);
    exit(0);
}

//  main

int main(int argc, char **argv)
{
    int fail = 0;
    int i, j;
    int64_t *dump_tim = NULL;
    int64_t thresh = 1;

    if (argc < 3) {
        fprintf(stderr, "Usage: readvcd <file.vcd> <time signal>"
                        " [threshold] [report cycles]\n");
        return fail;
    }
    if (argc > 3) {
        thresh = strtoll(argv[3], NULL, 0);
    }
    printf("[info] toggle threshold: %ld\n", thresh);

    if (argc > 4) {
        dump_tim = calloc(argc - 3, sizeof(int64_t));
        if (dump_tim == NULL)
            exit(-1);
        j = 0;
        for (i = 4; i < argc; i++) {
            dump_tim[j++] = strtoll(argv[i], NULL, 0);
        }
        dump_tim[j++] = -1;

        printf("[info] report cycles:");
        for (i = 0; dump_tim[i] >= 0; i++) {
            printf(" %ld", dump_tim[i]);
        }
        printf("\n");
    }

    //  read the file
    fail += read_vcd(argv[1], argv[2], thresh, dump_tim);

    if (dump_tim != NULL) {
        free(dump_tim);
    }

    return fail;
}

