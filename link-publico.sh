#!/bin/bash
# Link público temporário da Portgiga pelo túnel rápido da Cloudflare (https://…trycloudflare.com).
#   ./link-publico.sh          abre o túnel (ou mostra o link se já estiver aberto)
#   ./link-publico.sh parar    fecha o túnel: o link para de funcionar
# O endereço muda toda vez que o túnel reinicia (e ao desligar o PC).
# As rotas de dono continuam só no próprio PC: o servidor recusa pedidos com cabeçalhos do túnel.
DIR="$(cd "$(dirname "$0")" && pwd)"
ESTADO="$HOME/.local/share/portgiga/tunel"
LOG="$ESTADO/tunel.log"; PID="$ESTADO/tunel.pid"; LINK="$ESTADO/link.txt"
CF="${CLOUDFLARED:-$HOME/.local/bin/cloudflared}"
# Se a Arena Fichas existir, o cartão "Minere Portgiga" do lobby dela acompanha o link
ARENA_LINK="${ARENA_LINK:-$HOME/projetos/plataforma-jogos/deploy/link-portgiga.sh}"
mkdir -p "$ESTADO"

rodando() { [ -f "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null; }

if [ "$1" = parar ]; then
    if rodando; then kill "$(cat "$PID")"; echo "Túnel fechado. O link deixou de funcionar."; else echo "O túnel já estava fechado."; fi
    rm -f "$PID" "$LINK"
    [ -x "$ARENA_LINK" ] && "$ARENA_LINK" limpar
    exit 0
fi

if rodando && [ -s "$LINK" ]; then echo "Túnel já aberto: $(cat "$LINK")"; exit 0; fi
[ -x "$CF" ] || { echo "cloudflared não encontrado em $CF"; exit 1; }

# Sobe o servidor se estiver parado (mesmo jeito do atalho da área de trabalho)
if ! curl -s -m 2 -o /dev/null http://127.0.0.1:7777/; then
    (cd "$DIR/servidor" && setsid nohup python3 servidor.py >> "$DIR/servidor/servidor.log" 2>&1 < /dev/null &)
    for i in $(seq 1 30); do curl -s -m 1 -o /dev/null http://127.0.0.1:7777/ && break; sleep 1; done
fi

: > "$LOG"
setsid nohup "$CF" tunnel --no-autoupdate --url http://localhost:7777 > "$LOG" 2>&1 < /dev/null &
echo $! > "$PID"
for i in $(seq 1 60); do
    url=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$LOG" | head -1)
    [ -n "$url" ] && break; sleep 1
done
[ -n "$url" ] || { echo "O túnel não respondeu. Veja $LOG"; exit 1; }
echo "$url" > "$LINK"
for i in $(seq 1 30); do [ "$(curl -s -m 5 -o /dev/null -w '%{http_code}' "$url/")" = 200 ] && break; sleep 2; done
echo "Link público: $url"
[ -x "$ARENA_LINK" ] && "$ARENA_LINK" "$url"
