#!/usr/bin/env bash
# Free, zero-account public URL for reviewing the PoC.
#
# Runs the real stack locally (Neo4j + Streamlit) and exposes it via a Cloudflare
# quick tunnel (https://*.trycloudflare.com). Nothing leaves your machine except the
# tunnelled HTTP. Requires: Docker, a populated graph (run scripts/smoke.sh or `make ingest`),
# and ANTHROPIC_API_KEY in .env.
#
# Usage:  bash deploy/tunnel.sh        (Ctrl-C to stop the log tail; `make down` to stop)
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> Building + starting Neo4j, app, and the Cloudflare tunnel"
docker compose --profile app --profile tunnel up -d --build

echo "==> Waiting for the public URL (printed by cloudflared)…"
url=""
for _ in $(seq 1 30); do
  url=$(docker compose logs cloudflared 2>/dev/null | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -1 || true)
  [ -n "$url" ] && break
  sleep 2
done

if [ -n "$url" ]; then
  echo ""
  echo "  ┌──────────────────────────────────────────────────────────────┐"
  printf "  │  Public URL:  %-47s│\n" "$url"
  echo "  └──────────────────────────────────────────────────────────────┘"
  echo ""
  echo "Share that URL for review. Stop everything with:  make down"
else
  echo "Could not detect the URL yet. Check:  docker compose logs cloudflared"
fi