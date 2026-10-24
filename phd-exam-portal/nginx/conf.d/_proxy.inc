# Shared proxy rules, included by both the HTTP and HTTPS server blocks.
# $scheme is forwarded so FastAPI marks Secure cookies correctly behind TLS.

proxy_http_version 1.1;
proxy_set_header Host              $host;
proxy_set_header X-Real-IP         $remote_addr;
proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_set_header X-Forwarded-Host  $host;
proxy_set_header X-Forwarded-Port  $server_port;

location = /health {
    proxy_pass http://examportal;
    access_log off;
}

# WebSocket signalling: no read timeout, or feeds die mid-exam.
location /ws/ {
    proxy_pass http://examportal;
    proxy_set_header Upgrade    $http_upgrade;
    proxy_set_header Connection $connection_upgrade;
    proxy_read_timeout  7200s;
    proxy_send_timeout  7200s;
    proxy_buffering     off;
}

location ~ ^/api/auth/(candidate|admin)/login$ {
    limit_req zone=login burst=10 nodelay;
    proxy_pass http://examportal;
}

location /api/ {
    limit_req zone=api burst=120 nodelay;
    proxy_pass http://examportal;
    proxy_read_timeout 120s;
}

location /static/ {
    proxy_pass http://examportal;
    proxy_cache_valid 200 1h;
    expires 1h;
    add_header Cache-Control "public";
}

location /media/ {
    # Violation snapshots: never cached by shared proxies.
    proxy_pass http://examportal;
    add_header Cache-Control "private, no-store";
}

location / {
    proxy_pass http://examportal;
    proxy_read_timeout 120s;
}
