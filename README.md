# Google Workspace Automated Provisioning System

A production-style backend for automated Google Workspace company onboarding with mock API support.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                  REST API Layer                      │
│   POST /api/v1/provision  │  POST /api/v1/provision/csv │
│   GET  /api/v1/provision/{id}  │  GET /api/v1/companies │
└─────────────┬───────────────────────────────────────┘
              │
┌─────────────▼───────────────────────────────────────┐
│            Provisioning Orchestrator                 │
│  validate → customer → subscription → users → email │
└──────┬──────────┬──────────┬────────────────────────┘
       │          │          │
┌──────▼──┐ ┌────▼────┐ ┌───▼────┐
│ Reseller│ │Directory│ │ Email  │   ← Service Interfaces (ABC)
│ Service │ │ Service │ │Service │
└──┬───┬──┘ └──┬───┬──┘ └─┬──┬──┘
   │   │       │   │       │  │
 Mock Google  Mock Google Mock Gmail  ← Adapter Implementations
```

**Key design**: Swap `SERVICE_ADAPTER=mock` → `SERVICE_ADAPTER=google` to use real APIs without changing business logic.

## Quick Start

```bash
# 1. Create virtual environment
python -m venv venv
venv\Scripts\activate   # Windows
# source venv/bin/activate  # Linux/Mac

# 2. Install dependencies
pip install -r requirements.txt

# 3. Copy environment config
copy .env.example .env    # Windows
# cp .env.example .env    # Linux/Mac

# 4. Run the server
uvicorn app.main:app --reload

# 5. Open API docs
# http://localhost:8000/docs
```

## API Endpoints

### Provisioning

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/v1/provision` | Submit company provisioning request |
| `POST` | `/api/v1/provision/csv` | Bulk provisioning via CSV upload |
| `GET`  | `/api/v1/provision/{job_id}` | Get provisioning job status |

### Companies

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/companies` | List all companies |
| `GET` | `/api/v1/companies/{id}` | Company detail with subscription |
| `GET` | `/api/v1/companies/{id}/users` | List company employees |

### Mock APIs (Debug)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/mock/reseller/v1/customers` | Create mock customer |
| `GET`  | `/mock/reseller/v1/customers/{id}` | Get mock customer |
| `POST` | `/mock/reseller/v1/customers/{id}/subscriptions` | Create subscription |
| `POST` | `/mock/admin/directory/v1/users` | Create mock user |
| `GET`  | `/mock/admin/directory/v1/users?domain=` | List users by domain |

## Example: Provision a Company

```bash
curl -X POST http://localhost:8000/api/v1/provision \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: my-unique-key" \
  -d '{
    "company_name": "ABC Pvt Ltd",
    "primary_domain": "abc-example.com",
    "alternate_email": "founder@gmail.com",
    "contact_name": "John Doe",
    "postal_address": {
      "address_line1": "123 Example Street",
      "locality": "Bengaluru",
      "region": "KA",
      "postal_code": "560001",
      "country_code": "IN"
    },
    "plan": "FLEXIBLE",
    "sku_id": "SKU-BUSINESS-STANDARD",
    "license_count": 5,
    "initiated_by_email": "developer@example.net",
    "econz_notification_email": "econz-notify@example.net",
    "employees": [
      {"first_name": "Rahul", "last_name": "Sharma", "personal_email": "rahul@example.net"},
      {"first_name": "Priya", "last_name": "Patel", "personal_email": "priya@example.net"},
      {"first_name": "Amit", "last_name": "Kumar", "personal_email": "amit@example.net"}
    ]
  }'
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `USE_FIRESTORE` | `false` | Use Firestore (`true`) or in-memory (`false`) |
| `SERVICE_ADAPTER` | `mock` | `mock` or `google` |
| `MOCK_FAILURE_MODE` | `false` | Enable failure simulation |
| `MOCK_FAILURE_RATE` | `0.0` | Failure probability (0.0–1.0) |
| `GOOGLE_CLOUD_PROJECT` | `testing-ratnesh` | GCP project ID |

## Testing

```bash
pytest tests/ -v
```

## CSV Format

See `data/sample_companies.csv` for a 10-company example. Required columns:

```
company_name, primary_domain, alternate_email, contact_name,
address_line1, locality, region, postal_code, country_code,
plan, sku_id, license_count, initiated_by_email,
econz_notification_email, employees_json
```

## Project Structure

```
app/
├── main.py                      # FastAPI application
├── dependencies.py              # Dependency injection
├── api/routes/                  # REST endpoints
│   ├── provisioning.py          # Provisioning API
│   ├── companies.py             # Company API
│   ├── mock_reseller.py         # Mock Reseller endpoints
│   └── mock_directory.py        # Mock Directory endpoints
├── models/                      # Pydantic models
│   ├── requests.py              # API request models
│   ├── responses.py             # API response models
│   ├── database.py              # Firestore document models
│   └── google_api.py            # Google API compatible models
├── services/                    # Service interfaces (ABC)
│   ├── reseller_service.py
│   ├── directory_service.py
│   ├── email_service.py
│   └── provisioning_service.py  # Orchestrator
├── adapters/
│   ├── mock/                    # Mock implementations
│   │   ├── mock_reseller.py
│   │   ├── mock_directory.py
│   │   └── mock_email.py
│   └── google/                  # Real API placeholders
│       ├── google_reseller.py
│       ├── google_directory.py
│       └── gmail_email.py
├── repositories/                # Data access layer
│   ├── firestore_client.py      # Storage backend
│   ├── company_repository.py
│   ├── subscription_repository.py
│   ├── employee_repository.py
│   ├── job_repository.py
│   └── notification_repository.py
├── workers/                     # Background processing
│   └── provisioning_worker.py
└── core/                        # Cross-cutting concerns
    ├── config.py
    ├── logging.py
    ├── exceptions.py
    └── security.py
```

## Switching to Real Google APIs

1. Implement `GoogleResellerService` in `app/adapters/google/google_reseller.py`
2. Implement `GoogleDirectoryService` in `app/adapters/google/google_directory.py`
3. Implement `GmailEmailService` in `app/adapters/google/gmail_email.py`
4. Set `SERVICE_ADAPTER=google` in `.env`
5. No changes needed to orchestration, routes, models, or repositories
