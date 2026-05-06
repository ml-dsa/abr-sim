//  presi_sram.h

#ifndef PRESI_SRAM_H
#define PRESI_SRAM_H

#include <stdint.h>
#include <stddef.h>

struct presi_sram_desc;

struct presi_sram {
    const struct presi_sram_desc *desc;
    unsigned words;
    uint32_t *data;
};

int presi_sram_init(struct presi_sram *ram,
                    const struct presi_sram_desc *desc);
void presi_sram_free(struct presi_sram *ram);
void presi_sram_clear(struct presi_sram *ram);
void presi_sram_read(const struct presi_sram *ram,
                     unsigned addr, uint32_t *out);
void presi_sram_write(struct presi_sram *ram,
                      unsigned addr, const uint32_t *in);
void presi_sram_write_be(struct presi_sram *ram,
                         unsigned addr, const uint32_t *in,
                         const uint8_t *strobe);

#endif
