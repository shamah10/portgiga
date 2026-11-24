#!/usr/bin/env bash
# Roda o túnel público da Portgiga (Cloudflare) para o serviço portgiga-tunel.service.
# Grava o link novo em ~/.local/share/portgiga/tunel/link.txt (o servidor e o app leem dali)
# e avisa a Arena Fichas. Se o túnel cair, o systemd liga de novo sozinho.
ESTADO="$HOME/.local/share/portgiga/tunel"
LOG="$ESTADO/tunel.log"; PID="$ESTADO/tunel.pid"; LINK="$ESTADO/link.txt"
CF="${CLOUDFLARED:-$HOME/.local/bin/cloudflared}"
ARENA_LINK="$HOME/projetos/plataforma-jogos/deploy/link-portgiga.sh"
mkdir -p "$ESTADO"

# espera o servidor responder antes de abrir o túnel
for _ in $(seq 1 60); do curl -s -m 2 -o /dev/null http://127.0.0.1:7777/api/estado && break; sleep 2; done

# domínio fixo (dominio-fixo.sh já rodou): túnel nomeado, link que nunca muda
FIXO_ARQ="$(dirname "$0")/.dominio-fixo"
if [ -s "$FIXO_ARQ" ] && [ -s "$HOME/.cloudflared/config.yml" ]; then
  FIXO="$(cat "$FIXO_ARQ")"
  : > "$LOG"
  "$CF" tunnel --no-autoupdate --config "$HOME/.cloudflared/config.yml" run >> "$LOG" 2>&1 &
  CFPID=$!
  echo "$CFPID" > "$PID"
  trap 'kill "$CFPID" 2>/dev/null; rm -f "$PID"' TERM INT
  echo "$FIXO" > "$LINK"
  ARENA_FIXO="https://arena.${FIXO#https://}"
  mkdir -p "$HOME/.local/share/arena-fichas/tunel" && echo "$ARENA_FIXO" > "$HOME/.local/share/arena-fichas/tunel/link.txt"
  grep -q "$FIXO" "$ESTADO/links.txt" 2>/dev/null || echo "$(date '+%F %T') link fixo: $FIXO" >> "$ESTADO/links.txt"
  ( sleep 20; [ -x "$ARENA_LINK" ] && "$ARENA_LINK" "$FIXO" >/dev/null 2>&1
    python3 "$(dirname "$0")/ferramentas/qr_carteira.py" "$FIXO" >/dev/null 2>&1 || true
    bash "$(dirname "$0")/pages/publicar.sh" >> "$ESTADO/pages.log" 2>&1 || true ) &   # + endereço fixo grátis (Cloudflare Pages)
  wait "$CFPID"
  exit $?
fi

: > "$LOG"
"$CF" tunnel --no-autoupdate --url http://localhost:7777 >> "$LOG" 2>&1 &
CFPID=$!
echo "$CFPID" > "$PID"
trap 'kill "$CFPID" 2>/dev/null; rm -f "$PID"' TERM INT

(
  url=""
  for _ in $(seq 1 90); do
    kill -0 "$CFPID" 2>/dev/null || exit 0   # o cloudflared desistiu (ex.: sem internet): o systemd tenta de novo
    url=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$LOG" | grep -v '^https://api\.' | head -1)   # api.* aparece nos erros
    [ -n "$url" ] && break; sleep 1
  done
  if [ -n "$url" ]; then
    echo "$url" > "$LINK"
    echo "$(date '+%F %T') link público: $url" >> "$ESTADO/links.txt"
    [ -x "$ARENA_LINK" ] && "$ARENA_LINK" "$url" >/dev/null 2>&1
    python3 "$(dirname "$0")/ferramentas/qr_carteira.py" "$url" >/dev/null 2>&1 || true   # QR da carteira na área de trabalho
    sleep 15; bash "$(dirname "$0")/pages/publicar.sh" >> "$ESTADO/pages.log" 2>&1 || true   # endereço fixo grátis (Cloudflare Pages)
  fi
) &

wait "$CFPID"
