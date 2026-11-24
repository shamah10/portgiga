#!/usr/bin/env bash
# Abre a Carteira Portgiga (app do celular) numa janela própria no PC. Liga o servidor se estiver desligado.
if ! curl -s -o /dev/null --max-time 2 http://127.0.0.1:7777/api/estado; then
  systemctl --user start portgiga 2>/dev/null
  for _ in $(seq 1 20); do curl -s -o /dev/null --max-time 1 http://127.0.0.1:7777/api/estado && break; sleep 0.5; done
fi
exec brave-browser --app=http://127.0.0.1:7777/app/ --user-data-dir="$HOME/.local/share/portgiga/carteira" \
  --no-first-run --no-default-browser-check --class=portgiga-carteira --window-size=440,900
