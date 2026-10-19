# Access control and hospital dashboards: how it all works

This document explains, step by step, what was built on top of the
*Access Control & Hospital Dashboard Framework*: who can see what, how accounts
get in, and where each piece lives in the code. It starts with the question
that comes up most — **what is the difference between a case manager and a
hospital admin?** — and then walks through each build step in order.

---

## Contents

1. [The one rule everything follows](#1-the-one-rule-everything-follows)
2. [The seven roles](#2-the-seven-roles)
3. [Case manager vs hospital admin](#3-case-manager-vs-hospital-admin)
4. [Full permissions table](#4-full-permissions-table)
5. [Step 1 — Roles become real](#5-step-1--roles-become-real)
6. [Step 2 — Hospitals, insurers and User Management](#6-step-2--hospitals-insurers-and-user-management)
7. [Step 3 — Every patient belongs to a hospital](#7-step-3--every-patient-belongs-to-a-hospital)
8. [Step 4 — The hospital dashboards](#8-step-4--the-hospital-dashboards)
9. [Step 5 — Choosing a role at sign-up](#9-step-5--choosing-a-role-at-sign-up)
10. [How a new person gets in, end to end](#10-how-a-new-person-gets-in-end-to-end)
11. [Demo data and demo accounts](#11-demo-data-and-demo-accounts)
12. [Where things live in the code](#12-where-things-live-in-the-code)
13. [What is not built yet](#13-what-is-not-built-yet)

---

## 1. The one rule everything follows

> **Every account has exactly one role, and belongs to one hospital** (or, for
> insurers, one insurer). The superadmin — our team — is the only exception.

Two consequences run through everything below:

- **The server decides, not the screen.** Hiding a menu item is a courtesy. Every
  API request re-checks who the person is and what they may see, so a hidden
  page cannot be reached by typing its URL, and a refused request never falls
  back to made-up data.
- **Changes take effect immediately.** Both backends re-read the account from the
  database on every request. Approving someone, changing their role or suspending
  them applies to their very next click — nobody has to sign out and back in.

---

## 2. The eight roles

| Role | Who it is | Belongs to | In one sentence |
|---|---|---|---|
| **Superadmin** | Our team | Nobody (sees every hospital) | Sets up hospitals and insurers, can see and fix anything. |
| **Hospital admin** | The person who runs the hospital's account | One hospital | Manages the hospital's people and money; sees patients at overview level. |
| **Case manager** | A care coordinator (often a nurse or social worker) | One hospital | Works on *every* patient in the hospital to keep their care on track. |
| **Doctor** | A treating clinician | One hospital | Works on the patients assigned to them. |
| **Nurse** (includes caretakers) | A nurse or caretaker | One hospital | Follows up the patients assigned to them. |
| **Insurer** | A payer's staff | One insurer | Sees its own members, in any hospital, mainly costs and outcomes. |
| **Pharmacy** | A pharmacy's staff | One pharmacy | Sees the patients who fill there, in any hospital: risk, care team and case manager — never diagnoses. |
| **Patient** | A patient | One hospital | Sees their own record only. |

The role names are identical everywhere — database, sign-in token, both
backends, both frontends — so there is never a translation between them.

---

## 3. Case manager vs hospital admin

This is the part that looks confusing, because on the menu they look similar:
both see the **whole hospital**, both have an **Overview** page and a **Staff**
page, and both can **assign patients** to doctors and nurses. The difference is
in *what kind of work* each one does.

### The short version

- A **hospital admin** runs the hospital's *account*: people, access and money.
  They look at patients from a distance.
- A **case manager** runs patients' *care*: follow-ups and coordination. They
  work inside individual patient records all day.

### Side by side

| | Hospital admin | Case manager |
|---|---|---|
| **Main job** | Running the account: people, access, money | Coordinating patient care: follow-ups, who looks after whom |
| **Sees which patients** | All of the hospital's | All of the hospital's |
| **Patient list** | Overview only: risk, care team, insurer. **No diagnoses, drivers, vitals or drugs** | Everything, including diagnoses and risk drivers |
| **Opening one patient's clinical details** | **Must give a reason** (care coordination, incident review, audit, billing). It is written to the access log | Opens directly, no reason asked |
| **Adding and approving people** (User Management) | **Yes** — doctors, nurses, case managers, patients | No |
| **Access log** | **Can read** their hospital's log | No |
| **Cost and ROI screens** (GLP-1 Budget Simulator, Cost of Inaction, drug spend) | No — insurers and superadmin only (the payer pays for the drug) | No |
| **Registering doctors for alerts** (Readmissions) | **Yes** | No |
| **Uploading or adding patients** | **Yes** | No |
| **Assigning doctors and nurses to patients** | Yes | Yes |
| **Setting a patient's main case manager** (GLP-1: one per patient, their point of contact) | Yes | Yes |
| **Adding care notes, answering clinical alerts** | No | **Yes** |
| **Clinician Console** (doctors' alert inboxes) | No | **Yes** |

**Main case manager (GLP-1).** Each patient can have one main case manager —
the person who calls them. It is set like a doctor or nurse (Care team, on the
patient's page or for many patients from the Patients list); how a hospital
divides patients (by doctor team, by problem, by ward) is its own choice, and
the app has no rule for it. Every case manager still sees the whole hospital,
so colleagues can cover. A new follow-up request rings only the bell of the
patient's main case manager (everyone's, if the patient has none or theirs has
left); the rest of the team still sees it in Follow-ups. A case manager's
"My caseload" (Today, Patients) shows just their own patients. A colleague
with a different skill is brought in by handing over the follow-up, with a
note, not by sharing the patient. Readmissions does not have this yet.

So they overlap on *seeing the whole hospital* and *assigning*, and are
opposites on everything else: the admin has management and money but only the
outside of patient records; the case manager has the inside of patient records
but no management and no money.

### Why the admin must give a reason and the case manager doesn't

Health data follows a "minimum necessary" principle: people see clinical detail
when their job needs it. A case manager's job *is* the clinical detail — they
cannot arrange a heart-failure follow-up without knowing it is heart failure. A
hospital admin's job usually isn't, so they see the big picture by default, and
when they do need one patient's details (a complaint, an audit, a billing
query) they say why, and it is recorded. That also means misuse is visible in
the access log.

### An example day

- **The hospital admin** adds two new nurses in User Management, approves a
  doctor who signed up, checks drug spend on the GLP-1 Overview, and sees on the
  Readmissions Overview that 12 patients have no doctor assigned. A complaint
  comes in about one patient, so they open that patient with the reason
  *Complaint / incident review*.
- **The case manager** opens the "Needs attention" list, reads each patient's
  diagnoses and risk drivers, assigns the unassigned patients to doctors,
  adds a note after calling a patient, and answers an alert in the Clinician
  Console.

### If you still want them to be more different

The current split follows the framework document exactly (its table gives case
managers the whole hospital, clinical detail and assignment, but no user
management and no costs). If the team wants a sharper difference, the options
are:

| Option | What changes | Effort |
|---|---|---|
| **A. Keep as is** | Nothing. The difference is management and money vs clinical work. | None |
| **B. Give case managers a caseload** | Case managers see only the patients assigned *to them*, like nurses. Admins assign patients to case managers too. | Medium: a case-manager field on each patient, plus assignment UI |
| **C. Rename the role on screen** | Show "Care coordinator" instead of "Case manager", so the purpose is obvious. The code keeps `case_manager`. | Small |
| **D. Merge into hospital admin** | One role. Loses the separation between clinical and admin access. Not recommended. | Medium |

---

## 4. Full permissions table

"Own" means patients assigned to that person. "Hospital" means every patient of
their hospital. The superadmin can narrow any view to one hospital with the
hospital picker.

| What | Superadmin | Hospital admin | Case manager | Doctor | Nurse | Insurer | Patient |
|---|---|---|---|---|---|---|---|
| Which patients | All hospitals | Hospital | Hospital | Own | Own | Own members | Own record |
| Overview page | Yes | Yes | Yes | — | — | Yes (members) | — |
| Patients list | Yes | Overview layer | Yes | Yes | Yes | Overview layer | — |
| Clinical details of a patient | Yes (logged) | With reason (logged) | Yes | Yes | Yes | With reason (logged) | Own |

**Pharmacy** (not a column above, to keep the table readable): sees the patients
who fill at its pharmacy, in any hospital, in both apps; the patients list at the
overview layer only, and **never** the clinical layer — no reason opens it
(`OVERVIEW_ONLY_ROLES` in both `access.py` files; 45 CFR 164.502(b), minimum
necessary). No hospital pages, costs, notes, assignment or clinical alerts. In both apps
a pharmacy's home page lists its patients — GLP-1: drug, refill reliability,
hospital and case manager; Readmissions: readmission risk, discharge date and the
latest weekly refill from the pharmacy feed — and it can **report a refill
problem** (missed pickup, prior authorisation, out of stock, side effects, cost).
That becomes a follow-up for the patient's case manager (Readmissions: the
hospital's case managers), or a note on the one already open. The pharmacy sees
the status of its own reports, never the care team's notes. If someone already
holds that follow-up, the report rings their bell; when the follow-up is done, the
pharmacy's bell says so, with the outcome only (cleared by opening Follow-ups, or
the pharmacy page). Opening a patient is
logged as "Dispensing (pharmacy)", and such a log entry never opens the clinical
layer. Placed in its
pharmacy by the superadmin, like insurer staff. Patients are linked to a pharmacy
by `pharmacy_id` on their ownership record (`Readmissions/scripts/seed_pharmacies.py`
for the demo data).

**Patient** (GLP-1; Readmissions to follow): sees only their own record, on a home
page written for them — how things look in words (never the percentage), their
current medicine and how often it's taken, age, BMI and HbA1c with the usual
ranges, the main things affecting them in plain words, and their care team by
name and role. Patients have a right of access to their own record
(45 CFR 164.524). They don't see the clinician's patient page, the care team's
notes, the model's internal numbers or costs. They can **ask their case manager
to get in touch**, choosing a reason; that becomes a follow-up (or a note on the
open one), signed "The patient" on staff screens — never their login. They see
whether someone is on it and how it ended, not the notes.
| Staff page | Yes | Yes | Yes | — | — | — | — |
| Assign doctors / nurses | Yes | Yes | Yes | — | — | — | — |
| Add notes, answer alerts | Yes | — | Yes | Yes | Yes | — | — |
| Clinician Console | Yes | — | Yes | Own inbox | — | — | — |
| Add / upload patients | Yes | Yes | — | — | — | — | — |
| Register doctors for alerts | Yes | Yes | — | — | — | — | — |
| GLP-1 cost and ROI screens | Yes | — | — | — | — | Yes (members) | — |
| User Management | Everyone | Own hospital's staff and patients | — | — | — | — | — |
| Create hospitals and insurers | Yes | — | — | — | — | — | — |
| Access log | All hospitals | Own hospital | — | — | — | — | — |
| Forecast sweep | Yes | — | — | — | — | — | — |

"Overview layer" = patient number, risk score and band, trend, discharge date,
care team and insurer. The clinical layer = diagnoses, risk drivers, vitals
(BMI, HbA1c, age), drug, pharmacy, notes and alerts.

---

## 5. Step 1 — Roles become real

**Before:** anyone signing up typed their own role ("Doctor", even "superadmin")
and their own hospital name, and nothing checked either. The Readmissions API
only checked a shared key that ships inside the public website, and most GLP-1
routes checked nothing at all. GLP-1 had a role toggle anyone could flip.

**What changed:**

1. **A fixed list of seven roles** (section 2). The database rejects any other
   value once `scripts/migrate_user_roles.py` has run.
2. **Every account has a status**, `pending` or `active`, and a `hospital_id`.
   Pending accounts can sign in, but see a "waiting for approval" screen and get
   no data.
3. **Every API route requires an active, signed-in user** — all of Readmissions'
   routes and all of GLP-1's. A route added later is covered automatically: the
   check is attached to the whole app, not route by route.
4. **The account is re-read on every request**, so changes apply at once (see
   section 1). The sign-in token's contents are for display only.
5. **Old accounts are not trusted.** An account created before this has whatever
   role its owner typed, so it is treated as an active case manager with no
   hospital until an admin places it.
6. **The first superadmin** is created with `scripts/create_superadmin.py`, run
   by someone with database access. No web page can create or grant superadmin.
7. **GLP-1's role toggle is gone.** The role shows read-only. The "Clinician"
   badge became "Care team".

---

## 6. Step 2 — Hospitals, insurers and User Management

**What changed:**

1. **Hospitals and insurers are records** (`shared_identity.hospitals` and
   `shared_identity.insurers`). Only the superadmin creates them. A hospital's id
   is made from its name, e.g. "Demo Hospital" → `demo-hospital`.
2. **User Management** — one section, shown in GLP-1's Settings page and on
   Readmissions' "User Management" page. The same accounts work in both apps.
   - The **superadmin** creates hospitals, insurers and hospital admins, and can
     manage every account except other superadmins.
   - A **hospital admin** adds and manages doctors, nurses, case managers and
     patients, only in their own hospital. They cannot create another hospital
     admin, and cannot see other hospitals' accounts at all.
3. **Temporary passwords.** A person added by an admin gets a 12-character
   temporary password, shown to the admin once. At first sign-in the Portal makes
   them choose their own; until they do, both apps refuse them data.
4. **CSV import.** Many staff at once: a file with the columns `email,name,role`
   (up to 1,000 rows). Each row succeeds or fails on its own.
5. **Suspend** puts an account back to pending, which cuts it off immediately.

---

## 7. Step 3 — Every patient belongs to a hospital

**What changed:**

1. **Each patient has an ownership record** saying which hospital they belong
   to, who their insurer is, their doctor, their nurses, and (for patient
   accounts) which login is theirs.
   - Readmissions keeps it on each patient's `care_actions` record.
   - GLP-1 keeps it in a `patient_access` collection, one per patient (plus the
     patient's pharmacy).
   - Both survive the data being reloaded.
2. **Every patient API is filtered** to the patients the person may see
   (section 4, first row). Asking for someone else's patient returns
   **"not found"** — the same answer as for a patient that does not exist, so
   nobody can find out which patients another hospital has.
3. **Headline numbers are the caller's own.** Summaries, counts and cost figures
   are worked out from the caller's patients, not the whole population.
4. **Both chatbots are scoped the same way**, and a chatbot conversation belongs
   to the account that started it.
5. **GLP-1 cost and ROI screens** are for the superadmin and insurers only. The
   payer pays for the GLP-1 drug, not the hospital, so a hospital admin sees
   patients, adherence and risk but no drug spend, budget or ROI.
6. **The seed script** `Readmissions/scripts/seed_hospital.py` puts the demo data
   into one hospital, sets insurers from the data, and can create demo accounts
   with assignments (section 11).

---

## 8. Step 4 — The hospital dashboards

**What changed, page by page (both apps):**

1. **Overview** — "How is my hospital doing?"
   - Readmissions: patients, high risk (and the change since last batch), needs
     attention, open alerts, patients with no doctor or no nurse, recent
     discharges, insurer mix.
   - GLP-1: patients on therapy, adherent vs not, high dropout risk, drug mix,
     insurer mix, and — for cost roles only — drug spend. The segment charts
     below it are the existing Executive Summary.
   - Every figure you can act on opens the Patients list already filtered.
   - Doctors and nurses have no Overview; they start on their Patients list.
   - **Reeha's change:** in GLP-1 the hospital summary box is hidden for the
     superadmin, because Talha's dashboard below already covers it. Hospital
     admins, case managers and insurers still see it.
2. **Patients** — one row per patient with their doctor, nurses and insurer.
   Admins and case managers can tick patients and **assign a care team** in one
   go. The doctor or nurse sees their new patients on their next click.
3. **Patient detail** — for hospital admins and insurers, the overview first,
   then the **reason prompt** (section 3). The reason is logged once per patient
   per sign-in; signing in again asks again.
4. **Staff** — every doctor and nurse with how many patients they have and how
   many of those are high risk, plus open alerts (Readmissions) or non-adherent
   patients (GLP-1). Click a person to see
   their patients. In Readmissions, a doctor must be registered for alerts (with
   a specialty) before patients can be assigned to them; admins do that with a
   button on this page.
5. **Access log** — a section at the bottom of User Management: who opened which
   patient, in which app, when, and why. A hospital admin sees their hospital's
   log (including our team's and insurers' views of their patients); the
   superadmin sees everything.
6. **Hospital picker** — the superadmin can switch to one hospital and see
   exactly what its admin sees, or "All hospitals". Nobody else can use it.
7. **No more made-up numbers in GLP-1.** Pages used to show sample data while
   loading or after an error; now they show a loading state or the real error.

---

## 9. Step 5 — Choosing a role at sign-up

**Why:** Reeha suggested it: when people pick their role at sign-up, the admin
can see at a glance who is waiting to join as what, which makes approving them
easier. It is safe because choosing a role is only a **request**.

**What changed:**

1. **The Portal's "Create account" form has an "I am joining as" dropdown**:
   Doctor, Nurse or caretaker, Case manager, Hospital administrator, Insurer,
   Patient. Picking one shows a one-line explanation of that role underneath, so
   people choose correctly. Superadmin is never offered, and the server refuses
   it even if someone sends it directly.
2. **The account is still pending**, with no hospital, and sees nothing until an
   admin approves it. Asking to be a hospital admin grants nothing.
3. **The waiting screen** now says "You asked to join as: Doctor", for example.
4. **User Management can filter accounts by role.** A pending sign-up shows
   "Signed up as Doctor" under its email, and its role picker already shows the
   role they asked for.
5. **The admin still decides.** When approving, they can keep the requested role
   or change it (for example someone who picked "Hospital administrator" but is
   really a case manager). What they originally asked for is kept on the account.
6. **The sign-up never asks for a hospital.** The admin assigns it on approval,
   so typing a hospital's name is never enough to see its patients.

---

## 10. How a new person gets in, end to end

### A. An admin adds them (recommended for staff)

1. The hospital admin (or superadmin) opens User Management → **Add a person**,
   enters the email, name and role.
2. The screen shows a **temporary password** once. The admin passes it on
   privately.
3. The person signs in on the Portal, is asked to **set their own password**,
   and then sees their apps.

### B. They sign up themselves

1. On the Portal they choose **Create one**, enter email and password, and pick
   **I am joining as**.
2. They land on **"Your account is waiting for approval"**.
3. Approval:
   - **Superadmin:** User Management → filter **Pending** (and a role if useful)
     → choose the hospital (or insurer) → **Approve**. The role can be changed
     first.
   - **Hospital admin:** User Management → **Add a person** with the same email
     and the role they should have. Because the email belongs to a pending
     sign-up, this approves it into the admin's hospital instead of creating a
     second account. (Hospital admins do not see a list of all sign-ups: that
     would show them people who signed up for other hospitals.)
4. The person clicks **Check again** (or signs in again) and sees their apps. They
   keep the password they chose.

---

## 11. Demo data and demo accounts

- Readmissions uses MIMIC patients; GLP-1 uses its existing dataset. Both are
  placed in one demo hospital by `seed_hospital.py`.
- Running the seed with `--with-demo-accounts` creates these logins, all with the
  password typed when the seed was run:

| Account | Role |
|---|---|
| `admin@<hospital-id>.test` | Hospital admin |
| `casemanager@<hospital-id>.test` | Case manager |
| `nurse1@…`, `nurse2@…` | Nurses (patients split between them) |
| `cardiology@…`, `pulmonology@…`, `endocrinology@…`, `medicine@…` | Doctors (patients assigned by condition) |
| `claims@medicare.test`, `claims@medicaid.test`, `claims@private.test`, `claims@other.test` | Insurers |
| `patient1@…`, `patient2@…` | Patients, each linked to one record |

Demo accounts share one known password. Change it, or remove them, before anyone
outside the team gets a link — real MIMIC data sits behind them.

---

## 12. Where things live in the code

| Piece | File |
|---|---|
| Roles, statuses, sign-up, sign-in, tokens | `Readmissions/api/auth.py` |
| User Management rules | `Readmissions/api/user_admin.py` |
| Who sees which patients (Readmissions) | `Readmissions/api/access.py` |
| Who sees which patients (GLP-1) | `GLP1/Backend/core/access.py` |
| Overview, Staff, care-team assignment | `Readmissions/api/hospital.py`, `GLP1/Backend/core/hospital.py` |
| Access log | `Readmissions/api/access_log.py`, `GLP1/Backend/core/access_log.py` |
| GLP-1's check of the signed-in user | `GLP1/Backend/core/security.py` |
| Portal (sign-in, sign-up, waiting screen) | `Portal/index.html` |
| User Management screen (identical copy in both apps) | `*/src/components/shared/UserManagement.jsx` |
| Readmissions pages | `Readmissions/frontend/src/pages/Overview.jsx`, `Patients.jsx`, `Staff.jsx`, `PatientDetail.jsx` |
| GLP-1 pages | `GLP1/Frontend/src/pages/ExecutiveSummary.jsx` (Overview), `PatientRiskPanel.jsx` (Patients), `Staff.jsx`, `PatientDetail.jsx` |
| Which menu items each role gets | `Readmissions/frontend/src/roles.js`, `GLP1/Frontend/src/context/RoleContext.jsx` |
| Operator scripts | `Readmissions/scripts/create_superadmin.py`, `migrate_user_roles.py`, `seed_hospital.py` |
| Tests for all of the above | `Readmissions/tests/units/test_auth.py`, `test_user_admin.py`, `test_isolation.py`, `test_hospital_pages.py`; `GLP1/Backend/tests/test_isolation.py`, `test_hospital_pages.py` |

---

## 13. What is not built yet

| Item | Status |
|---|---|
| Deactivate and delete accounts | "Suspend" (back to pending) exists; a proper deactivate and a superadmin-only delete do not. |
| Email invites | Admins hand over temporary passwords for now; invites need an email service. |
| Hospital admins seeing sign-ups for their hospital | Would need the sign-up to name a hospital, which also means showing the list of hospitals publicly. |
| Forecast sweep per hospital | Superadmin only, because it currently scans every hospital at once. |
| BMI and HbA1c over time (GLP-1) | Needs repeated measurements; the data has one snapshot per patient. |

Open questions for the team:

- Is the reason list right (care coordination, complaint / incident review,
  audit, billing query)?
- Should case managers stay as they are, or change (section 3, options A–D)?
- Email invites instead of temporary passwords, once there is an email service?