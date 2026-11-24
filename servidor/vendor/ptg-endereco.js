// Portgiga: endereço da rede independente a partir da chave atual, no navegador.
// chave secp256k1 = sha256("ptg-secp256k1:" + chave) mod n ; endereço = P2WPKH (bech32, prefixo "ptg").
// Escrito para a Portgiga (MIT). Só calcula a chave pública: nada é assinado aqui e a chave não sai do aparelho.
// Conferido contra servidor/derivacao.py e contra o nó portgigad (testes/test_migracao.py e teste de navegador).
(function () {
  'use strict';
  const P = 2n ** 256n - 2n ** 32n - 977n;
  const N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141n;
  const G = [0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798n,
             0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8n];
  const mod = (a, m = P) => ((a % m) + m) % m;
  function inv(a) { // exponenciação de Fermat: a^(p-2)
    let r = 1n, b = mod(a), e = P - 2n;
    while (e > 0n) { if (e & 1n) r = r * b % P; b = b * b % P; e >>= 1n; }
    return r;
  }
  function soma(p1, p2) {
    if (!p1) return p2; if (!p2) return p1;
    if (p1[0] === p2[0] && mod(p1[1] + p2[1]) === 0n) return null;
    const l = p1[0] === p2[0] ? mod(3n * p1[0] * p1[0] * inv(2n * p1[1])) : mod((p2[1] - p1[1]) * inv(p2[0] - p1[0]));
    const x = mod(l * l - p1[0] - p2[0]);
    return [x, mod(l * (p1[0] - x) - p1[1])];
  }
  function mult(k) { let r = null, a = G; while (k > 0n) { if (k & 1n) r = soma(r, a); a = soma(a, a); k >>= 1n; } return r; }
  const hex = b => [...b].map(x => x.toString(16).padStart(2, '0')).join('');
  const deHex = h => new Uint8Array(h.match(/../g).map(x => parseInt(x, 16)));
  async function sha256(bytes) { return new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)); }

  // RIPEMD-160 (padrão), em palavras de 32 bits little-endian
  function ripemd160(msg) {
    const rl = [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,7,4,13,1,10,6,15,3,12,0,9,5,2,14,11,8,3,10,14,4,9,15,8,1,2,7,0,6,13,11,5,12,1,9,11,10,0,8,12,4,13,3,7,15,14,5,6,2,4,0,5,9,7,12,2,10,14,1,3,8,11,6,15,13];
    const rr = [5,14,7,0,9,2,11,4,13,6,15,8,1,10,3,12,6,11,3,7,0,13,5,10,14,15,8,12,4,9,1,2,15,5,1,3,7,14,6,9,11,8,12,2,10,0,4,13,8,6,4,1,3,11,15,0,5,12,2,13,9,7,10,14,12,15,10,4,1,5,8,7,6,2,13,14,0,3,9,11];
    const sl = [11,14,15,12,5,8,7,9,11,13,14,15,6,7,9,8,7,6,8,13,11,9,7,15,7,12,15,9,11,7,13,12,11,13,6,7,14,9,13,15,14,8,13,6,5,12,7,5,11,12,14,15,14,15,9,8,9,14,5,6,8,6,5,12,9,15,5,11,6,8,13,12,5,12,13,14,11,8,5,6];
    const sr = [8,9,9,11,13,15,15,5,7,7,8,11,14,14,12,6,9,13,15,7,12,8,9,11,7,7,12,7,6,15,13,11,9,7,15,11,8,6,6,14,12,13,5,14,13,13,7,5,15,5,8,11,14,14,6,14,6,9,12,9,12,5,15,8,8,5,12,9,12,5,14,6,8,13,6,5,15,13,11,11];
    const kl = [0x00000000, 0x5A827999, 0x6ED9EBA1, 0x8F1BBCDC, 0xA953FD4E];
    const kr = [0x50A28BE6, 0x5C4DD124, 0x6D703EF3, 0x7A6D76E9, 0x00000000];
    const f = (j, x, y, z) => j < 16 ? x ^ y ^ z : j < 32 ? (x & y) | (~x & z) : j < 48 ? (x | ~y) ^ z : j < 64 ? (x & z) | (y & ~z) : x ^ (y | ~z);
    const rotl = (x, n) => (x << n) | (x >>> (32 - n));
    const len = msg.length, total = ((len + 8) >> 6) + 1, w = new Uint32Array(total * 16);
    for (let i = 0; i < len; i++) w[i >> 2] |= msg[i] << ((i & 3) * 8);
    w[len >> 2] |= 0x80 << ((len & 3) * 8);
    w[total * 16 - 2] = (len * 8) >>> 0; w[total * 16 - 1] = Math.floor(len / 0x20000000);
    let h0 = 0x67452301, h1 = 0xEFCDAB89, h2 = 0x98BADCFE, h3 = 0x10325476, h4 = 0xC3D2E1F0;
    for (let b = 0; b < total; b++) {
      let al = h0, bl = h1, cl = h2, dl = h3, el = h4, ar = h0, br = h1, cr = h2, dr = h3, er = h4;
      for (let j = 0; j < 80; j++) {
        let t = (rotl((al + f(j, bl, cl, dl) + w[b * 16 + rl[j]] + kl[j >> 4]) | 0, sl[j]) + el) | 0;
        al = el; el = dl; dl = rotl(cl, 10); cl = bl; bl = t;
        t = (rotl((ar + f(79 - j, br, cr, dr) + w[b * 16 + rr[j]] + kr[j >> 4]) | 0, sr[j]) + er) | 0;
        ar = er; er = dr; dr = rotl(cr, 10); cr = br; br = t;
      }
      const t = (h1 + cl + dr) | 0; h1 = (h2 + dl + er) | 0; h2 = (h3 + el + ar) | 0; h3 = (h4 + al + br) | 0; h4 = (h0 + bl + cr) | 0; h0 = t;
    }
    const out = new Uint8Array(20);
    [h0, h1, h2, h3, h4].forEach((h, i) => { for (let k = 0; k < 4; k++) out[i * 4 + k] = (h >>> (8 * k)) & 255; });
    return out;
  }

  const CHARSET = 'qpzry9x8gf2tvdw0s3jn54khce6mua7l';
  function polymod(v) {
    const g = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3]; let c = 1;
    for (const x of v) { const b = c >>> 25; c = ((c & 0x1ffffff) << 5) ^ x; for (let i = 0; i < 5; i++) if ((b >>> i) & 1) c ^= g[i]; }
    return c >>> 0;
  }
  function bech32(hrp, versao, prog) {
    const dados = [versao]; let acc = 0, bits = 0;
    for (const b of prog) { acc = (acc << 8) | b; bits += 8; while (bits >= 5) { bits -= 5; dados.push((acc >>> bits) & 31); } }
    if (bits) dados.push((acc << (5 - bits)) & 31);
    const exp = [...hrp].map(c => c.charCodeAt(0) >> 5).concat([0], [...hrp].map(c => c.charCodeAt(0) & 31));
    const chk = polymod(exp.concat(dados, [0, 0, 0, 0, 0, 0])) ^ 1;
    for (let i = 0; i < 6; i++) dados.push((chk >>> (5 * (5 - i))) & 31);
    return hrp + '1' + dados.map(d => CHARSET[d]).join('');
  }

  async function enderecoNovo(chaveHex, hrp = 'ptg') {
    const d = BigInt('0x' + hex(await sha256(new TextEncoder().encode('ptg-secp256k1:' + chaveHex)))) % N;
    if (d === 0n) throw new Error('chave inválida');
    const [x, y] = mult(d);
    const pub = deHex(((y & 1n) ? '03' : '02') + x.toString(16).padStart(64, '0'));
    return bech32(hrp, 0, ripemd160(await sha256(pub)));
  }
  window.ptgEnderecoNovo = enderecoNovo;
})();
