#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""REDE PORTGIGA (PTG) - servidor da rede, fase 1 (rede local).

Como funciona:
- Carteira: a chave secreta fica so no aparelho do dono. Dela sai um par Ed25519; o endereco (ptg1...) e o hash
  da chave publica. Para mover moedas o aparelho assina a transferencia e o servidor so confere a assinatura.
  Carteiras antigas (endereco = hash da chave secreta) ganham um vinculo unico com a chave publica, feito no PC.
- Qualquer um pode baixar a corrente inteira (/api/cadeia) e conferir com o verificar.py.
- Mineracao: o minerador (ESP32, celular ou PC) pede um trabalho. O servidor sorteia um numero n e envia
  base + alvo = sha256(base + n). O minerador testa 0, 1, 2... ate achar o n que gera o alvo e devolve.
  A dificuldade (o tamanho do intervalo) se ajusta a cada aparelho para cada um entregar ~1 resposta a cada
  10 s, entao uma ESP32 e um celular ganham parecido (a mesma ideia da Duino-Coin).
- Emissao fixa como o Bitcoin: a cada bloco de 1 minuto nascem 10 PTG, divididos entre as respostas aceitas
  naquele minuto. A emissao cai pela metade a cada 2 anos de blocos -> no maximo 21 milhoes de PTG.
- Cada bloco guarda o hash do anterior (corrente a prova de alteracao) e e salvo num banco SQLite.
"""
import hashlib
import json
import math
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

import boasvindas
import corretora
import derivacao

NOME = "Portgiga"
SIMBOLO = "PTG"
PORTA = 7777
PASTA = os.path.dirname(os.path.abspath(__file__))
# link público atual da Arena Fichas (gravado pelo plataforma-jogos/deploy/link-publico.sh quando o túnel abre)
ARENA_LINK_ARQ = os.path.expanduser("~/.local/share/arena-fichas/tunel/link.txt")
ARENA_LOCAL = "http://localhost:8100/"
# link público atual da própria Portgiga (gravado pelo link-publico.sh): é o endereço que as placas de fora usam
PORTGIGA_LINK_ARQ = os.path.expanduser("~/.local/share/portgiga/tunel/link.txt")
# app da carteira para celular (PWA): só estes arquivos de servidor/app/ são servidos
APP_PASTA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app")
APP_TIPOS = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".webmanifest": "application/manifest+json",
             ".png": "image/png", ".svg": "image/svg+xml", ".apk": "application/vnd.android.package-archive"}
APP_ARQUIVOS = {"index.html", "app.js", "minerador.js", "app.css", "qrcode.js", "sw.js", "manifest.webmanifest", "Portgiga-Carteira.apk"}
BANCO = os.path.join(PASTA, "dados", "rede.db")

UNIDADE = 100_000_000                 # 1 PTG = 100 milhoes de unidades (como satoshis)
BLOCO_S = 60                          # um bloco por minuto
EMISSAO_INICIAL = 10 * UNIDADE        # PTG por bloco
BLOCOS_POR_METADE = 1_051_200         # 2 anos de blocos de 1 minuto
OFERTA_MAX = 2 * EMISSAO_INICIAL * BLOCOS_POR_METADE   # 21.024.000 PTG

ALVO_S = 10                           # tempo desejado por resposta
TRABALHO_VALE_S = 120
MAX_MINERADORES_CARTEIRA = 64
MAX_MINERADORES_IP = 16   # cada placa de verdade tem seu proprio IP na rede local; de fora, 16 por casa basta
MINERADOR_SUMIDO_S = 40      # cada minerador fala com o servidor a cada ~10 s (no maximo ~20 s)
# peso de cada resposta e velocidade maxima plausivel por tipo de aparelho
PESO = {"esp32": 1.0, "celular": 1.0, "pc": 0.25}
HPS_MAX = {"esp32": 3_000_000, "celular": 60_000_000, "pc": 10_000_000_000}
DIFICULDADE_INICIAL = {"esp32": 300_000, "celular": 1_000_000, "pc": 5_000_000}
DIFICULDADE_MIN = 5_000

# protecao para quando o servidor estiver na internet
LIMITE_PEDIDOS_MIN = 1200          # por IP (64 mineradores x ~13 pedidos/min cabem folgado)
MAX_PENDENTES = 5000               # transferencias esperando o proximo bloco
ASSINATURA_VALE_S = 300            # idade maxima de um pedido assinado (renomear, mural)
MAX_OFERTAS_CARTEIRA = 10          # anuncios abertos por carteira no mural de trocas
LIMITE_OFERTAS_HORA = 20           # anuncios novos por IP por hora
# o mural e para trocar PTG por coisas e servicos; vender PTG por dinheiro de verdade fica de fora
PALAVRAS_DINHEIRO = re.compile(r"r\$|\b(reais|pix|dinheiro|d[oó]lar(es)?|usd|usdt|brl|investimento|lucro|valoriza\w*|rendimentos?)\b")

# carteiras que ganharam recompensa ate este bloco sao do tipo antigo e podem ser vinculadas (uma vez) a uma chave publica
ALTURA_LEGADO = 130

# contas: login com usuario e senha. O navegador nunca manda a senha de verdade: manda uma derivada dela
# (PBKDF2) e guarda aqui a chave da carteira CIFRADA com outra derivada que nunca sai do aparelho.
# Assim o servidor nao consegue abrir a carteira de ninguem. As placas de cada carteira so aparecem para o dono logado.
SESSAO_S = 30 * 24 * 3600
LIMITE_CADASTROS_HORA = 5          # por IP
LIMITE_LOGIN_ERRADO = 10           # tentativas erradas por IP a cada 15 min
LIMITE_DOWNLOADS_HORA = 10         # downloads do minerador por conta
LIMITE_LOGIN_ERRADO_CONTA = 20     # senhas erradas por USUÁRIO a cada hora (ataque espalhado em muitos IPs)
MAX_CONEXOES = 300                 # pedidos atendidos ao mesmo tempo (o resto espera na fila do sistema)
TEMPO_CONEXAO_S = 30               # conexão parada (ataque "slowloris") é derrubada
# nomes de servidor aceitos no cabeçalho Host (contra "DNS rebinding": um site malicioso fingindo ser este servidor)
HOST_OK = re.compile(r"^(localhost|127\.0\.0\.1|\[::1\]|10(\.\d{1,3}){3}|192\.168(\.\d{1,3}){2}|172\.(1[6-9]|2\d|3[01])(\.\d{1,3}){2}"
                     r"|[a-z0-9-]+\.local|akwyles-10|[a-z0-9-]+\.trycloudflare\.com|([a-z0-9-]+\.)*portgiga\.com\.br)(:\d{1,5})?$")
HOST_PC = re.compile(r"^(localhost|127\.0\.0\.1|\[::1\])(:\d{1,5})?$")
CSP_PAGINAS = ("default-src 'self'; script-src 'self' 'unsafe-inline' blob:; worker-src 'self' blob:; style-src 'self' 'unsafe-inline'; "
               "img-src 'self' data: blob:; media-src 'self' blob:; font-src 'self' data:; connect-src 'self'; object-src 'none'; "
               "base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
# rede independente: cada carteira registra (assinando) o endereço novo dela; a foto dos saldos usa esses registros.
# No lançamento, definir a altura em que o registro fecha (a rede atual para nesse bloco).
MIGRACAO_CONGELADA_EM_ALTURA = None
USUARIO_OK = re.compile(r"^[A-Za-z0-9_.-]{3,20}$")

trava = threading.RLock()
contagem_ip = {}      # ip -> [inicio da janela, pedidos]
ofertas_ip = {}       # ip -> [inicio da janela, anuncios criados]
cadastros_ip = {}     # ip -> [inicio da janela, contas criadas]
login_ip = {}         # ip -> [inicio da janela, senhas erradas]
login_conta = {}      # usuario -> [inicio da janela, senhas erradas]
downloads_conta = {}  # conta -> [inicio da janela, downloads do minerador]
cache_estado = [0.0, None]
db = None
mineradores = {}      # (carteira, id) -> dados do minerador
trabalhos = {}        # id do trabalho -> dados
rodada = {}           # carteira -> peso acumulado no bloco atual
rodada_min = {}       # (carteira, id) -> peso acumulado no bloco atual (para mostrar quanto cada minerador ganhou)
historico = []        # [t, velocidade total, mineradores online] a cada 15 s nas ultimas 24 h
transferencias = []   # transferencias assinadas que entram no proximo bloco
vinculos_pendentes = {}   # endereco antigo -> chave publica, entram no proximo bloco
legado = set()        # enderecos do tipo antigo (ver ALTURA_LEGADO)
inicio_bloco = time.time()


def endereco_da_chave(chave):
    """Endereco do tipo antigo: hash da chave secreta."""
    return "ptg1" + hashlib.sha256(("ptg:" + chave).encode()).hexdigest()[:38]


def pub_da_chave(chave):
    """Chave publica Ed25519 que sai da chave secreta (a pagina faz a mesma conta)."""
    semente = hashlib.sha256(("ptg-ed25519:" + chave).encode()).digest()
    return Ed25519PrivateKey.from_private_bytes(semente).public_key().public_bytes_raw().hex()


def endereco_do_pub(pub):
    return "ptg1" + hashlib.sha256(("ptg-pub:" + pub).encode()).hexdigest()[:38]


def link_de_arquivo(caminho):
    """Link https://… gravado por um túnel, sem barra no fim; None se o arquivo não existe ou é estranho."""
    try:
        with open(caminho, encoding="utf-8") as fh:
            url = fh.read().strip()
    except OSError:
        return None
    if not re.fullmatch(r"https://[a-z0-9-]+(\.[a-z0-9-]+)+/?", url):
        return None
    return url.rstrip("/")


def link_arena():
    """Link público da Arena (com barra no fim), ou None se o túnel não estiver aberto."""
    url = link_de_arquivo(ARENA_LINK_ARQ)
    return url + "/" if url else None


def eh_hex(s, tamanho):
    return isinstance(s, str) and len(s) == tamanho and all(c in "0123456789abcdef" for c in s)


def assinatura_ok(pub, mensagem, assinatura):
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub)).verify(bytes.fromhex(assinatura), mensagem.encode())
        return True
    except Exception:
        return False


def vinculo_de(endereco):
    if endereco in vinculos_pendentes:
        return vinculos_pendentes[endereco]
    r = db.execute("SELECT pub FROM vinculos WHERE endereco=?", (endereco,)).fetchone()
    return r[0] if r else None


def pub_manda_em(endereco, pub):
    return endereco_do_pub(pub) == endereco or vinculo_de(endereco) == pub


def hash_do_bloco(altura, anterior, quando, dados):
    corpo = json.dumps({"altura": altura, "anterior": anterior, "quando": quando, "dados": dados}, sort_keys=True)
    return hashlib.sha256(corpo.encode()).hexdigest()


def endereco_valido(e):
    return isinstance(e, str) and len(e) == 42 and e.startswith("ptg1") and all(c in "0123456789abcdef" for c in e[4:])


def fmt(unidades):
    return round(unidades / UNIDADE, 8)


def abrir_banco():
    global db
    os.makedirs(os.path.dirname(BANCO), exist_ok=True)
    db = sqlite3.connect(BANCO, check_same_thread=False)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS saldos (endereco TEXT PRIMARY KEY, saldo INTEGER NOT NULL DEFAULT 0, minerado INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS apelidos (carteira TEXT, id TEXT, nome TEXT, PRIMARY KEY (carteira, id));
        CREATE TABLE IF NOT EXISTS blocos (altura INTEGER PRIMARY KEY, hash TEXT, anterior TEXT, quando REAL,
                                           emissao INTEGER, respostas REAL, dados TEXT);
        CREATE TABLE IF NOT EXISTS vinculos (endereco TEXT PRIMARY KEY, pub TEXT NOT NULL, altura INTEGER);
        CREATE TABLE IF NOT EXISTS contadores (endereco TEXT PRIMARY KEY, proximo INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS meta (chave TEXT PRIMARY KEY, valor TEXT);
        CREATE TABLE IF NOT EXISTS contas (id INTEGER PRIMARY KEY AUTOINCREMENT, usuario TEXT NOT NULL UNIQUE COLLATE NOCASE,
                                           senha TEXT NOT NULL, sal TEXT NOT NULL, carteira TEXT NOT NULL UNIQUE,
                                           chave_cifrada TEXT NOT NULL, criada REAL);
        CREATE TABLE IF NOT EXISTS migracoes (carteira TEXT PRIMARY KEY, endereco_novo TEXT NOT NULL, pub TEXT NOT NULL, quando REAL);
        CREATE TABLE IF NOT EXISTS sessoes (token TEXT PRIMARY KEY, conta INTEGER NOT NULL, criada REAL, expira REAL);
        CREATE TABLE IF NOT EXISTS ofertas (id INTEGER PRIMARY KEY AUTOINCREMENT, carteira TEXT, titulo TEXT, descricao TEXT,
                                            preco INTEGER, criada REAL, aberta INTEGER NOT NULL DEFAULT 1, pub TEXT, assinatura TEXT);
    """)
    if not db.execute("SELECT 1 FROM blocos LIMIT 1").fetchone():
        g = {"rede": NOME, "simbolo": SIMBOLO, "mensagem": "Bloco inicial da rede Portgiga - simbolo: o pau-brasil", "oferta_max": OFERTA_MAX}
        h = hashlib.sha256(json.dumps(g, sort_keys=True).encode()).hexdigest()
        db.execute("INSERT INTO blocos VALUES (0, ?, ?, ?, 0, 0, ?)", (h, "0" * 64, time.time(), json.dumps(g)))
        db.commit()
    corrigir_quando_antigos()
    for altura, dados in db.execute("SELECT altura, dados FROM blocos WHERE altura BETWEEN 1 AND ?", (ALTURA_LEGADO,)):
        legado.update(json.loads(dados).get("recompensas", {}))
    corretora.preparar(sys.modules[__name__])
    boasvindas.preparar(sys.modules[__name__])


def corrigir_quando_antigos():
    """Ate a versao com assinaturas o hash usava a hora arredondada, mas o banco guardava a hora cheia (alguns
    microssegundos depois). Acha a hora exata que gera cada hash e guarda ela: os hashes nao mudam, so ficam conferiveis."""
    if db.execute("SELECT 1 FROM meta WHERE chave='quando_corrigido'").fetchone():
        return
    for altura, h, anterior, quando, dados in db.execute("SELECT altura, hash, anterior, quando, dados FROM blocos WHERE altura > 0").fetchall():
        d = json.loads(dados)
        for k in range(200):
            q = round(round(quando, 3) - k / 1000, 3)
            if hash_do_bloco(altura, anterior, q, d) == h:
                db.execute("UPDATE blocos SET quando=? WHERE altura=?", (q, altura))
                break
        else:
            print("aviso: o hash do bloco %d nao confere" % altura, flush=True)
    db.execute("INSERT INTO meta VALUES ('quando_corrigido', '1')")
    db.commit()


def saldo(endereco):
    r = db.execute("SELECT saldo FROM saldos WHERE endereco=?", (endereco,)).fetchone()
    return r[0] if r else 0


def creditar(endereco, valor, minerado=False):
    db.execute("INSERT INTO saldos (endereco, saldo, minerado) VALUES (?, ?, ?) ON CONFLICT(endereco) DO UPDATE SET "
               "saldo = saldo + excluded.saldo, minerado = minerado + excluded.minerado", (endereco, valor, valor if minerado else 0))


def emissao_do_bloco(altura):
    return EMISSAO_INICIAL >> ((altura - 1) // BLOCOS_POR_METADE)


def fechar_bloco():
    """Grava o bloco do minuto: primeiro os vinculos, depois as transferencias assinadas, por fim divide a emissao
    entre as respostas aceitas. Saldos e bloco vao para o banco juntos (o verificar.py refaz tudo nesta ordem)."""
    global rodada, rodada_min, transferencias, inicio_bloco
    with trava:
        ult = db.execute("SELECT altura, hash FROM blocos ORDER BY altura DESC LIMIT 1").fetchone()
        altura = ult[0] + 1
        vinculos = [{"endereco": e, "pub": p} for e, p in sorted(vinculos_pendentes.items())]
        for v in vinculos:
            db.execute("INSERT OR IGNORE INTO vinculos VALUES (?, ?, ?)", (v["endereco"], v["pub"], altura))
        feitas = []
        for t in transferencias:
            if saldo(t["de"]) < t["unidades"]:
                continue   # nao deveria acontecer: o envio ja conferiu o saldo livre
            creditar(t["de"], -t["unidades"])
            creditar(t["para"], t["unidades"])
            db.execute("INSERT INTO contadores VALUES (?, ?) ON CONFLICT(endereco) DO UPDATE SET proximo = excluded.proximo",
                       (t["de"], t["nonce"] + 1))
            feitas.append(t)
        total_peso = sum(rodada.values())
        emitido = db.execute("SELECT COALESCE(SUM(emissao), 0) FROM blocos").fetchone()[0]
        emissao = min(emissao_do_bloco(altura), OFERTA_MAX - emitido) if total_peso > 0 else 0
        recompensas = {}
        if emissao > 0:
            sobra = emissao
            for i, (cart, peso) in enumerate(sorted(rodada.items())):
                v = sobra if i == len(rodada) - 1 else int(emissao * peso / total_peso)
                sobra -= v
                recompensas[cart] = v
                creditar(cart, v, minerado=True)
            for chave, peso in rodada_min.items():
                if chave in mineradores:
                    mineradores[chave]["ganho"] += fmt(int(emissao * peso / total_peso))
        if not recompensas and not feitas and not vinculos:
            db.rollback()
            transferencias.clear()
            inicio_bloco = time.time()
            rodada_min = {}
            return   # minuto sem nada: nao cria bloco vazio
        dados = {"recompensas": {k: fmt(v) for k, v in recompensas.items()}, "transferencias": feitas}
        if vinculos:
            dados["vinculos"] = vinculos
        quando = round(time.time(), 3)
        db.execute("INSERT INTO blocos VALUES (?, ?, ?, ?, ?, ?, ?)",
                   (altura, hash_do_bloco(altura, ult[1], quando, dados), ult[1], quando, emissao, total_peso, json.dumps(dados)))
        db.commit()
        rodada, rodada_min, transferencias, inicio_bloco = {}, {}, [], time.time()
        vinculos_pendentes.clear()


def registrar_historico():
    while True:
        with trava:
            agora = time.time()
            ativos = [m for m in mineradores.values() if agora - m["visto"] < MINERADOR_SUMIDO_S]
            historico.append([round(agora), round(sum(m["hps"] for m in ativos)), len(ativos)])
            del historico[:-5760]
        time.sleep(15)


def nome_salvo(carteira, mid):
    r = db.execute("SELECT nome FROM apelidos WHERE carteira=? AND id=?", (carteira, mid)).fetchone()
    return r[0] if r else ""


def limpar_nome(nome):
    return "".join(c for c in str(nome or "") if c.isprintable() and c not in "<>\"&")[:24].strip()


def renomear(a):
    carteira, mid, nome, quando, pub = a.get("carteira"), a.get("id", ""), a.get("nome", ""), a.get("quando"), a.get("pub")
    if not (endereco_valido(carteira) and isinstance(mid, str) and isinstance(nome, str) and isinstance(quando, int)
            and eh_hex(pub, 64) and eh_hex(a.get("assinatura"), 128)):
        return {"ok": False, "erro": "pedido invalido"}
    if abs(time.time() - quando) > ASSINATURA_VALE_S:
        return {"ok": False, "erro": "pedido vencido: confira o relogio do aparelho"}
    mensagem = "portgiga-renomear:" + json.dumps([carteira, mid, nome, quando], separators=(",", ":"), ensure_ascii=False)
    with trava:
        if not pub_manda_em(carteira, pub) or not assinatura_ok(pub, mensagem, a["assinatura"]):
            return {"ok": False, "erro": "assinatura invalida para esta carteira"}
        nome = limpar_nome(nome)
        m = mineradores.get((carteira, mid))
        if not m:
            return {"ok": False, "erro": "minerador nao encontrado nesta carteira"}
        db.execute("INSERT INTO apelidos VALUES (?, ?, ?) ON CONFLICT(carteira, id) DO UPDATE SET nome = excluded.nome", (carteira, mid, nome))
        db.commit()
        m["nome"] = nome or m["id"]
    return {"ok": True, "nome": m["nome"]}


def produtor_de_blocos():
    while True:
        time.sleep(max(0.5, inicio_bloco + BLOCO_S - time.time()))
        try:
            fechar_bloco()
        except Exception as e:
            print("erro ao fechar bloco:", e)
        with trava:
            agora = time.time()
            for k in [k for k, t in trabalhos.items() if agora - t["em"] > TRABALHO_VALE_S]:
                del trabalhos[k]
            for k in [k for k, m in mineradores.items() if agora - m["visto"] > 24 * 3600]:
                del mineradores[k]


# ---------------------------------------------------------------- mineracao
def novo_trabalho(carteira, aparelho, mid, ip, nome=""):
    if not endereco_valido(carteira):
        return {"erro": "carteira invalida"}
    if aparelho not in PESO:
        return {"erro": "aparelho deve ser esp32, celular ou pc"}
    mid = (mid or "sem-nome")[:32]
    agora = time.time()
    with trava:
        chave = (carteira, mid)
        m = mineradores.get(chave)
        if not m:
            ativos = [x for x in mineradores.values() if agora - x["visto"] < MINERADOR_SUMIDO_S]
            if sum(1 for x in ativos if x["carteira"] == carteira) >= MAX_MINERADORES_CARTEIRA:
                return {"erro": "limite de %d mineradores por carteira" % MAX_MINERADORES_CARTEIRA}
            if sum(1 for x in ativos if x["ip"] == ip) >= MAX_MINERADORES_IP:
                return {"erro": "limite de %d mineradores por IP" % MAX_MINERADORES_IP}
            m = mineradores[chave] = {"carteira": carteira, "id": mid, "aparelho": aparelho, "ip": ip,
                                      "dificuldade": DIFICULDADE_INICIAL[aparelho], "hps": 0.0,
                                      "aceitas": 0, "rejeitadas": 0, "visto": agora, "desde": agora, "ganho": 0.0,
                                      "nome": nome_salvo(carteira, mid) or limpar_nome(nome) or mid}
        m.update(aparelho=aparelho, ip=ip, visto=agora)
        if limpar_nome(nome) and limpar_nome(nome) != m["nome"] and not nome_salvo(carteira, mid):
            m["nome"] = limpar_nome(nome)
        n = secrets.randbelow(m["dificuldade"])
        base = secrets.token_hex(20)
        tid = secrets.token_hex(8)
        alvo = hashlib.sha256((base + str(n)).encode()).hexdigest()
        trabalhos[tid] = {"chave": chave, "n": n, "em": agora, "base": base, "alvo": alvo}
        return {"trabalho": tid, "base": base, "alvo": alvo, "max": m["dificuldade"]}


def entregar(tid, nonce):
    agora = time.time()
    with trava:
        t = trabalhos.pop(tid, None)
        if not t:
            return {"aceito": False, "erro": "trabalho desconhecido ou vencido"}
        m = mineradores.get(t["chave"])
        if not m:
            return {"aceito": False, "erro": "minerador desconhecido"}
        m["visto"] = agora
        dt = max(agora - t["em"], 0.05)
        hps = (t["n"] + 1) / dt
        if str(nonce) != str(t["n"]):
            m["rejeitadas"] += 1
            return {"aceito": False, "erro": "resposta errada"}
        if hps > HPS_MAX[m["aparelho"]]:
            m["rejeitadas"] += 1
            return {"aceito": False, "erro": "rapido demais para um(a) %s" % m["aparelho"]}
        m["aceitas"] += 1
        # prova publica: qualquer um pode conferir que sha256(base + n) == hash
        m["prova"] = {"base": t["base"], "n": t["n"], "hash": t["alvo"], "quando": agora, "segundos": round(dt, 1), "tentativas": t["n"] + 1}
        m["hps"] = hps if m["hps"] == 0 else 0.8 * m["hps"] + 0.2 * hps
        # proxima dificuldade: ~ALVO_S segundos na media (o numero sorteado cai, em media, no meio do intervalo)
        m["dificuldade"] = int(max(DIFICULDADE_MIN, min(2 * m["hps"] * ALVO_S, m["dificuldade"] * 2)))
        peso = PESO[m["aparelho"]]
        rodada[m["carteira"]] = rodada.get(m["carteira"], 0) + peso
        rodada_min[t["chave"]] = rodada_min.get(t["chave"], 0) + peso
        try:
            boasvindas.ao_minerar(m["carteira"], m["ip"])
        except Exception as erro:   # o presente nunca pode derrubar a mineracao
            print("boas-vindas: erro %r" % erro, flush=True)
        return {"aceito": True, "hps": round(m["hps"]), "proxima_dificuldade": m["dificuldade"],
                "saldo": fmt(saldo(m["carteira"])), "proximo_bloco_s": round(inicio_bloco + BLOCO_S - agora)}


# ---------------------------------------------------------------- carteira
def proximo_nonce(endereco):
    """Numero de ordem da proxima transferencia desta carteira (impede repetir uma transferencia ja assinada)."""
    r = db.execute("SELECT proximo FROM contadores WHERE endereco=?", (endereco,)).fetchone()
    return (r[0] if r else 0) + sum(1 for t in transferencias if t["de"] == endereco)


def saldo_livre(endereco):
    return saldo(endereco) - sum(t["unidades"] for t in transferencias if t["de"] == endereco)


def enviar(a):
    de, para, unidades, nonce, pub, assinatura = (a.get(k) for k in ("de", "para", "unidades", "nonce", "pub", "assinatura"))
    if not endereco_valido(de):
        return {"ok": False, "erro": "carteira de origem invalida"}
    if not endereco_valido(para) or para == de:
        return {"ok": False, "erro": "endereco de destino invalido"}
    if type(unidades) is not int or unidades <= 0 or unidades > OFERTA_MAX:
        return {"ok": False, "erro": "valor invalido"}
    if type(nonce) is not int or nonce < 0 or not eh_hex(pub, 64) or not eh_hex(assinatura, 128):
        return {"ok": False, "erro": "pedido invalido"}
    mensagem = "portgiga-tx:" + json.dumps([de, para, unidades, nonce], separators=(",", ":"))
    with trava:
        if not pub_manda_em(de, pub):
            if de in legado and not vinculo_de(de):
                return {"ok": False, "erro": "carteira antiga ainda nao ativada: abra a carteira uma vez pelo painel do PC"}
            return {"ok": False, "erro": "esta chave nao e dona dessa carteira"}
        if not assinatura_ok(pub, mensagem, assinatura):
            return {"ok": False, "erro": "assinatura invalida"}
        if nonce != proximo_nonce(de):
            return {"ok": False, "erro": "numero de ordem errado: atualize a pagina e tente de novo"}
        if saldo_livre(de) < unidades:
            return {"ok": False, "erro": "saldo insuficiente"}
        if len(transferencias) >= MAX_PENDENTES:
            return {"ok": False, "erro": "rede ocupada, tente no proximo bloco"}
        transferencias.append({"de": de, "para": para, "valor": fmt(unidades), "unidades": unidades, "nonce": nonce,
                               "pub": pub, "assinatura": assinatura, "quando": round(time.time(), 3)})
        livre = saldo_livre(de)
    return {"ok": True, "de": de, "para": para, "valor": fmt(unidades), "saldo": fmt(livre)}


def vincular(chave):
    """Ativa uma carteira antiga: guarda (uma vez, no proximo bloco) a chave publica que sai da chave secreta dela.
    So aceito de dentro do proprio PC, porque a chave secreta viaja neste pedido."""
    if not eh_hex(chave, 64):
        return {"ok": False, "erro": "chave invalida"}
    endereco = endereco_da_chave(chave)
    with trava:
        if endereco not in legado:
            return {"ok": False, "erro": "esta carteira nao e do tipo antigo"}
        if vinculo_de(endereco):
            return {"ok": True, "endereco": endereco, "ja_estava": True}
        vinculos_pendentes[endereco] = pub_da_chave(chave)
    return {"ok": True, "endereco": endereco, "pub": vinculos_pendentes[endereco]}


# ---------------------------------------------------------------- mural de trocas
def pedido_assinado(a, prefixo, campos):
    """Confere um pedido assinado pela carteira (mensagem = prefixo + json dos campos). Devolve o erro ou None."""
    carteira, quando, pub = a.get("carteira"), a.get("quando"), a.get("pub")
    if not endereco_valido(carteira) or type(quando) is not int or not eh_hex(pub, 64) or not eh_hex(a.get("assinatura"), 128):
        return "pedido invalido"
    if abs(time.time() - quando) > ASSINATURA_VALE_S:
        return "pedido vencido: confira o relogio do aparelho"
    mensagem = prefixo + json.dumps([carteira] + campos + [quando], separators=(",", ":"), ensure_ascii=False)
    if not pub_manda_em(carteira, pub) or not assinatura_ok(pub, mensagem, a["assinatura"]):
        return "assinatura invalida para esta carteira"
    return None


def limpar_texto(t, tamanho):
    """Tira caracteres perigosos e espacos sobrando; mantem ate 6 quebras de linha."""
    linhas = (" ".join("".join(c for c in l if c.isprintable() and c not in "<>\"&").split()) for l in str(t or "").splitlines())
    return "\n".join([l for l in linhas if l][:6])[:tamanho]


def anunciar(a):
    titulo, descricao, preco = a.get("titulo"), a.get("descricao", ""), a.get("preco")
    if not isinstance(titulo, str) or not isinstance(descricao, str) or type(preco) is not int:
        return {"ok": False, "erro": "pedido invalido"}
    if not 0 < preco <= OFERTA_MAX:
        return {"ok": False, "erro": "preco invalido"}
    if len(limpar_texto(titulo, 60)) < 3:
        return {"ok": False, "erro": "escreva o que voce oferece (3 letras ou mais)"}
    if PALAVRAS_DINHEIRO.search((titulo + " " + descricao).lower()):
        return {"ok": False, "erro": "o mural e para trocar PTG por coisas e servicos, nao por dinheiro ou outras moedas"}
    with trava:
        erro = pedido_assinado(a, "portgiga-oferta:", [titulo, descricao, preco])
        if erro:
            return {"ok": False, "erro": erro}
        abertas = db.execute("SELECT COUNT(*) FROM ofertas WHERE carteira=? AND aberta=1", (a["carteira"],)).fetchone()[0]
        if abertas >= MAX_OFERTAS_CARTEIRA:
            return {"ok": False, "erro": "limite de %d anuncios abertos por carteira" % MAX_OFERTAS_CARTEIRA}
        cur = db.execute("INSERT INTO ofertas (carteira, titulo, descricao, preco, criada, pub, assinatura) VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (a["carteira"], limpar_texto(titulo, 60), limpar_texto(descricao, 300), preco, time.time(), a["pub"], a["assinatura"]))
        db.commit()
    return {"ok": True, "id": cur.lastrowid}


def encerrar_oferta(a):
    oid = a.get("id")
    if type(oid) is not int:
        return {"ok": False, "erro": "pedido invalido"}
    with trava:
        erro = pedido_assinado(a, "portgiga-encerrar:", [oid])
        if erro:
            return {"ok": False, "erro": erro}
        if not db.execute("UPDATE ofertas SET aberta=0 WHERE id=? AND carteira=? AND aberta=1", (oid, a["carteira"])).rowcount:
            return {"ok": False, "erro": "anuncio nao encontrado nesta carteira"}
        db.commit()
    return {"ok": True}


def mural():
    with trava:
        return {"ofertas": [{"id": i, "carteira": c, "titulo": t, "descricao": d, "preco": fmt(p), "criada": q}
                            for i, c, t, d, p, q in db.execute("SELECT id, carteira, titulo, descricao, preco, criada FROM ofertas "
                                                               "WHERE aberta=1 ORDER BY id DESC LIMIT 200")]}


def cadeia(desde, ate):
    """Blocos completos para conferencia independente (no maximo 1000 por pedido)."""
    with trava:
        topo = db.execute("SELECT MAX(altura) FROM blocos").fetchone()[0]
        desde = max(0, desde)
        ate = min(topo, ate if ate is not None else topo, desde + 999)
        blocos = [{"altura": a, "hash": h, "anterior": an, "quando": q, "dados": json.loads(d)}
                  for a, h, an, q, d in db.execute("SELECT altura, hash, anterior, quando, dados FROM blocos "
                                                   "WHERE altura BETWEEN ? AND ? ORDER BY altura", (desde, ate))]
    return {"altura_atual": topo, "blocos": blocos}


def info_carteira(endereco):
    if not endereco_valido(endereco):
        return {"erro": "endereco invalido"}
    with trava:
        r = db.execute("SELECT saldo, minerado FROM saldos WHERE endereco=?", (endereco,)).fetchone()
        movs = []
        # resumo das últimas 24 h (gráfico do saldo no app): variação líquida por hora, da mais antiga à mais nova
        agora = time.time()
        por_hora = [0.0] * 24
        resumo = {"minerado": 0.0, "recebido": 0.0, "enviado": 0.0, "blocos_minerados": 0}
        for altura, quando, dados in db.execute("SELECT altura, quando, dados FROM blocos WHERE altura > "
                                                "(SELECT MAX(altura) FROM blocos) - 1500 ORDER BY altura DESC"):
            d = json.loads(dados)
            nas_24h = agora - quando < 86400
            hora = min(23, max(0, 23 - int((agora - quando) // 3600)))
            if endereco in d.get("recompensas", {}):
                v = d["recompensas"][endereco]
                if len(movs) < 400:
                    movs.append({"bloco": altura, "quando": quando, "tipo": "mineracao", "valor": v})
                if nas_24h:
                    resumo["minerado"] += v; resumo["blocos_minerados"] += 1; por_hora[hora] += v
        # transferências de toda a cadeia (o filtro LIKE só pré-seleciona; o JSON confere de verdade), as 50 mais novas
        transf = []
        for altura, quando, dados in db.execute("SELECT altura, quando, dados FROM blocos WHERE dados LIKE ? OR dados LIKE ? "
                                                "ORDER BY altura DESC", ('%%"de": "%s"%%' % endereco, '%%"para": "%s"%%' % endereco)):
            for t in json.loads(dados).get("transferencias", []):
                if endereco in (t["de"], t["para"]):
                    enviado = t["de"] == endereco
                    transf.append({"bloco": altura, "quando": t["quando"], "tipo": "enviado" if enviado else "recebido",
                                   "valor": t["valor"], "outro": t["para"] if enviado else t["de"]})
                    if agora - quando < 86400:
                        resumo["enviado" if enviado else "recebido"] += t["valor"]
                        por_hora[min(23, max(0, 23 - int((agora - quando) // 3600)))] += -t["valor"] if enviado else t["valor"]
            if len(transf) >= 50:
                break
        # as transferências nunca somem do histórico no meio de tantas recompensas de mineração
        movs = sorted(transf[:50] + movs[:60], key=lambda m: (m["bloco"], m["quando"]), reverse=True)
        resumo = {k: (round(v, 8) if isinstance(v, float) else v) for k, v in resumo.items()}
        resumo["variacao_por_hora"] = [round(v, 8) for v in por_hora]

        pendente = rodada.get(endereco, 0)
        extra = {"tipo": "antigo" if endereco in legado else "novo", "ativada": endereco not in legado or bool(vinculo_de(endereco)),
                 "proximo_nonce": proximo_nonce(endereco), "saldo_livre": fmt(saldo_livre(endereco))}
    return {"endereco": endereco, "saldo": fmt(r[0]) if r else 0, "minerado": fmt(r[1]) if r else 0,
            "respostas_no_bloco_atual": pendente, "movimentos": movs, "resumo_24h": resumo, **extra}


def estado_rede():
    with trava:
        if cache_estado[1] is not None and time.time() - cache_estado[0] < 1:
            return cache_estado[1]
        cache_estado[0], cache_estado[1] = time.time(), _estado_rede()
        return cache_estado[1]


def dentro_do_limite(tabela, ip, janela_s, limite):
    agora = time.time()
    with trava:
        j = tabela.get(ip)
        if not j or agora - j[0] > janela_s:
            j = tabela[ip] = [agora, 0]
        j[1] += 1
        if len(tabela) > 50000:
            tabela.clear()
        return j[1] <= limite


def _estado_rede():
    with trava:
        agora = time.time()
        ativos = [m for m in mineradores.values() if agora - m["visto"] < MINERADOR_SUMIDO_S]
        emitido = db.execute("SELECT COALESCE(SUM(emissao), 0) FROM blocos").fetchone()[0]
        ult = db.execute("SELECT altura FROM blocos ORDER BY altura DESC LIMIT 1").fetchone()[0]
        blocos = [{"altura": a, "hash": h, "anterior": an, "quando": q, "emissao": fmt(e), "respostas": r,
                   "recompensas": len(json.loads(d).get("recompensas", {})), "transferencias": len(json.loads(d).get("transferencias", []))}
                  for a, h, an, q, e, r, d in db.execute("SELECT * FROM blocos ORDER BY altura DESC LIMIT 15")]
        ricos = [{"endereco": e, "saldo": fmt(s)} for e, s in
                 db.execute("SELECT endereco, saldo FROM saldos WHERE saldo > 0 ORDER BY saldo DESC LIMIT 10")]
        # todas as carteiras conhecidas: com saldo (mesmo zero), com conta no site ou minerando agora
        todas = {e: [s, False] for e, s in db.execute("SELECT endereco, saldo FROM saldos")}
        for (c,) in db.execute("SELECT carteira FROM contas"):
            todas.setdefault(c, [0, False])
        for m in ativos:
            todas.setdefault(m["carteira"], [0, False])[1] = True
        carteiras = [{"endereco": e, "saldo": fmt(v[0]), "minerando": v[1]}
                     for e, v in sorted(todas.items(), key=lambda kv: (-kv[1][0], kv[0]))][:300]
        por_tipo = {}
        for m in ativos:
            por_tipo[m["aparelho"]] = por_tipo.get(m["aparelho"], 0) + 1
        return {"nome": NOME, "simbolo": SIMBOLO, "altura": ult, "emitido": fmt(emitido), "oferta_max": fmt(OFERTA_MAX),
                "emissao_por_bloco": fmt(emissao_do_bloco(ult + 1)), "bloco_s": BLOCO_S,
                "proximo_bloco_s": round(max(0, inicio_bloco + BLOCO_S - agora)),
                "mineradores_online": len(ativos), "por_aparelho": por_tipo,
                "hps_total": round(sum(m["hps"] for m in ativos)),
                "respostas_no_bloco_atual": round(sum(rodada.values()), 2),
                "blocos": blocos, "maiores_saldos": ricos, "carteiras": carteiras, "total_carteiras": len(todas),
                "historico": historico[::max(1, len(historico) // 240)]}


# ---------------------------------------------------------------- explorador (estilo Duino)
# resumo da cadeia feito aos poucos: cada pedido só lê os blocos novos desde o último
expl = {"ate": -1, "dias": {}, "primeira": {}, "transf": [], "total_transf": 0, "inicio": time.time()}


def _dia(q):
    return time.strftime("%Y-%m-%d", time.localtime(q))


def _expl_atualizar():
    for altura, quando, dados in db.execute("SELECT altura, quando, dados FROM blocos WHERE altura > ? ORDER BY altura", (expl["ate"],)):
        d = json.loads(dados)
        dia = expl["dias"].setdefault(_dia(quando), {"blocos": 0, "emitido": 0.0, "novas": 0, "transf": 0, "mineradoras": set()})
        dia["blocos"] += 1
        for end, v in d.get("recompensas", {}).items():
            dia["emitido"] += v
            dia["mineradoras"].add(end)
            if end not in expl["primeira"]:
                expl["primeira"][end] = quando; dia["novas"] += 1
        for t in d.get("transferencias", []):
            dia["transf"] += 1; expl["total_transf"] += 1
            expl["transf"].append({"bloco": altura, "quando": t["quando"], "de": t["de"], "para": t["para"], "valor": t["valor"]})
            if t["para"] not in expl["primeira"]:
                expl["primeira"][t["para"]] = quando; dia["novas"] += 1
        expl["ate"] = altura
    expl["transf"] = expl["transf"][-30:]


def _carga_pc():
    try:
        with open("/proc/loadavg") as fh:
            carga = float(fh.read().split()[0])
        cpu = min(100, round(100 * carga / (os.cpu_count() or 1)))
        info = {}
        with open("/proc/meminfo") as fh:
            for linha in fh:
                k, v = linha.split(":")
                info[k] = int(v.split()[0])
        ram = round(100 * (1 - info["MemAvailable"] / info["MemTotal"]))
        return cpu, ram
    except Exception:
        return None, None


def explorador():
    with trava:
        _expl_atualizar()
        agora = time.time()
        ativos = [m for m in mineradores.values() if agora - m["visto"] < MINERADOR_SUMIDO_S]
        # placas agrupadas por carteira: sem nome, sem IP (ninguem ve onde fica a placa de outra pessoa)
        grupos = {}
        for m in ativos:
            g = grupos.setdefault(m["carteira"], {"carteira": m["carteira"], "placas": [], "hps": 0.0})
            g["placas"].append({"aparelho": m["aparelho"], "hps": round(m["hps"]), "online_s": round(agora - m["desde"]),
                                "aceitas": m["aceitas"]})
            g["hps"] += m["hps"]
        placas = sorted(grupos.values(), key=lambda g: -g["hps"])
        for g in placas:
            g["hps"] = round(g["hps"])
            g["placas"].sort(key=lambda x: -x["hps"])
        por_tipo = {"esp32": 0, "celular": 0, "pc": 0}
        for m in ativos:
            por_tipo[m["aparelho"]] = por_tipo.get(m["aparelho"], 0) + 1
        saldos = db.execute("SELECT endereco, saldo FROM saldos WHERE saldo > 0 ORDER BY saldo DESC").fetchall()
        total = sum(s for _, s in saldos) or 1
        distrib = [{"endereco": e, "saldo": fmt(s), "parte": round(s / total, 4)} for e, s in saldos[:6]]
        resto = sum(s for _, s in saldos[6:])
        if resto:
            distrib.append({"endereco": "outras", "saldo": fmt(resto), "parte": round(resto / total, 4), "quantas": len(saldos) - 6})
        dias = sorted(expl["dias"].items())[-14:]
        ult = db.execute("SELECT altura, hash, quando, emissao, dados FROM blocos ORDER BY altura DESC LIMIT 8").fetchall()
        topo = ult[0][0]
        emitido = db.execute("SELECT COALESCE(SUM(emissao), 0) FROM blocos").fetchone()[0]
        contas = db.execute("SELECT COUNT(*) FROM contas").fetchone()[0]
        todas = {e: v for e, v in db.execute("SELECT endereco, saldo FROM saldos")}
        for (c,) in db.execute("SELECT carteira FROM contas"):
            todas.setdefault(c, 0)
        minerando = {m["carteira"] for m in ativos}
        for c in minerando:
            todas.setdefault(c, 0)
        lista = [{"endereco": e, "saldo": fmt(v), "minerando": e in minerando, "desde": expl["primeira"].get(e)}
                 for e, v in sorted(todas.items(), key=lambda kv: (-kv[1], kv[0]))][:300]
    cpu, ram = _carga_pc()
    return {"altura": topo, "emitido": fmt(emitido), "oferta_max": fmt(OFERTA_MAX), "hps_total": round(sum(m["hps"] for m in ativos)),
            "mineradores_online": len(ativos), "carteiras": len(expl["primeira"]), "carteiras_com_saldo": len(saldos),
            "contas": contas, "transferencias": expl["total_transf"], "bloco_s": BLOCO_S,
            "proximo_bloco_s": round(max(0, inicio_bloco + BLOCO_S - agora)),
            "por_aparelho": por_tipo, "placas": placas[:100], "distribuicao": distrib,
            "diario": [{"dia": k, "blocos": v["blocos"], "emitido": round(v["emitido"], 2), "novas": v["novas"],
                        "transferencias": v["transf"], "mineradoras": len(v["mineradoras"])} for k, v in dias],
            "historico": historico[::max(1, len(historico) // 120)],
            "ultimas_transferencias": expl["transf"][::-1][:10], "lista_carteiras": lista,
            "ultimos_blocos": [{"altura": a, "hash": h, "quando": q, "emissao": fmt(e),
                                "recompensas": len(json.loads(d).get("recompensas", {})),
                                "transferencias": len(json.loads(d).get("transferencias", []))} for a, h, q, e, d in ult],
            "servidor": {"cpu": cpu, "ram": ram, "clientes": len(ativos), "no_ar_s": round(agora - expl["inicio"]),
                         "link_publico": bool(link_de_arquivo(PORTGIGA_LINK_ARQ))}}


def bloco(ident):
    """Um bloco pela altura ou pelo hash (busca do explorador)."""
    with trava:
        if re.fullmatch(r"\d{1,9}", ident):
            r = db.execute("SELECT altura, hash, anterior, quando, emissao, dados FROM blocos WHERE altura=?", (int(ident),)).fetchone()
        elif re.fullmatch(r"[0-9a-f]{64}", ident):
            r = db.execute("SELECT altura, hash, anterior, quando, emissao, dados FROM blocos WHERE hash=?", (ident,)).fetchone()
        else:
            return {"erro": "procure um número de bloco, um hash ou uma carteira ptg1..."}
    if not r:
        return {"erro": "bloco não encontrado"}
    return {"altura": r[0], "hash": r[1], "anterior": r[2], "quando": r[3], "emissao": fmt(r[4]), "dados": json.loads(r[5])}


# ---------------------------------------------------------------- rede independente
def migrar(a):
    carteira, novo, quando, pub = a.get("carteira"), a.get("endereco_novo"), a.get("quando"), a.get("pub")
    if not (endereco_valido(carteira) and isinstance(quando, int) and eh_hex(pub, 64) and eh_hex(a.get("assinatura"), 128)):
        return {"ok": False, "erro": "pedido invalido"}
    if not derivacao.endereco_valido(novo):
        return {"ok": False, "erro": "endereco novo invalido (precisa ser ptg1q...)"}
    if abs(time.time() - quando) > ASSINATURA_VALE_S:
        return {"ok": False, "erro": "pedido vencido: confira o relogio do aparelho"}
    mensagem = "portgiga-migrar:" + json.dumps([carteira, novo, quando], separators=(",", ":"))
    with trava:
        if MIGRACAO_CONGELADA_EM_ALTURA is not None:
            topo = db.execute("SELECT MAX(altura) FROM blocos").fetchone()[0]
            if topo >= MIGRACAO_CONGELADA_EM_ALTURA:
                return {"ok": False, "erro": "registro encerrado: a rede nova ja foi lancada"}
        if not pub_manda_em(carteira, pub) or not assinatura_ok(pub, mensagem, a["assinatura"]):
            return {"ok": False, "erro": "assinatura invalida para esta carteira"}
        db.execute("INSERT INTO migracoes VALUES (?, ?, ?, ?) ON CONFLICT(carteira) DO UPDATE SET endereco_novo=excluded.endereco_novo, "
                   "pub=excluded.pub, quando=excluded.quando", (carteira, novo, pub, time.time()))
        db.commit()
    return {"ok": True}


def migracao_de(carteira):
    if not endereco_valido(carteira):
        return {"erro": "endereco invalido"}
    with trava:
        r = db.execute("SELECT endereco_novo, quando FROM migracoes WHERE carteira=?", (carteira,)).fetchone()
        topo = db.execute("SELECT MAX(altura) FROM blocos").fetchone()[0]
    congela = MIGRACAO_CONGELADA_EM_ALTURA
    # encerra só quando a rede chega na altura marcada (marcar antes serve para anunciar a data)
    return {"carteira": carteira, "endereco_novo": r[0] if r else None, "quando": r[1] if r else None,
            "congela_em": congela, "encerrado": congela is not None and topo >= congela}


# ---------------------------------------------------------------- contas
def _hash_senha(derivada, sal):
    return hashlib.scrypt(derivada.encode(), salt=bytes.fromhex(sal), n=2 ** 14, r=8, p=1, dklen=32).hex()


def cadastrar(a):
    usuario, senha, carteira, quando, pub = a.get("usuario"), a.get("senha"), a.get("carteira"), a.get("quando"), a.get("pub")
    cifrada = a.get("chave_cifrada")
    if not (isinstance(usuario, str) and USUARIO_OK.match(usuario)):
        return {"ok": False, "erro": "usuario: 3 a 20 letras, numeros, ponto, traco ou _"}
    if not (eh_hex(senha, 64) and endereco_valido(carteira) and isinstance(quando, int) and eh_hex(pub, 64)
            and eh_hex(a.get("assinatura"), 128) and isinstance(cifrada, str) and len(cifrada) < 1000):
        return {"ok": False, "erro": "pedido invalido"}
    try:
        c = json.loads(cifrada)
        assert set(c) >= {"sal", "iv", "dados"} and all(isinstance(c[k], str) for k in ("sal", "iv", "dados"))
    except Exception:
        return {"ok": False, "erro": "chave cifrada invalida"}
    if abs(time.time() - quando) > ASSINATURA_VALE_S:
        return {"ok": False, "erro": "pedido vencido: confira o relogio do aparelho"}
    # a assinatura prova que quem cadastra tem a chave da carteira
    mensagem = "portgiga-cadastro:" + json.dumps([usuario, carteira, quando], separators=(",", ":"), ensure_ascii=False)
    with trava:
        if not pub_manda_em(carteira, pub) or not assinatura_ok(pub, mensagem, a["assinatura"]):
            return {"ok": False, "erro": "assinatura invalida para esta carteira"}
        if db.execute("SELECT 1 FROM contas WHERE usuario=?", (usuario,)).fetchone():
            return {"ok": False, "erro": "este usuario ja existe"}
        if db.execute("SELECT 1 FROM contas WHERE carteira=?", (carteira,)).fetchone():
            return {"ok": False, "erro": "esta carteira ja tem uma conta: entre com ela"}
        sal = secrets.token_hex(16)
        cur = db.execute("INSERT INTO contas (usuario, senha, sal, carteira, chave_cifrada, criada) VALUES (?, ?, ?, ?, ?, ?)",
                         (usuario, _hash_senha(senha, sal), sal, carteira, cifrada, time.time()))
        db.commit()
        return {"ok": True, **_nova_sessao(cur.lastrowid)}


def _nova_sessao(conta):
    token = secrets.token_urlsafe(32)
    agora = time.time()
    db.execute("DELETE FROM sessoes WHERE expira < ?", (agora,))
    db.execute("INSERT INTO sessoes VALUES (?, ?, ?, ?)", (hashlib.sha256(token.encode()).hexdigest(), conta, agora, agora + SESSAO_S))
    db.commit()
    u, cart, cif = db.execute("SELECT usuario, carteira, chave_cifrada FROM contas WHERE id=?", (conta,)).fetchone()
    return {"token": token, "usuario": u, "carteira": cart, "chave_cifrada": cif}


def entrar(a):
    usuario, senha = a.get("usuario"), a.get("senha")
    if not (isinstance(usuario, str) and USUARIO_OK.match(usuario) and eh_hex(senha, 64)):
        return {"ok": False, "erro": "usuario ou senha incorretos"}
    with trava:
        r = db.execute("SELECT id, senha, sal FROM contas WHERE usuario=?", (usuario,)).fetchone()
        if not r or not secrets.compare_digest(_hash_senha(senha, r[2]), r[1]):
            return {"ok": False, "erro": "usuario ou senha incorretos"}
        return {"ok": True, **_nova_sessao(r[0])}


def conta_da_sessao(token):
    if not token:
        return None
    with trava:
        r = db.execute("SELECT c.id, c.usuario, c.carteira, c.chave_cifrada FROM sessoes s JOIN contas c ON c.id = s.conta "
                       "WHERE s.token=? AND s.expira > ?", (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
    return {"id": r[0], "usuario": r[1], "carteira": r[2], "chave_cifrada": r[3]} if r else None


def sair(token):
    if token:
        with trava:
            db.execute("DELETE FROM sessoes WHERE token=?", (hashlib.sha256(token.encode()).hexdigest(),))
            db.commit()
    return {"ok": True}


def minhas_placas(carteira):
    """So para o dono logado: todos os detalhes das placas da carteira dele (IP, prova de trabalho...)"""
    with trava:
        agora = time.time()
        return {"carteira": carteira, "mineradores": sorted(
            [{"id": m["id"], "nome": m["nome"], "carteira": m["carteira"], "aparelho": m["aparelho"],
              "hps": round(m["hps"]), "aceitas": m["aceitas"], "rejeitadas": m["rejeitadas"],
              "ganho": round(m["ganho"], 4), "desde": m["desde"], "visto": m["visto"],
              "ip": m["ip"], "prova": m.get("prova"), "online": agora - m["visto"] < MINERADOR_SUMIDO_S}
             for m in mineradores.values() if m["carteira"] == carteira and agora - m["visto"] < 24 * 3600],
            key=lambda x: (not x["online"], -x["aceitas"]))}


# ---------------------------------------------------------------- HTTP
class Manipulador(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    timeout = TEMPO_CONEXAO_S

    def end_headers(self):
        # protecao: nenhum outro site pode abrir estas paginas escondidas dentro dele (clickjacking)
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", getattr(self, "_csp", None) or "frame-ancestors 'none'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(self), microphone=(), geolocation=(), payment=(), usb=()")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        if self.headers.get("CF-Connecting-IP"):     # pelo túnel é sempre https: o navegador passa a recusar http
            self.send_header("Strict-Transport-Security", "max-age=31536000")
        super().end_headers()

    def _privado(self):
        """Rotas com dado de dono/conta: nenhum outro site pode ler a resposta (sem CORS aberto)."""
        p = urlparse(self.path).path
        return "/dono" in p or p in ("/api/eu", "/api/minhas-placas")

    def _resp(self, corpo, tipo="application/json", codigo=200, cookie=None):
        b = (corpo if isinstance(corpo, str) else json.dumps(corpo)).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", tipo + "; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        if not self._privado():
            self.send_header("Access-Control-Allow-Origin", "*")
        self._csp = CSP_PAGINAS if tipo == "text/html" else None
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(b)

    def _args(self):
        u = urlparse(self.path)
        a = {k: v[0] for k, v in parse_qs(u.query).items()}
        if self.command == "POST":
            n = int(self.headers.get("Content-Length") or 0)
            if 0 < n < 10000:
                try:
                    a.update(json.loads(self.rfile.read(n).decode("utf-8")))
                except Exception:
                    pass
        return u.path, a

    def _token(self):
        for parte in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = parte.strip().partition("=")
            if k == "ptg_sessao":
                return v
        return None

    def _json(self):
        # so aceita JSON: outro site nao consegue mandar esse pedido com o cookie de quem visita (CSRF)
        return (self.headers.get("Content-Type") or "").split(";")[0].strip() == "application/json"

    def _seguro(self):
        # pelo tunel (https) o cookie so anda criptografado; no PC/rede local (http) nao da
        return "; Secure" if self.headers.get("CF-Connecting-IP") or self.headers.get("X-Forwarded-Proto") == "https" else ""

    def _com_sessao(self, r):
        if not r.get("ok"):
            return self._resp(r, codigo=400)
        token = r.pop("token")
        return self._resp(r, cookie="ptg_sessao=%s; Path=/; Max-Age=%d; HttpOnly; SameSite=Lax%s" % (token, SESSAO_S, self._seguro()))

    def _app(self, resto):
        """Arquivos do app da carteira: lista fechada (nada de caminhos com .. ou arquivos fora de servidor/app/)."""
        nome = resto or "index.html"
        permitido = nome in APP_ARQUIVOS or re.fullmatch(r"icones/[a-z0-9-]+\.(png|svg)", nome)
        arq = os.path.join(APP_PASTA, nome)
        if not permitido or not os.path.isfile(arq):
            return self._resp({"erro": "nao encontrado"}, codigo=404)
        with open(arq, "rb") as fh:
            b = fh.read()
        tipo = APP_TIPOS[os.path.splitext(nome)[1]]
        self._csp = CSP_PAGINAS if tipo == "text/html" else None
        self.send_response(200)
        self.send_header("Content-Type", tipo + ("; charset=utf-8" if tipo.startswith(("text/", "application/")) else ""))
        self.send_header("Content-Length", str(len(b)))
        if nome in ("sw.js", "index.html"):
            self.send_header("Cache-Control", "no-cache")
        if nome == "sw.js":
            self.send_header("Service-Worker-Allowed", "/app/")
        if nome.endswith(".apk"):
            self.send_header("Content-Disposition", 'attachment; filename="Portgiga-Carteira.apk"')
        self.end_headers()
        self.wfile.write(b)

    def _arena(self, do_pc):
        """Leva para a Arena Fichas: no PC, o endereço local; de fora, o link público atual do túnel da Arena."""
        destino = ARENA_LOCAL if do_pc else link_arena()
        if not destino:
            return self._resp("<p style='font-family:sans-serif'>A Arena Fichas está fora do ar agora. Tente de novo mais tarde.</p>",
                              "text/html", codigo=503)
        self.send_response(302)
        self.send_header("Location", destino)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        self._rota()

    def do_POST(self):
        self._rota()

    def _rota(self):
        self._csp = None
        host = (self.headers.get("Host") or "").strip().lower()
        if not HOST_OK.match(host):
            self.close_connection = True
            return self._resp({"erro": "servidor desconhecido"}, codigo=421)
        caminho, a = self._args()
        ip = self.client_address[0]
        # pedido feito no proprio PC (o tunel tambem chega por 127.0.0.1, mas sempre com estes cabecalhos).
        # Além disso o endereço digitado tem que ser localhost (contra DNS rebinding) e o pedido não pode vir
        # de outro site aberto no navegador do PC (o navegador avisa isso no Sec-Fetch-Site).
        do_pc = (ip in ("127.0.0.1", "::1") and not self.headers.get("CF-Connecting-IP") and not self.headers.get("X-Forwarded-For")
                 and bool(HOST_PC.match(host)) and self.headers.get("Sec-Fetch-Site", "none") in ("none", "same-origin"))
        if ip in ("127.0.0.1", "::1"):
            # atras do tunel/proxy o pedido chega de 127.0.0.1: o IP real vem no cabecalho
            ip = (self.headers.get("CF-Connecting-IP") or self.headers.get("X-Forwarded-For", "").split(",")[0]).strip() or ip
        if not dentro_do_limite(contagem_ip, ip, 60, LIMITE_PEDIDOS_MIN):
            return self._resp({"erro": "muitos pedidos, espere um minuto"}, codigo=429)
        if caminho.startswith("/u/"):
            import social
            pag = social.pagina_publica(caminho[3:].strip("/")[:20]) if re.fullmatch(r"/u/[a-z0-9_.]{3,20}/?", caminho) else None
            return self._resp(pag, "text/html") if pag else self._resp("<p style='font-family:sans-serif'>Perfil não encontrado na PTG Social.</p>", "text/html", codigo=404)
        if (caminho.startswith("/corretora") or caminho.startswith("/api/cx/")) and corretora.rota(self, caminho, a, ip, do_pc):
            return
        if caminho in ("/", "/index.html", "/minerar", "/carteira", "/entrar", "/cadastro"):
            with open(os.path.join(PASTA, "pagina.html"), encoding="utf-8") as fh:
                return self._resp(fh.read(), "text/html")
        if caminho == "/vendor/ptg-endereco.js":
            with open(os.path.join(PASTA, "vendor", "ptg-endereco.js"), encoding="utf-8") as fh:
                return self._resp(fh.read(), "text/javascript")
        if caminho == "/baixar":
            # o minerador da ESP32 so sai para quem tem conta (e com limite por conta)
            c = conta_da_sessao(self._token())
            if not c:
                self.send_response(302)
                self.send_header("Location", "/?entrar=baixar")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if not dentro_do_limite(downloads_conta, str(c["id"]), 3600, LIMITE_DOWNLOADS_HORA):
                return self._resp({"erro": "muitos downloads, espere uma hora"}, codigo=429)
            print("download do minerador: %s (%s)" % (c["usuario"], ip), flush=True)
        if caminho in ("/baixar", "/como-instalar", "/verificar.py"):
            arq, tipo, nome = {
                "/baixar": ("../Portgiga-Minerador-ESP32.zip", "application/zip", "Portgiga-Minerador-ESP32.zip"),
                "/como-instalar": ("../distribuir/COMO-INSTALAR.html", "text/html; charset=utf-8", None),
                "/verificar.py": ("verificar.py", "text/x-python; charset=utf-8", "verificar.py")}[caminho]
            with open(os.path.join(PASTA, arq), "rb") as fh:
                b = fh.read()
            self.send_response(200)
            self.send_header("Content-Type", tipo)
            if nome:
                self.send_header("Content-Disposition", 'attachment; filename="%s"' % nome)
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
            return
        if caminho == "/app":
            self.send_response(301)
            self.send_header("Location", "/app/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if caminho.startswith("/app/"):
            return self._app(caminho[len("/app/"):])
        if caminho == "/api/servidor-publico":
            return self._resp({"url": link_de_arquivo(PORTGIGA_LINK_ARQ)})
        if caminho == "/arena":
            return self._arena(do_pc)
        if caminho == "/api/trabalho":
            return self._resp(novo_trabalho(a.get("carteira", ""), a.get("aparelho", ""), a.get("id", ""), ip, a.get("nome", "")))
        if caminho == "/api/resultado":
            return self._resp(entregar(a.get("trabalho", ""), a.get("nonce", "")))
        if caminho in ("/explorador", "/explorer"):
            with open(os.path.join(PASTA, "explorador.html"), encoding="utf-8") as fh:
                return self._resp(fh.read(), "text/html")
        if caminho == "/api/explorador":
            return self._resp(explorador())
        if caminho == "/api/bloco":
            return self._resp(bloco(a.get("id", "").strip().lower()))
        if caminho == "/api/estado":
            return self._resp(estado_rede())
        if caminho == "/api/carteira":
            return self._resp(info_carteira(a.get("endereco", "")))
        if caminho == "/api/cadeia":
            try:
                desde, ate = int(a.get("desde", 0)), (int(a["ate"]) if "ate" in a else None)
            except ValueError:
                return self._resp({"erro": "desde/ate invalidos"}, codigo=400)
            return self._resp(cadeia(desde, ate))
        if caminho == "/api/renomear" and self.command == "POST":
            return self._resp(renomear(a))
        if caminho == "/api/enviar" and self.command == "POST":
            return self._resp(enviar(a))
        if caminho == "/api/chegada" and self.command == "POST":
            return self._resp(boasvindas.registrar(a, ip))
        if caminho == "/api/boas-vindas":
            if not endereco_valido(a.get("endereco")):
                return self._resp({"ok": False, "erro": "carteira invalida"}, codigo=400)
            return self._resp(boasvindas.situacao(a["endereco"]))
        if caminho in ("/dono/usuarios", "/api/dono/usuarios"):
            if not do_pc:
                return self._resp({"erro": "so no PC do servidor"}, codigo=403)
            if caminho == "/api/dono/usuarios":
                return self._resp(boasvindas.painel())
            with open(os.path.join(PASTA, "dono_usuarios.html"), encoding="utf-8") as fh:
                return self._resp(fh.read(), "text/html")
        if caminho == "/api/mural":
            return self._resp(mural())
        if caminho == "/api/anunciar" and self.command == "POST":
            if not dentro_do_limite(ofertas_ip, ip, 3600, LIMITE_OFERTAS_HORA):
                return self._resp({"ok": False, "erro": "muitos anuncios, espere uma hora"}, codigo=429)
            return self._resp(anunciar(a))
        if caminho == "/api/encerrar" and self.command == "POST":
            return self._resp(encerrar_oferta(a))
        if caminho == "/api/cadastro" and self.command == "POST":
            if not self._json():
                return self._resp({"ok": False, "erro": "pedido invalido"}, codigo=400)
            if not dentro_do_limite(cadastros_ip, ip, 3600, LIMITE_CADASTROS_HORA):
                return self._resp({"ok": False, "erro": "muitos cadastros, espere uma hora"}, codigo=429)
            return self._com_sessao(cadastrar(a))
        if caminho == "/api/entrar" and self.command == "POST":
            if not self._json():
                return self._resp({"ok": False, "erro": "pedido invalido"}, codigo=400)
            j = login_ip.get(ip)
            if j and time.time() - j[0] < 900 and j[1] >= LIMITE_LOGIN_ERRADO:
                return self._resp({"ok": False, "erro": "muitas senhas erradas, espere 15 minutos"}, codigo=429)
            quem = str(a.get("usuario", ""))[:20].lower()
            jc = login_conta.get(quem)
            if jc and time.time() - jc[0] < 3600 and jc[1] >= LIMITE_LOGIN_ERRADO_CONTA:
                return self._resp({"ok": False, "erro": "muitas senhas erradas nesta conta, espere 1 hora"}, codigo=429)
            r = entrar(a)
            if not r["ok"]:
                dentro_do_limite(login_ip, ip, 900, LIMITE_LOGIN_ERRADO)
                dentro_do_limite(login_conta, quem, 3600, LIMITE_LOGIN_ERRADO_CONTA)
            return self._com_sessao(r)
        if caminho == "/api/sair" and self.command == "POST":
            if not self._json():
                return self._resp({"ok": False, "erro": "pedido invalido"}, codigo=400)
            r = sair(self._token())
            return self._resp(r, cookie="ptg_sessao=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax" + self._seguro())
        if caminho == "/api/eu":
            c = conta_da_sessao(self._token())
            return self._resp({"logado": False} if not c else {"logado": True, "usuario": c["usuario"], "carteira": c["carteira"], "chave_cifrada": c["chave_cifrada"]})
        if caminho == "/api/minhas-placas":
            c = conta_da_sessao(self._token())
            if not c:
                return self._resp({"erro": "entre na sua conta para ver suas placas"}, codigo=401)
            return self._resp(minhas_placas(c["carteira"]))
        if caminho == "/api/migrar" and self.command == "POST":
            if not self._json():
                return self._resp({"ok": False, "erro": "pedido invalido"}, codigo=400)
            return self._resp(migrar(a))
        if caminho == "/api/migracao":
            return self._resp(migracao_de(a.get("endereco", "")))
        if caminho == "/api/vincular" and self.command == "POST":
            if not do_pc:
                return self._resp({"ok": False, "erro": "a ativacao de carteira antiga so pode ser feita no PC do servidor"}, codigo=403)
            return self._resp(vincular(a.get("chave")))
        self._resp({"erro": "nao encontrado"}, codigo=404)


class Servidor(ThreadingHTTPServer):
    """Atende no máximo MAX_CONEXOES pedidos ao mesmo tempo (protege memória e CPU de uma enxurrada)."""
    daemon_threads = True
    request_queue_size = 128

    def __init__(self, *a, **k):
        self._vagas = threading.BoundedSemaphore(MAX_CONEXOES)
        super().__init__(*a, **k)

    def process_request(self, request, client_address):
        if not self._vagas.acquire(timeout=5):
            try:
                request.close()
            except OSError:
                pass
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._vagas.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._vagas.release()


def main():
    os.umask(0o077)                     # arquivos novos (banco, fotos, logs) só o dono do PC lê
    abrir_banco()
    threading.Thread(target=produtor_de_blocos, daemon=True).start()
    threading.Thread(target=registrar_historico, daemon=True).start()
    threading.Thread(target=corretora.vigiar_depositos, daemon=True).start()
    srv = Servidor(("0.0.0.0", PORTA), Manipulador)
    print("Rede %s (%s) no ar: http://0.0.0.0:%d/" % (NOME, SIMBOLO, PORTA), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
