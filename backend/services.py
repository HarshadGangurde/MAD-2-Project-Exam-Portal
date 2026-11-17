"""Helpers shared by the API and the Celery tasks: mail, chat webhook, monthly statistics."""
import html
import json
import logging
import re
import smtplib
import time
import urllib.request
from calendar import monthrange
from datetime import date, datetime
from email.header import Header
from email.utils import formataddr, formatdate, make_msgid
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from flask import current_app
from sqlalchemy import func

from models import db, Booking, Evaluation, Slot, User

log = logging.getLogger("emp")
_mail_down_until = 0.0   # after a failed connection, skip the SMTP server for a while (see send_mail)


def _plain(html_text):
    """Readable plain-text version of an HTML mail (for clients that do not show HTML)."""
    t = re.sub(r"(?is)<(style|head).*?</\\1>", "", html_text)
    t = re.sub(r"(?i)<br\\s*/?>|</(p|div|tr|h[1-6])>", "\\n", t)
    t = re.sub(r"(?i)</t[dh]>", "  ", t)
    t = re.sub(r"<[^>]+>", "", t)
    return re.sub(r"\\n\\s*\\n+", "\\n\\n", html.unescape(t)).strip()


def email_html(title, intro, rows=None, note=None, footer="This is an automated message from the Examination Management Portal."):
    """One consistent, mail-client-safe layout (tables + inline styles). rows = [(label, value), ...] (escaped here)."""
    e = html.escape
    table = ""
    if rows:
        body = "".join(
            f'<tr><td style="padding:8px 12px;color:#64748b;border-bottom:1px solid #e2e8f0;width:38%">{e(str(k))}</td>'
            f'<td style="padding:8px 12px;font-weight:600;border-bottom:1px solid #e2e8f0">{e(str(v))}</td></tr>'
            for k, v in rows if v not in (None, ""))
        table = f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin:16px 0;font-size:14px">{body}</table>'
    note_html = f'<p style="font-size:14px;color:#334155;margin:0 0 12px">{e(note)}</p>' if note else ""
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>'
        '<body style="margin:0;background:#f1f5f9;font-family:Arial,Helvetica,sans-serif;color:#0f172a">'
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td align="center" style="padding:24px 12px">'
        '<table role="presentation" width="600" cellspacing="0" cellpadding="0" style="max-width:600px;width:100%;background:#ffffff;border-radius:10px;overflow:hidden">'
        '<tr><td style="background:#4f46e5;color:#ffffff;padding:18px 24px;font-size:18px;font-weight:bold">Examination Management Portal</td></tr>'
        f'<tr><td style="padding:24px"><h2 style="margin:0 0 12px;font-size:20px">{e(title)}</h2>'
        f'<p style="font-size:15px;line-height:1.5;margin:0 0 8px">{e(intro)}</p>{table}{note_html}</td></tr>'
        f'<tr><td style="padding:14px 24px;background:#f8fafc;color:#94a3b8;font-size:12px">{e(footer)}</td></tr>'
        '</table></td></tr></table></body></html>')


def send_mail(to, subject, html_body, pdf=None, text=None):
    """Send one e-mail (HTML + plain-text alternative, optional PDF). Never raises. If the SMTP server cannot be reached,
    further mails are skipped for 60 s: a refused connection costs seconds, which would otherwise be paid per recipient.
    Works with a local sink (MailHog/Mailpit) and with a real server (MAIL_USERNAME/MAIL_PASSWORD, MAIL_USE_TLS/SSL)."""
    global _mail_down_until
    cfg = current_app.config
    if time.time() < _mail_down_until:
        log.warning("mail to %s skipped (SMTP server unreachable a moment ago). Subject: %s", to, subject)
        return False
    msg = MIMEMultipart("mixed")
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(text or _plain(html_body), "plain", "utf-8"))
    alt.attach(MIMEText(html_body, "html", "utf-8"))
    msg.attach(alt)
    if pdf:
        part = MIMEApplication(pdf, _subtype="pdf")
        part.add_header("Content-Disposition", "attachment", filename="examination_report.pdf")
        msg.attach(part)
    sender = cfg["MAIL_FROM"]
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr((cfg.get("MAIL_FROM_NAME", "Examination Portal"), sender))
    msg["To"] = to
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=sender.split("@")[-1] or "localhost")
    try:
        if cfg.get("MAIL_USE_SSL"):
            smtp = smtplib.SMTP_SSL(cfg["MAIL_HOST"], cfg["MAIL_PORT"], timeout=10)
        else:
            smtp = smtplib.SMTP(cfg["MAIL_HOST"], cfg["MAIL_PORT"], timeout=10)
        with smtp as s:
            if cfg.get("MAIL_USE_TLS") and not cfg.get("MAIL_USE_SSL"):
                s.starttls()
            if cfg.get("MAIL_USERNAME"):
                # Offer only PLAIN: after a refused login Gmail hangs up, which smtplib reports as a vague
                # "connection closed" when it then tries the next method. One method = the real error (535 bad password).
                s.ehlo()
                if "PLAIN" in s.esmtp_features.get("auth", ""):
                    s.esmtp_features["auth"] = "PLAIN"
                s.login(cfg["MAIL_USERNAME"], cfg.get("MAIL_PASSWORD", ""))
            s.send_message(msg, from_addr=sender, to_addrs=[to])
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
    exam_rows = [("Examinations conducted", stats["examinations_conducted"]), ("Students evaluated", stats["students_evaluated"]),
                 ("Completed bookings", stats["completed_bookings"]), ("Pending evaluations", stats["pending_evaluations"])]
    top = [(e["name"], f"{e['evaluations']} evaluation(s)") for e in stats["active_examiners"]] or [("Most active examiners", "No evaluations")]
    page = email_html(f"Examination report - {stats['month']}", "Here are the examination statistics for the month.",
                      exam_rows + [("Most active examiners", "")] + top,
                      note="The full report is attached as a PDF.")
    return page
