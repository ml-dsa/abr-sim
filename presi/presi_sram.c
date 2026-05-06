//  presi_sram.c

#include <stdlib.h>
#include <string.h>

#include "abr_wrap.sram.h"
#include "presi_sram.h"

int presi_sram_init(struct presi_sram *ram,
                    const struct presi_sram_desc *desc)
{
    size_t count;

    ram->desc = desc;
    ram->words = (desc->data_width + 31) / 32;
    count = (size_t) desc->depth * ram->words;
    ram->data = (uint32_t *) calloc(count, sizeof(uint32_t));
    return ram->data == NULL ? -1 : 0;
}

void presi_sram_free(struct presi_sram *ram)
{
    free(ram->data);
    ram->data = NULL;
    ram->desc = NULL;
    ram->words = 0;
}

void presi_sram_clear(struct presi_sram *ram)
{
    memset(ram->data, 0,
           (size_t) ram->desc->depth * ram->words * sizeof(uint32_t));
}

void presi_sram_read(const struct presi_sram *ram,
                     unsigned addr, uint32_t *out)
{
    if (addr >= ram->desc->depth) {
        memset(out, 0, (size_t) ram->words * sizeof(uint32_t));
        return;
    }
    memcpy(out, &ram->data[(size_t) addr * ram->words],
           (size_t) ram->words * sizeof(uint32_t));
}

void presi_sram_write(struct presi_sram *ram,
                      unsigned addr, const uint32_t *in)
{
    if (addr >= ram->desc->depth) {
        return;
    }
    memcpy(&ram->data[(size_t) addr * ram->words], in,
           (size_t) ram->words * sizeof(uint32_t));
}

void presi_sram_write_be(struct presi_sram *ram,
                         unsigned addr, const uint32_t *in,
                         const uint8_t *strobe)
{
    uint8_t *dst;
    const uint8_t *src;
    unsigned bytes;
    unsigned i;

    if (addr >= ram->desc->depth) {
        return;
    }

    bytes = ram->desc->data_width / 8;
    dst = (uint8_t *) &ram->data[(size_t) addr * ram->words];
    src = (const uint8_t *) in;

    for (i = 0; i < bytes; i++) {
        if (strobe[i]) {
            dst[i] = src[i];
        }
    }
}
