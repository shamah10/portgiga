#!/usr/bin/env bash
# Instala dois serviços do usuário (systemd --user):
#   portgiga-tunel.service   túnel público ligado 24 h, volta sozinho se cair ou se o PC reiniciar
#   portgiga-backup.timer    cópia da cadeia todo dia (guarda 30 dias em ~/projetos/backups/cadeia-portgiga)
# Para desfazer:
#   systemctl --user disable --now portgiga-tunel.service portgiga-backup.timer
#   rm ~/.config/systemd/user/portgiga-tunel.service ~/.config/systemd/user/portgiga-backup.{service,timer}
set -euo pipefail
PASTA="$(cd "$(dirname "$0")" && pwd)"
U="$HOME/.config/systemd/user"; mkdir -p "$U"

cat > "$U/portgiga-tunel.service" <<EOT
[Unit]
Description=Rede Portgiga (PTG) - link publico (Cloudflare)
After=network-online.target portgiga.service
Wants=portgiga.service

[Service]
ExecStart=$PASTA/tunel-servico.sh
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
EOT

cat > "$U/portgiga-backup.service" <<EOT
[Unit]
Description=Rede Portgiga (PTG) - copia da cadeia

[Service]
Type=oneshot
ExecStart=$PASTA/backup-cadeia.sh
EOT

cat > "$U/portgiga-backup.timer" <<EOT
[Unit]
Description=Rede Portgiga (PTG) - copia diaria da cadeia

[Timer]
OnCalendar=*-*-* 03:30:00
Persistent=true

[Install]
WantedBy=timers.target
EOT

# fecha o túnel que está rodando solto (aberto à mão), para o serviço assumir
for p in /proc/[0-9]*; do
  cmd=$(tr '\0' ' ' < "$p/cmdline" 2>/dev/null || true)
  case "$cmd" in *cloudflared*"--url http://localhost:7777"*) kill "${p#/proc/}" && echo "túnel antigo fechado (PID ${p#/proc/})";; esac
done

rm -f "$HOME/.local/share/portgiga/tunel/link.txt"   # o link velho morre junto com o túnel antigo
systemctl --user daemon-reload
systemctl --user enable --now portgiga-tunel.service portgiga-backup.timer
echo "Esperando o link novo..."
for _ in $(seq 1 60); do
  L=$(cat "$HOME/.local/share/portgiga/tunel/link.txt" 2>/dev/null || true)
  [ -n "$L" ] && [ "$(curl -s -m 5 -o /dev/null -w '%{http_code}' "$L/api/estado")" = 200 ] && break
  sleep 2
done
echo
echo "OK. Link público agora: $L"
echo "Carteira no celular:    $L/app/"
echo "Cópia diária da cadeia: todo dia às 03:30 (próxima: $(systemctl --user list-timers portgiga-backup.timer --no-legend | awk '{print $1, $2, $3}'))"
