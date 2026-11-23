# syntax=docker/dockerfile:1.7
# API Gateway + web UI. Build from the repository root:
#   docker build -f gateway/Dockerfile -t shophub/gateway .
#
# nginx-unprivileged runs as user 101 and listens on 8080 (no root needed).
FROM nginxinc/nginx-unprivileged:1.27-alpine

# Defaults for Docker Compose; Kubernetes overrides them from the ConfigMap.
ENV DNS_RESOLVER=127.0.0.11 \
    UPSTREAM_SUFFIX="" \
    RATE_LIMIT=20r/s

COPY gateway/nginx.conf.template /etc/nginx/templates/default.conf.template
COPY frontend/index.html /usr/share/nginx/html/index.html

EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -qO- http://127.0.0.1:8080/healthz >/dev/null || exit 1
# CMD/ENTRYPOINT come from the base image: it renders the templates and starts nginx in the foreground.
