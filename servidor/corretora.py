# -*- coding: utf-8 -*-
"""CORRETORA PORTGIGA: qualquer pessoa cria a própria moeda e todos negociam pagando em PTG.

Regras de ouro (não mudar sem pensar):
- Só PTG. Nada de reais, pix ou outras criptos: a corretora não toca em dinheiro de verdade.
- Preço automático por curva (produto constante com reserva virtual de PTG, como o pump.fun): a corretora
  sempre compra e sempre vende, e o preço sobe quando compram e cai quando vendem.
  reserva_ptg = virtual + real ; k = reserva_ptg * reserva_moeda. A reserva real só cresce com compras e só
  sai em vendas, então a corretora nunca deve mais PTG do que tem (está sempre solvente).
- Saldos de dentro da corretora: o PTG entra por uma transferência assinada normal para a carteira da corretora
  (creditado quando o bloco fecha) e sai por um saque assinado (a corretora assina a transferência de volta).
- Toda ação de dinheiro é assinada pela carteira do usuário (Ed25519), vale 5 min e não pode ser repetida.
- Taxas: criação de moeda e % de cada negócio para o DONO; uma % menor para o criador da moeda.
- Anti-golpe: a compra inicial do criador fica travada N dias; ninguém pode ter mais que X% de uma moeda;
  moeda banida pelo dono não aceita compras, mas quem tem ainda pode vender (ninguém fica preso).
"""
import hashlib
import json
import os
import re
import threading
import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

S = None                                   # o módulo do servidor (db, trava, enviar...)
PASTA = os.path.dirname(os.path.abspath(__file__))
CARTEIRA_ARQ = os.path.join(PASTA, "..", "corretora_carteira.json")   # gitignored, chmod 600
DONO = "ptg1374be3887d14547da346b560c4ce183c55154f"                    # recebe as taxas
U = 100_000_000                            # unidades por PTG e por moeda (8 casas)
SIMBOLO_OK = re.compile(r"^[A-Z][A-Z0-9]{1,7}$")
RESERVADOS = {"PTG", "BTC", "ETH", "USDT", "USD", "BRL", "REAL", "PIX", "XAU", "GOLD"}
EMOJIS = ["🚀", "🌳", "🦜", "🐆", "🔥", "💎", "⚡", "🌙", "☀️", "🐸", "🐶", "🐱", "🦁", "🐉", "🍀", "⭐", "🎮", "⚽",
          "🎵", "🍕", "🌊", "🏆", "👑", "🤖", "👾", "🧠", "🌵", "🍌", "🥥", "🐢"]
CORES = ["#22c55e", "#3cdcff", "#f0be50", "#ef4444", "#a855f7", "#f97316", "#ec4899", "#14b8a6", "#eab308", "#6366f1"]
REGRAS_PADRAO = {
    "taxa_criacao_ptg": 100,      # PTG para o dono por moeda criada
    "taxa_dono_pct": 1.0,         # % de cada compra/venda para o dono
    "taxa_criador_pct": 0.25,     # % de cada compra/venda para quem criou a moeda
    "reserva_virtual_ptg": 1000,  # "liquidez de partida" da curva (define o preço inicial)
    "oferta_moedas": 1_000_000_000,
    "trava_criador_dias": 7,      # a compra inicial do criador não pode ser vendida antes disso
    "max_carteira_pct": 10.0,     # ninguém compra mais que isso de uma moeda
    "max_moedas_dia": 3,          # moedas novas por carteira a cada 24 h
    "min_negocio_ptg": 0.01,
    "compras_pausadas": False,    # botão de emergência do dono: só vendas e saques continuam
    "criacao_pausada": False,
    "palavras_proibidas": [],
    "taxa_feira_pct": 2.0,        # % de cada venda da feira para o dono
    "feira_liberar_dias": 7,      # enviado e sem reclamação por N dias: o PTG vai para o vendedor
    "max_anuncios": 20,           # anúncios abertos por carteira
    "feira_pausada": False,
    "social_pausada": False,      # PTG Social: botão de emergência do dono
    "social_max_posts_dia": 15,
    "social_videos": True,        # PTG Social aceita vídeos (até 60 s, convertidos no PC)
    "social_max_comentarios_hora": 60,
    "social_monet_ativa": True,       # criadores elegíveis ganham PTG por visualização
    "social_ptg_por_mil": 0.01,       # PTG a cada 1000 visualizações
    "social_monet_min_seguidores": 100,
    "social_monet_min_curtidas": 500,
    "social_monet_teto_dia": 20,      # PTG máximo pago por dia somando todos os criadores
    "social_denuncias_ocultar": 5,
    "social_real_min_ptg": 1,         # anti-robô: só conta seguidor/curtida/visualização de quem já minerou (ou depositou) isso
    "social_real_dias": 3,            # ...ou depositou e tem perfil há esses dias    # denúncias de pessoas diferentes que escondem um post até o dono decidir
    "apoio_btc": "",              # endereço bitcoin para DOAÇÕES ao projeto (sem nada em troca); vazio = área escondida
}
BTC_OK = re.compile(r"^(bc1[02-9ac-hj-np-z]{11,71}|[13][1-9A-HJ-NP-Za-km-z]{25,34})$")
ASSINATURA_VALE_S = 300
saque_trava = threading.Lock()
CX = {"endereco": None, "chave": None}


# ---------------------------------------------------------------- matemática da curva (pura, testável)
def comprar_na_curva(virtual, real, reserva_moeda, ptg_liquido):
    """Quantas moedas saem por ptg_liquido unidades. Devolve (moedas, nova_reserva_moeda)."""
    x = virtual + real
    k = x * reserva_moeda
    nova = -(-k // (x + ptg_liquido))          # arredonda para cima: a sobra fica na curva
    return reserva_moeda - nova, nova


def vender_na_curva(virtual, real, reserva_moeda, moedas):
    """Quantos PTG (brutos) saem por moedas vendidas. Nunca mais que a reserva real."""
    x = virtual + real
    k = x * reserva_moeda
    novo_x = -(-k // (reserva_moeda + moedas))
    return min(real, x - novo_x)


def preco(virtual, real, reserva_moeda):
    """PTG por 1 moeda."""
    return (virtual + real) / reserva_moeda if reserva_moeda else 0.0


def taxas(bruto, regras):
    dono = bruto * int(round(regras["taxa_dono_pct"] * 100)) // 10000
    criador = bruto * int(round(regras["taxa_criador_pct"] * 100)) // 10000
    return dono, criador


# ---------------------------------------------------------------- banco e carteira da corretora
def preparar(servidor):
    """Chamado pelo abrir_banco do servidor: cria as tabelas e carrega a carteira da corretora."""
    global S
    S = servidor
    S.db.executescript("""
        CREATE TABLE IF NOT EXISTS cx_ptg (carteira TEXT PRIMARY KEY, saldo INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS cx_moedas (simbolo TEXT PRIMARY KEY, nome TEXT, descricao TEXT, emoji TEXT, cor TEXT,
            criador TEXT, criada REAL, virtual INTEGER, real INTEGER, reserva INTEGER, oferta INTEGER,
            banida INTEGER NOT NULL DEFAULT 0, motivo TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS cx_saldos (simbolo TEXT, carteira TEXT, qtd INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (simbolo, carteira));
        CREATE TABLE IF NOT EXISTS cx_travas (simbolo TEXT, carteira TEXT, qtd INTEGER, ate REAL);
        CREATE TABLE IF NOT EXISTS cx_negocios (id INTEGER PRIMARY KEY AUTOINCREMENT, simbolo TEXT, carteira TEXT, lado TEXT,
            ptg INTEGER, qtd INTEGER, taxa_dono INTEGER, taxa_criador INTEGER, preco REAL, quando REAL);
        CREATE TABLE IF NOT EXISTS cx_movimentos (id INTEGER PRIMARY KEY AUTOINCREMENT, carteira TEXT, tipo TEXT,
            unidades INTEGER, ref TEXT, quando REAL);
        CREATE TABLE IF NOT EXISTS cx_assinaturas (assinatura TEXT PRIMARY KEY, quando REAL);
        CREATE INDEX IF NOT EXISTS cx_neg_simbolo ON cx_negocios (simbolo, id);
    """)
    if not S.db.execute("SELECT 1 FROM meta WHERE chave='cx_altura'").fetchone():
        alt = S.db.execute("SELECT COALESCE(MAX(altura), 0) FROM blocos").fetchone()[0]
        S.db.execute("INSERT INTO meta VALUES ('cx_altura', ?)", (str(alt),))
    S.db.commit()
    carregar_carteira()
    import feira
    feira.preparar()
    import social
    social.preparar()


def carregar_carteira():
    if not os.path.exists(CARTEIRA_ARQ):
        chave = os.urandom(32).hex()
        with open(os.open(CARTEIRA_ARQ, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as f:
            json.dump({"endereco": S.endereco_do_pub(S.pub_da_chave(chave)), "chave": chave,
                       "uso": "carteira da Corretora Portgiga (guarda os PTG depositados)"}, f)
    with open(CARTEIRA_ARQ) as f:
        d = json.load(f)
    CX["chave"] = d["chave"]
    CX["endereco"] = S.endereco_do_pub(S.pub_da_chave(d["chave"]))


def regras():
    r = S.db.execute("SELECT valor FROM meta WHERE chave='cx_regras'").fetchone()
    atual = dict(REGRAS_PADRAO)
    if r:
        atual.update({k: v for k, v in json.loads(r[0]).items() if k in REGRAS_PADRAO})
    return atual


def ptg_de(carteira):
    r = S.db.execute("SELECT saldo FROM cx_ptg WHERE carteira=?", (carteira,)).fetchone()
    return r[0] if r else 0


def mover_ptg(carteira, valor):
    S.db.execute("INSERT INTO cx_ptg VALUES (?, ?) ON CONFLICT(carteira) DO UPDATE SET saldo = saldo + excluded.saldo",
                 (carteira, valor))


def moedas_de(simbolo, carteira):
    r = S.db.execute("SELECT qtd FROM cx_saldos WHERE simbolo=? AND carteira=?", (simbolo, carteira)).fetchone()
    return r[0] if r else 0


def travadas(simbolo, carteira, agora=None):
    agora = agora or time.time()
    return S.db.execute("SELECT COALESCE(SUM(qtd), 0) FROM cx_travas WHERE simbolo=? AND carteira=? AND ate>?",
                        (simbolo, carteira, agora)).fetchone()[0]


def mover_moedas(simbolo, carteira, qtd):
    S.db.execute("INSERT INTO cx_saldos VALUES (?, ?, ?) ON CONFLICT(simbolo, carteira) DO UPDATE SET qtd = qtd + excluded.qtd",
                 (simbolo, carteira, qtd))


def mov(carteira, tipo, unidades, ref=""):
    S.db.execute("INSERT INTO cx_movimentos (carteira, tipo, unidades, ref, quando) VALUES (?, ?, ?, ?, ?)",
                 (carteira, tipo, unidades, ref, time.time()))


# ---------------------------------------------------------------- pedidos assinados
def conferir(a, prefixo, campos):
    """Assinatura válida, dentro de 5 min e nunca usada antes. Chamar com a trava do servidor."""
    erro = S.pedido_assinado(a, prefixo, campos)
    if erro:
        return erro
    if S.db.execute("SELECT 1 FROM cx_assinaturas WHERE assinatura=?", (a["assinatura"],)).fetchone():
        return "este pedido já foi feito"
    S.db.execute("INSERT INTO cx_assinaturas VALUES (?, ?)", (a["assinatura"], time.time()))
    S.db.execute("DELETE FROM cx_assinaturas WHERE quando < ?", (time.time() - 3 * ASSINATURA_VALE_S,))
    return None


AVISOS_OK = re.compile(r"n[aã]o (é|e) (um )?investimento|n[aã]o (tem|têm) valor em dinheiro|sem valor em dinheiro|nada de (reais|pix|dinheiro)"
                       r"(, (reais|pix|dinheiro|lucro))*( (ou|e) (reais|pix|dinheiro|lucro|investimento))?")


def proibido(texto, r):
    t = AVISOS_OK.sub(" ", texto.lower())     # avisos honestos ("não é investimento") podem ser escritos
    if S.PALAVRAS_DINHEIRO.search(t):
        return True
    return any(p and p.lower() in t for p in r.get("palavras_proibidas", []))


# ---------------------------------------------------------------- ações
def criar(a):
    simbolo, nome, descricao = a.get("simbolo"), a.get("nome"), a.get("descricao", "")
    emoji, cor, inicial = a.get("emoji"), a.get("cor"), a.get("compra_inicial", 0)
    if not all(isinstance(v, str) for v in (simbolo, nome, descricao, emoji, cor)) or type(inicial) is not int:
        return {"ok": False, "erro": "pedido inválido"}
    with S.trava:
        r = regras()
        if r["criacao_pausada"]:
            return {"ok": False, "erro": "a criação de moedas está pausada pelo dono da corretora"}
        if not SIMBOLO_OK.match(simbolo) or simbolo in RESERVADOS:
            return {"ok": False, "erro": "símbolo: 2 a 8 letras maiúsculas/números, começando com letra (e não pode ser PTG, BTC...)"}
        nome_l, desc_l = S.limpar_texto(nome, 30), S.limpar_texto(descricao, 200)
        if len(nome_l) < 3:
            return {"ok": False, "erro": "o nome precisa de 3 letras ou mais"}
        if emoji not in EMOJIS or cor not in CORES:
            return {"ok": False, "erro": "escolha um ícone e uma cor da lista"}
        if proibido(simbolo + " " + nome_l + " " + desc_l, r):
            return {"ok": False, "erro": "nome ou descrição com palavra proibida (nada de dinheiro, lucro, pix...)"}
        if inicial < 0 or inicial > 10_000_000 * U:
            return {"ok": False, "erro": "compra inicial inválida"}
        erro = conferir(a, "portgiga-cx-criar:", [simbolo, nome, descricao, emoji, cor, inicial])
        if erro:
            return {"ok": False, "erro": erro}
        carteira = a["carteira"]
        if S.db.execute("SELECT 1 FROM cx_moedas WHERE simbolo=?", (simbolo,)).fetchone():
            S.db.rollback()
            return {"ok": False, "erro": "já existe uma moeda com esse símbolo"}
        hoje = S.db.execute("SELECT COUNT(*) FROM cx_moedas WHERE criador=? AND criada>?", (carteira, time.time() - 86400)).fetchone()[0]
        if hoje >= r["max_moedas_dia"]:
            S.db.rollback()
            return {"ok": False, "erro": "limite de %d moedas novas por dia" % r["max_moedas_dia"]}
        custo = int(r["taxa_criacao_ptg"] * U)
        if ptg_de(carteira) < custo + inicial:
            S.db.rollback()
            return {"ok": False, "erro": "saldo na corretora insuficiente: precisa de %s PTG (taxa) + a compra inicial. Deposite PTG primeiro."
                    % S.fmt(custo)}
        oferta = int(r["oferta_moedas"]) * U
        mover_ptg(carteira, -custo)
        mover_ptg(DONO, custo)
        mov(carteira, "taxa de criação", -custo, simbolo)
        mov(DONO, "taxa de criação recebida", custo, simbolo)
        S.db.execute("INSERT INTO cx_moedas (simbolo, nome, descricao, emoji, cor, criador, criada, virtual, real, reserva, oferta) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)",
                     (simbolo, nome_l, desc_l, emoji, cor, carteira, time.time(), int(r["reserva_virtual_ptg"] * U), oferta, oferta))
        res = {"ok": True, "simbolo": simbolo}
        if inicial > 0:
            c = _comprar(carteira, simbolo, inicial, 0, r, inicial_do_criador=True)
            if not c["ok"]:
                S.db.rollback()
                return c
            res["compra_inicial"] = c
        S.db.commit()
    return res


def _comprar(carteira, simbolo, bruto, minimo, r, inicial_do_criador=False):
    m = S.db.execute("SELECT criador, virtual, real, reserva, oferta, banida FROM cx_moedas WHERE simbolo=?", (simbolo,)).fetchone()
    if not m:
        return {"ok": False, "erro": "moeda não encontrada"}
    criador, virtual, real, reserva, oferta, banida = m
    if banida:
        return {"ok": False, "erro": "esta moeda foi suspensa pelo dono da corretora: só é possível vender"}
    if r["compras_pausadas"]:
        return {"ok": False, "erro": "as compras estão pausadas pelo dono da corretora"}
    if bruto < int(r["min_negocio_ptg"] * U):
        return {"ok": False, "erro": "valor mínimo por negócio: %s PTG" % r["min_negocio_ptg"]}
    if ptg_de(carteira) < bruto:
        return {"ok": False, "erro": "saldo de PTG na corretora insuficiente"}
    t_dono, t_criador = taxas(bruto, r)
    liquido = bruto - t_dono - t_criador
    qtd, nova = comprar_na_curva(virtual, real, reserva, liquido)
    if qtd <= 0:
        return {"ok": False, "erro": "valor pequeno demais"}
    if qtd < minimo:
        return {"ok": False, "erro": "o preço mudou enquanto você decidia; confira a cotação e tente de novo"}
    limite = oferta * int(r["max_carteira_pct"] * 100) // 10000
    if moedas_de(simbolo, carteira) + qtd > limite:
        return {"ok": False, "erro": "limite anti-baleia: ninguém pode ter mais de %s%% desta moeda" % r["max_carteira_pct"]}
    mover_ptg(carteira, -bruto)
    mover_ptg(DONO, t_dono)
    mover_ptg(criador, t_criador)
    mover_moedas(simbolo, carteira, qtd)
    S.db.execute("UPDATE cx_moedas SET real=?, reserva=? WHERE simbolo=?", (real + liquido, nova, simbolo))
    p = preco(virtual, real + liquido, nova)
    S.db.execute("INSERT INTO cx_negocios (simbolo, carteira, lado, ptg, qtd, taxa_dono, taxa_criador, preco, quando) "
                 "VALUES (?, ?, 'compra', ?, ?, ?, ?, ?, ?)", (simbolo, carteira, bruto, qtd, t_dono, t_criador, p, time.time()))
    if inicial_do_criador:
        S.db.execute("INSERT INTO cx_travas VALUES (?, ?, ?, ?)", (simbolo, carteira, qtd, time.time() + r["trava_criador_dias"] * 86400))
    return {"ok": True, "lado": "compra", "ptg": S.fmt(bruto), "moedas": S.fmt(qtd), "taxa_dono": S.fmt(t_dono),
            "taxa_criador": S.fmt(t_criador), "preco": p}


def comprar(a):
    simbolo, ptg, minimo = a.get("simbolo"), a.get("ptg"), a.get("minimo", 0)
    if not isinstance(simbolo, str) or type(ptg) is not int or type(minimo) is not int or ptg <= 0 or minimo < 0:
        return {"ok": False, "erro": "pedido inválido"}
    with S.trava:
        erro = conferir(a, "portgiga-cx-comprar:", [simbolo, ptg, minimo])
        if erro:
            return {"ok": False, "erro": erro}
        res = _comprar(a["carteira"], simbolo, ptg, minimo, regras())
        S.db.commit()                      # _comprar só grava se tudo passou; a assinatura fica marcada como usada
    return res


def vender(a):
    simbolo, qtd, minimo = a.get("simbolo"), a.get("qtd"), a.get("minimo", 0)
    if not isinstance(simbolo, str) or type(qtd) is not int or type(minimo) is not int or qtd <= 0 or minimo < 0:
        return {"ok": False, "erro": "pedido inválido"}
    with S.trava:
        erro = conferir(a, "portgiga-cx-vender:", [simbolo, qtd, minimo])
        if erro:
            return {"ok": False, "erro": erro}
        res = _vender(a["carteira"], simbolo, qtd, minimo, regras())
        S.db.commit()
    return res


def _vender(carteira, simbolo, qtd, minimo, r):
    m = S.db.execute("SELECT criador, virtual, real, reserva FROM cx_moedas WHERE simbolo=?", (simbolo,)).fetchone()
    if not m:
        return {"ok": False, "erro": "moeda não encontrada"}
    criador, virtual, real, reserva = m
    livre = moedas_de(simbolo, carteira) - travadas(simbolo, carteira)
    if qtd > livre:
        trav = travadas(simbolo, carteira)
        return {"ok": False, "erro": "você só tem %s livres para vender%s" % (
            S.fmt(max(0, livre)), " (%s travadas pela regra anti-golpe do criador)" % S.fmt(trav) if trav else "")}
    bruto = vender_na_curva(virtual, real, reserva, qtd)
    t_dono, t_criador = taxas(bruto, r)
    liquido = bruto - t_dono - t_criador
    if liquido <= 0:
        return {"ok": False, "erro": "quantidade pequena demais"}
    if liquido < minimo:
        return {"ok": False, "erro": "o preço mudou enquanto você decidia; confira a cotação e tente de novo"}
    mover_moedas(simbolo, carteira, -qtd)
    mover_ptg(carteira, liquido)
    mover_ptg(DONO, t_dono)
    mover_ptg(criador, t_criador)
    S.db.execute("UPDATE cx_moedas SET real=?, reserva=? WHERE simbolo=?", (real - bruto, reserva + qtd, simbolo))
    p = preco(virtual, real - bruto, reserva + qtd)
    S.db.execute("INSERT INTO cx_negocios (simbolo, carteira, lado, ptg, qtd, taxa_dono, taxa_criador, preco, quando) "
                 "VALUES (?, ?, 'venda', ?, ?, ?, ?, ?, ?)", (simbolo, carteira, bruto, qtd, t_dono, t_criador, p, time.time()))
    return {"ok": True, "lado": "venda", "moedas": S.fmt(qtd), "ptg": S.fmt(liquido), "taxa_dono": S.fmt(t_dono),
            "taxa_criador": S.fmt(t_criador), "preco": p}


def cotar(simbolo, lado, valor):
    """Prévia sem assinar: quanto sai em moedas (compra) ou em PTG (venda)."""
    with S.trava:
        m = S.db.execute("SELECT virtual, real, reserva FROM cx_moedas WHERE simbolo=?", (simbolo,)).fetchone()
        if not m or valor <= 0:
            return {"erro": "cotação inválida"}
        r = regras()
        virtual, real, reserva = m
        if lado == "compra":
            t_d, t_c = taxas(valor, r)
            qtd, nova = comprar_na_curva(virtual, real, reserva, valor - t_d - t_c)
            antes, depois = preco(virtual, real, reserva), preco(virtual, real + valor - t_d - t_c, nova)
            return {"moedas": S.fmt(qtd), "unidades": qtd, "taxas": S.fmt(t_d + t_c), "preco_antes": antes, "preco_depois": depois,
                    "impacto_pct": round((depois / antes - 1) * 100, 2) if antes else 0}
        bruto = vender_na_curva(virtual, real, reserva, valor)
        t_d, t_c = taxas(bruto, r)
        antes, depois = preco(virtual, real, reserva), preco(virtual, real - bruto, reserva + valor)
        return {"ptg": S.fmt(bruto - t_d - t_c), "unidades": bruto - t_d - t_c, "taxas": S.fmt(t_d + t_c),
                "preco_antes": antes, "preco_depois": depois, "impacto_pct": round((depois / antes - 1) * 100, 2) if antes else 0}


# ---------------------------------------------------------------- depósitos e saques
def ler_depositos():
    """Credita na corretora cada transferência para a carteira dela que já entrou num bloco."""
    with S.trava:
        ult = int(S.db.execute("SELECT valor FROM meta WHERE chave='cx_altura'").fetchone()[0])
        maior = ult
        for altura, dados in S.db.execute("SELECT altura, dados FROM blocos WHERE altura > ? ORDER BY altura", (ult,)).fetchall():
            for t in json.loads(dados).get("transferencias", []):
                if t["para"] == CX["endereco"]:
                    mover_ptg(t["de"], t["unidades"])
                    mov(t["de"], "depósito", t["unidades"], "bloco %d" % altura)
            maior = altura
        if maior != ult:
            S.db.execute("UPDATE meta SET valor=? WHERE chave='cx_altura'", (str(maior),))
        S.db.commit()


def vigiar_depositos():
    import feira
    volta = 0
    while True:
        try:
            ler_depositos()
            if volta % 12 == 0:
                feira.liberar_vencidos()
                import social
                social.pagar_views()
                social.encerrar_desafios()
        except Exception as e:
            print("corretora: erro na rotina:", e, flush=True)
        volta += 1
        time.sleep(5)


def sacar(a):
    unidades = a.get("unidades")
    if type(unidades) is not int or unidades <= 0:
        return {"ok": False, "erro": "valor inválido"}
    with saque_trava:                      # um saque por vez: o número de ordem da carteira da corretora não se repete
        with S.trava:
            erro = conferir(a, "portgiga-cx-sacar:", [unidades])
            if erro:
                return {"ok": False, "erro": erro}
            carteira = a["carteira"]
            if ptg_de(carteira) < unidades:
                S.db.rollback()
                return {"ok": False, "erro": "saldo na corretora insuficiente"}
            mover_ptg(carteira, -unidades)
            mov(carteira, "saque", -unidades)
            S.db.commit()
            nonce = S.proximo_nonce(CX["endereco"])
        de, priv = CX["endereco"], Ed25519PrivateKey.from_private_bytes(
            hashlib.sha256(("ptg-ed25519:" + CX["chave"]).encode()).digest())
        msg = "portgiga-tx:" + json.dumps([de, carteira, unidades, nonce], separators=(",", ":"))
        pub = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
        r = S.enviar({"de": de, "para": carteira, "unidades": unidades, "nonce": nonce, "pub": pub, "assinatura": priv.sign(msg.encode()).hex()})
        if not r.get("ok"):
            with S.trava:
                mover_ptg(carteira, unidades)          # devolve: nada sai se a transferência não entrou
                mov(carteira, "saque devolvido", unidades, r.get("erro", ""))
                S.db.commit()
            return {"ok": False, "erro": "o saque não entrou na rede (%s); o saldo voltou para a corretora" % r.get("erro")}
    return {"ok": True, "valor": S.fmt(unidades), "aviso": "o PTG chega na sua carteira no próximo bloco (até 1 minuto)"}


# ---------------------------------------------------------------- força da moeda e velas
def nota_forca(pressao, variacao, donos, reserva_ptg, maior_pct):
    """0 a 100, de partes simples que qualquer um entende. Cada parte também vai de 0 a 100."""
    import math
    partes = {
        "pressao": round(pressao * 100),                                   # % do volume 24 h que foi compra
        "momento": round(max(0, min(100, 50 + variacao * 1.5))),             # variação 24 h
        "comunidade": round(min(100, 25 * math.log2(1 + donos))),           # 15 donos ~ 100
        "liquidez": round(min(100, 33 * math.log10(1 + reserva_ptg))),     # ~1000 PTG na reserva ~ 100
        "distribuicao": round(max(0, min(100, 100 - maior_pct * 5))),        # maior dono com 10% -> 50
    }
    pesos = {"pressao": .30, "momento": .20, "comunidade": .20, "liquidez": .15, "distribuicao": .15}
    nota = round(sum(partes[k] * pesos[k] for k in pesos))
    rotulo = "muito fraca" if nota < 20 else "fraca" if nota < 40 else "neutra" if nota < 60 else "forte" if nota < 80 else "muito forte"
    return {"nota": nota, "rotulo": rotulo, "partes": partes}


def _forca(simbolo, oferta, real, variacao, donos, agora):
    c, v = S.db.execute("SELECT COALESCE(SUM(CASE WHEN lado='compra' THEN ptg END),0), COALESCE(SUM(CASE WHEN lado='venda' THEN ptg END),0) "
                        "FROM cx_negocios WHERE simbolo=? AND quando>?", (simbolo, agora - 86400)).fetchone()
    pressao = c / (c + v) if c + v else 0.5
    maior = S.db.execute("SELECT COALESCE(MAX(qtd),0) FROM cx_saldos WHERE simbolo=?", (simbolo,)).fetchone()[0]
    f = nota_forca(pressao, variacao, donos, real / U, maior * 100 / oferta if oferta else 0)
    f["compras_24h"], f["vendas_24h"] = S.fmt(c), S.fmt(v)
    return f


def montar_velas(pontos, inicio, preco_inicial, tf):
    """pontos = [(quando, preco, ptg)] em ordem. Velas OHLC com volume; cada vela abre no fechamento da anterior."""
    velas, ultimo = [], preco_inicial
    for quando, p, vol in pontos:
        b = int(quando // tf * tf)
        if not velas or velas[-1]["t"] != b:
            velas.append({"t": b, "o": ultimo, "h": max(ultimo, p), "l": min(ultimo, p), "c": p, "v": 0.0})
        v = velas[-1]
        v["h"], v["l"], v["c"] = max(v["h"], p), min(v["l"], p), p
        v["v"] = round(v["v"] + vol, 8)
        ultimo = p
    if not velas:
        velas.append({"t": int(inicio // tf * tf), "o": preco_inicial, "h": preco_inicial, "l": preco_inicial, "c": preco_inicial, "v": 0.0})
    return velas[-600:]


def velas(simbolo, tf):
    tf = tf if tf in (60, 300, 900, 3600, 14400, 86400) else 300
    with S.trava:
        m = S.db.execute("SELECT criada, virtual, oferta FROM cx_moedas WHERE simbolo=?", (simbolo,)).fetchone()
        if not m:
            return {"erro": "moeda não encontrada"}
        pts = [(q, p, x / U) for q, p, x in S.db.execute("SELECT quando, preco, ptg FROM cx_negocios WHERE simbolo=? ORDER BY id", (simbolo,))]
    return {"tf": tf, "velas": montar_velas(pts, m[0], m[1] / m[2], tf)}


# ---------------------------------------------------------------- leituras
def _resumo_moeda(row, agora):
    simbolo, nome, descricao, emoji, cor, criador, criada, virtual, real, reserva, oferta, banida, motivo = row
    p = preco(virtual, real, reserva)
    ant = S.db.execute("SELECT preco FROM cx_negocios WHERE simbolo=? AND quando<=? ORDER BY id DESC LIMIT 1", (simbolo, agora - 86400)).fetchone()
    p24 = ant[0] if ant else preco(virtual, 0, oferta)
    vol = S.db.execute("SELECT COALESCE(SUM(ptg), 0), COUNT(*) FROM cx_negocios WHERE simbolo=? AND quando>?", (simbolo, agora - 86400)).fetchone()
    donos = S.db.execute("SELECT COUNT(*) FROM cx_saldos WHERE simbolo=? AND qtd>0", (simbolo,)).fetchone()[0]
    var = round((p / p24 - 1) * 100, 2) if p24 else 0
    mini = [r[0] for r in S.db.execute("SELECT preco FROM (SELECT id, preco FROM cx_negocios WHERE simbolo=? ORDER BY id DESC LIMIT 40) ORDER BY id",
                                       (simbolo,))]
    return {"forca": _forca(simbolo, oferta, real, var, donos, agora), "mini": [preco(virtual, 0, oferta)] + mini, "simbolo": simbolo, "nome": nome, "descricao": descricao, "emoji": emoji, "cor": cor, "criador": criador, "criada": criada,
            "preco": p, "variacao_24h": round((p / p24 - 1) * 100, 2) if p24 else 0, "volume_24h": S.fmt(vol[0]), "negocios_24h": vol[1],
            "valor_mercado": round(p * oferta / U, 4), "reserva_ptg": S.fmt(real), "vendidas_pct": round((oferta - reserva) * 100 / oferta, 3),
            "donos": donos, "banida": bool(banida), "motivo": motivo or ""}


COLUNAS = "simbolo, nome, descricao, emoji, cor, criador, criada, virtual, real, reserva, oferta, banida, motivo"


def mercado():
    with S.trava:
        agora = time.time()
        moedas = [_resumo_moeda(row, agora) for row in S.db.execute("SELECT %s FROM cx_moedas" % COLUNAS).fetchall()]
        r = regras()
        ult = [{"simbolo": s, "lado": l, "ptg": S.fmt(p), "quando": q} for s, l, p, q in
               S.db.execute("SELECT simbolo, lado, ptg, quando FROM cx_negocios ORDER BY id DESC LIMIT 20")]
    moedas.sort(key=lambda m: -m["valor_mercado"])
    with S.trava:
        top = [{"carteira": c, "volume": S.fmt(v), "negocios": n} for c, v, n in S.db.execute(
            "SELECT carteira, SUM(ptg), COUNT(*) FROM cx_negocios WHERE quando>? GROUP BY carteira ORDER BY SUM(ptg) DESC LIMIT 8",
            (time.time() - 7 * 86400,))]
    publicas = {k: r[k] for k in ("taxa_criacao_ptg", "taxa_dono_pct", "taxa_criador_pct", "trava_criador_dias", "max_carteira_pct",
                                  "max_moedas_dia", "min_negocio_ptg", "compras_pausadas", "criacao_pausada", "reserva_virtual_ptg", "oferta_moedas", "apoio_btc")}
    return {"moedas": moedas, "regras": publicas, "top": top, "deposito": CX["endereco"], "ultimos": ult, "emojis": EMOJIS, "cores": CORES}


def moeda(simbolo):
    with S.trava:
        row = S.db.execute("SELECT %s FROM cx_moedas WHERE simbolo=?" % COLUNAS, (simbolo,)).fetchone()
        if not row:
            return {"erro": "moeda não encontrada"}
        info = _resumo_moeda(row, time.time())
        info["negocios"] = [{"lado": l, "carteira": c, "ptg": S.fmt(p), "qtd": S.fmt(q), "preco": pr, "quando": t} for l, c, p, q, pr, t in
                            S.db.execute("SELECT lado, carteira, ptg, qtd, preco, quando FROM cx_negocios WHERE simbolo=? ORDER BY id DESC LIMIT 300", (simbolo,))]
        oferta = row[10]
        info["maiores"] = [{"carteira": c, "qtd": S.fmt(q), "pct": round(q * 100 / oferta, 3)} for c, q in
                           S.db.execute("SELECT carteira, qtd FROM cx_saldos WHERE simbolo=? AND qtd>0 ORDER BY qtd DESC LIMIT 10", (simbolo,))]
        info["travadas_criador"] = S.fmt(S.db.execute("SELECT COALESCE(SUM(qtd),0) FROM cx_travas WHERE simbolo=? AND ate>?",
                                                      (simbolo, time.time())).fetchone()[0])
        t = S.db.execute("SELECT MAX(ate) FROM cx_travas WHERE simbolo=?", (simbolo,)).fetchone()[0]
        info["trava_ate"] = t
        info["preco_inicial"] = row[7] / row[10]
        agora = time.time()
        p24 = [p for (p,) in S.db.execute("SELECT preco FROM cx_negocios WHERE simbolo=? AND quando>?", (simbolo, agora - 86400))]
        ant = S.db.execute("SELECT preco FROM cx_negocios WHERE simbolo=? AND quando<=? ORDER BY id DESC LIMIT 1", (simbolo, agora - 86400)).fetchone()
        base = [ant[0] if ant else info["preco_inicial"]]
        info["minima_24h"], info["maxima_24h"] = min(base + p24 + [info["preco"]]), max(base + p24 + [info["preco"]])
        info["curva"] = {"x": row[7] + row[8], "reserva": row[9], "oferta": row[10]}
        info["maxima"] = max([info["preco_inicial"]] + [x["preco"] for x in info["negocios"]])
    return info


def conta(carteira):
    if not S.endereco_valido(carteira):
        return {"erro": "carteira inválida"}
    with S.trava:
        agora = time.time()
        pos = []
        for simbolo, qtd in S.db.execute("SELECT simbolo, qtd FROM cx_saldos WHERE carteira=? AND qtd>0", (carteira,)).fetchall():
            v, re_, res = S.db.execute("SELECT virtual, real, reserva FROM cx_moedas WHERE simbolo=?", (simbolo,)).fetchone()
            valor = vender_na_curva(v, re_, res, qtd)
            pos.append({"simbolo": simbolo, "qtd": S.fmt(qtd), "unidades": qtd, "travadas": S.fmt(travadas(simbolo, carteira, agora)),
                        "valor_ptg": S.fmt(valor), "preco": preco(v, re_, res)})
        movs = [{"tipo": t, "valor": S.fmt(u), "ref": r, "quando": q} for t, u, r, q in
                S.db.execute("SELECT tipo, unidades, ref, quando FROM cx_movimentos WHERE carteira=? ORDER BY id DESC LIMIT 50", (carteira,))]
        negs = [{"simbolo": s, "lado": l, "ptg": S.fmt(p), "qtd": S.fmt(q), "quando": t} for s, l, p, q, t in
                S.db.execute("SELECT simbolo, lado, ptg, qtd, quando FROM cx_negocios WHERE carteira=? ORDER BY id DESC LIMIT 50", (carteira,))]
        criadas = [s for (s,) in S.db.execute("SELECT simbolo FROM cx_moedas WHERE criador=?", (carteira,))]
        ganhos_criador = S.db.execute("SELECT COALESCE(SUM(n.taxa_criador),0) FROM cx_negocios n JOIN cx_moedas m ON m.simbolo=n.simbolo "
                                      "WHERE m.criador=?", (carteira,)).fetchone()[0]
        return {"carteira": carteira, "ptg": S.fmt(ptg_de(carteira)), "ptg_unidades": ptg_de(carteira), "posicoes": pos, "movimentos": movs,
                "negocios": negs, "criadas": criadas, "ganhos_criador": S.fmt(ganhos_criador), "deposito": CX["endereco"],
                "eh_dono": carteira == DONO}


# ---------------------------------------------------------------- painel do dono (só do PC)
def painel_dono():
    with S.trava:
        receita = S.db.execute("SELECT COALESCE(SUM(taxa_dono),0), COUNT(*), COALESCE(SUM(ptg),0) FROM cx_negocios").fetchone()
        criacao = S.db.execute("SELECT COALESCE(SUM(unidades),0) FROM cx_movimentos WHERE carteira=? AND tipo='taxa de criação recebida'",
                               (DONO,)).fetchone()[0]
        hoje = S.db.execute("SELECT COALESCE(SUM(taxa_dono),0) FROM cx_negocios WHERE quando>?", (time.time() - 86400,)).fetchone()[0]
        import feira
        devido = S.db.execute("SELECT COALESCE(SUM(saldo),0) FROM cx_ptg").fetchone()[0] + \
            S.db.execute("SELECT COALESCE(SUM(real),0) FROM cx_moedas").fetchone()[0] + feira.em_custodia()
        na_carteira = S.saldo_livre(CX["endereco"])
        n_moedas = S.db.execute("SELECT COUNT(*) FROM cx_moedas").fetchone()[0]
        usuarios = S.db.execute("SELECT COUNT(DISTINCT carteira) FROM cx_movimentos WHERE tipo='depósito'").fetchone()[0]
        return {"regras": regras(), "dono": DONO, "deposito": CX["endereco"], "saldo_dono_corretora": S.fmt(ptg_de(DONO)),
                "receita_negocios": S.fmt(receita[0]), "receita_criacao": S.fmt(criacao), "receita_24h": S.fmt(hoje),
                "negocios": receita[1], "volume_total": S.fmt(receita[2]), "moedas": n_moedas, "usuarios": usuarios,
                "solvencia": {"devido": S.fmt(devido), "na_carteira": S.fmt(na_carteira), "ok": na_carteira >= devido}}


def salvar_regras(novas):
    with S.trava:
        atual = regras()
        for k, v in (novas or {}).items():
            if k not in REGRAS_PADRAO:
                continue
            padrao = REGRAS_PADRAO[k]
            if isinstance(padrao, bool):
                atual[k] = bool(v)
            elif isinstance(padrao, list):
                atual[k] = [str(p).strip()[:30] for p in (v if isinstance(v, list) else str(v).split(",")) if str(p).strip()][:100]
            else:
                try:
                    atual[k] = type(padrao)(v)
                except (TypeError, ValueError):
                    return {"ok": False, "erro": "valor inválido em %s" % k}
        if atual["apoio_btc"] and not BTC_OK.match(atual["apoio_btc"].strip()):
            return {"ok": False, "erro": "endereço bitcoin inválido (começa com bc1, 1 ou 3)"}
        atual["apoio_btc"] = atual["apoio_btc"].strip()
        limites = {"taxa_dono_pct": (0, 10), "taxa_criador_pct": (0, 5), "taxa_criacao_ptg": (0, 100000), "reserva_virtual_ptg": (1, 1000000),
                   "oferta_moedas": (1000, 10 ** 10), "trava_criador_dias": (0, 365), "max_carteira_pct": (0.1, 100),
                   "max_moedas_dia": (0, 100), "min_negocio_ptg": (0.00000001, 1000), "taxa_feira_pct": (0, 10),
                   "feira_liberar_dias": (1, 60), "max_anuncios": (1, 200),
                   "social_max_posts_dia": (1, 500), "social_max_comentarios_hora": (1, 5000),
                   "social_ptg_por_mil": (0, 100), "social_monet_min_seguidores": (0, 10 ** 7),
                   "social_monet_min_curtidas": (0, 10 ** 8), "social_monet_teto_dia": (0, 100000),
                   "social_denuncias_ocultar": (1, 1000), "social_real_min_ptg": (0, 100000), "social_real_dias": (0, 365)}
        for k, (lo, hi) in limites.items():
            if not lo <= atual[k] <= hi:
                return {"ok": False, "erro": "%s precisa ficar entre %s e %s" % (k, lo, hi)}
        S.db.execute("INSERT INTO meta VALUES ('cx_regras', ?) ON CONFLICT(chave) DO UPDATE SET valor=excluded.valor", (json.dumps(atual),))
        S.db.commit()
    return {"ok": True, "regras": atual}


def banir(simbolo, banida, motivo=""):
    with S.trava:
        n = S.db.execute("UPDATE cx_moedas SET banida=?, motivo=? WHERE simbolo=?",
                         (1 if banida else 0, S.limpar_texto(motivo, 120), simbolo)).rowcount
        S.db.commit()
    return {"ok": bool(n)} if n else {"ok": False, "erro": "moeda não encontrada"}


# ---------------------------------------------------------------- rotas HTTP
def _servir_video(h, arq):
    """MP4 da PTG Social com suporte a Range (o celular pede pedaços para tocar e pular no vídeo)."""
    import re as _re
    if not os.path.exists(arq):
        h._resp({"erro": "não encontrado"}, codigo=404)
        return True
    tam = os.path.getsize(arq)
    ini, fim, parcial = 0, tam - 1, False
    m = _re.fullmatch(r"bytes=(\d*)-(\d*)", (h.headers.get("Range") or "").strip())
    if m and (m.group(1) or m.group(2)):
        if m.group(1):
            ini = int(m.group(1))
            fim = min(int(m.group(2)), tam - 1) if m.group(2) else tam - 1
        else:                                   # bytes=-N: os últimos N bytes
            ini = max(0, tam - int(m.group(2)))
        if ini > fim or ini >= tam:
            h.send_response(416)
            h.send_header("Content-Range", "bytes */%d" % tam)
            h.send_header("Content-Length", "0")
            h.end_headers()
            return True
        parcial = True
    h.send_response(206 if parcial else 200)
    h.send_header("Content-Type", "video/mp4")
    h.send_header("Accept-Ranges", "bytes")
    h.send_header("Content-Length", str(fim - ini + 1))
    if parcial:
        h.send_header("Content-Range", "bytes %d-%d/%d" % (ini, fim, tam))
    h.send_header("Cache-Control", "public, max-age=604800")
    h.end_headers()
    if h.command == "HEAD":
        return True
    with open(arq, "rb") as fh:
        fh.seek(ini)
        falta = fim - ini + 1
        try:
            while falta > 0:
                b = fh.read(min(256 * 1024, falta))
                if not b:
                    break
                h.wfile.write(b)
                falta -= len(b)
        except (BrokenPipeError, ConnectionResetError):
            pass
    return True


def rota(h, caminho, a, ip, do_pc):
    """Devolve True se a rota é da corretora (e já respondeu)."""
    if caminho in ("/corretora", "/corretora/"):
        with open(os.path.join(PASTA, "corretora.html"), encoding="utf-8") as fh:
            h._resp(fh.read(), "text/html")
        return True
    if caminho == "/corretora/graficos.js":
        with open(os.path.join(PASTA, "vendor", "lightweight-charts-4.2.0.js"), "rb") as fh:
            b = fh.read()
        h.send_response(200)
        h.send_header("Content-Type", "text/javascript; charset=utf-8")
        h.send_header("Content-Length", str(len(b)))
        h.send_header("Cache-Control", "public, max-age=604800")
        h.end_headers()
        h.wfile.write(b)
        return True
    if caminho.startswith("/corretora/capa/"):
        import re as _re
        import feira
        m = _re.fullmatch(r"/corretora/capa/(\d{1,9})(?:_([1-9]))?\.jpg", caminho)
        arq = feira.caminho_capa(int(m.group(1)), int(m.group(2) or 0)) if m else None
        if not arq or not os.path.exists(arq):
            h._resp({"erro": "não encontrado"}, codigo=404)
            return True
        with open(arq, "rb") as fh:
            b = fh.read()
        h.send_response(200)
        h.send_header("Content-Type", "image/jpeg")
        h.send_header("Content-Length", str(len(b)))
        h.send_header("Cache-Control", "public, max-age=86400")
        h.end_headers()
        h.wfile.write(b)
        return True
    if caminho.startswith("/corretora/social/"):
        import re as _re
        import social
        mv = _re.fullmatch(r"/corretora/social/p(\d{1,9})\.mp4", caminho)
        if mv:
            return _servir_video(h, social.caminho_video(int(mv.group(1))))
        m = _re.fullmatch(r"/corretora/social/(p\d{1,9}|a_ptg[0-9a-f]{20,64})\.jpg", caminho)
        arq = None
        if m:
            arq = social.caminho_foto(int(m.group(1)[1:])) if m.group(1)[0] == "p" else social.caminho_avatar(m.group(1)[2:])
        if not arq or not os.path.exists(arq):
            h._resp({"erro": "não encontrado"}, codigo=404)
            return True
        with open(arq, "rb") as fh:
            b = fh.read()
        h.send_response(200)
        h.send_header("Content-Type", "image/jpeg")
        h.send_header("Content-Length", str(len(b)))
        h.send_header("Cache-Control", "public, max-age=604800")
        h.end_headers()
        h.wfile.write(b)
        return True
    if caminho == "/corretora/dono":
        if not do_pc:
            h._resp("<p style='font-family:sans-serif'>O painel do dono só abre no PC da corretora.</p>", "text/html", codigo=403)
            return True
        with open(os.path.join(PASTA, "corretora_dono.html"), encoding="utf-8") as fh:
            h._resp(fh.read(), "text/html")
        return True
    if not caminho.startswith("/api/cx/"):
        return False
    acao = caminho[len("/api/cx/"):]
    post = h.command == "POST"
    if acao.startswith("feira/"):
        import feira
        feira.rota(h, acao[len("feira/"):], a, do_pc, ip)
        return True
    if acao.startswith("social/"):
        import social
        social.rota(h, acao[len("social/"):], a, do_pc, ip)
        return True
    if acao == "mercado":
        h._resp(mercado())
    elif acao == "velas":
        try:
            h._resp(velas(a.get("s", ""), int(a.get("tf", 300))))
        except ValueError:
            h._resp({"erro": "tempo inválido"}, codigo=400)
    elif acao == "moeda":
        h._resp(moeda(a.get("s", "")))
    elif acao == "conta":
        h._resp(conta(a.get("carteira", "")))
    elif acao == "cotar":
        try:
            h._resp(cotar(a.get("s", ""), a.get("lado", ""), int(a.get("valor", 0))))
        except ValueError:
            h._resp({"erro": "valor inválido"}, codigo=400)
    elif acao in ("criar", "comprar", "vender", "sacar") and post:
        import social
        if not do_pc and social._excedeu(ip, "escrita"):
            h._resp({"ok": False, "erro": "muitas ações seguidas deste aparelho: espere alguns minutos"}, codigo=429)
            return True
        if not h._json():
            h._resp({"ok": False, "erro": "pedido inválido"}, codigo=400)
            return True
        h._resp({"criar": criar, "comprar": comprar, "vender": vender, "sacar": sacar}[acao](a))
    elif acao.startswith("dono/"):
        if not do_pc:
            h._resp({"ok": False, "erro": "só no PC da corretora"}, codigo=403)
        elif acao == "dono/painel":
            h._resp(painel_dono())
        elif acao == "dono/regras" and post and h._json():
            h._resp(salvar_regras(a.get("regras")))
        elif acao == "dono/banir" and post and h._json():
            h._resp(banir(a.get("simbolo", ""), a.get("banida", True), a.get("motivo", "")))
        else:
            h._resp({"erro": "não encontrado"}, codigo=404)
    else:
        h._resp({"erro": "não encontrado"}, codigo=404)
    return True
