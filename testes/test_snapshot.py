#!/usr/bin/env python3
"""Ferramenta de snapshot: soma, trava do dono, datas das parcelas, carteiras sem registro e determinismo."""
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(AQUI, "..", "servidor"))
sys.path.insert(0, os.path.join(AQUI, "..", "ferramentas"))

import derivacao  # noqa: E402
import snapshot  # noqa: E402

U = 100_000_000
DONO, ANTIGA, PREMIOS = "ptg1" + "a" * 38, "ptg1" + "b" * 38, "ptg1" + "c" * 38
CH_DONO, CH_PREMIOS = "d1" * 32, "d2" * 32


def banco(caminho, saldos, migracoes, altura=6000):
    db = sqlite3.connect(caminho)
    db.executescript("CREATE TABLE saldos (endereco TEXT PRIMARY KEY, saldo INTEGER, minerado INTEGER);"
                     "CREATE TABLE migracoes (carteira TEXT PRIMARY KEY, endereco_novo TEXT, pub TEXT, quando REAL);"
                     "CREATE TABLE blocos (altura INTEGER PRIMARY KEY, hash TEXT, anterior TEXT, quando REAL, emissao INTEGER, respostas REAL, dados TEXT);")
    db.executemany("INSERT INTO saldos VALUES (?, ?, 0)", saldos)
    db.executemany("INSERT INTO migracoes VALUES (?, ?, 'x', 0)", migracoes)
    db.execute("INSERT INTO blocos VALUES (?, 'h', 'a', 0, 0, 0, '{}')", (altura,))
    db.commit()
    db.close()


class TestSnapshot(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "rede.db")
        self.novo_dono = derivacao.endereco_novo(CH_DONO)
        self.novo_premios = derivacao.endereco_novo(CH_PREMIOS)
        self.pub_dono = snapshot.pub_secp(CH_DONO)
        banco(self.db, [(DONO, 5271984656165), (ANTIGA, 53115343835), (PREMIOS, 999 * U)],
              [(DONO, self.novo_dono), (ANTIGA, self.novo_dono), (PREMIOS, self.novo_premios)])

    def gerar(self, **kw):
        args = dict(banco=self.db, altura=6000, lancamento="2026-12-01", rede="principal", dono=[DONO, ANTIGA], dono_pub=self.pub_dono)
        args.update(kw)
        return snapshot.gerar(**args)

    def test_soma_igual_aos_saldos(self):
        s = self.gerar()
        self.assertEqual(sum(x["valor"] for x in s["saidas"]), 5271984656165 + 53115343835 + 999 * U)

    def test_trava_do_dono(self):
        s = self.gerar()
        total = 5271984656165 + 53115343835
        livre = [x for x in s["saidas"] if x["tipo"] == "livre" and x["endereco"] == self.novo_dono]
        parcelas = [x for x in s["saidas"] if x["tipo"] == "parcela"]
        self.assertEqual(len(livre), 1)
        self.assertEqual(livre[0]["valor"], total * 10 // 100)
        self.assertEqual(len(parcelas), 48)
        travado = total - total * 10 // 100
        self.assertTrue(all(p["valor"] == travado // 48 for p in parcelas[:-1]))
        self.assertEqual(parcelas[-1]["valor"], travado - 47 * (travado // 48))
        self.assertEqual([p["parcela"] for p in parcelas], list(range(1, 49)))

    def test_datas_das_parcelas(self):
        s = self.gerar()
        p = [x for x in s["saidas"] if x["tipo"] == "parcela"]
        self.assertEqual(p[0]["desde"], 1798761600)   # 2027-01-01T00:00:00Z
        self.assertEqual(p[11]["desde"], 1827619200)  # 2027-12-01T00:00:00Z
        self.assertEqual(p[47]["desde"], 1922313600)  # 2030-12-01T00:00:00Z

    def test_scripts(self):
        s = self.gerar()
        livre = next(x for x in s["saidas"] if x["endereco"] == self.novo_premios)
        self.assertEqual(livre["script"], "0014" + derivacao.decodificar(self.novo_premios)[2].hex())
        p1 = next(x for x in s["saidas"] if x.get("parcela") == 1)
        ws = bytes.fromhex("21" + self.pub_dono) + b"\xad" + snapshot.push_numero(1798761600) + b"\xb1"
        self.assertEqual(p1["script"], "0020" + hashlib.sha256(ws).hexdigest())
        self.assertEqual(p1["descritor"], f"wsh(and_v(v:pk({self.pub_dono}),after(1798761600)))")

    def test_sem_registro_falha(self):
        banco(self.db + "2", [(DONO, 10 * U), ("ptg1" + "e" * 38, 5 * U)], [(DONO, self.novo_dono)])
        with self.assertRaises(snapshot.ErroSnapshot) as e:
            self.gerar(banco=self.db + "2", dono=[DONO])
        self.assertIn("ptg1" + "e" * 38, str(e.exception))

    def test_pub_do_dono_tem_que_bater(self):
        with self.assertRaises(snapshot.ErroSnapshot):
            self.gerar(dono_pub=snapshot.pub_secp(CH_PREMIOS))

    def test_altura_tem_que_ser_a_atual(self):
        with self.assertRaises(snapshot.ErroSnapshot):
            self.gerar(altura=5999)

    def test_sha256_e_determinismo(self):
        a, b = self.gerar(), self.gerar()
        self.assertEqual(a, b)
        semhash = {k: v for k, v in a.items() if k != "sha256"}
        self.assertEqual(a["sha256"], hashlib.sha256(json.dumps(semhash, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest())

    def test_linha_de_comando(self):
        saida = os.path.join(self.tmp, "s.json")
        r = subprocess.run([sys.executable, os.path.join(AQUI, "..", "ferramentas", "snapshot.py"), "--banco", self.db, "--altura", "6000",
                            "--lancamento", "2026-12-01", "--rede", "principal", "--dono", f"{DONO},{ANTIGA}", "--dono-pub", self.pub_dono,
                            "--saida", saida], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.load(open(saida))["sha256"], self.gerar()["sha256"])


if __name__ == "__main__":
    unittest.main()
