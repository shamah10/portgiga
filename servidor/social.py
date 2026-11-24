# -*- coding: utf-8 -*-
"""PTG SOCIAL: rede social dentro da Corretora Portgiga (parecida com o Instagram).

- Qualquer carteira cria um perfil (nome, @usuario, bio, foto) e posta fotos com legenda, marcando a PTG ou
  uma moeda da corretora. Curtir, comentar e seguir. Tudo assinado pela carteira (nada de senha no servidor).
- Ler é público. Escrever exige assinatura válida, nunca repetida (mesmo esquema da corretora).
- Mesmo filtro da feira: nada de reais, pix, lucro, valorização, investimento. Post é opinião de quem postou.
- O dono da corretora pode remover posts e comentários no painel dele (só no PC).
- VÍDEOS (até 60 s): só MP4/MOV/WebM conferidos pelos primeiros bytes; o servidor converte com ffmpeg para um MP4
  leve (máx. 1280 px, sem metadados/GPS, só arquivos locais) numa fila de 1 por vez, gera a capa e só então libera.
"""
import base64
import hashlib
import os
import re
import subprocess
import threading
import time

import corretora as cx

PASTA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dados", "social")
USUARIO_OK = re.compile(r"^[a-z0-9_.]{3,20}$")
MAX_IMG_BYTES = 25_000_000
MAX_VIDEO_BYTES = 95_000_000           # o túnel da Cloudflare recusa pedidos acima de 100 MB
VIDEO_MAX_S = 60                        # o post fica com os primeiros 60 s
VIDEO_FONTE_MAX_S = 15 * 60             # vídeo original maior que isso nem é aberto
_FILA_VIDEO = threading.Semaphore(1)      # converte um vídeo por vez (o PC também minera e serve a rede)
POR_PAGINA = 20


def preparar():
    cx.S.db.executescript("""
        CREATE TABLE IF NOT EXISTS sc_perfis (carteira TEXT PRIMARY KEY, nome TEXT, usuario TEXT UNIQUE, bio TEXT DEFAULT '',
            avatar INTEGER NOT NULL DEFAULT 0, criado REAL);
        CREATE TABLE IF NOT EXISTS sc_posts (id INTEGER PRIMARY KEY AUTOINCREMENT, autor TEXT, legenda TEXT, moeda TEXT DEFAULT '',
            foto INTEGER NOT NULL DEFAULT 0, criado REAL, ativo INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS sc_curtidas (post INTEGER, carteira TEXT, quando REAL, PRIMARY KEY (post, carteira));
        CREATE TABLE IF NOT EXISTS sc_comentarios (id INTEGER PRIMARY KEY AUTOINCREMENT, post INTEGER, autor TEXT, texto TEXT,
            quando REAL, ativo INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS sc_seguindo (seguidor TEXT, alvo TEXT, quando REAL, PRIMARY KEY (seguidor, alvo));
        CREATE INDEX IF NOT EXISTS sc_posts_autor ON sc_posts (autor, id);
        CREATE INDEX IF NOT EXISTS sc_posts_moeda ON sc_posts (moeda, id);
        CREATE INDEX IF NOT EXISTS sc_com_post ON sc_comentarios (post, id);
        CREATE INDEX IF NOT EXISTS sc_seg_alvo ON sc_seguindo (alvo);
        CREATE TABLE IF NOT EXISTS sc_views (post INTEGER, visitante TEXT, dia INTEGER, PRIMARY KEY (post, visitante, dia));
        CREATE TABLE IF NOT EXISTS sc_monet (carteira TEXT PRIMARY KEY, views_contadas INTEGER NOT NULL DEFAULT 0,
            ganho INTEGER NOT NULL DEFAULT 0, resto INTEGER NOT NULL DEFAULT 0);
    """)
    cx.S.db.executescript("""
        CREATE TABLE IF NOT EXISTS sc_notif (id INTEGER PRIMARY KEY AUTOINCREMENT, para TEXT, tipo TEXT, de TEXT, post INTEGER,
            valor INTEGER DEFAULT 0, texto TEXT DEFAULT '', quando REAL);
        CREATE INDEX IF NOT EXISTS sc_notif_para ON sc_notif (para, id);
        CREATE TABLE IF NOT EXISTS sc_gorjetas (id INTEGER PRIMARY KEY AUTOINCREMENT, post INTEGER, de TEXT, para TEXT, unidades INTEGER, quando REAL);
        CREATE TABLE IF NOT EXISTS sc_denuncias (post INTEGER, carteira TEXT, motivo TEXT, quando REAL, PRIMARY KEY (post, carteira));
        CREATE TABLE IF NOT EXISTS sc_desafios (id INTEGER PRIMARY KEY AUTOINCREMENT, titulo TEXT, descricao TEXT, hashtag TEXT,
            premio INTEGER, inicio REAL, fim REAL, encerrado INTEGER NOT NULL DEFAULT 0, vencedores TEXT DEFAULT '');
    """)
    cols = [c[1] for c in cx.S.db.execute("PRAGMA table_info(sc_posts)")]
    for nome, tipo in (("views", "INTEGER NOT NULL DEFAULT 0"), ("anuncio", "INTEGER NOT NULL DEFAULT 0"), ("oculto", "INTEGER NOT NULL DEFAULT 0"),
                       ("views_q", "INTEGER NOT NULL DEFAULT 0"), ("video", "INTEGER NOT NULL DEFAULT 0")):
        if nome not in cols:
            cx.S.db.execute("ALTER TABLE sc_posts ADD COLUMN %s %s" % (nome, tipo))
            if nome == "views_q":      # a monetização passa a contar só visualizações qualificadas
                cx.S.db.execute("UPDATE sc_monet SET views_contadas=0, resto=0")
    # vídeo que estava sendo convertido quando o servidor reiniciou: some (o autor é avisado)
    for pid, autor in cx.S.db.execute("SELECT id, autor FROM sc_posts WHERE ativo=2").fetchall():
        cx.S.db.execute("DELETE FROM sc_posts WHERE id=?", (pid,))
        cx.S.db.execute("INSERT INTO sc_notif (para, tipo, de, post, valor, texto, quando) VALUES (?,?,?,?,?,?,?)",
                        (autor, "video_erro", "", 0, 0, "o servidor reiniciou enquanto preparava seu vídeo: mande de novo", time.time()))
    cx.S.db.commit()
    if os.path.isdir(PASTA):     # envios crus que ficaram pela metade
        for f in os.listdir(PASTA):
            if f.startswith(("up_", "tmp_v")) and f.endswith(".bin"):
                try:
                    os.remove(os.path.join(PASTA, f))
                except OSError:
                    pass


# ---------------------------------------------------------------- imagens
def caminho_foto(pid):
    return os.path.join(PASTA, "p%d.jpg" % int(pid))


def caminho_video(pid):
    return os.path.join(PASTA, "p%d.mp4" % int(pid))


def caminho_avatar(carteira):
    return os.path.join(PASTA, "a_%s.jpg" % carteira)


def _jpeg(dados, lado):
    import io
    from PIL import Image, ImageOps
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except Exception:
        pass
    Image.MAX_IMAGE_PIXELS = 80_000_000
    im = Image.open(io.BytesIO(dados))
    im.seek(0)
    im = ImageOps.exif_transpose(im)
    if im.mode in ("RGBA", "LA", "P", "PA"):
        im = im.convert("RGBA")
        fundo = Image.new("RGB", im.size, (27, 30, 36))
        fundo.paste(im, mask=im.split()[-1])
        im = fundo
    else:
        im = im.convert("RGB")
    im.thumbnail((lado, lado))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=86, optimize=True)
    return out.getvalue()


def _gravar(caminho, dados, lado):
    try:
        jpg = _jpeg(dados, lado)
    except Exception:
        return "não consegui abrir essa imagem: tente tirar print ou salvar como JPG"
    os.makedirs(PASTA, exist_ok=True)
    with open(caminho + ".tmp", "wb") as fh:
        fh.write(jpg)
    os.replace(caminho + ".tmp", caminho)
    return None


def _imagem(a):
    """Lê a imagem base64 do pedido. Devolve (bytes, sha256 hex, erro)."""
    img = a.get("imagem", "")
    if not isinstance(img, str) or len(img) > MAX_IMG_BYTES * 4 // 3 + 16:
        return None, "", "imagem grande demais (máximo 25 MB)"
    if not img:
        return b"", "", None
    try:
        dados = base64.b64decode(img, validate=True)
    except Exception:
        return None, "", "imagem inválida"
    return dados, hashlib.sha256(dados).hexdigest(), None


def url_foto(pid, versao):
    return "/corretora/social/p%d.jpg?v=%d" % (pid, versao) if versao else None


def url_video(pid, versao):
    return "/corretora/social/p%d.mp4?v=%d" % (pid, versao) if versao else None


def _video(a):
    """Lê o vídeo do pedido. Devolve (bytes ou caminho do arquivo cru, sha256 hex, erro)."""
    if a.get("_video_arquivo"):          # enviado cru por /postar_video (não passa pelo JSON)
        return a["_video_arquivo"], a["_video_sha"], None
    v = a.get("video", "")
    if not isinstance(v, str) or len(v) > MAX_VIDEO_BYTES * 4 // 3 + 16:
        return None, "", "vídeo grande demais (máximo 95 MB)"
    if not v:
        return b"", "", None
    try:
        dados = base64.b64decode(v, validate=True)
    except Exception:
        return None, "", "vídeo inválido"
    # só contêineres de vídeo comuns, conferidos pelos primeiros bytes (nada de listas m3u8/concat que o ffmpeg seguiria)
    if not (dados[4:8] == b"ftyp" or dados[:4] == b"\x1a\x45\xdf\xa3"):
        return None, "", "formato não aceito: mande o vídeo em MP4, MOV ou WebM"
    return dados, hashlib.sha256(dados).hexdigest(), None


FORMATOS_VIDEO = {"mov", "mp4", "m4a", "3gp", "3g2", "mj2", "matroska", "webm"}


def _converter_video(entrada, saida, capa):
    """Confere e converte. Devolve None se deu certo, ou o motivo (texto para o autor)."""
    base = ["nice", "-n", "10"]
    seg = ["-protocol_whitelist", "file"]
    try:
        r = subprocess.run(base + ["ffprobe", "-v", "error"] + seg + ["-show_entries", "format=format_name,duration:stream=codec_type,width,height",
                           "-of", "json", entrada], capture_output=True, text=True, timeout=60)
        import json as _json
        info = _json.loads(r.stdout or "{}")
    except Exception:
        return "não consegui abrir esse vídeo"
    fmt = set((info.get("format", {}).get("format_name") or "").split(","))
    if not fmt & FORMATOS_VIDEO:
        return "formato não aceito: mande o vídeo em MP4, MOV ou WebM"
    try:
        duracao = float(info.get("format", {}).get("duration") or 0)
    except ValueError:
        duracao = 0
    if not any(s.get("codec_type") == "video" and (s.get("width") or 0) > 0 for s in info.get("streams", [])):
        return "esse arquivo não tem vídeo"
    if duracao > VIDEO_FONTE_MAX_S:
        return "vídeo longo demais: mande um de até %d minutos (o post fica com os primeiros %d s)" % (VIDEO_FONTE_MAX_S // 60, VIDEO_MAX_S)
    escala = "scale='if(gte(iw,ih),min(1280,iw),-2)':'if(gte(iw,ih),-2,min(1280,ih))',fps=fps='min(30,source_fps)'"
    cmd = base + ["ffmpeg", "-nostdin", "-v", "error", "-y"] + seg + ["-i", entrada, "-t", str(VIDEO_MAX_S),
           "-map", "0:v:0", "-map", "0:a:0?", "-vf", escala, "-c:v", "libx264", "-preset", "veryfast", "-crf", "27",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k", "-ac", "2", "-map_metadata", "-1", "-map_chapters", "-1",
           "-movflags", "+faststart", "-threads", "2", "-f", "mp4", saida]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=600)
    except subprocess.TimeoutExpired:
        return "o vídeo demorou demais para converter: tente um mais curto"
    if r.returncode or not os.path.exists(saida) or os.path.getsize(saida) < 1000:
        return "não consegui converter esse vídeo: tente gravar de novo ou mandar em MP4"
    for ss in ("0.5", "0"):
        subprocess.run(base + ["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", ss, "-i", saida, "-frames:v", "1",
                               "-vf", "scale='min(1080,iw)':-2", "-q:v", "3", capa + ".tmp.jpg"], capture_output=True, timeout=60)
        if os.path.exists(capa + ".tmp.jpg"):
            os.replace(capa + ".tmp.jpg", capa)
            return None
    return "não consegui gerar a capa do vídeo"


def _processar_video(pid, dados, autor):
    tmp = os.path.join(PASTA, "tmp_v%d.bin" % pid)
    saida = caminho_video(pid) + ".tmp.mp4"
    cru = dados if isinstance(dados, str) else None
    erro = None
    with _FILA_VIDEO:
        try:
            os.makedirs(PASTA, exist_ok=True)
            if isinstance(dados, str):       # já está em disco (envio cru)
                os.replace(dados, tmp)
            else:
                with open(tmp, "wb") as fh:
                    fh.write(dados)
            del dados
            erro = _converter_video(tmp, saida, caminho_foto(pid))
            if not erro:
                os.replace(saida, caminho_video(pid))
        except Exception:
            erro = "não consegui processar esse vídeo"
        finally:
            for f in (tmp, saida, cru):
                if not f:
                    continue
                try:
                    os.remove(f)
                except OSError:
                    pass
    with cx.S.trava:
        if erro:
            cx.S.db.execute("DELETE FROM sc_posts WHERE id=? AND ativo=2", (pid,))
            cx.S.db.execute("INSERT INTO sc_notif (para, tipo, de, post, valor, texto, quando) VALUES (?,?,?,?,?,?,?)",
                            (autor, "video_erro", "", 0, 0, erro[:120], time.time()))
        else:
            v = int(time.time() * 1000)
            cx.S.db.execute("UPDATE sc_posts SET foto=?, video=?, ativo=1 WHERE id=? AND ativo=2", (v, v, pid))
        cx.S.db.commit()


def estado_post(pid):
    """Para o autor acompanhar o vídeo que está sendo preparado."""
    with cx.S.trava:
        l = cx.S.db.execute("SELECT ativo FROM sc_posts WHERE id=?", (pid,)).fetchone()
    return {"estado": "pronto" if l and l[0] == 1 else "processando" if l and l[0] == 2 else "falhou"}


def url_avatar(carteira, versao):
    return "/corretora/social/a_%s.jpg?v=%d" % (carteira, versao) if versao else None


# ---------------------------------------------------------------- auxiliares
def _texto_proibido(texto, r):
    return cx.proibido(texto, r)


def _perfil(carteira):
    p = cx.S.db.execute("SELECT nome, usuario, bio, avatar FROM sc_perfis WHERE carteira=?", (carteira,)).fetchone()
    curto = carteira[:7] + "…" + carteira[-4:]
    if not p:
        return {"carteira": carteira, "nome": curto, "usuario": "", "bio": "", "avatar": None, "tem_perfil": False}
    return {"carteira": carteira, "nome": p[0] or curto, "usuario": p[1] or "", "bio": p[2] or "",
            "avatar": url_avatar(carteira, p[3]), "tem_perfil": True}


def _moeda_ok(simbolo):
    if simbolo in ("", "PTG"):
        return True
    r = cx.S.db.execute("SELECT banida FROM cx_moedas WHERE simbolo=?", (simbolo,)).fetchone()
    return bool(r) and not r[0]


def _info_moedas():
    return {s: {"emoji": e, "cor": c, "nome": n} for s, e, c, n in cx.S.db.execute("SELECT simbolo, emoji, cor, nome FROM cx_moedas")}


def _montar(linhas, eu, previa=True):
    """linhas: (id, autor, legenda, moeda, foto, criado)."""
    ids = [l[0] for l in linhas]
    if not ids:
        return []
    marca = ",".join("?" * len(ids))
    curt = dict(cx.S.db.execute("SELECT post, COUNT(*) FROM sc_curtidas WHERE post IN (%s) GROUP BY post" % marca, ids).fetchall())
    coms = dict(cx.S.db.execute("SELECT post, COUNT(*) FROM sc_comentarios WHERE ativo=1 AND post IN (%s) GROUP BY post" % marca, ids).fetchall())
    minhas = set()
    if eu:
        minhas = {p for (p,) in cx.S.db.execute("SELECT post FROM sc_curtidas WHERE carteira=? AND post IN (%s)" % marca, [eu] + ids)}
    vistos = dict(cx.S.db.execute("SELECT id, views FROM sc_posts WHERE id IN (%s)" % marca, ids).fetchall())
    anun = dict(cx.S.db.execute("SELECT id, anuncio FROM sc_posts WHERE id IN (%s) AND anuncio>0" % marca, ids).fetchall())
    gorj = dict(cx.S.db.execute("SELECT post, SUM(unidades) FROM sc_gorjetas WHERE post IN (%s) GROUP BY post" % marca, ids).fetchall())
    vids = dict(cx.S.db.execute("SELECT id, video FROM sc_posts WHERE id IN (%s) AND video>0" % marca, ids).fetchall())
    moedas, perfis, out = _info_moedas(), {}, []
    for pid, autor, legenda, moeda, foto, criado in linhas:
        if autor not in perfis:
            perfis[autor] = _perfil(autor)
        it = {"id": pid, "autor": perfis[autor], "legenda": legenda, "moeda": moeda, "foto": url_foto(pid, foto), "criado": criado,
              "curtidas": curt.get(pid, 0), "comentarios": coms.get(pid, 0), "curti": pid in minhas,
              "views": vistos.get(pid, 0), "gorjetas": cx.S.fmt(gorj.get(pid, 0))}
        if pid in vids:
            it["video"] = url_video(pid, vids[pid])
        if pid in anun:
            an = cx.S.db.execute("SELECT id, titulo, preco, estoque, ativo FROM fx_anuncios WHERE id=? AND vendedor=?", (anun[pid], autor)).fetchone()
            if an and an[4] and an[3] > 0:
                it["anuncio"] = {"id": an[0], "titulo": an[1], "preco": cx.S.fmt(an[2])}
        if moeda and moeda != "PTG":
            it["moeda_info"] = moedas.get(moeda)
        if previa:
            ult = cx.S.db.execute("SELECT autor, texto FROM sc_comentarios WHERE post=? AND ativo=1 ORDER BY id DESC LIMIT 2", (pid,)).fetchall()
            it["ultimos"] = [{"autor": _perfil(au)["nome"], "carteira": au, "texto": t} for au, t in reversed(ult)]
        out.append(it)
    return out


def notificar(para, tipo, de="", post=0, valor=0, texto=""):
    """Chamar com a trava. Ninguém é notificado das próprias ações."""
    if para and para != de:
        cx.S.db.execute("INSERT INTO sc_notif (para, tipo, de, post, valor, texto, quando) VALUES (?,?,?,?,?,?,?)",
                        (para, tipo, de, post, valor, texto[:120], time.time()))


def _autor(pid):
    r = cx.S.db.execute("SELECT autor FROM sc_posts WHERE id=?", (pid,)).fetchone()
    return r[0] if r else ""


# ---------------------------------------------------------------- ações assinadas
def perfil(a):
    nome, usuario, bio = a.get("nome"), a.get("usuario"), a.get("bio")
    if not all(isinstance(v, str) for v in (nome, usuario, bio)):
        return {"ok": False, "erro": "pedido inválido"}
    dados, resumo, erro = _imagem(a)
    if erro:
        return {"ok": False, "erro": erro}
    with cx.S.trava:
        r = cx.regras()
        if r["social_pausada"]:
            return {"ok": False, "erro": "a PTG Social está pausada pelo dono da corretora"}
        n, u, b = cx.S.limpar_texto(nome, 40), usuario.strip().lower(), cx.S.limpar_texto(bio, 160)
        if len(n) < 2:
            return {"ok": False, "erro": "escreva o seu nome (2 letras ou mais)"}
        if not USUARIO_OK.match(u):
            return {"ok": False, "erro": "usuário: 3 a 20 letras minúsculas, números, _ ou ponto"}
        if _texto_proibido(n + " " + u + " " + b, r):
            return {"ok": False, "erro": "a PTG Social é para conteúdo: nada de reais, pix, lucro ou investimento"}
        erro = cx.conferir(a, "portgiga-sc-perfil:", [nome, usuario, bio, resumo])
        if erro:
            return {"ok": False, "erro": erro}
        dono = cx.S.db.execute("SELECT carteira FROM sc_perfis WHERE usuario=?", (u,)).fetchone()
        if dono and dono[0] != a["carteira"]:
            cx.S.db.commit()
            return {"ok": False, "erro": "esse @usuario já é de outra pessoa"}
        cx.S.db.execute("INSERT INTO sc_perfis (carteira, nome, usuario, bio, criado) VALUES (?,?,?,?,?) "
                        "ON CONFLICT(carteira) DO UPDATE SET nome=excluded.nome, usuario=excluded.usuario, bio=excluded.bio",
                        (a["carteira"], n, u, b, time.time()))
        cx.S.db.commit()
    if dados:
        erro = _gravar(caminho_avatar(a["carteira"]), dados, 400)
        if erro:
            return {"ok": False, "erro": erro}
        with cx.S.trava:
            cx.S.db.execute("UPDATE sc_perfis SET avatar=? WHERE carteira=?", (int(time.time() * 1000), a["carteira"]))
            cx.S.db.commit()
    with cx.S.trava:
        return {"ok": True, "perfil": _perfil(a["carteira"])}


def postar(a):
    legenda, moeda, anuncio = a.get("legenda"), a.get("moeda", ""), a.get("anuncio", 0)
    if not isinstance(legenda, str) or not isinstance(moeda, str) or type(anuncio) is not int:
        return {"ok": False, "erro": "pedido inválido"}
    dados, resumo, erro = _imagem(a)
    if erro:
        return {"ok": False, "erro": erro}
    video, resumo_v, erro = _video(a)
    if erro:
        return {"ok": False, "erro": erro}
    if video and dados:
        return {"ok": False, "erro": "mande uma foto ou um vídeo, não os dois"}
    if video:
        resumo = resumo_v
    with cx.S.trava:
        r = cx.regras()
        if r["social_pausada"]:
            return {"ok": False, "erro": "a PTG Social está pausada pelo dono da corretora"}
        if video and not r.get("social_videos", True):
            return {"ok": False, "erro": "os vídeos estão desligados na PTG Social agora: poste uma foto"}
        if video:
            fila = cx.S.db.execute("SELECT COUNT(*), SUM(autor=?) FROM sc_posts WHERE ativo=2", (a["carteira"],)).fetchone()
            if fila[1]:
                return {"ok": False, "erro": "espere o seu outro vídeo ficar pronto antes de mandar mais um"}
            if fila[0] >= 4:
                return {"ok": False, "erro": "muitos vídeos sendo preparados agora: tente de novo em alguns minutos"}
        leg = cx.S.limpar_texto(legenda, 600)
        if not dados and not video and len(leg) < 3:
            return {"ok": False, "erro": "mande uma foto ou escreva alguma coisa"}
        if not _moeda_ok(moeda):
            return {"ok": False, "erro": "essa moeda não existe na corretora"}
        if _texto_proibido(leg, r):
            return {"ok": False, "erro": "a PTG Social é para conteúdo: nada de reais, pix, lucro, valorização ou investimento"}
        erro = cx.conferir(a, "portgiga-sc-postar:", [legenda, moeda, resumo, anuncio])
        if erro:
            return {"ok": False, "erro": erro}
        if anuncio and not cx.S.db.execute("SELECT 1 FROM fx_anuncios WHERE id=? AND vendedor=? AND ativo=1", (anuncio, a["carteira"])).fetchone():
            cx.S.db.commit()
            return {"ok": False, "erro": "esse anúncio da Feira não é seu ou já foi encerrado"}
        hoje = cx.S.db.execute("SELECT COUNT(*) FROM sc_posts WHERE autor=? AND criado>?", (a["carteira"], time.time() - 86400)).fetchone()[0]
        if hoje >= r["social_max_posts_dia"]:
            cx.S.db.commit()
            return {"ok": False, "erro": "limite de %d posts por dia" % r["social_max_posts_dia"]}
        cur = cx.S.db.execute("INSERT INTO sc_posts (autor, legenda, moeda, criado, anuncio, ativo) VALUES (?,?,?,?,?,?)",
                              (a["carteira"], leg, moeda, time.time(), anuncio, 2 if video else 1))
        pid = cur.lastrowid
        cx.S.db.commit()
    if video:       # converte em segundo plano; o post só aparece quando ficar pronto
        threading.Thread(target=_processar_video, args=(pid, video, a["carteira"]), daemon=True).start()
        return {"ok": True, "id": pid, "processando": True}
    if dados:
        erro = _gravar(caminho_foto(pid), dados, 1080)
        if erro:
            with cx.S.trava:
                cx.S.db.execute("DELETE FROM sc_posts WHERE id=?", (pid,))
                cx.S.db.commit()
            return {"ok": False, "erro": erro}
        with cx.S.trava:
            cx.S.db.execute("UPDATE sc_posts SET foto=? WHERE id=?", (int(time.time() * 1000), pid))
            cx.S.db.commit()
    return {"ok": True, "id": pid}


def apagar(a):
    pid = a.get("id")
    if type(pid) is not int:
        return {"ok": False, "erro": "pedido inválido"}
    with cx.S.trava:
        erro = cx.conferir(a, "portgiga-sc-apagar:", [pid])
        if erro:
            return {"ok": False, "erro": erro}
        n = cx.S.db.execute("UPDATE sc_posts SET ativo=0 WHERE id=? AND autor=? AND ativo=1", (pid, a["carteira"])).rowcount
        cx.S.db.commit()
    if n:
        try:
            os.remove(caminho_video(pid))
        except OSError:
            pass
    return {"ok": True} if n else {"ok": False, "erro": "post não encontrado nesta carteira"}


def curtir(a):
    pid, sim = a.get("id"), a.get("curtir")
    if type(pid) is not int or type(sim) is not bool:
        return {"ok": False, "erro": "pedido inválido"}
    with cx.S.trava:
        erro = cx.conferir(a, "portgiga-sc-curtir:", [pid, sim])
        if erro:
            return {"ok": False, "erro": erro}
        if not cx.S.db.execute("SELECT 1 FROM sc_posts WHERE id=? AND ativo=1", (pid,)).fetchone():
            cx.S.db.commit()
            return {"ok": False, "erro": "post não encontrado"}
        if sim:
            if cx.S.db.execute("INSERT OR IGNORE INTO sc_curtidas VALUES (?,?,?)", (pid, a["carteira"], time.time())).rowcount:
                notificar(_autor(pid), "curtida", a["carteira"], pid)
        else:
            cx.S.db.execute("DELETE FROM sc_curtidas WHERE post=? AND carteira=?", (pid, a["carteira"]))
        total = cx.S.db.execute("SELECT COUNT(*) FROM sc_curtidas WHERE post=?", (pid,)).fetchone()[0]
        cx.S.db.commit()
    return {"ok": True, "curtidas": total, "curti": sim}


def comentar(a):
    pid, texto = a.get("id"), a.get("texto")
    if type(pid) is not int or not isinstance(texto, str):
        return {"ok": False, "erro": "pedido inválido"}
    with cx.S.trava:
        r = cx.regras()
        if r["social_pausada"]:
            return {"ok": False, "erro": "a PTG Social está pausada pelo dono da corretora"}
        t = cx.S.limpar_texto(texto, 300)
        if not t:
            return {"ok": False, "erro": "escreva o comentário"}
        if _texto_proibido(t, r):
            return {"ok": False, "erro": "nada de reais, pix, lucro, valorização ou investimento por aqui"}
        erro = cx.conferir(a, "portgiga-sc-comentar:", [pid, texto])
        if erro:
            return {"ok": False, "erro": erro}
        if not cx.S.db.execute("SELECT 1 FROM sc_posts WHERE id=? AND ativo=1", (pid,)).fetchone():
            cx.S.db.commit()
            return {"ok": False, "erro": "post não encontrado"}
        hora = cx.S.db.execute("SELECT COUNT(*) FROM sc_comentarios WHERE autor=? AND quando>?", (a["carteira"], time.time() - 3600)).fetchone()[0]
        if hora >= r["social_max_comentarios_hora"]:
            cx.S.db.commit()
            return {"ok": False, "erro": "calma! limite de %d comentários por hora" % r["social_max_comentarios_hora"]}
        cur = cx.S.db.execute("INSERT INTO sc_comentarios (post, autor, texto, quando) VALUES (?,?,?,?)", (pid, a["carteira"], t, time.time()))
        notificar(_autor(pid), "comentario", a["carteira"], pid, texto=t)
        cx.S.db.commit()
    return {"ok": True, "id": cur.lastrowid}


def apagar_comentario(a):
    cid = a.get("id")
    if type(cid) is not int:
        return {"ok": False, "erro": "pedido inválido"}
    with cx.S.trava:
        erro = cx.conferir(a, "portgiga-sc-apagar-com:", [cid])
        if erro:
            return {"ok": False, "erro": erro}
        # quem comentou ou o dono do post podem apagar
        n = cx.S.db.execute("UPDATE sc_comentarios SET ativo=0 WHERE id=? AND ativo=1 AND (autor=? OR post IN "
                            "(SELECT id FROM sc_posts WHERE autor=?))", (cid, a["carteira"], a["carteira"])).rowcount
        cx.S.db.commit()
    return {"ok": True} if n else {"ok": False, "erro": "comentário não encontrado"}


def seguir(a):
    alvo, sim = a.get("alvo"), a.get("seguir")
    if not isinstance(alvo, str) or type(sim) is not bool:
        return {"ok": False, "erro": "pedido inválido"}
    with cx.S.trava:
        if not cx.S.endereco_valido(alvo) or alvo == a.get("carteira"):
            return {"ok": False, "erro": "perfil inválido"}
        erro = cx.conferir(a, "portgiga-sc-seguir:", [alvo, sim])
        if erro:
            return {"ok": False, "erro": erro}
        if sim:
            if cx.S.db.execute("INSERT OR IGNORE INTO sc_seguindo VALUES (?,?,?)", (a["carteira"], alvo, time.time())).rowcount:
                notificar(alvo, "seguiu", a["carteira"])
        else:
            cx.S.db.execute("DELETE FROM sc_seguindo WHERE seguidor=? AND alvo=?", (a["carteira"], alvo))
        seg = cx.S.db.execute("SELECT COUNT(*) FROM sc_seguindo WHERE alvo=?", (alvo,)).fetchone()[0]
        cx.S.db.commit()
    return {"ok": True, "seguidores": seg, "sigo": sim}


# ---------------------------------------------------------------- leituras públicas
def feed(eu="", antes=0, moeda="", modo="todos", de="", tag=""):
    with cx.S.trava:
        sql, args = "SELECT id, autor, legenda, moeda, foto, criado FROM sc_posts WHERE ativo=1 AND oculto=0", []
        if tag:
            sql += " AND (' ' || lower(legenda) || ' ') LIKE ?"; args.append("%#" + tag.lower() + "%")
        if antes:
            sql += " AND id<?"; args.append(antes)
        if moeda:
            sql += " AND moeda=?"; args.append(moeda)
        if de:
            sql += " AND autor=?"; args.append(de)
        if modo == "seguindo" and eu:
            sql += " AND (autor IN (SELECT alvo FROM sc_seguindo WHERE seguidor=?) OR autor=?)"; args += [eu, eu]
        sql += " ORDER BY id DESC LIMIT %d" % POR_PAGINA
        itens = _montar(cx.S.db.execute(sql, args).fetchall(), eu)
        r = cx.regras()
    return {"itens": itens, "fim": len(itens) < POR_PAGINA, "pausada": r["social_pausada"]}


def post(pid, eu=""):
    with cx.S.trava:
        l = cx.S.db.execute("SELECT id, autor, legenda, moeda, foto, criado FROM sc_posts WHERE id=? AND ativo=1", (pid,)).fetchall()
        if not l:
            return {"erro": "post não encontrado"}
        it = _montar(l, eu, previa=False)[0]
        it["lista"] = [{"id": c, "autor": _perfil(au), "texto": t, "quando": q} for c, au, t, q in cx.S.db.execute(
            "SELECT id, autor, texto, quando FROM sc_comentarios WHERE post=? AND ativo=1 ORDER BY id LIMIT 300", (pid,))]
    return it


# selos comemorativos: quem recebeu recompensa de mineração naquele bloco ganha o selo para sempre
SELOS_BLOCO = {20000: ("🏅", "Testemunha do bloco 20.000")}
_selos_cache = {}


def _recompensados(altura):
    if altura in _selos_cache:
        return _selos_cache[altura]
    r = cx.S.db.execute("SELECT dados FROM blocos WHERE altura=?", (altura,)).fetchone()
    if not r:
        return set()          # o bloco ainda não saiu: não guarda no cache
    import json as _json
    try:
        quem = set((_json.loads(r[0] or "{}").get("recompensas") or {}).keys())
    except ValueError:
        quem = set()
    _selos_cache[altura] = quem
    return quem


def selos(carteira):
    return [{"emoji": e, "nome": n, "bloco": h} for h, (e, n) in sorted(SELOS_BLOCO.items()) if carteira in _recompensados(h)]


def perfil_de(carteira, eu=""):
    if not cx.S.endereco_valido(carteira):
        return {"erro": "perfil inválido"}
    with cx.S.trava:
        p = _perfil(carteira)
        p["posts"] = [{"id": i, "foto": url_foto(i, f), "video": bool(v), "legenda": l[:80], "curtidas": cu, "comentarios": co} for i, l, f, v, cu, co in cx.S.db.execute(
            "SELECT p.id, p.legenda, p.foto, p.video, (SELECT COUNT(*) FROM sc_curtidas WHERE post=p.id), "
            "(SELECT COUNT(*) FROM sc_comentarios WHERE post=p.id AND ativo=1) FROM sc_posts p WHERE autor=? AND ativo=1 AND oculto=0 ORDER BY id DESC LIMIT 120",
            (carteira,))]
        p["n_posts"] = cx.S.db.execute("SELECT COUNT(*) FROM sc_posts WHERE autor=? AND ativo=1", (carteira,)).fetchone()[0]
        p["seguidores"] = cx.S.db.execute("SELECT COUNT(*) FROM sc_seguindo WHERE alvo=?", (carteira,)).fetchone()[0]
        p["seguindo"] = cx.S.db.execute("SELECT COUNT(*) FROM sc_seguindo WHERE seguidor=?", (carteira,)).fetchone()[0]
        p["sigo"] = bool(eu) and bool(cx.S.db.execute("SELECT 1 FROM sc_seguindo WHERE seguidor=? AND alvo=?", (eu, carteira)).fetchone())
        p["moedas"] = [{"simbolo": s, "emoji": e, "cor": c} for s, e, c in cx.S.db.execute(
            "SELECT simbolo, emoji, cor FROM cx_moedas WHERE criador=? AND banida=0 ORDER BY criada", (carteira,))]
        p["monet"] = estado_monet(carteira)
        p["selos"] = selos(carteira)
        u = p.get("usuario")
        p["link"] = ("/u/" + u) if u else ""
    return p


def usuario(nome):
    with cx.S.trava:
        r = cx.S.db.execute("SELECT carteira FROM sc_perfis WHERE usuario=?", (str(nome).strip().lower().lstrip("@"),)).fetchone()
    return {"carteira": r[0]} if r else {"erro": "usuário não encontrado"}


def em_alta():
    """Moedas com mais posts nos últimos 7 dias (a PTG sempre aparece primeiro) + perfis sugeridos."""
    with cx.S.trava:
        lim = time.time() - 7 * 86400
        top = cx.S.db.execute("SELECT moeda, COUNT(*) n FROM sc_posts WHERE ativo=1 AND moeda NOT IN ('', 'PTG') AND criado>? "
                              "GROUP BY moeda ORDER BY n DESC LIMIT 12", (lim,)).fetchall()
        info = _info_moedas()
        moedas = [{"simbolo": "PTG", "posts": cx.S.db.execute("SELECT COUNT(*) FROM sc_posts WHERE ativo=1 AND moeda='PTG' AND criado>?",
                                                               (lim,)).fetchone()[0]}]
        moedas += [dict(simbolo=s, posts=n, **info.get(s, {})) for s, n in top]
        # completa com moedas novas da corretora (para a faixa nunca ficar vazia)
        ja = {m["simbolo"] for m in moedas}
        for s, e, c, nm in cx.S.db.execute("SELECT simbolo, emoji, cor, nome FROM cx_moedas WHERE banida=0 ORDER BY criada DESC LIMIT 12"):
            if s not in ja and len(moedas) < 12:
                moedas.append({"simbolo": s, "posts": 0, "emoji": e, "cor": c, "nome": nm})
        sugest = [_perfil(c) for (c,) in cx.S.db.execute(
            "SELECT p.carteira FROM sc_perfis p LEFT JOIN sc_seguindo s ON s.alvo=p.carteira GROUP BY p.carteira "
            "ORDER BY COUNT(s.seguidor) DESC, p.criado DESC LIMIT 8")]
    return {"moedas": moedas, "perfis": sugest}


# ---------------------------------------------------------------- monetização (PTG por visualização)
VIEWS_HORA_MAX = 400          # visualizações que um mesmo visitante pode contar por hora


def _real(col):
    """Trecho SQL: a carteira em `col` é de alguém de verdade (anti-robô)? Já minerou PTG, ou depositou PTG na corretora
    e tem perfil na Social há alguns dias. Carteira é grátis; mineração e depósito custam trabalho ou PTG de verdade."""
    r = cx.regras()
    minimo = int(round(r["social_real_min_ptg"] * cx.U))
    if minimo <= 0:
        return "1", []
    return ("(EXISTS(SELECT 1 FROM saldos s WHERE s.endereco={c} AND s.minerado>=?) OR "
            "(EXISTS(SELECT 1 FROM cx_movimentos m WHERE m.carteira={c} AND m.tipo='depósito' AND m.unidades>=?) AND "
            "EXISTS(SELECT 1 FROM sc_perfis pf WHERE pf.carteira={c} AND pf.criado<?)))").format(c=col), \
        [minimo, minimo, time.time() - r["social_real_dias"] * 86400]


def eh_real(carteira):
    """Chamar com a trava."""
    sql, args = _real("?")
    if sql == "1":
        return True
    # ordem dos '?': s.endereco, s.minerado, m.carteira, m.unidades, pf.carteira, pf.criado
    return bool(cx.S.db.execute("SELECT " + sql, [carteira, args[0], carteira, args[1], carteira, args[2]]).fetchone()[0])


def _curtidas_recebidas(carteira):
    sql, args = _real("c.carteira")
    return cx.S.db.execute("SELECT COUNT(*) FROM sc_curtidas c JOIN sc_posts p ON p.id=c.post WHERE p.autor=? AND p.ativo=1 "
                           "AND c.carteira<>? AND " + sql, [carteira, carteira] + args).fetchone()[0]


def _seguidores_reais(carteira):
    sql, args = _real("g.seguidor")
    return cx.S.db.execute("SELECT COUNT(*) FROM sc_seguindo g WHERE g.alvo=? AND " + sql, [carteira] + args).fetchone()[0]


def estado_monet(carteira):
    """Chamar com a trava. Diz se a pessoa é criadora monetizada, quanto falta e quanto já ganhou."""
    r = cx.regras()
    seg = _seguidores_reais(carteira)
    curt = _curtidas_recebidas(carteira)
    views = cx.S.db.execute("SELECT COALESCE(SUM(views_q),0) FROM sc_posts WHERE autor=?", (carteira,)).fetchone()[0]
    m = cx.S.db.execute("SELECT ganho FROM sc_monet WHERE carteira=?", (carteira,)).fetchone()
    return {"ativa": bool(r["social_monet_ativa"]), "elegivel": seg >= r["social_monet_min_seguidores"] and curt >= r["social_monet_min_curtidas"],
            "seguidores": seg, "curtidas": curt, "views": views, "min_seguidores": r["social_monet_min_seguidores"],
            "min_curtidas": r["social_monet_min_curtidas"], "ptg_por_mil": r["social_ptg_por_mil"], "ganho": cx.S.fmt(m[0] if m else 0)}


def ver(ids, visitante):
    """Conta visualizações (1 por visitante, por post, por dia). Quem vê o próprio post não conta."""
    if not isinstance(ids, list) or not visitante:
        return {"ok": False}
    ids = [i for i in ids[:30] if type(i) is int]
    dia, agora = int(time.time() // 86400), time.time()
    with cx.S.trava:
        hora = cx.S.db.execute("SELECT COUNT(*) FROM sc_views WHERE visitante=? AND dia=?", (visitante, dia)).fetchone()[0]
        novas = 0
        for pid in ids:
            if hora + novas >= VIEWS_HORA_MAX * 24:
                break
            a = cx.S.db.execute("SELECT autor FROM sc_posts WHERE id=? AND ativo=1", (pid,)).fetchone()
            if not a or a[0] == visitante:
                continue
            # qualificada (vale para a monetização): carteira de verdade, ou visitante sem carteira na 1ª vez
            # do dia em posts DESTE autor (um mesmo IP não infla as visualizações de ninguém)
            if visitante.startswith("ip:"):
                q = not cx.S.db.execute("SELECT 1 FROM sc_views v JOIN sc_posts p ON p.id=v.post WHERE v.visitante=? AND v.dia=? AND p.autor=?",
                                        (visitante, dia, a[0])).fetchone()
            else:
                q = eh_real(visitante)
            if cx.S.db.execute("INSERT OR IGNORE INTO sc_views VALUES (?,?,?)", (pid, visitante, dia)).rowcount:
                cx.S.db.execute("UPDATE sc_posts SET views = views + 1, views_q = views_q + ? WHERE id=?", (1 if q else 0, pid))
                novas += 1
        cx.S.db.execute("DELETE FROM sc_views WHERE dia < ?", (dia - 2,))
        cx.S.db.commit()
    return {"ok": True, "novas": novas}


def pagar_views():
    """Rotina: paga os criadores elegíveis pelas visualizações novas, com PTG do saldo do dono na corretora.
    Visualizações recebidas enquanto a pessoa NÃO era elegível não contam (sem pagamento retroativo).
    Teto diário total: social_monet_teto_dia. Sem saldo do dono, o pagamento espera."""
    with cx.S.trava:
        r = cx.regras()
        por_mil = int(round(r["social_ptg_por_mil"] * cx.U))
        hoje0 = time.time() - 86400
        pago_hoje = -cx.S.db.execute("SELECT COALESCE(SUM(unidades),0) FROM cx_movimentos WHERE carteira=? AND tipo='monetização PTG Social paga' "
                                     "AND quando>?", (cx.DONO, hoje0)).fetchone()[0]
        teto = int(round(r["social_monet_teto_dia"] * cx.U)) - pago_hoje
        for autor, total in cx.S.db.execute("SELECT autor, SUM(views_q) FROM sc_posts GROUP BY autor").fetchall():
            m = cx.S.db.execute("SELECT views_contadas, resto FROM sc_monet WHERE carteira=?", (autor,)).fetchone()
            contadas, resto = m if m else (0, 0)
            novas = total - contadas
            if novas <= 0:
                continue
            if not r["social_monet_ativa"] or autor == cx.DONO or not estado_monet(autor)["elegivel"]:
                cx.S.db.execute("INSERT INTO sc_monet (carteira, views_contadas) VALUES (?,?) ON CONFLICT(carteira) DO UPDATE SET views_contadas=?",
                                (autor, total, total))
                continue
            mil, sobra = divmod(novas + resto, 1000)            # paga a cada 1000 visualizações completas
            devido = mil * por_mil                               # unidades de PTG
            if devido <= 0:
                cx.S.db.execute("INSERT INTO sc_monet (carteira, views_contadas, resto) VALUES (?,?,?) ON CONFLICT(carteira) DO UPDATE "
                                "SET views_contadas=excluded.views_contadas, resto=excluded.resto", (autor, total, novas + resto))
                continue
            if devido > teto or cx.ptg_de(cx.DONO) < devido:
                continue                                          # espera: teto do dia ou saldo do dono
            cx.mover_ptg(cx.DONO, -devido)
            cx.mover_ptg(autor, devido)
            cx.mov(autor, "monetização PTG Social", devido, "%d visualizações" % (novas + resto - sobra))
            cx.mov(cx.DONO, "monetização PTG Social paga", -devido, autor)
            notificar(autor, "monetizacao", "", 0, devido, "%d visualizações" % (mil * 1000))
            teto -= devido
            cx.S.db.execute("INSERT INTO sc_monet (carteira, views_contadas, ganho, resto) VALUES (?,?,?,?) ON CONFLICT(carteira) DO UPDATE "
                            "SET views_contadas=excluded.views_contadas, ganho=ganho+excluded.ganho, resto=excluded.resto",
                            (autor, total, devido, sobra))
        cx.S.db.commit()


# ---------------------------------------------------------------- gorjeta em PTG
def gorjeta(a):
    pid, unidades = a.get("id"), a.get("unidades")
    if type(pid) is not int or type(unidades) is not int:
        return {"ok": False, "erro": "pedido inválido"}
    with cx.S.trava:
        if not cx.U // 100 <= unidades <= 10_000 * cx.U:
            return {"ok": False, "erro": "gorjeta de 0,01 até 10.000 PTG"}
        erro = cx.conferir(a, "portgiga-sc-gorjeta:", [pid, unidades])
        if erro:
            return {"ok": False, "erro": erro}
        cx.S.db.commit()
        autor = cx.S.db.execute("SELECT autor FROM sc_posts WHERE id=? AND ativo=1", (pid,)).fetchone()
        if not autor:
            return {"ok": False, "erro": "post não encontrado"}
        autor = autor[0]
        if autor == a["carteira"]:
            return {"ok": False, "erro": "você não pode mandar gorjeta para você mesmo"}
        if cx.ptg_de(a["carteira"]) < unidades:
            return {"ok": False, "erro": "saldo de PTG na corretora insuficiente: deposite PTG em Minha conta"}
        cx.mover_ptg(a["carteira"], -unidades)
        cx.mover_ptg(autor, unidades)
        cx.mov(a["carteira"], "gorjeta enviada (PTG Social)", -unidades, "post %d" % pid)
        cx.mov(autor, "gorjeta recebida (PTG Social)", unidades, "post %d" % pid)
        cx.S.db.execute("INSERT INTO sc_gorjetas (post, de, para, unidades, quando) VALUES (?,?,?,?,?)", (pid, a["carteira"], autor, unidades, time.time()))
        notificar(autor, "gorjeta", a["carteira"], pid, unidades)
        total = cx.S.db.execute("SELECT SUM(unidades) FROM sc_gorjetas WHERE post=?", (pid,)).fetchone()[0]
        cx.S.db.commit()
    return {"ok": True, "gorjetas": cx.S.fmt(total)}


# ---------------------------------------------------------------- notificações (leitura pública: curtidas e seguidores já são públicos)
def notificacoes(carteira):
    if not cx.S.endereco_valido(carteira):
        return {"itens": []}
    with cx.S.trava:
        lista = []
        for i, tipo, de, post_, valor, texto, quando in cx.S.db.execute(
                "SELECT id, tipo, de, post, valor, texto, quando FROM sc_notif WHERE para=? ORDER BY id DESC LIMIT 60", (carteira,)):
            foto = None
            if post_:
                r = cx.S.db.execute("SELECT foto FROM sc_posts WHERE id=?", (post_,)).fetchone()
                foto = url_foto(post_, r[0]) if r else None
            lista.append({"id": i, "tipo": tipo, "de": _perfil(de) if de else None, "post": post_, "foto": foto,
                          "valor": cx.S.fmt(valor), "texto": texto, "quando": quando})
    return {"itens": lista}


# ---------------------------------------------------------------- denúncias
def denunciar(a):
    pid, motivo = a.get("id"), a.get("motivo", "")
    if type(pid) is not int or not isinstance(motivo, str):
        return {"ok": False, "erro": "pedido inválido"}
    with cx.S.trava:
        erro = cx.conferir(a, "portgiga-sc-denunciar:", [pid, motivo])
        if erro:
            return {"ok": False, "erro": erro}
        if not cx.S.db.execute("SELECT 1 FROM sc_posts WHERE id=? AND ativo=1", (pid,)).fetchone():
            cx.S.db.commit()
            return {"ok": False, "erro": "post não encontrado"}
        cx.S.db.execute("INSERT OR REPLACE INTO sc_denuncias VALUES (?,?,?,?)", (pid, a["carteira"], cx.S.limpar_texto(motivo, 200), time.time()))
        n = cx.S.db.execute("SELECT COUNT(*) FROM sc_denuncias WHERE post=?", (pid,)).fetchone()[0]
        if n >= cx.regras()["social_denuncias_ocultar"]:
            cx.S.db.execute("UPDATE sc_posts SET oculto=1 WHERE id=?", (pid,))
        cx.S.db.commit()
    return {"ok": True}


def denuncias():
    with cx.S.trava:
        linhas = cx.S.db.execute("SELECT p.id, p.autor, p.legenda, p.moeda, p.foto, p.criado, COUNT(d.carteira), GROUP_CONCAT(d.motivo, ' | '), p.oculto "
                                 "FROM sc_denuncias d JOIN sc_posts p ON p.id=d.post WHERE p.ativo=1 GROUP BY p.id ORDER BY COUNT(d.carteira) DESC").fetchall()
        itens = _montar([l[:6] for l in linhas], "", previa=False)
        for it, l in zip(itens, linhas):
            it["denuncias"], it["motivos"], it["oculto"] = l[6], l[7] or "", bool(l[8])
    return {"itens": itens}


def decidir_denuncia(pid, remover_post):
    with cx.S.trava:
        if remover_post:
            cx.S.db.execute("UPDATE sc_posts SET ativo=0 WHERE id=?", (pid,))
        else:
            cx.S.db.execute("UPDATE sc_posts SET oculto=0 WHERE id=?", (pid,))
        cx.S.db.execute("DELETE FROM sc_denuncias WHERE post=?", (pid,))
        cx.S.db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- desafios da semana
TAG_OK = re.compile(r"^[a-z0-9_]{3,30}$")
DIVISAO = (50, 30, 20)          # % do prêmio para 1º, 2º e 3º


def criar_desafio(titulo, descricao, hashtag, premio_ptg, dias):
    try:
        premio, dias = int(round(float(premio_ptg) * cx.U)), float(dias)
    except (TypeError, ValueError):
        return {"ok": False, "erro": "prêmio ou dias inválidos"}
    tag = str(hashtag or "").strip().lstrip("#").lower()
    tit, desc = cx.S.limpar_texto(titulo, 80), cx.S.limpar_texto(descricao, 400)
    if len(tit) < 3 or not TAG_OK.match(tag) or not 0 <= premio <= 100_000 * cx.U or not 0.04 <= dias <= 60:
        return {"ok": False, "erro": "preencha título, hashtag (letras/números), prêmio e duração (até 60 dias)"}
    with cx.S.trava:
        if cx.S.db.execute("SELECT 1 FROM sc_desafios WHERE encerrado=0").fetchone():
            return {"ok": False, "erro": "já existe um desafio aberto: encerre ou espere ele acabar"}
        agora = time.time()
        cx.S.db.execute("INSERT INTO sc_desafios (titulo, descricao, hashtag, premio, inicio, fim) VALUES (?,?,?,?,?,?)",
                        (tit, desc, tag, premio, agora, agora + dias * 86400))
        cx.S.db.commit()
    return {"ok": True}


def _ranking(d):
    """d = (id, titulo, descricao, hashtag, premio, inicio, fim, encerrado, vencedores)."""
    sql, args = _real("c.carteira")      # no desafio só valem curtidas de carteiras de verdade
    return cx.S.db.execute(
        "SELECT p.id, p.autor, (SELECT COUNT(*) FROM sc_curtidas c WHERE c.post=p.id AND c.carteira<>p.autor AND " + sql + ") n FROM sc_posts p "
        "WHERE p.ativo=1 AND p.oculto=0 AND p.criado BETWEEN ? AND ? AND (' ' || lower(p.legenda) || ' ') LIKE ? ORDER BY n DESC, p.id LIMIT 30",
        args + [d[5], d[6], "%#" + d[3] + "%"]).fetchall()


def desafio_atual():
    with cx.S.trava:
        d = cx.S.db.execute("SELECT id, titulo, descricao, hashtag, premio, inicio, fim, encerrado, vencedores FROM sc_desafios "
                            "ORDER BY encerrado, id DESC LIMIT 1").fetchone()
        if not d:
            return {"desafio": None}
        rk, vistos, top = _ranking(d), set(), []
        for pid, autor, n in rk:
            if autor in vistos:
                continue
            vistos.add(autor)
            top.append({"post": pid, "autor": _perfil(autor), "curtidas": n})
            if len(top) == 3:
                break
        out = {"id": d[0], "titulo": d[1], "descricao": d[2], "hashtag": d[3], "premio": cx.S.fmt(d[4]), "fim": d[6],
               "encerrado": bool(d[7]), "participantes": len({a for _, a, _ in rk}), "top": top, "divisao": DIVISAO}
    if out["encerrado"] and time.time() - d[6] > 7 * 86400:
        return {"desafio": None}
    return {"desafio": out}


def encerrar_desafios(agora=None):
    """Rotina: desafio vencido paga os 3 posts mais curtidos (pessoas diferentes) com PTG do saldo do dono."""
    agora = agora or time.time()
    with cx.S.trava:
        for d in cx.S.db.execute("SELECT id, titulo, descricao, hashtag, premio, inicio, fim, encerrado, vencedores FROM sc_desafios "
                                 "WHERE encerrado=0 AND fim<?", (agora,)).fetchall():
            vencedores, vistos = [], set()
            for pid, autor, n in _ranking(d):
                if autor in vistos or autor == cx.DONO or n == 0:
                    continue
                vistos.add(autor)
                vencedores.append((pid, autor))
                if len(vencedores) == 3:
                    break
            pagos = []
            for (pid, autor), pct in zip(vencedores, DIVISAO):
                valor = d[4] * pct // 100
                if valor and cx.ptg_de(cx.DONO) >= valor:
                    cx.mover_ptg(cx.DONO, -valor)
                    cx.mover_ptg(autor, valor)
                    cx.mov(autor, "prêmio do desafio (PTG Social)", valor, "#" + d[3])
                    cx.mov(cx.DONO, "prêmio de desafio pago", -valor, autor)
                    notificar(autor, "desafio", "", pid, valor, "#" + d[3])
                    pagos.append(autor)
            cx.S.db.execute("UPDATE sc_desafios SET encerrado=1, vencedores=? WHERE id=?", (",".join(pagos), d[0]))
        cx.S.db.commit()


def encerrar_desafio_agora():
    with cx.S.trava:
        cx.S.db.execute("UPDATE sc_desafios SET fim=? WHERE encerrado=0", (time.time() - 1,))
        cx.S.db.commit()
    encerrar_desafios()
    return {"ok": True}


# ---------------------------------------------------------------- conquista de mineração (para o post automático)
def mineracao(carteira):
    if not cx.S.endereco_valido(carteira):
        return {"erro": "carteira inválida"}
    with cx.S.trava:
        r = cx.S.db.execute("SELECT minerado FROM saldos WHERE endereco=?", (carteira,)).fetchone()
        maquinas = sum(1 for (c, _i), m in list(cx.S.mineradores.items()) if c == carteira and time.time() - m.get("visto", 0) < 60) \
            if hasattr(cx.S, "mineradores") else 0
        altura = cx.S.db.execute("SELECT COALESCE(MAX(altura),0) FROM blocos").fetchone()[0]
    return {"minerado": cx.S.fmt(r[0] if r else 0), "minerado_unidades": r[0] if r else 0, "maquinas": maquinas, "bloco": altura}


# ---------------------------------------------------------------- meus anúncios da Feira (para ligar ao post)
def meus_anuncios(carteira):
    with cx.S.trava:
        return {"itens": [{"id": i, "titulo": t, "preco": cx.S.fmt(p)} for i, t, p in cx.S.db.execute(
            "SELECT id, titulo, preco FROM fx_anuncios WHERE vendedor=? AND ativo=1 AND estoque>0 ORDER BY id DESC LIMIT 50", (carteira,))]}


# ---------------------------------------------------------------- página pública /u/<usuario> (prévia bonita ao divulgar o link)
def pagina_publica(usuario_):
    import html
    with cx.S.trava:
        r = cx.S.db.execute("SELECT carteira FROM sc_perfis WHERE usuario=?", (str(usuario_).lower(),)).fetchone()
        p = _perfil(r[0]) if r else None
        if p:
            p["n_posts"] = cx.S.db.execute("SELECT COUNT(*) FROM sc_posts WHERE autor=? AND ativo=1 AND oculto=0", (r[0],)).fetchone()[0]
            p["seguidores"] = cx.S.db.execute("SELECT COUNT(*) FROM sc_seguindo WHERE alvo=?", (r[0],)).fetchone()[0]
    if not p:
        return None
    e = html.escape
    titulo = "%s (@%s) · PTG Social" % (p["nome"], p["usuario"])
    desc = "%d posts · %d seguidores. %s" % (p["n_posts"], p["seguidores"], p["bio"] or "Na PTG Social, a rede da Portgiga.")
    img = p["avatar"] or "/app/icones/icone-512.png"
    destino = "/corretora#@" + p["usuario"]
    return ("<!doctype html><html lang=pt-BR><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>%s</title><meta name=description content='%s'><meta property=og:title content='%s'><meta property=og:description content='%s'>"
            "<meta property=og:image content='%s'><meta property=og:type content=profile><meta name=twitter:card content=summary>"
            "<meta http-equiv=refresh content='0;url=%s'><style>body{background:#14161b;color:#eef2f7;font:16px system-ui;display:grid;place-items:center;"
            "min-height:100vh;margin:0}a{color:#22d3ff}</style></head><body><p>Abrindo o perfil de <b>%s</b> na PTG Social… <a href='%s'>abrir</a></p>"
            "<script>location.replace(%s)</script></body></html>") % (
        e(titulo), e(desc, True), e(titulo, True), e(desc, True), e(img, True), e(destino, True), e(p["nome"]), e(destino, True),
        json_str(destino))


def json_str(v):
    import json
    return json.dumps(v)


# ---------------------------------------------------------------- dono
def painel_dono():
    with cx.S.trava:
        ult = _montar(cx.S.db.execute("SELECT id, autor, legenda, moeda, foto, criado FROM sc_posts WHERE ativo=1 ORDER BY id DESC LIMIT 40").fetchall(), "")
        tot = {k: cx.S.db.execute(q).fetchone()[0] for k, q in (
            ("posts", "SELECT COUNT(*) FROM sc_posts WHERE ativo=1"), ("perfis", "SELECT COUNT(*) FROM sc_perfis"),
            ("curtidas", "SELECT COUNT(*) FROM sc_curtidas"), ("comentarios", "SELECT COUNT(*) FROM sc_comentarios WHERE ativo=1"),
            ("views", "SELECT COALESCE(SUM(views),0) FROM sc_posts"))}
        tot["pago"] = cx.S.fmt(cx.S.db.execute("SELECT COALESCE(SUM(ganho),0) FROM sc_monet").fetchone()[0])
        tot["criadores"] = [dict(nome=_perfil(c)["nome"], ganho=cx.S.fmt(g)) for c, g in
                            cx.S.db.execute("SELECT carteira, ganho FROM sc_monet WHERE ganho>0 ORDER BY ganho DESC LIMIT 10")]
    return {"posts": ult, "totais": tot}


def remover(pid=None, cid=None):
    with cx.S.trava:
        n = 0
        if type(pid) is int:
            n = cx.S.db.execute("UPDATE sc_posts SET ativo=0 WHERE id=?", (pid,)).rowcount
        elif type(cid) is int:
            n = cx.S.db.execute("UPDATE sc_comentarios SET ativo=0 WHERE id=?", (cid,)).rowcount
        cx.S.db.commit()
    return {"ok": bool(n)}


# ---------------------------------------------------------------- rotas (/api/cx/social/...)
def _int(v, padrao=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return padrao


# ---------------------------------------------------------------- proteção contra abuso (por IP, em memória)
import threading as _th
_janelas, _jtrava = {}, _th.Lock()
LIMITES_IP = {"escrita": (240, 600), "imagem": (40, 3600)}    # (quantas, em quantos segundos)
DISCO_LIVRE_MIN = 3 * 1024 ** 3                                 # sem 3 GB livres, ninguém manda foto


def _excedeu(ip, tipo):
    n, janela = LIMITES_IP[tipo]
    agora = time.time()
    with _jtrava:
        lista = [t for t in _janelas.get((ip, tipo), []) if agora - t < janela]
        if len(lista) >= n:
            _janelas[(ip, tipo)] = lista
            return True
        lista.append(agora)
        _janelas[(ip, tipo)] = lista
        if len(_janelas) > 20000:
            for k in [k for k, v in _janelas.items() if not v or agora - v[-1] > 3600]:
                del _janelas[k]
    return False


def _disco_ok():
    import shutil
    try:
        return shutil.disk_usage(os.path.dirname(PASTA)).free >= DISCO_LIVRE_MIN
    except OSError:
        return False


def _postar_video(h, do_pc, ip):
    """Vídeo enviado cru (corpo = o arquivo, campos assinados no cabeçalho X-Pedido em base64 de JSON).
    Assim o celular não precisa montar uma cópia base64 de dezenas de MB na memória."""
    import json as _json
    # application/octet-stream não é um tipo "simples": outro site não consegue mandar isso com o cookie de quem visita
    if (h.headers.get("Content-Type") or "").split(";")[0].strip() != "application/octet-stream":
        return h._resp({"ok": False, "erro": "pedido inválido"}, codigo=400)
    try:
        a = _json.loads(base64.b64decode(h.headers.get("X-Pedido") or "", validate=True).decode("utf-8"))
        assert isinstance(a, dict)
    except Exception:
        return h._resp({"ok": False, "erro": "pedido inválido"}, codigo=400)
    for k in [k for k in a if k.startswith("_") or k in ("imagem", "video")]:
        a.pop(k)
    if not cx.S.endereco_valido(a.get("carteira", "")):
        return h._resp({"ok": False, "erro": "pedido inválido"}, codigo=400)
    n = int(h.headers.get("Content-Length") or 0)
    if n < 10000:       # abaixo disso o servidor já leu o corpo como JSON (e não é um vídeo de verdade)
        return h._resp({"ok": False, "erro": "esse arquivo não parece um vídeo"}, codigo=400)
    if n > MAX_VIDEO_BYTES:
        return h._resp({"ok": False, "erro": "vídeo grande demais: o máximo é %d MB" % (MAX_VIDEO_BYTES // 1_000_000)}, codigo=413)
    if not _disco_ok():
        return h._resp({"ok": False, "erro": "o servidor está sem espaço para vídeos agora: poste só texto"}, codigo=507)
    if not do_pc and _excedeu(ip, "imagem"):
        return h._resp({"ok": False, "erro": "muitos envios deste aparelho na última hora: espere um pouco"}, codigo=429)
    os.makedirs(PASTA, exist_ok=True)
    arq = os.path.join(PASTA, "up_%s.bin" % os.urandom(8).hex())
    sha, inicio, falta = hashlib.sha256(), b"", n
    try:
        with open(arq, "wb") as fh:
            while falta:
                bloco = h.rfile.read(min(falta, 1 << 20))
                if not bloco:
                    raise IOError("envio interrompido")
                if len(inicio) < 16:
                    inicio += bloco[:16]
                sha.update(bloco)
                fh.write(bloco)
                falta -= len(bloco)
        if not (inicio[4:8] == b"ftyp" or inicio[:4] == b"\x1a\x45\xdf\xa3"):
            r = {"ok": False, "erro": "formato não aceito: mande o vídeo em MP4, MOV ou WebM"}
        else:
            a["_video_arquivo"], a["_video_sha"] = arq, sha.hexdigest()
            r = postar(a)
    except Exception:
        r = {"ok": False, "erro": "o envio do vídeo foi interrompido: tente de novo"}
    if not r.get("processando"):       # recusado: o arquivo cru não fica no PC
        try:
            os.remove(arq)
        except OSError:
            pass
    return h._resp(r)


def rota(h, acao, a, do_pc, ip=""):
    post_ = h.command == "POST"
    if acao == "ver" and post_:
        if not h._json():
            return h._resp({"ok": False}, codigo=400)
        # a visualização só conta como da carteira se vier assinada por ela (senão alguém "veria" no nome dos outros)
        quem = a.get("carteira", "")
        if not (isinstance(a.get("ids"), list) and cx.S.endereco_valido(quem)
                and not cx.S.pedido_assinado(a, "portgiga-sc-ver:", [a.get("ids")])):
            quem = "ip:" + hashlib.sha256(("portgiga-views:" + str(ip)).encode()).hexdigest()[:20]
        return h._resp(ver(a.get("ids"), quem))
    if post_ and not do_pc and _excedeu(ip, "escrita"):
        return h._resp({"ok": False, "erro": "muitas ações seguidas deste aparelho: espere alguns minutos"}, codigo=429)
    eu = a.get("eu", "")
    eu = eu if isinstance(eu, str) and cx.S.endereco_valido(eu) else ""
    if acao == "feed":
        tag = str(a.get("tag", "")).lstrip("#").lower()
        return h._resp(feed(eu, _int(a.get("antes")), str(a.get("moeda", ""))[:8], a.get("modo", "todos"), a.get("de", ""),
                            tag if TAG_OK.match(tag) else ""))
    if acao == "notif":
        return h._resp(notificacoes(a.get("c", "")))
    if acao == "desafio":
        return h._resp(desafio_atual())
    if acao == "mineracao":
        return h._resp(mineracao(a.get("c", "")))
    if acao == "meus_anuncios":
        return h._resp(meus_anuncios(a.get("c", "")))
    if acao == "post":
        return h._resp(post(_int(a.get("id"), -1), eu))
    if acao == "estado":
        return h._resp(estado_post(_int(a.get("id"), -1)))
    if acao == "perfil":
        return h._resp(perfil_de(a.get("c", ""), eu))
    if acao == "usuario":
        return h._resp(usuario(a.get("u", "")))
    if acao == "alta":
        return h._resp(em_alta())
    if acao == "postar_video" and post_:
        return _postar_video(h, do_pc, ip)
    if acao in ("postar", "perfil_salvar") and post_:
        if not h._json():
            return h._resp({"ok": False, "erro": "pedido inválido"}, codigo=400)
        grande = int(h.headers.get("Content-Length") or 0) > 20000
        if grande and not _disco_ok():
            return h._resp({"ok": False, "erro": "o servidor está sem espaço para fotos agora: poste só texto"}, codigo=507)
        if grande and not do_pc and _excedeu(ip, "imagem"):
            return h._resp({"ok": False, "erro": "muitas fotos deste aparelho na última hora: espere um pouco"}, codigo=429)
        if "carteira" not in a:   # corpo grande (com imagem): _args só lê até 10 KB
            import json as _json
            n = int(h.headers.get("Content-Length") or 0)
            if not 0 < n <= max(MAX_IMG_BYTES, MAX_VIDEO_BYTES if acao == "postar" else 0) * 4 // 3 + 8192:
                return h._resp({"ok": False, "erro": "arquivo grande demais (foto até 25 MB, vídeo até 60 MB)"}, codigo=413)
            try:
                a.update(_json.loads(h.rfile.read(n).decode("utf-8")))
            except Exception:
                return h._resp({"ok": False, "erro": "pedido inválido"}, codigo=400)
        return h._resp(postar(a) if acao == "postar" else perfil(a))
    if acao in ("apagar", "curtir", "comentar", "apagar_com", "seguir", "gorjeta", "denunciar") and post_:
        if not h._json():
            return h._resp({"ok": False, "erro": "pedido inválido"}, codigo=400)
        return h._resp({"apagar": apagar, "curtir": curtir, "comentar": comentar, "apagar_com": apagar_comentario,
                        "seguir": seguir, "gorjeta": gorjeta, "denunciar": denunciar}[acao](a))
    if acao.startswith("dono/"):
        if not do_pc:
            return h._resp({"ok": False, "erro": "só no PC da corretora"}, codigo=403)
        if acao == "dono/painel":
            return h._resp(painel_dono())
        if acao == "dono/remover" and post_ and h._json():
            return h._resp(remover(a.get("post"), a.get("comentario")))
        if acao == "dono/denuncias":
            return h._resp(denuncias())
        if acao == "dono/decidir" and post_ and h._json():
            return h._resp(decidir_denuncia(_int(a.get("post"), -1), bool(a.get("remover"))))
        if acao == "dono/desafio" and post_ and h._json():
            return h._resp(criar_desafio(a.get("titulo"), a.get("descricao", ""), a.get("hashtag"), a.get("premio", 0), a.get("dias", 7)))
        if acao == "dono/desafio_encerrar" and post_ and h._json():
            return h._resp(encerrar_desafio_agora())
    return h._resp({"erro": "não encontrado"}, codigo=404)
