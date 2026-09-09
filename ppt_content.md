# Google Workspace Automation — Presentation Content (10 Slides)

> Copy the content below into your PowerPoint slides. Diagrams are described in text — you can recreate them using SmartArt or shapes in PowerPoint.

---

## Slide 1 — Title Slide

**Title:** Google Workspace Automated Provisioning System

**Subtitle:** End-to-End Customer Onboarding & License Management via Reseller API

**Presented by:** Ratnesh Singh

**Date:** September 2026

---

## Slide 2 — Problem Statement & Purpose

**Why Are We Building This?**

| Current (Manual) Process | Proposed (Automated) Solution |
|---|---|
| Admin manually creates Google Workspace customer accounts | One-click automated customer creation via Reseller API |
| Subscriptions & licenses assigned manually per customer | Auto-subscription ordering with correct SKU and seat count |
| User accounts (emails, passwords) created one-by-one in Admin Console | Bulk user provisioning via Admin SDK Directory API |
| Welcome emails sent manually with credentials | Automated welcome email with admin credentials via Gmail API |
| Error-prone, slow, not scalable | Idempotent, fault-tolerant, real-time tracking |

**Purpose:** Build a self-service internal tool that allows reseller admins to onboard a new company onto Google Workspace — from customer creation to user provisioning to email notification — in a single automated workflow.

---

## Slide 3 — Scope of Work

**In Scope:**

- ✅ Web-based UI for submitting provisioning requests (company details, admin info, employee list)
- ✅ Google Reseller API integration — create customer, order subscription (Business Standard/Starter, 30-day trial or annual)
- ✅ Admin SDK Directory API integration — bulk create user accounts with generated corporate emails and temporary passwords
- ✅ Gmail API integration — send automated welcome emails to initiator and newly created admin
- ✅ Real-time job tracking dashboard with step-by-step progress
- ✅ Idempotency & error handling — safe to retry failed jobs
- ✅ CSV upload support for bulk employee provisioning
- ✅ Cloud deployment on Google Cloud Run

**Out of Scope (Future Phases):**

- ❌ Multi-reseller / multi-tenant support
- ❌ Billing & invoice management
- ❌ Domain verification automation
- ❌ Role-based access control (RBAC) beyond Google OAuth

---

## Slide 4 — Tech Stack

| Layer | Technology | Purpose |
|---|---|---|
| **Frontend** | HTML5, CSS3, Vanilla JavaScript | Responsive single-page UI with real-time polling |
| **Backend** | Python 3.12, FastAPI | High-performance async REST API framework |
| **Database** | Google Cloud Firestore | NoSQL document store for jobs, companies, employees |
| **Authentication** | Google OAuth 2.0 (Sign-In) | Secure admin login via Google Identity |
| **Google APIs** | Reseller API v1, Admin SDK Directory API v1, Gmail API v1 | Core business integrations |
| **Auth Mechanism** | Service Account + Domain-Wide Delegation (DWD) | Server-to-server API access without user interaction |
| **Deployment** | Google Cloud Run + Secret Manager + Artifact Registry | Serverless container hosting with secure credential management |
| **Dev Tools** | Docker, gcloud CLI, pytest | Containerization, deployment, testing |

---

## Slide 5 — Architecture Diagram

> **Recreate this as a diagram in PowerPoint using SmartArt or shapes:**

```
┌─────────────────────────────────────────────────────────────────┐
│                        FRONTEND (Browser)                       │
│   ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│   │  Login Page   │  │ Onboard Form │  │ Real-Time Job Tracker│  │
│   │ (Google OAuth)│  │ (Company +   │  │ (Polls /provision/   │  │
│   │              │  │  Employees)  │  │  {job_id} every 1.5s)│  │
│   └──────┬───────┘  └──────┬───────┘  └──────────┬───────────┘  │
└──────────┼─────────────────┼─────────────────────┼──────────────┘
           │ POST /auth      │ POST /provision     │ GET /provision/{id}
           ▼                 ▼                     ▼
┌─────────────────────────────────────────────────────────────────┐
│                   BACKEND (FastAPI on Cloud Run)                 │
│                                                                 │
│   ┌─────────────┐    ┌──────────────────────────────────────┐   │
│   │  Auth Routes │    │   Provisioning Orchestrator Service  │   │
│   │ (OAuth Token │    │                                      │   │
│   │  Validation) │    │  Step 1: Validate Input              │   │
│   └─────────────┘    │  Step 2: Create Customer (Reseller)  │   │
│                      │  Step 3: Order Subscription           │   │
│   ┌─────────────┐    │  Step 4: Provision Users (Admin SDK) │   │
│   │  Adapters    │    │  Step 5: Send Welcome Emails (Gmail) │   │
│   │ (Pluggable)  │    └──────────────────────────────────────┘   │
│   │ Google/Mock  │                                               │
│   └─────────────┘                                               │
└──────────┬──────────────────┬──────────────────┬────────────────┘
           │                  │                  │
           ▼                  ▼                  ▼
   ┌──────────────┐   ┌─────────────┐   ┌──────────────────┐
   │  Firestore   │   │ Google APIs │   │ Secret Manager   │
   │  (Database)  │   │ • Reseller  │   │ (credentials.json│
   │ • Jobs       │   │ • Admin SDK │   │  via DWD)        │
   │ • Companies  │   │ • Gmail     │   │                  │
   │ • Employees  │   │             │   │                  │
   └──────────────┘   └─────────────┘   └──────────────────┘
```

**Key Design Decision:** Hexagonal Architecture (Ports & Adapters) — Google API adapters are pluggable and can be swapped with mock adapters for testing.

---

## Slide 6 — Process Flow (End-to-End)

> **Recreate this as a flow diagram in PowerPoint:**

```
 ┌───────────┐     ┌──────────────┐     ┌──────────────────┐
 │   Admin    │────▶│  Login via   │────▶│  Fill Onboarding │
 │  Opens UI  │     │ Google OAuth │     │  Form / Upload   │
 └───────────┘     └──────────────┘     │  CSV             │
                                        └────────┬─────────┘
                                                 │ Submit
                                                 ▼
                                   ┌─────────────────────────┐
                                   │  Step 1: VALIDATE INPUT │
                                   │  • Check required fields│
                                   │  • Validate domain      │
                                   └────────────┬────────────┘
                                                ▼
                                   ┌─────────────────────────┐
                                   │ Step 2: CREATE CUSTOMER │
                                   │ • Reseller API call     │
                                   │ • Get Google Customer ID│
                                   └────────────┬────────────┘
                                                ▼
                                   ┌─────────────────────────┐
                                   │ Step 3: ORDER SUBSCRIPTION│
                                   │ • SKU: Business Standard│
                                   │ • Plan: Trial/Annual    │
                                   │ • Seat count allocation │
                                   └────────────┬────────────┘
                                                ▼
                                   ┌─────────────────────────┐
                                   │ Step 4: PROVISION USERS │
                                   │ • Generate corp emails  │
                                   │ • Generate temp passwords│
                                   │ • Admin SDK: create users│
                                   │ • Retry on failure      │
                                   └────────────┬────────────┘
                                                ▼
                                   ┌─────────────────────────┐
                                   │ Step 5: SEND EMAILS     │
                                   │ • Gmail API via DWD     │
                                   │ • To: Initiator + Admin │
                                   │ • Content: Credentials  │
                                   └────────────┬────────────┘
                                                ▼
                                        ┌──────────────┐
                                        │  JOB COMPLETE │
                                        │  ✅ SUCCESS   │
                                        └──────────────┘
```

**Key Feature:** Each step updates Firestore in real-time → Frontend polls every 1.5 seconds → User sees live progress.

---

## Slide 7 — Google APIs & Authentication Flow

**APIs Used:**

| API | Purpose | Auth Method |
|---|---|---|
| Google Reseller API v1 | Create customer account, order Workspace subscription | Service Account + DWD |
| Admin SDK Directory API v1 | Create user accounts under the new domain | Service Account + DWD |
| Gmail API v1 | Send welcome emails with credentials | Service Account + DWD |
| Google OAuth 2.0 | Admin login to the web UI | OAuth Client ID (browser) |

**Domain-Wide Delegation (DWD) Flow:**

1. Service Account (`reseller-api@reseller-integration.iam.gserviceaccount.com`) is created in GCP
2. DWD is enabled in Google Admin Console with required scopes
3. Backend impersonates `Admin@supportnation.co.in` to call APIs
4. No user interaction required for API calls — fully automated

---

## Slide 8 — Key Features & Design Decisions

**Features:**

- 🔄 **Idempotent Operations** — Safe to retry. If a job fails at Step 3, re-running it skips Steps 1-2 and resumes from Step 3
- 📊 **Real-Time Progress Tracking** — Live dashboard shows each step's status (Pending → In Progress → Completed/Failed)
- 🔌 **Pluggable Adapter Pattern** — Google API adapters can be swapped with mock adapters for local testing without touching real APIs
- 📧 **Dual Email Notifications** — Welcome emails sent to both the initiator and the newly created admin
- 📄 **CSV Bulk Upload** — Upload a CSV file with employee data instead of filling forms manually
- 🔒 **Secure Credential Management** — Service account keys stored in Google Secret Manager, never in code

**Design Decisions:**

| Decision | Rationale |
|---|---|
| FastAPI over Flask/Django | Async support, auto-generated API docs, high performance |
| Firestore over SQL | Schema-flexible, real-time updates, serverless scaling |
| Cloud Run over GKE/VM | Zero-config scaling, pay-per-use, built-in HTTPS |
| Hexagonal Architecture | Testability — swap real APIs with mocks in one line |

---

## Slide 9 — Deployment & Infrastructure

**Cloud Run Deployment:**

| Component | Details |
|---|---|
| **Service Name** | `workspace-provisioning` |
| **Region** | `us-central1` |
| **Project** | `reseller-integration` |
| **URL** | `https://workspace-provisioning-xxxxx.us-central1.run.app` |
| **Container** | Python 3.12-slim Docker image |
| **Secrets** | `credentials.json` mounted via Secret Manager |
| **Scaling** | Auto-scales 0 → N instances based on traffic |
| **CPU** | Always-on (no throttling) for background task support |

**CI/CD Pipeline (Future):**

```
Code Push → Cloud Build → Docker Image → Artifact Registry → Cloud Run Deploy
```

---

## Slide 10 — Summary & Expected Outcomes

**What We Built:**

A fully automated, production-ready Google Workspace provisioning system that reduces customer onboarding time from **hours of manual work** to **under 60 seconds** with a single form submission.

**Key Metrics:**

| Metric | Before (Manual) | After (Automated) |
|---|---|---|
| Time to onboard 1 customer | 2-3 hours | < 60 seconds |
| User accounts created per hour | 5-10 (manual) | 100+ (automated) |
| Error rate | High (human error) | Near-zero (idempotent retry) |
| Scalability | Limited by manpower | Unlimited (serverless) |

**Learnings:**

- End-to-end application lifecycle: Requirements → Design → Build → Test → Deploy
- Working with Google Workspace APIs (Reseller, Admin SDK, Gmail)
- Domain-Wide Delegation and Service Account authentication
- Cloud-native deployment with Docker, Cloud Run, and Secret Manager
- Hexagonal/Clean Architecture for testable, maintainable code

**Thank You!**
