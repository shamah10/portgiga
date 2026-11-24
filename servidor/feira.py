# -*- coding: utf-8 -*-
"""FEIRA PORTGIGA: qualquer pessoa anuncia qualquer coisa (produto ou serviço) e vende por PTG.

- Só PTG, nunca reais/pix/outras moedas (o filtro de palavras do mural vale aqui também).
- Pagamento protegido: ao comprar, o PTG sai do saldo da corretora do comprador e fica GUARDADO.
  Só vai para o vendedor quando o comprador confirma que recebeu (ou, se o vendedor marcou como enviado
  e ninguém reclamou, depois de N dias). O vendedor pode cancelar e devolver. O comprador pode abrir
  disputa, e quem decide é o dono da corretora, no painel dele.
- Taxa da feira (% do preço) vai para o dono quando a venda se conclui.
- Conversa do pedido: só comprador e vendedor leem (pedido assinado para ler).
"""
import io
import os
import time

import corretora as cx

CAPAS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dados", "capas")

CATEGORIAS = {"servicos": "🛠️ Serviços", "eletronicos": "📱 Eletrônicos", "roupas": "👕 Roupas", "jogos": "🎮 Jogos",
              "comida": "🍰 Comida", "arte": "🎨 Arte", "aulas": "📚 Aulas", "casa": "🏠 Casa", "outros": "📦 Outros"}
ESTADOS_ABERTOS = ("pago", "enviado", "disputa")


def preparar():
    cx.S.db.executescript("""
        CREATE TABLE IF NOT EXISTS fx_anuncios (id INTEGER PRIMARY KEY AUTOINCREMENT, vendedor TEXT, titulo TEXT, descricao TEXT,
            categoria TEXT, preco INTEGER, estoque INTEGER, criado REAL, ativo INTEGER NOT NULL DEFAULT 1, vendidos INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS fx_pedidos (id INTEGER PRIMARY KEY AUTOINCREMENT, anuncio INTEGER, comprador TEXT, vendedor TEXT,
            titulo TEXT, preco INTEGER, estado TEXT, criado REAL, atualizado REAL, enviado_em REAL, motivo TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS fx_mensagens (id INTEGER PRIMARY KEY AUTOINCREMENT, pedido INTEGER, autor TEXT, texto TEXT, quando REAL);
        CREATE INDEX IF NOT EXISTS fx_ped_comp ON fx_pedidos (comprador);
        CREATE INDEX IF NOT EXISTS fx_ped_vend ON fx_pedidos (vendedor);
    """)
    cx.S.db.commit()


def em_custodia():
    """PTG guardado em pedidos em andamento (entra na conta de solvência da corretora)."""
    return cx.S.db.execute("SELECT COALESCE(SUM(preco),0) FROM fx_pedidos WHERE estado IN ('pago','enviado','disputa')").fetchone()[0]


def _pedido(pid):
    r = cx.S.db.execute("SELECT id, anuncio, comprador, vendedor, titulo, preco, estado, criado, atualizado, enviado_em, motivo "
                        "FROM fx_pedidos WHERE id=?", (pid,)).fetchone()
    if not r:
        return None
    return dict(zip(("id", "anuncio", "comprador", "vendedor", "titulo", "preco", "estado", "criado", "atualizado", "enviado_em", "motivo"), r))


def _estado(pid, estado, motivo=None):
    if motivo is None:
        cx.S.db.execute("UPDATE fx_pedidos SET estado=?, atualizado=? WHERE id=?", (estado, time.time(), pid))
    else:
        cx.S.db.execute("UPDATE fx_pedidos SET estado=?, atualizado=?, motivo=? WHERE id=?", (estado, time.time(), motivo, pid))


def _pagar_vendedor(p, r):
    taxa = p["preco"] * int(round(r["taxa_feira_pct"] * 100)) // 10000
    cx.mover_ptg(p["vendedor"], p["preco"] - taxa)
    cx.mover_ptg(cx.DONO, taxa)
    cx.mov(p["vendedor"], "venda na feira", p["preco"] - taxa, "pedido %d" % p["id"])
    cx.mov(cx.DONO, "taxa da feira recebida", taxa, "pedido %d" % p["id"])
    _estado(p["id"], "concluido")


def _devolver(p, estado):
    cx.mover_ptg(p["comprador"], p["preco"])
    cx.mov(p["comprador"], "devolução da feira", p["preco"], "pedido %d" % p["id"])
    cx.S.db.execute("UPDATE fx_anuncios SET estoque = estoque + 1, vendidos = vendidos - 1 WHERE id=?", (p["anuncio"],))
    _estado(p["id"], estado)


# ---------------------------------------------------------------- ações assinadas
def anunciar(a):
    titulo, descricao, categoria, preco, estoque = (a.get(k) for k in ("titulo", "descricao", "categoria", "preco", "estoque"))
    if not isinstance(titulo, str) or not isinstance(descricao, str) or categoria not in CATEGORIAS \
            or type(preco) is not int or type(estoque) is not int:
        return {"ok": False, "erro": "pedido inválido"}
    with cx.S.trava:
        r = cx.regras()
        if r["feira_pausada"]:
            return {"ok": False, "erro": "a feira está pausada pelo dono da corretora"}
        tit, desc = cx.S.limpar_texto(titulo, 70), cx.S.limpar_texto(descricao, 600)
        if len(tit) < 3:
            return {"ok": False, "erro": "escreva o que você está vendendo (3 letras ou mais)"}
        if not 0 < preco <= 10_000_000 * cx.U or not 1 <= estoque <= 999:
            return {"ok": False, "erro": "preço ou quantidade inválidos"}
        if cx.proibido(tit + " " + desc, r):
            return {"ok": False, "erro": "a feira é para trocar coisas e serviços por PTG: nada de reais, pix, dinheiro ou outras moedas"}
        erro = cx.conferir(a, "portgiga-fx-anunciar:", [titulo, descricao, categoria, preco, estoque])
        if erro:
            return {"ok": False, "erro": erro}
        abertos = cx.S.db.execute("SELECT COUNT(*) FROM fx_anuncios WHERE vendedor=? AND ativo=1", (a["carteira"],)).fetchone()[0]
        if abertos >= r["max_anuncios"]:
            cx.S.db.commit()
            return {"ok": False, "erro": "limite de %d anúncios abertos por carteira" % r["max_anuncios"]}
        cur = cx.S.db.execute("INSERT INTO fx_anuncios (vendedor, titulo, descricao, categoria, preco, estoque, criado) VALUES (?,?,?,?,?,?,?)",
                              (a["carteira"], tit, desc, categoria, preco, estoque, time.time()))
        cx.S.db.commit()
    return {"ok": True, "id": cur.lastrowid}


def encerrar(a):
    aid = a.get("id")
    if type(aid) is not int:
        return {"ok": False, "erro": "pedido inválido"}
    with cx.S.trava:
        erro = cx.conferir(a, "portgiga-fx-encerrar:", [aid])
        if erro:
            return {"ok": False, "erro": erro}
        n = cx.S.db.execute("UPDATE fx_anuncios SET ativo=0 WHERE id=? AND vendedor=? AND ativo=1", (aid, a["carteira"])).rowcount
        cx.S.db.commit()
    return {"ok": True} if n else {"ok": False, "erro": "anúncio não encontrado nesta carteira"}


def comprar(a):
    aid, preco = a.get("id"), a.get("preco")
    if type(aid) is not int or type(preco) is not int:
        return {"ok": False, "erro": "pedido inválido"}
    with cx.S.trava:
        erro = cx.conferir(a, "portgiga-fx-comprar:", [aid, preco])
        if erro:
            return {"ok": False, "erro": erro}
        cx.S.db.commit()
        r = cx.regras()
        if r["feira_pausada"]:
            return {"ok": False, "erro": "a feira está pausada pelo dono da corretora"}
        an = cx.S.db.execute("SELECT vendedor, titulo, preco, estoque, ativo FROM fx_anuncios WHERE id=?", (aid,)).fetchone()
        if not an or not an[4] or an[3] < 1:
            return {"ok": False, "erro": "este anúncio não está mais disponível"}
        vendedor, titulo, preco_atual, estoque, _ = an
        comprador = a["carteira"]
        if vendedor == comprador:
            return {"ok": False, "erro": "você não pode comprar o seu próprio anúncio"}
        if preco_atual != preco:
            return {"ok": False, "erro": "o preço mudou: atualize a página"}
        if cx.ptg_de(comprador) < preco:
            return {"ok": False, "erro": "saldo de PTG na corretora insuficiente: deposite PTG primeiro"}
        cx.mover_ptg(comprador, -preco)
        cx.mov(comprador, "compra na feira (guardado)", -preco, titulo)
        cx.S.db.execute("UPDATE fx_anuncios SET estoque = estoque - 1, vendidos = vendidos + 1 WHERE id=?", (aid,))
        agora = time.time()
        cur = cx.S.db.execute("INSERT INTO fx_pedidos (anuncio, comprador, vendedor, titulo, preco, estado, criado, atualizado) "
                              "VALUES (?,?,?,?,?,'pago',?,?)", (aid, comprador, vendedor, titulo, preco, agora, agora))
        cx.S.db.commit()
    return {"ok": True, "pedido": cur.lastrowid, "aviso": "Pago! O PTG fica guardado até você confirmar que recebeu."}


def acao_pedido(a):
    """enviado (vendedor) · recebido (comprador) · cancelar (vendedor) · disputa (comprador)."""
    pid, acao, motivo = a.get("pedido"), a.get("acao"), a.get("motivo", "")
    if type(pid) is not int or acao not in ("enviado", "recebido", "cancelar", "disputa") or not isinstance(motivo, str):
        return {"ok": False, "erro": "pedido inválido"}
    with cx.S.trava:
        erro = cx.conferir(a, "portgiga-fx-pedido:", [pid, acao, motivo])
        if erro:
            return {"ok": False, "erro": erro}
        cx.S.db.commit()
        p = _pedido(pid)
        eu = a["carteira"]
        if not p or eu not in (p["comprador"], p["vendedor"]):
            return {"ok": False, "erro": "pedido não encontrado"}
        e = p["estado"]
        if acao == "enviado" and eu == p["vendedor"] and e == "pago":
            _estado(pid, "enviado")
            cx.S.db.execute("UPDATE fx_pedidos SET enviado_em=? WHERE id=?", (time.time(), pid))
        elif acao == "recebido" and eu == p["comprador"] and e in ("pago", "enviado"):
            _pagar_vendedor(p, cx.regras())
        elif acao == "cancelar" and eu == p["vendedor"] and e in ("pago", "enviado"):
            _devolver(p, "cancelado")
        elif acao == "disputa" and eu == p["comprador"] and e in ("pago", "enviado"):
            _estado(pid, "disputa", cx.S.limpar_texto(motivo, 300))
        else:
            return {"ok": False, "erro": "essa ação não vale para este pedido agora"}
        cx.S.db.commit()
    return {"ok": True, "estado": _pedido(pid)["estado"]}


def mensagem(a):
    pid, texto = a.get("pedido"), a.get("texto")
    if type(pid) is not int or not isinstance(texto, str):
        return {"ok": False, "erro": "pedido inválido"}
    t = cx.S.limpar_texto(texto, 500)
    if not t:
        return {"ok": False, "erro": "escreva a mensagem"}
    with cx.S.trava:
        erro = cx.conferir(a, "portgiga-fx-msg:", [pid, texto])
        if erro:
            return {"ok": False, "erro": erro}
        p = _pedido(pid)
        if not p or a["carteira"] not in (p["comprador"], p["vendedor"]):
            cx.S.db.commit()
            return {"ok": False, "erro": "pedido não encontrado"}
        if cx.S.db.execute("SELECT COUNT(*) FROM fx_mensagens WHERE pedido=?", (pid,)).fetchone()[0] >= 300:
            cx.S.db.commit()
            return {"ok": False, "erro": "limite de mensagens deste pedido"}
        cx.S.db.execute("INSERT INTO fx_mensagens (pedido, autor, texto, quando) VALUES (?,?,?,?)", (pid, a["carteira"], t, time.time()))
        cx.S.db.commit()
    return {"ok": True}


def meus_pedidos(a):
    """Leitura privada: só com assinatura da carteira (não fica marcada como usada; vale 5 min)."""
    with cx.S.trava:
        erro = cx.S.pedido_assinado(a, "portgiga-fx-ler:", [])
        if erro:
            return {"ok": False, "erro": erro}
        eu = a["carteira"]
        lista = []
        for (pid,) in cx.S.db.execute("SELECT id FROM fx_pedidos WHERE comprador=? OR vendedor=? ORDER BY atualizado DESC LIMIT 100",
                                      (eu, eu)).fetchall():
            p = _pedido(pid)
            p["papel"] = "comprador" if p["comprador"] == eu else "vendedor"
            p["preco"] = cx.S.fmt(p["preco"])
            p["mensagens"] = [{"autor": au, "texto": t, "quando": q} for au, t, q in
                              cx.S.db.execute("SELECT autor, texto, quando FROM fx_mensagens WHERE pedido=? ORDER BY id", (pid,))]
            lista.append(p)
        r = cx.regras()
    return {"ok": True, "pedidos": lista, "liberar_dias": r["feira_liberar_dias"]}


# ---------------------------------------------------------------- fotos do anúncio (até MAX_FOTOS; a 1ª é a capa)
MAX_FOTOS = 5
MAX_FOTO_BYTES = 25_000_000


def caminho_capa(aid, pos=0):
    return os.path.join(CAPAS, ("%d.jpg" % int(aid)) if not pos else ("%d_%d.jpg" % (int(aid), int(pos))))


def _jpeg(dados):
    """Abre qualquer formato que o Pillow entende (JPG, PNG, WEBP, GIF, BMP, TIFF, ICO...; HEIC/AVIF se houver o plugin)
    e regrava como JPEG de até 1600 px. Só pixels passam: metadados e o resto do arquivo ficam para trás."""
    from PIL import Image, ImageOps
    try:
        import pillow_heif  # opcional: fotos HEIC/AVIF de celular
        pillow_heif.register_heif_opener()
    except Exception:
        pass
    Image.MAX_IMAGE_PIXELS = 80_000_000
    im = Image.open(io.BytesIO(dados))
    im.seek(0)
    im = ImageOps.exif_transpose(im)
    if im.mode in ("RGBA", "LA", "P", "PA"):
        im = im.convert("RGBA")
        fundo = Image.new("RGB", im.size, (255, 255, 255))
        fundo.paste(im, mask=im.split()[-1])
        im = fundo
    else:
        im = im.convert("RGB")
    im.thumbnail((1600, 1600))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=86, optimize=True)
    return out.getvalue()


def definir_capa(aid, dados, pos=0):
    """Grava uma foto do anúncio (pos 0 = capa). Devolve {"ok"...}."""
    if not 0 <= pos < MAX_FOTOS:
        return {"ok": False, "erro": "posição de foto inválida"}
    if len(dados) > MAX_FOTO_BYTES:
        return {"ok": False, "erro": "imagem grande demais (máximo 25 MB)"}
    try:
        jpg = _jpeg(dados)
    except Exception:
        return {"ok": False, "erro": "não consegui abrir essa imagem: tente tirar print ou salvar como JPG"}
    os.makedirs(CAPAS, exist_ok=True)
    tmp = caminho_capa(aid, pos) + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(jpg)
    os.replace(tmp, caminho_capa(aid, pos))
    return {"ok": True}


def _reordenar(aid):
    """Fecha buracos: as fotos ficam em 0, 1, 2... (a primeira que sobrar vira a capa)."""
    existentes = [p for p in range(MAX_FOTOS) if os.path.exists(caminho_capa(aid, p))]
    for novo, velho in enumerate(existentes):
        if novo != velho:
            os.replace(caminho_capa(aid, velho), caminho_capa(aid, novo))


def foto(a):
    """Vendedor manda (ou apaga, com imagem vazia) uma foto do seu anúncio. Assinado sobre o sha256 da imagem."""
    import base64
    import hashlib
    aid, pos, img = a.get("id"), a.get("pos"), a.get("imagem")
    if type(aid) is not int or type(pos) is not int or not isinstance(img, str) or len(img) > MAX_FOTO_BYTES * 4 // 3 + 16:
        return {"ok": False, "erro": "pedido inválido"}
    try:
        dados = base64.b64decode(img, validate=True) if img else b""
    except Exception:
        return {"ok": False, "erro": "imagem inválida"}
    resumo = hashlib.sha256(dados).hexdigest() if dados else ""
    with cx.S.trava:
        erro = cx.conferir(a, "portgiga-fx-foto:", [aid, pos, resumo])
        if erro:
            return {"ok": False, "erro": erro}
        cx.S.db.commit()
        an = cx.S.db.execute("SELECT vendedor, ativo FROM fx_anuncios WHERE id=?", (aid,)).fetchone()
    if not an or an[0] != a["carteira"] or not an[1]:
        return {"ok": False, "erro": "anúncio não encontrado nesta carteira"}
    if not 0 <= pos < MAX_FOTOS:
        return {"ok": False, "erro": "cada anúncio tem até %d fotos" % MAX_FOTOS}
    if not dados:
        c = caminho_capa(aid, pos)
        if os.path.exists(c):
            os.remove(c)
        _reordenar(aid)
        return {"ok": True, "fotos": urls_fotos(aid)}
    r = definir_capa(aid, dados, pos)
    if r["ok"]:
        _reordenar(aid)
        r["fotos"] = urls_fotos(aid)
    return r


def urls_fotos(aid):
    lista = []
    for p in range(MAX_FOTOS):
        c = caminho_capa(aid, p)
        if os.path.exists(c):
            lista.append("/corretora/capa/%s.jpg?v=%d" % (("%d" % aid) if not p else ("%d_%d" % (aid, p)), int(os.path.getmtime(c) * 1000)))
    return lista


def url_capa(aid):
    f = urls_fotos(aid)
    return f[0] if f else None


# ---------------------------------------------------------------- leituras públicas
def vitrine():
    with cx.S.trava:
        itens = [{"id": i, "vendedor": v, "titulo": t, "descricao": d, "categoria": c, "preco": cx.S.fmt(p), "preco_unidades": p,
                  "estoque": e, "criado": q, "vendidos": vd}
                 for i, v, t, d, c, p, e, q, vd in cx.S.db.execute(
                     "SELECT id, vendedor, titulo, descricao, categoria, preco, estoque, criado, vendidos FROM fx_anuncios "
                     "WHERE ativo=1 AND estoque>0 ORDER BY id DESC LIMIT 300")]
        reput = {}
        for v, ok, total in cx.S.db.execute("SELECT vendedor, SUM(estado='concluido'), SUM(estado IN ('concluido','reembolsado')) "
                                            "FROM fx_pedidos GROUP BY vendedor"):
            reput[v] = {"vendas": ok or 0, "problemas": (total or 0) - (ok or 0)}
        r = cx.regras()
    for it in itens:
        it["reputacao"] = reput.get(it["vendedor"], {"vendas": 0, "problemas": 0})
        it["fotos"] = urls_fotos(it["id"])
        it["capa"] = it["fotos"][0] if it["fotos"] else None
    return {"itens": itens, "max_fotos": MAX_FOTOS, "categorias": CATEGORIAS, "taxa_pct": r["taxa_feira_pct"], "liberar_dias": r["feira_liberar_dias"],
            "pausada": r["feira_pausada"]}


# ---------------------------------------------------------------- dono e rotina
def disputas():
    with cx.S.trava:
        lista = []
        for (pid,) in cx.S.db.execute("SELECT id FROM fx_pedidos WHERE estado='disputa' ORDER BY atualizado").fetchall():
            p = _pedido(pid)
            p["preco"] = cx.S.fmt(p["preco"])
            p["mensagens"] = [{"autor": au, "texto": t, "quando": q} for au, t, q in
                              cx.S.db.execute("SELECT autor, texto, quando FROM fx_mensagens WHERE pedido=? ORDER BY id", (pid,))]
            lista.append(p)
        vendas = cx.S.db.execute("SELECT COUNT(*), COALESCE(SUM(preco),0) FROM fx_pedidos WHERE estado='concluido'").fetchone()
        receita = cx.S.db.execute("SELECT COALESCE(SUM(unidades),0) FROM cx_movimentos WHERE carteira=? AND tipo='taxa da feira recebida'",
                                  (cx.DONO,)).fetchone()[0]
        anuncios = cx.S.db.execute("SELECT COUNT(*) FROM fx_anuncios WHERE ativo=1").fetchone()[0]
    return {"disputas": lista, "vendas": vendas[0], "volume": cx.S.fmt(vendas[1]), "receita": cx.S.fmt(receita), "anuncios": anuncios}


def resolver(pid, decisao):
    with cx.S.trava:
        p = _pedido(pid if type(pid) is int else -1)
        if not p or p["estado"] != "disputa" or decisao not in ("vendedor", "comprador"):
            return {"ok": False, "erro": "disputa não encontrada"}
        if decisao == "vendedor":
            _pagar_vendedor(p, cx.regras())
        else:
            _devolver(p, "reembolsado")
        cx.S.db.commit()
    return {"ok": True}


def remover_anuncio(aid):
    with cx.S.trava:
        n = cx.S.db.execute("UPDATE fx_anuncios SET ativo=0 WHERE id=?", (aid,)).rowcount
        cx.S.db.commit()
    return {"ok": bool(n)}


def liberar_vencidos(agora=None):
    """Enviado há mais de N dias sem reclamação: o PTG vai para o vendedor."""
    agora = agora or time.time()
    with cx.S.trava:
        r = cx.regras()
        limite = agora - r["feira_liberar_dias"] * 86400
        for (pid,) in cx.S.db.execute("SELECT id FROM fx_pedidos WHERE estado='enviado' AND enviado_em < ?", (limite,)).fetchall():
            _pagar_vendedor(_pedido(pid), r)
        cx.S.db.commit()


# ---------------------------------------------------------------- rotas (/api/cx/feira/...)
def rota(h, acao, a, do_pc, ip=""):
    post = h.command == "POST"
    if post and acao in ("foto", "anunciar", "comprar", "pedido", "mensagem") and not do_pc:
        import social
        if social._excedeu(ip, "imagem" if acao == "foto" else "escrita"):
            return h._resp({"ok": False, "erro": "muitas ações seguidas deste aparelho: espere alguns minutos"}, codigo=429)
        if acao == "foto" and not social._disco_ok():
            return h._resp({"ok": False, "erro": "o servidor está sem espaço para fotos agora"}, codigo=507)
    if acao == "vitrine":
        return h._resp(vitrine())
    if acao == "foto" and post:
        if not h._json():
            return h._resp({"ok": False, "erro": "pedido inválido"}, codigo=400)
        if "imagem" not in a:
            import json as _json
            n = int(h.headers.get("Content-Length") or 0)
            if not 0 < n <= MAX_FOTO_BYTES * 4 // 3 + 4096:
                return h._resp({"ok": False, "erro": "imagem grande demais (máximo 25 MB)"}, codigo=413)
            try:
                a.update(_json.loads(h.rfile.read(n).decode("utf-8")))
            except Exception:
                return h._resp({"ok": False, "erro": "pedido inválido"}, codigo=400)
        return h._resp(foto(a))
    if acao in ("anunciar", "encerrar", "comprar", "pedido", "mensagem", "meus") and post:
        if not h._json():
            return h._resp({"ok": False, "erro": "pedido inválido"}, codigo=400)
        return h._resp({"anunciar": anunciar, "encerrar": encerrar, "comprar": comprar, "pedido": acao_pedido,
                        "mensagem": mensagem, "meus": meus_pedidos}[acao](a))
    if acao.startswith("dono/"):
        if not do_pc:
            return h._resp({"ok": False, "erro": "só no PC da corretora"}, codigo=403)
        if acao == "dono/disputas":
            return h._resp(disputas())
        if acao == "dono/resolver" and post and h._json():
            return h._resp(resolver(a.get("pedido"), a.get("decisao")))
        if acao == "dono/remover" and post and h._json():
            return h._resp(remover_anuncio(a.get("id")))
    return h._resp({"erro": "não encontrado"}, codigo=404)
