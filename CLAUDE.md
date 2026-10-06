# Preventra — combined monorepo

Two products plus one shared sign-in page. One account, one token, two apps.

| Folder | What it is | Stack |
|---|---|---|
| `GLP1/` | GLP-1 adherence, cost-effectiveness, payer-ROI analytics | FastAPI (`GLP1/Backend`) + React/Vite (`GLP1/Frontend`) |
| `Readmissions/` | 30-day readmission risk, weekly monitoring, clinician console — **and the shared auth service** | FastAPI (`Readmissions/api`) + React/Vite (`Readmissions/frontend`) |
| `Portal/` | Shared sign-in / sign-up page that hands a token to either app | Static `index.html` + a `build.js` that injects per-env URLs |

Root `README.md` is the long-form setup/deploy runbook. `ACCESS_CONTROL.md` is the
role-and-permissions narrative. This file is the orientation layer; the skills under
`.claude/skills/` carry task-specific procedure.

---

## The three invariants

Break any of these and the merge comes apart. They are load-bearing.

**1. One issuer, two verifiers.**
`Readmissions/api/auth.py` is the ONLY place a JWT is minted. Both backends verify it
independently with the same `SHARED_SECRET_KEY` (HS256). Neither backend calls the other, so
they stay separately deployable. GLP-1 verifies in `GLP1/Backend/core/security.py`; it had its
own issuer once and it was deliberately deleted. **Never re-add a second issuer.**

**2. The token proves identity, never authority.**
Both backends re-read the account from `shared_identity.users` on EVERY request. An approval,
role change, or removal takes effect on the next request, not when the token expires. Anything
read from token claims in a frontend (`readClaims`, `decodeClaims`) is display-only — it is
base64, not ciphertext. Never gate anything real on it.

**3. Shared concepts are duplicated, not imported.**
The two services deploy separately, so they cannot share a module. Role lists, reason lists and
access rules exist as twin copies that must be edited together. See the parity map below and
the `cross-app-parity` skill.

---

## Token contract (frozen by agreement between both products)

```json
{"sub": "<users._id>", "email": "...", "role": "...", "status": "pending|active",
 "hospital_id": "...", "must_change_password": false,
 "app_access": ["glp1", "readmissions"], "sid": "<per-sign-in id>", "exp": 1700000000}
```

- `sid` names one sign-in and is kept across refresh. The access log's "don't ask again this
  session" is keyed on it. Older tokens have none — fallback is `api/access_log.session_of`.
- `app_access` strings must match the Portal tile `key` values (`glp1`, `readmissions`) exactly,
  or the tile never appears.
- TTL 12h (`SHARED_AUTH_TOKEN_TTL`). `/auth/refresh` re-issues with the SAME `exp` and `sid`, so
  refreshing updates claims without extending the session.

## Handoff mechanics

The token always travels on the URL **fragment** (`#token=...`) — a fragment is never sent to a
server, so it stays out of access logs, proxy logs and `Referer` headers. Each receiving app
reads it before render and `history.replaceState`s it out of the address bar.

Sign-out is a **relay chain**: each app clears its own copy and forwards to the next.
`#signout=1&chain=glp1` → portal clears → hops to GLP-1 → GLP-1 clears → ends at portal.
Each app's `receiveSignout()` runs in `main.jsx` BEFORE render and returns `true` to skip
mounting — rendering mid-hop flashes a dashboard at someone who just signed out.

Token storage differs per surface, on purpose:

| Surface | Key | Store |
|---|---|---|
| Portal | `portal_token` | `sessionStorage` |
| Readmissions FE | `shared_auth_token` | `sessionStorage` |
| GLP-1 FE | `glp1_token` / `glp1_user` | `localStorage` |

---

## Databases — one cluster, three databases

| DB | Owner | Contents |
|---|---|---|
| `shared_identity` | Readmissions auth service | `users`, `hospitals`, `insurers`, access log |
| `neuroshield` | Readmissions | `patient_worklist`, `weekly_monitoring`, `care_actions`, `alerts`, `clinical_alerts`, `risk_registry`, `notifications` |
| `glp1_analytics` | GLP-1 | patient data + `patient_access` (ownership) |

Accounts are deliberately held apart from either product's data. `user_admin.py` reaches into
`glp1_analytics` (via `GLP1_DB`) only to set a patient's insurer / care team.

---

## Parity map — files that must change together

| Concept | Readmissions | GLP-1 |
|---|---|---|
| Role + status lists | `api/auth.py` `ROLES`, `STATUSES` | `Backend/core/security.py` (same tuples) |
| Patient-visibility rules | `api/access.py` | `Backend/core/access.py` |
| Reason list for clinical layer | `api/access.py` `REASONS` | `Backend/core/access.py` `REASONS` |
| Access log | `api/access_log.py` | `Backend/core/access_log.py` |
| Hospital pages (overview/staff/care-team) | `api/hospital.py` | `Backend/routers/hospital.py` + `core/hospital.py` |
| Session id derivation | `access_log.session_of` | `security.current_user` (`sid`, else sha256 of token) |
| Which menu items a role gets | `frontend/src/roles.js` | `Frontend/src/context/RoleContext.jsx` |
| User Management screen | `frontend/src/components/shared/UserManagement.jsx` | `Frontend/src/components/shared/UserManagement.jsx` |
| Loading sequence | `frontend/src/hooks/useAppLoader.js` + `components/LoadingScreen.jsx` | `Frontend/src/hooks/useAppLoader.js` + `components/shared/LoadingScreen.jsx` |
| Sign-out relay | `frontend/src/api/auth.js` | `Frontend/src/context/AuthContext.jsx` |
| Role labels / hints | `roles.js`, `AppSwitcher.jsx` | `RoleContext.jsx` — **and `Portal/index.html`** |

The Portal is the third copy of `ROLE_LABELS`, `ROLE_HINTS` and `HOSPITAL_ROLES`.

---

## Stack differences that bite

- **Tailwind versions differ.** GLP-1 is v3 (`tailwind.config.js` + `postcss.config.js`, theme
  colours under `theme.extend`: `primary`, `surface`, `canvas`, `border`, `muted`).
  Readmissions is v4 (`@tailwindcss/vite`, an `@theme` block inside `src/index.css`, no config
  file: `--color-risk-high`, `--color-ns-navy`, …). Copying a component between the apps means
  re-checking class names and custom colour tokens.
- **Env var names differ for the same thing**, because the services were built separately:
  `MONGODB_URI` / `MONGODB_DB_NAME` (GLP-1) vs `MONGO_URI` / `MONGO_DB` (Readmissions).
  `CORS_ORIGINS` is a **JSON list** on GLP-1; `ALLOWED_ORIGINS` is **comma-separated** on
  Readmissions. `SHARED_SECRET_KEY` is the one name shared by both and must be byte-identical.
- **Working directory matters.** GLP-1: `uvicorn main:app` from inside `GLP1/Backend/` (its
  imports are `core.*` / `routers.*`). Readmissions: `uvicorn api.main:app` from `Readmissions/`.
- **`VITE_*` is read at build time.** Changing one needs a dev-server restart or a redeploy.

## Ports (local)

| Process | Port |
|---|---|
| GLP-1 API | 8000 |
| Readmissions API + auth | 8001 |
| GLP-1 frontend | 5173 |
| Readmissions frontend | 5174 |
| Portal (static) | 5175 |

Both backends default to 8000, so the port split is not optional — pass `--port`.

Health: `:8000/health` (singular), `:8001/healthz`, `:8001/auth/config`
(`secret_configured: false` means the `.env` was not picked up — the fastest login diagnosis).

---

## Security rules that are not negotiable

- `SHARED_SECRET_KEY` is symmetric: holding it means being able to MINT tokens, not merely check
  them. Never in a browser bundle, never committed.
- Readmissions' `API_KEY` ships inside the public frontend bundle. It is only a meaningful gate
  alongside a pinned `ALLOWED_ORIGINS`. A wildcard in production lets any page on the internet
  read patient data through a visitor's browser.
- `/auth/*` and `/healthz` are exempt from `X-API-Key` (`_skips_api_key` in `api/main.py`) — the
  portal calls them before anyone has a token, and GLP-1 does not hold the service key.
- Login returns the same message and the same amount of work for an unknown email as for a wrong
  password (`_DUMMY_HASH`). Do not add a distinguishing error.
- An account on a temporary password (`must_change_password`) is refused everything except
  `/auth/me`, `/auth/refresh` and `/auth/change-password`.
- Hiding a tile, a menu item or a button is a courtesy. The backend is the control — add the
  server-side refusal first, then hide the UI to match.

## Known drift (documentation vs. code)

- `Readmissions/docs/shared_login_api.md` and `docs/portal_handover.md` describe the old
  `role` + `org_name` → `org_id` signup contract. The live contract is `role` + `hospital_id`,
  and the response carries `hospital_id` / `requested_hospital_id`. The code is the truth.
- `ACCESS_CONTROL.md` §9.6 says "the sign-up never asks for a hospital". It does now —
  `GET /auth/hospitals` feeds a dropdown and the choice is filed as `requested_hospital_id`.
- Root `README.md` §1 says "each frontend also has its own sign-in screen". No longer true.
  `GLP1/Frontend/src/pages/Login.jsx` is dead code — nothing imports it, and `App.jsx` sends an
  unauthenticated visitor to the Portal. Readmissions never had one.
- `Readmissions/.env` holds live credentials in the working tree (gitignored, never committed).
  Rotation is still outstanding — see root README §9 "Still outstanding".
