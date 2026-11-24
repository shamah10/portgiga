"""Boas-vindas e convites da Portgiga.

- Carteira nova de fora ganha BONUS PTG quando tem a 1a resposta de mineracao aceita (prova que e um aparelho de
  verdade minerando, nao um robo criando carteiras).
- Se ela chegou por um link de convite, quem convidou ganha BONUS_CONVITE PTG na mesma hora.
- O dinheiro sai da carteira de boas-vindas (carteira_boas_vindas.json), por transferencias assinadas normais:
  entram na corrente como qualquer outra e o verificar.py confere tudo. Acabou o saldo dela, acabou o presente.
- Nada disso e venda: PTG nao e vendida por dinheiro e nao e investimento.
- Contador para o dono: carteiras novas, de onde vieram, quantas mineraram, quantas voltaram.
"""
import hashlib
import ipaddress
import json
import os
import re
import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

BONUS_PTG = 10
BONUS_CONVITE_PTG = 10
MAX_BONUS_IP_DIA = 2            # presentes por IP (casa/celular) a cada 24 h
MIN_RESPOSTAS_PRESENTE = 30     # respostas aceitas (~5 min minerando de verdade) antes do presente
MAX_BONUS_DIA = 50              # presentes no total a cada 24 h (protege o fundo de um ataque)
MAX_CONVITES_PAGOS = 20         # quantos convites pagos cada carteira pode ganhar
LIMITE_CHEGADAS_HORA = 30       # registros por IP por hora
ORIGEM_OK = re.compile(r"^[a-z0-9_-]{1,20}$")
CARTEIRA_ARQ = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "carteira_boas_vindas.json")

S = None            # o modulo servidor
fundo = None        # {"endereco", "chave"}
decididos = set()   # carteiras que ja passaram pela decisao do presente (nao consulta o banco de novo)
visto_em = {}       # carteira -> ultima vez que gravamos "ultima_vez"
chegadas_ip = {}    # ip -> [inicio da janela, registros]


def preparar(servidor):
    """Chamado pelo abrir_banco: cria a tabela e marca as carteiras que ja existiam como antigas (nao contam como
    usuario novo e nao ganham presente)."""
    global S, fundo
    S = servidor
    nova = not S.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='chegadas'").fetchone()
    S.db.executescript("""
        CREATE TABLE IF NOT EXISTS chegadas (endereco TEXT PRIMARY KEY, quando REAL, ip TEXT, casa INTEGER NOT NULL DEFAULT 0,
            antiga INTEGER NOT NULL DEFAULT 0, origem TEXT, convite TEXT, minerou_em REAL, bonus_em REAL,
            convite_pago_em REAL, ultima_vez REAL);
    """)
    if nova:
        agora = time.time()
        enderecos = {r[0] for r in S.db.execute("SELECT endereco FROM saldos")}
        enderecos |= {r[0] for r in S.db.execute("SELECT carteira FROM contas")}
        for e in enderecos:
            S.db.execute("INSERT OR IGNORE INTO chegadas (endereco, quando, antiga, origem, ultima_vez) VALUES (?, ?, 1, 'antiga', ?)",
                         (e, agora, agora))
        S.db.commit()
    fundo = None
    if os.path.exists(CARTEIRA_ARQ):
        with open(CARTEIRA_ARQ, encoding="utf-8") as fh:
            fundo = json.load(fh)
        S.db.execute("INSERT OR IGNORE INTO chegadas (endereco, quando, antiga, origem) VALUES (?, ?, 1, 'fundo')",
                     (fundo["endereco"], time.time()))
        S.db.commit()


def eh_casa(ip):
    """IP da rede de casa (o PC do servidor, as placas, o celular no Wi-Fi): nao e usuario de fora."""
    try:
        a = ipaddress.ip_address(ip)
        return a.is_private or a.is_loopback
    except ValueError:
        return True


def criar_carteira_fundo(caminho=None):
    """Cria a carteira de boas-vindas (chave nova, do tipo novo: endereco = hash da chave publica)."""
    chave = os.urandom(32).hex()
    d = {"endereco": S.endereco_do_pub(S.pub_da_chave(chave)), "chave": chave,
         "uso": "presente de boas-vindas e convites (PTG gratis, nunca vendida)"}
    caminho = caminho or CARTEIRA_ARQ
    with open(caminho, "w", encoding="utf-8") as fh:
        json.dump(d, fh, indent=1)
    os.chmod(caminho, 0o600)
    return d


def _pagar(para, ptg):
    """Transferencia assinada da carteira de boas-vindas (entra no proximo bloco). Devolve True se entrou."""
    if not fundo:
        return False
    unidades = ptg * S.UNIDADE
    if S.saldo_livre(fundo["endereco"]) < unidades:
        return False
    de = fundo["endereco"]
    nonce = S.proximo_nonce(de)
    semente = hashlib.sha256(("ptg-ed25519:" + fundo["chave"]).encode()).digest()
    privada = Ed25519PrivateKey.from_private_bytes(semente)
    msg = "portgiga-tx:" + json.dumps([de, para, unidades, nonce], separators=(",", ":"))
    r = S.enviar({"de": de, "para": para, "unidades": unidades, "nonce": nonce,
                  "pub": privada.public_key().public_bytes_raw().hex(), "assinatura": privada.sign(msg.encode()).hex()})
    if not r.get("ok"):
        print("boas-vindas: transferencia recusada: %s" % r.get("erro"), flush=True)
    return bool(r.get("ok"))


def registrar(a, ip):
    """O app avisa que esta carteira abriu (com a origem do link e o convite, se tiver). Nao paga nada aqui."""
    endereco, origem, convite = a.get("endereco"), str(a.get("origem") or "direto").lower()[:20], a.get("convite")
    if not S.endereco_valido(endereco):
        return {"ok": False, "erro": "carteira invalida"}
    if not ORIGEM_OK.match(origem):
        origem = "outro"
    if not S.endereco_valido(convite) or convite == endereco:
        convite = None
    agora = time.time()
    with S.trava:
        r = S.db.execute("SELECT 1 FROM chegadas WHERE endereco=?", (endereco,)).fetchone()
        if not r:
            if not S.dentro_do_limite(chegadas_ip, ip, 3600, LIMITE_CHEGADAS_HORA):
                return {"ok": False, "erro": "muitos pedidos, espere um pouco"}
            S.db.execute("INSERT INTO chegadas (endereco, quando, ip, casa, origem, convite, ultima_vez) VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (endereco, agora, ip, int(eh_casa(ip)), origem, convite, agora))
        else:
            S.db.execute("UPDATE chegadas SET ultima_vez=? WHERE endereco=?", (agora, endereco))
        S.db.commit()
    return situacao(endereco)


def ao_minerar(carteira, ip):
    """Chamado a cada resposta aceita. Na 1a de uma carteira nova de fora: presente + premio de quem convidou."""
    agora = time.time()
    if agora - visto_em.get(carteira, 0) > 600:
        visto_em[carteira] = agora
        S.db.execute("UPDATE chegadas SET ultima_vez=? WHERE endereco=?", (agora, carteira))
        S.db.commit()
    if carteira in decididos:
        return
    with S.trava:
        r = S.db.execute("SELECT antiga, casa, convite, minerou_em, bonus_em FROM chegadas WHERE endereco=?", (carteira,)).fetchone()
        if not r:
            # minerou sem abrir o app antes (ex.: placa ou painel): conta como chegada direta
            S.db.execute("INSERT INTO chegadas (endereco, quando, ip, casa, origem, ultima_vez) VALUES (?, ?, ?, ?, 'direto', ?)",
                         (carteira, agora, ip, int(eh_casa(ip)), agora))
            r = (0, int(eh_casa(ip)), None, None, None)
        antiga, casa, convite, minerou_em, bonus_em = r
        if minerou_em is None:
            S.db.execute("UPDATE chegadas SET minerou_em=? WHERE endereco=?", (agora, carteira))
        if antiga or casa or bonus_em is not None or eh_casa(ip):
            decididos.add(carteira)
            S.db.commit()
            return
        aceitas = sum(m["aceitas"] for m in S.mineradores.values() if m["carteira"] == carteira)
        if aceitas < MIN_RESPOSTAS_PRESENTE:
            S.db.commit()
            return   # ainda não: o presente sai depois de alguns minutos minerando (robô que só cria carteira não leva)
        dia = agora - 86400
        if S.db.execute("SELECT COUNT(*) FROM chegadas WHERE bonus_em > ?", (dia,)).fetchone()[0] >= MAX_BONUS_DIA:
            S.db.commit()
            return   # tenta de novo amanha (nao marca como decidido)
        if S.db.execute("SELECT COUNT(*) FROM chegadas WHERE bonus_em > ? AND ip=?", (dia, ip)).fetchone()[0] >= MAX_BONUS_IP_DIA:
            decididos.add(carteira)
            S.db.commit()
            return
        if not _pagar(carteira, BONUS_PTG):
            S.db.commit()
            return   # fundo vazio: sem presente, mas tenta de novo se o fundo for recarregado
        S.db.execute("UPDATE chegadas SET bonus_em=?, ip=COALESCE(ip, ?) WHERE endereco=?", (agora, ip, carteira))
        decididos.add(carteira)
        if convite:
            c = S.db.execute("SELECT ip FROM chegadas WHERE endereco=?", (convite,)).fetchone()
            pagos = S.db.execute("SELECT COUNT(*) FROM chegadas WHERE convite=? AND convite_pago_em IS NOT NULL", (convite,)).fetchone()[0]
            mesmo_ip = c is not None and c[0] == ip
            if not mesmo_ip and pagos < MAX_CONVITES_PAGOS and _pagar(convite, BONUS_CONVITE_PTG):
                S.db.execute("UPDATE chegadas SET convite_pago_em=? WHERE endereco=?", (agora, carteira))
        S.db.commit()
    print("boas-vindas: presente de %d PTG para %s (convite: %s)" % (BONUS_PTG, carteira, convite or "-"), flush=True)


def situacao(endereco):
    """O que o app mostra: se ja ganhou o presente e quantos amigos convidou."""
    r = S.db.execute("SELECT antiga, casa, bonus_em FROM chegadas WHERE endereco=?", (endereco,)).fetchone()
    convidados = S.db.execute("SELECT COUNT(*), COUNT(convite_pago_em) FROM chegadas WHERE convite=?", (endereco,)).fetchone()
    ativo = bool(fundo) and S.saldo_livre(fundo["endereco"]) >= BONUS_PTG * S.UNIDADE
    return {"ok": True, "bonus_ptg": BONUS_PTG, "bonus_convite_ptg": BONUS_CONVITE_PTG, "ativo": ativo,
            "ganhou": bool(r and r[2]), "pode_ganhar": bool(r is None or (not r[0] and not r[2])),
            "convidados": convidados[0], "convites_pagos": convidados[1],
            "ganho_convites": convidados[1] * BONUS_CONVITE_PTG}


def painel():
    """Numeros para o dono (so no PC)."""
    agora = time.time()
    fora = "antiga=0 AND casa=0"
    q = lambda sql, *p: S.db.execute(sql, p).fetchone()[0]
    origens = S.db.execute("SELECT origem, COUNT(*), COUNT(minerou_em) FROM chegadas WHERE %s GROUP BY origem ORDER BY 2 DESC" % fora).fetchall()
    dias = []
    for i in range(13, -1, -1):
        ini = time.mktime(time.localtime(agora - i * 86400)[:3] + (0, 0, 0, 0, 0, -1))
        dias.append({"dia": time.strftime("%d/%m", time.localtime(ini)),
                     "novas": q("SELECT COUNT(*) FROM chegadas WHERE %s AND quando >= ? AND quando < ?" % fora, ini, ini + 86400),
                     "mineraram": q("SELECT COUNT(*) FROM chegadas WHERE %s AND minerou_em >= ? AND minerou_em < ?" % fora, ini, ini + 86400)})
    ultimas = [dict(zip(("endereco", "quando", "origem", "convite", "minerou", "bonus", "ultima_vez"), r)) for r in S.db.execute(
        "SELECT endereco, quando, origem, convite, minerou_em IS NOT NULL, bonus_em IS NOT NULL, ultima_vez FROM chegadas "
        "WHERE %s ORDER BY quando DESC LIMIT 30" % fora)]
    return {
        "total_fora": q("SELECT COUNT(*) FROM chegadas WHERE %s" % fora),
        "mineraram": q("SELECT COUNT(*) FROM chegadas WHERE %s AND minerou_em IS NOT NULL" % fora),
        "ganharam_presente": q("SELECT COUNT(*) FROM chegadas WHERE bonus_em IS NOT NULL"),
        "convites_pagos": q("SELECT COUNT(*) FROM chegadas WHERE convite_pago_em IS NOT NULL"),
        "voltaram_7d": q("SELECT COUNT(*) FROM chegadas WHERE %s AND ultima_vez - quando >= 7*86400" % fora),
        "da_casa": q("SELECT COUNT(*) FROM chegadas WHERE antiga=0 AND casa=1"),
        "hoje": dias[-1]["novas"],
        "fundo": {"endereco": fundo["endereco"] if fundo else None,
                  "saldo": S.fmt(S.saldo_livre(fundo["endereco"])) if fundo else 0,
                  "presentes_restantes": (S.saldo_livre(fundo["endereco"]) // (BONUS_PTG * S.UNIDADE)) if fundo else 0},
        "origens": [{"origem": o, "carteiras": n, "mineraram": m} for o, n, m in origens],
        "dias": dias, "ultimas": ultimas,
    }
