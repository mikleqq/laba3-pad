# Все YAML-файлы лабы 3 — построчно и простыми словами

Преподаватель проверяет именно YAML. Цель этого файла: чтобы ты мог объяснить **любую строку** и сказать, что
будет, если её убрать или изменить. Читай вместе с открытыми файлами.

---

## 0. Как устроен YAML

YAML — текстовый формат «ключ: значение» со вложенностью **через отступы пробелами**.

```yaml
metadata:              # ключ, значение которого — вложенный блок (словарь)
  name: order-service  # 2 пробела отступа = «внутри metadata»
  labels:
    app: order-service
ports:                 # список: каждый элемент начинается с "- "
  - name: http
    containerPort: 8000
  - name: metrics      # второй элемент списка
    containerPort: 9000
command: ["sh", "-c", "echo hi"]   # короткая запись списка
env: { name: A, value: "1" }       # короткая запись словаря
data:
  script: |            # "|" — многострочный текст, переводы строк сохраняются
    line 1
    line 2
```

Правила, на которых ломаются чаще всего:

| Правило | Пример ошибки | Что будет |
|---|---|---|
| Только пробелы, **табы запрещены** | таб перед `name:` | `found character that cannot start any token` |
| Отступ определяет вложенность | `name:` сдвинут на 1 пробел меньше | поле «уедет» в другой блок, kubectl скажет `unknown field` или его не будет |
| После двоеточия нужен пробел | `replicas:2` | это уже строка "replicas:2", а не ключ |
| Строки с `:` или `#` и числа-строки — в кавычки | `DB_PORT: 5432` в ConfigMap | ConfigMap принимает только строки → ошибка; нужно `"5432"` |
| `---` разделяет несколько документов в одном файле | | у нас каждый объект в своём файле, поэтому `---` не нужен |
| `#` — комментарий до конца строки | | Kubernetes его игнорирует |

Каждый объект Kubernetes имеет 4 верхних поля:

* `apiVersion` — версия API, в которой описан тип (`v1`, `apps/v1`, `networking.k8s.io/v1`, `autoscaling/v2`);
* `kind` — тип объекта (Deployment, Service, ConfigMap…);
* `metadata` — имя, namespace, метки;
* `spec` (или `data` у ConfigMap/Secret) — что мы хотим получить.

Kubernetes работает **декларативно**: ты описываешь желаемое состояние, а контроллеры сами доводят кластер до
него и поддерживают его.

---

## 1. `k8s/namespace.yaml`

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: shophub
  labels:
    app.kubernetes.io/part-of: shophub
```

* `kind: Namespace` — «папка» внутри кластера. Все наши объекты лежат в `shophub` и не смешиваются с системными
  (`kube-system`) и чужими.
* Зачем: `kubectl get all -n shophub` показывает только наше, а `kubectl delete namespace shophub` удаляет всё.
* Уберёшь — объекты, где написано `namespace: shophub`, не создадутся (namespace не существует).

---

## 2. `k8s/configmaps/shophub-config.yaml` — обычные настройки

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: shophub-config          # по этому имени на него ссылаются Deployment'ы
  namespace: shophub
data:
  LOG_LEVEL: "INFO"
  DB_HOST: "postgres"           # имя Service базы данных
  DB_PORT: "5432"               # в кавычках: в ConfigMap только строки
  DB_NAME: "shophub"
  USER_SERVICE_URL: "http://user-service:8000"
  PRODUCT_SERVICE_URL: "http://product-service:8000"
  ...
```

* `data` — пары «имя: значение». В Deployment строка `envFrom: - configMapRef: name: shophub-config`
  превращает **каждый** ключ в переменную окружения контейнера.
* Зачем: настройки не зашиты в образ, и один и тот же образ работает в Compose и в Kubernetes.
* Адреса сервисов — **имена Kubernetes Service** (`http://product-service:8000`), а не `localhost`: у каждого
  пода свой localhost.
* Поменял значение → `kubectl apply -f …` → `kubectl rollout restart deployment -n shophub`. Переменные окружения
  читаются только при старте контейнера, поэтому без рестарта ничего не изменится.
* Без кавычек у `"5432"` → ошибка: ConfigMap хранит только строки, а 5432 — число.

`k8s/configmaps/gateway-config.yaml` — то же для nginx: `DNS_RESOLVER` (DNS кластера) и `UPSTREAM_SUFFIX`
(`.shophub.svc.cluster.local`, полное имя сервиса).

`k8s/configmaps/postgres-init.yaml` — ConfigMap, где значение — **целый файл** (скрипт инициализации базы):

```yaml
data:
  01-shophub.sh: |      # имя файла: содержимое (многострочное)
    #!/bin/sh
    ...
```

В StatefulSet он подключается как том: каждый ключ становится файлом в папке `/docker-entrypoint-initdb.d`.

---

## 3. Secret `shophub-secrets` — пароли и ключи

В Git лежит только шаблон `k8s/secrets/secret.example.yaml.txt`. Сам Secret создаётся командой:

```powershell
kubectl create secret generic shophub-secrets -n shophub --from-env-file=.env
```

Если вывести его в YAML (`kubectl get secret shophub-secrets -n shophub -o yaml`):

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: shophub-secrets
  namespace: shophub
type: Opaque                   # «произвольные данные» (бывают ещё kubernetes.io/tls и т.д.)
data:
  JWT_SECRET: c2VjcmV0...      # base64
  ORDER_DB_PASSWORD: ...
```

* `data` хранится в **base64** — это кодирование, **не шифрование** (`[Convert]::FromBase64String` раскодирует).
  Защита Secret — в другом: доступ ограничивается правами (RBAC), значения не печатаются в `describe` и не
  лежат в Git.
* В шаблоне используется `stringData` — то же самое, но значения пишутся обычным текстом; Kubernetes сам
  переведёт их в base64.
* Как попадает в контейнер:

```yaml
env:
  - name: JWT_SECRET
    valueFrom:
      secretKeyRef:
        name: shophub-secrets   # какой Secret
        key: JWT_SECRET         # какой ключ из него
```

* **ConfigMap vs Secret**: устроены одинаково (ключ → значение). Secret — для паролей: отдельные права
  доступа, не показываются в выводе, можно включить шифрование в etcd.
* Нет Secret'а или ключа → под зависнет в `CreateContainerConfigError` (видно в `kubectl describe pod`).

---

## 4. `k8s/deployments/order-service.yaml` — главный файл, построчно

```yaml
apiVersion: apps/v1                 # Deployment живёт в группе API "apps", версия v1
kind: Deployment
metadata:
  name: order-service               # имя Deployment'а (kubectl get deploy order-service)
  namespace: shophub
  labels:                           # метки самого Deployment'а (для поиска/группировки)
    app: order-service
    app.kubernetes.io/part-of: shophub
    tier: backend
spec:
  replicas: 2                       # сколько одинаковых подов держать
```

* `replicas: 2` — контроллер постоянно сравнивает «сколько есть» и «сколько нужно». Удалили под → создаст новый.
  `kubectl scale … --replicas=3` меняет именно это поле.

```yaml
  revisionHistoryLimit: 3           # сколько старых ReplicaSet хранить для отката (kubectl rollout undo)
  strategy:
    type: RollingUpdate             # обновлять постепенно, а не «удалить всё и создать заново» (Recreate)
    rollingUpdate:
      maxSurge: 1                   # можно временно иметь на 1 под больше, чем replicas
      maxUnavailable: 0             # нельзя опускаться ниже replicas готовых подов -> обновление без простоя
```

```yaml
  selector:
    matchLabels:
      app: order-service            # «мои поды — это поды с меткой app=order-service»
  template:                         # шаблон пода: по нему создаются все копии
    metadata:
      labels:
        app: order-service          # ОБЯЗАНО совпадать с selector выше
        app.kubernetes.io/part-of: shophub
        tier: backend
```

**Метки и селекторы — самый частый вопрос.**

* `template.metadata.labels` — метки, которые получит каждый под.
* `spec.selector.matchLabels` — по каким меткам Deployment считает поды «своими».
* `Service.spec.selector` (в services/order-service.yaml) — по каким меткам Service ищет поды для трафика.
* Если selector не совпадает с метками шаблона → `kubectl apply` выдаст ошибку
  `selector does not match template labels`. Если Service ищет другую метку → у Service нет endpoints, и
  запросы уходят в никуда (gateway вернёт 503).
* `selector` у Deployment **нельзя изменить** после создания (immutable): придётся удалить Deployment.

```yaml
    spec:                           # описание пода
      securityContext:              # для всех контейнеров пода
        runAsNonRoot: true          # запрет запуска от root (если образ попытается — под не стартует)
        runAsUser: 10001            # UID пользователя "app" из Dockerfile
        runAsGroup: 10001
        seccompProfile:
          type: RuntimeDefault      # стандартный фильтр системных вызовов
```

```yaml
      initContainers:               # запускаются ПО ОЧЕРЕДИ и ДО основных контейнеров
        - name: wait-for-postgres
          image: busybox:1.37       # крошечный образ с утилитой nc
          command: ["sh", "-c", "until nc -z -w 2 $DB_HOST $DB_PORT; do echo waiting for postgres; sleep 2; done"]
          envFrom:
            - configMapRef:
                name: shophub-config   # отсюда $DB_HOST и $DB_PORT
          resources: ...
```

* Пока база не принимает соединения, init-контейнер крутится в цикле, а под в статусе `Init:0/1`.
  Как только `nc` подключился, он завершается, и стартует сервис.
* Зачем: без него при холодном старте сервисы стартовали раньше базы, startupProbe не дожидалась, и было
  по 1 рестарту на сервис. С ним — 0 рестартов.

```yaml
      containers:
        - name: order-service
          image: shophub/order-service:latest
          imagePullPolicy: IfNotPresent
```

* `image` — какой образ запускать. Мы его собрали и загрузили в Minikube (`minikube image load`).
* `imagePullPolicy: IfNotPresent` — не скачивать, если образ уже есть на узле. Для тега `latest` по умолчанию
  было бы `Always`: Kubernetes пошёл бы в Docker Hub и не нашёл образ → `ErrImagePull`.

```yaml
          ports:
            - name: http            # имя порта, на него ссылаются пробы и Service (targetPort: http)
              containerPort: 8000   # порт, который слушает uvicorn внутри контейнера
```

```yaml
          envFrom:
            - configMapRef:
                name: shophub-config        # ВСЕ ключи ConfigMap -> переменные окружения
          env:                              # отдельные переменные
            - name: SERVICE_NAME
              value: order-service          # просто значение
            - name: DB_PASSWORD
              valueFrom:
                secretKeyRef:               # значение из Secret
                  name: shophub-secrets
                  key: ORDER_DB_PASSWORD
            - name: DATABASE_URL
              value: postgresql+asyncpg://order_svc:$(DB_PASSWORD)@$(DB_HOST):$(DB_PORT)/$(DB_NAME)
```

* `$(DB_PASSWORD)` — Kubernetes подставит значение переменной, объявленной **раньше** (в `env` выше или в
  `envFrom`). Так строка подключения собирается из ConfigMap (хост, порт, имя базы) и Secret (пароль).
  В итоге пароль нигде не написан открытым текстом.
* Переставишь `DATABASE_URL` выше `DB_PASSWORD` → подстановки не будет, и в строке останется буквально
  `$(DB_PASSWORD)`.

```yaml
          resources:
            requests:               # гарантированный минимум: планировщик ищет узел, где это есть
              cpu: 50m              # 50 милли-ядер = 5% одного ядра
              memory: 96Mi
            limits:                 # потолок
              cpu: 500m             # больше 0.5 ядра -> процесс притормаживают (throttling)
              memory: 256Mi         # больше 256 МиБ -> контейнер убивают (OOMKilled) и перезапускают
```

* Значения по факту: `kubectl top pods` показывает ~60 МиБ у сервиса.
* HPA считает загрузку в процентах **от requests** (у каталога: «70% от 50m»).
* Без requests под может попасть на перегруженный узел; без limits одна утечка памяти съест весь узел.

```yaml
          startupProbe:             # проверка ЗАПУСКА: пока не прошла, остальные пробы не работают
            httpGet:
              path: /health
              port: http
            periodSeconds: 2        # проверять каждые 2 секунды
            failureThreshold: 30    # 30 неудач подряд (60 с) -> контейнер перезапускается
          livenessProbe:            # «процесс жив?»
            httpGet: { path: /health, port: http }
            periodSeconds: 10
            timeoutSeconds: 2       # ответ дольше 2 с = неудача
            failureThreshold: 3     # 3 неудачи подряд -> перезапуск контейнера
          readinessProbe:           # «готов принимать запросы?»
            httpGet: { path: /ready, port: http }
            periodSeconds: 5
            timeoutSeconds: 2
            failureThreshold: 3     # 3 неудачи -> под убирается из Service (но НЕ перезапускается)
```

| Проба | Endpoint | Что проверяет | Что делает Kubernetes при провале |
|---|---|---|---|
| startup | `/health` | успел ли сервис запуститься | ждёт; после 30 провалов перезапускает |
| liveness | `/health` | не завис ли процесс | **перезапускает контейнер** |
| readiness | `/ready` | доступны ли база и шина событий | **убирает под из Service** (трафик не идёт), ждёт |

* Почему liveness не на `/ready`: если упадёт база, все поды стали бы «мёртвыми» и начали бы бесконечно
  перезапускаться, хотя сами они живы. Правильно — убрать их из трафика (readiness).

```yaml
          securityContext:                  # для этого контейнера
            allowPrivilegeEscalation: false # нельзя получить больше прав (sudo/setuid)
            readOnlyRootFilesystem: true    # файловая система только для чтения: код не изменить
            capabilities:
              drop: ["ALL"]                 # убрать все Linux-привилегии
```

Остальные 5 сервисов устроены так же. Отличаются имя, пароль из Secret, `replicas` и у auth — ещё
`ADMIN_PASSWORD`.

---

## 5. `k8s/services/order-service.yaml`

```yaml
apiVersion: v1
kind: Service
metadata:
  name: order-service       # это имя = DNS-имя внутри кластера: http://order-service:8000
  namespace: shophub
spec:
  type: ClusterIP           # адрес доступен только внутри кластера (снаружи — через Ingress/gateway)
  selector:
    app: order-service      # отправлять трафик подам с этой меткой (и только ГОТОВЫМ — readiness!)
  ports:
    - name: http
      port: 8000            # порт Service (куда стучатся другие)
      targetPort: http      # порт контейнера (по имени -> 8000)
```

* Поды умирают и меняют IP, а имя и IP Service постоянны. Service сам балансирует между подами (у нас 10/10/10).
* Посмотреть, на какие поды он шлёт: `kubectl get endpoints order-service -n shophub`.
* Типы: `ClusterIP` (внутренний), `NodePort` (порт на узле), `LoadBalancer` (внешний балансировщик облака).
* `services/postgres.yaml` — то же для базы на порту 5432; `services/gateway.yaml` — порт 80 → 8080 контейнера.

---

## 6. `k8s/deployments/postgres.yaml` — StatefulSet

Отличия от Deployment:

```yaml
kind: StatefulSet
spec:
  serviceName: postgres          # Service, который даёт подам стабильные DNS-имена
  replicas: 1
  template:
    spec:
      containers:
        - name: postgres
          image: postgres:16-alpine
          env:
            - name: POSTGRES_DB                 # официальный образ создаст базу с таким именем
              valueFrom: { configMapKeyRef: { name: shophub-config, key: DB_NAME } }
            - name: POSTGRES_PASSWORD           # пароль суперпользователя — из Secret
              valueFrom: { secretKeyRef: { name: shophub-secrets, key: POSTGRES_PASSWORD } }
            - name: PGDATA                      # данные в подпапке: в корне тома лежит lost+found
              value: /var/lib/postgresql/data/pgdata
            - name: AUTH_DB_PASSWORD ...        # пароли 6 ролей для скрипта инициализации
          volumeMounts:
            - name: data                        # постоянный диск
              mountPath: /var/lib/postgresql/data
            - name: init                        # скрипт из ConfigMap
              mountPath: /docker-entrypoint-initdb.d
              readOnly: true
          readinessProbe:
            exec:                               # проба-команда внутри контейнера (не HTTP)
              command: ["pg_isready", "-U", "postgres", "-d", "shophub"]
      volumes:
        - name: init
          configMap:
            name: postgres-init
            defaultMode: 0555                   # права r-x: скрипт должен быть исполняемым
  volumeClaimTemplates:                         # для КАЖДОГО пода свой PersistentVolumeClaim
    - metadata:
        name: data                              # -> PVC "data-postgres-0"
      spec:
        accessModes: ["ReadWriteOnce"]          # диск монтируется одним узлом на запись
        resources:
          requests:
            storage: 1Gi
```

* **Почему StatefulSet**: стабильное имя пода (`postgres-0`, а не случайное) и **свой диск**, который
  переживает удаление пода. Удалишь `postgres-0` — он вернётся с тем же именем и тем же PVC, данные на месте.
* `kubectl get pvc -n shophub` → `data-postgres-0 Bound 1Gi`. В Minikube диск выдаёт StorageClass `standard`.
* Скрипт инициализации выполняется **только при пустой базе** (первый старт). Пароли в нём берутся из
  переменных окружения, а не написаны в SQL.

---

## 7. `k8s/deployments/gateway.yaml`

Как сервисы, плюс:

```yaml
      securityContext:
        runAsUser: 101                # пользователь nginx в образе nginx-unprivileged
      containers:
        - name: gateway
          ports:
            - name: http
              containerPort: 8080     # непривилегированный порт (порты <1024 требуют root)
          envFrom:
            - configMapRef:
                name: gateway-config  # подставляется в шаблон конфигурации nginx при старте
          livenessProbe:
            httpGet: { path: /healthz, port: http }
          volumeMounts:               # при readOnlyRootFilesystem nginx нужны 2 места для записи
            - name: tmp
              mountPath: /tmp
            - name: conf
              mountPath: /etc/nginx/conf.d   # сюда пишется готовый конфиг из шаблона
      volumes:
        - name: tmp
          emptyDir: {}                # временная пустая папка, живёт вместе с подом
        - name: conf
          emptyDir: {}
```

---

## 8. `k8s/ingress.yaml`

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: shophub
  namespace: shophub
spec:
  ingressClassName: nginx        # какой Ingress-контроллер обрабатывает правило (minikube addon "ingress")
  rules:
    - http:                      # без host: любое доменное имя
        paths:
          - path: /
            pathType: Prefix     # всё, что начинается с "/" (то есть всё)
            backend:
              service:
                name: gateway    # отправить в Service gateway
                port:
                  name: http
```

* Ingress — правила «какой внешний HTTP-запрос в какой Service». Сам по себе он ничего не делает: работает
  **Ingress-контроллер** (в Minikube — `minikube addons enable ingress`).
* Почему всё идёт в gateway, а не по путям в каждый сервис: gateway отдаёт сайт, скрывает `/internal`, делает
  rate limit, request id и красивый 503. Ingress — дверь в кластер, gateway — «ресепшен».
* Доступ на Windows: `minikube tunnel` (http://127.0.0.1) или `kubectl port-forward svc/gateway 8080:80`.

---

## 9. `k8s/autoscaling/product-service-hpa.yaml`

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
spec:
  scaleTargetRef:                # что масштабировать
    apiVersion: apps/v1
    kind: Deployment
    name: product-service
  minReplicas: 2                 # не меньше 2
  maxReplicas: 5                 # не больше 5
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70 # держать средний CPU около 70% от requests
```

* Данные о CPU берёт **metrics-server** (`kubectl top pods`).
* HPA сам меняет `replicas` у Deployment. Поэтому ручной `kubectl scale` на product-service HPA «откатит» назад —
  ручное масштабирование показываем на order-service, у которого HPA нет.

---

## 10. `k8s/kustomization.yaml`

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - namespace.yaml
  - configmaps/shophub-config.yaml
  ...
```

* Список всех файлов, которые применяет `kubectl apply -k k8s` (Kustomize встроен в kubectl).
* Secret в списке нет специально: он создаётся из `.env`.
* `kubectl kustomize k8s` покажет итоговый YAML, не применяя его.

---

## 11. `docker-compose.yml`

```yaml
name: laba3-pad                       # имя проекта (префикс контейнеров laba3-pad-...)

x-service: &service                   # "x-" — свой блок; &service — якорь (шаблон для копирования)
  restart: unless-stopped             # перезапускать упавший контейнер
  depends_on:
    postgres:
      condition: service_healthy      # стартовать только когда healthcheck базы зелёный

x-env: &env
  LOG_LEVEL: ${LOG_LEVEL:-INFO}       # из окружения/.env, по умолчанию INFO
  JWT_SECRET: ${JWT_SECRET:?run scripts/new-env.ps1 to create .env}   # ":?" — обязательна, иначе ошибка с подсказкой
```

```yaml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: shophub
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?...}
    volumes:
      - pgdata:/var/lib/postgresql/data          # именованный том: данные переживают пересоздание контейнера
      - ./database/init:/docker-entrypoint-initdb.d:ro   # скрипт инициализации, только чтение
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres -d shophub"]

  order-service:
    <<: *service                     # "<<: *service" — вставить всё из якоря service
    image: shophub/order-service:latest   # имя образа (тот же образ потом грузится в Minikube)
    build: { context: ., dockerfile: services/order-service/Dockerfile }  # контекст — корень (нужна libs/)
    environment:
      <<: *env
      DATABASE_URL: postgresql+asyncpg://order_svc:${ORDER_DB_PASSWORD}@postgres:5432/shophub
      PRODUCT_SERVICE_URL: http://product-service:8000   # имя сервиса = DNS-имя в сети Compose

  gateway:
    ports:
      - "8080:8080"                  # "хост:контейнер" — единственный порт наружу
```

* Compose = Kubernetes «на минималках»: сеть с DNS по именам сервисов, тома, healthcheck, перезапуск. Но нет
  нескольких узлов, самовосстановления по пробам, Service-балансировки и автомасштабирования.
* `docker compose up --build` — одна команда для всей системы.

---

## 12. `.github/workflows/ci-cd.yml`

```yaml
name: CI/CD                          # название во вкладке Actions
on:                                  # КОГДА запускать
  push:
    branches: [main]                 # пуш в main
  pull_request:
    branches: [main]                 # PR в main
  workflow_dispatch:                 # кнопка «Run workflow»
concurrency:
  group: ci-${{ github.ref }}        # новый пуш в ту же ветку отменяет старый запуск
  cancel-in-progress: true
permissions:
  contents: read                     # минимальные права токена по умолчанию
env:
  IMAGE_PREFIX: ghcr.io/${{ github.repository }}   # ghcr.io/mikleqq/laba3-pad
```

`${{ … }}` — выражения GitHub Actions (контекст запуска: ветка, репозиторий, секреты).

**Job 1 `test`:**

```yaml
  test:
    runs-on: ubuntu-latest           # чистая виртуальная машина GitHub
    steps:
      - uses: actions/checkout@v4    # скачать код репозитория
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip                 # кэш пакетов между запусками
      - run: pip install -r requirements-dev.txt
      - run: pytest -q --ignore=tests/e2e        # 105 тестов
      - run: |                                   # проверка манифестов по схемам Kubernetes
          curl ... kubeconform ...
          kubectl kustomize k8s | ./kubeconform -strict -summary -
```

* `uses:` — готовое действие (action) из маркетплейса; `run:` — shell-команда.
* Упал любой шаг → job красный, следующие job'ы (`needs`) не запускаются.

**Job 2 `e2e`:**

```yaml
  e2e:
    needs: test                      # только если test прошёл
    steps:
      - run: |                       # .env со случайными паролями: в Git их нет даже для CI
          echo "JWT_SECRET=$(openssl rand -hex 32)" >> .env ...
      - run: docker compose up -d --build --wait --wait-timeout 240   # вся система + ждать healthy
      - run: cd tests/e2e && pytest ...                                # покупка через gateway
      - if: failure()                # только при ошибке — показать логи
        run: docker compose logs --no-color
      - if: always()                 # всегда — остановить
        run: docker compose down -v
```

**Job 3 `build-push`:**

```yaml
  build-push:
    needs: e2e
    permissions:
      packages: write                # разрешить токену писать в ghcr.io
    strategy:
      matrix:                        # один и тот же job 7 раз, параллельно, с разными параметрами
        include:
          - { image: auth-service, dockerfile: services/auth-service/Dockerfile }
          ...
    steps:
      - uses: docker/setup-buildx-action@v3      # BuildKit-сборщик
      - uses: docker/login-action@v3             # вход в ghcr.io
        if: github.event_name != 'pull_request'  # на PR не логинимся и не пушим
        with:
          password: ${{ secrets.GITHUB_TOKEN }}  # токен, который GitHub выдаёт каждому запуску сам
      - uses: docker/metadata-action@v5          # теги: sha-abc1234 и latest (только для main)
      - uses: docker/build-push-action@v6
        with:
          context: .
          file: ${{ matrix.dockerfile }}
          push: ${{ github.event_name != 'pull_request' }}
          cache-from: type=gha,scope=${{ matrix.image }}   # кэш слоёв между запусками
```

* Итог: на каждый пуш в main в **GitHub → Packages** появляются 7 образов с тегом коммита.
* Деплой в кластер остаётся ручным (`scripts/k8s-deploy.ps1`): серверы GitHub не видят Minikube на ноутбуке.

---

## 13. Типичные вопросы по YAML (коротко)

* **Deployment vs Pod?** Pod — один экземпляр. Deployment — контроллер, который держит `replicas` подов и
  обновляет их.
* **Deployment vs ReplicaSet?** Deployment создаёт ReplicaSet (он и держит число подов), а при обновлении
  делает новый ReplicaSet и плавно переносит поды. `kubectl get rs -n shophub`.
* **Зачем `selector`, если есть `labels`?** labels — «бирки» на подах; selector — «кого считать своими».
  Должны совпадать в Deployment и в Service.
* **ConfigMap vs Secret?** Устроены одинаково. Secret — для паролей: права доступа, не виден в выводе, base64
  (не шифрование!).
* **requests vs limits?** requests — гарантия и основа для планировщика и HPA; limits — потолок (CPU —
  тормозим, память — убиваем).
* **readiness vs liveness?** readiness → убрать из трафика; liveness → перезапустить.
* **Что если удалить под?** Deployment создаст новый (`replicas`). У postgres-0 (StatefulSet) — с тем же именем
  и тем же диском.
* **Почему `imagePullPolicy: IfNotPresent`?** Образы загружены в Minikube локально; иначе для `latest`
  Kubernetes пытался бы скачать их из реестра.

---

## 14. Потренируйся (сломай и почини)

| Что сделать | Команда / правка | Что увидишь | Как починить |
|---|---|---|---|
| Поменять число копий в YAML | `replicas: 3` в order-service.yaml → `kubectl apply -k k8s` | 3 пода | вернуть 2 и apply |
| Сломать метку | в services/order-service.yaml `selector: app: order` → apply | `kubectl get endpoints order-service -n shophub` пустой, заказы → 503 | вернуть `order-service` |
| Табуляция | вставь таб перед `replicas` | `kubectl apply` → ошибка разбора YAML | заменить на пробелы |
| Неверный ключ Secret | `key: ORDER_DB_PASS` | под в `CreateContainerConfigError`, `kubectl describe pod` объяснит | вернуть ключ |
| Маленький лимит памяти | `memory: 20Mi` в limits | под `OOMKilled`, RESTARTS растёт | вернуть 256Mi |
| Неверный путь пробы | `readinessProbe path: /nope` | под Running, но `0/1` READY, трафика нет | вернуть `/ready` |
| Изменить настройку | `LOG_LEVEL: "DEBUG"` в ConfigMap → apply → `kubectl rollout restart deployment/order-service -n shophub` | в логах появятся DEBUG-сообщения | вернуть INFO |

После каждой правки: `kubectl get pods -n shophub` и `kubectl describe pod -n shophub <pod>` — в разделе
**Events** всегда написана причина.
