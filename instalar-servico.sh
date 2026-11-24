#!/usr/bin/env bash
# Deixa o servidor da Portgiga ligado 24 h: vira um servico do usuario (systemd --user),
# liga sozinho quando voce entra no PC e reinicia sozinho se cair.
# Para desfazer: systemctl --user disable --now portgiga.service && rm ~/.config/systemd/user/portgiga.service
set -euo pipefail

mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/portgiga.service <<'EOF'
[Unit]
Description=Rede Portgiga (PTG) - servidor
After=network-online.target

[Service]
WorkingDirectory=%h/projetos/rede-portgiga/servidor
ExecStart=/usr/bin/python3 %h/projetos/rede-portgiga/servidor/servidor.py
StandardOutput=append:%h/projetos/rede-portgiga/servidor/servidor.log
StandardError=append:%h/projetos/rede-portgiga/servidor/servidor.log
Restart=always
RestartSec=3
UMask=0077
NoNewPrivileges=yes
PrivateTmp=yes
LimitNOFILE=4096

[Install]
WantedBy=default.target
EOF

# fecha o servidor que esta rodando solto (o processo que ocupa a porta 7777), para o servico assumir a porta
PID=$(ss -ltnp 2>/dev/null | grep ":7777 " | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2)
if [ -n "$PID" ] && tr '\0' ' ' < "/proc/$PID/cmdline" | grep -q "servidor.py"; then
  kill "$PID"
  for _ in $(seq 1 20); do ss -ltn | grep -q ":7777 " || break; sleep 0.3; done
fi
systemctl --user daemon-reload
systemctl --user enable --now portgiga.service
# liga junto com o PC, mesmo antes de entrar na conta
loginctl enable-linger "$USER" 2>/dev/null || echo "Para ligar antes do login, rode: sudo loginctl enable-linger $USER"
sleep 5
if curl -s -o /dev/null http://127.0.0.1:7777/api/estado; then
  echo "OK: servidor da Portgiga rodando como servico (liga sozinho com o PC)."
else
  echo "ATENCAO: o servico nao respondeu. Veja: systemctl --user status portgiga.service"
fi
