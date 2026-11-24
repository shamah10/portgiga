#!/usr/bin/env python3
"""Fluxo completo no navegador de celular: chega por link de convite com ?o=tiktok, cria a carteira, vê a faixa do
presente, minera no próprio app, ganha o presente no próximo bloco, e quem convidou também ganha."""
import os
import sys
import unittest

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
sys.path.insert(0, os.path.join(AQUI, "..", "servidor"))

import boasvindas  # noqa: E402
import servidor  # noqa: E402
from test_app_navegador import BaseApp  # noqa: E402

CHAVE = "44" * 32


class TestPresenteNavegador(BaseApp):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        boasvindas.MIN_RESPOSTAS_PRESENTE = 1
        boasvindas.CARTEIRA_ARQ = os.path.join(cls.tmp, "fundo.json")
        fundo = boasvindas.criar_carteira_fundo()
        boasvindas.preparar(servidor)
        with servidor.trava:
            servidor.creditar(fundo["endereco"], 100 * servidor.UNIDADE)
            servidor.db.commit()
        cls.fundo = fundo["endereco"]
        cls.eh_casa = boasvindas.eh_casa
        boasvindas.eh_casa = lambda ip: False   # no teste tudo vem de 127.0.0.1; finge que é gente de fora
        cls.amigo = servidor.endereco_do_pub(servidor.pub_da_chave("55" * 32))
        boasvindas.registrar({"endereco": cls.amigo}, "200.0.0.9")

    @classmethod
    def tearDownClass(cls):
        boasvindas.eh_casa = cls.eh_casa
        super().tearDownClass()

    def test_convite_minerar_e_ganhar(self):
        ctx = self.nav.new_context(**self.pw.devices["Pixel 7"], bypass_csp=True)
        pg = ctx.new_page()
        erros = []
        pg.on("pageerror", lambda e: erros.append(str(e)))
        pg.goto(self.url + "?o=TikTok&convite=" + self.amigo)
        pg.click("#bv-importar")
        pg.fill("#im-chave", CHAVE)
        pg.click("#im-ok")
        pg.wait_for_selector('[data-tela="pin"].ativa')
        self.digitar_pin(pg)
        pg.wait_for_function("document.getElementById('pin-titulo').textContent==='Repita o PIN'")
        self.digitar_pin(pg)
        pg.wait_for_selector('[data-tela="inicio"].ativa', timeout=20000)
        endereco = servidor.endereco_do_pub(servidor.pub_da_chave(CHAVE))

        # faixa do presente aparece e o servidor guardou de onde veio
        pg.wait_for_selector("#in-presente:not([hidden])", timeout=10000)
        self.assertIn("Ganhe 10 PTG", pg.inner_text("#in-presente"))
        r = servidor.db.execute("SELECT origem, convite FROM chegadas WHERE endereco=?", (endereco,)).fetchone()
        self.assertEqual(r, ("tiktok", self.amigo))

        # minera de verdade no navegador até ter uma resposta aceita
        pg.click("#in-presente")
        pg.wait_for_selector("#f-minerar.ativa")
        self.assertIn("ganha 10 PTG", pg.inner_text("#mn-presente"))
        pg.click("#mn-botao")
        pg.wait_for_function("Number(document.getElementById('mn-ok').textContent) >= 1", timeout=60000)
        pg.click("#mn-botao")   # para
        self.assertIn("Começar", pg.inner_text("#mn-botao"))

        # no próximo bloco: presente para a carteira nova e prêmio para quem convidou
        servidor.fechar_bloco()
        self.assertGreaterEqual(servidor.saldo(endereco), 10 * servidor.UNIDADE)
        self.assertEqual(servidor.saldo(self.amigo), 10 * servidor.UNIDADE)
        self.assertEqual(servidor.saldo(self.fundo), 80 * servidor.UNIDADE)

        # a faixa vira "convide amigos" e a folha de convite mostra o link certo
        pg.click("#f-minerar [data-fechar]")
        pg.evaluate("atualizarPresente()")
        pg.wait_for_function("document.getElementById('in-presente-t').textContent.startsWith('Convide')", timeout=10000)
        pg.click("#in-presente")
        pg.wait_for_selector("#f-convite.ativa")
        self.assertTrue(pg.inner_text("#cv-link").endswith("/?convite=" + endereco))

        # rede caiu: aparece o aviso de manutenção
        pg.route("**/api/**", lambda rota: rota.abort())
        pg.click("#f-convite [data-fechar]")
        pg.evaluate("atualizar(true)")
        pg.wait_for_selector("#in-manutencao:not([hidden])", timeout=10000)
        self.assertEqual(erros, [])
        ctx.close()


if __name__ == "__main__":
    unittest.main()
