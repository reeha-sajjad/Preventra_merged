# Case Manager: Role and Access

**Products:** GLP-1 Analytics and Readmission Risk (Preventra)
**Audience:** the Preventra team, hospital administrators, and anyone reviewing access for compliance
**Checked against the code:** 14 October 2026

> The rules described here are enforced in two files that must always change together:
> `GLP1/Backend/core/access.py` and `Readmissions/api/access.py`. If this document and
> the code ever disagree, the code is what actually happens. Fix the document.

---

## Contents

1. [At a glance](#1-at-a-glance)
2. [Where the case manager fits](#2-where-the-case-manager-fits)
3. [A typical day](#3-a-typical-day)
4. [Which patients a case manager can see](#4-which-patients-a-case-manager-can-see)
5. [Page by page: GLP-1 Analytics](#5-page-by-page-glp-1-analytics)
6. [Page by page: Readmission Risk](#6-page-by-page-readmission-risk)
7. [Permissions at a glance](#7-permissions-at-a-glance)
8. [Follow-up requests in detail](#8-follow-up-requests-in-detail)
9. [Care teams](#9-care-teams)
10. [Alerts and notifications](#10-alerts-and-notifications)
11. [The assistant (chatbot)](#11-the-assistant-chatbot)
12. [What is recorded](#12-what-is-recorded)
13. [The account: from sign-up to sign-out](#13-the-account-from-sign-up-to-sign-out)
14. [What a case manager cannot do, and why](#14-what-a-case-manager-cannot-do-and-why)
15. [How the rules are enforced and tested](#15-how-the-rules-are-enforced-and-tested)
16. [HIPAA basis](#16-hipaa-basis)
17. [Open questions](#17-open-questions)
18. [Where it lives in the code](#18-where-it-lives-in-the-code)

---

## 1. At a glance

| | |
|---|---|
| **Purpose** | Coordinate care across the whole hospital, so that no patient falls through the gaps between doctors, nurses and appointments. |
| **Main jobs** | Arrange the follow-ups doctors and nurses ask for. Keep every patient on a care team. Spot patients who are getting worse or being missed. |
| **Sees** | Every patient in **their own hospital**, in full clinical detail. No other hospital's patients, ever. |
| **Reason needed to open a patient?** | **No.** Care coordination is part of treatment and operations, so they open records directly. Every opening is still logged automatically. |
| **Cannot** | Add or approve people, read the access log, see cost and ROI pages, upload patients, train models. |
| **Same in both apps?** | Yes for patients, care teams, follow-ups, logging and the account. A few pages exist in only one app (sections 5 and 6). |

---

## 2. Where the case manager fits

Five roles work inside a hospital. The case manager's place is easiest to understand next to the others:

| | Doctor | Nurse | **Case manager** | Hospital admin | Insurer |
|---|---|---|---|---|---|
| **Main job** | Treat their patients | Care for their patients | **Coordinate care for the whole hospital** | Run the account: people, access, money | Pay for care; watch outcomes and cost |
| **Whose patients** | Only those assigned to them | Only those assigned to them | **Everyone in their hospital** | Everyone in their hospital | Their members, in any hospital |
| **How much of each patient** | Everything | Everything | **Everything** | Overview only (risk, care team, insurer) until they give a reason | Overview only until they give a reason |
| **Reason to open a record** | No (logged as "Treatment") | No (logged as "Treatment") | **No (logged as "Care coordination")** | Yes, chosen from a list | Yes, chosen from a list |
| **Home page** | My patients | My patients | **Hospital Overview** | Hospital Overview | Overview of their members |
| **Follow-ups** | Asks for them | Asks for them | **Arranges them** | Can step in | — |
| **Assigns care teams** | No | No | **Yes** | Yes | No |
| **User Management** | No | No | **No** | Yes | No |
| **Cost and ROI pages** | No | No | **No** | Yes (GLP-1) | Yes (GLP-1) |

The case manager and the hospital admin **overlap** on seeing the whole hospital and on assigning
care teams. They are **opposites** on everything else:

- the admin manages people and money but sees only the overview layer of patients;
- the case manager sees every patient in full but manages neither people nor money.

---

## 3. A typical day

1. **Sign in** once at the Preventra portal. The same sign-in opens both apps.
2. **Check the follow-ups.**
   - **GLP-1:** the bell shows new requests.
   - **Readmission Risk:** the **Follow-ups** menu item shows a red count of requests nobody has taken yet.
3. **Take a request.** Pressing "I'll take it" claims it, so no other case manager phones the same patient.
4. **Contact the patient** and **book them in the hospital's own scheduling system.** Preventra does not book appointments.
5. **Mark it done** with the outcome: *Booked*, *Couldn't reach patient* or *Not needed*, plus an optional note such as "Tuesday clinic, 10:30". The doctor or nurse who asked sees the outcome on the patient's page.
6. **Fix gaps in care teams.** The Overview shows how many patients have no doctor or no nurse; the Staff page shows who is overloaded. Assign people from the patient's page or the patient list.
7. **Watch for patients getting worse.**
   - **Readmission Risk:** the rising-risk bell and the "Needs attention" count on the Overview.
   - **GLP-1:** the highest-risk patients on the patient list.
8. **(Readmission Risk) Cover a doctor's inbox if needed.** In the Clinician Console, open a doctor's alert inbox and acknowledge or answer alerts. Anything done there is recorded against that doctor, and a banner says so.

---

## 4. Which patients a case manager can see

### The rule

A case manager sees **every patient whose record belongs to their hospital**, and no one else.

| Situation | What the case manager sees |
|---|---|
| Patient in their hospital | The full record. |
| Patient in another hospital | **"Not found"**, exactly the same answer as for a patient who does not exist. They cannot even learn that the patient exists. |
| Patient with no doctor or nurse yet | Visible, as long as the patient is in their hospital. Finding these patients is part of the job. |
| Their account approved but **no hospital set** | **No patients at all**, until an admin sets the hospital. |
| Moved to another hospital by an admin | From the very next click, the new hospital's patients only. |

### Why the whole hospital, and why in full

Coordination crosses care teams. A case manager arranging a follow-up for a heart failure patient needs to see:

- who the doctor is;
- what the risk is;
- why it is rising;
- what has already been tried.

That is the clinical layer. HIPAA allows this use without asking for a reason each time (section 16), but every opening is still recorded (section 12).

---

## 5. Page by page: GLP-1 Analytics

| Page | Can open? | What they see |
|---|---|---|
| **Overview** (home) | ✅ | Their hospital's patients: risk spread, how many are at critical risk, care-team coverage. Hospital figures only, with no cost or ROI tiles. |
| **Patients** | ✅ | Every patient in their hospital, with risk, prediction, segment, drug and main reasons. Filters, search and export work over their hospital only. |
| **Patient page** | ✅ | The full record, including risk factors and what is driving them. It also shows the care team and the **Follow-up** card. They can take or finish the request there. |
| **Staff** | ✅ | Doctors and nurses in their hospital, with how many patients each looks after. |
| **Follow-ups** | ✅ | Their hospital's requests. See section 8. |
| **Segment Explorer** | ✅ | Patient groups, counted over their hospital's patients. |
| **Survival analysis** | ✅ | How long patients stay on therapy, over their hospital's patients. |
| **Budget Simulator, Cost-effectiveness, Cost of Inaction** | ❌ | Hidden from the menu; the server also refuses them. These are for superadmin, hospital admins and insurers. |
| **Settings** | ✅ | Their own account (name, email, role, hospital, how many patients they can see) and password. **No** User Management. |

---

## 6. Page by page: Readmission Risk

| Page | Can open? | What they see |
|---|---|---|
| **Overview** (home) | ✅ | Their hospital: patients, high-risk count, "needs attention", open alerts, doctors and nurses, patients without a doctor or nurse, risk bands, insurer mix. Most tiles open the filtered patient list; the Doctors and Nurses tiles open the Staff page. |
| **Patients** | ✅ | Every patient in their hospital with the full clinical columns (diagnosis, risk drivers, trend). The rising-risk panel sits at the top. |
| **Patient page** | ✅ | The full record: risk, trend, forecast, care plan, care notes, care team, and the **Follow-up** card. |
| **Update patient** | ✅ | Can record changes to a patient's details. |
| **Staff** | ✅ | Doctors and nurses with their workload; clicking a person filters the patient list to their patients. |
| **Follow-ups** | ✅ | Their hospital's requests, with a red badge in the menu. See section 8. |
| **Analytics** | ✅ | Hospital-level charts over their own patients. |
| **Clinician Console** | ✅ | Any doctor's alert inbox in their hospital, chosen from a list. A banner warns that anything answered is recorded against that doctor. |
| **Model Studio** | ❌ | Training and deploying the hospital's own models: hospital admin and superadmin. |
| **Manual entry / upload** | ❌ | Adding patients: hospital admin and superadmin. |
| **Settings** | ✅ | Own account and password. **No** User Management. |

---

## 7. Permissions at a glance

✅ allowed · ❌ refused by the server · — not in that app

| Action | GLP-1 | Readmission Risk |
|---|---|---|
| See every patient in their hospital, in full | ✅ | ✅ |
| See patients of another hospital | ❌ | ❌ |
| Open a patient without giving a reason | ✅ | ✅ |
| Assign or change a patient's doctor and nurses | ✅ | ✅ |
| Request a follow-up | ❌ (doctors and nurses ask) | ❌ (doctors and nurses ask) |
| See the hospital's follow-up list, take one, mark one done | ✅ | ✅ |
| Add care notes | — | ✅ |
| Update a patient's details | — | ✅ |
| Read and answer doctors' clinical alerts | — | ✅ |
| Get rising-risk alerts | — | ✅ |
| Register a doctor for alerts | — | ❌ |
| Add or upload patients | — | ❌ |
| Train or deploy models (Model Studio) | — | ❌ |
| Cost and ROI pages | ❌ | — |
| Add, approve, suspend or remove accounts | ❌ | ❌ |
| Read the access log | ❌ | ❌ |
| Look at another hospital via the hospital picker | ❌ (superadmin only) | ❌ (superadmin only) |
| Change their own password | ✅ | ✅ |

---

## 8. Follow-up requests in detail

Follow-ups are the case manager's main job. They work the same way in both apps.

### 8.1 The life of a request

```
  DOCTOR / NURSE               CASE MANAGER                          DOCTOR / NURSE
  ──────────────               ────────────                          ──────────────
  "Request follow-up"
   urgent or routine
   + optional note
          │
          ▼
     ┌─────────┐   "I'll take it"   ┌─────────────┐   "Mark done"   ┌──────────────────┐
     │  OPEN   │ ─────────────────► │ IN PROGRESS │ ──────────────► │       DONE       │
     │(waiting)│                    │ (claimed by │                 │ Booked           │
     └─────────┘                    │  one person)│                 │ Couldn't reach   │
          │                         └─────────────┘                 │ Not needed       │
          └──────────── "Mark done" directly (takes it on the way) ►└──────────────────┘
                                                                            │
                                                         outcome shows on the patient's
                                                         page for 30 days
```

### 8.2 Where requests come from

| Source | App | How |
|---|---|---|
| **Patient page** | Both | A doctor or nurse presses **Request follow-up**, chooses *Urgent (within days)* or *Routine (within weeks)*, and may add a note. |
| **Reply to a clinical alert** | Readmission Risk | A doctor or nurse answers an alert in the Clinician Console and ticks an action a case manager arranges: **telephone the patient, bring the clinic review forward, arrange bloods or investigations, arrange a home visit, or increase monitoring frequency**. A request is created automatically. The doctor's reply is saved exactly as before; the request is added on top. |

How alert replies map to requests:

| Reply | Request |
|---|---|
| Urgency *routine* | Routine |
| Urgency *urgent* or *immediate* | **Urgent** |
| "Increase monitoring frequency" | **Request.** Arranging more frequent check-ins is coordination work. |
| Only "Send to the emergency department" | **No request.** That is an emergency now, not a booking. |
| Only "No action", "Medication change" or "Refer to another specialty" | **No request.** These are the doctor's own actions. |
| Reply from a case manager | **No request.** They would be asking themselves; they simply do it. |
| Reply from the superadmin on a doctor's behalf | A request, as if the doctor had replied. |
| A request is already open for that patient | **No second request.** The reply is added to the open one as a note, and makes it urgent if the reply was urgent. |

### 8.3 The rules

| Rule | Detail |
|---|---|
| **One open request per patient** | If someone has already asked, the next person sees the open request and adds a note to it. Enforced by the database, so two clicks at the same moment cannot create two. |
| **Taking is exclusive** | Once a case manager takes a request, other case managers see "*name* is handling this" and cannot take it or finish it. |
| **The admin can step in** | A hospital admin (or the superadmin) can take or finish any request in the hospital, for example when a case manager is away. |
| **Finishing without taking** | A case manager may mark an untaken request done directly; it is taken on the way, so the record still shows who handled it. |
| **Overdue** | A request still open after **7 days** is marked overdue and highlighted red. |
| **Outcomes** | *Booked*, *Couldn't reach patient* or *Not needed*, plus an optional note of up to 500 characters. |
| **After it is done** | It leaves the to-do list. The outcome shows on the patient's page and in the "Done (last 30 days)" tab for **30 days**. A new request can then be made. |
| **Nothing is deleted** | Every step (requested, note added, taken, done) is kept in the request's history with who did it and when. |
| **No hospital, no list** | A case manager with no hospital set sees an empty list. |

### 8.4 What the follow-up list shows, and what it leaves out

**Shows:** patient number, current risk, care team, how soon (urgent or routine), who asked and when, their note and any later notes, and the status.

**Leaves out:** diagnoses, risk drivers and clinical notes. Booking a call or an appointment does not need them (*minimum necessary*, 45 CFR 164.514(d)). The full record is one click away, and that click is logged like any other opening.

### 8.5 What Preventra does not do

Preventra **does not book appointments** and does not contact patients. It passes the request
along and records that it was dealt with. The booking itself lives in the hospital's own
scheduling system. The "Mark done" dialog reminds the case manager of this.

### 8.6 If the hospital has no case manager

In **GLP-1**, new requests go to the **hospital admin's** bell instead, so a request is never left unseen.
In **Readmission Risk**, the hospital admin sees the same Follow-ups page and badge.

---

## 9. Care teams

A patient's care team is **one doctor and any number of nurses**. Doctors and nurses only see
the patients they are assigned to, so a patient with no care team is invisible to every
clinician. That is why keeping care teams complete is part of the case manager's job.

| | |
|---|---|
| **Who can assign** | Case managers, hospital admins, superadmin. |
| **Where** | The patient's page (Care team, "Change"), or several patients at once from the patient list. |
| **Limits** | Only people from the **same hospital** can be assigned; the server refuses anyone else. |
| **Takes effect** | Immediately. The doctor's or nurse's very next click already includes the patient. |
| **Finding gaps** | The Overview counts patients with **no doctor** and **no nurse**; each count opens the list of those patients. The Staff page shows each person's workload. |
| **Doctors in Readmission Risk** | A doctor must be **registered for alerts** before they can be assigned. Registering is done by the hospital admin. |

In GLP-1, a doctor or nurse who is newly added to a patient gets a "New on your care team" alert.

---

## 10. Alerts and notifications

| Where | What the case manager gets |
|---|---|
| **GLP-1 bell** | New follow-up requests nobody has taken yet. A request leaves the bell once someone takes it. Case managers do **not** get the doctors' patient alerts (risk up, new patient), because those are for the patient's own care team. |
| **Readmission Risk: Follow-ups badge** | A red count on the menu item: requests nobody has taken yet. Updates on every page change, and at once when a request is taken, finished or created. |
| **Readmission Risk: rising-risk bell and panel** | Patients in their hospital whose latest weekly check got worse: a red flag, a large rise, or a move up a risk band. A patient can be marked as seen; a newer worsening brings them back. |
| **Readmission Risk: Clinician Console** | Doctors' open clinical alerts, if the case manager opens a doctor's inbox. |

Nothing is emailed or texted. Notifications appear only inside the apps.

---

## 11. The assistant (chatbot)

Both apps have an assistant. For a case manager it:

- answers only about **their hospital's patients**, using the same limits as the pages (a question about another hospital's patient gets nothing);
- **leads with patients and clinical risk**, not money;
- in GLP-1, **refuses cost and ROI questions** (budget, payer ROI, cost of inaction), the same as the pages;
- keeps each conversation private to the account that started it. Another account cannot open it, even with its id.

The assistant gives decision support. It is not a clinician and does not give treatment advice for real patients.

---

## 12. What is recorded

### Opening a patient

Every time a case manager opens a patient's record, an entry is written to the **shared access
log**. There is one log for both apps (`shared_identity.access_log`).

| Field | Example |
|---|---|
| Who | hina.qureshi@city-hospital.org (case manager) |
| Patient | MIMIC-1101 |
| App | Readmission Risk |
| Reason | **Care coordination (case manager)**, filled in automatically and never asked |
| Hospital | City Hospital |
| When | 13 Oct 2026, 10:42 |

- **One entry per patient per sign-in**, so the log stays readable. Opening the same patient ten times in one session is one entry; signing in again tomorrow and opening them is another.
- Viewing a **list** (Patients, Follow-ups, Overview) is not an opening and is not logged. Opening an individual record is.
- The **hospital admin** can read their hospital's log. The **superadmin** can read every hospital's. The case manager cannot read it.

### Follow-ups

Each request keeps its own history: who asked, every note, who took it, who finished it, the outcome, and the time of each step.

### Care-team changes

Every change is stored with who made it, when, and the care team before and after. The patient's
record holds the current care team and the latest change. Every earlier change is kept in a
separate archive that is never edited or deleted. In Readmission Risk the archive is the
`care_team_history` collection, and case managers, hospital admins and the superadmin can read a
patient's history.

---

## 13. The account: from sign-up to sign-out

| Step | What happens |
|---|---|
| **Sign up** | On the portal, choose **Case manager** and the hospital. (Case manager is also the default when no role is chosen.) |
| **Waiting for approval** | The account can sign in but sees nothing, with a "waiting for approval" message, until the hospital admin approves it in User Management. |
| **Added by an admin instead** | The admin adds the person directly and they get a **temporary password**. At first sign-in they must choose a new one; until they do, they can do nothing else. |
| **Signing in** | Once, at the portal. The same sign-in works in both apps for up to **12 hours**. |
| **Password** | Changed in **Settings** in either app; at least 8 characters, one password for both apps. |
| **Role change, hospital move, suspension, removal** | Done by the hospital admin. Takes effect on the **next click**, not at the next sign-in, because every request re-reads the account from the database. |
| **Signing out** | From either app; signs out of both. |

---

## 14. What a case manager cannot do, and why

| Cannot | Why | Who can |
|---|---|---|
| Add, approve, suspend or remove people | Account management is separated from patient care, so the person handling patients cannot also grant themselves or others access | Hospital admin, superadmin |
| Read the access log | The log is how the hospital checks who opened records, including the case manager's own openings. Someone being audited should not hold the audit. | Hospital admin, superadmin |
| See cost and ROI pages (GLP-1) | These are about the insurer's and hospital's money, not patient care | Hospital admin, insurer, superadmin |
| Request a follow-up | They arrange follow-ups; a request is how doctors and nurses hand one to them | Doctors, nurses |
| Add or upload patients, register doctors for alerts | These set up the hospital's data and routing | Hospital admin, superadmin |
| Train or deploy models | Choosing the model that scores the hospital's patients is an administrative decision | Hospital admin, superadmin |
| See other hospitals | Each hospital's patients belong to that hospital | Superadmin only |

---

## 15. How the rules are enforced and tested

- **The server decides.** Hiding a page or a button is only a courtesy. Every request is checked on the server, and a refused action gets "not allowed" (403) or "not found" (404), whatever the screen shows.
- **Checked against the live account.** The sign-in token only proves who someone is. Their role, hospital and status are re-read from the database on every request, so changes take effect immediately (section 13).
- **"Not found" for other hospitals.** Asking for another hospital's patient gives the same answer as asking for one that does not exist, so nothing can be learned by guessing patient numbers.
- **Two copies, kept in step.** Each app has its own copy of the rules, because the apps are deployed separately. They must be changed together.
- **Automated tests.** Both apps have tests that sign in as every role and call every page of the API. They check that no response mentions a patient outside that account's set. There are also tests for follow-ups, care-team assignment and logging:
  - `GLP1/Backend/tests/test_isolation.py`, `test_followups.py`, `test_hospital_pages.py`
  - `Readmissions/tests/units/test_isolation.py`, `test_followups.py`, `test_hospital_pages.py`

---

## 16. HIPAA basis

| Requirement | How the case manager role meets it |
|---|---|
| **Access limited to what the job needs**, [45 CFR 164.308(a)(4)](https://www.ecfr.gov/current/title-45/subtitle-A/subchapter-C/part-164/subpart-C/section-164.308) | Own hospital only. No user management, access log, cost pages or model training. |
| **Minimum necessary**, [45 CFR 164.502(b)](https://www.ecfr.gov/current/title-45/subtitle-A/subchapter-C/part-164/subpart-E/section-164.502) and [164.514(d)](https://www.ecfr.gov/current/title-45/subtitle-A/subchapter-C/part-164/subpart-E/section-164.514) | Care coordination is part of treatment and health care operations, so the full record is available. The follow-up list shows only what booking needs. |
| **Treatment and operations uses**, [45 CFR 164.506](https://www.ecfr.gov/current/title-45/subtitle-A/subchapter-C/part-164/subpart-E/section-164.506) | Coordinating care is a permitted use within the hospital, which is why no reason is asked. |
| **Unique user identification**, [45 CFR 164.312(a)(2)(i)](https://www.ecfr.gov/current/title-45/subtitle-A/subchapter-C/part-164/subpart-C/section-164.312) | Personal accounts only; no shared logins. |
| **Audit controls**, [45 CFR 164.312(b)](https://www.ecfr.gov/current/title-45/subtitle-A/subchapter-C/part-164/subpart-C/section-164.312) | Every patient opening is logged with the reason "Care coordination"; follow-ups keep a full history. |
| **Prompt removal of access**, [45 CFR 164.308(a)(3)(ii)(C)](https://www.ecfr.gov/current/title-45/subtitle-A/subchapter-C/part-164/subpart-C/section-164.308) | Suspension or removal takes effect on the next request. |

---

## 17. Open questions

These are decisions not yet made. They are noted here so they are not forgotten.

1. **Case managers across several hospitals.** An account belongs to one hospital. Someone who coordinates across a hospital group needs one account per hospital today.
2. **Department-level access.** Case managers see the whole hospital. A large hospital may want case managers limited to a department or ward. That is not built yet; it would follow the same pattern as doctors and nurses.
3. **Reminders outside the app.** Overdue requests are highlighted on screen, but nobody is emailed. Worth adding if case managers do not open the app every day.
4. **Reassigning a taken request.** Today only the case manager who took a request, or the admin, can finish it. There is no "hand over to a colleague" button.
5. **GLP-1 follow-ups from alerts.** In Readmission Risk, a doctor's alert reply can create a request. GLP-1 has no clinical-alert inbox, so requests there come only from the patient page.

---

## 18. Where it lives in the code

| What | GLP-1 | Readmission Risk |
|---|---|---|
| Role lists and patient visibility | `GLP1/Backend/core/access.py` | `Readmissions/api/access.py` |
| Automatic logging reason | `AUTOMATIC_REASONS`, `LOGGED_REASONS` in `access.py` | same names in `access.py` |
| Access log | `GLP1/Backend/core/access_log.py` | `Readmissions/api/access_log.py` |
| Follow-ups (server) | `GLP1/Backend/core/followups.py`, `routers/followups.py` | `Readmissions/api/followups.py`, routes in `api/main.py` |
| Follow-ups (screens) | `GLP1/Frontend/src/pages/FollowUps.jsx`, `components/followups/` | `Readmissions/frontend/src/pages/FollowUps.jsx`, `components/followups/` |
| Care teams | `GLP1/Backend/core/hospital.py` | `Readmissions/api/hospital.py` |
| Care-team history | — | `Readmissions/api/hospital.py` (`care_team_history`) |
| Bell / alerts | `GLP1/Backend/core/notifications.py` | `Readmissions/api/risk_watch.py` |
| Menu by role | `GLP1/Frontend/src/context/RoleContext.jsx` | `Readmissions/frontend/src/roles.js` |
| Accounts, sign-in, passwords | Verified in `GLP1/Backend/core/security.py` | Issued in `Readmissions/api/auth.py`, `api/user_admin.py` |
