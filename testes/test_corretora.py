#!/usr/bin/env python3
"""Corretora Portgiga: curva de preço, depósito, criar moeda, comprar, vender, taxas, travas, banimento e saque."""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(AQUI, "..", "servidor"))

import corretora as cx  # noqa: E402
import servidor  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402

U = servidor.UNIDADE
CARTEIRA_ORIGINAL = cx.CARTEIRA_ARQ


class Pessoa:
    def __init__(self, nome):
        self.chave = hashlib.sha256(nome.encode()).hexdigest()
        self.priv = Ed25519PrivateKey.from_private_bytes(hashlib.sha256(("ptg-ed25519:" + self.chave).encode()).digest())
        self.pub = self.priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
        self.end = servidor.endereco_do_pub(self.pub)

    def pedido(self, prefixo, campos, **extra):
        quando = int(time.time())
        msg = prefixo + json.dumps([self.end] + campos + [quando], separators=(",", ":"), ensure_ascii=False)
        return {"carteira": self.end, "quando": quando, "pub": self.pub, "assinatura": self.priv.sign(msg.encode()).hex(), **extra}

    def depositar(self, ptg):
        """Ganha PTG na rede e manda para a corretora (como o usuário faria pela página)."""
        with servidor.trava:
            servidor.creditar(self.end, ptg * U)
            servidor.db.commit()
        nonce = servidor.proximo_nonce(self.end)
        msg = "portgiga-tx:" + json.dumps([self.end, cx.CX["endereco"], ptg * U, nonce], separators=(",", ":"))
        r = servidor.enviar({"de": self.end, "para": cx.CX["endereco"], "unidades": ptg * U, "nonce": nonce, "pub": self.pub,
                             "assinatura": self.priv.sign(msg.encode()).hex()})
        assert r["ok"], r
        servidor.fechar_bloco()
        cx.ler_depositos()

    def criar(self, simbolo, inicial=0, nome="Moeda Teste"):
        campos = [simbolo, nome, "uma moeda de teste", "🚀", "#22c55e", inicial]
        return cx.criar(self.pedido("portgiga-cx-criar:", campos, simbolo=simbolo, nome=nome, descricao="uma moeda de teste",
                                    emoji="🚀", cor="#22c55e", compra_inicial=inicial))

    def comprar(self, simbolo, ptg, minimo=0):
        return cx.comprar(self.pedido("portgiga-cx-comprar:", [simbolo, ptg, minimo], simbolo=simbolo, ptg=ptg, minimo=minimo))

    def vender(self, simbolo, qtd, minimo=0):
        return cx.vender(self.pedido("portgiga-cx-vender:", [simbolo, qtd, minimo], simbolo=simbolo, qtd=qtd, minimo=minimo))

    def sacar(self, unidades):
        return cx.sacar(self.pedido("portgiga-cx-sacar:", [unidades], unidades=unidades))


class TestCurva(unittest.TestCase):
    def test_compra_sobe_o_preco_e_venda_devolve_no_maximo_o_que_entrou(self):
        v, oferta = 1000 * U, 10 ** 9 * U
        qtd, nova = cx.comprar_na_curva(v, 0, oferta, 100 * U)
        self.assertGreater(qtd, 0)
        self.assertGreater(cx.preco(v, 100 * U, nova), cx.preco(v, 0, oferta))
        volta = cx.vender_na_curva(v, 100 * U, nova, qtd)
        self.assertLessEqual(volta, 100 * U)
        self.assertGreater(volta, 99 * U)

    def test_venda_nunca_passa_da_reserva_real(self):
        self.assertEqual(cx.vender_na_curva(1000 * U, 5 * U, 10 ** 9 * U, 10 ** 9 * U), 5 * U)

    def test_taxas_em_pontos_base(self):
        self.assertEqual(cx.taxas(1000 * U, {"taxa_dono_pct": 1.0, "taxa_criador_pct": 0.25}), (10 * U, int(2.5 * U)))


class TestForcaEVelas(unittest.TestCase):
    def test_forca_vai_de_0_a_100_e_tem_rotulo(self):
        fraca = cx.nota_forca(0.0, -60, 0, 0, 10)
        forte = cx.nota_forca(1.0, 40, 30, 5000, 1)
        self.assertLess(fraca["nota"], 20)
        self.assertEqual(fraca["rotulo"], "muito fraca")
        self.assertGreater(forte["nota"], 80)
        for f in (fraca, forte):
            self.assertTrue(all(0 <= v <= 100 for v in f["partes"].values()))

    def test_velas_abrem_no_fechamento_anterior(self):
        v = cx.montar_velas([(10, 2.0, 1), (20, 3.0, 2), (70, 1.0, 5)], 0, 1.0, 60)
        self.assertEqual(len(v), 2)
        self.assertEqual((v[0]["o"], v[0]["h"], v[0]["l"], v[0]["c"], v[0]["v"]), (1.0, 3.0, 1.0, 3.0, 3))
        self.assertEqual((v[1]["o"], v[1]["c"], v[1]["l"]), (3.0, 1.0, 1.0))

    def test_sem_negocios_tem_uma_vela(self):
        self.assertEqual(len(cx.montar_velas([], 100, 1.0, 60)), 1)


class TestCorretora(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        servidor.BANCO = os.path.join(self.tmp, "rede.db")
        cx.CARTEIRA_ARQ = os.path.join(self.tmp, "corretora_carteira.json")
        servidor.transferencias.clear()
        servidor.rodada.clear()
        servidor.abrir_banco()
        self.ana, self.beto = Pessoa("ana"), Pessoa("beto")

    def tearDown(self):
        servidor.db.close()
        cx.CARTEIRA_ARQ = CARTEIRA_ORIGINAL          # não deixa os próximos testes apontando para a pasta apagada
        shutil.rmtree(self.tmp)

    def test_carteira_da_corretora_e_secreta_e_valida(self):
        self.assertTrue(servidor.endereco_valido(cx.CX["endereco"]))
        self.assertEqual(os.stat(cx.CARTEIRA_ARQ).st_mode & 0o777, 0o600)

    def test_deposito_entra_so_depois_do_bloco(self):
        self.ana.depositar(500)
        self.assertEqual(cx.ptg_de(self.ana.end), 500 * U)
        cx.ler_depositos()                       # ler de novo não credita duas vezes
        self.assertEqual(cx.ptg_de(self.ana.end), 500 * U)

    def test_criar_cobra_a_taxa_para_o_dono(self):
        self.ana.depositar(500)
        r = self.ana.criar("GIGA")
        self.assertTrue(r["ok"], r)
        self.assertEqual(cx.ptg_de(self.ana.end), 400 * U)
        self.assertEqual(cx.ptg_de(cx.DONO), 100 * U)
        self.assertFalse(self.beto.criar("GIGA")["ok"])

    def test_sem_saldo_nao_cria(self):
        r = self.ana.criar("GIGA")
        self.assertFalse(r["ok"])
        self.assertIn("insuficiente", r["erro"])

    def test_simbolo_e_palavras_proibidas(self):
        self.ana.depositar(500)
        self.assertFalse(self.ana.criar("PTG")["ok"])
        self.assertFalse(self.ana.criar("giga")["ok"])
        self.assertFalse(self.ana.criar("LUCRO", nome="Lucro garantido")["ok"])

    def test_compra_e_venda_com_taxas(self):
        self.ana.depositar(500)
        self.ana.criar("GIGA")
        self.beto.depositar(200)
        c = self.beto.comprar("GIGA", 100 * U)
        self.assertTrue(c["ok"], c)
        self.assertEqual(cx.ptg_de(self.beto.end), 100 * U)
        self.assertEqual(cx.ptg_de(cx.DONO), 100 * U + 1 * U)          # criação + 1% da compra
        self.assertEqual(cx.ptg_de(self.ana.end), 400 * U + U // 4)     # criadora ganha 0,25%
        qtd = cx.moedas_de("GIGA", self.beto.end)
        v = self.beto.vender("GIGA", qtd)
        self.assertTrue(v["ok"], v)
        self.assertEqual(cx.moedas_de("GIGA", self.beto.end), 0)
        self.assertLess(cx.ptg_de(self.beto.end), 200 * U)              # pagou as taxas na ida e na volta
        self.assertGreater(cx.ptg_de(self.beto.end), 197 * U)

    def test_compra_inicial_do_criador_fica_travada(self):
        self.ana.depositar(500)
        r = self.ana.criar("GIGA", inicial=50 * U)
        self.assertTrue(r["ok"], r)
        qtd = cx.moedas_de("GIGA", self.ana.end)
        self.assertGreater(qtd, 0)
        v = self.ana.vender("GIGA", qtd)
        self.assertFalse(v["ok"])
        self.assertIn("travadas", v["erro"])

    def test_limite_anti_baleia(self):
        cx.salvar_regras({"max_carteira_pct": 1})
        self.ana.depositar(500)
        self.ana.criar("GIGA")
        self.beto.depositar(5000)
        r = self.beto.comprar("GIGA", 4000 * U)
        self.assertFalse(r["ok"])
        self.assertIn("anti-baleia", r["erro"])

    def test_pedido_repetido_e_recusado(self):
        self.ana.depositar(500)
        self.ana.criar("GIGA")
        self.beto.depositar(200)
        p = self.beto.pedido("portgiga-cx-comprar:", ["GIGA", 10 * U, 0], simbolo="GIGA", ptg=10 * U, minimo=0)
        self.assertTrue(cx.comprar(dict(p))["ok"])
        self.assertFalse(cx.comprar(dict(p))["ok"])

    def test_assinatura_de_outro_nao_vale(self):
        self.ana.depositar(500)
        self.ana.criar("GIGA")
        p = self.beto.pedido("portgiga-cx-comprar:", ["GIGA", 10 * U, 0], simbolo="GIGA", ptg=10 * U, minimo=0)
        p["carteira"] = self.ana.end
        self.assertFalse(cx.comprar(p)["ok"])

    def test_preco_minimo_protege_quem_compra(self):
        self.ana.depositar(500)
        self.ana.criar("GIGA")
        self.beto.depositar(200)
        r = self.beto.comprar("GIGA", 10 * U, minimo=10 ** 30)
        self.assertFalse(r["ok"])
        self.assertEqual(cx.ptg_de(self.beto.end), 200 * U)

    def test_moeda_banida_so_vende(self):
        self.ana.depositar(500)
        self.ana.criar("GIGA")
        self.beto.depositar(200)
        self.beto.comprar("GIGA", 50 * U)
        cx.banir("GIGA", True, "golpe")
        self.assertFalse(self.beto.comprar("GIGA", 10 * U)["ok"])
        self.assertTrue(self.beto.vender("GIGA", cx.moedas_de("GIGA", self.beto.end))["ok"])

    def test_saque_volta_para_a_carteira_e_corretora_fica_solvente(self):
        self.ana.depositar(500)
        self.ana.criar("GIGA")
        self.beto.depositar(200)
        self.beto.comprar("GIGA", 50 * U)
        r = self.beto.sacar(100 * U)
        self.assertTrue(r["ok"], r)
        self.assertFalse(self.beto.sacar(100 * U)["ok"])                 # só sobrou 50
        servidor.fechar_bloco()
        self.assertEqual(servidor.saldo(self.beto.end), 100 * U)
        p = cx.painel_dono()
        self.assertTrue(p["solvencia"]["ok"], p["solvencia"])

    def test_pausa_de_compras_do_dono(self):
        self.ana.depositar(500)
        self.ana.criar("GIGA")
        cx.salvar_regras({"compras_pausadas": True})
        self.assertFalse(self.ana.comprar("GIGA", 10 * U)["ok"])

    def test_regras_fora_do_limite_sao_recusadas(self):
        self.assertFalse(cx.salvar_regras({"taxa_dono_pct": 50})["ok"])
        self.assertTrue(cx.salvar_regras({"taxa_dono_pct": 2})["ok"])
        self.assertEqual(cx.regras()["taxa_dono_pct"], 2.0)

    def test_endereco_de_doacao_valida_formato(self):
        self.assertFalse(cx.salvar_regras({"apoio_btc": "minhas doze palavras secretas"})["ok"])
        self.assertTrue(cx.salvar_regras({"apoio_btc": " 1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa "})["ok"])
        self.assertEqual(cx.mercado()["regras"]["apoio_btc"], "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa")
        self.assertTrue(cx.salvar_regras({"apoio_btc": "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"})["ok"])
        self.assertTrue(cx.salvar_regras({"apoio_btc": ""})["ok"])

    def test_mercado_e_conta(self):
        self.ana.depositar(500)
        self.ana.criar("GIGA")
        self.beto.depositar(200)
        self.beto.comprar("GIGA", 20 * U)
        m = cx.mercado()
        self.assertEqual(m["moedas"][0]["simbolo"], "GIGA")
        self.assertEqual(m["moedas"][0]["donos"], 1)
        c = cx.conta(self.beto.end)
        self.assertEqual(c["posicoes"][0]["simbolo"], "GIGA")
        self.assertEqual(len(cx.moeda("GIGA")["negocios"]), 1)
        self.assertIn("nota", m["moedas"][0]["forca"])
        self.assertGreaterEqual(len(cx.velas("GIGA", 60)["velas"]), 1)
        d = cx.moeda("GIGA")
        self.assertLessEqual(d["minima_24h"], d["preco"])
        self.assertGreaterEqual(d["maxima_24h"], d["preco"])
        self.assertEqual(d["curva"]["oferta"], 10 ** 9 * U)
        self.assertEqual(m["top"][0]["carteira"], self.beto.end)


if __name__ == "__main__":
    unittest.main()
