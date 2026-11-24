#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VERIFICADOR DA REDE PORTGIGA (PTG) - confira a rede voce mesmo, sem confiar no servidor.

Uso:  python3 verificar.py [endereco do servidor]      (padrao: http://127.0.0.1:7777)

Baixa a corrente de blocos inteira e refaz tudo no seu computador:
- cada bloco aponta para o hash do anterior e o hash dele confere (nenhum bloco foi alterado);
- nunca nasceu mais PTG do que a regra (10 por bloco, metade a cada 1.051.200 blocos, maximo 21.024.000);
- toda transferencia tem a assinatura do dono da carteira, na ordem certa, e ninguem gastou o que nao tinha;
- os saldos que o servidor mostra sao os mesmos que saem da corrente;
- a historia nao foi reescrita desde a ultima vez que voce conferiu (guarda marcos em ~/.portgiga-verificador.json).

O que ele NAO prova: quem ganha cada bloco ainda e decidido pelo servidor (a rede ainda e centralizada).
So usa a biblioteca padrao do Python 3.
"""
import hashlib
import json
import os
import sys
import urllib.request

UNIDADE = 100_000_000
EMISSAO_INICIAL = 10 * UNIDADE
BLOCOS_POR_METADE = 1_051_200
OFERTA_MAX = 2 * EMISSAO_INICIAL * BLOCOS_POR_METADE
ALTURA_LEGADO = 130   # carteiras com recompensa ate este bloco sao do tipo antigo (podem ter um vinculo de chave)
MARCOS = os.path.expanduser("~/.portgiga-verificador.json")

# ------------------------------------------------- Ed25519 (RFC 8032), so a parte de conferir assinatura
P = 2 ** 255 - 19
Q = 2 ** 252 + 27742317777372353535851937790883648493
D = -121665 * pow(121666, P - 2, P) % P
RAIZ_M1 = pow(2, (P - 1) // 4, P)


def _soma(a, b):
    x = (a[1] - a[0]) * (b[1] - b[0]) % P
    y = (a[1] + a[0]) * (b[1] + b[0]) % P
    z = 2 * a[3] * b[3] * D % P
    w = 2 * a[2] * b[2] % P
    e, f, g, h = y - x, w - z, w + z, y + x
    return (e * f % P, g * h % P, f * g % P, e * h % P)


def _vezes(s, a):
    r = (0, 1, 1, 0)
    while s:
        if s & 1:
            r = _soma(r, a)
        a = _soma(a, a)
        s >>= 1
    return r


def _iguais(a, b):
    return (a[0] * b[2] - b[0] * a[2]) % P == 0 and (a[1] * b[2] - b[1] * a[2]) % P == 0


def _x(y, sinal):
    if y >= P:
        return None
    x2 = (y * y - 1) * pow(D * y * y + 1, P - 2, P)
    if x2 % P == 0:
        return None if sinal else 0
    x = pow(x2, (P + 3) // 8, P)
    if (x * x - x2) % P:
        x = x * RAIZ_M1 % P
    if (x * x - x2) % P:
        return None
    return P - x if (x & 1) != sinal else x


def _ponto(b):
    y = int.from_bytes(b, "little")
    x = _x(y & ((1 << 255) - 1), y >> 255)
    return None if x is None else (x, y & ((1 << 255) - 1), 1, x * (y & ((1 << 255) - 1)) % P)


_GY = 4 * pow(5, P - 2, P) % P
G = (_x(_GY, 0), _GY, 1, _x(_GY, 0) * _GY % P)


def assinatura_ok(pub_hex, mensagem, assinatura_hex):
    try:
        pub, sig = bytes.fromhex(pub_hex), bytes.fromhex(assinatura_hex)
    except (TypeError, ValueError):
        return False
    if len(pub) != 32 or len(sig) != 64:
        return False
    a, r = _ponto(pub), _ponto(sig[:32])
    s = int.from_bytes(sig[32:], "little")
    if a is None or r is None or s >= Q:
        return False
    h = int.from_bytes(hashlib.sha512(sig[:32] + pub + mensagem.encode()).digest(), "little") % Q
    return _iguais(_vezes(s, G), _soma(r, _vezes(h, a)))


# ------------------------------------------------- regras da rede
def sha(t):
    return hashlib.sha256(t.encode()).hexdigest()


def endereco_do_pub(pub):
    return "ptg1" + sha("ptg-pub:" + pub)[:38]


def emissao_do_bloco(altura):
    return EMISSAO_INICIAL >> ((altura - 1) // BLOCOS_POR_METADE)


def baixar(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    base = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:7777").rstrip("/")
    print("Baixando a corrente de", base, "...")
    blocos = []
    while True:
        d = baixar("%s/api/cadeia?desde=%d" % (base, len(blocos)))
        blocos += d["blocos"]
        if not d["blocos"] or blocos[-1]["altura"] >= d["altura_atual"]:
            # confere de novo o estado: se nasceu bloco enquanto baixava, busca o resto
            estado = baixar(base + "/api/estado")
            if estado["altura"] <= blocos[-1]["altura"]:
                break
    print("%d blocos baixados. Conferindo...\n" % len(blocos))

    problemas = []
    saldos, contadores, vinculos, legado = {}, {}, {}, set()
    emitido = transf = 0
    for i, b in enumerate(blocos):
        if b["altura"] != i:
            problemas.append("bloco fora de ordem na posicao %d" % i)
            break
        dados = b["dados"]
        if i == 0:
            if sha(json.dumps(dados, sort_keys=True)) != b["hash"] or b["anterior"] != "0" * 64:
                problemas.append("bloco inicial nao confere")
            continue
        if b["anterior"] != blocos[i - 1]["hash"]:
            problemas.append("bloco %d nao aponta para o bloco %d" % (i, i - 1))
        corpo = json.dumps({"altura": i, "anterior": b["anterior"], "quando": b["quando"], "dados": dados}, sort_keys=True)
        if sha(corpo) != b["hash"]:
            problemas.append("bloco %d foi alterado (hash nao confere)" % i)

        for v in dados.get("vinculos", []):
            e, pub = v["endereco"], v["pub"]
            if e not in legado or e in vinculos:
                problemas.append("bloco %d: vinculo invalido para %s" % (i, e))
                continue
            vinculos[e] = pub

        for t in dados.get("transferencias", []):
            de, para, u, n, pub = t.get("de"), t.get("para"), t.get("unidades"), t.get("nonce"), t.get("pub", "")
            msg = "portgiga-tx:" + json.dumps([de, para, u, n], separators=(",", ":"))
            erro = None
            if type(u) is not int or u <= 0 or de == para:
                erro = "valor ou destino invalido"
            elif endereco_do_pub(pub) != de and vinculos.get(de) != pub:
                erro = "chave que assinou nao e dona da carteira"
            elif not assinatura_ok(pub, msg, t.get("assinatura", "")):
                erro = "assinatura invalida"
            elif n != contadores.get(de, 0):
                erro = "numero de ordem errado (transferencia repetida?)"
            elif saldos.get(de, 0) < u:
                erro = "gastou mais do que tinha"
            if erro:
                problemas.append("bloco %d: transferencia de %s: %s" % (i, de, erro))
                continue
            saldos[de] = saldos.get(de, 0) - u
            saldos[para] = saldos.get(para, 0) + u
            contadores[de] = n + 1
            transf += 1

        recompensas = {e: int(round(v * UNIDADE)) for e, v in dados.get("recompensas", {}).items()}
        if sum(recompensas.values()) > emissao_do_bloco(i):
            problemas.append("bloco %d emitiu mais do que a regra permite" % i)
        emitido += sum(recompensas.values())
        for e, v in recompensas.items():
            saldos[e] = saldos.get(e, 0) + v
            if i <= ALTURA_LEGADO:
                legado.add(e)
    if emitido > OFERTA_MAX:
        problemas.append("emissao total passou do maximo")

    # o que o servidor mostra bate com o que sai da corrente?
    for r in estado.get("maiores_saldos", []):
        if int(round(r["saldo"] * UNIDADE)) != saldos.get(r["endereco"], 0):
            problemas.append("saldo mostrado de %s (%s) nao bate com a corrente (%s)"
                             % (r["endereco"], r["saldo"], saldos.get(r["endereco"], 0) / UNIDADE))

    # a historia foi reescrita desde a ultima conferencia?
    try:
        with open(MARCOS, encoding="utf-8") as fh:
            marcos = json.load(fh)
    except (OSError, ValueError):
        marcos = {}
    vistos = marcos.get(base, {})
    for altura, h in vistos.items():
        a = int(altura)
        if a < len(blocos) and blocos[a]["hash"] != h:
            problemas.append("ALERTA: o bloco %d mudou desde a ultima conferencia (historia reescrita)" % a)
        elif a >= len(blocos):
            problemas.append("ALERTA: a corrente encolheu (o bloco %d sumiu)" % a)
    novos = {str(a): blocos[a]["hash"] for a in list(range(0, len(blocos), 1000)) + [len(blocos) - 1]}
    marcos[base] = {**vistos, **novos}
    try:
        with open(MARCOS, "w", encoding="utf-8") as fh:
            json.dump(marcos, fh)
    except OSError:
        pass

    print("Blocos:          %d (ultimo %s...)" % (len(blocos) - 1, blocos[-1]["hash"][:16]))
    print("PTG emitidos:    %s de no maximo %s" % (emitido / UNIDADE, OFERTA_MAX / UNIDADE))
    print("Transferencias:  %d (todas com assinatura conferida)" % transf if not problemas else "Transferencias:  %d validas" % transf)
    print("Carteiras:       %d com saldo" % sum(1 for v in saldos.values() if v > 0))
    print()
    if problemas:
        print("PROBLEMAS ENCONTRADOS (%d):" % len(problemas))
        for p in problemas[:50]:
            print("  -", p)
        sys.exit(1)
    print("TUDO CONFERE. Lembrete: quem ganha cada bloco ainda e decidido pelo servidor (rede centralizada).")


if __name__ == "__main__":
    main()
