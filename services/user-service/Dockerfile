# syntax=docker/dockerfile:1.7
# user-service: build from the repository root (the shared library lives in libs/):
#   docker build -f services/user-service/Dockerfile -t shophub/user-service .

# ---------- stage 1: install dependencies (cached until requirements.txt changes)
FROM python:3.12-alpine AS deps
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
COPY services/user-service/requirements.txt /tmp/requirements.txt
RUN pip install --no-compile --target /opt/deps -r /tmp/requirements.txt

# ---------- stage 2: small runtime image (Alpine; no pip cache, no build tools, no tests)
FROM python:3.12-alpine AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/deps:/app/libs:/app

# unprivileged user; code stays owned by root, so the process cannot modify it
RUN addgroup -S -g 10001 app \
 && adduser -S -D -H -u 10001 -G app -s /sbin/nologin app

WORKDIR /app
COPY --from=deps /opt/deps /opt/deps
# code is copied last: changing it does not invalidate the dependency layers above
COPY libs/common/shopcommon /app/libs/shopcommon
COPY services/user-service/user_service /app/user_service

USER 10001:10001
EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"]

# configuration and secrets come from environment variables at run time, never from the image
ENTRYPOINT ["python", "-m", "uvicorn"]
CMD ["user_service.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
