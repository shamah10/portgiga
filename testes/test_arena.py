#!/usr/bin/env python3
"""Link para a Arena Fichas: no PC vai para o localhost; de fora, para o link público atual do túnel da Arena."""
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(AQUI, "..", "servidor"))

import servidor  # noqa: E402


class SemRedirecionar(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


abrir = urllib.request.build_opener(SemRedirecionar).open


class TestArena(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        servidor.BANCO = os.path.join(cls.tmp, "rede.db")
        servidor.abrir_banco()
        cls.link = os.path.join(cls.tmp, "link.txt")
        servidor.ARENA_LINK_ARQ = cls.link
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), servidor.Manipulador)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = "http://127.0.0.1:%d" % cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        servidor.db.close()
        shutil.rmtree(cls.tmp)

    def destino(self, cabecalhos=None):
        try:
            abrir(urllib.request.Request(self.url + "/arena", headers=cabecalhos or {}))
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Location"), e.read().decode()
        self.fail("esperava um redirecionamento")

    def test_no_pc_vai_para_o_localhost(self):
        codigo, onde, _ = self.destino()
        self.assertEqual((codigo, onde), (302, "http://localhost:8100/"))

    def test_de_fora_vai_para_o_link_do_tunel(self):
        with open(self.link, "w") as fh:
            fh.write("https://exemplo-teste.trycloudflare.com\n")
        codigo, onde, _ = self.destino({"CF-Connecting-IP": "200.1.2.3"})
        self.assertEqual((codigo, onde), (302, "https://exemplo-teste.trycloudflare.com/"))

    def test_de_fora_sem_tunel_avisa(self):
        if os.path.exists(self.link):
            os.remove(self.link)
        codigo, onde, corpo = self.destino({"CF-Connecting-IP": "200.1.2.3"})
        self.assertEqual(codigo, 503)
        self.assertIsNone(onde)
        self.assertIn("Arena", corpo)

    def test_link_invalido_no_arquivo_e_ignorado(self):
        with open(self.link, "w") as fh:
            fh.write("javascript:alert(1)")
        codigo, _, _ = self.destino({"CF-Connecting-IP": "200.1.2.3"})
        self.assertEqual(codigo, 503)


class TestServidorPublico(unittest.TestCase):
    """Endereço público do servidor para quem vai configurar uma placa longe da casa do dono."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        servidor.BANCO = os.path.join(cls.tmp, "rede.db")
        servidor.abrir_banco()
        cls.link = os.path.join(cls.tmp, "link-portgiga.txt")
        servidor.PORTGIGA_LINK_ARQ = cls.link
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), servidor.Manipulador)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = "http://127.0.0.1:%d" % cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        servidor.db.close()
        shutil.rmtree(cls.tmp)

    def pedir(self):
        import json
        return json.load(urllib.request.urlopen(self.url + "/api/servidor-publico"))

    def test_com_tunel_mostra_o_link(self):
        with open(self.link, "w") as fh:
            fh.write("https://abc-def.trycloudflare.com\n")
        self.assertEqual(self.pedir(), {"url": "https://abc-def.trycloudflare.com"})

    def test_sem_tunel_diz_que_esta_fechado(self):
        if os.path.exists(self.link):
            os.remove(self.link)
        self.assertEqual(self.pedir(), {"url": None})

    def test_conteudo_estranho_nao_vaza(self):
        with open(self.link, "w") as fh:
            fh.write("<script>alert(1)</script>")
        self.assertEqual(self.pedir(), {"url": None})


if __name__ == "__main__":
    unittest.main()
