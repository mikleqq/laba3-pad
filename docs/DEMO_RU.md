# Лаба 3: как запустить и как сдавать

Подробный разбор каждого YAML-файла (строка за строкой) — в [YAML_RU.md](YAML_RU.md). Преподаватель любит
проверять именно YAML, поэтому выучи его в первую очередь.

## 0. Что получилось (одним абзацем)

Магазин техники **ShopHub** из лабы 2 упакован в Docker-образы и запущен в Kubernetes (Minikube):

* **6 микросервисов** (auth, user, product, order, payment, notification) — 6 Deployment'ов;
* **gateway** (nginx) — единая точка входа: отдаёт сайт и направляет запросы `/auth`, `/products`,
  `/orders`… в нужный сервис;
* **postgres** — одна база `shophub` в StatefulSet с постоянным диском (PVC). У каждого сервиса своя схема и
  свой пользователь;
* **Ingress** — вход снаружи;
* **ConfigMap** — настройки, **Secret** — пароли и ключи (в Git их нет);
* **HPA** — автомасштабирование каталога;
* **CI/CD** — GitHub Actions: тесты → проверка всей системы в Docker Compose → сборка 7 образов и выгрузка в ghcr.io.

## 1. Подготовка (до сдачи, один раз)

```powershell
cd C:\Users\ps\Projekt\laba3-pad
powershell -ExecutionPolicy Bypass -File scripts\new-env.ps1          # .env со случайными паролями (не попадает в Git)
minikube start --driver=docker --cpus=4 --memory=6144 --addons=ingress --addons=metrics-server
powershell -ExecutionPolicy Bypass -File scripts\k8s-deploy.ps1        # сборка образов -> загрузка в minikube -> Secret -> apply -> ожидание
```

> `minikube.exe` лежит в `C:\Users\ps\bin`. Если команда `minikube` не находится, пиши полный путь
> `C:\Users\ps\bin\minikube.exe` или добавь эту папку в PATH.

Проверь заранее, что все поды `Running` и `1/1`:

```powershell
kubectl get pods -n shophub
```

Открыть магазин (в отдельном окне PowerShell, окно не закрывать):

```powershell
kubectl port-forward -n shophub svc/gateway 8080:80
```

Магазин будет на http://localhost:8080.

После перезагрузки компьютера: запусти Docker Desktop, потом `minikube start`. Всё поднимется само: поды, база
и данные сохраняются.

## 2. Сценарий сдачи

### Шаг 1. Приложение и архитектура (1–2 минуты)

Открой магазин и сделай покупку: **Войти → Регистрация**, товар в корзину, **Оформить заказ**. Через секунду
заказ станет «Оплачен», придут уведомления.

Что сказать: «Это 6 микросервисов. Сайт обращается только к gateway, а gateway направляет запрос в нужный
сервис. Заказ синхронно резервирует товар в product-service (REST по имени сервиса `http://product-service:8000`),
а оплата и уведомления идут асинхронно, через таблицу событий в базе».

### Шаг 2. Kubernetes: что запущено

```powershell
kubectl get pods -n shophub              # все поды: у некоторых сервисов по 2 копии
kubectl get deployments -n shophub       # READY 2/2 — сколько копий нужно и сколько готово
kubectl get services -n shophub          # внутренние адреса: order-service:8000, postgres:5432 ...
kubectl get configmap,secret -n shophub
kubectl get ingress,hpa,pvc -n shophub
```

Как всё запускалось: `kubectl apply -k k8s` применяет все манифесты из `k8s/kustomization.yaml`.
Secret создаётся отдельно из `.env` командой
`kubectl create secret generic shophub-secrets --from-env-file=.env`, потому что пароли не хранятся в Git.

Логи (JSON, у всех записей одного запроса общий `request_id`):

```powershell
kubectl logs -n shophub deploy/order-service --tail=20
kubectl logs -n shophub -l app=gateway --tail=5
```

### Шаг 3. Работа API (несколько реальных запросов)

```powershell
Invoke-RestMethod "http://localhost:8080/products?limit=3" | ConvertTo-Json -Depth 4
Invoke-RestMethod "http://localhost:8080/status/order"
Invoke-WebRequest "http://localhost:8080/internal/stock/reserve" -Method Post -UseBasicParsing   # 404: внутренние адреса скрыты
```

Или в браузере: сайт, «Мои заказы», уведомления.

### Шаг 4. Масштабирование

```powershell
kubectl scale deployment order-service -n shophub --replicas=3
kubectl get pods -n shophub -l app=order-service      # стало 3 пода
```

Что сказать: «Service распределяет запросы между всеми готовыми подами. Я проверял: 30 запросов разошлись
ровно 10/10/10». Посмотреть это вживую можно так: сделай несколько действий на сайте и выполни
`kubectl logs -n shophub -l app=order-service --tail=3 --prefix`, где видно, какой под ответил.

Автомасштабирование: `kubectl get hpa -n shophub`. Каталог сам добавит поды (до 5), если нагрузка на CPU
превысит 70%.

Вернуть как было: `kubectl scale deployment order-service -n shophub --replicas=2`.

### Шаг 5. Выключить один сервис — остальные работают

```powershell
kubectl scale deployment payment-service -n shophub --replicas=0
```

* на сайте внизу точка «Оплата» стала красной, в заказах появилась плашка «Статус оплаты временно не обновляется»;
* каталог, корзина, профиль работают; **заказ оформляется** со статусом «Ожидает оплаты»;
* `Invoke-WebRequest http://localhost:8080/status/payment -UseBasicParsing` → 503 (gateway честно говорит,
  что сервиса нет).

```powershell
kubectl scale deployment payment-service -n shophub --replicas=1
```

Через несколько секунд заказ **сам становится «Оплачен»**: событие ждало в базе.

### Шаг 6. Kubernetes сам восстанавливает упавший под

```powershell
kubectl get pods -n shophub -l app=user-service
kubectl delete pod -n shophub <имя-пода-user-service>
kubectl get pods -n shophub -l app=user-service -w      # новый под: Init -> Running (Ctrl+C чтобы выйти)
```

Что сказать: «Deployment хочет, чтобы была 1 копия. Под удалили, копий стало 0, и контроллер сразу создал
новый под. Сначала отработал initContainer `wait-for-postgres`, потом readinessProbe `/ready` сказала
"готов", и только тогда Service начал слать на него запросы».

Падение процесса внутри контейнера (livenessProbe/restart):

```powershell
kubectl exec -n shophub deploy/order-service -- python -c "import os, signal; os.kill(1, signal.SIGTERM)"
kubectl get pods -n shophub -l app=order-service     # RESTARTS увеличился на 1
```

### Шаг 7. CI/CD

Открой на GitHub вкладку **Actions** → workflow **CI/CD**.

* **Когда запускается**: при `push` в `main`, при pull request в `main` и по кнопке «Run workflow».
* **Шаги**:
  1. *Tests and manifest validation* — 105 тестов (сервисы, интеграция, проверки YAML) и `kubeconform`
     (сверяет манифесты со схемами Kubernetes API);
  2. *End-to-end test* — создаёт `.env` со случайными паролями, поднимает всю систему `docker compose up --wait`
     и прогоняет настоящую покупку через gateway;
  3. *Build image* ×7 — собирает 7 образов и **на `main`** выгружает их в GitHub Container Registry
     (`ghcr.io/mikleqq/laba3-pad/<сервис>`, теги `sha-xxxx` и `latest`). На pull request образы только
     собираются, без выгрузки.
* Показать вживую: поменяй что-нибудь безопасное (например, строку в README), потом

```powershell
git add README.md
git commit -m "docs: update README"
git push
```

  и открой Actions: появится новый запуск.

## 3. Ответы на вопросы с защиты

**Почему приложение разделено именно на такие микросервисы?** По бизнес-задачам: вход, профили, каталог,
заказы, оплата, уведомления. У каждой своя команда данных (своя схема в базе) и своя причина меняться. Оплата
медленная и внешняя, поэтому она асинхронная. Уведомления — чистый подписчик на события.

**Это микросервисы или распределённый монолит?** Ближе к микросервисам: сервисы деплоятся и масштабируются
отдельно, не читают чужие таблицы, падение оплаты или уведомлений не ломает остальное. Но есть честные связи:
одна общая база (общий ресурс), а оформление заказа синхронно зависит от каталога, регистрация — от профилей.
Это осознанный компромисс ради простоты, и об этом надо сказать.

**Как сервисы взаимодействуют?** Синхронно — HTTP по именам Kubernetes Service (`http://product-service:8000`).
Асинхронно — через таблицу `bus.events`: сервис записывает событие, подписчики читают его по порядку.

**Что произойдёт при падении одного сервиса?** Gateway вернёт 503 только на его запросы, остальные работают.
Kubernetes перезапустит контейнер (livenessProbe) или создаст новый под. События дождутся в базе.

**Как приложение масштабируется?** Горизонтально: `kubectl scale` или HPA (по CPU). Сервисы без состояния,
поэтому копии взаимозаменяемы, и Service делит между ними трафик. Подписчики событий не дублируют работу:
события обрабатывает та копия, что захватила блокировку строки (`FOR UPDATE SKIP LOCKED`).

**Чем Docker отличается от виртуальной машины?** ВМ эмулирует целый компьютер со своим ядром ОС: она тяжёлая и
стартует минутами. Контейнер — изолированный процесс на общем ядре хоста: лёгкий, стартует за секунды, образ
весит 150 МБ, а не гигабайты.

**Зачем нужен Kubernetes?** Он сам поддерживает нужное состояние: заданное число копий, перезапуск упавших,
обновление без простоя, балансировка, масштабирование, раздача конфигов и секретов. Docker Compose просто
запускает контейнеры на одной машине.

**Что делает Deployment?** Описывает желаемое состояние: какой образ, сколько копий (`replicas`), с какими
настройками. Его контроллер через ReplicaSet создаёт и пересоздаёт поды, а также делает плавное обновление
(RollingUpdate).

**Чем Deployment отличается от Pod?** Pod — одна запущенная копия (один или несколько контейнеров с общей сетью).
Deployment — «менеджер», который следит, чтобы нужное число подов существовало. Pod, созданный вручную, после
удаления не вернётся; под из Deployment — вернётся.

**Что делает Service?** Даёт постоянное имя и адрес (`order-service:8000`) группе подов, которые он находит
по меткам (`selector`), и распределяет между ними запросы. Поды меняют IP, а адрес Service остаётся прежним.

**Зачем ConfigMap и Secret?** Чтобы конфигурация не была зашита в образ: один и тот же образ работает в разных
окружениях. ConfigMap — обычные настройки, Secret — пароли и ключи. Secret хранится отдельно, доступ к нему
можно ограничить правами, и он не лежит в Git (у нас он создаётся из `.env`). Важно: base64 в Secret — это
кодирование, а не шифрование.

**Что происходит при удалении Pod?** Контроллер Deployment видит, что подов меньше, чем `replicas`, и создаёт
новый. Пока новый не прошёл readinessProbe, Service не шлёт на него трафик.

**Как Kubernetes понимает, что сервис работает?** По пробам. `startupProbe` (`/health`) ждёт запуска до 60 с.
`livenessProbe` (`/health`) проверяет, жив ли процесс: если нет, контейнер перезапускается. `readinessProbe`
(`/ready`) проверяет, доступны ли база и шина событий: если нет, под убирается из Service, но не убивается.

**Зачем resources requests/limits?** `requests` — сколько ресурсов зарезервировать (по ним планировщик выбирает
узел, по ним считает HPA). `limits` — потолок: CPU сверх лимита притормаживается, память сверх лимита —
контейнер убивается (OOMKilled). Значения подобраны по факту: сервис ест ~60 МиБ, лимит 256 МиБ.

**Почему StatefulSet для базы, а не Deployment?** Базе нужны постоянное имя (`postgres-0`) и свой диск (PVC),
который переживает перезапуск пода. Deployment для этого не предназначен.

**Где хранятся секреты и как они попадают в контейнер?** В `.env` (в `.gitignore`), откуда скрипт делает
Kubernetes Secret. В манифесте: `valueFrom.secretKeyRef`, то есть переменная окружения берётся из ключа
Secret'а. В образах и в коде паролей нет: без `JWT_SECRET` сервис не запустится.

## 4. Шпаргалка команд

```powershell
kubectl get pods -n shophub -o wide
kubectl describe pod -n shophub <pod>              # события, причины рестартов, пробы
kubectl logs -n shophub <pod> [--previous]         # логи (предыдущего запуска)
kubectl apply -k k8s                               # применить все манифесты
kubectl apply -f k8s/configmaps/shophub-config.yaml
kubectl rollout restart deployment/order-service -n shophub
kubectl rollout status deployment/order-service -n shophub
kubectl scale deployment order-service -n shophub --replicas=3
kubectl delete pod -n shophub <pod>
kubectl get events -n shophub --sort-by=.lastTimestamp
kubectl delete namespace shophub                   # удалить всё (вместе с данными базы)
```
