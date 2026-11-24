#!/usr/bin/env python3
"""PTG Social: perfil, post com foto, curtir, comentar, seguir, filtro de dinheiro, limites e remoção."""
import base64
import hashlib
import io
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
import servidor  # noqa: E402
import social  # noqa: E402
from test_corretora import Pessoa, CARTEIRA_ORIGINAL  # noqa: E402


def png():
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (40, 30), (0, 200, 255)).save(b, "PNG")
    return b.getvalue()


_VIDEOS = {}


def video(segundos=2, fmt="mp4"):
    """Vídeo de teste gerado pelo ffmpeg (guardado em memória para não gerar de novo)."""
    import subprocess
    chave = (segundos, fmt)
    if chave not in _VIDEOS:
        d = tempfile.mkdtemp()
        arq = os.path.join(d, "v." + fmt)
        cod = ["-c:v", "libvpx", "-b:v", "200k"] if fmt == "webm" else ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=15:duration=%s" % segundos,
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=%s" % segundos, "-shortest"] + cod +
                       ["-metadata", "location=+12.34-056.78/", "-metadata", "title=SEGREDO-DO-AUTOR", arq], check=True)
        _VIDEOS[chave] = open(arq, "rb").read()
        shutil.rmtree(d)
    return _VIDEOS[chave]


class TestSocial(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        servidor.BANCO = os.path.join(self.tmp, "rede.db")
        cx.CARTEIRA_ARQ = os.path.join(self.tmp, "corretora_carteira.json")
        servidor.transferencias.clear()
        servidor.rodada.clear()
        servidor.abrir_banco()
        self._pasta, social.PASTA = social.PASTA, os.path.join(self.tmp, "social")
        self.ana, self.beto = Pessoa("ana"), Pessoa("beto")
        cx.salvar_regras({"social_real_min_ptg": 0})      # testes antigos: sem o filtro anti-robô (testado à parte)

    def tearDown(self):
        servidor.db.close()
        social.PASTA = self._pasta
        cx.CARTEIRA_ARQ = CARTEIRA_ORIGINAL
        shutil.rmtree(self.tmp)

    def postar(self, p, legenda="Minerando PTG na plaquinha", moeda="PTG", img=b""):
        resumo = hashlib.sha256(img).hexdigest() if img else ""
        return social.postar(p.pedido("portgiga-sc-postar:", [legenda, moeda, resumo, 0], legenda=legenda, moeda=moeda, anuncio=0,
                                      imagem=base64.b64encode(img).decode() if img else ""))

    def postar_video(self, p, vid, legenda="Minha plaquinha minerando", img=b""):
        resumo = hashlib.sha256(vid).hexdigest()
        return social.postar(p.pedido("portgiga-sc-postar:", [legenda, "PTG", resumo, 0], legenda=legenda, moeda="PTG", anuncio=0,
                                      imagem=base64.b64encode(img).decode() if img else "", video=base64.b64encode(vid).decode()))

    def esperar(self, pid, limite=60):
        fim = time.time() + limite
        while time.time() < fim:
            e = social.estado_post(pid)["estado"]
            if e != "processando":
                return e
            time.sleep(0.2)
        return "processando"

    def test_video_converte_e_aparece_no_feed(self):
        r = self.postar_video(self.ana, video(2))
        self.assertTrue(r["ok"] and r["processando"], r)
        self.assertEqual(social.feed()["itens"], [])                 # ainda preparando: não aparece
        self.assertEqual(self.esperar(r["id"]), "pronto")
        it = social.feed()["itens"][0]
        self.assertTrue(it["video"].startswith("/corretora/social/p%d.mp4" % r["id"]))
        self.assertTrue(it["foto"].startswith("/corretora/social/p%d.jpg" % r["id"]))   # capa gerada
        saida = open(social.caminho_video(r["id"]), "rb").read()
        self.assertEqual(saida[4:8], b"ftyp")
        self.assertNotIn(b"SEGREDO-DO-AUTOR", saida)                 # metadados (título, GPS) apagados
        self.assertNotIn(b"+12.34-056.78", saida)
        self.assertFalse([f for f in os.listdir(social.PASTA) if f.startswith("tmp_v") or f.endswith(".tmp.mp4")])
        self.assertTrue(social.perfil_de(self.ana.end)["posts"][0]["video"])

    def test_webm_tambem_aceita(self):
        r = self.postar_video(self.ana, video(1, "webm"))
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.esperar(r["id"]), "pronto")

    def test_selo_testemunha_do_bloco_20000(self):
        import json
        social._selos_cache.clear()
        self.assertEqual(social.perfil_de(self.ana.end)["selos"], [])          # bloco ainda não saiu
        cx.S.db.execute("INSERT OR REPLACE INTO blocos (altura, hash, anterior, quando, emissao, respostas, dados) VALUES (?,?,?,?,?,?,?)",
                        (20000, "h", "a", time.time(), 10, 3, json.dumps({"recompensas": {self.ana.end: 5.0}, "transferencias": []})))
        selos = social.perfil_de(self.ana.end)["selos"]
        self.assertEqual([x["bloco"] for x in selos], [20000])
        self.assertIn("20.000", selos[0]["nome"])
        social._selos_cache.clear()

    def test_video_sem_legenda_e_aceito(self):
        r = self.postar_video(self.ana, video(1), legenda="")
        self.assertTrue(r["ok"] and r["processando"], r)
        self.assertEqual(self.esperar(r["id"]), "pronto")

    def test_video_mais_longo_e_cortado(self):
        import json, subprocess
        social.VIDEO_MAX_S, antes = 1, social.VIDEO_MAX_S
        try:
            r = self.postar_video(self.ana, video(3))
            self.assertTrue(r["ok"], r)
            self.assertEqual(self.esperar(r["id"]), "pronto")
        finally:
            social.VIDEO_MAX_S = antes
        d = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json",
                                       social.caminho_video(r["id"])], capture_output=True, text=True).stdout)
        self.assertLess(float(d["format"]["duration"]), 1.6)

    def test_video_original_longo_demais_e_recusado_com_aviso(self):
        social.VIDEO_FONTE_MAX_S, antes = 1, social.VIDEO_FONTE_MAX_S
        try:
            r = self.postar_video(self.ana, video(3))
            self.assertTrue(r["ok"], r)
            self.assertEqual(self.esperar(r["id"]), "falhou")
        finally:
            social.VIDEO_FONTE_MAX_S = antes
        self.assertEqual(social.feed()["itens"], [])
        n = social.notificacoes(self.ana.end)["itens"]
        self.assertEqual(n[0]["tipo"], "video_erro")
        self.assertIn("longo demais", n[0]["texto"])

    def test_arquivo_que_nao_e_video_e_recusado(self):
        lista = b"#EXTM3U\n#EXTINF:1,\nfile:///etc/passwd\n"          # truque do ffmpeg: lista que lê arquivos do PC
        self.assertFalse(self.postar_video(self.ana, lista)["ok"])
        self.assertFalse(self.postar_video(self.ana, png())["ok"])
        self.assertFalse(self.postar_video(self.ana, video(1), img=png())["ok"])       # foto e vídeo juntos: não

    def test_video_assinatura_cobre_o_arquivo(self):
        vid = video(1)
        resumo = hashlib.sha256(vid).hexdigest()
        falso = vid[:-10] + b"0123456789"
        r = social.postar(self.ana.pedido("portgiga-sc-postar:", ["oi pessoal", "PTG", resumo, 0], legenda="oi pessoal", moeda="PTG",
                                          anuncio=0, imagem="", video=base64.b64encode(falso).decode()))
        self.assertFalse(r["ok"])

    def test_videos_desligados_e_um_por_vez(self):
        cx.salvar_regras({"social_videos": False})
        self.assertFalse(self.postar_video(self.ana, video(1))["ok"])
        cx.salvar_regras({"social_videos": True})
        with social._FILA_VIDEO:                       # segura a fila: o primeiro fica "processando"
            r = self.postar_video(self.ana, video(1))
            self.assertTrue(r["ok"])
            r2 = self.postar_video(self.ana, video(1), legenda="outro video")
            self.assertFalse(r2["ok"])
            self.assertIn("outro vídeo", r2["erro"])
        self.assertEqual(self.esperar(r["id"]), "pronto")

    def perfil(self, p, nome, usuario, bio=""):
        return social.perfil(p.pedido("portgiga-sc-perfil:", [nome, usuario, bio, ""], nome=nome, usuario=usuario, bio=bio, imagem=""))

    def test_post_com_foto_aparece_no_feed(self):
        r = self.postar(self.ana, img=png())
        self.assertTrue(r["ok"], r)
        f = social.feed()
        self.assertEqual(len(f["itens"]), 1)
        self.assertTrue(f["itens"][0]["foto"].startswith("/corretora/social/p%d.jpg" % r["id"]))
        self.assertTrue(os.path.exists(social.caminho_foto(r["id"])))

    def test_filtro_de_dinheiro(self):
        self.assertFalse(self.postar(self.ana, "compre agora, lucro garantido")["ok"])
        self.assertFalse(self.postar(self.ana, "aceito pix")["ok"])

    def test_moeda_inexistente(self):
        self.assertFalse(self.postar(self.ana, moeda="NADA")["ok"])

    def test_perfil_usuario_unico(self):
        self.assertTrue(self.perfil(self.ana, "Ana", "ana.ptg")["ok"])
        self.assertFalse(self.perfil(self.beto, "Beto", "ana.ptg")["ok"])
        self.assertFalse(self.perfil(self.beto, "Beto", "Ab")["ok"])
        self.assertEqual(social.usuario("@ana.ptg")["carteira"], self.ana.end)

    def test_curtir_comentar_seguir(self):
        pid = self.postar(self.ana)["id"]
        r = social.curtir(self.beto.pedido("portgiga-sc-curtir:", [pid, True], id=pid, curtir=True))
        self.assertEqual(r["curtidas"], 1)
        r = social.comentar(self.beto.pedido("portgiga-sc-comentar:", [pid, "Show!"], id=pid, texto="Show!"))
        self.assertTrue(r["ok"], r)
        f = social.feed(eu=self.beto.end)["itens"][0]
        self.assertTrue(f["curti"])
        self.assertEqual(f["comentarios"], 1)
        r = social.seguir(self.beto.pedido("portgiga-sc-seguir:", [self.ana.end, True], alvo=self.ana.end, seguir=True))
        self.assertEqual(r["seguidores"], 1)
        self.assertEqual(len(social.feed(eu=self.beto.end, modo="seguindo")["itens"]), 1)
        p = social.perfil_de(self.ana.end, self.beto.end)
        self.assertTrue(p["sigo"])
        self.assertEqual(p["n_posts"], 1)
        # dono do post apaga o comentário do outro
        cid = social.post(pid)["lista"][0]["id"]
        self.assertTrue(social.apagar_comentario(self.ana.pedido("portgiga-sc-apagar-com:", [cid], id=cid))["ok"])

    def test_so_o_autor_apaga(self):
        pid = self.postar(self.ana)["id"]
        self.assertFalse(social.apagar(self.beto.pedido("portgiga-sc-apagar:", [pid], id=pid))["ok"])
        self.assertTrue(social.apagar(self.ana.pedido("portgiga-sc-apagar:", [pid], id=pid))["ok"])
        self.assertEqual(social.feed()["itens"], [])

    def test_assinatura_nao_repete(self):
        pid = self.postar(self.ana)["id"]
        a = self.beto.pedido("portgiga-sc-curtir:", [pid, True], id=pid, curtir=True)
        self.assertTrue(social.curtir(dict(a))["ok"])
        self.assertFalse(social.curtir(dict(a))["ok"])

    def test_limite_por_dia_e_pausa(self):
        cx.salvar_regras({"social_max_posts_dia": 2})
        self.assertTrue(self.postar(self.ana, "um post")["ok"])
        self.assertTrue(self.postar(self.ana, "outro post")["ok"])
        self.assertFalse(self.postar(self.ana, "mais um")["ok"])
        cx.salvar_regras({"social_pausada": True})
        self.assertFalse(self.postar(self.beto, "oi gente")["ok"])

    def test_dono_remove(self):
        pid = self.postar(self.ana)["id"]
        self.assertTrue(social.remover(pid=pid)["ok"])
        self.assertEqual(social.painel_dono()["totais"]["posts"], 0)

    def test_visualizacao_unica_por_dia_e_autor_nao_conta(self):
        pid = self.postar(self.ana)["id"]
        social.ver([pid], self.ana.end)
        social.ver([pid], self.beto.end)
        social.ver([pid], self.beto.end)
        social.ver([pid], "ip:abc")
        self.assertEqual(social.feed()["itens"][0]["views"], 2)

    def _views(self, pid, n):
        for i in range(n):
            social.ver([pid], "ip:%d" % i)

    def test_monetizacao_paga_a_cada_mil_so_elegivel(self):
        cx.salvar_regras({"social_monet_min_seguidores": 1, "social_monet_min_curtidas": 1})
        cx.mover_ptg(cx.DONO, 100 * cx.U)
        pid = self.postar(self.ana)["id"]
        self._views(pid, 1500)                       # ainda não elegível: não conta
        social.pagar_views()
        self.assertEqual(cx.ptg_de(self.ana.end), 0)
        social.seguir(self.beto.pedido("portgiga-sc-seguir:", [self.ana.end, True], alvo=self.ana.end, seguir=True))
        social.curtir(self.beto.pedido("portgiga-sc-curtir:", [pid, True], id=pid, curtir=True))
        self.assertTrue(social.perfil_de(self.ana.end)["monet"]["elegivel"])
        for i in range(2500):
            social.ver([pid], "ip:n%d" % i)
        social.pagar_views()
        self.assertEqual(cx.ptg_de(self.ana.end), 2 * cx.U // 100)        # 2 x 1000 = 0,02 PTG
        self.assertEqual(cx.ptg_de(cx.DONO), 100 * cx.U - 2 * cx.U // 100)
        for i in range(500):
            social.ver([pid], "ip:m%d" % i)                 # 500 que sobraram + 500 = mais 1000
        social.pagar_views()
        self.assertEqual(cx.ptg_de(self.ana.end), 3 * cx.U // 100)

    def test_sem_saldo_do_dono_espera(self):
        cx.salvar_regras({"social_monet_min_seguidores": 0, "social_monet_min_curtidas": 0})
        pid = self.postar(self.ana)["id"]
        self._views(pid, 1000)
        social.pagar_views()
        self.assertEqual(cx.ptg_de(self.ana.end), 0)
        cx.mover_ptg(cx.DONO, 1 * cx.U)
        social.pagar_views()
        self.assertEqual(cx.ptg_de(self.ana.end), cx.U // 100)

    def test_aviso_honesto_pode(self):
        self.assertTrue(self.postar(self.ana, "Minerando PTG. Não é investimento, é só troca!")["ok"])
        self.assertFalse(self.postar(self.beto, "Não é investimento, é lucro garantido")["ok"])

    def test_gorjeta_e_notificacoes(self):
        pid = self.postar(self.ana)["id"]
        self.beto.depositar(10)
        u = cx.U // 2
        r = social.gorjeta(self.beto.pedido("portgiga-sc-gorjeta:", [pid, u], id=pid, unidades=u))
        self.assertTrue(r["ok"], r)
        self.assertEqual(cx.ptg_de(self.ana.end), u)
        self.assertEqual(cx.ptg_de(self.beto.end), 10 * cx.U - u)
        self.assertFalse(social.gorjeta(self.ana.pedido("portgiga-sc-gorjeta:", [pid, u], id=pid, unidades=u))["ok"])   # para si mesma
        social.curtir(self.beto.pedido("portgiga-sc-curtir:", [pid, True], id=pid, curtir=True))
        social.seguir(self.beto.pedido("portgiga-sc-seguir:", [self.ana.end, True], alvo=self.ana.end, seguir=True))
        tipos = [n["tipo"] for n in social.notificacoes(self.ana.end)["itens"]]
        self.assertEqual(tipos, ["seguiu", "curtida", "gorjeta"])
        self.assertEqual(social.feed()["itens"][0]["gorjetas"], 0.5)

    def test_denuncias_escondem_o_post(self):
        cx.salvar_regras({"social_denuncias_ocultar": 2})
        pid = self.postar(self.ana)["id"]
        social.denunciar(self.beto.pedido("portgiga-sc-denunciar:", [pid, "spam"], id=pid, motivo="spam"))
        self.assertEqual(len(social.feed()["itens"]), 1)
        outro = Pessoa("carla")
        social.denunciar(outro.pedido("portgiga-sc-denunciar:", [pid, "spam"], id=pid, motivo="spam"))
        self.assertEqual(social.feed()["itens"], [])
        self.assertEqual(social.denuncias()["itens"][0]["denuncias"], 2)
        social.decidir_denuncia(pid, False)
        self.assertEqual(len(social.feed()["itens"]), 1)

    def test_desafio_paga_os_mais_curtidos(self):
        cx.mover_ptg(cx.DONO, 100 * cx.U)
        self.assertTrue(social.criar_desafio("Foto da plaquinha", "", "#minhaplaca", 10, 1)["ok"])
        p1 = self.postar(self.ana, "olha minha placa #minhaplaca")["id"]
        p2 = self.postar(self.beto, "a minha também #MinhaPlaca")["id"]
        self.postar(self.beto, "sem hashtag")
        social.curtir(self.beto.pedido("portgiga-sc-curtir:", [p1, True], id=p1, curtir=True))
        social.curtir(self.ana.pedido("portgiga-sc-curtir:", [p2, True], id=p2, curtir=True))
        c = Pessoa("carla")
        social.curtir(c.pedido("portgiga-sc-curtir:", [p1, True], id=p1, curtir=True))
        self.assertEqual(social.desafio_atual()["desafio"]["participantes"], 2)
        social.encerrar_desafios(agora=time.time() + 2 * 86400)
        self.assertEqual(cx.ptg_de(self.ana.end), 5 * cx.U)        # 1º lugar: 50%
        self.assertEqual(cx.ptg_de(self.beto.end), 3 * cx.U)       # 2º: 30%
        self.assertTrue(social.desafio_atual()["desafio"]["encerrado"])

    def test_post_com_anuncio_da_feira(self):
        import feira
        t, dsc = "Bolo de cenoura", "caseiro"
        aid = feira.anunciar(self.ana.pedido("portgiga-fx-anunciar:", [t, dsc, "comida", 5 * cx.U, 3], titulo=t, descricao=dsc,
                                             categoria="comida", preco=5 * cx.U, estoque=3))["id"]
        leg = "Saiu do forno!"
        r = social.postar(self.ana.pedido("portgiga-sc-postar:", [leg, "PTG", "", aid], legenda=leg, moeda="PTG", anuncio=aid, imagem=""))
        self.assertTrue(r["ok"], r)
        self.assertEqual(social.feed()["itens"][0]["anuncio"]["titulo"], t)
        r = social.postar(self.beto.pedido("portgiga-sc-postar:", [leg, "PTG", "", aid], legenda=leg, moeda="PTG", anuncio=aid, imagem=""))
        self.assertFalse(r["ok"])                      # anúncio de outra pessoa

    def test_anti_robo_so_conta_carteira_de_verdade(self):
        cx.salvar_regras({"social_real_min_ptg": 1, "social_monet_min_seguidores": 1, "social_monet_min_curtidas": 1})
        pid = self.postar(self.ana)["id"]
        robo = Pessoa("robo")
        social.seguir(robo.pedido("portgiga-sc-seguir:", [self.ana.end, True], alvo=self.ana.end, seguir=True))
        social.curtir(robo.pedido("portgiga-sc-curtir:", [pid, True], id=pid, curtir=True))
        m = social.perfil_de(self.ana.end)["monet"]
        self.assertEqual((m["seguidores"], m["curtidas"], m["elegivel"]), (0, 0, False))
        with servidor.trava:                                   # beto minerou 2 PTG: é de verdade
            servidor.db.execute("INSERT INTO saldos (endereco, saldo, minerado) VALUES (?,?,?)", (self.beto.end, 2 * cx.U, 2 * cx.U))
            servidor.db.commit()
        social.seguir(self.beto.pedido("portgiga-sc-seguir:", [self.ana.end, True], alvo=self.ana.end, seguir=True))
        social.curtir(self.beto.pedido("portgiga-sc-curtir:", [pid, True], id=pid, curtir=True))
        m = social.perfil_de(self.ana.end)["monet"]
        self.assertEqual((m["seguidores"], m["curtidas"], m["elegivel"]), (1, 1, True))
        # visualizações: robô não qualifica; o mesmo IP só qualifica 1 vez por dia por autor
        pid2 = self.postar(self.ana, "outro post")["id"]
        social.ver([pid, pid2], robo.end)
        social.ver([pid, pid2], "ip:x")
        social.ver([pid], self.beto.end)
        self.assertEqual(social.perfil_de(self.ana.end)["monet"]["views"], 2)

    def test_pagina_publica(self):
        self.perfil(self.ana, "Ana", "ana.ptg", "oi")
        html = social.pagina_publica("ana.ptg")
        self.assertIn("og:title", html)
        self.assertIn("/corretora#@ana.ptg", html)
        self.assertIsNone(social.pagina_publica("ninguem"))


class TestVideoHttp(unittest.TestCase):
    """O caminho de verdade: vídeo grande pelo HTTP, conversão, e o MP4 servido em pedaços (Range)."""
    @classmethod
    def setUpClass(cls):
        import threading
        from http.server import ThreadingHTTPServer
        cls.tmp = tempfile.mkdtemp()
        servidor.BANCO = os.path.join(cls.tmp, "rede.db")
        cx.CARTEIRA_ARQ = os.path.join(cls.tmp, "corretora_carteira.json")
        servidor.abrir_banco()
        cls._pasta, social.PASTA = social.PASTA, os.path.join(cls.tmp, "social")
        cx.salvar_regras({"social_real_min_ptg": 0})
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), servidor.Manipulador)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = "http://127.0.0.1:%d" % cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        servidor.db.close()
        social.PASTA = cls._pasta
        cx.CARTEIRA_ARQ = CARTEIRA_ORIGINAL
        shutil.rmtree(cls.tmp)

    def test_envio_e_reproducao(self):
        import json, urllib.request
        vid = video(4) + b""
        ana = Pessoa("ana")
        corpo = ana.pedido("portgiga-sc-postar:", ["meu video", "PTG", hashlib.sha256(vid).hexdigest(), 0], legenda="meu video",
                           moeda="PTG", anuncio=0, imagem="", video=base64.b64encode(vid).decode())
        req = urllib.request.Request(self.url + "/api/cx/social/postar", data=json.dumps(corpo).encode(),
                                     headers={"Content-Type": "application/json", "Host": "localhost:7777"})
        r = json.loads(urllib.request.urlopen(req).read())
        self.assertTrue(r["ok"] and r["processando"], r)
        fim = time.time() + 60
        while time.time() < fim:
            e = json.loads(urllib.request.urlopen(self.url + "/api/cx/social/estado?id=%d" % r["id"]).read())["estado"]
            if e != "processando":
                break
            time.sleep(0.3)
        self.assertEqual(e, "pronto")
        req = urllib.request.Request(self.url + "/corretora/social/p%d.mp4" % r["id"], headers={"Range": "bytes=0-9"})
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 206)
            self.assertEqual(resp.headers["Content-Type"], "video/mp4")
            self.assertEqual(len(resp.read()), 10)


    def _cru(self, pessoa, vid, legenda="", tipo="application/octet-stream", sha=None):
        import json, urllib.error, urllib.request
        ped = pessoa.pedido("portgiga-sc-postar:", [legenda, "PTG", sha or hashlib.sha256(vid).hexdigest(), 0],
                            legenda=legenda, moeda="PTG", anuncio=0)
        req = urllib.request.Request(self.url + "/api/cx/social/postar_video", data=vid, headers={
            "Content-Type": tipo, "Host": "localhost:7777",
            "X-Pedido": base64.b64encode(json.dumps(ped).encode()).decode()})
        try:
            return json.loads(urllib.request.urlopen(req).read())
        except urllib.error.HTTPError as e:
            return json.loads(e.read())

    def _esperar(self, pid):
        import json, urllib.request
        fim = time.time() + 60
        while time.time() < fim:
            e = json.loads(urllib.request.urlopen(self.url + "/api/cx/social/estado?id=%d" % pid).read())["estado"]
            if e != "processando":
                return e
            time.sleep(0.3)

    def test_video_cru_sem_legenda(self):
        r = self._cru(Pessoa("bia"), video(2))           # como o celular manda agora: arquivo cru, sem base64
        self.assertTrue(r["ok"] and r["processando"], r)
        self.assertEqual(self._esperar(r["id"]), "pronto")
        self.assertFalse([f for f in os.listdir(social.PASTA) if f.startswith("up_")])

    def test_video_cru_recusas_nao_deixam_arquivo(self):
        vid = video(2)
        self.assertEqual(self._cru(Pessoa("caio"), vid, tipo="text/plain")["erro"], "pedido inválido")   # CSRF
        self.assertFalse(self._cru(Pessoa("caio"), vid, sha="00" * 32)["ok"])                            # assinatura não bate
        self.assertIn("formato", self._cru(Pessoa("caio"), b"#EXTM3U\n" + b"x" * 20000)["erro"])
        self.assertFalse([f for f in os.listdir(social.PASTA) if f.startswith("up_")])


if __name__ == "__main__":
    unittest.main()
