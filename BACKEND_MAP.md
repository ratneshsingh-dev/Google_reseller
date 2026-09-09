# Backend Architecture Map

A quick reference for debugging. Open the exact file for the feature that's broken.

---

## How a request flows (top to bottom)

```
Browser / Postman
      │
      ▼
app/main.py              ← server entry point, registers all routers, CORS, error handlers
      │
      ▼
app/api/routes/          ← HTTP layer — validates input, calls service, returns response
      │
      ▼
app/services/            ← business logic — orchestrates the workflow
      │
      ▼
app/adapters/google/     ← Google API calls (Reseller API, Directory API, Gmail API)
      │
      ▼
app/repositories/        ← Firestore read/write
```

---

## File-by-file Guide

### Entry Point

| File | Responsibility | Debug when... |
|------|---------------|---------------|
| [`app/main.py`](app/main.py) | Server startup, router registration, CORS, global error handlers | Server won't start, 500 errors on all routes |
| [`app/.env`](.env) | All environment config (credentials, admin emails, JWT secret) | Wrong emails, wrong project, auth not working |

---

### API Routes (HTTP layer)

> These files are thin — they validate the request and call the service. No business logic here.

| File | Routes it handles | Debug when... |
|------|------------------|---------------|
| [`app/api/routes/auth.py`](app/api/routes/auth.py) | `POST /api/v1/auth/google` — admin login via Google OAuth<br>`GET /api/v1/auth/me` — get current admin session<br>`POST /api/v1/auth/logout`<br>`POST /api/v1/auth/dev-login` | Admin can't log in, wrong email rejected, session broken |
| [`app/api/routes/reseller_auth.py`](app/api/routes/reseller_auth.py) | `POST /api/v1/reseller/auth/token` — API token via client_id+secret<br>`POST /api/v1/reseller/auth/email-login` — portal login<br>`POST /api/v1/reseller/auth/google-login` — portal Google login | Channel partner can't log in, wrong 401/403 |
| [`app/api/routes/reseller_api.py`](app/api/routes/reseller_api.py) | `POST /api/v1/reseller/provision` — provision via API/Manual/CSV<br>`GET /api/v1/reseller/provision/{job_id}` — job status<br>`GET /api/v1/reseller/quota` — licence quota<br>`GET /api/v1/reseller/companies` — list companies | Manual form/CSV/API submission fails, licence cap errors, companies not showing |
| [`app/api/routes/provisioning.py`](app/api/routes/provisioning.py) | `POST /api/v1/provision` — admin provisioning form<br>`GET /api/v1/provision/{id}` — admin job status | Admin provisioning form fails |
| [`app/api/routes/admin.py`](app/api/routes/admin.py) | `GET /api/v1/admin/dashboard/summary`<br>`POST /api/v1/admin/resellers` — create partner<br>`PATCH /api/v1/admin/resellers/{id}` — update partner<br>`DELETE /api/v1/admin/resellers/{id}` | Admin dashboard broken, can't create/edit/delete channel partners |
| [`app/api/routes/companies.py`](app/api/routes/companies.py) | `GET /api/v1/companies` — admin company list<br>`GET /api/v1/companies/{id}` — company detail | Companies missing from admin view |

---

### Services (Business Logic)

| File | Responsibility | Debug when... |
|------|---------------|---------------|
| [`app/services/provisioning_service.py`](app/services/provisioning_service.py) | **Core orchestrator** — runs the full provisioning workflow:<br>1. Validate → 2. Create Customer → 3. Create Subscription<br>4. Create Users → 5. Send Email | Job gets stuck, wrong step fails, email not sent, users not created |
| [`app/services/reseller_service.py`](app/services/reseller_service.py) | Licence cap enforcement, quota calculation | Wrong licence count, cap not being enforced |
| [`app/services/directory_service.py`](app/services/directory_service.py) | Interface to Google Directory API (create users) | Users not being created, user creation errors |
| [`app/services/email_service.py`](app/services/email_service.py) | Interface to Gmail API (send confirmation emails) | Emails not sending, wrong email content |

---

### Google API Adapters

| File | Responsibility | Debug when... |
|------|---------------|---------------|
| [`app/adapters/google/google_reseller.py`](app/adapters/google/google_reseller.py) | Creates Google Workspace customers & subscriptions via Reseller API | `CREATE_CUSTOMER` or `CREATE_SUBSCRIPTION` step fails |
| [`app/adapters/google/google_directory.py`](app/adapters/google/google_directory.py) | Creates user accounts via Admin Directory API | `CREATE_USERS` step fails |
| [`app/adapters/google/gmail_email.py`](app/adapters/google/gmail_email.py) | Sends email via Gmail API (DWD as `Admin@supportnation.co.in`) | Email not arriving, goes to spam, `SEND_EMAIL` step fails |

---

### Repositories (Firestore)

> Each repository handles exactly one Firestore collection.

| File | Collection | Debug when... |
|------|-----------|---------------|
| [`app/repositories/reseller_repository.py`](app/repositories/reseller_repository.py) | `resellers` | Partner not found, quota wrong |
| [`app/repositories/job_repository.py`](app/repositories/job_repository.py) | `provisioning_jobs` | Job not found, status not updating |
| [`app/repositories/company_repository.py`](app/repositories/company_repository.py) | `companies` | Company not showing up in portal |
| [`app/repositories/subscription_repository.py`](app/repositories/subscription_repository.py) | `subscriptions` | Wrong plan/SKU in company details |
| [`app/repositories/employee_repository.py`](app/repositories/employee_repository.py) | `employees` | Users not saved to Firestore |
| [`app/repositories/audit_repository.py`](app/repositories/audit_repository.py) | `audit_logs` | Audit trail missing |
| [`app/repositories/notification_repository.py`](app/repositories/notification_repository.py) | `notifications` | Email notification history missing |
| [`app/repositories/firestore_client.py`](app/repositories/firestore_client.py) | Firestore connection & raw CRUD | All DB calls fail, connection errors |

---

### Core (Security, Auth, Config)

| File | Responsibility | Debug when... |
|------|---------------|---------------|
| [`app/core/auth_middleware.py`](app/core/auth_middleware.py) | `require_admin` — admin session cookie check<br>`require_reseller_token` — JWT Bearer check<br>`require_role` — RBAC role enforcement<br>`check_licence_cap` — licence limit check | 401/403 errors on protected routes |
| [`app/core/jwt_service.py`](app/core/jwt_service.py) | Create/decode/verify JWT tokens | Token expired, invalid token errors |
| [`app/core/config.py`](app/core/config.py) | Loads `.env` into Settings object | Config not loading, wrong values |
| [`app/core/exceptions.py`](app/core/exceptions.py) | All custom exception classes | Understanding what error codes mean |
| [`app/core/security.py`](app/core/security.py) | Password hashing, email generation, temporary password | Wrong password generated, email format wrong |

---

## Common Debugging Scenarios

### ❌ "Channel partner can't log in to portal"
1. Check [`reseller_auth.py`](app/api/routes/reseller_auth.py) — does the email match a registered partner?
2. Check Firestore `resellers` collection — is `status: ACTIVE`?
3. Check `access_methods` — is it set to `MANUAL` only? (blocks portal access)

### ❌ "Provisioning job stuck at PENDING"
1. Check server logs — the background worker logs every step
2. Open [`provisioning_service.py`](app/services/provisioning_service.py) — find the step that failed
3. Check [`google_reseller.py`](app/adapters/google/google_reseller.py) for customer/subscription errors

### ❌ "Email not received"
1. Check spam folder first
2. Open [`gmail_email.py`](app/adapters/google/gmail_email.py) — verify DWD credentials
3. Check Firestore `notifications` collection for `message_id` (if present, Gmail accepted it)

### ❌ "Company not showing in portal"
1. Check Firestore `companies` collection — does the record have `reseller_id` set?
2. If `reseller_id` is `None`, run the fix: set it to the correct `RSL-XXXXXXXX` value

### ❌ "Licence cap not working"
1. Check [`auth_middleware.py`](app/core/auth_middleware.py) → `check_licence_cap()`
2. Check the reseller's `max_licence_cap` and `licences_used` in Firestore

### ❌ "Admin email rejected on login"
1. Check `.env` → `ADMIN_EMAILS` list
2. Check [`auth.py`](app/api/routes/auth.py) → `google_login()` — it validates against this list
