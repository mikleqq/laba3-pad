# ShopHub on Docker and Kubernetes (Lab 3, PAD)

Lab 3 takes the microservice online store from lab 2 (**ShopHub**, an electronics shop), packs every part into
an optimized Docker image, runs the whole system with one `docker compose` command, deploys it to a local
Kubernetes cluster (**Minikube**) and builds/tests/publishes it automatically with **GitHub Actions**.

> Guides in Russian for the defense: [docs/DEMO_RU.md](docs/DEMO_RU.md) (what to show and say, step by step)
> and [docs/YAML_RU.md](docs/YAML_RU.md) (every YAML file explained line by line).

## 1. Project

| Component | What it does | Image | Replicas in k8s |
|---|---|---|---|
| `auth-service` | registration, login, JWT | `shophub/auth-service` | 2 |
| `user-service` | customer profiles | `shophub/user-service` | 1 |
| `product-service` | catalog and stock | `shophub/product-service` | 2–5 (autoscaled) |
| `order-service` | orders and delivery details (saga with product and payment) | `shophub/order-service` | 2 |
| `payment-service` | payments and refunds | `shophub/payment-service` | 1 |
| `notification-service` | notifications for customers | `shophub/notification-service` | 1 |
| `gateway` | API gateway (nginx) + web UI: the single entry point | `shophub/gateway` | 2 |
| `postgres` | one database `shophub`, a schema + login role per service, event bus tables | `postgres:16-alpine` | 1 (StatefulSet + PVC) |

## 2. Architecture

```
                         Browser
                            │ http
              ┌─────────────▼─────────────┐
              │ Ingress (nginx controller)│   kubectl port-forward / minikube tunnel
              └─────────────┬─────────────┘
              ┌─────────────▼─────────────┐
              │ gateway (nginx) ×2        │   web UI + /auth /users /products /orders /payments /notifications
              └─────────────┬─────────────┘   rate limit, X-Request-ID, JSON 503 when a service is down
   ┌───────────┬────────────┼────────────┬────────────┬─────────────────┐
   ▼           ▼            ▼            ▼            ▼                 ▼
 auth ×2 ──► user ×1    product ×2-5 ◄── order ×2   payment ×1    notification ×1     Deployments
   │           │            │            │            │                 │            behind ClusterIP Services
   └───────────┴────────────┴─────┬──────┴────────────┴─────────────────┘
                     ┌────────────▼────────────┐
                     │ postgres-0 (StatefulSet)│  schemas auth, users, products, orders, payments,
                     │ PVC data-postgres-0 1Gi │  notifications + bus.events (asynchronous events)
                     └─────────────────────────┘
ConfigMaps: shophub-config, gateway-config, postgres-init   Secret: shophub-secrets (created from .env)
```

* **Synchronous calls** use Kubernetes Service names, never `localhost`: auth → `http://user-service:8000`,
  order → `http://product-service:8000`.
* **Asynchronous events** (`order.created`, `payment.completed`, …) go through the `bus.events` table. When a
  service runs several replicas, only the replica that holds the row lock on its offset consumes
  (`SELECT … FOR UPDATE SKIP LOCKED`), so scaling never processes an event twice.
* **One database, isolated schemas**: every service has its own login role and schema and cannot read the others.

## 3. Technologies and why

| Area | Choice | Why / alternatives considered |
|---|---|---|
| Services | Python 3.12, FastAPI | from lab 2; async, automatic OpenAPI |
| Images | multi-stage, `python:3.12-alpine` | 153 MB instead of 297 MB (Debian slim). Distroless was considered but needs glibc wheels and has no shell for health checks |
| Gateway | nginx (unprivileged image) | tiny (74 MB, 7 MiB RAM), serves the UI and routes the API. Kong/Traefik would be heavier for this size |
| Orchestration | Kubernetes on Minikube (docker driver) | required by the lab; runs locally next to Docker Desktop. kind and k3d were the alternatives |
| Manifests | plain YAML + Kustomize (`kubectl apply -k`) | built into kubectl, no templating language. Helm is better for many environments |
| Database | PostgreSQL StatefulSet + PVC | stable identity and storage that survive restarts |
| CI/CD | GitHub Actions + GHCR | lives next to the code; the built-in `GITHUB_TOKEN` can push images, so no extra secrets are needed |

## 4. Installation

Requirements: Docker Desktop, Minikube, kubectl, PowerShell (Windows).

```powershell
git clone https://github.com/mikleqq/laba3-pad.git
cd laba3-pad
powershell -ExecutionPolicy Bypass -File scripts\new-env.ps1     # .env with random secrets (git-ignored)
```

## 5. Run

**Docker Compose** (the whole system with one command):

```powershell
docker compose up --build            # http://localhost:8080
docker compose down -v               # stop and delete data
```

**Kubernetes (Minikube)**:

```powershell
minikube start --driver=docker --cpus=4 --memory=6144 --addons=ingress --addons=metrics-server
powershell -ExecutionPolicy Bypass -File scripts\k8s-deploy.ps1   # build -> minikube image load -> Secret -> apply -> wait
kubectl port-forward -n shophub svc/gateway 8080:80               # http://localhost:8080
# or through the Ingress:  minikube tunnel  ->  http://127.0.0.1
```

Manual equivalent of `k8s-deploy.ps1`:

```powershell
docker compose build
minikube image load shophub/auth-service:latest   # ... for each of the 7 images
kubectl apply -f k8s/namespace.yaml
kubectl create secret generic shophub-secrets -n shophub --from-env-file=.env
kubectl apply -k k8s
kubectl get pods -n shophub -w
```

## 6. Configuration

| Where | What | How it reaches the container |
|---|---|---|
| `k8s/configmaps/shophub-config.yaml` | `LOG_LEVEL`, `DB_HOST`, `DB_PORT`, `DB_NAME`, `USER_SERVICE_URL`, `PRODUCT_SERVICE_URL`, `MAX_PAYMENT_AMOUNT`, … | `envFrom: configMapRef` |
| `k8s/configmaps/gateway-config.yaml` | DNS resolver, service domain suffix, rate limit | `envFrom`, rendered into the nginx config at start |
| `k8s/configmaps/postgres-init.yaml` | database init script (same file as `database/init/01-shophub.sh`) | mounted as a volume into `/docker-entrypoint-initdb.d` |
| Secret `shophub-secrets` | DB passwords, `JWT_SECRET`, `INTERNAL_TOKEN`, `ADMIN_PASSWORD` | `valueFrom: secretKeyRef`, created from `.env` |

`DATABASE_URL` is built by Kubernetes from these parts:
`postgresql+asyncpg://order_svc:$(DB_PASSWORD)@$(DB_HOST):$(DB_PORT)/$(DB_NAME)`.

**Secrets are never in Git**: `.env` and `k8s/secrets/secret.yaml` are git-ignored, only templates are
committed (`.env.example`, `k8s/secrets/secret.example.yaml.txt`). The code has no default secrets, so a
service without `JWT_SECRET` refuses to start. The images contain no configuration at all.

## 7. API (through the gateway)

| Method | Path | Service |
|---|---|---|
| POST | `/auth/register`, `/auth/login` | auth |
| GET/PATCH | `/users/me` | user |
| GET | `/products?search=&category=`, `/products/{id}` | product |
| POST/GET | `/orders`, `/orders/{id}`, `/orders/{id}/cancel` | order |
| GET | `/payments`, `/payments/order/{id}` | payment |
| GET/POST | `/notifications`, `/notifications/{id}/read` | notification |
| GET | `/status/<auth\|user\|product\|order\|payment\|notification>` | health of one service (via gateway) |
| GET | `/healthz` | gateway itself |

Each service also has `/health` (liveness) and `/ready` (database + event bus) on port 8000. Swagger:
`kubectl port-forward -n shophub deploy/order-service 8004:8000` → http://localhost:8004/docs.
`/internal/*` (service-to-service) answers 404 at the gateway.

## 8. Usage examples

```powershell
$base = "http://localhost:8080"
Invoke-RestMethod "$base/products?limit=3"
$body = @{ email = "anna@example.com"; password = "Secret2024"; full_name = "Анна Иванова" } | ConvertTo-Json
Invoke-RestMethod -Method Post "$base/auth/register" -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($body))
```

```powershell
kubectl get pods -n shophub
kubectl logs -n shophub deploy/order-service --tail=20
kubectl scale deployment order-service -n shophub --replicas=3
kubectl delete pod -n shophub -l app=user-service        # Kubernetes creates a new pod immediately
kubectl get hpa -n shophub
```

## 9. Testing

```powershell
pip install -r requirements-dev.txt
pytest --ignore=tests/e2e        # 105 unit, integration and manifest tests
pytest -m e2e                    # end-to-end through the gateway (Compose: :8080, k8s: set SHOPHUB_URL)
```

* `tests/unit/test_k8s_manifests.py` checks the manifests statically: selectors match labels, every container has
  resources and probes, secrets come only from `secretKeyRef`, and the init ConfigMap matches the Compose script.
* `tests/e2e/test_stack.py`: health of all services, `/internal` hidden, request id, purchase → payment →
  notifications, validation, admin catalog management.
* **CI/CD** (`.github/workflows/ci-cd.yml`) runs on every push and pull request:
  1. tests + `kubeconform` schema validation of `kubectl kustomize k8s`;
  2. `docker compose up --build --wait` with random secrets, then the e2e tests;
  3. build of the 7 images, pushed to `ghcr.io/mikleqq/laba3-pad/<image>` (tags `sha-…` and `latest`) on `main`.

  Deployment to the cluster stays manual (`scripts/k8s-deploy.ps1`), because GitHub's runners cannot reach a
  Minikube that runs on a laptop.

## 10. Results of experiments

| Experiment | Result |
|---|---|
| Image size, lab 2 (`python:3.12-slim`, single stage) vs lab 3 (multi-stage `alpine`, per-service deps) | 297 MB → **151–153 MB** per service; gateway 74 MB |
| Rebuild after a code change only | dependency layers come from the cache, only the last `COPY` layers are rebuilt (seconds) |
| Memory use per service pod (`kubectl top`) | ~60 MiB (request 96 Mi, limit 256 Mi); gateway 7 MiB; postgres 77 MiB |
| Cold start of the whole cluster (empty namespace → all pods ready) | ~50 s, **0 restarts** thanks to the `wait-for-postgres` initContainer (without it the first start produced 1 restart per service: the startup probe gave up while the database was still initialising) |
| `kubectl scale deployment order-service --replicas=3`, 30 requests | **10 / 10 / 10** requests per pod (Service load balancing) |
| `kubectl scale deployment payment-service --replicas=0` | `/status/payment` → 503 JSON from the gateway, catalog and orders → 200; orders wait as `pending_payment` and are paid after `--replicas=1` |
| `kubectl delete pod <user-service pod>` | new pod `Init:0/1` after 2 s, `Running 1/1` after ~17 s |
| HPA `product-service` | `cpu: 7%/70%`, 2 replicas (min 2, max 5) |

## 11. Conclusions

* Multi-stage builds on Alpine, per-service dependency lists and allowlist `.dockerignore` files halve the
  image size. The images run as non-root and contain neither secrets nor configuration.
* Kubernetes adds what Docker Compose alone does not do: self-healing (a deleted or crashed pod is recreated),
  rolling updates without downtime (`maxUnavailable: 0`), readiness-based load balancing, and manual and
  automatic horizontal scaling.
* Probes need care. `startupProbe` protects slow starts, `readinessProbe` keeps traffic away from pods that
  cannot reach the database, `livenessProbe` restarts hung processes, and an initContainer orders the startup.
* Scaling a service that consumes events needs coordination. A row lock (`FOR UPDATE SKIP LOCKED`) turns
  replicas into competing consumers without an external broker.
* One shared PostgreSQL is a single point of failure for data. A production setup would use a managed or
  replicated database (or one per service) and a message broker; the manifests would stay almost the same.

## Repository structure

```
laba3-pad/
├── README.md
├── docker-compose.yml              # whole system: postgres + 6 services + gateway
├── .env.example                    # template of the secrets file (.env is git-ignored)
├── .github/workflows/ci-cd.yml     # CI/CD
├── services/<name>/                # Dockerfile, Dockerfile.dockerignore, requirements.txt, <package>/
├── gateway/                        # Dockerfile, nginx.conf.template (API gateway + UI)
├── frontend/index.html             # web UI (served by the gateway)
├── libs/common/shopcommon/         # shared infrastructure code (logging, errors, security, db, event bus)
├── database/init/01-shophub.sh     # database init script (Compose)
├── k8s/
│   ├── namespace.yaml
│   ├── kustomization.yaml
│   ├── configmaps/                 # shophub-config, gateway-config, postgres-init
│   ├── secrets/                    # template only
│   ├── deployments/                # 6 services + gateway + postgres StatefulSet
│   ├── services/
│   ├── ingress.yaml
│   └── autoscaling/                # HPA of product-service
├── scripts/                        # new-env.ps1, k8s-secret.ps1, k8s-deploy.ps1
├── tests/{unit,integration,e2e}/
└── docs/                           # DEMO_RU.md, YAML_RU.md
```
