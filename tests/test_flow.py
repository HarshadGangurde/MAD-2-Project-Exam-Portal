"""End-to-end API tests. Run from the project folder:

    venv\\Scripts\\python -m unittest discover -s tests -v

Uses a throwaway SQLite file and needs neither Redis nor a Celery worker (tasks run inline, mail is only logged).
"""
import os
import sys
import tempfile
import time
import unittest
from datetime import date, timedelta

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from config import Config  # noqa: E402

_tmp = tempfile.mkdtemp()
Config.SQLALCHEMY_DATABASE_URI = "sqlite:///" + os.path.join(_tmp, "test.db").replace("\\", "/")
Config.EXPORT_DIR = _tmp
Config.AUDIT_LOG = os.path.join(_tmp, "audit.log")
Config.REDIS_URL = "redis://127.0.0.1:1/0"          # unreachable on purpose: exercises the fallbacks
Config.CELERY_BROKER_URL = "redis://127.0.0.1:1/1"
# Tests must never send real mail (not even with a Gmail .env): point at a closed port, no login.
Config.MAIL_HOST, Config.MAIL_PORT, Config.MAIL_USERNAME, Config.MAIL_USE_TLS, Config.MAIL_USE_SSL = "127.0.0.1", 1, "", False, False

import cache  # noqa: E402
from app import create_app  # noqa: E402

TODAY = date.today()


class EmpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.c = cls.app.test_client()
        cls.tok = {}
        # Ensure models use the test app's db instance
        with cls.app.app_context():
            # Import models inside app context to ensure proper db initialization
            from models import db as _db
            cls.db = _db

    def setUp(self):
        cache._local.clear()  # reset rate-limit counters between tests

    # -- helpers
    def call(self, who, method, url, data=None):
        h = {"Authorization": "Bearer " + self.tok[who]} if who in self.tok else {}
        return self.c.open("/api" + url, method=method, json=data, headers=h)

    def login(self, who, email, pw):
        r = self.call(None, "POST", "/auth/login", {"email": email, "password": pw})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.tok[who] = r.get_json()["token"]

    def ok(self, r, code=200):
        self.assertEqual(r.status_code, code, r.get_data(as_text=True))
        return r.get_json()

    # -- the main flow, in order (unittest runs methods alphabetically, hence the numeric prefixes)
    def test_01_setup_and_rules(self):
        self.login("admin", Config.ADMIN_EMAIL, Config.ADMIN_PASSWORD)
        self.ok(self.call("admin", "POST", "/courses", {"code": "cs1", "name": "Intro & Basics"}), 201)
        self.ok(self.call("admin", "POST", "/examiners", {"email": "x@e.com", "name": "Ex", "password": "secret1"}), 201)
        self.ok(self.call(None, "POST", "/auth/register", {"email": "s1@e.com", "name": "Stu One", "password": "secret1"}), 201)
        self.ok(self.call(None, "POST", "/auth/register", {"email": "s2@e.com", "name": "Stu Two", "password": "secret1"}), 201)
        self.login("ex", "x@e.com", "secret1")
        self.login("s1", "s1@e.com", "secret1")
        self.login("s2", "s2@e.com", "secret1")
        self.call("s1", "POST", "/courses", {"code": "x", "name": "y"})
        self.assertEqual(self.call("s1", "POST", "/courses", {"code": "x", "name": "y"}).status_code, 403)

        e = self.ok(self.call("admin", "POST", "/exams", {
            "course_id": 1, "name": "Viva 1", "type": "Viva", "duration": 15, "max_marks": 50,
            "booking_start": str(TODAY), "booking_end": str(TODAY + timedelta(days=5))}), 201)
        self.assertEqual(e["status"], "Draft")
        self.ok(self.call("admin", "POST", "/exams/1/rubrics", {"criterion": "Knowledge", "max_marks": 10, "weightage": 2}), 201)
        self.ok(self.call("admin", "POST", "/exams/1/rubrics", {"criterion": "Clarity", "max_marks": 10, "weightage": 1}), 201)
        self.assertEqual(self.call("admin", "POST", "/exams/1/status", {"status": "Slot Creation"}).status_code, 400)
        self.ok(self.call("admin", "PUT", "/exams/1/examiners", {"examiner_ids": [2]}))

        slot = {"exam_id": 1, "date": str(TODAY), "start_time": "10:00", "end_time": "11:00", "capacity": 1}
        self.assertEqual(self.call("ex", "POST", "/slots", slot).status_code, 403)          # slot creation not open
        self.ok(self.call("admin", "POST", "/exams/1/status", {"status": "Slot Creation"}))
        self.ok(self.call("ex", "POST", "/slots", slot), 201)
        self.assertEqual(self.call("ex", "POST", "/slots", {**slot, "start_time": "10:30", "end_time": "11:30"}).status_code, 409)
        self.ok(self.call("ex", "POST", "/slots", {**slot, "start_time": "12:00", "end_time": "13:00", "capacity": 2}), 201)

        self.assertEqual(self.call("s1", "POST", "/bookings", {"slot_id": 1}).status_code, 403)   # booking not open
        self.ok(self.call("admin", "POST", "/exams/1/status", {"status": "Booking Open"}))
        self.ok(self.call("s1", "POST", "/bookings", {"slot_id": 1}), 201)
        self.assertEqual(self.call("s1", "POST", "/bookings", {"slot_id": 2}).status_code, 409)  # one per exam
        self.assertEqual(self.call("s2", "POST", "/bookings", {"slot_id": 1}).status_code, 409)  # slot full
        slots = self.ok(self.call("s2", "GET", "/slots?exam_id=1"))
        self.assertEqual([s["status"] for s in slots], ["Full", "Available"])

        # admin moves the booking, then back: seats stay consistent
        self.ok(self.call("admin", "PUT", "/bookings/1/reschedule", {"slot_id": 2}))
        slots = self.ok(self.call("s2", "GET", "/slots?exam_id=1"))
        self.assertEqual([(s["available_seats"], s["status"]) for s in slots], [(1, "Available"), (1, "Available")])
        self.ok(self.call("admin", "PUT", "/bookings/1/reschedule", {"slot_id": 1}))

        # evaluation: weighted marks, range check, own slots only
        bad = {"scores": [{"rubric_id": 1, "marks": 11}, {"rubric_id": 2, "marks": 10}]}
        self.assertEqual(self.call("ex", "POST", "/bookings/1/evaluate", bad).status_code, 400)
        good = {"scores": [{"rubric_id": 1, "marks": 8}, {"rubric_id": 2, "marks": 10}], "remarks": "good"}
        self.assertEqual(self.ok(self.call("ex", "POST", "/bookings/1/evaluate", good))["evaluation"]["total_marks"], 43.33)
        self.assertEqual(self.call("s1", "GET", "/results").get_json(), [])               # hidden until published
        self.assertEqual(self.call("admin", "POST", "/exams/1/publish").status_code, 409)  # still Booking Open
        self.assertEqual(self.call("admin", "POST", "/exams/1/status", {"status": "Completed"}).status_code, 409)  # skip
        self.ok(self.call("admin", "POST", "/exams/1/status", {"status": "Closed"}))
        self.ok(self.call("admin", "POST", "/exams/1/publish"))
        self.assertEqual(self.call("admin", "POST", "/exams/1/publish").status_code, 409)  # no double publish
        self.assertEqual(self.ok(self.call("s1", "GET", "/results"))[0]["evaluation"]["remarks"], "good")

    def test_02_export_runs_without_redis_quickly(self):
        self.login("admin", Config.ADMIN_EMAIL, Config.ADMIN_PASSWORD)
        self.ok(self.call(None, "POST", "/auth/register", {"email": "s3@e.com", "name": "Stu Three", "password": "secret1"}), 201)
        self.login("s3", "s3@e.com", "secret1")
        t = time.time()
        job = self.ok(self.call("s3", "POST", "/export/history"), 202)
        self.assertLess(time.time() - t, 10, "export must not wait for a dead broker")
        self.assertEqual(self.ok(self.call("s3", "GET", f"/export/{job['id']}"))["status"], "Done")
        r = self.call("s3", "GET", f"/export/{job['id']}/download")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Student ID", r.get_data(as_text=True))

    # -- hardening
    def test_03_meeting_link_must_be_http(self):
        self.login("admin", Config.ADMIN_EMAIL, Config.ADMIN_PASSWORD)
        e, _ = self.open_exam("Link rules", [2], [], status="Slot Creation")
        base = {"exam_id": e["id"], "examiner_id": 2, "date": str(TODAY + timedelta(days=1)),
                "start_time": "10:00", "end_time": "11:00", "capacity": 1}
        for bad in ("javascript:alert(1)", "data:text/html,x", "ftp://x.y", "https://a b.c"):
            self.assertEqual(self.call("admin", "POST", "/slots", {**base, "meeting_link": bad}).status_code, 400, bad)
        self.ok(self.call("admin", "POST", "/slots", {**base, "meeting_link": "https://meet.google.com/abc-defg"}), 201)

    def test_04_bad_input_is_json_400_not_500(self):
        self.login("admin", Config.ADMIN_EMAIL, Config.ADMIN_PASSWORD)
        cases = [("POST", "/slots", {"exam_id": "abc"}), ("GET", "/exams?course_id=abc", None),
                 ("PUT", "/exams/1/examiners", {"examiner_ids": "x"}), ("POST", "/courses", {"code": 5, "name": "n"}),
                 ("POST", "/courses", {"code": "zz", "name": "n", "status": "weird"}),
                 ("POST", "/exams", {"course_id": [1], "name": "n", "type": "Viva", "duration": 1, "max_marks": 1})]
        for m, u, d in cases:
            r = self.call("admin", m, u, d)
            self.assertEqual(r.status_code, 400, (m, u, r.get_data(as_text=True)[:100]))
            self.assertEqual(r.mimetype, "application/json")
        r = self.c.get("/api/does-not-exist")
        self.assertEqual((r.status_code, r.mimetype), (404, "application/json"))
        self.assertEqual(self.c.get("/some/spa/route").status_code, 200)                    # SPA routes still work

    def test_05_name_is_not_html_escaped_in_storage(self):
        r = self.ok(self.call(None, "POST", "/auth/register", {"email": "amp@e.com", "name": "R&D Amy", "password": "secret1"}), 201)
        self.assertEqual(r["name"], "R&D Amy")

    def test_06_password_policy_everywhere(self):
        self.login("admin", Config.ADMIN_EMAIL, Config.ADMIN_PASSWORD)
        self.assertEqual(self.call("admin", "PUT", "/examiners/2", {"password": "1"}).status_code, 400)
        self.assertEqual(self.call(None, "POST", "/auth/register", {"email": "w@e.com", "name": "W", "password": "123"}).status_code, 400)

    def test_07_drafts_hidden_from_students(self):
        self.login("admin", Config.ADMIN_EMAIL, Config.ADMIN_PASSWORD)
        self.ok(self.call("admin", "POST", "/exams", {"course_id": 1, "name": "Secret Draft", "type": "Viva",
                                                       "duration": 5, "max_marks": 5}), 201)
        self.login("s1", "s1@e.com", "secret1")
        draft_id = max(e["id"] for e in self.ok(self.call("admin", "GET", "/exams")))
        for url in (f"/exams/{draft_id}", f"/exams/{draft_id}/rubrics", f"/slots?exam_id={draft_id}"):
            self.assertEqual(self.call("s1", "GET", url).status_code, 404, url)

    def test_08_logout_revokes_token(self):
        self.login("tmp", "s1@e.com", "secret1")
        self.assertEqual(self.call("tmp", "GET", "/auth/me").status_code, 200)
        self.assertEqual(self.call("tmp", "POST", "/auth/logout").status_code, 200)
        self.assertEqual(self.call("tmp", "GET", "/auth/me").status_code, 401)

    def test_09_security_headers(self):
        h = self.c.get("/").headers
        self.assertEqual(h.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(h.get("X-Frame-Options"), "DENY")
        self.assertIn("Content-Security-Policy", h)
        page = self.c.get("/").get_data(as_text=True)
        import re
        # the CSP has no 'unsafe-inline' for scripts, so an inline <script> would silently never run
        self.assertEqual(re.findall(r"<script(?![^>]*\bsrc=)[^>]*>", page), [])
        # demo-login credentials are only sent to the browser when the server was started in demo mode
        self.assertNotIn("emp-demo", page)
        self.assertNotIn("admin@123", open(os.path.join(ROOT, "static", "js", "app.js"), encoding="utf-8").read())
        self.app.config["DEMO_LOGIN"] = True
        try:
            self.assertIn('name="emp-demo"', self.c.get("/").get_data(as_text=True))
        finally:
            self.app.config["DEMO_LOGIN"] = False

    # -- database integrity
    def test_10_database_enforces_integrity(self):
        from sqlalchemy.exc import IntegrityError
        with self.app.app_context():
            from models import Booking
            self.db.session.add(Booking(student_id=9999, slot_id=9999, exam_id=9999))
            with self.assertRaises(IntegrityError):                      # foreign keys are enforced
                self.db.session.commit()
            self.db.session.rollback()
            self.db.session.add(Booking(student_id=3, slot_id=2, exam_id=1, status="Booked"))
            with self.assertRaises(IntegrityError):                      # student 3 already has an active booking in exam 1
                self.db.session.commit()
            self.db.session.rollback()
            self.db.session.add(Booking(student_id=3, slot_id=2, exam_id=1, status="Cancelled"))
            self.db.session.commit()                                           # cancelled duplicates are allowed

    # -- business rules
    def test_11_published_exam_is_locked(self):
        self.login("admin", Config.ADMIN_EMAIL, Config.ADMIN_PASSWORD)
        self.assertEqual(self.call("admin", "POST", "/exams/1/status", {"status": "Slot Creation"}).status_code, 409)
        body = {"course_id": 1, "name": "Viva 1", "type": "Viva", "duration": 15, "max_marks": 60}
        self.assertEqual(self.call("admin", "PUT", "/exams/1", body).status_code, 409)       # marks locked
        self.assertEqual(self.call("admin", "POST", "/exams/1/rubrics", {"criterion": "New", "max_marks": 5}).status_code, 409)
        self.assertEqual(self.call("admin", "PUT", "/rubrics/1", {"criterion": "K", "max_marks": 9}).status_code, 409)
        self.assertEqual(self.call("admin", "POST", "/exams/1/status", {"status": "Draft"}).status_code, 409)

    def test_12_no_slots_in_the_past(self):
        self.login("admin", Config.ADMIN_EMAIL, Config.ADMIN_PASSWORD)
        yesterday = str(TODAY - timedelta(days=1))
        r = self.call("admin", "POST", "/slots", {"exam_id": 1, "examiner_id": 2, "date": yesterday,
                                                   "start_time": "10:00", "end_time": "11:00", "capacity": 1})
        self.assertEqual(r.status_code, 400)

    def test_13_reminder_goes_out_once_for_tomorrow_only(self):
        import tasks
        with self.app.app_context():
            from models import Exam, Slot, Booking
            ex = Exam(course_id=1, name="Reminder Exam", type="Viva", duration=10, max_marks=10, status="Booking Open")
            self.db.session.add(ex)
            self.db.session.flush()
            today_slot = Slot(exam_id=ex.id, examiner_id=2, date=TODAY, start_time=__import__("datetime").time(9, 0),
                              end_time=__import__("datetime").time(10, 0), capacity=2, available_seats=1)
            tomorrow_slot = Slot(exam_id=ex.id, examiner_id=2, date=TODAY + timedelta(days=1),
                                 start_time=__import__("datetime").time(9, 0), end_time=__import__("datetime").time(10, 0),
                                 capacity=2, available_seats=1)
            self.db.session.add_all([today_slot, tomorrow_slot])
            self.db.session.flush()
            self.db.session.add_all([Booking(student_id=4, slot_id=today_slot.id, exam_id=ex.id),
                                Booking(student_id=5, slot_id=tomorrow_slot.id, exam_id=ex.id)])
            self.db.session.commit()
        self.assertEqual(tasks.daily_reminders(), 1)

    def test_14_seed_script_works_with_enforced_foreign_keys(self):
        import runpy
        original = Config.SQLALCHEMY_DATABASE_URI
        Config.SQLALCHEMY_DATABASE_URI = "sqlite:///" + os.path.join(_tmp, "seed.db").replace("\\", "/")
        argv = sys.argv
        sys.argv = ["seed_data.py", "--yes"]
        try:
            g = runpy.run_path(os.path.join(ROOT, "seed_data.py"))
            from models import User, Exam, Booking
            with g["app"].app_context():
                self.assertEqual((User.query.count(), Exam.query.count(), Booking.query.count()), (13, 4, 5))
                g["db"].session.remove()
            with open(os.devnull, "w"):
                runpy.run_path(os.path.join(ROOT, "seed_data.py"))      # re-seeding (delete order with FKs) must also work
        finally:
            sys.argv = argv
            Config.SQLALCHEMY_DATABASE_URI = original

    def test_15_student_cannot_book_overlapping_slots_across_exams(self):
        self.login("admin", Config.ADMIN_EMAIL, Config.ADMIN_PASSWORD)
        self.login("s2", "s2@e.com", "secret1")
        ex2 = self.new_examiner("x2@e.com")            # a second examiner, so the clash is the student's, not the examiner's
        # Two examinations on the same day: 12:00-13:00 with examiner 2 and an overlapping 12:30-13:30 with ex2
        _, (first,) = self.open_exam("Viva 2", [2], [(2, 2, "12:00", "13:00", 2)])
        _, (s3,) = self.open_exam("Viva 3", [ex2], [(ex2, 2, "12:30", "13:30", 2)])
        self.ok(self.call("s2", "POST", "/bookings", {"slot_id": first["id"]}), 201)
        # Attempting to book the overlapping slot returns 409
        r = self.call("s2", "POST", "/bookings", {"slot_id": s3["id"]})
        self.assertEqual(r.status_code, 409)
        self.assertIn("overlaps", r.get_json()["error"])

    # -- lifecycle, authorization and concurrency rules (each test builds its own examinations)
    def new_examiner(self, email):
        self.ok(self.call("admin", "POST", "/examiners", {"email": email, "name": email.split("@")[0], "password": "secret1"}), 201)
        return max(u["id"] for u in self.ok(self.call("admin", "GET", "/examiners")))

    def open_exam(self, name, examiners, slots, status="Booking Open"):
        """An examination with one rubric criterion, the given examiners and slots, moved on to `status`."""
        e = self.ok(self.call("admin", "POST", "/exams", {
            "course_id": 1, "name": name, "type": "Viva", "duration": 15, "max_marks": 50,
            "booking_start": str(TODAY), "booking_end": str(TODAY + timedelta(days=60))}), 201)
        self.ok(self.call("admin", "POST", f"/exams/{e['id']}/rubrics", {"criterion": "Content", "max_marks": 10, "weightage": 1}), 201)
        self.ok(self.call("admin", "PUT", f"/exams/{e['id']}/examiners", {"examiner_ids": examiners}))
        self.ok(self.call("admin", "POST", f"/exams/{e['id']}/status", {"status": "Slot Creation"}))
        made = [self.ok(self.call("admin", "POST", "/slots", {
            "exam_id": e["id"], "examiner_id": s[0], "date": str(TODAY + timedelta(days=s[1])),
            "start_time": s[2], "end_time": s[3], "capacity": s[4]}), 201) for s in slots]
        if status == "Booking Open":
            self.ok(self.call("admin", "POST", f"/exams/{e['id']}/status", {"status": "Booking Open"}))
        return e, made

    def test_16_status_changes_follow_the_lifecycle(self):
        self.login("admin", Config.ADMIN_EMAIL, Config.ADMIN_PASSWORD)
        e = self.ok(self.call("admin", "POST", "/exams", {"course_id": 1, "name": "Lifecycle", "type": "Viva",
                                                           "duration": 5, "max_marks": 5}), 201)
        eid = e["id"]
        self.assertEqual(e["next_statuses"], ["Slot Creation"])
        for bad in ("Booking Open", "Closed", "Completed"):                      # no skipping ahead from Draft
            self.assertEqual(self.call("admin", "POST", f"/exams/{eid}/status", {"status": bad}).status_code, 409, bad)
        slot = {"exam_id": eid, "examiner_id": 2, "date": str(TODAY + timedelta(days=20)),
                "start_time": "09:00", "end_time": "10:00", "capacity": 1}
        self.ok(self.call("admin", "POST", f"/exams/{eid}/rubrics", {"criterion": "C", "max_marks": 5}), 201)
        self.ok(self.call("admin", "PUT", f"/exams/{eid}/examiners", {"examiner_ids": [2]}))
        self.assertEqual(self.call("admin", "POST", "/slots", slot).status_code, 409)       # admin cannot bypass Draft
        self.ok(self.call("admin", "POST", f"/exams/{eid}/status", {"status": "Slot Creation"}))
        self.ok(self.call("admin", "POST", "/slots", slot), 201)                              # allowed in Slot Creation
        self.assertEqual(self.call("admin", "POST", f"/exams/{eid}/status", {"status": "Completed"}).status_code, 409)
        self.ok(self.call("admin", "POST", f"/exams/{eid}/status", {"status": "Closed"}))
        self.assertEqual(self.call("admin", "POST", "/slots", {**slot, "start_time": "11:00", "end_time": "12:00"}).status_code, 409)
        self.assertEqual(self.call("admin", "POST", f"/exams/{eid}/publish").status_code, 200)   # closed, nobody booked
        self.assertEqual(self.call("admin", "POST", f"/exams/{eid}/status", {"status": "Closed"}).status_code, 409)

    def test_17_removed_examiner_loses_access(self):
        self.login("admin", Config.ADMIN_EMAIL, Config.ADMIN_PASSWORD)
        self.login("s1", "s1@e.com", "secret1")
        ex3 = self.new_examiner("x3@e.com")
        e, (slot,) = self.open_exam("Reassign", [2, ex3], [(2, 3, "09:00", "10:00", 1)])
        eid = e["id"]
        # an examiner who still owns an open slot cannot be taken off the examination
        r = self.call("admin", "PUT", f"/exams/{eid}/examiners", {"examiner_ids": [ex3]})
        self.assertEqual(r.status_code, 409, r.get_data(as_text=True))
        self.assertIn("open slots", r.get_json()["error"])
        b = self.ok(self.call("s1", "POST", "/bookings", {"slot_id": slot["id"]}), 201)
        self.ok(self.call("admin", "PUT", f"/slots/{slot['id']}/examiner", {"examiner_id": ex3}))
        self.ok(self.call("admin", "PUT", f"/exams/{eid}/examiners", {"examiner_ids": [ex3]}))   # now allowed
        # the removed examiner no longer sees the examination, its roster or the evaluation form
        self.assertEqual(self.call("ex", "GET", f"/exams/{eid}").status_code, 404)
        self.assertEqual(self.call("ex", "GET", f"/exams/{eid}/rubrics").status_code, 404)
        self.assertEqual(self.call("ex", "POST", f"/bookings/{b['id']}/evaluate",
                                   {"scores": [{"rubric_id": 1, "marks": 5}]}).status_code, 403)
        # even if a stale slot were left behind (legacy data), assignment is what counts
        with self.app.app_context():
            from models import Slot
            slot_obj = self.db.session.get(Slot, slot["id"])
            slot_obj.examiner_id = 2
            self.db.session.commit()
        r = self.call("ex", "POST", f"/bookings/{b['id']}/evaluate", {"scores": [{"rubric_id": 1, "marks": 5}]})
        self.assertEqual(r.status_code, 403)
        self.assertIn("no longer assigned", r.get_json()["error"])
        self.assertEqual(self.call("ex", "GET", f"/slots/{slot['id']}/bookings").status_code, 403)
        # unknown / inactive examiners are rejected instead of being ignored
        self.assertEqual(self.call("admin", "PUT", f"/exams/{eid}/examiners", {"examiner_ids": [ex3, 99999]}).status_code, 400)

    def test_18_publication_rules_and_evaluation_history(self):
        self.login("admin", Config.ADMIN_EMAIL, Config.ADMIN_PASSWORD)
        self.login("s1", "s1@e.com", "secret1")
        self.login("s2", "s2@e.com", "secret1")
        e, (slot,) = self.open_exam("Publish rules", [2], [(2, 4, "09:00", "10:00", 2)])
        eid = e["id"]
        b1 = self.ok(self.call("s1", "POST", "/bookings", {"slot_id": slot["id"]}), 201)
        self.ok(self.call("s2", "POST", "/bookings", {"slot_id": slot["id"]}), 201)
        rubric = self.ok(self.call("admin", "GET", f"/exams/{eid}/rubrics"))[0]["id"]
        self.assertEqual(self.call("admin", "POST", f"/exams/{eid}/publish").status_code, 409)   # not closed yet
        # re-evaluating keeps the earlier version
        first = self.ok(self.call("ex", "POST", f"/bookings/{b1['id']}/evaluate", {"scores": [{"rubric_id": rubric, "marks": 4}]}))
        self.ok(self.call("ex", "POST", f"/bookings/{b1['id']}/evaluate", {"scores": [{"rubric_id": rubric, "marks": 9}], "remarks": "fixed"}))
        hist = self.ok(self.call("admin", "GET", f"/bookings/{b1['id']}/evaluation-history"))
        self.assertEqual([h["total_marks"] for h in hist], [first["evaluation"]["total_marks"]])
        self.assertEqual(hist[0]["scores"][0]["marks"], 4)
        self.assertEqual(self.call("ex", "GET", f"/bookings/{b1['id']}/evaluation-history").status_code, 403)
        # one student is still unevaluated: publishing needs an explicit "force"
        self.ok(self.call("admin", "POST", f"/exams/{eid}/status", {"status": "Closed"}))
        r = self.call("admin", "POST", f"/exams/{eid}/publish")
        self.assertEqual((r.status_code, r.get_json()["pending_evaluations"]), (409, 1))
        t = time.time()
        done = self.ok(self.call("admin", "POST", f"/exams/{eid}/publish", {"force": True}))
        self.assertLess(time.time() - t, 3, "publishing must not wait for the SMTP server (none is running in the tests)")
        self.assertEqual((done["exam"]["status"], done["exam"]["results_published"], done["pending_evaluations"]),
                         ("Completed", True, 1))
        self.assertEqual(self.call("ex", "POST", f"/bookings/{b1['id']}/evaluate",
                                   {"scores": [{"rubric_id": rubric, "marks": 1}]}).status_code, 403)   # locked now
        with open(Config.AUDIT_LOG, encoding="utf-8") as f:                                   # audit trail was written
            log = f.read()
        self.assertIn("action=exam.publish", log)
        self.assertIn("action=evaluation.save", log)

    def test_19_only_failed_logins_count(self):
        def attempt(ip, password):  # every attempt comes from its own address, so only the ACCOUNT counter is tested
            return self.c.post("/api/auth/login", json={"email": "s1@e.com", "password": password},
                               environ_base={"REMOTE_ADDR": ip}).status_code
        self.assertEqual([attempt("10.1.0.1", "secret1") for _ in range(15)], [200] * 15)   # good sign-ins never lock
        self.assertEqual([attempt(f"10.2.0.{i}", "nope") for i in range(3)], [401] * 3)
        self.assertEqual(attempt("10.2.1.1", "secret1"), 200)             # a success clears the failure count
        self.assertEqual([attempt(f"10.3.0.{i}", "nope") for i in range(10)], [401] * 10)
        self.assertEqual(attempt("10.3.1.1", "secret1"), 429)             # ten failures in a row do lock the account

    def test_20_simultaneous_requests_cannot_create_clashes(self):
        import threading
        self.login("admin", Config.ADMIN_EMAIL, Config.ADMIN_PASSWORD)
        self.login("s3", "s3@e.com", "secret1")
        ex3, ex4 = self.new_examiner("x4@e.com"), self.new_examiner("x5@e.com")

        def race(requests):
            """Fire all requests at the same moment from separate threads; return their status codes."""
            gate, out = threading.Barrier(len(requests)), [None] * len(requests)

            def run(i, who, method, url, data):
                client = self.app.test_client()
                gate.wait()
                out[i] = client.open("/api" + url, method=method, json=data,
                                     headers={"Authorization": "Bearer " + self.tok[who]}).status_code
            ts = [threading.Thread(target=run, args=(i, *r)) for i, r in enumerate(requests)]
            [t.start() for t in ts]
            [t.join() for t in ts]
            return sorted(out)

        # 1) two overlapping slots for the same examiner: exactly one wins
        e, _ = self.open_exam("Race slots", [ex3], [(ex3, 30, "08:00", "09:00", 1)])
        mk = lambda a, b: ("admin", "POST", "/slots", {"exam_id": e["id"], "examiner_id": ex3, "date": str(TODAY + timedelta(days=31)),
                                                        "start_time": a, "end_time": b, "capacity": 1})
        self.assertEqual(race([mk("09:00", "10:00"), mk("09:30", "10:30"), mk("09:15", "09:45")]), [201, 409, 409])
        # 2) one student, two overlapping slots in different examinations: exactly one booking
        _, (a,) = self.open_exam("Race A", [ex3], [(ex3, 40, "09:00", "10:00", 2)])
        _, (b,) = self.open_exam("Race B", [ex4], [(ex4, 40, "09:30", "10:30", 2)])
        book = lambda s: ("s3", "POST", "/bookings", {"slot_id": s["id"]})
        self.assertEqual(race([book(a), book(b)]), [201, 409])

    def test_21_student_dashboard_counts_only_bookable_slots(self):
        import datetime as dt
        self.ok(self.call(None, "POST", "/auth/register", {"email": "s9@e.com", "name": "Fresh", "password": "secret1"}), 201)
        self.login("s9", "s9@e.com", "secret1")
        before = self.ok(self.call("s9", "GET", "/dashboard"))["available_slots"]
        with self.app.app_context():
            from models import Exam, Slot
            closed = Exam(course_id=1, name="Window over", type="Viva", duration=5, max_marks=5, status="Booking Open",
                          booking_start=TODAY - timedelta(days=9), booking_end=TODAY - timedelta(days=1))
            self.db.session.add(closed)
            self.db.session.flush()
            for days in (-2, 3):   # a past slot and a future slot, both in an examination whose booking window ended
                self.db.session.add(Slot(exam_id=closed.id, examiner_id=2, date=TODAY + timedelta(days=days),
                                    start_time=dt.time(6, 0), end_time=dt.time(7, 0), capacity=1, available_seats=1))
            self.db.session.commit()
        d = self.ok(self.call("s9", "GET", "/dashboard"))
        self.assertEqual(d["available_slots"], before)
        self.assertNotIn("Window over", [x["name"] for x in d["available_exams"]])

    def test_22_openapi_describes_every_route(self):
        import re
        spec = open(os.path.join(ROOT, "openapi.yaml"), encoding="utf-8").read()
        documented, path = set(), None
        for line in spec.splitlines():
            m = re.match(r"^  (/[^\s:]*):\s*$", line)
            if m:
                path = re.sub(r"\{\w+\}", "{}", m.group(1))        # parameter names differ; only the shape matters
            m = re.match(r"^    (get|post|put|delete):", line)
            if m and path:
                documented.add((m.group(1).upper(), path))
        actual = set()
        for rule in self.app.url_map.iter_rules():
            if rule.rule.startswith("/api/"):
                for method in rule.methods - {"HEAD", "OPTIONS"}:
                    actual.add((method, re.sub(r"<(?:int:)?\w+>", "{}", rule.rule[len("/api"):])))
        self.assertEqual(sorted(actual - documented), [], "routes missing from openapi.yaml")

    def test_23_login_checks_the_chosen_role(self):
        def go(email, pw, role):
            data = {"email": email, "password": pw}
            if role is not ...:
                data["role"] = role
            return self.call(None, "POST", "/auth/login", data)
        admin, ex, stu = (Config.ADMIN_EMAIL, Config.ADMIN_PASSWORD), ("x@e.com", "secret1"), ("s1@e.com", "secret1")
        for (email, pw), role in ((admin, "admin"), (ex, "examiner"), (stu, "student")):
            r = go(email, pw, role)
            self.assertEqual((r.status_code, r.get_json()["user"]["role"]), (200, role))
        self.assertEqual(go(*stu, ...).status_code, 200)                      # API clients may leave the role out
        for (email, pw), wrong in ((admin, "student"), (ex, "admin"), (stu, "examiner")):
            r = go(email, pw, wrong)
            self.assertEqual(r.status_code, 403, (email, wrong))
            self.assertIn(f"not {'an' if wrong != 'student' else 'a'} {wrong} account", r.get_json()["error"])
            self.assertNotIn("token", r.get_json())
        # a wrong password is still just "Invalid credentials": the role is never revealed to someone without it
        r = go("s1@e.com", "wrong-pass", "admin")
        self.assertEqual((r.status_code, r.get_json()["error"]), (401, "Invalid credentials"))
        for junk in ("superuser", "", ["admin"], 5):
            self.assertEqual(go(*stu, junk).status_code, 400, junk)

    def test_24_every_pointer_of_the_brief(self):
        """Walks the Roles & Functionalities list of the project brief, bullet by bullet, through the API."""
        A = lambda *a, **k: self.call("admin", *a, **k)
        S1 = lambda *a, **k: self.call("s1", *a, **k)
        self.login("admin", Config.ADMIN_EMAIL, Config.ADMIN_PASSWORD)
        self.login("s1", "s1@e.com", "secret1")

        # ---- ADMIN: courses (create / update / remove) -------------------------------------------------------
        c = self.ok(A("POST", "/courses", {"code": "zz9", "name": "Spec Course", "description": "d"}), 201)
        self.assertEqual((c["code"], c["status"]), ("ZZ9", "Active"))
        self.ok(A("PUT", f"/courses/{c['id']}", {"name": "Spec Course 2", "description": "d2", "status": "Inactive"}))
        self.assertNotIn(c["id"], [x["id"] for x in self.ok(S1("GET", "/courses"))])      # inactive: hidden from students
        self.ok(A("DELETE", f"/courses/{c['id']}"))
        self.assertEqual(A("DELETE", "/courses/1").status_code, 409)                      # a course with exams is kept

        # ---- ADMIN: add and manage examiners; examiners exist only if the admin creates them -------------------
        xid = self.new_examiner("spec_ex@e.com")
        self.ok(A("PUT", f"/examiners/{xid}", {"name": "Spec Examiner", "phone": "+91 98765 43210", "department": "CSE"}))
        self.assertIn(xid, [x["id"] for x in self.ok(A("GET", "/examiners?q=CSE"))])      # search by department
        self.ok(A("PUT", f"/examiners/{xid}", {"status": "Inactive"}))
        self.assertEqual(self.call(None, "POST", "/auth/login", {"email": "spec_ex@e.com", "password": "secret1"}).status_code, 403)
        self.ok(A("PUT", f"/examiners/{xid}", {"status": "Active"}))
        self.login("sx", "spec_ex@e.com", "secret1")
        sneaky = self.ok(self.call(None, "POST", "/auth/register", {"email": "sneaky@e.com", "name": "S", "password": "secret1", "role": "examiner"}), 201)
        self.assertEqual(sneaky["role"], "student")                                        # registration can only make students
        self.assertEqual(S1("POST", "/examiners", {"email": "q@e.com", "name": "Q", "password": "secret1"}).status_code, 403)
        self.assertEqual(self.call("sx", "POST", "/examiners", {"email": "q@e.com", "name": "Q", "password": "secret1"}).status_code, 403)

        # ---- ADMIN: examination + rubric + timelines + examiner assignment ------------------------------------
        e, (slot,) = self.open_exam("Spec Exam", [xid], [(xid, 6, "09:00", "10:00", 2)], status="Slot Creation")
        eid = e["id"]
        self.ok(A("POST", f"/exams/{eid}/rubrics", {"criterion": "Delivery", "max_marks": 5, "weightage": 1, "description": "How it was said"}), 201)
        rub = self.ok(A("GET", f"/exams/{eid}/rubrics"))
        self.assertEqual([r["criterion"] for r in rub], ["Content", "Delivery"])
        self.ok(A("PUT", f"/rubrics/{rub[1]['id']}", {"criterion": "Delivery", "max_marks": 10, "weightage": 1}))
        self.ok(A("DELETE", f"/rubrics/{rub[1]['id']}"))
        base = {"course_id": 1, "name": "Spec Exam", "type": "Practical", "duration": 20, "max_marks": 50}
        window = lambda a, b: {**base, "slot_start": str(TODAY + timedelta(days=a)), "slot_end": str(TODAY + timedelta(days=b)),
                               "booking_start": str(TODAY), "booking_end": str(TODAY + timedelta(days=30))}
        got = self.ok(A("PUT", f"/exams/{eid}", window(2, 9)))                             # configure timelines
        self.assertEqual((got["slot_start"], got["booking_end"], got["type"]), (str(TODAY + timedelta(days=2)), str(TODAY + timedelta(days=30)), "Practical"))

        # ---- EXAMINER: assigned exams, slots only inside the slot-creation period, edit/remove before booking ---
        self.assertEqual([x["id"] for x in self.ok(self.call("sx", "GET", "/exams"))], [eid])       # sees assigned exams only
        mine = {"exam_id": eid, "date": str(TODAY + timedelta(days=7)), "start_time": "14:00", "end_time": "15:00", "capacity": 3}
        self.assertEqual(self.call("sx", "POST", "/slots", mine).status_code, 403)        # slot window not open yet
        self.ok(A("PUT", f"/exams/{eid}", window(-1, 9)))
        s2 = self.ok(self.call("sx", "POST", "/slots", mine), 201)
        self.assertEqual(self.ok(self.call("sx", "PUT", f"/slots/{s2['id']}", {**mine, "capacity": 4}))["capacity"], 4)
        self.ok(self.call("sx", "DELETE", f"/slots/{s2['id']}"))
        ids = [s["id"] for s in self.ok(A("GET", "/slots"))]                              # admin sees every slot
        self.assertIn(slot["id"], ids)
        self.assertEqual([s["id"] for s in self.ok(self.call("sx", "GET", "/slots"))].count(slot["id"]), 1)

        # ---- ADMIN: open booking; examiner may no longer touch slots ------------------------------------------
        self.ok(A("POST", f"/exams/{eid}/status", {"status": "Booking Open"}))
        same = {"exam_id": eid, "date": str(TODAY + timedelta(days=6)), "start_time": "09:00", "end_time": "10:00", "capacity": 2}
        self.assertEqual(self.call("sx", "PUT", f"/slots/{slot['id']}", same).status_code, 403)
        self.assertEqual(self.call("sx", "DELETE", f"/slots/{slot['id']}").status_code, 403)

        # ---- STUDENT: search/filter, book, booking status, profile -------------------------------------------
        found = self.ok(S1("GET", "/exams?q=Spec&type=Practical&status=Booking%20Open"))
        self.assertEqual([x["name"] for x in found], ["Spec Exam"])
        self.assertEqual(self.ok(S1("GET", "/exams?type=Viva&q=Spec")), [])               # filters really filter
        b1 = self.ok(S1("POST", "/bookings", {"slot_id": slot["id"]}), 201)
        self.assertEqual(b1["status"], "Booked")
        self.assertEqual([x["status"] for x in self.ok(S1("GET", "/bookings")) if x["id"] == b1["id"]], ["Booked"])
        roster = self.ok(self.call("sx", "GET", f"/slots/{slot['id']}/bookings"))          # examiner sees booked students
        self.assertEqual([r["student_name"] for r in roster], ["Stu One"])
        prof = self.ok(S1("PUT", "/auth/me", {"name": "Stu One", "phone": "98765 43210", "department": "ECE"}))
        self.assertEqual((prof["phone"], prof["department"]), ("98765 43210", "ECE"))

        # ---- ADMIN: reschedule a slot in an exceptional situation, change the examiner ------------------------
        moved = self.ok(A("PUT", f"/slots/{slot['id']}", {**same, "start_time": "11:00", "end_time": "12:00"}))
        self.assertEqual(moved["start_time"], "11:00")
        self.assertTrue(any("rescheduled" in n["message"] for n in self.ok(S1("GET", "/notifications"))))   # student is told
        self.ok(A("PUT", f"/exams/{eid}/examiners", {"examiner_ids": [xid, 2]}))
        self.assertEqual(self.ok(A("PUT", f"/slots/{slot['id']}/examiner", {"examiner_id": 2}))["examiner_id"], 2)
        self.ok(A("PUT", f"/slots/{slot['id']}/examiner", {"examiner_id": xid}))

        # ---- STUDENT: cancel before the deadline (seat returns), not after it ---------------------------------
        seats = lambda: [s for s in self.ok(S1("GET", f"/slots?exam_id={eid}")) if s["id"] == slot["id"]][0]["available_seats"]
        self.assertEqual(seats(), 1)
        self.ok(S1("POST", f"/bookings/{b1['id']}/cancel"))
        self.assertEqual(seats(), 2)
        b2 = self.ok(S1("POST", "/bookings", {"slot_id": slot["id"]}), 201)                # may book again after cancelling
        self.ok(A("PUT", f"/exams/{eid}", {**window(-1, 9), "booking_start": str(TODAY - timedelta(days=5)), "booking_end": str(TODAY - timedelta(days=1))}))
        late = S1("POST", f"/bookings/{b2['id']}/cancel")
        self.assertEqual(late.status_code, 403, late.get_data(as_text=True))               # deadline has passed
        self.ok(A("PUT", f"/exams/{eid}", window(-1, 9)))

        # ---- EXAMINER: evaluate with the rubric, marks + remarks, update the exam status -----------------------
        rid = rub[0]["id"]
        done = self.ok(self.call("sx", "POST", f"/bookings/{b2['id']}/evaluate", {"scores": [{"rubric_id": rid, "marks": 8}], "remarks": "Well done"}))
        self.assertEqual((done["evaluation"]["total_marks"], done["evaluation"]["remarks"], done["status"]), (40.0, "Well done", "Completed"))
        self.assertEqual([r for r in S1("GET", "/results").get_json() if r["exam_id"] == eid], [])   # hidden until published
        self.ok(self.call("sx", "POST", f"/exams/{eid}/status", {"status": "Closed"}))      # examiner updates the status

        # ---- ADMIN publishes; STUDENT sees result + examiner feedback and history --------------------------------
        self.ok(A("POST", f"/exams/{eid}/publish"))
        res = [r for r in self.ok(S1("GET", "/results")) if r["exam_id"] == eid]
        self.assertEqual((res[0]["evaluation"]["total_marks"], res[0]["evaluation"]["remarks"]), (40.0, "Well done"))
        self.assertIn(eid, [r["exam_id"] for r in self.ok(S1("GET", "/bookings"))])         # examination history

        # ---- ADMIN: search everything, reports and statistics -----------------------------------------------------
        hit = self.ok(A("GET", "/admin/search?q=Spec"))
        self.assertIn("Spec Exam", [x["name"] for x in hit["exams"]])
        self.assertIn("Spec Examiner", [x["name"] for x in hit["examiners"]])
        self.assertTrue(self.ok(A("GET", "/bookings?q=Spec")))                              # search bookings
        self.assertTrue(self.ok(A("GET", "/students?q=Stu")))                               # search students
        rep = self.ok(A("GET", "/admin/report"))
        self.assertGreaterEqual(rep["students_evaluated"], 1)
        pdf = A("GET", "/admin/report/pdf")
        self.assertEqual((pdf.status_code, pdf.mimetype, pdf.data[:4]), (200, "application/pdf", b"%PDF"))
        self.assertEqual(sorted(self.ok(A("GET", "/admin/charts"))), ["popular", "trend", "workload"])

        # ---- brief's terminology: the attributes every entity must carry ----------------------------------------
        self.assertTrue({"id", "code", "name", "description", "status"} <= set(c))                                  # Course
        self.assertTrue({"id", "course_id", "name", "type", "duration", "max_marks", "slot_start", "slot_end",
                         "booking_start", "booking_end", "status"} <= set(e))                                       # Examination
        self.assertTrue({"id", "exam_id", "criterion", "max_marks", "weightage", "description"} <= set(rub[0]))    # Rubric
        self.assertTrue({"id", "exam_id", "examiner_id", "date", "start_time", "end_time", "capacity",
                         "available_seats", "status", "meeting_link"} <= set(slot))                                 # Slot
        self.assertTrue({"id", "student_id", "slot_id", "booked_at", "status"} <= set(b1))                          # Booking
        self.assertTrue({"id", "total_marks", "remarks", "evaluated_at", "status"} <= set(done["evaluation"]))      # Evaluation
        x = self.ok(A("GET", "/examiners?q=Spec"))[0]
        self.assertTrue({"id", "name", "email", "phone", "department", "status"} <= set(x))                         # Examiner profile
        for kind in ("Viva", "Practical", "Project Demo", "Assessment"):                                           # the 4 types
            self.ok(A("POST", "/exams", {**base, "type": kind, "name": "Type " + kind}), 201)
        self.assertEqual(A("POST", "/exams", {**base, "type": "Quiz"}).status_code, 400)                             # nothing else

    def test_25_monthly_report_is_marked_done_only_when_it_was_sent(self):
        import tasks
        
        def count():
            with self.app.app_context():
                from models import Notification
                return Notification.query.filter(Notification.message.like("Monthly report for%")).count()
        
        start = count()
        real, tasks.send_mail = tasks.send_mail, lambda *a, **k: False   # mail fails (independent of any local SMTP)
        try:
            tasks.monthly_report()                              # nothing was sent...
        finally:
            tasks.send_mail = real
        self.assertEqual(count(), start)                        # ...so it is not recorded and would be retried
        real, tasks.send_mail = tasks.send_mail, lambda *a, **k: True
        try:
            tasks.monthly_report()
            tasks.monthly_report()                              # a second run for the same month must not repeat it
        finally:
            tasks.send_mail = real
        self.assertEqual(count(), start + 1)

    def test_26_tasks_can_build_the_app_inside_a_celery_worker(self):
        """Regression: the Celery CLI removes the backend folder from sys.path after start-up, so a task doing
        `from app import create_app` failed in a real worker (tests that call the tasks directly never saw it)."""
        import subprocess
        backend = os.path.join(ROOT, "backend")
        db = "sqlite:///" + os.path.join(_tmp, "worker_sim.db").replace("\\", "/")
        script = (
            "import os, sys\n"
            "sys.path.insert(0, os.getcwd())\n"              # what `celery -A celery_app.celery` does while importing
            "import tasks, config\n"
            "sys.path.remove(os.getcwd())\n"                  # ...and undoes once start-up is over
            f"config.Config.SQLALCHEMY_DATABASE_URI = {db!r}\n"
            "assert tasks.flask_app() is not None\n"
            "print('worker-sim ok')\n")
        r = subprocess.run([sys.executable, "-W", "ignore", "-c", script], cwd=backend, capture_output=True, text=True, timeout=60)
        self.assertIn("worker-sim ok", r.stdout, r.stderr[-600:])

    def test_27_single_command_entry_point_is_present_and_working(self):
        import subprocess
        r = subprocess.run([sys.executable, os.path.join(ROOT, "run.py"), "--help"], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        for flag in ("--demo", "--seed", "--no-redis", "--stop", "--check", "--port"):
            self.assertIn(flag, r.stdout)
        for name in ("run.cmd", "run.sh", "check_services.py", "requirements.txt"):
            self.assertTrue(os.path.exists(os.path.join(ROOT, name)), name)
        # the defaults must not use the name "localhost": on Windows it adds ~2 s to every new connection (IPv6 first)
        src = open(os.path.join(ROOT, "backend", "config.py"), encoding="utf-8").read()
        self.assertNotIn("redis://localhost", src)
        self.assertIn('"MAIL_HOST", "127.0.0.1"', src)

    def test_99_login_rate_limit(self):
        codes = [self.call(None, "POST", "/auth/login", {"email": "a@b.co", "password": "bad"}).status_code for _ in range(12)]
        self.assertEqual(codes[:10], [401] * 10)
        self.assertEqual(codes[10:], [429, 429])


if __name__ == "__main__":
    unittest.main(verbosity=2)
