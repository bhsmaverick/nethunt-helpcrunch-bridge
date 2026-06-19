# Implementation Plan - Bridge Refactoring and Bug Fixes

This plan outlines the proposed refactoring and bug fixes for the NetHunt-HelpCrunch Bridge service to resolve key issues and improve system reliability.

## Proposed Changes

We will modify two backend files to fix bugs, persist user sessions across server restarts, and make background synchronization tasks resilient to unhandled exceptions.

---

### [Component: Backend Services & Authentication]

#### [MODIFY] [nethunt.py](file:///c:/Users/bhsma/Projects/nethunt-helpcrunch-bridge/backend/services/nethunt.py)
- Normalize the dictionary returned by `create_contact` so that the record ID is always accessible via the `"id"` key (copying it from NetHunt's Zapier-specific `"recordId"` key).

#### [MODIFY] [auth.py](file:///c:/Users/bhsma/Projects/nethunt-helpcrunch-bridge/backend/auth.py)
- Persist the `SESSION_SECRET` in a dedicated SQLite table `session_keys` instead of generating a random key in-memory on every startup. This prevents active user sessions from being invalidated whenever the FastAPI application restarts or reloads.

#### [MODIFY] [main.py](file:///c:/Users/bhsma/Projects/nethunt-helpcrunch-bridge/backend/main.py)
- Wrap the background synchronization task (`process_sync_task`) in a global `try...except` block so that any network, API, or parsing exceptions are gracefully caught, logged to the database, and reflected in the dashboard UI.
- Update contact name resolution logic to fallback to `contact.get("fields", {}).get("Name")` if the root `"name"` key is missing in the NetHunt record response.

---

## Verification Plan

### Automated Tests
- Run `python test_auth.py` to verify that login, registration, and session token verification function correctly.
- Run `python test_sync.py` to trigger a simulated webhook and verify that the sync completes successfully and updates the CRM contact cards and logs.

### Manual Verification
- Restart the docker container (`docker-compose restart`) and verify that we remain logged in on the web dashboard (verifying the persistent session secret).
- Inspect the logs dashboard on the frontend to ensure syncs are logged correctly.
