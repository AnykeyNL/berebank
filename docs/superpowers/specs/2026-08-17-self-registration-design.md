# Self-registration with BankManager approval — Design

**Date:** 2026-08-17
**Status:** Approved

## Goal

Let new people request an account themselves from the login page instead of asking the
BankManager to create one. A registration is not an account: the BankManager reviews each
request and approves it with a starting balance, or rejects it. A new **WhatsApp number**
field is captured at registration and stored on the user.

## Decisions (from brainstorming)

- **Storage:** pending registrations live in a separate `registration_requests` table, not
  in `users`. Approval creates the real `User` + `Account`; rejection deletes the request so
  the person can register again later. Pending people never appear in the users table, so
  the leaderboard, snapshots, admin user list and OAuth login need no changes.
- **Pending login:** someone with a pending request who tries to log in gets a clear
  403 message — "Your registration is awaiting approval by the bank manager" — instead of
  the generic invalid-credentials error.
- **WhatsApp number:** required at registration, validated as international format
  (`+` followed by 8–15 digits). Visible to the BankManager in the admin user list and
  editable on the user's own Profile page.

## Data model

New table `registration_requests` (created by `Base.metadata.create_all()`):

| column | type | notes |
| --- | --- | --- |
| `id` | int PK | |
| `display_name` | varchar(100) | |
| `email` | varchar(255) unique | lowercased |
| `whatsapp_number` | varchar(32) | validated international format |
| `password_hash` | varchar(255) | bcrypt — the plaintext password is never stored |
| `created_at` | datetime tz | |

New nullable column `users.whatsapp_number VARCHAR(32)`, added via an additive entry in
`migrate_schema()` in `backend/app/main.py` (the repo has no Alembic).

## Backend API

### Public (no auth)

- `POST /auth/register` — body: `display_name`, `email`, `whatsapp_number`, `password`
  (min 6 chars, same rule as admin create). Hashes the password and stores a request.
  - 409 "A user with this email already exists" if the email belongs to a user.
  - 409 "A registration for this email is already awaiting approval" if a request is open.
  - 200 with a success message otherwise.
- `POST /auth/login` — extra check: when credentials match no user but the email has a
  pending request, respond 403 "Your registration is awaiting approval by the bank manager".

### BankManager (router-level `require_bank_manager`, in `/admin`)

- `GET /admin/registration-requests` — pending requests, newest first.
- `POST /admin/registration-requests/{id}/approve` — body: `initial_balance_eur`
  (UI default 10 000, same as the create-user form). Creates `User` (role `user`, active)
  with the stored password hash and WhatsApp number, creates `Account` with the balance,
  deletes the request. 409 if the email meanwhile became a user.
- `DELETE /admin/registration-requests/{id}` — reject: deletes the request.

### WhatsApp on existing endpoints

- `whatsapp_number` added to the admin user list (`GET /admin/users`), to
  `PATCH /admin/users/{id}`, to `GET /auth/me` and to `PUT /auth/profile`.

## Frontend

- **LoginPage** — footer "No account? Ask your bank manager…" becomes a link to `/register`:
  "No account? Register here".
- **RegisterPage** (`/register`, public route) — same centered-card style as the login page.
  Fields: display name, email, WhatsApp number, password, confirm password. Client-side
  validation mirrors the server. On success, shows "Registration submitted — the bank
  manager will review your request" with a link back to login.
- **AdminPage** — new "Registration requests" section above user management: each pending
  request shows name, email, WhatsApp number and request date, with an editable start
  balance input (default 10 000) and Approve / Reject buttons. The section shows a count
  and collapses to nothing when the queue is empty.
- **Admin user list** — shows the WhatsApp number.
- **ProfilePage** — user can view and edit their own WhatsApp number.
- All new strings in both `en.json` and `nl.json`.

## Error handling and edge cases

- Duplicate email at registration (user or pending) → specific 409 messages.
- Approval when the email meanwhile exists as a user → 409, request kept.
- No email/WhatsApp notifications: the app has no messaging infrastructure and adding one
  is out of scope. Approval simply makes login work.

## Testing

Backend tests: register happy path; duplicate email against users and against pending
requests; invalid WhatsApp format; pending login returns the awaiting-approval message;
approve creates user + account with the chosen balance, copies the WhatsApp number and
removes the request; reject deletes the request; both admin endpoints 403 for non-managers.
