# -*- coding: utf-8 -*-
"""Endereço da Portgiga na rede independente (nó derivado do Bitcoin Core) a partir da chave atual.

A mesma chave secreta (64 hex) continua valendo: chave secp256k1 = sha256("ptg-secp256k1:" + chave) mod n,
e o endereço é P2WPKH (SegWit v0) em bech32 com prefixo "ptg" -> "ptg1q...".
A página faz a mesma conta em JavaScript; os testes conferem as duas.
"""
import hashlib

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

N_SECP256K1 = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
HRP = "ptg"
_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def _polymod(valores):
    g = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3]
    chk = 1
    for v in valores:
        b = chk >> 25
        chk = (chk & 0x1ffffff) << 5 ^ v
        for i in range(5):
            chk ^= g[i] if ((b >> i) & 1) else 0
    return chk


def _expandir(hrp):
    return [ord(x) >> 5 for x in hrp] + [0] + [ord(x) & 31 for x in hrp]


def _converter(dados, de, para, completar=True):
    acc = bits = 0
    out = []
    maxv = (1 << para) - 1
    for v in dados:
        if v < 0 or v >> de:
            return None
        acc = (acc << de) | v
        bits += de
        while bits >= para:
            bits -= para
            out.append((acc >> bits) & maxv)
    if completar and bits:
        out.append((acc << (para - bits)) & maxv)
    elif not completar and (bits >= de or ((acc << (para - bits)) & maxv)):
        return None
    return out


def codificar(hrp, versao, programa):
    dados = [versao] + _converter(programa, 8, 5)
    chk = _polymod(_expandir(hrp) + dados + [0] * 6) ^ 1
    return hrp + "1" + "".join(_CHARSET[d] for d in dados + [(chk >> 5 * (5 - i)) & 31 for i in range(6)])


def decodificar(endereco):
    """(hrp, versão, programa) de um endereço bech32 SegWit v0, ou None."""
    if not isinstance(endereco, str) or endereco.lower() != endereco or len(endereco) > 90:
        return None
    pos = endereco.rfind("1")
    if pos < 1 or pos + 7 > len(endereco):
        return None
    hrp, resto = endereco[:pos], endereco[pos + 1:]
    if any(c not in _CHARSET for c in resto):
        return None
    dados = [_CHARSET.find(c) for c in resto]
    if _polymod(_expandir(hrp) + dados) != 1:
        return None
    programa = _converter(dados[1:-6], 5, 8, False)
    if programa is None or dados[0] != 0 or len(programa) not in (20, 32):
        return None
    return hrp, 0, bytes(programa)


def endereco_valido(endereco, hrp=HRP):
    d = decodificar(endereco)
    return bool(d) and d[0] == hrp and len(d[2]) == 20


def endereco_novo(chave_hex, hrp=HRP):
    d = int.from_bytes(hashlib.sha256(("ptg-secp256k1:" + chave_hex).encode()).digest(), "big") % N_SECP256K1
    if d == 0:
        raise ValueError("chave inválida")
    pub = ec.derive_private_key(d, ec.SECP256K1()).public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.CompressedPoint)
    return codificar(hrp, 0, hashlib.new("ripemd160", hashlib.sha256(pub).digest()).digest())
