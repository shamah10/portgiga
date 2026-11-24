#!/usr/bin/env bash
# Monta o endereço fixo grátis da Portgiga (Cloudflare Pages) em pages/dist:
#   /            página de links (inicio.html)
#   /app/        a Carteira (cópia de servidor/app, em modo "hospedado": o servidor é o túnel do dia)
#   /link.json   o link público de agora (Portgiga + Arena), lido pela página e pela Carteira
# Uso: bash pages/construir.sh   (depois: bash pages/publicar.sh)
set -euo pipefail
AQUI="$(cd "$(dirname "$0")" && pwd)"; RAIZ="$(dirname "$AQUI")"; DIST="$AQUI/dist"
LINK_PTG="$(cat "$HOME/.local/share/portgiga/tunel/link.txt" 2>/dev/null || true)"
LINK_ARENA="$(cat "$HOME/.local/share/arena-fichas/tunel/link.txt" 2>/dev/null || true)"
rm -rf "$DIST"; mkdir -p "$DIST"
cp "$AQUI/inicio.html" "$DIST/index.html"
cp -r "$RAIZ/servidor/app" "$DIST/app"
cp -r "$AQUI/midia" "$DIST/midia"   # vídeo da Vila Esperança + capa
# a Carteira hospedada fala com o túnel do dia (ou o domínio fixo, quando existir)
sed -i "s#connect-src 'self'\">#connect-src 'self' https://*.trycloudflare.com https://portgiga.com.br https://*.portgiga.com.br\">#" "$DIST/app/index.html"
grep -q 'trycloudflare.com https://portgiga' "$DIST/app/index.html" || { echo "CSP da carteira não foi ajustada"; exit 1; }
python3 - "$LINK_PTG" "$LINK_ARENA" "$DIST/link.json" <<'PY'
import json, sys, datetime
ptg, arena, saida = sys.argv[1:4]
json.dump({'portgiga': ptg.strip(), 'arena': arena.strip(),
           'atualizado': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')},
          open(saida, 'w'), ensure_ascii=False)
PY
cat > "$DIST/_headers" <<'H'
/link.json
  Access-Control-Allow-Origin: *
  Cache-Control: no-store
/app/sw.js
  Cache-Control: no-cache
/*
  X-Frame-Options: DENY
  X-Content-Type-Options: nosniff
  Referrer-Policy: strict-origin-when-cross-origin
H
echo "pages/dist pronto · portgiga=$LINK_PTG · arena=$LINK_ARENA"
