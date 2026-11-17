import base64
import json
import logging
import re
import threading
from datetime import date, datetime, timedelta
from functools import wraps

from flask import Blueprint, Response, jsonify, request, send_from_directory, current_app
from flask_jwt_extended import create_access_token, get_jwt_identity, jwt_required
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload, selectinload

import cache
from models import (db, User, Course, Exam, Rubric, Slot, Booking, Evaluation, EvaluationScore, EvaluationHistory,
                    Notification, ExportJob, EXAM_TYPES, EXAM_STATUSES, EXAM_TRANSITIONS, utc_now)
from services import email_html, monthly_report_html, monthly_report_pdf, monthly_stats, send_mail

api = Blueprint("api", __name__)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
audit_log = logging.getLogger("emp.audit")
LOGIN_ROLES = {"admin": "an admin", "examiner": "an examiner", "student": "a student"}  # role -> wording for messages

# "Check for a clash, then insert" is not atomic, so every endpoint that does it runs one at a time. The app is a
# single process (threaded dev server), which is what makes an in-process lock sufficient. For several worker
# processes this would have to move into the database.
_schedule_lock = threading.RLock()


def serialized(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        with _schedule_lock:
            return fn(*a, **kw)
    return wrapper


def audit(u, action, **info):
    """Append one line to instance/audit.log: who did what to which record."""
    audit_log.info("user=%s role=%s action=%s %s", u.id, u.role, action,
                   " ".join(f"{k}={v}" for k, v in info.items()))


class ApiError(Exception):
    def __init__(self, msg, code=400, **extra):
        self.msg, self.code, self.extra = msg, code, extra


def clean(v, label="field", required=False):
    """Trimmed text or None. Output escaping is left to Vue (and html.escape in emails), so nothing is rewritten."""
    if v is None:
        v = ""
    if not isinstance(v, str):
        raise ApiError(f"'{label}' must be text")
    v = v.strip()
    if required and not v:
        raise ApiError(f"'{label}' is required")
    if len(v) > 2000:
        raise ApiError(f"'{label}' is too long")
    return v or None


def to_int(v, label):
    """Integer from JSON/query input; anything else is a 400 instead of a crash."""
    if isinstance(v, bool):
        raise ApiError(f"'{label}' must be a number")
    try:
        return int(v)
    except (ValueError, TypeError):
        raise ApiError(f"'{label}' must be a number")


def valid_password(pw):
    if not isinstance(pw, str) or not 6 <= len(pw) <= 128:
        raise ApiError("Password must be 6-128 characters")
    return pw


def valid_email(v):
    if not isinstance(v, str) or len(v) > 120 or not EMAIL_RE.match(v.strip()):
        raise ApiError("Invalid email")
    return v.strip().lower()


def check_phone(v):
    v = clean(v, "phone")
    if v and not re.match(r"^[0-9+\- ]{7,15}$", v):
        raise ApiError("Invalid phone number")
    return v


def valid_link(v):
    """Meeting links are rendered as <a href>, so only http(s) is allowed (blocks javascript: URLs)."""
    v = clean(v, "meeting_link")
    if v and (len(v) > 255 or not re.match(r"^https?://[^\s<>\"']+$", v, re.I)):
        raise ApiError("Meeting link must be a valid http:// or https:// URL")
    return v


def course_status(v):
    if v not in ("Active", "Inactive"):
        raise ApiError("Status must be Active or Inactive")
    return v


def rate_limit(bucket, limit, window):
    if cache.hit(f"{bucket}:{request.remote_addr}", window) > limit:
        raise ApiError("Too many attempts. Please wait a minute and try again.", 429)


def error_response(e):
    return jsonify(error=e.msg, **e.extra), e.code


# ---------- helpers ----------
def current_user():
    u = db.session.get(User, int(get_jwt_identity()))
    if not u or u.status != "Active":
        raise ApiError("Account inactive or not found", 401)
    return u


def roles(*allowed):
    def deco(fn):
        @wraps(fn)
        @jwt_required()
        def wrapper(*a, **kw):
            u = current_user()
            if u.role not in allowed:
                raise ApiError("Forbidden for your role", 403)
            return fn(u, *a, **kw)
        return wrapper
    return deco


def body():
    return request.get_json(silent=True) or {}


def need(data, *fields):
    for f in fields:
        if data.get(f) in (None, ""):
            raise ApiError(f"'{f}' is required")


def pdate(v, name):
    if v in (None, ""):
        return None
    try:
        return datetime.strptime(v, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        raise ApiError(f"'{name}' must be YYYY-MM-DD")


def ptime(v, name):
    try:
        return datetime.strptime(v, "%H:%M").time()
    except (ValueError, TypeError):
        raise ApiError(f"'{name}' must be HH:MM")


def pint(v, name, minimum=1):
    try:
        n = int(v)
    except (ValueError, TypeError):
        raise ApiError(f"'{name}' must be a number")
    if n < minimum:
        raise ApiError(f"'{name}' must be at least {minimum}")
    return n


def get_or_404(model, id_):
    obj = db.session.get(model, id_)
    if not obj:
        raise ApiError(f"{model.__name__} not found", 404)
    return obj


def notify(user_id, message, link=None):
    db.session.add(Notification(user_id=user_id, message=message[:250], link=link))


def run_task(task, *args):
    """Queue on Celery when the broker answers quickly; otherwise run inline so the feature still works locally."""
    if cache.broker_available():
        try:
            task.delay(*args)
            return
        except Exception:
            pass
    task(*args)


def mail_async(to, subject, html_body, pdf=None):
    """Hand the mail to Celery when a broker answers. Otherwise send it from a background thread: the request must
    not wait for an SMTP connection (a missing mail server costs several seconds per recipient)."""
    from tasks import send_email
    if cache.broker_available():
        try:
            send_email.delay(to, subject, html_body, base64.b64encode(pdf).decode() if pdf else None)
            return
        except Exception:
            pass
    app = current_app._get_current_object()

    def work():
        with app.app_context():
            send_mail(to, subject, html_body, pdf)
    threading.Thread(target=work, daemon=True).start()


def exam_for(u, eid):
    """Load an exam the caller may see: students never see Drafts, examiners only exams they are assigned to now."""
    e = get_or_404(Exam, eid)
    if u.role == "student" and e.status == "Draft":
        raise ApiError("Examination not found", 404)
    if u.role == "examiner" and u not in e.examiners:
        raise ApiError("Examination not found", 404)
    return e


def require_assigned(u, exam):
    """Examiner actions on slots and evaluations need a current assignment, not just an old slot."""
    if u not in exam.examiners:
        raise ApiError("You are no longer assigned to this examination", 403)


def refresh_slot_status(slot):
    if slot.status in ("Cancelled", "Completed"):
        return
    slot.status = "Full" if slot.available_seats <= 0 else "Available"


def clear_exam_cache():
    cache.invalidate("exams:")
    cache.invalidate("schedule:")
    cache.invalidate("rubrics:")


# ---------- auth ----------
@api.post("/auth/register")
def register():
    rate_limit("register", 30, 3600)
    d = body()
    need(d, "email", "password", "name")
    email = valid_email(d["email"])
    valid_password(d["password"])
    phone = check_phone(d.get("phone"))
    if User.query.filter_by(email=email).first():
        raise ApiError("Email already registered", 409)
    u = User(email=email, name=clean(d["name"], "name", required=True), role="student",
             phone=phone, department=clean(d.get("department"), "department"))
    u.set_password(d["password"])
    db.session.add(u)
    db.session.commit()
    return jsonify(u.to_dict()), 201


@api.post("/auth/login")
def login():
    rate_limit("login", 120, 60)  # generous cap on all attempts (password hashing is CPU work)
    d = body()
    need(d, "email", "password")
    role = d.get("role")
    if role is not None and (not isinstance(role, str) or role not in LOGIN_ROLES):
        raise ApiError("'role' must be admin, examiner or student")
    ip_key = f"login_fail_ip:{request.remote_addr}"
    acct_key = f"login_fail_acct:{str(d['email']).strip().lower()}"
    # Only FAILED attempts count towards a lock-out, so successful sign-ins never lock anyone out.
    if cache.count(ip_key) >= 10 or cache.count(acct_key) >= 10:
        raise ApiError("Too many failed sign-in attempts. Please wait a few minutes and try again.", 429)
    u = None
    if isinstance(d["email"], str) and isinstance(d["password"], str):
        u = User.query.filter_by(email=d["email"].strip().lower()).first()
    if not u or not u.check_password(d["password"]):
        cache.hit(ip_key, 60)
        cache.hit(acct_key, 300)
        raise ApiError("Invalid credentials", 401)
    cache.clear(acct_key)
    # The sign-in form asks "Sign in as Admin / Examiner / Student". Checked only after the password is right, so it
    # tells nothing to someone who does not know the credentials.
    if role is not None and u.role != role:
        raise ApiError(f"This account is not {LOGIN_ROLES[role]} account. Choose the role you were given and try again.", 403)
    if u.status != "Active":
        raise ApiError("Account is inactive. Contact the administrator.", 403)
    return jsonify(token=create_access_token(identity=str(u.id)), user=u.to_dict())


@api.post("/auth/logout")
@jwt_required()
def logout_api():
    from flask_jwt_extended import get_jwt
    cache.put("revoked:" + get_jwt()["jti"], 1, current_app.config["JWT_ACCESS_TOKEN_EXPIRES"])
    return jsonify(ok=True)


@api.get("/auth/me")
@roles("admin", "examiner", "student")
def me(u):
    return jsonify(u.to_dict())


@api.put("/auth/me")
@roles("admin", "examiner", "student")
def update_me(u):
    d = body()
    if d.get("name"):
        u.name = clean(d["name"], "name", required=True)
    if "phone" in d:
        u.phone = check_phone(d["phone"])
    if "department" in d:
        u.department = clean(d["department"], "department")
    if d.get("password"):
        u.set_password(valid_password(d["password"]))
    db.session.commit()
    return jsonify(u.to_dict())


# ---------- courses ----------
@api.get("/courses")
@roles("admin", "examiner", "student")
def list_courses(u):
    q = Course.query
    if u.role != "admin":
        q = q.filter_by(status="Active")
    return jsonify([c.to_dict() for c in q.order_by(Course.code).all()])


@api.post("/courses")
@roles("admin")
def create_course(u):
    d = body()
    code = clean(d.get("code"), "code", required=True).upper()
    if len(code) > 20:
        raise ApiError("'code' must be at most 20 characters")
    if Course.query.filter_by(code=code).first():
        raise ApiError("Course code already exists", 409)
    c = Course(code=code, name=clean(d.get("name"), "name", required=True),
               description=clean(d.get("description"), "description"),
               status=course_status(d.get("status", "Active")))
    db.session.add(c)
    db.session.commit()
    return jsonify(c.to_dict()), 201


@api.put("/courses/<int:cid>")
@roles("admin")
def update_course(u, cid):
    c, d = get_or_404(Course, cid), body()
    code = clean(d.get("code", c.code), "code", required=True).upper()
    if len(code) > 20:
        raise ApiError("'code' must be at most 20 characters")
    if code != c.code and Course.query.filter_by(code=code).first():
        raise ApiError("Course code already exists", 409)
    c.code, c.name = code, clean(d.get("name", c.name), "name", required=True)
    c.description = clean(d.get("description", c.description), "description")
    c.status = course_status(d.get("status", c.status))
    db.session.commit()
    clear_exam_cache()
    return jsonify(c.to_dict())


@api.delete("/courses/<int:cid>")
@roles("admin")
def delete_course(u, cid):
    c = get_or_404(Course, cid)
    if c.exams:
        raise ApiError("Course has examinations; mark it Inactive instead", 409)
    db.session.delete(c)
    db.session.commit()
    return jsonify(ok=True)


# ---------- examinations ----------
def validate_exam(d, exam=None):
    need(d, "course_id", "name", "type", "duration", "max_marks")
    if d["type"] not in EXAM_TYPES:
        raise ApiError("Invalid examination type")
    course_id = to_int(d["course_id"], "course_id")
    get_or_404(Course, course_id)
    out = dict(course_id=course_id, name=clean(d["name"], "name", required=True), type=d["type"],
               duration=pint(d["duration"], "duration"), max_marks=pint(d["max_marks"], "max_marks"),
               instructions=clean(d.get("instructions"), "instructions"))
    for f in ("slot_start", "slot_end", "booking_start", "booking_end"):
        out[f] = pdate(d.get(f), f)
    if out["slot_start"] and out["slot_end"] and out["slot_end"] < out["slot_start"]:
        raise ApiError("Slot creation end must be after start")
    if out["booking_start"] and out["booking_end"] and out["booking_end"] < out["booking_start"]:
        raise ApiError("Booking end must be after start")
    return out


@api.get("/exams")
@roles("admin", "examiner", "student")
def list_exams(u):
    key = f"exams:{u.role}:{u.id if u.role == 'examiner' else 0}:{request.query_string.decode()}"
    hit = cache.get(key)
    if hit is not None:
        return jsonify(hit)
    q = Exam.query.join(Course)
    a = request.args
    if a.get("q"):
        like = f"%{a['q']}%"
        q = q.filter(or_(Exam.name.ilike(like), Course.name.ilike(like), Course.code.ilike(like)))
    if a.get("type"):
        q = q.filter(Exam.type == a["type"])
    if a.get("status"):
        q = q.filter(Exam.status == a["status"])
    if a.get("course_id"):
        q = q.filter(Exam.course_id == to_int(a["course_id"], "course_id"))
    if u.role == "student":
        q = q.filter(Exam.status != "Draft")
    elif u.role == "examiner":
        q = q.filter(Exam.examiners.any(User.id == u.id))
    data = [e.to_dict() for e in q.order_by(Exam.id.desc()).all()]
    cache.put(key, data)
    return jsonify(data)


@api.post("/exams")
@roles("admin")
def create_exam(u):
    e = Exam(**validate_exam(body()))
    db.session.add(e)
    db.session.commit()
    clear_exam_cache()
    return jsonify(e.to_dict()), 201


@api.get("/exams/<int:eid>")
@roles("admin", "examiner", "student")
def get_exam(u, eid):
    return jsonify(exam_for(u, eid).to_dict())


@api.put("/exams/<int:eid>")
@roles("admin")
def update_exam(u, eid):
    e = get_or_404(Exam, eid)
    fields = validate_exam(body())
    if fields["max_marks"] != e.max_marks and has_evaluations(e.id):
        raise ApiError("Maximum marks cannot change after evaluations exist", 409)
    for k, v in fields.items():
        setattr(e, k, v)
    db.session.commit()
    clear_exam_cache()
    return jsonify(e.to_dict())


@api.delete("/exams/<int:eid>")
@roles("admin")
def delete_exam(u, eid):
    e = get_or_404(Exam, eid)
    if e.slots:
        raise ApiError("Examination has slots; close it instead of deleting", 409)
    db.session.delete(e)
    db.session.commit()
    clear_exam_cache()
    return jsonify(ok=True)


@api.put("/exams/<int:eid>/examiners")
@roles("admin")
def assign_examiners(u, eid):
    e, ids = get_or_404(Exam, eid), body().get("examiner_ids", [])
    if not isinstance(ids, list):
        raise ApiError("'examiner_ids' must be a list")
    ids = {to_int(i, "examiner_ids") for i in ids}
    found = User.query.filter(User.id.in_(ids), User.role == "examiner").all() if ids else []
    if len(found) != len(ids):
        raise ApiError("Every id in 'examiner_ids' must be an existing examiner")
    keep = {x.id for x in found}
    for x in found:  # new assignments only for active accounts; existing ones stay as they are
        if x not in e.examiners and x.status != "Active":
            raise ApiError(f"{x.name} is inactive and cannot be assigned")
    with _schedule_lock:  # no new slot may appear for an examiner while we check that they are not being cut off
        for x in e.examiners:
            if x.id not in keep and Slot.query.filter(Slot.exam_id == e.id, Slot.examiner_id == x.id,
                                                      Slot.status.in_(("Available", "Full"))).first():
                raise ApiError(f"{x.name} still has open slots in this examination. Reassign or cancel them first.", 409)
        e.examiners = found
        db.session.commit()
    audit(u, "exam.assign_examiners", exam=e.id, examiners=",".join(str(i) for i in sorted(keep)))
    clear_exam_cache()
    return jsonify(e.to_dict())


@api.post("/exams/<int:eid>/status")
@roles("admin", "examiner")
def set_status(u, eid):
    e, status = get_or_404(Exam, eid), body().get("status")
    if status not in EXAM_STATUSES:
        raise ApiError("Invalid status")
    if e.results_published and status != "Completed":
        raise ApiError("Results are published, so the status is locked at Completed", 409)
    if u.role == "examiner":
        require_assigned(u, e)
        if status not in ("Closed", "Completed"):
            raise ApiError("Examiners may only mark an examination Closed or Completed", 403)
    if status == e.status:
        return jsonify(e.to_dict())
    if e.results_published:
        raise ApiError("Results are published, so the status can no longer change", 409)
    if status not in EXAM_TRANSITIONS[e.status]:
        allowed = ", ".join(EXAM_TRANSITIONS[e.status]) or "none"
        raise ApiError(f"An examination that is {e.status} can only move to: {allowed}", 409)
    if status == "Draft" and (e.slots or Booking.query.filter_by(exam_id=e.id).first()):
        raise ApiError("An examination that has slots or bookings cannot go back to Draft", 409)
    if status == "Slot Creation" and not e.examiners:
        raise ApiError("Assign at least one examiner first")
    if status == "Booking Open":
        if not e.rubrics:
            raise ApiError("Create at least one rubric criterion first")
        if not Slot.query.filter_by(exam_id=e.id, status="Available").count():
            raise ApiError("No available slots to book yet")
    if status == "Completed":
        if pending_for_exam(e.id):
            raise ApiError("Evaluate all booked students first")
        later = (Slot.query.join(Booking, Booking.slot_id == Slot.id)
                 .filter(Slot.exam_id == e.id, Slot.status != "Cancelled", Slot.date > date.today(),
                         Booking.status != "Cancelled").first())
        if later:
            raise ApiError(f"A session with booked students is still scheduled for {later.date}", 409)
    audit(u, "exam.status", exam=e.id, old=e.status.replace(" ", "_"), new=status.replace(" ", "_"))
    e.status = status
    db.session.commit()
    clear_exam_cache()
    return jsonify(e.to_dict())


def has_evaluations(eid):
    return Evaluation.query.join(Booking, Evaluation.booking_id == Booking.id) \
        .filter(Booking.exam_id == eid).first() is not None


def pending_for_exam(eid):
    return (Booking.query.outerjoin(Evaluation, Evaluation.booking_id == Booking.id)
            .filter(Booking.exam_id == eid, Booking.status == "Booked", Evaluation.id.is_(None)).count())


@api.post("/exams/<int:eid>/publish")
@roles("admin")
def publish_results(u, eid):
    e = get_or_404(Exam, eid)
    if e.results_published:
        raise ApiError("Results are already published", 409)
    if e.status not in ("Closed", "Completed"):
        raise ApiError("Close the examination before publishing its results", 409)
    pending = pending_for_exam(eid)
    if pending and body().get("force") is not True:  # publishing is final, so unevaluated students need a deliberate yes
        raise ApiError(f"{pending} booked student(s) have not been evaluated yet. "
                       "They will get no result if you publish now.", 409, pending_evaluations=pending)
    e.results_published = True
    e.status = "Completed"
    for b in Booking.query.filter(Booking.exam_id == eid, Booking.status == "Completed").all():
        notify(b.student_id, f"Results published for {e.name}", "/student/results")
        mail_async(b.student.email, f"Results published: {e.name}",
                   email_html(f"Your result for {e.name} is available", f"Hi {b.student.name}, the results have been published.",
                              [("Examination", f"{e.name} ({e.course.code})")],
                              note="Sign in to the portal (Results) to see your marks, rubric breakdown and examiner feedback."))
    db.session.commit()
    audit(u, "exam.publish", exam=eid, pending_evaluations=pending)
    clear_exam_cache()
    return jsonify(exam=e.to_dict(), pending_evaluations=pending)


# ---------- rubrics ----------
@api.get("/exams/<int:eid>/rubrics")
@roles("admin", "examiner", "student")
def list_rubrics(u, eid):
    exam_for(u, eid)
    key = f"rubrics:{eid}"
    hit = cache.get(key)
    if hit is None:
        hit = [r.to_dict() for r in Rubric.query.filter_by(exam_id=eid).order_by(Rubric.id)]
        cache.put(key, hit, 300)
    return jsonify(hit)


def rubric_fields(d):
    need(d, "criterion", "max_marks")
    try:
        mm, w = float(d["max_marks"]), float(d.get("weightage", 1))
    except (ValueError, TypeError):
        raise ApiError("Marks and weightage must be numbers")
    if not (0 < mm <= 1000 and 0 < w <= 1000):
        raise ApiError("Marks and weightage must be positive numbers (max 1000)")
    return dict(criterion=clean(d["criterion"], "criterion", required=True), max_marks=mm, weightage=w,
                description=clean(d.get("description"), "description"))


@api.post("/exams/<int:eid>/rubrics")
@roles("admin")
def create_rubric(u, eid):
    get_or_404(Exam, eid)
    if has_evaluations(eid):
        raise ApiError("The rubric is locked because evaluations already exist", 409)
    r = Rubric(exam_id=eid, **rubric_fields(body()))
    db.session.add(r)
    db.session.commit()
    cache.invalidate("rubrics:")
    return jsonify(r.to_dict()), 201


@api.put("/rubrics/<int:rid>")
@roles("admin")
def update_rubric(u, rid):
    r = get_or_404(Rubric, rid)
    if has_evaluations(r.exam_id):
        raise ApiError("The rubric is locked because evaluations already exist", 409)
    for k, v in rubric_fields(body()).items():
        setattr(r, k, v)
    db.session.commit()
    cache.invalidate("rubrics:")
    return jsonify(r.to_dict())


@api.delete("/rubrics/<int:rid>")
@roles("admin")
def delete_rubric(u, rid):
    r = get_or_404(Rubric, rid)
    if EvaluationScore.query.filter_by(rubric_id=rid).count():
        raise ApiError("Rubric already used in evaluations", 409)
    db.session.delete(r)
    db.session.commit()
    cache.invalidate("rubrics:")
    return jsonify(ok=True)


# ---------- examiners / students ----------
@api.get("/examiners")
@roles("admin")
def list_examiners(u):
    q = User.query.filter_by(role="examiner")
    if request.args.get("q"):
        like = f"%{request.args['q']}%"
        q = q.filter(or_(User.name.ilike(like), User.email.ilike(like), User.department.ilike(like)))
    return jsonify([x.to_dict() for x in q.order_by(User.name)])


@api.post("/examiners")
@roles("admin")
def create_examiner(u):
    d = body()
    need(d, "email", "name", "password")
    email = valid_email(d["email"])
    valid_password(d["password"])
    phone = check_phone(d.get("phone"))
    if User.query.filter_by(email=email).first():
        raise ApiError("Email already registered", 409)
    x = User(email=email, name=clean(d["name"], "name", required=True), role="examiner",
             phone=phone, department=clean(d.get("department"), "department"))
    x.set_password(d["password"])
    db.session.add(x)
    db.session.commit()
    return jsonify(x.to_dict()), 201


@api.put("/examiners/<int:xid>")
@roles("admin")
def update_examiner(u, xid):
    x, d = get_or_404(User, xid), body()
    if x.role != "examiner":
        raise ApiError("Not an examiner", 404)
    x.name = clean(d.get("name", x.name), "name", required=True)
    x.phone = check_phone(d.get("phone", x.phone))
    x.department = clean(d.get("department", x.department), "department")
    if d.get("status") in ("Active", "Inactive"):
        x.status = d["status"]
    if d.get("password"):
        x.set_password(valid_password(d["password"]))
    db.session.commit()
    return jsonify(x.to_dict())


@api.get("/students")
@roles("admin")
def list_students(u):
    q = User.query.filter_by(role="student")
    if request.args.get("q"):
        like = f"%{request.args['q']}%"
        q = q.filter(or_(User.name.ilike(like), User.email.ilike(like), User.id == (
            int(request.args["q"]) if request.args["q"].isdigit() else -1)))
    return jsonify([x.to_dict() for x in q.order_by(User.name)])


@api.put("/students/<int:sid>/status")
@roles("admin")
def student_status(u, sid):
    s = get_or_404(User, sid)
    if s.role != "student" or body().get("status") not in ("Active", "Inactive"):
        raise ApiError("Invalid request")
    s.status = body()["status"]
    db.session.commit()
    return jsonify(s.to_dict())


# ---------- slots ----------
def slot_fields(d, exam):
    need(d, "date", "start_time", "end_time", "capacity")
    out = dict(date=pdate(d["date"], "date"), start_time=ptime(d["start_time"], "start_time"),
               end_time=ptime(d["end_time"], "end_time"), capacity=pint(d["capacity"], "capacity"),
               meeting_link=valid_link(d.get("meeting_link")))
    if out["end_time"] <= out["start_time"]:
        raise ApiError("End time must be after start time")
    return out


def check_overlap(examiner_id, f, ignore_id=None):
    q = Slot.query.filter(Slot.examiner_id == examiner_id, Slot.date == f["date"], Slot.status != "Cancelled",
                          Slot.start_time < f["end_time"], Slot.end_time > f["start_time"])
    if ignore_id:
        q = q.filter(Slot.id != ignore_id)
    if q.first():
        raise ApiError("The examiner already has an overlapping slot at that time", 409)


@api.get("/slots")
@roles("admin", "examiner", "student")
def list_slots(u):
    a = request.args
    if u.role == "student":
        if not a.get("exam_id"):
            raise ApiError("exam_id is required")
        exam_id = to_int(a["exam_id"], "exam_id")
        exam_for(u, exam_id)
        key = f"schedule:{exam_id}"
        hit = cache.get(key)
        if hit is None:
            hit = [s.to_dict() for s in Slot.query.filter(Slot.exam_id == exam_id, Slot.status != "Cancelled")
                   .order_by(Slot.date, Slot.start_time)]
            cache.put(key, hit, 60)
        return jsonify(hit)
    q = Slot.query
    if u.role == "examiner":
        q = q.filter_by(examiner_id=u.id)
    if a.get("exam_id"):
        q = q.filter_by(exam_id=to_int(a["exam_id"], "exam_id"))
    if a.get("examiner_id") and u.role == "admin":
        q = q.filter_by(examiner_id=to_int(a["examiner_id"], "examiner_id"))
    return jsonify([s.to_dict() for s in q.order_by(Slot.date, Slot.start_time)])


@api.post("/slots")
@roles("admin", "examiner")
@serialized
def create_slot(u):
    d = body()
    need(d, "exam_id")
    exam = get_or_404(Exam, to_int(d["exam_id"], "exam_id"))
    examiner_id = u.id
    if u.role == "admin":
        need(d, "examiner_id")
        examiner_id = to_int(d["examiner_id"], "examiner_id")
    else:
        today = date.today()
        if exam.status != "Slot Creation":
            raise ApiError("Slot creation is not open for this examination", 403)
        if (exam.slot_start and today < exam.slot_start) or (exam.slot_end and today > exam.slot_end):
            raise ApiError("Outside the slot creation period "
                           f"({exam.slot_start or '-'} to {exam.slot_end or '-'})", 403)
    if examiner_id not in [x.id for x in exam.examiners]:
        raise ApiError("Examiner is not assigned to this examination", 403)
    f = slot_fields(d, exam)
    if f["date"] < date.today():
        raise ApiError("Slot date cannot be in the past")
    if exam.results_published or exam.status not in ("Slot Creation", "Booking Open"):
        # The admin may add slots outside the examiner's slot-creation dates (e.g. an extra session while booking
        # is open), but never to a Draft, Closed or Completed examination.
        raise ApiError("Slots can only be added while the examination is in Slot Creation or Booking Open", 409)
    check_overlap(examiner_id, f)
    s = Slot(exam_id=exam.id, examiner_id=examiner_id, available_seats=f["capacity"], **f)
    db.session.add(s)
    db.session.commit()
    audit(u, "slot.create", slot=s.id, exam=exam.id, examiner=examiner_id)
    cache.invalidate("schedule:")
    return jsonify(s.to_dict()), 201


@api.put("/slots/<int:sid>")
@roles("admin", "examiner")
@serialized
def update_slot(u, sid):
    s, d = get_or_404(Slot, sid), body()
    active = [b for b in s.bookings if b.status != "Cancelled"]
    if u.role == "examiner":
        if s.examiner_id != u.id:
            raise ApiError("Not your slot", 403)
        require_assigned(u, s.exam)
        if s.exam.status not in ("Draft", "Slot Creation") or active:
            raise ApiError("Slots can only be changed before student booking begins", 403)
    f = slot_fields(d, s.exam)
    if f["date"] != s.date and f["date"] < date.today():
        raise ApiError("Slot date cannot be moved into the past")
    if f["capacity"] < len(active):
        raise ApiError("Capacity cannot be lower than the number of booked students")
    check_overlap(s.examiner_id, f, s.id)
    for k, v in f.items():
        setattr(s, k, v)
    s.available_seats = f["capacity"] - len(active)
    refresh_slot_status(s)
    db.session.commit()
    cache.invalidate("schedule:")
    if u.role == "admin":
        for b in active:
            notify(b.student_id, f"Your {s.exam.name} slot was rescheduled to {s.date} {s.start_time:%H:%M}",
                   "/student/bookings")
        db.session.commit()
    return jsonify(s.to_dict())


@api.delete("/slots/<int:sid>")
@roles("admin", "examiner")
def delete_slot(u, sid):
    s = get_or_404(Slot, sid)
    active = [b for b in s.bookings if b.status != "Cancelled"]
    if u.role == "examiner":
        if s.examiner_id != u.id:
            raise ApiError("Not your slot", 403)
        if s.exam.status not in ("Draft", "Slot Creation") or active:
            raise ApiError("Slots can only be removed before student booking begins", 403)
    if active:
        raise ApiError("Slot has bookings. Reschedule them first.", 409)
    s.status = "Cancelled"
    db.session.commit()
    cache.invalidate("schedule:")
    return jsonify(ok=True)


@api.put("/slots/<int:sid>/examiner")
@roles("admin")
@serialized
def reassign_examiner(u, sid):
    s = get_or_404(Slot, sid)
    x = get_or_404(User, to_int(body().get("examiner_id", 0), "examiner_id"))
    if x.role != "examiner" or x not in s.exam.examiners:
        raise ApiError("Examiner must be assigned to this examination")
    if x.status != "Active":
        raise ApiError(f"{x.name} is inactive and cannot be assigned a slot")
    check_overlap(x.id, dict(date=s.date, start_time=s.start_time, end_time=s.end_time), s.id)
    old_examiner = s.examiner_id
    s.examiner_id = x.id
    for b in s.bookings:
        if b.status != "Cancelled":
            notify(b.student_id, f"Examiner for {s.exam.name} changed to {x.name}", "/student/bookings")
    notify(x.id, f"You were assigned a slot for {s.exam.name} on {s.date}", "/examiner/slots")
    db.session.commit()
    audit(u, "slot.reassign", slot=s.id, old_examiner=old_examiner, new_examiner=x.id)
    cache.invalidate("schedule:")
    return jsonify(s.to_dict())


@api.get("/slots/<int:sid>/bookings")
@roles("admin", "examiner")
def slot_bookings(u, sid):
    s = get_or_404(Slot, sid)
    if u.role == "examiner":
        if s.examiner_id != u.id:
            raise ApiError("Not your slot", 403)
        require_assigned(u, s.exam)
    return jsonify([b.to_dict(with_eval=True) for b in s.bookings if b.status != "Cancelled"])


@api.post("/slots/<int:sid>/complete")
@roles("examiner")
def complete_slot(u, sid):
    s = get_or_404(Slot, sid)
    if s.examiner_id != u.id:
        raise ApiError("Not your slot", 403)
    require_assigned(u, s.exam)
    if any(b.status == "Booked" and not b.evaluation for b in s.bookings):
        raise ApiError("Evaluate all booked students first")
    s.status = "Completed"
    db.session.commit()
    cache.invalidate("schedule:")
    return jsonify(s.to_dict())


# ---------- bookings ----------
@api.get("/bookings")
@roles("admin", "student")
def list_bookings(u):
    q = Booking.query
    if u.role == "student":
        q = q.filter_by(student_id=u.id)
    elif request.args.get("q"):
        like = f"%{request.args['q']}%"
        q = q.join(User, Booking.student_id == User.id).join(Exam, Booking.exam_id == Exam.id).filter(
            or_(User.name.ilike(like), Exam.name.ilike(like), User.email.ilike(like),
                Booking.id == (int(request.args["q"]) if request.args["q"].isdigit() else -1)))
    if request.args.get("status"):
        q = q.filter(Booking.status == request.args["status"])
    # Load everything to_dict() touches in a few queries instead of several per booking (N+1).
    q = q.options(joinedload(Booking.student), joinedload(Booking.exam).joinedload(Exam.course),
                  joinedload(Booking.slot).joinedload(Slot.examiner),
                  selectinload(Booking.evaluation).selectinload(Evaluation.scores).joinedload(EvaluationScore.rubric))
    data = [b.to_dict(with_eval=True) for b in q.order_by(Booking.id.desc())]
    if u.role == "student":  # marks/remarks stay hidden until results are published
        for b in data:
            if not b["results_published"]:
                b.pop("evaluation", None)
    return jsonify(data)


@api.post("/bookings")
@roles("student")
@serialized
def book_slot(u):
    s = get_or_404(Slot, to_int(body().get("slot_id", 0), "slot_id"))
    exam = s.exam
    today = date.today()
    if exam.status != "Booking Open":
        raise ApiError("Booking is not open for this examination", 403)
    if s.date < today:
        raise ApiError("This slot is in the past", 409)
    if (exam.booking_start and today < exam.booking_start) or (exam.booking_end and today > exam.booking_end):
        raise ApiError("Outside the booking period", 403)
    if Booking.query.filter(Booking.student_id == u.id, Booking.exam_id == exam.id,
                            Booking.status != "Cancelled").first():
        raise ApiError("You already have a booking for this examination", 409)
    clash = Booking.query.join(Slot).filter(
        Booking.student_id == u.id,
        Booking.status != "Cancelled",
        Slot.date == s.date,
        Slot.start_time < s.end_time,
        Slot.end_time > s.start_time
    ).first()
    if clash:
        raise ApiError("You already have an active booking that overlaps with this time slot", 409)
    if s.status != "Available":
        raise ApiError("Slot is not available", 409)
    # atomic decrement guards against overbooking under concurrent requests
    n = Slot.query.filter(Slot.id == s.id, Slot.available_seats > 0).update(
        {Slot.available_seats: Slot.available_seats - 1}, synchronize_session=False)
    if not n:
        db.session.rollback()
        raise ApiError("Slot is full", 409)
    db.session.expire(s)
    b = Booking(student_id=u.id, slot_id=s.id, exam_id=exam.id)
    db.session.add(b)
    refresh_slot_status(s)
    notify(u.id, f"Booked {exam.name} on {s.date} at {s.start_time:%H:%M}", "/student/bookings")
    notify(s.examiner_id, f"{u.name} booked your {exam.name} slot on {s.date}", "/examiner/slots")
    try:
        db.session.commit()
    except IntegrityError:  # a parallel request won the race: the unique index rejects the second booking
        db.session.rollback()
        raise ApiError("You already have a booking for this examination", 409)
    cache.invalidate("schedule:")
    mail_async(u.email, f"Booking confirmed: {exam.name}",
               email_html("Your booking is confirmed", f"Hi {u.name}, your slot is booked.",
                          [("Examination", f"{exam.name} ({exam.course.code})"), ("Date", f"{s.date:%A, %d %B %Y}"),
                           ("Time", f"{s.start_time:%H:%M} - {s.end_time:%H:%M}"), ("Examiner", s.examiner.name),
                           ("Meeting link", s.meeting_link)],
                          note="You can cancel from My Bookings until the booking deadline."))
    return jsonify(b.to_dict()), 201


@api.post("/bookings/<int:bid>/cancel")
@roles("student")
def cancel_booking(u, bid):
    b = get_or_404(Booking, bid)
    if b.student_id != u.id:
        raise ApiError("Not your booking", 403)
    if b.status != "Booked":
        raise ApiError("Only active bookings can be cancelled")
    end = b.exam.booking_end
    if end and date.today() > end:
        raise ApiError("Booking deadline has passed", 403)
    if b.slot.date < date.today():
        raise ApiError("The examination date has already passed", 403)
    b.status = "Cancelled"
    Slot.query.filter(Slot.id == b.slot_id, Slot.available_seats < Slot.capacity).update(
        {Slot.available_seats: Slot.available_seats + 1}, synchronize_session=False)
    db.session.expire(b.slot)
    refresh_slot_status(b.slot)
    notify(u.id, f"Cancelled booking for {b.exam.name}", "/student/bookings")
    db.session.commit()
    mail_async(u.email, f"Booking cancelled: {b.exam.name}",
               email_html("Your booking was cancelled", f"Hi {u.name}, your booking has been cancelled.",
                          [("Examination", f"{b.exam.name} ({b.exam.course.code})"), ("Date", f"{b.slot.date:%A, %d %B %Y}"),
                           ("Time", f"{b.slot.start_time:%H:%M} - {b.slot.end_time:%H:%M}")],
                          note="You may book another slot while booking is open."))
    cache.invalidate("schedule:")
    return jsonify(b.to_dict())


@api.put("/bookings/<int:bid>/reschedule")
@roles("admin")
@serialized
def reschedule_booking(u, bid):
    b = get_or_404(Booking, bid)
    new = get_or_404(Slot, to_int(body().get("slot_id", 0), "slot_id"))
    if b.status != "Booked":
        raise ApiError("Only active bookings can be rescheduled")
    if new.exam_id != b.exam_id:
        raise ApiError("New slot must belong to the same examination")
    if new.id == b.slot_id:
        raise ApiError("Booking is already in this slot")
    if new.status != "Available" or new.available_seats <= 0:
        raise ApiError("New slot has no free seat", 409)
    clash = Booking.query.join(Slot).filter(
        Booking.student_id == b.student_id,
        Booking.id != b.id,
        Booking.status != "Cancelled",
        Slot.date == new.date,
        Slot.start_time < new.end_time,
        Slot.end_time > new.start_time
    ).first()
    if clash:
        raise ApiError("Student already has an active booking that overlaps with this time slot", 409)
    old_id, new_id = b.slot_id, new.id
    taken = Slot.query.filter(Slot.id == new_id, Slot.available_seats > 0).update(
        {Slot.available_seats: Slot.available_seats - 1}, synchronize_session=False)
    if not taken:
        db.session.rollback()
        raise ApiError("New slot has no free seat", 409)
    Slot.query.filter(Slot.id == old_id, Slot.available_seats < Slot.capacity).update(
        {Slot.available_seats: Slot.available_seats + 1}, synchronize_session=False)
    b.slot_id = new_id
    db.session.flush()
    db.session.expire_all()
    refresh_slot_status(db.session.get(Slot, old_id))
    refresh_slot_status(db.session.get(Slot, new_id))
    new = db.session.get(Slot, new_id)
    b = db.session.get(Booking, bid)
    notify(b.student_id, f"Your {b.exam.name} booking moved to {new.date} {new.start_time:%H:%M} "
                         f"with {new.examiner.name}", "/student/bookings")
    db.session.commit()
    mail_async(b.student.email, f"Booking rescheduled: {b.exam.name}",
               email_html("Your booking was rescheduled", f"Hi {b.student.name}, the administrator moved your slot.",
                          [("Examination", f"{b.exam.name} ({b.exam.course.code})"), ("New date", f"{new.date:%A, %d %B %Y}"),
                           ("Time", f"{new.start_time:%H:%M} - {new.end_time:%H:%M}"), ("Examiner", new.examiner.name),
                           ("Meeting link", new.meeting_link)]))
    audit(u, "booking.reschedule", booking=bid, old_slot=old_id, new_slot=new_id)
    cache.invalidate("schedule:")
    return jsonify(b.to_dict())


# ---------- evaluation ----------
@api.post("/bookings/<int:bid>/evaluate")
@roles("examiner")
def evaluate(u, bid):
    b, d = get_or_404(Booking, bid), body()
    if b.slot.examiner_id != u.id:
        raise ApiError("You can only evaluate students booked into your slots", 403)
    require_assigned(u, b.exam)
    if b.status == "Cancelled":
        raise ApiError("Booking was cancelled")
    if b.exam.results_published:
        raise ApiError("Results already published", 403)
    if b.exam.status in ("Draft", "Slot Creation"):  # defensive: bookings only exist once booking has opened
        raise ApiError("This examination has not started yet", 409)
    rubrics = {r.id: r for r in b.exam.rubrics}
    if not rubrics:
        raise ApiError("No rubric defined for this examination")
    raw = d.get("scores", [])
    if not isinstance(raw, list) or not all(isinstance(x, dict) and "rubric_id" in x for x in raw):
        raise ApiError("'scores' must be a list of {rubric_id, marks}")
    scores = {to_int(x["rubric_id"], "rubric_id"): x.get("marks") for x in raw}
    if set(scores) != set(rubrics):
        raise ApiError("Provide marks for every rubric criterion")
    weighted, wsum = 0.0, 0.0
    for rid, r in rubrics.items():
        try:
            m = float(scores[rid])
        except (TypeError, ValueError):
            raise ApiError(f"Marks for '{r.criterion}' must be a number")
        if not 0 <= m <= r.max_marks:
            raise ApiError(f"Marks for '{r.criterion}' must be between 0 and {r.max_marks:g}")
        scores[rid] = m
        weighted += m / r.max_marks * r.weightage
        wsum += r.weightage
    total = round(weighted / wsum * b.exam.max_marks, 2)
    remarks = clean(d.get("remarks"), "remarks")
    ev = b.evaluation
    if ev:  # re-evaluation: keep what is about to be overwritten
        db.session.add(EvaluationHistory(
            evaluation_id=ev.id, examiner_id=ev.examiner_id, total_marks=ev.total_marks, remarks=ev.remarks,
            evaluated_at=ev.evaluated_at,
            scores_json=json.dumps([dict(rubric_id=s.rubric_id, criterion=s.rubric.criterion, marks=s.marks)
                                    for s in ev.scores])))
    else:
        ev = Evaluation(booking_id=b.id, student_id=b.student_id, examiner_id=u.id, total_marks=total)
    ev.total_marks, ev.remarks, ev.evaluated_at, ev.status = total, remarks, utc_now(), "Evaluated"
    ev.examiner_id = u.id
    ev.scores = [EvaluationScore(rubric_id=rid, marks=m) for rid, m in scores.items()]
    b.status = "Completed"
    db.session.add(ev)
    db.session.commit()
    audit(u, "evaluation.save", booking=b.id, student=b.student_id, total=total)
    return jsonify(b.to_dict(with_eval=True))


@api.get("/bookings/<int:bid>/evaluation-history")
@roles("admin")
def evaluation_history(u, bid):
    """Earlier versions of an evaluation that was changed before results were published (newest first)."""
    b = get_or_404(Booking, bid)
    rows = []
    if b.evaluation:
        rows = EvaluationHistory.query.filter_by(evaluation_id=b.evaluation.id).order_by(EvaluationHistory.id.desc())
    return jsonify([h.to_dict() for h in rows])


@api.get("/results")
@roles("student")
def results(u):
    bs = (Booking.query.join(Exam, Booking.exam_id == Exam.id)
          .filter(Booking.student_id == u.id, Exam.results_published.is_(True), Booking.status == "Completed").all())
    return jsonify([b.to_dict(with_eval=True) for b in bs])


# ---------- dashboards ----------
@api.get("/dashboard")
@roles("admin", "examiner", "student")
def dashboard(u):
    today = date.today()
    if u.role == "admin":
        return jsonify(
            courses=Course.query.count(), exams=Exam.query.count(),
            students=User.query.filter_by(role="student").count(),
            examiners=User.query.filter_by(role="examiner").count(),
            slots=Slot.query.filter(Slot.status != "Cancelled").count(),
            bookings=Booking.query.filter(Booking.status != "Cancelled").count())
    if u.role == "examiner":
        mine = Slot.query.filter_by(examiner_id=u.id).filter(Slot.status != "Cancelled").all()
        ids = [s.id for s in mine]
        pend = (Booking.query.outerjoin(Evaluation, Evaluation.booking_id == Booking.id)
                .filter(Booking.slot_id.in_(ids), Booking.status == "Booked", Evaluation.id.is_(None)).count()) \
            if ids else 0
        exams = Exam.query.filter(Exam.examiners.any(User.id == u.id)).all()
        return jsonify(assigned_exams=[e.to_dict() for e in exams], slots=len(mine),
                       slot_list=[s.to_dict() for s in mine],
                       booked_students=sum(1 for s in mine for b in s.bookings if b.status != "Cancelled"),
                       pending_evaluations=pend)
    bookings = Booking.query.filter_by(student_id=u.id).all()
    booked_exam_ids = {b.exam_id for b in bookings if b.status != "Cancelled"}
    # same eligibility as book_slot(): status, booking window, and (for slots) not in the past and not already booked
    in_window = (Exam.status == "Booking Open", or_(Exam.booking_start.is_(None), Exam.booking_start <= today),
                 or_(Exam.booking_end.is_(None), Exam.booking_end >= today))
    open_exams = Exam.query.filter(*in_window).all()
    avail = (Slot.query.join(Exam).filter(*in_window, Slot.status == "Available", Slot.date >= today)
             .filter(Slot.exam_id.notin_(booked_exam_ids or [0])).count())
    upcoming = [b.to_dict() for b in bookings if b.status == "Booked" and b.slot.date >= today]
    res = [b.to_dict(with_eval=True) for b in bookings if b.evaluation and b.exam.results_published]
    return jsonify(available_exams=[e.to_dict() for e in open_exams if e.id not in booked_exam_ids],
                   available_slots=avail, upcoming=upcoming, booked=len(booked_exam_ids), results=res)


@api.get("/admin/charts")
@roles("admin")
def charts(u):
    popular = (db.session.query(Exam.name, func.count(Booking.id)).join(Booking, Booking.exam_id == Exam.id)
               .filter(Booking.status != "Cancelled").group_by(Exam.id).order_by(func.count(Booking.id).desc())
               .limit(8).all())
    workload = (db.session.query(User.name, func.count(Booking.id)).join(Slot, Slot.examiner_id == User.id)
                .join(Booking, Booking.slot_id == Slot.id).filter(Booking.status != "Cancelled")
                .group_by(User.id).all())
    since = utc_now() - timedelta(days=30)
    trend = (db.session.query(func.date(Booking.booked_at), func.count(Booking.id))
             .filter(Booking.booked_at >= since).group_by(func.date(Booking.booked_at)).all())
    return jsonify(popular=[dict(label=n, value=c) for n, c in popular],
                   workload=[dict(label=n, value=c) for n, c in workload],
                   trend=[dict(label=str(d), value=c) for d, c in sorted(trend)])


@api.get("/admin/search")
@roles("admin")
def search(u):
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify(students=[], examiners=[], exams=[], bookings=[])
    like = f"%{q}%"
    users = User.query.filter(or_(User.name.ilike(like), User.email.ilike(like))).limit(50).all()
    exams = Exam.query.filter(Exam.name.ilike(like)).limit(30).all()
    bookings = (Booking.query.join(User, Booking.student_id == User.id).join(Exam, Booking.exam_id == Exam.id)
                .filter(or_(User.name.ilike(like), Exam.name.ilike(like))).limit(30).all())
    return jsonify(students=[x.to_dict() for x in users if x.role == "student"],
                   examiners=[x.to_dict() for x in users if x.role == "examiner"],
                   exams=[e.to_dict() for e in exams], bookings=[b.to_dict() for b in bookings])


@api.get("/admin/report")
@roles("admin")
def report(u):
    t = date.today()
    return jsonify(monthly_stats(int(request.args.get("year", t.year)), int(request.args.get("month", t.month))))


@api.post("/admin/report/send")
@roles("admin")
def send_report(u):
    t = date.today()
    s = monthly_stats(t.year, t.month)
    mail_async(current_app.config["ADMIN_NOTIFY_EMAIL"], f"Examination report - {s['month']}", monthly_report_html(s), monthly_report_pdf(s))
    return jsonify(ok=True)


@api.get("/admin/report/pdf")
@roles("admin")
def report_pdf(u):
    t = date.today()
    s = monthly_stats(int(request.args.get("year", t.year)), int(request.args.get("month", t.month)))
    return Response(monthly_report_pdf(s), mimetype="application/pdf",
                    headers={"Content-Disposition": "attachment; filename=examination_report.pdf"})


# ---------- notifications & CSV export ----------
@api.get("/notifications")
@roles("admin", "examiner", "student")
def notifications(u):
    n = Notification.query.filter_by(user_id=u.id).order_by(Notification.id.desc()).limit(30).all()
    return jsonify([x.to_dict() for x in n])


@api.post("/notifications/read")
@roles("admin", "examiner", "student")
def read_notifications(u):
    Notification.query.filter_by(user_id=u.id, read=False).update({Notification.read: True})
    db.session.commit()
    return jsonify(ok=True)


@api.post("/export/history")
@roles("student")
def export_history(u):
    from tasks import export_history as task
    job = ExportJob(user_id=u.id)
    db.session.add(job)
    db.session.commit()
    run_task(task, job.id)
    return jsonify(job.to_dict()), 202


@api.get("/export/<int:jid>")
@roles("student")
def export_status(u, jid):
    j = get_or_404(ExportJob, jid)
    if j.user_id != u.id:
        raise ApiError("Not your export", 403)
    return jsonify(j.to_dict())


@api.get("/export/<int:jid>/download")
@roles("student")
def export_download(u, jid):
    j = get_or_404(ExportJob, jid)
    if j.user_id != u.id or j.status != "Done":
        raise ApiError("Export not available", 404)
    return send_from_directory(current_app.config["EXPORT_DIR"], j.filename, as_attachment=True)
