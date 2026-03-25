# Insighta 2.0

Insighta is a FastAPI-based two-sided platform for publishing surveys and interviews, matching qualified participants, collecting responses, managing payouts, and handling feedback.  
This version also includes:

- **email verification for registration**
- **password reset with email verification**
- **registration password policy enforcement**

The project uses **FastAPI + Jinja2 + SQLAlchemy** and currently runs as a server-rendered monolith.

---

## 1. Core capabilities

### Authentication and account flows
- Email/password registration
- Email verification code during registration
- Login with cookie-based session
- Forgot-password flow with email verification code
- Registration password policy enforcement:
  - at least 8 characters
  - at least 1 uppercase letter
  - at least 1 lowercase letter
  - at least 1 digit
  - at least 1 special character

### Participant profile system
Users can fill a detailed participant profile, including:
- age range
- education level
- field/status/state
- ethnicity
- mental/physical health diagnosis
- sexual orientation
- sport type/frequency
- smoking / cannabis use
- language
- student status / year in school
- international or domestic
- experience tags
- participation format
- device type

### Survey and interview publishing
Publishers can:
- create surveys
- create interviews
- define targeting filters
- upload a cover image
- define time, reward, and response volume
- calculate pricing using platform commission rules
- publish, close, reopen, and edit listings

### Matching and participant side
Participants can:
- browse matched survey/interview opportunities
- start a task
- complete a task
- modify a completed response back to started state
- view dashboard stats
- connect Stripe and withdraw earnings

### Publisher review flow
When a participant completes a survey/interview:
- a `Response` record is updated
- a `Notification` is created for the publisher
- the publisher can accept or reject the completion

### Feedback and admin
- users can submit feedback
- admin can review feedback
- admin can credit a user balance
- admin can reject feedback

### AI-assisted publishing
- `/api/ai-fill` uses Anthropic to generate draft publishing form content from a natural-language prompt

---

## 2. Tech stack

- **Backend:** FastAPI
- **Templating:** Jinja2
- **ORM:** SQLAlchemy
- **Database:** SQLite by default, PostgreSQL supported via `DATABASE_URL`
- **Password hashing:** Passlib + bcrypt
- **Payments:** Stripe Checkout + Stripe Connect
- **Email:** Gmail SMTP
- **AI:** Anthropic API
- **Runtime server:** Uvicorn

---

## 3. Project structure

```text
Insighta-main/
├── api/
│   └── main.py                 # main FastAPI application and most business logic
├── app/
│   ├── database.py            # database engine/session setup
│   ├── models.py              # SQLAlchemy models
│   ├── schemas.py             # limited pydantic schemas
│   ├── core.py                # legacy/older logic file
│   ├── templates/             # Jinja2 HTML templates
│   └── static/                # CSS, images, uploads
├── migrate_add_columns.py     # old schema patch script
├── requirements.txt
├── README.md
├── survey.db                  # default SQLite database
├── survey.db.bak              # backup db (may exist locally)
├── surveybridge.db            # legacy db file
└── vercel.json
```

---

## 4. Main database models

### `User`
Stores:
- authentication fields (`email`, `password`)
- participant profile attributes
- Stripe account state
- earnings balance

### `Survey`
Stores:
- publisher ownership
- survey/interview metadata
- targeting rules
- reward and budget data
- status and payment state

### `Response`
Stores:
- participant progress per survey/interview
- started/completed status
- payout state

### `Notification`
Stores:
- publisher-side review items for completed submissions

### `Feedback`
Stores:
- user feedback tickets
- review/credit state

### `EmailVerificationCode`
Stores:
- registration verification codes
- password reset verification codes
- expiry and usage state

---

## 5. Environment variables

Create a `.env` file in the project root.

```env
DATABASE_URL=sqlite:///./survey.db

STRIPE_SECRET_KEY=
STRIPE_PUBLISHABLE_KEY=
STRIPE_WEBHOOK_SECRET=

EMAIL_ADDRESS=insightacom@gmail.com
EMAIL_PASSWORD=babx pysm yevt tamq

ANTHROPIC_API_KEY=
ADMIN_KEY=insighta-admin
```

### Notes
- If `DATABASE_URL` is not provided, the project uses `sqlite:///./survey.db`
- Stripe features require valid Stripe keys
- Anthropic autofill requires `ANTHROPIC_API_KEY`
- The current code defaults to Gmail SMTP using `smtp.gmail.com:465`

---

## 6. Installation

### Windows
```bat
python -m venv venv
venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
```

### Linux / macOS
```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

---

## 7. Run locally

```bash
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```

Then open:

- Home: `http://127.0.0.1:8000/`
- Login: `http://127.0.0.1:8000/login`
- Register: `http://127.0.0.1:8000/register`

---

## 8. Current authentication behavior

### Registration
Registration requires:
- email
- password + confirm password
- verification code sent to email

The backend validates the password using the following policy:
- minimum 8 characters
- at least one uppercase letter
- at least one lowercase letter
- at least one digit
- at least one special character

If the password does not satisfy the rule, registration is blocked and the user sees a validation message.

### Login
Login uses:
- email
- password
- server-side password verification via bcrypt/passlib
- `user_id` cookie on success

### Forgot password
The login page includes a password reset flow:
- send verification code to email
- verify code
- submit new password

**Current code behavior:** password reset currently only enforces a minimum length of 6 characters on the new password.
It does **not yet reuse the full registration password policy** unless you explicitly extend it.

---

## 9. Main user flows

### Participant flow
1. Register and verify email
2. Complete profile
3. Browse matched opportunities
4. Start a survey/interview
5. Complete submission
6. Wait for publisher review
7. Withdraw approved earnings through Stripe Connect

### Publisher flow
1. Login
2. Publish survey/interview
3. Complete payment (for survey publishing flow)
4. Review completion notifications
5. Accept or reject submissions
6. Manage listing state (publish / close / reopen / edit)

### Admin flow
1. Open admin page
2. Use `ADMIN_KEY`
3. Review feedback items
4. Credit or reject feedback

---

## 10. Key routes

### Public/auth routes
- `GET /`
- `GET /login`
- `POST /login`
- `GET /register`
- `POST /register`
- `POST /auth/send-code`
- `POST /password-reset`

### Participant routes
- `GET /choice`
- `GET /dashboard`
- `GET /api/dashboard/stats`
- `POST /surveys/{survey_id}/start`
- `POST /surveys/{survey_id}/complete`
- `POST /surveys/{survey_id}/modify`
- `POST /api/withdraw`
- `GET /profile`
- `POST /profile`

### Publisher routes
- `GET /publisher`
- `GET /publish`
- `POST /publish`
- `GET /publish_interview`
- `POST /publish_interview`
- `POST /api/calculate-price`
- `GET /publisher/edit/{survey_id}`
- `POST /publisher/edit/{survey_id}`
- `POST /surveys/{survey_id}/publish`
- `POST /surveys/{survey_id}/close`
- `POST /surveys/{survey_id}/reopen`
- `POST /publisher/delete/{survey_id}`
- `GET /api/notifications`
- `POST /api/notifications/{notif_id}/accept`
- `POST /api/notifications/{notif_id}/reject`
- `GET /api/publisher/pending-responses`

### Payment / Stripe routes
- `GET /payment/success`
- `POST /webhook/stripe`
- `GET /connect/onboard`
- `GET /connect/complete`

### AI / feedback / admin routes
- `POST /api/ai-fill`
- `GET /feedback`
- `POST /feedback`
- `GET /admin`
- `GET /admin/feedbacks`
- `POST /admin/feedback/{feedback_id}/credit`
- `POST /admin/feedback/{feedback_id}/reject`

---

## 11. Pricing logic

Current commission logic in `api/main.py`:

- gross reward per person `>= 25` → `25%` commission
- gross reward per person `>= 15` → `20%` commission
- otherwise → `15%` commission

Publisher-facing calculation API:
- `POST /api/calculate-price`

Returned values include:
- `per_person_gross`
- `commission_rate`
- `commission_pct`
- `reward_amount`
- `total_budget`

---

## 12. Email verification details

Verification code logic uses table `email_verification_codes` and supports two purposes:
- `register`
- `reset_password`

Behavior:
- old unused codes for the same email/purpose are marked as used when a new code is issued
- codes expire after 10 minutes
- code is consumed after successful validation

---

## 13. Database notes

### Default mode
By default the project will create tables automatically:

```python
Base.metadata.create_all(bind=engine)
```

### Important compatibility note
If you use an old existing `survey.db`, it may be missing newer columns such as:
- `student_status`
- other newer profile fields
- `email_verification_codes` table

In that case, the app may fail even before email sending.  
For a clean local run, either:
- rename/remove the old `survey.db` and let the app recreate it, or
- run a schema migration manually

---

## 14. Known issues / current limitations

These are behaviors present in the current codebase and worth knowing before deployment.

### 1) Gmail SMTP reachability depends on network
The project uses Gmail SMTP by default:
- host: `smtp.gmail.com`
- port: `465`
- mode: SSL

If your local network cannot reach Gmail SMTP, verification emails will fail.
Typical symptom:
- backend logs show `Email error: ...`
- request may still appear to return success depending on current code path

### 2) Email sending currently prints errors instead of raising them
`send_email()` catches exceptions and only prints:

```python
print(f"Email error: {e}")
```

So email delivery failure may not always propagate to the frontend as a hard failure.

### 3) Password reset policy is weaker than registration policy
Registration uses the full strong password rule.  
Password reset currently only checks that the new password length is at least 6.

### 4) Cookie/session model is simple
Login currently sets a `user_id` cookie directly.  
This is acceptable for local development/prototyping, but should be strengthened before production.

### 5) Old DB files and cached artifacts are present in the repository package
The zip currently includes:
- `.pyc` files
- SQLite db files
- backup db files

For a cleaner repository, these should normally be excluded via `.gitignore`.

---

## 15. Suggested `.gitignore`

```gitignore
__pycache__/
*.pyc
*.pyo
*.pyd

venv/
.venv/

.env

survey.db
survey.db.bak
surveybridge.db

app/static/uploads/
```

---

## 16. Deployment notes

Before production deployment, strongly consider:
- switching from SQLite to PostgreSQL
- adding proper database migrations
- returning hard API failures when email sending fails
- moving SMTP credentials fully into environment variables
- strengthening auth/session security
- cleaning duplicate/legacy files such as `app/core.py` if no longer used
- removing local DB files and cache files from the repository

---

## 17. Development status summary

This version should be understood as a **working prototype / evolving product build**, not a fully hardened production backend.

It already contains a full functional loop for:
- account creation
- participant profiling
- opportunity publishing
- matching
- response completion
- publisher review
- payout preparation
- user feedback
- admin credit workflow
- AI-assisted publishing

It is suitable for continued iteration, debugging, feature expansion, and controlled deployment after cleanup.

---

## 18. Recommended version label

Suggested release name for this state:

```text
Insighta 2.0
```

Suggested release summary:

```text
Added registration email verification, password reset with verification code, and strong password validation for registration.
```
