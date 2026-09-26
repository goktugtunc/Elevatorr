# monadback.yolalapp.com — TraderKirala backend, Monad (FastAPI in Docker, 127.0.0.1:8013)
# Cloudflare (proxied) -> nginx -> uvicorn. TLS terminated here with Let's Encrypt:
#   sudo certbot --nginx -d monadback.yolalapp.com   (OPS-10; adds the 443 / redirect blocks)
# The Stellar backend (mobilback.yolalapp.com, 8012) is untouched (K13).

limit_req_zone $binary_remote_addr zone=traderkirala_auth:10m rate=10r/s;
limit_req_zone $binary_remote_addr zone=traderkirala_api:10m rate=60r/s;

server {
    listen 80;
    listen [::]:80;
    server_name monadback.yolalapp.com;

    include /etc/nginx/snippets/cloudflare-realip.conf;

    client_max_body_size 5M;
    server_tokens off;

    # health checks without rate limiting or logging noise
    location = /health {
        proxy_pass http://127.0.0.1:8013;
        proxy_set_header Host $host;
        access_log off;
    }

    location = /health/chain {
        proxy_pass http://127.0.0.1:8013;
        proxy_set_header Host $host;
        proxy_read_timeout 30s;
        access_log off;
    }

    # SIWE nonce / verify: tight limit (the app-side token bucket is the second line of defence)
    location /api/v1/auth/ {
        limit_req zone=traderkirala_auth burst=20 nodelay;
        proxy_pass http://127.0.0.1:8013;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 60s;
    }

    location / {
        limit_req zone=traderkirala_api burst=120 nodelay;
        proxy_pass http://127.0.0.1:8013;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 60s;    # no long polls: POST /tx/submit waits at most TX_SUBMIT_TIMEOUT_SECONDS (20 s)
        proxy_send_timeout 60s;
    }
}
