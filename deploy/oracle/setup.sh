#!/usr/bin/env bash
# One-time setup on a fresh Oracle Cloud Always Free Ubuntu VM (Ampere A1), run from the repo root:
#   VOX_PUBLIC_URL=https://<your-app>.vercel.app bash deploy/oracle/setup.sh
# Needs backend/.env copied here first (scp, never git). Safe to run again: it only updates and restarts.
set -euo pipefail

: "${VOX_PUBLIC_URL:?set VOX_PUBLIC_URL to your Vercel address, e.g. https://voxagent.vercel.app}"
[ -f docker-compose.yml ] || { echo "run this from the voxagent repo root"; exit 1; }
[ -f backend/.env ] || { echo "copy backend/.env here first: scp backend/.env ubuntu@<ip>:~/voxagent/backend/.env"; exit 1; }

if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER"
fi

# Oracle's Ubuntu images drop inbound traffic in iptables even after the cloud firewall allows it.
for port in 80 443; do
  sudo iptables -C INPUT -p tcp --dport "$port" -j ACCEPT 2>/dev/null \
    || sudo iptables -I INPUT 6 -p tcp --dport "$port" -j ACCEPT
done
sudo sh -c 'command -v netfilter-persistent >/dev/null && netfilter-persistent save' || true

# A free HTTPS name that points at this machine: 1.2.3.4 -> 1-2-3-4.sslip.io (Caddy gets the certificate).
ip=$(curl -fsS https://api.ipify.org)
export VOX_DOMAIN="${VOX_DOMAIN:-${ip//./-}.sslip.io}"
export VOX_PUBLIC_URL

sudo -E docker compose -f docker-compose.yml -f deploy/oracle/docker-compose.server.yml up -d --build

echo
echo "API: https://$VOX_DOMAIN  (first start builds images and gets a certificate: give it a few minutes)"
echo "Check: curl -sf https://$VOX_DOMAIN/healthz"
echo "Then on your laptop, in web/:"
echo "  BACKEND_URL=https://$VOX_DOMAIN node scripts/vercel-config.ts"
echo "  and set VITE_WS_URL=wss://$VOX_DOMAIN in the Vercel project's environment variables, then redeploy."
