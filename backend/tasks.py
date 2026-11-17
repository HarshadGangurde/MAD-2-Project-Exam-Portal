import base64
import csv
import html
import os
import sys
import time
from datetime import date, timedelta

from celery_app import celery
# Ensure backend directory is in path for Celery workers
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from models import db, Booking, ExportJob, Notification, Slot, User
from services import email_html, monthly_report_html, monthly_report_pdf, monthly_stats, send_chat, send_mail

_app = None


def flask_app():
    """Build the Flask app once per worker process so tasks can use the DB and config."""
    global _app
    if _app is None:
        # The Celery command line puts the backend folder on sys.path only while it imports celery_app and this module,
        # then removes it again. Without this line `from app import ...` fails inside a running worker
        # ("No module named 'app'") even though the tests, which call the tasks directly, pass.
        backend = os.path.dirname(os.path.abspath(__file__))
        if backend not in sys.path:
            sys.path.insert(0, backend)
        from app import create_app
        _app = create_app()
    return _app


@celery.task(name="tasks.health")
def health():
    """Used by check_services.py: proves a worker can build the Flask app and read the database (changes nothing)."""
    with flask_app().app_context():
        return User.query.count()


@celery.task(name="tasks.send_email")
def send_email(to, subject, html, pdf_b64=None):
    with flask_app().app_context():
        return send_mail(to, subject, html, base64.b64decode(pdf_b64) if pdf_b64 else None)


@celery.task(name="tasks.daily_reminders")
def daily_reminders():
    """Day-before reminder: every student booked for tomorrow gets one (email + optional Google Chat).
    Only tomorrow is selected, so nobody is reminded twice for the same examination."""
    with flask_app().app_context():
        tomorrow = date.today() + timedelta(days=1)
        bookings = Booking.query.join(Slot).filter(Booking.status == "Booked", Slot.date == tomorrow).all()
        sent = 0
        for b in bookings:
            s, e = b.slot, b.exam
            text = (f"Reminder: {e.name} ({e.course.code}) on {s.date} at {s.start_time:%H:%M}-{s.end_time:%H:%M}. "
                    f"Examiner: {s.examiner.name}. Instructions: {e.instructions or 'None'}")
            # Idempotent: running the job again (manual re-run, beat restart) must not remind the same student twice.
            if Notification.query.filter_by(user_id=b.student_id, message=text[:250]).first():
                continue
            mail = email_html(f"Reminder: {e.name} is tomorrow", f"Hi {b.student.name}, this is a reminder about your examination.",
                              [("Examination", f"{e.name} ({e.course.code})"), ("Type", e.type), ("Date", f"{s.date:%A, %d %B %Y}"),
                               ("Time", f"{s.start_time:%H:%M} - {s.end_time:%H:%M}"), ("Examiner", s.examiner.name),
                               ("Meeting link", s.meeting_link)],
                              note=f"Instructions: {e.instructions}" if e.instructions else None)
            send_mail(b.student.email, f"Exam reminder: {e.name} on {s.date:%d %b}", mail)
            send_chat(f"{b.student.name}: {text}")
            db.session.add(Notification(user_id=b.student_id, message=text[:250], link="/student/bookings"))
            db.session.commit()
            sent += 1
        return sent


@celery.task(name="tasks.monthly_report")
def monthly_report():
    """Runs on the 1st: report on the previous month, mailed to the admin as HTML."""
    with flask_app().app_context():
        last_month_end = date.today().replace(day=1) - timedelta(days=1)
        stats = monthly_stats(last_month_end.year, last_month_end.month)
        admin = User.query.filter_by(role="admin").first()
        marker = f"Monthly report for {stats['month']} was emailed to you."
        if Notification.query.filter_by(user_id=admin.id, message=marker).first():
            return stats  # already sent for that month (idempotent re-run)
        sent = send_mail(flask_app().config["ADMIN_NOTIFY_EMAIL"], f"Monthly examination report - {stats['month']}", monthly_report_html(stats),
                         monthly_report_pdf(stats))
        if sent:  # only a mail that really went out counts as "done"; a failed one is retried on the next run
            db.session.add(Notification(user_id=admin.id, message=marker, link="/admin"))
            db.session.commit()
        return stats


@celery.task(name="tasks.export_history")
def export_history(job_id):
    """Student-triggered: write the student's examination history to CSV, then notify them."""
    with flask_app().app_context():
        job = db.session.get(ExportJob, job_id)
        try:
            export_dir = flask_app().config["EXPORT_DIR"]
            os.makedirs(export_dir, exist_ok=True)
            # Clean old export files (> 1 hour) so they do not pile up. Only our own history_*.csv files are touched.
            now = time.time()
            for fn in os.listdir(export_dir):
                fp = os.path.join(export_dir, fn)
                try:
                    if fn.startswith("history_") and fn.endswith(".csv") and os.path.isfile(fp) \
                            and (now - os.path.getmtime(fp)) > 3600:
                        os.remove(fp)
                except OSError:
                    pass
            fname = f"history_{job.user_id}_{job.id}.csv"
            path = os.path.join(flask_app().config["EXPORT_DIR"], fname)
            bookings = Booking.query.filter_by(student_id=job.user_id).order_by(Booking.id).all()
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["Student ID", "Examination Name", "Course", "Examination Date", "Examiner",
                            "Marks Obtained", "Booking Status"])
                for b in bookings:
                    marks = b.evaluation.total_marks if b.evaluation and b.exam.results_published else ""
                    w.writerow([b.student_id, b.exam.name, b.exam.course.code, b.slot.date, b.slot.examiner.name,
                                marks, b.status])
            job.status, job.filename = "Done", fname
            db.session.add(Notification(user_id=job.user_id, message="Your examination history CSV is ready to download.",
                                        link="/student"))
        except Exception:
            job.status = "Failed"
            db.session.add(Notification(user_id=job.user_id, message="Your CSV export failed. Please try again."))
        db.session.commit()
        return job.status
