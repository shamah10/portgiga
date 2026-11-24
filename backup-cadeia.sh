#!/usr/bin/env bash
# Cópia de segurança da Portgiga: cadeia (servidor/dados/rede.db, feita com o servidor ligado), fotos da Feira/Social
# e a chave da carteira da corretora
# (backup online do SQLite: a cópia sai inteira e consistente). Guarda os últimos 30 dias.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
DESTINO="$HOME/projetos/backups/cadeia-portgiga"
mkdir -p "$DESTINO"; chmod 700 "$DESTINO"
ARQ="$DESTINO/rede-$(date +%Y%m%d-%H%M).db"
python3 -I - "$DIR/servidor/dados/rede.db" "$ARQ" <<'PY'
import sqlite3, sys
origem = sqlite3.connect("file:%s?mode=ro" % sys.argv[1], uri=True)
destino = sqlite3.connect(sys.argv[2])
origem.backup(destino)
ok = destino.execute("PRAGMA integrity_check").fetchone()[0]
altura = destino.execute("SELECT MAX(altura) FROM blocos").fetchone()[0]
destino.close(); origem.close()
if ok != "ok":
    sys.exit("cópia com defeito: " + ok)
print("cópia ok, bloco", altura)
PY
gzip -9 "$ARQ"; chmod 600 "$ARQ.gz"
ls -1t "$DESTINO"/rede-*.db.gz | tail -n +31 | xargs -r rm -f   # fica com as 30 mais novas
echo "salvo em $ARQ.gz"

# fotos da Feira e da PTG Social (não ficam no banco): um pacote por dia, guarda os 14 mais novos
FOTOS="$DESTINO/fotos-$(date +%Y%m%d-%H%M).tar.gz"
PASTAS=(); for p in capas social; do [ -d "$DIR/servidor/dados/$p" ] && PASTAS+=("$p"); done
if [ ${#PASTAS[@]} -gt 0 ]; then
  tar -czf "$FOTOS" --exclude="*.mp4" --exclude="tmp_v*" -C "$DIR/servidor/dados" "${PASTAS[@]}" && chmod 600 "$FOTOS" && tar -tzf "$FOTOS" >/dev/null
  ls -1t "$DESTINO"/fotos-*.tar.gz 2>/dev/null | tail -n +15 | xargs -r rm -f
  echo "fotos salvas em $FOTOS ($(tar -tzf "$FOTOS" | grep -c '\.jpg$') imagens)"
fi

# chave da carteira da corretora (guarda os PTG depositados): cópia só quando mudar
if [ -f "$DIR/corretora_carteira.json" ] && ! cmp -s "$DIR/corretora_carteira.json" "$DESTINO/corretora_carteira.json" 2>/dev/null; then
  install -m 600 "$DIR/corretora_carteira.json" "$DESTINO/corretora_carteira.json"; echo "chave da corretora copiada"
fi
