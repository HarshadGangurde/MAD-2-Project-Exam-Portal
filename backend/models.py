import sqlite3
from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event
from sqlalchemy.engine import Engine
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


def utc_now():
    """Timezone-aware UTC timestamp without tzinfo (for SQLite DateTime storage). Avoids datetime.utcnow() deprecation."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


@event.listens_for(Engine, "connect")
def _enforce_foreign_keys(dbapi_conn, _record):
    """SQLite ignores FOREIGN KEY constraints unless this pragma is switched on for every connection."""
    if isinstance(dbapi_conn, sqlite3.Connection):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


def iso(v):
    return v.isoformat() if v else None


class User(db.Model):
    """Unified user model for admin / examiner / student."""
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(10), nullable=False)  # admin | examiner | student
    phone = db.Column(db.String(20))
    department = db.Column(db.String(80))
    status = db.Column(db.String(10), default="Active", nullable=False)  # Active | Inactive
    created_at = db.Column(db.DateTime, default=utc_now)

    __table_args__ = (db.CheckConstraint("role in ('admin','examiner','student')", name="ck_user_role"),)

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)

    def to_dict(self):
        return dict(id=self.id, email=self.email, name=self.name, role=self.role, phone=self.phone,
                    department=self.department, status=self.status)


class Course(db.Model):
    __tablename__ = "courses"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text)
    status = db.Column(db.String(10), default="Active", nullable=False)
    exams = db.relationship("Exam", backref="course", lazy=True)

    def to_dict(self):
        return dict(id=self.id, code=self.code, name=self.name, description=self.description, status=self.status)


# many-to-many: examiners assigned to an examination
exam_examiners = db.Table(
    "exam_examiners",
    db.Column("exam_id", db.Integer, db.ForeignKey("exams.id"), primary_key=True),
    db.Column("examiner_id", db.Integer, db.ForeignKey("users.id"), primary_key=True),
)

EXAM_TYPES = ["Viva", "Practical", "Project Demo", "Assessment"]
EXAM_STATUSES = ["Draft", "Slot Creation", "Booking Open", "Closed", "Completed"]
# The only moves the status endpoint accepts (the UI offers just these). Completed can step back to Closed only
# until results are published; after that the status is locked.
EXAM_TRANSITIONS = {
    "Draft": ["Slot Creation"],
    "Slot Creation": ["Draft", "Booking Open", "Closed"],
    "Booking Open": ["Slot Creation", "Closed"],
    "Closed": ["Booking Open", "Completed"],
    "Completed": ["Closed"],
}


class Exam(db.Model):
    __tablename__ = "exams"
    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey("courses.id"), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    type = db.Column(db.String(20), nullable=False)
    duration = db.Column(db.Integer, nullable=False)  # minutes
    max_marks = db.Column(db.Integer, nullable=False)
    instructions = db.Column(db.Text)
    slot_start = db.Column(db.Date)
    slot_end = db.Column(db.Date)
    booking_start = db.Column(db.Date)
    booking_end = db.Column(db.Date)
    status = db.Column(db.String(15), default="Draft", nullable=False)
    results_published = db.Column(db.Boolean, default=False, nullable=False)
    examiners = db.relationship("User", secondary=exam_examiners, lazy="subquery")
    rubrics = db.relationship("Rubric", backref="exam", cascade="all, delete-orphan", lazy=True)
    slots = db.relationship("Slot", backref="exam", lazy=True)

    def to_dict(self):
        return dict(id=self.id, course_id=self.course_id, course_code=self.course.code, course_name=self.course.name,
                    name=self.name, type=self.type, duration=self.duration, max_marks=self.max_marks,
                    instructions=self.instructions, slot_start=iso(self.slot_start), slot_end=iso(self.slot_end),
                    booking_start=iso(self.booking_start), booking_end=iso(self.booking_end), status=self.status,
                    results_published=self.results_published,
                    next_statuses=[] if self.results_published else EXAM_TRANSITIONS.get(self.status, []),
                    examiners=[dict(id=e.id, name=e.name) for e in self.examiners])


class Rubric(db.Model):
    __tablename__ = "rubrics"
    id = db.Column(db.Integer, primary_key=True)
    exam_id = db.Column(db.Integer, db.ForeignKey("exams.id"), nullable=False)
    criterion = db.Column(db.String(120), nullable=False)
    max_marks = db.Column(db.Float, nullable=False)
    weightage = db.Column(db.Float, default=1, nullable=False)
    description = db.Column(db.Text)

    def to_dict(self):
        return dict(id=self.id, exam_id=self.exam_id, criterion=self.criterion, max_marks=self.max_marks,
                    weightage=self.weightage, description=self.description)


class Slot(db.Model):
    __tablename__ = "slots"
    id = db.Column(db.Integer, primary_key=True)
    exam_id = db.Column(db.Integer, db.ForeignKey("exams.id"), nullable=False)
    examiner_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    date = db.Column(db.Date, nullable=False)
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)
    capacity = db.Column(db.Integer, default=1, nullable=False)
    available_seats = db.Column(db.Integer, default=1, nullable=False)
    status = db.Column(db.String(10), default="Available", nullable=False)  # Available|Full|Cancelled|Completed
    meeting_link = db.Column(db.String(255))
    examiner = db.relationship("User", foreign_keys=[examiner_id])
    bookings = db.relationship("Booking", backref="slot", lazy=True)

    __table_args__ = (db.CheckConstraint("available_seats >= 0", name="ck_slot_seats"),)

    def to_dict(self):
        return dict(id=self.id, exam_id=self.exam_id, exam_name=self.exam.name, examiner_id=self.examiner_id,
                    examiner_name=self.examiner.name, date=iso(self.date),
                    start_time=self.start_time.strftime("%H:%M"), end_time=self.end_time.strftime("%H:%M"),
                    capacity=self.capacity, available_seats=self.available_seats, status=self.status,
                    meeting_link=self.meeting_link,
                    booked=sum(1 for b in self.bookings if b.status != "Cancelled"))


class Booking(db.Model):
    __tablename__ = "bookings"
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    slot_id = db.Column(db.Integer, db.ForeignKey("slots.id"), nullable=False)
    exam_id = db.Column(db.Integer, db.ForeignKey("exams.id"), nullable=False)  # denormalised for the one-per-exam rule
    booked_at = db.Column(db.DateTime, default=utc_now)
    status = db.Column(db.String(10), default="Booked", nullable=False)  # Booked|Cancelled|Completed
    student = db.relationship("User", foreign_keys=[student_id])
    exam = db.relationship("Exam")
    evaluation = db.relationship("Evaluation", backref="booking", uselist=False)

    def to_dict(self, with_eval=False):
        s = self.slot
        d = dict(id=self.id, student_id=self.student_id, student_name=self.student.name, slot_id=self.slot_id,
                 exam_id=self.exam_id, exam_name=self.exam.name, course=self.exam.course.code,
                 date=iso(s.date), start_time=s.start_time.strftime("%H:%M"), end_time=s.end_time.strftime("%H:%M"),
                 examiner_id=s.examiner_id, examiner_name=s.examiner.name, meeting_link=s.meeting_link,
                 status=self.status, booked_at=iso(self.booked_at), max_marks=self.exam.max_marks,
                 evaluated=self.evaluation is not None, results_published=self.exam.results_published)
        if with_eval and self.evaluation:
            d["evaluation"] = self.evaluation.to_dict()
        return d


class Evaluation(db.Model):
    __tablename__ = "evaluations"
    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey("bookings.id"), unique=True, nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    examiner_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    total_marks = db.Column(db.Float, nullable=False)
    remarks = db.Column(db.Text)
    evaluated_at = db.Column(db.DateTime, default=utc_now)
    status = db.Column(db.String(12), default="Evaluated", nullable=False)
    scores = db.relationship("EvaluationScore", backref="evaluation", cascade="all, delete-orphan")

    def to_dict(self):
        return dict(id=self.id, total_marks=self.total_marks, remarks=self.remarks,
                    evaluated_at=iso(self.evaluated_at), status=self.status,
                    scores=[dict(rubric_id=s.rubric_id, criterion=s.rubric.criterion, marks=s.marks,
                                 max_marks=s.rubric.max_marks) for s in self.scores])


class EvaluationScore(db.Model):
    __tablename__ = "evaluation_scores"
    id = db.Column(db.Integer, primary_key=True)
    evaluation_id = db.Column(db.Integer, db.ForeignKey("evaluations.id"), nullable=False)
    rubric_id = db.Column(db.Integer, db.ForeignKey("rubrics.id"), nullable=False)
    marks = db.Column(db.Float, nullable=False)
    rubric = db.relationship("Rubric")


class EvaluationHistory(db.Model):
    """Snapshot of an evaluation taken just before it is overwritten (re-evaluation before publication)."""
    __tablename__ = "evaluation_history"
    id = db.Column(db.Integer, primary_key=True)
    evaluation_id = db.Column(db.Integer, db.ForeignKey("evaluations.id"), nullable=False, index=True)
    examiner_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    total_marks = db.Column(db.Float, nullable=False)
    remarks = db.Column(db.Text)
    scores_json = db.Column(db.Text, nullable=False)  # [{"rubric_id": 1, "criterion": "...", "marks": 8.0}, ...]
    evaluated_at = db.Column(db.DateTime)
    superseded_at = db.Column(db.DateTime, default=utc_now)

    def to_dict(self):
        import json
        return dict(id=self.id, evaluation_id=self.evaluation_id, examiner_id=self.examiner_id,
                    total_marks=self.total_marks, remarks=self.remarks, scores=json.loads(self.scores_json),
                    evaluated_at=iso(self.evaluated_at), superseded_at=iso(self.superseded_at))


class Notification(db.Model):
    __tablename__ = "notifications"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    message = db.Column(db.String(255), nullable=False)
    link = db.Column(db.String(255))
    read = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=utc_now)

    def to_dict(self):
        return dict(id=self.id, message=self.message, link=self.link, read=self.read, created_at=iso(self.created_at))


class ExportJob(db.Model):
    __tablename__ = "export_jobs"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    status = db.Column(db.String(10), default="Pending", nullable=False)  # Pending|Done|Failed
    filename = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=utc_now)

    def to_dict(self):
        return dict(id=self.id, status=self.status, created_at=iso(self.created_at))
