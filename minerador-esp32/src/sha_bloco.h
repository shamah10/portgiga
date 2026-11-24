// SHA-256 de uma mensagem curta (ate 55 bytes = um bloco so), feito para a mineracao da Portgiga:
// os 40 primeiros bytes (a base do trabalho) sao fixos, so os digitos do numero mudam a cada tentativa.
#include <stdint.h>
#include <string.h>
#ifndef IRAM_ATTR
#define IRAM_ATTR
#endif
static const uint32_t K256[64] = {
  0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,
  0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
  0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,
  0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
  0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,
  0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2};
#define ROR(x, n) (((x) >> (n)) | ((x) << (32 - (n))))

// msg: bloco de 64 bytes ja montado (mensagem + 0x80 + zeros + tamanho em bits). out: as 8 palavras do hash.
static void IRAM_ATTR sha256Bloco(const uint8_t *b, uint32_t out[8]) {
  uint32_t w[64];
  for (int i = 0; i < 16; i++) w[i] = ((uint32_t)b[4 * i] << 24) | ((uint32_t)b[4 * i + 1] << 16) | ((uint32_t)b[4 * i + 2] << 8) | b[4 * i + 3];
  for (int i = 16; i < 64; i++) {
    uint32_t s0 = ROR(w[i - 15], 7) ^ ROR(w[i - 15], 18) ^ (w[i - 15] >> 3);
    uint32_t s1 = ROR(w[i - 2], 17) ^ ROR(w[i - 2], 19) ^ (w[i - 2] >> 10);
    w[i] = w[i - 16] + s0 + w[i - 7] + s1;
  }
  uint32_t a = 0x6a09e667, bb = 0xbb67ae85, c = 0x3c6ef372, d = 0xa54ff53a, e = 0x510e527f, f = 0x9b05688c, g = 0x1f83d9ab, h = 0x5be0cd19;
  for (int i = 0; i < 64; i++) {
    uint32_t t1 = h + (ROR(e, 6) ^ ROR(e, 11) ^ ROR(e, 25)) + ((e & f) ^ (~e & g)) + K256[i] + w[i];
    uint32_t t2 = (ROR(a, 2) ^ ROR(a, 13) ^ ROR(a, 22)) + ((a & bb) ^ (a & c) ^ (bb & c));
    h = g; g = f; f = e; e = d + t1; d = c; c = bb; bb = a; a = t1 + t2;
  }
  out[0] = 0x6a09e667 + a; out[1] = 0xbb67ae85 + bb; out[2] = 0x3c6ef372 + c; out[3] = 0xa54ff53a + d;
  out[4] = 0x510e527f + e; out[5] = 0x9b05688c + f; out[6] = 0x1f83d9ab + g; out[7] = 0x5be0cd19 + h;
}

// monta o bloco para base + numero (em decimal) e devolve o tamanho da mensagem
static int IRAM_ATTR montarBloco(uint8_t *b, int lb, uint32_t n) {
  char dig[11];
  int k = 0;
  do { dig[k++] = '0' + n % 10; n /= 10; } while (n);
  int L = lb;
  while (k) b[L++] = dig[--k];
  memset(b + L, 0, 64 - L);
  b[L] = 0x80;
  b[62] = (L * 8) >> 8;
  b[63] = (L * 8) & 0xff;
  return L;
}
