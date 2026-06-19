# NetHunt - HelpCrunch Bridge

Інтеграційний міст між HelpCrunch (чат-платформа) та NetHunt CRM. Автоматично синхронізує клієнтів, чати, угоди та UTM-мітки між двома системами.

## Можливості

- **Автоматична обробка вебхуків** від HelpCrunch (`chat.new`, `customer.new`, `message.chat.customer`).
- **Дедуплікація контактів** за HelpCrunch ID, email, телефоном, Telegram або Instagram.
- **Локальне дзеркало CRM/HC** для повної історії та швидкого матчингу нових чатів.
- **Двостороння синхронізація**:
  - Записує лінк на діалог HelpCrunch у картку NetHunt.
  - Оновлює нотатки та приватні повідомлення в HelpCrunch інформацією про угоди з CRM.
- **Панель моніторингу** з логами, метриками та налаштуваннями.
- **Адміністраторська авторизація** з TOTP 2FA.

## Стек технологій

- Python 3.11 + FastAPI
- SQLite (локальна база налаштувань, логів та дзеркала)
- Uvicorn + Docker
- httpx для роботи з API
- pyotp + segno для 2FA та QR-кодів

## Швидкий старт

### Локально

```bash
pip install -r requirements.txt
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8091
```

Відкрийте `http://127.0.0.1:8091` і зареєструйте першого адміністратора (2FA обов’язкова).

### Через Docker

```bash
cp .env.example .env
# Заповніть .env реальними ключами

docker-compose up -d --build
```

Додаток буде доступний на порту `8091`.

## Налаштування

Усі налаштування зберігаються в SQLite (`data/bridge.db` для Docker, `backend/bridge.db` для локального запуску). Введіть їх через веб-інтерфейс або використовуйте `.env` як шаблон.

### Обов’язкові параметри

- **HelpCrunch API Key** — ключ з налаштувань HelpCrunch.
- **HelpCrunch Subdomain** — ваш субдомен (наприклад, `company`).
- **NetHunt API Email** — email облікового запису NetHunt.
- **NetHunt API Key** — API-ключ NetHunt.
- **NetHunt Contacts Folder** — ID папки контактів у NetHunt.
- **NetHunt Deals Folder** — ID папки угод (опціонально).

### Маппінг полів

За замовчуванням:

- Email → `Email адреса`
- Phone → `Телефон`
- Telegram → `Telegram`
- Instagram → `Instagram`
- Лінк на діалог → `Лінк на HelpCrunch`
- HelpCrunch ID → `HelpCrunch ID`

Пріоритет пошуку: `email,phone,telegram` (налаштовується).

## API ендпоінти

### Публічні

- `POST /api/webhook` — приймає вебхуки HelpCrunch. Підпис перевіряється за заголовком `X-HelpCrunch-Signature`, якщо встановлено `helpcrunch_webhook_secret`.

### Захищені (потребують авторизації)

- `GET /api/settings` / `POST /api/settings` — отримати/зберегти налаштування.
- `GET /api/logs` / `GET /api/metrics` — логи та метрики синхронізації.
- `POST /api/sync/full` — запуск повної історичної синхронізації в фоні.
- `GET /api/sync/stats` — кількість записів у локальному дзеркалі.
- `POST /api/test-nethunt` / `POST /api/test-helpcrunch` — перевірка з’єднання.
- `POST /api/simulate-webhook` — ручна симуляція вебхука для тестування.

## Повна синхронізація

Перед роботою рекомендується запустити повну синхронізацію, щоб локальне дзеркало містило всі існуючі контакти, чати та угоди:

```bash
curl -X POST http://localhost:8091/api/sync/full \
  -H "Content-Type: application/json" \
  -b session_id=<your_session_cookie>
```

Або через веб-інтерфейс.

## Тестування

```bash
# Авторизація та 2FA
python test_auth.py

# Симуляція вебхука
python test_sync.py "John Doe" "john@example.com" "+380501112233" "johndoe_tg"
```

## Деплой на продакшн

Детальні інструкції — у файлі `DEPLOY.md`. Коротко:

1. Розпакувати `deploy-package.zip` на сервері.
2. Заповнити `.env` і перенести значення в налаштування через веб-інтерфейс.
3. Запустити `docker-compose up -d --build`.
4. Налаштувати вебхук HelpCrunch на `https://<domain>/api/webhook`.
5. Запустити повну синхронізацію.

## Безпека

- `.env` і файли бази даних не повинні потрапляти в Git (див. `.gitignore`).
- API-ключі та TOTP-секрети зберігаються в SQLite.
- Сесійний секрет зберігається в таблиці `session_keys`, тому перезапуск контейнера не розлогінює адміністратора.

## Ліцензія

MIT
