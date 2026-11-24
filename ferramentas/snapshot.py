#!/usr/bin/env python3
"""Foto dos saldos da rede atual para o bloco 1 da rede independente (Portgiga Core).

Uso:
  python3 ferramentas/snapshot.py --banco servidor/dados/rede.db --altura H --lancamento AAAA-MM-DD \\
      --rede principal --dono ptg1374…,ptg1b0513… --dono-pub <chave pública secp256k1 comprimida do dono> --saida snapshot.json

Regras (documento de projeto 2026-11-13-rede-independente-design.md):
- H tem que ser a altura atual do banco (rede atual já parada nesse bloco).
- Toda carteira com saldo precisa ter registrado o endereço novo (/api/migrar); senão, erro listando quem falta.
- Carteiras do dono somadas: 10% livres (piso) para o endereço novo da primeira; 90% em 48 parcelas mensais iguais
  (resto na última), cada uma gastável a partir de lançamento + n meses às 00:00 UTC, no script
  wsh(and_v(v:pk(DONO),after(DATA))).
- Saídas: livres em ordem alfabética do endereço novo, depois parcelas 1..48. sha256 do JSON canônico sem o campo.
O formato é lido por contrib/portgiga/gerar_header_snapshot.py (portgiga-core).
"""
import argparse
import calendar
import datetime
import hashlib
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "servidor"))
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402

import derivacao  # noqa: E402

PARCELAS = 48
HRP = {"principal": "ptg", "teste": "tptg", "regtest": "bcrt"}


class ErroSnapshot(Exception):
    pass


def pub_secp(chave_hex):
    """Chave pública secp256k1 comprimida (hex) da carteira na rede nova (mesma derivação de derivacao.py)."""
    d = int.from_bytes(hashlib.sha256(("ptg-secp256k1:" + chave_hex).encode()).digest(), "big") % derivacao.N_SECP256K1
    return ec.derive_private_key(d, ec.SECP256K1()).public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.CompressedPoint).hex()


def push_numero(n):
    """Número inteiro como push mínimo de script (CScriptNum), como o Bitcoin Core codifica after(N)."""
    corpo = n.to_bytes((n.bit_length() + 8) // 8, "little")  # sobra um bit para o sinal
    while len(corpo) > 1 and corpo[-1] == 0 and not (corpo[-2] & 0x80):
        corpo = corpo[:-1]
    return bytes([len(corpo)]) + corpo


def script_wpkh(endereco, hrp):
    d = derivacao.decodificar(endereco)
    if not d or d[0] != hrp or len(d[2]) != 20:
        raise ErroSnapshot("endereço novo inválido: %s" % endereco)
    return "0014" + d[2].hex()


def script_parcela(pub_hex, desde):
    ws = bytes([33]) + bytes.fromhex(pub_hex) + b"\xad" + push_numero(desde) + b"\xb1"  # <K> CHECKSIGVERIFY <T> CLTV
    return "0020" + hashlib.sha256(ws).hexdigest()


def mais_meses(data, n):
    m = data.month - 1 + n
    return calendar.timegm(datetime.date(data.year + m // 12, m % 12 + 1, data.day).timetuple())


def canonico(dados):
    return json.dumps({k: v for k, v in dados.items() if k != "sha256"}, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def gerar(banco, altura, lancamento, rede, dono, dono_pub):
    hrp = HRP[rede]
    db = sqlite3.connect("file:%s?mode=ro" % banco, uri=True)
    try:
        topo = db.execute("SELECT MAX(altura) FROM blocos").fetchone()[0]
        if topo != altura:
            raise ErroSnapshot("a altura pedida (%d) não é a atual do banco (%s): pare a rede no bloco do snapshot" % (altura, topo))
        saldos = dict(db.execute("SELECT endereco, saldo FROM saldos WHERE saldo > 0"))
        novos = dict(db.execute("SELECT carteira, endereco_novo FROM migracoes"))
    finally:
        db.close()
    faltam = sorted(e for e in saldos if e not in novos)
    if faltam:
        raise ErroSnapshot("carteiras com saldo sem registro na rede nova: " + ", ".join(faltam))
    if not dono or dono[0] not in saldos:
        raise ErroSnapshot("a primeira carteira do dono precisa ter saldo")
    novo_dono = novos[dono[0]]
    if derivacao.codificar(hrp, 0, hashlib.new("ripemd160", hashlib.sha256(bytes.fromhex(dono_pub)).digest()).digest()) != novo_dono:
        raise ErroSnapshot("--dono-pub não corresponde ao endereço novo registrado pela carteira do dono")

    livres = {}
    for carteira, valor in saldos.items():
        if carteira not in dono:
            livres[novos[carteira]] = livres.get(novos[carteira], 0) + valor
    total_dono = sum(saldos.get(c, 0) for c in dono)
    parte_livre = total_dono * 10 // 100
    livres[novo_dono] = livres.get(novo_dono, 0) + parte_livre

    saidas = [{"tipo": "livre", "endereco": e, "valor": v, "script": script_wpkh(e, hrp), "descritor": "addr(%s)" % e, "parcela": None, "desde": None}
              for e, v in sorted(livres.items()) if v > 0]
    travado = total_dono - parte_livre
    base = datetime.datetime.strptime(lancamento, "%Y-%m-%d").date()
    for n in range(1, PARCELAS + 1):
        valor = travado // PARCELAS if n < PARCELAS else travado - (PARCELAS - 1) * (travado // PARCELAS)
        desde = mais_meses(base, n)
        saidas.append({"tipo": "parcela", "endereco": None, "valor": valor, "script": script_parcela(dono_pub, desde),
                       "descritor": "wsh(and_v(v:pk(%s),after(%d)))" % (dono_pub, desde), "parcela": n, "desde": desde})
    if sum(s["valor"] for s in saidas) != sum(saldos.values()):
        raise ErroSnapshot("a soma das saídas não bate com a soma dos saldos")
    dados = {"rede": rede, "altura_antiga": altura, "lancamento": lancamento, "saidas": saidas}
    dados["sha256"] = hashlib.sha256(canonico(dados)).hexdigest()
    return dados


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--banco", required=True)
    ap.add_argument("--altura", type=int, required=True)
    ap.add_argument("--lancamento", required=True)
    ap.add_argument("--rede", choices=sorted(HRP), required=True)
    ap.add_argument("--dono", required=True, help="carteiras do dono separadas por vírgula (a primeira recebe a parte livre)")
    ap.add_argument("--dono-pub", required=True)
    ap.add_argument("--saida", required=True)
    a = ap.parse_args()
    try:
        dados = gerar(a.banco, a.altura, a.lancamento, a.rede, a.dono.split(","), a.dono_pub)
    except ErroSnapshot as e:
        sys.exit("erro: %s" % e)
    with open(a.saida, "w", encoding="utf-8") as fh:
        json.dump(dados, fh, ensure_ascii=False, indent=1)
    print("snapshot: %d saídas, total %.8f PTG, sha256 %s" % (len(dados["saidas"]), sum(s["valor"] for s in dados["saidas"]) / 1e8, dados["sha256"]))


if __name__ == "__main__":
    main()
