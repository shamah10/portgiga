#!/usr/bin/env python3
"""Registra na rede atual o endereço da rede independente de uma carteira, a partir do arquivo da chave.

Uso: python3 ferramentas/registrar_migracao.py --chave-arquivo carteira_premios.json [--servidor http://127.0.0.1:7777]
O arquivo é um JSON com o campo "chave" (64 hex). A chave é lida e nunca impressa.
Para carteiras sem conta no site (prêmios do Arena, carteiras antigas). Quem tem conta registra pela aba Carteira.
"""
import argparse
import hashlib
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "servidor"))
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

import derivacao  # noqa: E402


def pedido(chave, carteira, quando):
    priv = Ed25519PrivateKey.from_private_bytes(hashlib.sha256(("ptg-ed25519:" + chave).encode()).digest())
    pub = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    novo = derivacao.endereco_novo(chave)
    msg = "portgiga-migrar:" + json.dumps([carteira, novo, quando], separators=(",", ":"))
    return {"carteira": carteira, "endereco_novo": novo, "quando": quando, "pub": pub, "assinatura": priv.sign(msg.encode()).hex()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chave-arquivo", required=True)
    ap.add_argument("--servidor", default="http://127.0.0.1:7777")
    a = ap.parse_args()
    with open(a.chave_arquivo, encoding="utf-8") as fh:
        dados = json.load(fh)
    chave = dados.get("chave", "")
    if len(chave) != 64 or any(c not in "0123456789abcdef" for c in chave):
        sys.exit("arquivo sem uma chave válida (campo 'chave' com 64 hex)")
    carteira = dados.get("endereco")
    if not carteira:
        sys.exit("arquivo sem o campo 'endereco' (a carteira ptg1... atual)")
    corpo = pedido(chave, carteira, int(time.time()))
    r = urllib.request.Request(a.servidor + "/api/migrar", data=json.dumps(corpo).encode(), headers={"Content-Type": "application/json"})
    try:
        resp = json.load(urllib.request.urlopen(r))
    except urllib.error.HTTPError as e:
        resp = json.load(e)
    if not resp.get("ok"):
        sys.exit("recusado: %s" % resp.get("erro"))
    print("registrado: %s -> %s" % (carteira, corpo["endereco_novo"]))


if __name__ == "__main__":
    main()
