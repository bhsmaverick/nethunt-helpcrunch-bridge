# Deployment Guide - NetHunt HelpCrunch Bridge

## Quick Start with Docker

1. Extract the deploy package on the production server.
2. Copy `.env.example` to `.env` and fill in the real credentials.
3. Make sure `data/bridge.db` exists (it is included in the package).
4. Run:
   ```bash
   docker-compose up -d --build
   ```
5. Open `http://<server-ip>:8091` in the browser and log in with the existing admin user.

## Environment Variables

All settings are stored in the SQLite database (`data/bridge.db`). The `.env` file is a convenient template for deployment configuration. You can either:

- Use the admin UI to enter credentials after first login, or
- Import the `.env` values into the database using a SQL script or the admin UI.

### Required variables

- `HELP_CRUNCH_API_KEY` — HelpCrunch API key.
- `HELP_CRUNCH_SUBDOMAIN` — your HelpCrunch subdomain (e.g. `mycompany`).
- `NETHUNT_API_EMAIL` — NetHunt API user email.
- `NETHUNT_API_KEY` — NetHunt API key.
- `NETHUNT_CONTACTS_FOLDER` — NetHunt folder ID for contacts (e.g. `69247846945ff8549001a07c`).
- `NETHUNT_DEALS_FOLDER` — NetHunt folder ID for deals/pipelines (optional).

### Field mapping defaults

The package is preconfigured with the field names from your NetHunt setup:

- `NH_CHAT_LINK_FIELD=Лінк на HelpCrunch`
- `SYNC_PRIORITY=email,phone,telegram`

## Database

- The production database is mounted from `./data/bridge.db` into the container at `/app/backend/bridge.db`.
- `data/bridge_dump.sql` is a full SQL dump of the current database state. You can restore it with:
  ```bash
  sqlite3 data/bridge.db < data/bridge_dump.sql
  ```

## After Deployment

1. Log in to the admin dashboard.
2. Verify settings (API keys, folders, field names).
3. Run a full sync: `POST /api/sync/full` from the UI or via curl.
4. Configure the HelpCrunch webhook to point to:
   ```
   https://<your-domain>/api/webhook
   ```
   with the secret from `HELP_CRUNCH_WEBHOOK_SECRET`.

## Backup

Back up the `data/bridge.db` file regularly. It contains all settings, the admin user, the activity log, and the local CRM/HC mirror.
