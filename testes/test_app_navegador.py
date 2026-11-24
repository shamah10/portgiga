#!/usr/bin/env python3
"""App da carteira num navegador de celular de verdade (Chromium do Playwright), contra um servidor de teste:
criar carteira, conferir a chave, PIN, receber saldo, enviar (assinatura aceita pelo servidor), trancar e importar."""
import os
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(AQUI, "..", "servidor"))

import servidor  # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

CHAVE_DESTINO = "22" * 32
CHAVE_IMPORTAR = "33" * 32
PIN = "246813"


@unittest.skipIf(sync_playwright is None, "playwright não instalado")
class BaseApp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        servidor.BANCO = os.path.join(cls.tmp, "rede.db")
        servidor.abrir_banco()
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), servidor.Manipulador)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = "http://127.0.0.1:%d/app/" % cls.srv.server_address[1]
        cls.pw = sync_playwright().start()
        cls.nav = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.nav.close()
        cls.pw.stop()
        cls.srv.shutdown()
        servidor.db.close()
        shutil.rmtree(cls.tmp)

    def novo_celular(self):
        ctx = self.nav.new_context(**self.pw.devices["Pixel 7"], bypass_csp=True)   # o CSP do app bloqueia o eval do Playwright
        pg = ctx.new_page()
        erros = []
        pg.on("pageerror", lambda e: erros.append(str(e)))
        pg.goto(self.url)
        return ctx, pg, erros

    def digitar_pin(self, pg, pin=PIN):
        for d in pin:
            pg.click(f'#teclado button[data-n="{d}"]')


class TestAppNavegador(BaseApp):
    def test_criar_receber_enviar_trancar(self):
        ctx, pg, erros = self.novo_celular()
        pg.click("#bv-criar")
        chave = pg.evaluate("[...document.querySelectorAll('#bk-chave .g b')].map(e => e.textContent).join('')")
        self.assertRegex(chave, r"^[0-9a-f]{64}$")
        self.assertTrue(pg.is_disabled("#bk-anotei"))          # só segue depois de concordar com os dois avisos
        pg.click("#bk-chave .revelar")
        pg.click("label.concordo:has(#bk-c1)")
        pg.click("label.concordo:has(#bk-c2)")
        pg.click("#bk-anotei")
        grupos = [chave[i:i + 4] for i in range(0, 64, 4)]
        a, b = (int(pg.get_attribute(s, "data-i")) for s in ("#cf-rot-a", "#cf-rot-b"))
        pg.fill("#cf-a", "zzzz")
        pg.fill("#cf-b", grupos[b])
        pg.click("#cf-ok")
        self.assertIn("Não confere", pg.inner_text("#cf-erro"))
        pg.fill("#cf-a", grupos[a])
        pg.click("#cf-ok")
        pg.wait_for_selector('[data-tela="pin"].ativa')
        self.digitar_pin(pg)
        pg.wait_for_function("document.getElementById('pin-titulo').textContent==='Repita o PIN'")
        self.digitar_pin(pg)
        pg.wait_for_selector('[data-tela="inicio"].ativa', timeout=20000)

        # o endereço do app é o mesmo que o servidor calcula para essa chave
        endereco = pg.evaluate("JSON.parse(localStorage.getItem('ptgapp:cofre')).endereco")
        self.assertEqual(endereco, servidor.endereco_do_pub(servidor.pub_da_chave(chave)))
        self.assertNotIn(chave, pg.evaluate("JSON.stringify(localStorage)"))   # a chave só fica trancada

        # recebe 50 PTG (crédito direto no banco de teste)
        with servidor.trava:
            servidor.creditar(endereco, 50 * servidor.UNIDADE)
            servidor.db.commit()
        pg.click("#in-atualizar")
        pg.wait_for_function("document.getElementById('in-saldo').textContent.startsWith('50')")

        # envia 1,5 PTG
        destino = servidor.endereco_do_pub(servidor.pub_da_chave(CHAVE_DESTINO))
        pg.click("#in-enviar")
        pg.fill("#ev-para", destino)
        pg.fill("#ev-valor", "1,5")
        pg.click("#ev-continuar")
        self.assertEqual(pg.inner_text("#ev-r-valor"), "1,50")
        pg.click("#ev-confirmar")
        pg.wait_for_selector('[data-tela="pin"].ativa')
        self.digitar_pin(pg)
        pg.wait_for_selector("#ev-passo3:not([hidden])", timeout=20000)
        self.assertEqual(len(servidor.transferencias), 1)
        t = servidor.transferencias[0]
        self.assertEqual((t["de"], t["para"], t["unidades"]), (endereco, destino, 150_000_000))
        pg.click("#ev-passo3 [data-fechar]")
        pg.wait_for_selector(".mov.pendente")

        # o bloco fecha: o envio aparece no histórico e o saldo cai
        servidor.fechar_bloco()
        pg.click("#in-atualizar")
        pg.wait_for_selector(".mov.enviado")
        pg.wait_for_function("document.getElementById('in-saldo').textContent.startsWith('48,5')")
        self.assertEqual(pg.locator(".mov.pendente").count(), 0)

        # receber mostra o QR do endereço
        pg.click("#in-receber")
        pg.wait_for_selector("#rc-qr img")
        self.assertEqual(pg.inner_text("#rc-end"), endereco)
        pg.click("#f-receber [data-fechar]")

        # tranca e confere PIN errado / certo
        pg.click('#abas [data-aba="ajustes"]')
        pg.click("#aj-trancar")
        pg.wait_for_selector('[data-tela="pin"].ativa')
        self.digitar_pin(pg, "000000")
        pg.wait_for_function("document.getElementById('pin-erro').textContent.includes('PIN errado')", timeout=20000)
        self.digitar_pin(pg)
        pg.wait_for_selector('[data-tela="inicio"].ativa', timeout=20000)
        self.assertEqual(erros, [])
        ctx.close()

    def test_importar_chave(self):
        ctx, pg, erros = self.novo_celular()
        pg.click("#bv-importar")
        pg.fill("#im-chave", "nao-e-chave")
        pg.click("#im-ok")
        self.assertIn("64 caracteres", pg.inner_text("#im-erro"))
        pg.fill("#im-chave", CHAVE_IMPORTAR)
        pg.click("#im-ok")
        pg.wait_for_selector('[data-tela="pin"].ativa')
        self.digitar_pin(pg)
        pg.wait_for_function("document.getElementById('pin-titulo').textContent==='Repita o PIN'")
        self.digitar_pin(pg)
        pg.wait_for_selector('[data-tela="inicio"].ativa', timeout=20000)
        endereco = pg.evaluate("JSON.parse(localStorage.getItem('ptgapp:cofre')).endereco")
        self.assertEqual(endereco, servidor.endereco_do_pub(servidor.pub_da_chave(CHAVE_IMPORTAR)))
        self.assertIn("Nenhuma movimentação", pg.inner_text("#in-movs"))
        self.assertEqual(erros, [])
        ctx.close()


class TestAppConta(BaseApp):
    """Entrar no app com a conta do site (mesma senha): a chave chega trancada e só abre no celular."""

    def test_entrar_com_conta_do_site(self):
        import hashlib
        import json
        import os as _os
        import time
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        chave, usuario, senha = "88" * 32, "maria_teste", "senha-forte-123"
        pbk = lambda s, sal: hashlib.pbkdf2_hmac("sha256", s.encode(), sal, 210000, 32)
        sal, iv = _os.urandom(16), _os.urandom(12)
        cifrada = json.dumps({"v": 1, "sal": sal.hex(), "iv": iv.hex(), "dados": AESGCM(pbk(senha, sal)).encrypt(iv, chave.encode(), None).hex()})
        carteira = servidor.endereco_do_pub(servidor.pub_da_chave(chave))
        quando = int(time.time())
        priv = Ed25519PrivateKey.from_private_bytes(hashlib.sha256(("ptg-ed25519:" + chave).encode()).digest())
        msg = "portgiga-cadastro:" + json.dumps([usuario, carteira, quando], separators=(",", ":"), ensure_ascii=False)
        r = servidor.cadastrar({"usuario": usuario, "senha": pbk(senha, ("portgiga-login:" + usuario).encode()).hex(), "carteira": carteira,
                                "quando": quando, "pub": servidor.pub_da_chave(chave), "assinatura": priv.sign(msg.encode()).hex(), "chave_cifrada": cifrada})
        self.assertTrue(r["ok"], r)
        ctx, pg, erros = self.novo_celular()
        pg.click("#bv-conta")
        pg.fill("#ct-usuario", usuario)
        pg.fill("#ct-senha", "senha-errada")
        pg.click("#ct-ok")
        pg.wait_for_function("document.getElementById('ct-erro').textContent.length>0", timeout=20000)
        pg.fill("#ct-senha", senha)
        pg.click("#ct-ok")
        pg.wait_for_selector('[data-tela="pin"].ativa', timeout=20000)
        self.digitar_pin(pg)
        pg.wait_for_function("document.getElementById('pin-titulo').textContent==='Repita o PIN'")
        self.digitar_pin(pg)
        pg.wait_for_selector('[data-tela="inicio"].ativa', timeout=20000)
        self.assertEqual(pg.evaluate("JSON.parse(localStorage.getItem('ptgapp:cofre')).endereco"), carteira)
        self.assertEqual(erros, [])
        ctx.close()


if __name__ == "__main__":
    unittest.main()
