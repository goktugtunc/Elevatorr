#!/usr/bin/env bash
# Refresh Cloudflare IP ranges used by nginx real_ip (run occasionally; then: sudo nginx -t && sudo systemctl reload nginx)
set -euo pipefail
OUT=/etc/nginx/snippets/cloudflare-realip.conf
TMP=$(mktemp)
{
  echo "# Cloudflare edge IP ranges -> trust CF-Connecting-IP as the client IP (generated $(date -u +%F))"
  for ip in $(curl -fsS https://www.cloudflare.com/ips-v4) $(curl -fsS https://www.cloudflare.com/ips-v6); do echo "set_real_ip_from $ip;"; done
  echo "real_ip_header CF-Connecting-IP;"
  echo "real_ip_recursive on;"
} > "$TMP"
sudo install -m 644 "$TMP" "$OUT"
rm -f "$TMP"
echo "updated $OUT"
