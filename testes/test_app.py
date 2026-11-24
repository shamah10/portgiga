#!/usr/bin/env python3
"""App da carteira para celular (PWA) servido em /app/: arquivos certos, tipos certos e nada fora da pasta."""
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


class TestApp(unittest.TestCase):
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

    def pegar(self, caminho):
        try:
            r = abrir(self.url + caminho)
            return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()

    def test_app_sem_barra_redireciona(self):
        codigo, cab, _ = self.pegar("/app")
        self.assertEqual((codigo, cab.get("Location")), (301, "/app/"))

    def test_pagina_do_app(self):
        codigo, cab, corpo = self.pegar("/app/")
        self.assertEqual(codigo, 200)
        self.assertIn("text/html", cab.get("Content-Type"))
        self.assertIn(b"Carteira Portgiga", corpo)

    def test_arquivos_e_tipos(self):
        for caminho, tipo in (("/app/app.js", "text/javascript"), ("/app/app.css", "text/css"), ("/app/qrcode.js", "text/javascript"),
                              ("/app/manifest.webmanifest", "application/manifest+json"), ("/app/icones/icone-192.png", "image/png"),
                              ("/app/icones/icone.svg", "image/svg+xml")):
            codigo, cab, corpo = self.pegar(caminho)
            self.assertEqual(codigo, 200, caminho)
            self.assertIn(tipo, cab.get("Content-Type"), caminho)
            self.assertTrue(len(corpo) > 0, caminho)

    def test_service_worker_vale_para_o_app(self):
        codigo, cab, _ = self.pegar("/app/sw.js")
        self.assertEqual(codigo, 200)
        self.assertEqual(cab.get("Service-Worker-Allowed"), "/app/")
        self.assertIn("no-cache", cab.get("Cache-Control", ""))

    def test_nada_fora_da_pasta_do_app(self):
        for caminho in ("/app/../servidor.py", "/app/%2e%2e/servidor.py", "/app/icones/../../servidor.py", "/app/nao-existe.js",
                        "/app/app.js.bak", "/app/.hidden"):
            codigo, _, corpo = self.pegar(caminho)
            self.assertEqual(codigo, 404, caminho)
            self.assertNotIn(b"import", corpo, caminho)


if __name__ == "__main__":
    unittest.main()
