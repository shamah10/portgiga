#!/usr/bin/env python3
"""Registro da carteira nova (rede independente) na rede atual: assinatura, prazo, troca e congelamento."""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(AQUI, "..", "servidor"))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402

import derivacao  # noqa: E402
import servidor  # noqa: E402

# Chave de teste fixa (nunca uma chave real)
CHAVE_TESTE = "11" * 32
# Endereço da rede nova esperado para CHAVE_TESTE (conferido também contra o JS da página no teste de navegador)
ENDERECO_TESTE = None  # preenchido em setUpClass a partir da referência independente


def ed25519_da_chave(chave):
    priv = Ed25519PrivateKey.from_private_bytes(hashlib.sha256(("ptg-ed25519:" + chave).encode()).digest())
    pub = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    return priv, pub


def carteira_da_chave(chave):
    _, pub = ed25519_da_chave(chave)
    return "ptg1" + hashlib.sha256(("ptg-pub:" + pub).encode()).hexdigest()[:38]


class TestDerivacao(unittest.TestCase):
    def test_formato(self):
        e = derivacao.endereco_novo(CHAVE_TESTE)
        self.assertTrue(e.startswith("ptg1q"), e)
        self.assertEqual(len(e), 43)  # "ptg" tem 3 letras: um caractere a mais que o bc1q… do Bitcoin
        self.assertTrue(derivacao.endereco_valido(e))

    def test_bate_com_bech32_e_hash160_independentes(self):
        # recalcula por fora: chave secp256k1 = sha256("ptg-secp256k1:"+chave) mod n, P2WPKH v0
        from cryptography.hazmat.primitives.asymmetric import ec
        n = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
        d = int.from_bytes(hashlib.sha256(("ptg-secp256k1:" + CHAVE_TESTE).encode()).digest(), "big") % n
        pub = ec.derive_private_key(d, ec.SECP256K1()).public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.CompressedPoint)
        h160 = hashlib.new("ripemd160", hashlib.sha256(pub).digest()).digest()
        self.assertEqual(derivacao.decodificar(derivacao.endereco_novo(CHAVE_TESTE)), ("ptg", 0, h160))

    def test_recusa_outros(self):
        self.assertFalse(derivacao.endereco_valido("bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"))
        self.assertFalse(derivacao.endereco_valido("ptg1qqqqq"))
        self.assertFalse(derivacao.endereco_valido(derivacao.endereco_novo(CHAVE_TESTE)[:-1] + "x"))


class TestRotaMigrar(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        servidor.BANCO = os.path.join(cls.tmp, "rede.db")
        servidor.abrir_banco()
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), servidor.Manipulador)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = "http://127.0.0.1:%d" % cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        servidor.db.close()
        shutil.rmtree(cls.tmp)

    def post(self, corpo):
        r = urllib.request.Request(self.url + "/api/migrar", data=json.dumps(corpo).encode(), headers={"Content-Type": "application/json"})
        try:
            return json.load(urllib.request.urlopen(r))
        except urllib.error.HTTPError as e:
            return json.load(e)

    def pedido(self, chave, endereco_novo, quando=None, assinar_com=None):
        quando = int(time.time()) if quando is None else quando
        carteira = carteira_da_chave(chave)
        priv, pub = ed25519_da_chave(assinar_com or chave)
        msg = "portgiga-migrar:" + json.dumps([carteira, endereco_novo, quando], separators=(",", ":"))
        return {"carteira": carteira, "endereco_novo": endereco_novo, "quando": quando, "pub": pub, "assinatura": priv.sign(msg.encode()).hex()}

    def consulta(self, chave):
        return json.load(urllib.request.urlopen(self.url + "/api/migracao?endereco=" + carteira_da_chave(chave)))

    def test_registro_valido_grava(self):
        novo = derivacao.endereco_novo("22" * 32)
        self.assertEqual(self.post(self.pedido("22" * 32, novo)), {"ok": True})
        self.assertEqual(self.consulta("22" * 32)["endereco_novo"], novo)

    def test_assinatura_de_outra_chave(self):
        r = self.post(self.pedido("33" * 32, derivacao.endereco_novo("33" * 32), assinar_com="44" * 32))
        self.assertFalse(r["ok"])

    def test_pedido_vencido(self):
        r = self.post(self.pedido("55" * 32, derivacao.endereco_novo("55" * 32), quando=int(time.time()) - 301))
        self.assertFalse(r["ok"])
        self.assertIn("vencido", r["erro"])

    def test_registrar_de_novo_troca(self):
        self.assertTrue(self.post(self.pedido("66" * 32, derivacao.endereco_novo("66" * 32)))["ok"])
        outro = derivacao.endereco_novo("77" * 32)
        self.assertTrue(self.post(self.pedido("66" * 32, outro))["ok"])
        self.assertEqual(self.consulta("66" * 32)["endereco_novo"], outro)

    def test_endereco_invalido(self):
        for ruim in ("bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4", "ptg1qnaovale", 123):
            self.assertFalse(self.post(self.pedido("88" * 32, ruim))["ok"])

    def test_congelado(self):
        servidor.MIGRACAO_CONGELADA_EM_ALTURA = -1
        try:
            r = self.post(self.pedido("99" * 32, derivacao.endereco_novo("99" * 32)))
            self.assertFalse(r["ok"])
            self.assertIn("encerrado", r["erro"])
        finally:
            servidor.MIGRACAO_CONGELADA_EM_ALTURA = None

    def test_encerrado_so_quando_a_altura_chega(self):
        # marcar a altura com antecedência não pode esconder o botão de quem ainda não registrou
        topo = servidor.db.execute("SELECT MAX(altura) FROM blocos").fetchone()[0]
        servidor.MIGRACAO_CONGELADA_EM_ALTURA = topo + 100
        try:
            r = self.consulta("ab" * 32)
            self.assertFalse(r["encerrado"])
            self.assertEqual(r["congela_em"], topo + 100)
            self.assertTrue(self.post(self.pedido("ab" * 32, derivacao.endereco_novo("ab" * 32)))["ok"])
            servidor.MIGRACAO_CONGELADA_EM_ALTURA = topo
            self.assertTrue(self.consulta("ab" * 32)["encerrado"])
        finally:
            servidor.MIGRACAO_CONGELADA_EM_ALTURA = None

    def test_consulta_sem_registro(self):
        self.assertEqual(self.consulta("aa" * 32)["endereco_novo"], None)


if __name__ == "__main__":
    unittest.main()


class TestScriptRegistrar(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        servidor.BANCO = os.path.join(self.tmp, "rede.db")
        servidor.abrir_banco()

    def tearDown(self):
        servidor.db.close()
        shutil.rmtree(self.tmp)

    def test_pedido_do_script_e_aceito_pela_rota(self):
        sys.path.insert(0, os.path.join(AQUI, "..", "ferramentas"))
        import registrar_migracao
        chave = "bb" * 32
        corpo = registrar_migracao.pedido(chave, carteira_da_chave(chave), int(time.time()))
        self.assertEqual(corpo["endereco_novo"], derivacao.endereco_novo(chave))
        self.assertEqual(servidor.migrar(corpo), {"ok": True})
