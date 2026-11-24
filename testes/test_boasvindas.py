#!/usr/bin/env python3
"""Presente de boas-vindas, convites e contador de usuarios de fora."""
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(AQUI, "..", "servidor"))

import boasvindas  # noqa: E402
import servidor  # noqa: E402

U = servidor.UNIDADE


def carteira_nova():
    chave = os.urandom(32).hex()
    return servidor.endereco_do_pub(servidor.pub_da_chave(chave))


def minerar(carteira, ip):
    """Uma resposta de mineracao certa, como um celular faria."""
    servidor.mineradores.pop((carteira, "cel"), None)
    t = servidor.novo_trabalho(carteira, "celular", "cel", ip)
    assert "trabalho" in t, t
    n = servidor.trabalhos[t["trabalho"]]["n"]
    servidor.trabalhos[t["trabalho"]]["em"] -= 5   # finge que levou 5 s (senao e "rapido demais")
    r = servidor.entregar(t["trabalho"], n)
    assert r["aceito"], r


class TestBoasVindas(unittest.TestCase):
    def setUp(self):
        self._min, boasvindas.MIN_RESPOSTAS_PRESENTE = boasvindas.MIN_RESPOSTAS_PRESENTE, 1   # testes antigos: 1 resposta basta
        self.tmp = tempfile.mkdtemp()
        servidor.BANCO = os.path.join(self.tmp, "rede.db")
        boasvindas.CARTEIRA_ARQ = os.path.join(self.tmp, "fundo.json")
        servidor.transferencias.clear(); servidor.mineradores.clear(); servidor.trabalhos.clear()
        servidor.rodada.clear(); servidor.rodada_min.clear()
        boasvindas.decididos.clear(); boasvindas.visto_em.clear(); boasvindas.chegadas_ip.clear()
        servidor.contagem_ip.clear()
        servidor.abrir_banco()
        self.antiga = carteira_nova()
        servidor.creditar(self.antiga, 5 * U)       # carteira que ja existia antes do recurso
        servidor.db.commit()
        servidor.db.execute("DROP TABLE chegadas")   # simula a 1a vez: as que existem viram "antigas"
        fundo = boasvindas.criar_carteira_fundo()
        boasvindas.preparar(servidor)
        servidor.creditar(fundo["endereco"], 1000 * U)
        servidor.db.commit()
        self.fundo = fundo["endereco"]

    def tearDown(self):
        boasvindas.MIN_RESPOSTAS_PRESENTE = self._min
        servidor.db.close()
        shutil.rmtree(self.tmp)

    def fechar(self):
        servidor.fechar_bloco()

    def test_presente_na_primeira_mineracao(self):
        c = carteira_nova()
        self.assertTrue(boasvindas.registrar({"endereco": c, "origem": "tiktok"}, "200.1.2.3")["pode_ganhar"])
        minerar(c, "200.1.2.3")
        minerar(c, "200.1.2.3")   # a 2a nao paga de novo
        self.fechar()
        self.assertEqual(servidor.saldo(self.fundo), 990 * U)
        self.assertGreaterEqual(servidor.saldo(c), 10 * U)
        s = boasvindas.situacao(c)
        self.assertTrue(s["ganhou"]); self.assertFalse(s["pode_ganhar"])
        p = boasvindas.painel()
        self.assertEqual(p["total_fora"], 1); self.assertEqual(p["mineraram"], 1)
        self.assertEqual(p["origens"][0]["origem"], "tiktok")

    def test_presente_so_depois_de_minerar_um_tempo(self):
        boasvindas.MIN_RESPOSTAS_PRESENTE = 3
        c = carteira_nova()
        boasvindas.registrar({"endereco": c}, "200.1.2.4")
        def resposta():                       # o mesmo aparelho minerando (sem zerar o contador dele)
            t = servidor.novo_trabalho(c, "celular", "cel2", "200.1.2.4")
            servidor.trabalhos[t["trabalho"]]["em"] -= 5
            self.assertTrue(servidor.entregar(t["trabalho"], servidor.trabalhos[t["trabalho"]]["n"])["aceito"])
        resposta(); resposta()
        self.fechar()
        self.assertEqual(servidor.saldo(self.fundo), 1000 * U)      # 2 respostas: ainda não
        resposta()
        self.fechar()
        self.assertEqual(servidor.saldo(self.fundo), 990 * U)       # 3ª resposta: presente

    def test_convite_paga_quem_convidou(self):
        amigo = carteira_nova()
        boasvindas.registrar({"endereco": amigo}, "200.9.9.9")
        c = carteira_nova()
        boasvindas.registrar({"endereco": c, "origem": "convite", "convite": amigo}, "201.1.1.1")
        minerar(c, "201.1.1.1")
        self.fechar()
        self.assertEqual(servidor.saldo(amigo), 10 * U)
        self.assertEqual(servidor.saldo(self.fundo), 980 * U)
        self.assertEqual(boasvindas.situacao(amigo)["convites_pagos"], 1)

    def test_convite_do_mesmo_ip_nao_paga(self):
        amigo = carteira_nova()
        boasvindas.registrar({"endereco": amigo}, "201.1.1.1")
        c = carteira_nova()
        boasvindas.registrar({"endereco": c, "convite": amigo}, "201.1.1.1")
        minerar(c, "201.1.1.1")
        self.fechar()
        self.assertEqual(servidor.saldo(amigo), 0)
        self.assertEqual(servidor.saldo(self.fundo), 990 * U)

    def test_carteira_antiga_e_casa_nao_ganham(self):
        minerar(self.antiga, "200.5.5.5")
        c = carteira_nova()
        minerar(c, "10.0.0.50")          # Wi-Fi de casa
        self.fechar()
        self.assertEqual(servidor.saldo(self.fundo), 1000 * U)
        p = boasvindas.painel()
        self.assertEqual(p["total_fora"], 0); self.assertEqual(p["da_casa"], 1)

    def test_limite_por_ip(self):
        for _ in range(boasvindas.MAX_BONUS_IP_DIA + 2):
            minerar(carteira_nova(), "200.7.7.7")
        self.fechar()
        self.assertEqual(servidor.saldo(self.fundo), (1000 - 10 * boasvindas.MAX_BONUS_IP_DIA) * U)

    def test_fundo_vazio_nao_quebra(self):
        servidor.creditar(self.fundo, -995 * U); servidor.db.commit()
        c = carteira_nova()
        minerar(c, "200.8.8.8")
        self.fechar()
        self.assertEqual(servidor.saldo(self.fundo), 5 * U)
        self.assertFalse(boasvindas.situacao(c)["ativo"])

    def test_corrente_confere(self):
        """As transferencias do presente sao assinadas de verdade: o verificar.py refaz e confere a corrente."""
        c = carteira_nova()
        minerar(c, "200.1.2.3")
        self.fechar()
        import verificar
        txs = [t for b in servidor.cadeia(0, None)["blocos"] for t in b["dados"].get("transferencias", []) if t["de"] == self.fundo]
        self.assertEqual(len(txs), 1)
        t = txs[0]
        msg = "portgiga-tx:" + json.dumps([t["de"], t["para"], t["unidades"], t["nonce"]], separators=(",", ":"))
        self.assertTrue(verificar.assinatura_ok(t["pub"], msg, t["assinatura"]))
        self.assertEqual(verificar.endereco_do_pub(t["pub"]), self.fundo)


class TestRotas(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        servidor.BANCO = os.path.join(cls.tmp, "rede.db")
        boasvindas.CARTEIRA_ARQ = os.path.join(cls.tmp, "nao-existe.json")
        servidor.abrir_banco()
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), servidor.Manipulador)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = "http://127.0.0.1:%d" % cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        servidor.db.close()
        shutil.rmtree(cls.tmp)

    def pedir(self, caminho, corpo=None, cab=None):
        req = urllib.request.Request(self.url + caminho, data=json.dumps(corpo).encode() if corpo is not None else None, headers=cab or {})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, None

    def test_chegada_e_situacao(self):
        c = carteira_nova()
        st, r = self.pedir("/api/chegada", {"endereco": c, "origem": "YouTube"}, {"CF-Connecting-IP": "200.3.3.3"})
        self.assertEqual(st, 200); self.assertTrue(r["ok"]); self.assertFalse(r["ativo"])   # sem fundo
        st, r = self.pedir("/api/boas-vindas?endereco=" + c)
        self.assertEqual(r["convidados"], 0)
        st, p = self.pedir("/api/dono/usuarios")
        self.assertEqual(p["total_fora"], 1); self.assertEqual(p["origens"][0]["origem"], "youtube")

    def test_painel_so_no_pc(self):
        st, _ = self.pedir("/api/dono/usuarios", cab={"CF-Connecting-IP": "200.3.3.3"})
        self.assertEqual(st, 403)
        st, _ = self.pedir("/dono/usuarios", cab={"CF-Connecting-IP": "200.3.3.3"})
        self.assertEqual(st, 403)


if __name__ == "__main__":
    unittest.main()
