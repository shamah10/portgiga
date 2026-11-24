#!/usr/bin/env bash
# Abre a Rede Portgiga: liga o servidor (se estiver desligado) e abre o painel numa janela propria.
PASTA="$(cd "$(dirname "$0")" && pwd)"
if ! curl -s -o /dev/null --max-time 2 http://127.0.0.1:7777/api/estado; then
  cd "$PASTA/servidor" && setsid nohup python3 servidor.py >> servidor.log 2>&1 < /dev/null &
  for _ in $(seq 1 20); do curl -s -o /dev/null --max-time 1 http://127.0.0.1:7777/api/estado && break; sleep 0.5; done
fi
exec brave-browser --app=http://127.0.0.1:7777/ --user-data-dir="$HOME/.local/share/portgiga/janela" \
  --no-first-run --no-default-browser-check --class=portgiga --window-size=1500,940
