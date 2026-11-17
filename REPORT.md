# Examination Management Portal (EMP) V2 - Project Report

## 1. Student details
- Name: **<your full name>**  |  Roll number: **<roll number>**  |  Email: **<student email>**
- <a couple of lines about you>

## 2. Project details
**Problem statement.** Institutions manage examinations with spreadsheets and email. Build a web portal where an Admin sets up courses, examinations, rubrics and examiners; Examiners publish availability slots and evaluate students against rubrics; Students register, book slots, and view published results. Batch jobs (daily reminder, monthly report, CSV export) and Redis caching are required.

**Approach.**
1. Modelled the domain first (users, courses, exams, rubrics, slots, bookings, evaluations) with one unified `users` table and a `role` column.
2. Built a Flask REST API where every route is wrapped by a `roles(...)` decorator (JWT) and validates input; business rules (slot windows, one booking per exam, capacity, evaluate-own-slots-only, cancellation deadline) live in the API, not the UI.
3. Built a single-page Vue 3 UI (CDN, one Jinja entry page) with a page per role task, Bootstrap for styling.
4. Added Redis caching (exam lists, rubrics, schedules, TTL 60-300 s, invalidated on writes) and Celery jobs (beat schedules + a student-triggered CSV export with an in-app alert).
5. Prevented overbooking with a DB `CHECK (available_seats >= 0)` and an atomic `UPDATE ... WHERE available_seats > 0`.

## 3. AI / LLM declaration
<State the tools used and the extent, e.g. "Claude Code generated the initial Flask API, Vue pages and Celery tasks; I reviewed, ran and tested every feature and can explain all code.">

## 4. Frameworks and libraries
Flask, Flask-SQLAlchemy (SQLite, tables created in code), Flask-JWT-Extended (token auth), redis-py, Celery (+ beat), Vue 3 (CDN, with a small hand-written router) and Bootstrap 5 (CDN; icons are built-in Unicode glyphs, system fonts). The dashboard charts use Chart.js (CDN). The PDF report, input validation, rate limiting (Redis counters with in-memory fallback), token revocation (Redis) and the test suite (`unittest`) use only the Python standard library and the packages above - no additional libraries.

## 5. ER diagram
Paste the diagram below into https://mermaid.live and export it as an image for the PDF.
```mermaid
erDiagram
  USERS ||--o{ BOOKINGS : "student makes"
  USERS ||--o{ SLOTS : "examiner creates"
  USERS ||--o{ EVALUATIONS : "examiner gives"
  USERS ||--o{ NOTIFICATIONS : receives
  USERS ||--o{ EXPORT_JOBS : triggers
  USERS }o--o{ EXAMS : "assigned via EXAM_EXAMINERS"
  COURSES ||--o{ EXAMS : has
  EXAMS ||--o{ RUBRICS : has
  EXAMS ||--o{ SLOTS : has
  EXAMS ||--o{ BOOKINGS : "denormalised link"
  SLOTS ||--o{ BOOKINGS : holds
  BOOKINGS ||--o| EVALUATIONS : "evaluated as"
  EVALUATIONS ||--o{ EVALUATION_SCORES : contains
  EVALUATIONS ||--o{ EVALUATION_HISTORY : "earlier versions"
  RUBRICS ||--o{ EVALUATION_SCORES : "scored in"
  USERS { int id PK string email UK string password_hash string name string role "admin|examiner|student" string phone string department string status }
  COURSES { int id PK string code UK string name text description string status }
  EXAMS { int id PK int course_id FK string name string type int duration int max_marks date slot_start date slot_end date booking_start date booking_end string status bool results_published }
  RUBRICS { int id PK int exam_id FK string criterion float max_marks float weightage text description }
  SLOTS { int id PK int exam_id FK int examiner_id FK date date time start_time time end_time int capacity int available_seats "CHECK >= 0" string status string meeting_link }
  BOOKINGS { int id PK int student_id FK int slot_id FK int exam_id FK datetime booked_at string status }
  EVALUATIONS { int id PK int booking_id FK_UK int student_id int examiner_id float total_marks text remarks datetime evaluated_at string status }
  EVALUATION_SCORES { int id PK int evaluation_id FK int rubric_id FK float marks }
  EVALUATION_HISTORY { int id PK int evaluation_id FK int examiner_id FK float total_marks text remarks text scores_json datetime evaluated_at datetime superseded_at }
  NOTIFICATIONS { int id PK int user_id FK string message bool read }
  EXPORT_JOBS { int id PK int user_id FK string status string filename }
```

## 6. API resource endpoints (all under `/api`; full spec in `openapi.yaml`)
| Area | Endpoints |
|---|---|
| Auth | `POST /auth/register`, `POST /auth/login`, `GET/PUT /auth/me` |
| Courses | `GET/POST /courses`, `PUT/DELETE /courses/<id>` |
| Exams | `GET/POST /exams`, `GET/PUT/DELETE /exams/<id>`, `PUT /exams/<id>/examiners`, `POST /exams/<id>/status`, `POST /exams/<id>/publish` |
| Rubrics | `GET/POST /exams/<id>/rubrics`, `PUT/DELETE /rubrics/<id>` |
| Examiners / Students | `GET/POST /examiners`, `PUT /examiners/<id>`, `GET /students`, `PUT /students/<id>/status` |
| Slots | `GET/POST /slots`, `PUT/DELETE /slots/<id>`, `PUT /slots/<id>/examiner`, `GET /slots/<id>/bookings`, `POST /slots/<id>/complete` |
| Bookings | `GET/POST /bookings`, `POST /bookings/<id>/cancel`, `PUT /bookings/<id>/reschedule`, `POST /bookings/<id>/evaluate`, `GET /bookings/<id>/evaluation-history`, `GET /results` |
| Dashboards / admin | `GET /dashboard`, `GET /admin/charts`, `GET /admin/search`, `GET /admin/report`, `GET /admin/report/pdf`, `POST /admin/report/send` |
| Notifications / export | `GET /notifications`, `POST /notifications/read`, `POST /export/history`, `GET /export/<id>`, `GET /export/<id>/download` |

## 7. Architecture and features
`backend/`: `app.py` (factory, admin seed), `api.py` (controllers), `models.py`, `cache.py`, `celery_app.py` + `tasks.py`, `services.py` (mail, chat webhook, report HTML/PDF). `templates/index.html` is the only template; `static/js/app.js` is the Vue app; `static/sw.js` + manifest give "add to desktop".

Features: all core admin/examiner/student functions; an enforced examination lifecycle (Draft > Slot Creation > Booking Open > Closed > Completed); slot-creation and booking windows enforced by status and dates; clash checks that hold under simultaneous requests; evaluation history and an audit log; weighted rubric scoring; result publishing with email and in-app notification; search and filters; role dashboards; Chart.js analytics charts; printable scorecard; daily reminder (email + optional Google Chat, time set by `REMINDER_HOUR/MINUTE`); monthly report (HTML email with PDF attached, also downloadable); async CSV export with completion alert.

## 8. Video
<Google Drive link, access: anyone with the link>
