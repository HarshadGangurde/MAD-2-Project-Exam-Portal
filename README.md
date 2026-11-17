# Examination Management Portal (EMP) - V2

Flask REST API + Vue 3 (CDN, no build step) + Bootstrap 5 + SQLite + Redis + Celery.

## Run everything with ONE command
```bash
python run.py           # Windows alternatives: .\run.cmd (or double-click it)      Linux/macOS: sh run.sh
```
That is all. The first time it creates the virtual environment, installs `requirements.txt`, loads the demo data, and then
starts **Redis** (if one is installed), the **API + UI**, the **Celery worker** and **Celery beat** in the same terminal,
runs a self-check (PASS/FAIL for the Redis cache and the Celery jobs) and prints the URL and logins. About 8 seconds on a
warm start. **Ctrl+C stops everything.** Re-running is always safe: leftovers of an earlier run are stopped first.

| Command | What it does |
|---|---|
| `python run.py` | everything above |
| `python run.py --demo` | also show the one-click demo logins on the sign-in page (for the video) |
| `python run.py --seed` | wipe and reload the demo data first (use before recording) |
| `python run.py --no-seed` | on a first run, start with only the admin instead of demo data |
| `python run.py --no-redis` | API + UI only (cache and jobs fall back to memory/inline; no worker, no beat) |
| `python run.py --check` | run the Redis/Celery self-check (and the reminder job) against a running stack |
| `python run.py --stop` | stop a stack that was left running |
| `python run.py --port 5001` | use another port (default 5000) · `--verbose` shows every request · `--open` opens the browser |

**Needs:** Python 3.10+ (developed on 3.13) and internet on the very first run only (to install the packages).
**Redis** is found automatically (PATH, `REDIS_SERVER`, Memurai, `~/redis-win`, the usual Linux/macOS paths); if none is
installed `run.py` says so and carries on without it. Install one with: Windows - Memurai or a `redis-server.exe`
(unzip into `C:\Users\<you>\redis-win`); Linux `apt install redis-server`; macOS `brew install redis`. If Redis stops by
itself the reason is in `instance/redis.log`. Optional: MailHog (SMTP on 127.0.0.1:1025) to see e-mails; without it mails
are only logged.

Look inside Redis while the app runs: `redis-cli keys "emp:*"` (cache, counters, revoked tokens, in database 0) and
`redis-cli -n 1 keys "*"` (the Celery broker). To see the scheduler fire quickly, set the reminder to a minute from now:
`REMINDER_HOUR=15 REMINDER_MINUTE=42 python run.py` (PowerShell: `$env:REMINDER_HOUR="15"; $env:REMINDER_MINUTE="42"; python run.py`).

**Dependency note:** `requirements.txt` pins `kombu==5.3.7` on purpose. Celery 5.4.0 accepts any kombu below 6, and the
newest kombu refuses to talk to redis-py 5.0.8 ("Redis transport requires redis-py versions 6.1.0 or later"), so an
unpinned install breaks the Celery worker.

<details><summary>Starting the parts by hand (what run.py does)</summary>

```bash
cd backend
python app.py                                                       # API + UI on http://localhost:5000
python -m celery -A celery_app.celery worker -l info -P solo        # batch jobs (-P solo is required on Windows)
python -m celery -A celery_app.celery beat -l info                  # schedules: daily reminder, monthly report on the 1st
```
Set `FLASK_DEBUG=1` before `python app.py` only if you want the debugger/auto-reload. The SQLite database (`emp.db`)
and the single admin are created automatically on first start.
</details>

## Logins
On the sign-in page first choose **Admin, Examiner or Student**, then enter email and password. The server checks that
the account really has the chosen role (a student's password under "Admin" is refused). The last choice is remembered
on that browser.

| Role | Email | Password |
|---|---|---|
| Admin (created on first start) | `admin@gmail.com` | `admin@123` |
| Examiners / students (only after running `seed_data.py`) | e.g. `rajesh@university.edu` / `rahul.verma@student.edu` | `examiner123` / `student123` |

Choose your own admin with the `ADMIN_EMAIL` / `ADMIN_PASSWORD` environment variables **before the first start**
(the admin is only created when the database has none; to change it later delete `emp.db`, or change the password
under *Profile*). The default password is for local demos only: the server logs a warning while it is in use, and
with `EMP_ENV=production` it refuses to start until `ADMIN_PASSWORD` is set.

The login page shows **no** demo shortcuts by default. For a demo or screen recording run `python run.py --demo` to get
the one-click Admin / Examiner / Student buttons for the demo accounts. No credentials are stored in the JavaScript file.

## Sample data
`python run.py` loads it automatically on a first run and `python run.py --seed` reloads it. By hand:
```bash
python seed_data.py     # DELETES all courses, exams, slots, bookings and non-admin users, then loads demo data
```

## Tests
```bash
venv\Scripts\python -m unittest discover -s tests -v
```
Runs against a throwaway database; needs no Redis or Celery worker.

## Configuration (environment variables)
`JWT_SECRET_KEY` (otherwise a random 32-byte secret is generated once into `instance/secret.key`),
`ADMIN_EMAIL`, `ADMIN_PASSWORD`, `REDIS_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`,
`TIMEZONE` (IANA name used by the Celery schedule, default `Asia/Kolkata`),
`REMINDER_HOUR` / `REMINDER_MINUTE` (daily reminder time in that timezone, default 08:00), `MAIL_HOST`, `MAIL_PORT`,
`MAIL_FROM`, `GCHAT_WEBHOOK` (optional Google Chat webhook for reminders), `EMP_ENV` (`production` enforces a
non-default admin password), `EMP_DEMO_LOGIN` (`1` shows the demo login buttons), `TRUSTED_PROXIES` (number of reverse
proxies in front of the app, so that rate limits use the real client address; default 0).

## Business rules worth knowing
- **Examination lifecycle** (enforced by the API; the UI only offers valid steps): Draft → Slot Creation → Booking Open
  → Closed → Completed. Backward steps exist only where safe (e.g. Booking Open → Slot Creation to add a session).
  *Completed* needs every booked student evaluated and no later session with bookings.
- **Publishing results** needs a Closed/Completed examination. If booked students are still unevaluated the API answers
  409 and the UI asks a second time ("Publish anyway"). Publishing is final and locks the examination at Completed.
- **Examiners** can only act on examinations they are *currently* assigned to. An examiner who still has open slots in an
  examination cannot be removed from it: reassign or cancel those slots first.
- **Admin slots**: the admin may add slots while an examination is in *Slot Creation* or *Booking Open* (the examiner's
  slot-date window is not applied to the admin), never to Draft, Closed or Completed examinations.
- **Re-evaluating** a student before publication keeps the earlier version (`GET /api/bookings/<id>/evaluation-history`).
- **Sign-in lock-out** counts only failed attempts: 10 per minute per IP address and 10 per 5 minutes per account.
- **Audit log**: sensitive actions (status changes, publication, evaluations, assignments, slot changes) are appended
  to `instance/audit.log`.
- **Concurrency**: the "check for a clash, then save" steps (slot overlap, student overlap, seat booking) run one at a
  time inside the server process, so simultaneous requests cannot both win. This assumes one server process (as
  started with `python app.py`); running several worker processes would need a database-level guard instead.
- **Content-Security-Policy** allows `'unsafe-eval'` because Vue's CDN build compiles its templates in the browser.
  Removing it would require a build step with pre-compiled templates, which this project deliberately does not use.

## Suggested demo flow
1. Admin: create a course, an examiner, an examination; add rubric criteria; assign the examiner; set status **Slot Creation**.
2. Examiner: log in, create slots. Admin sets status **Booking Open**.
3. Student: register, book a slot, cancel/rebook, export history (CSV job).
4. Examiner: evaluate the student with the rubric. Admin: publish results. Student: view result + scorecard.

## Scheduled jobs
Times are in `TIMEZONE` (default Asia/Kolkata).
- **Daily reminder** (08:00, see `REMINDER_HOUR`): each student booked for *tomorrow* gets one reminder (e-mail, optional Google Chat, in-app). Re-running the job never sends the same reminder twice.
- **Monthly report** (1st of the month, 07:00): previous month's statistics e-mailed to the admin as HTML with a PDF attached (once per month).

## Trigger scheduled jobs manually
```bash
cd backend
python -c "from tasks import daily_reminders, monthly_report; print(daily_reminders()); print(monthly_report())"
```

## Layout
- `backend/` `app.py` (app factory, admin seed, error handlers), `api.py` (all REST endpoints), `models.py`, `cache.py` (Redis cache, rate limits, broker check), `celery_app.py` + `tasks.py` (jobs), `services.py` (mail, chat, report)
- `templates/index.html` single entry point; `static/js/app.js` Vue app (router, pages); `static/sw.js` + manifest (add to desktop)
- `tests/` API test suite, `openapi.yaml` API definition, `REPORT.md` project report
- `run.py` (+ `run.cmd`, `run.sh`) the single command that starts everything; `check_services.py` verifies the Redis/Celery side
- `prepare_submission.ps1 -RollNumber <roll>` builds a clean ZIP (no venv, database, logs or caches)
