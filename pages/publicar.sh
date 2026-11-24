#!/usr/bin/env bash
# Publica pages/dist no Cloudflare Pages (projeto "portgiga"). Precisa do login feito uma vez:
#   npx --yes wrangler@4.157.0 login
# O túnel (tunel-servico.sh) chama este script quando o link muda.
set -euo pipefail
AQUI="$(cd "$(dirname "$0")" && pwd)"
WR="npx --yes wrangler@4.157.0"
bash "$AQUI/construir.sh"
$WR whoami 2>&1 | grep -qi "not authenticated" && { echo "sem login na Cloudflare: rode  npx --yes wrangler@4.157.0 login"; exit 2; }
$WR pages project list 2>/dev/null | grep -q '\bportgiga\b' || $WR pages project create portgiga --production-branch main
$WR pages deploy "$AQUI/dist" --project-name portgiga --branch main --commit-dirty=true
