#!/usr/bin/env python3
"""Feira Portgiga: anunciar, comprar com PTG guardado, enviar, receber, cancelar, disputa e liberação automática."""
import os
import shutil
import sys
import tempfile
import time
import unittest

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(AQUI, "..", "servidor"))
sys.path.insert(0, AQUI)

import corretora as cx  # noqa: E402
import feira  # noqa: E402
import servidor  # noqa: E402
from test_corretora import Pessoa, CARTEIRA_ORIGINAL  # noqa: E402

U = servidor.UNIDADE


class TestFeira(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        servidor.BANCO = os.path.join(self.tmp, "rede.db")
        cx.CARTEIRA_ARQ = os.path.join(self.tmp, "corretora_carteira.json")
        servidor.transferencias.clear()
        servidor.rodada.clear()
        servidor.abrir_banco()
        self._capas, feira.CAPAS = feira.CAPAS, os.path.join(self.tmp, "capas")
        self.vend, self.comp, self.outro = Pessoa("vendedora"), Pessoa("comprador"), Pessoa("curioso")
        self.comp.depositar(500)

    def tearDown(self):
        servidor.db.close()
        feira.CAPAS = self._capas
        cx.CARTEIRA_ARQ = CARTEIRA_ORIGINAL
        shutil.rmtree(self.tmp)

    def anunciar(self, p, titulo="Bolo de cenoura", preco=50 * U, estoque=2, desc="caseiro"):
        return feira.anunciar(p.pedido("portgiga-fx-anunciar:", [titulo, desc, "comida", preco, estoque],
                                       titulo=titulo, descricao=desc, categoria="comida", preco=preco, estoque=estoque))

    def comprar(self, p, aid, preco=50 * U):
        return feira.comprar(p.pedido("portgiga-fx-comprar:", [aid, preco], id=aid, preco=preco))

    def acao(self, p, pid, acao, motivo=""):
        return feira.acao_pedido(p.pedido("portgiga-fx-pedido:", [pid, acao, motivo], pedido=pid, acao=acao, motivo=motivo))

    def test_compra_guarda_o_ptg_e_libera_ao_receber(self):
        aid = self.anunciar(self.vend)["id"]
        r = self.comprar(self.comp, aid)
        self.assertTrue(r["ok"], r)
        self.assertEqual(cx.ptg_de(self.comp.end), 450 * U)
        self.assertEqual(cx.ptg_de(self.vend.end), 0)                     # ainda guardado
        self.assertEqual(feira.em_custodia(), 50 * U)
        self.assertTrue(self.acao(self.vend, r["pedido"], "enviado")["ok"])
        self.assertTrue(self.acao(self.comp, r["pedido"], "recebido")["ok"])
        self.assertEqual(cx.ptg_de(self.vend.end), 49 * U)                # 2% para o dono
        self.assertEqual(cx.ptg_de(cx.DONO), 1 * U)
        self.assertEqual(feira.em_custodia(), 0)

    def test_vendedor_cancela_e_comprador_recebe_de_volta(self):
        aid = self.anunciar(self.vend)["id"]
        pid = self.comprar(self.comp, aid)["pedido"]
        self.assertTrue(self.acao(self.vend, pid, "cancelar")["ok"])
        self.assertEqual(cx.ptg_de(self.comp.end), 500 * U)
        self.assertEqual(feira.vitrine()["itens"][0]["estoque"], 2)

    def test_so_quem_pode_faz_cada_acao(self):
        aid = self.anunciar(self.vend)["id"]
        pid = self.comprar(self.comp, aid)["pedido"]
        self.assertFalse(self.acao(self.comp, pid, "cancelar")["ok"])       # comprador não cancela sozinho
        self.assertFalse(self.acao(self.vend, pid, "recebido")["ok"])       # vendedor não libera para si
        self.assertFalse(self.acao(self.outro, pid, "recebido")["ok"])

    def test_disputa_resolvida_pelo_dono(self):
        aid = self.anunciar(self.vend)["id"]
        pid = self.comprar(self.comp, aid)["pedido"]
        self.assertTrue(self.acao(self.comp, pid, "disputa", "não chegou")["ok"])
        self.assertFalse(self.acao(self.comp, pid, "recebido")["ok"])
        self.assertEqual(len(feira.disputas()["disputas"]), 1)
        self.assertTrue(feira.resolver(pid, "comprador")["ok"])
        self.assertEqual(cx.ptg_de(self.comp.end), 500 * U)

    def test_liberacao_automatica_depois_de_enviado(self):
        aid = self.anunciar(self.vend)["id"]
        pid = self.comprar(self.comp, aid)["pedido"]
        self.acao(self.vend, pid, "enviado")
        feira.liberar_vencidos(time.time() + 6 * 86400)
        self.assertEqual(cx.ptg_de(self.vend.end), 0)
        feira.liberar_vencidos(time.time() + 8 * 86400)
        self.assertEqual(cx.ptg_de(self.vend.end), 49 * U)

    def test_regras_do_anuncio(self):
        self.assertFalse(self.anunciar(self.vend, titulo="Vendo por pix")["ok"])
        self.assertFalse(self.anunciar(self.vend, preco=0)["ok"])
        aid = self.anunciar(self.vend)["id"]
        self.assertFalse(self.comprar(self.vend, aid)["ok"])               # não compra de si mesma
        self.assertFalse(self.comprar(self.comp, aid, preco=10 * U)["ok"])  # preço diferente do anúncio
        self.assertFalse(self.comprar(self.outro, aid)["ok"])              # sem saldo

    def test_estoque_acaba(self):
        aid = self.anunciar(self.vend, estoque=1)["id"]
        self.assertTrue(self.comprar(self.comp, aid)["ok"])
        self.assertFalse(self.comprar(self.comp, aid)["ok"])
        self.assertEqual(feira.vitrine()["itens"], [])

    def test_conversa_so_para_quem_participa(self):
        aid = self.anunciar(self.vend)["id"]
        pid = self.comprar(self.comp, aid)["pedido"]
        self.assertTrue(feira.mensagem(self.comp.pedido("portgiga-fx-msg:", [pid, "Rua A, 10"], pedido=pid, texto="Rua A, 10"))["ok"])
        self.assertFalse(feira.mensagem(self.outro.pedido("portgiga-fx-msg:", [pid, "oi"], pedido=pid, texto="oi"))["ok"])
        meus = feira.meus_pedidos(self.vend.pedido("portgiga-fx-ler:", []))
        self.assertEqual(meus["pedidos"][0]["mensagens"][0]["texto"], "Rua A, 10")
        self.assertEqual(feira.meus_pedidos(self.outro.pedido("portgiga-fx-ler:", []))["pedidos"], [])

    def test_capa_so_aceita_imagem_e_regrava_em_jpeg(self):
        import io
        from PIL import Image
        feira.CAPAS = os.path.join(self.tmp, "capas")
        aid = self.anunciar(self.vend)["id"]
        self.assertFalse(feira.definir_capa(aid, b"<script>alert(1)</script>")["ok"])
        b = io.BytesIO()
        Image.new("RGB", (1600, 900), "green").save(b, "PNG")
        self.assertTrue(feira.definir_capa(aid, b.getvalue())["ok"])
        self.assertLessEqual(max(Image.open(feira.caminho_capa(aid)).size), 1600)
        self.assertTrue(feira.vitrine()["itens"][0]["capa"].startswith("/corretora/capa/%d.jpg" % aid))

    def _foto(self, p, aid, pos, dados):
        import base64, hashlib
        resumo = hashlib.sha256(dados).hexdigest() if dados else ""
        return feira.foto(p.pedido("portgiga-fx-foto:", [aid, pos, resumo], id=aid, pos=pos,
                                   imagem=base64.b64encode(dados).decode() if dados else ""))

    def _img(self, formato, modo="RGB", tam=(2400, 1200), cor="red"):
        import io
        from PIL import Image
        b = io.BytesIO()
        Image.new(modo, tam, cor).save(b, formato)
        return b.getvalue()

    def test_fotos_varios_formatos_e_ordem(self):
        from PIL import Image
        aid = self.anunciar(self.vend)["id"]
        formatos = [("PNG", "RGBA", (0, 0, 0, 0)), ("GIF", "P", 3), ("BMP", "RGB", "blue"), ("TIFF", "RGB", "green"), ("WEBP", "RGB", "red")]
        for pos, (fmt, modo, cor) in enumerate(formatos):
            r = self._foto(self.vend, aid, pos, self._img(fmt, modo, cor=cor))
            self.assertTrue(r["ok"], (fmt, r))
        self.assertEqual(len(r["fotos"]), 5)
        im = Image.open(feira.caminho_capa(aid))
        self.assertEqual(im.format, "JPEG")
        self.assertEqual(max(im.size), 1600)
        self.assertEqual(im.getpixel((10, 10)), (255, 255, 255))  # PNG transparente vira fundo branco
        self.assertFalse(self._foto(self.vend, aid, 5, self._img("JPEG"))["ok"])  # 6ª foto não
        # tirar a capa: as outras sobem e a próxima vira capa
        r = self._foto(self.vend, aid, 0, b"")
        self.assertTrue(r["ok"])
        self.assertEqual(len(r["fotos"]), 4)
        self.assertTrue(r["fotos"][0].startswith("/corretora/capa/%d.jpg" % aid))
        it = feira.vitrine()["itens"][0]
        self.assertEqual(len(it["fotos"]), 4)
        self.assertEqual(it["capa"], it["fotos"][0])

    def test_foto_so_do_vendedor_e_imagem_de_verdade(self):
        aid = self.anunciar(self.vend)["id"]
        self.assertFalse(self._foto(self.outro, aid, 0, self._img("JPEG"))["ok"])
        self.assertFalse(self._foto(self.vend, aid, 0, b"<script>alert(1)</script>")["ok"])
        self.assertFalse(os.path.exists(feira.caminho_capa(aid)))
        # assinatura sobre outra imagem não vale
        import base64, hashlib
        a = self.vend.pedido("portgiga-fx-foto:", [aid, 0, hashlib.sha256(b"x").hexdigest()], id=aid, pos=0,
                             imagem=base64.b64encode(self._img("JPEG")).decode())
        self.assertFalse(feira.foto(a)["ok"])

    def test_caixa_da_corretora_conta_o_guardado(self):
        aid = self.anunciar(self.vend)["id"]
        self.comprar(self.comp, aid)
        self.assertTrue(cx.painel_dono()["solvencia"]["ok"])


if __name__ == "__main__":
    unittest.main()
