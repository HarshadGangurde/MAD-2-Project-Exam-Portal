"""Helpers shared by the API and the Celery tasks: mail, chat webhook, monthly statistics."""
import html
import json
import logging
import smtplib
import time
import urllib.request
from calendar import monthrange
from datetime import date, datetime
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from flask import current_app
from sqlalchemy import func

from models import db, Booking, Evaluation, Slot, User

log = logging.getLogger("emp")
_mail_down_until = 0.0   # after a failed connection, skip the SMTP server for a while (see send_mail)


def send_mail(to, subject, html, pdf=None):
    """Send one e-mail. Never raises. If the SMTP server cannot be reached, further mails are skipped for 60 s:
    a refused connection costs seconds (about 4 s on Windows), which would otherwise be paid once per recipient."""
    global _mail_down_until
    cfg = current_app.config
    if time.time() < _mail_down_until:
        log.warning("mail to %s skipped (SMTP server unreachable a moment ago). Subject: %s", to, subject)
        return False
    msg = MIMEMultipart()
    msg.attach(MIMEText(html, "html"))
    if pdf:
        part = MIMEApplication(pdf, _subtype="pdf")
        part.add_header("Content-Disposition", "attachment", filename="examination_report.pdf")
        msg.attach(part)
    msg["Subject"], msg["From"], msg["To"] = subject, cfg["MAIL_FROM"], to
    try:
        with smtplib.SMTP(cfg["MAIL_HOST"], cfg["MAIL_PORT"], timeout=5) as s:
            s.send_message(msg)
        return True
    except Exception as e:  # mail server not running is fine for local demos
        if isinstance(e, OSError):  # cannot connect / timed out (not e.g. a rejected recipient)
            _mail_down_until = time.time() + 60
        log.warning("mail to %s not sent (%s). Subject: %s", to, e, subject)
        return False


def send_chat(text):
    url = current_app.config["GCHAT_WEBHOOK"]
    if not url:
        return False
    try:
        req = urllib.request.Request(url, data=json.dumps({"text": text}).encode(),
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
        return True
    except Exception as e:
        log.warning("chat webhook failed: %s", e)
        return False


def monthly_stats(year, month):
    first = date(year, month, 1)
    last = date(year, month, monthrange(year, month)[1])
    in_month = (Slot.date >= first) & (Slot.date <= last)

    completed = (Booking.query.join(Slot).filter(Booking.status == "Completed", in_month).count())
    conducted = (db.session.query(func.count(func.distinct(Booking.exam_id))).join(Slot, Booking.slot_id == Slot.id)
                 .filter(Booking.status == "Completed", in_month).scalar())
    evaluated = (db.session.query(func.count(func.distinct(Evaluation.student_id)))
                 .filter(Evaluation.evaluated_at >= datetime.combine(first, datetime.min.time()),
                         Evaluation.evaluated_at <= datetime.combine(last, datetime.max.time())).scalar())
    pending = (Booking.query.join(Slot).outerjoin(Evaluation, Evaluation.booking_id == Booking.id)
               .filter(Booking.status == "Booked", Evaluation.id.is_(None), Slot.date <= last).count())
    top = (db.session.query(User.name, func.count(Evaluation.id)).join(Evaluation, Evaluation.examiner_id == User.id)
           .filter(Evaluation.evaluated_at >= datetime.combine(first, datetime.min.time()),
                   Evaluation.evaluated_at <= datetime.combine(last, datetime.max.time()))
           .group_by(User.id).order_by(func.count(Evaluation.id).desc()).limit(5).all())
    return dict(month=first.strftime("%B %Y"), examinations_conducted=conducted, students_evaluated=evaluated,
                completed_bookings=completed, pending_evaluations=pending,
                active_examiners=[dict(name=n, evaluations=c) for n, c in top])


def monthly_report_pdf(stats):
    """Single-page text PDF built by hand (no PDF library is permitted for this project)."""
    lines = [("F2", 18, f"Examination Report - {stats['month']}"), ("F1", 12, ""),
             ("F1", 12, f"Examinations conducted: {stats['examinations_conducted']}"),
             ("F1", 12, f"Students evaluated: {stats['students_evaluated']}"),
             ("F1", 12, f"Completed bookings: {stats['completed_bookings']}"),
             ("F1", 12, f"Pending evaluations: {stats['pending_evaluations']}"), ("F1", 12, ""),
             ("F2", 14, "Most active examiners")]
    lines += [("F1", 12, f"  {e['name']}: {e['evaluations']} evaluations") for e in stats["active_examiners"]] \
        or [("F1", 12, "  No evaluations")]
    esc = lambda t: t.encode("latin-1", "replace").decode("latin-1").replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    y, ops = 790, []
    for font, size, text in lines:
        ops.append(f"BT /{font} {size} Tf 50 {y} Td ({esc(text)}) Tj ET")
        y -= size + 10
    stream = "\n".join(ops).encode("latin-1")
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R /F2 6 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>"]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % off for off in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (len(objs) + 1, xref)
    return bytes(out)


def monthly_report_html(stats):
    rows = "".join(f"<tr><td>{html.escape(e['name'])}</td><td>{e['evaluations']}</td></tr>"
                   for e in stats["active_examiners"]) \
        or "<tr><td colspan=2>No evaluations</td></tr>"
    return f"""<html><body style="font-family:Arial,sans-serif">
<h2>Examination Report &ndash; {stats['month']}</h2>
<table border="1" cellpadding="6" cellspacing="0">
<tr><td>Examinations conducted</td><td>{stats['examinations_conducted']}</td></tr>
<tr><td>Students evaluated</td><td>{stats['students_evaluated']}</td></tr>
<tr><td>Completed bookings</td><td>{stats['completed_bookings']}</td></tr>
<tr><td>Pending evaluations</td><td>{stats['pending_evaluations']}</td></tr></table>
<h3>Most active examiners</h3>
<table border="1" cellpadding="6" cellspacing="0"><tr><th>Examiner</th><th>Evaluations</th></tr>{rows}</table>
</body></html>"""
