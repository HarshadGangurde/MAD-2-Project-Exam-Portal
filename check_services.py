"""Checks the Redis / Celery side of EMP and says plainly what works. `python run.py` runs it for you at start-up.

    python check_services.py            # read-only checks (throw-away keys, a ping and a health job; changes nothing)
    python check_services.py --jobs     # also runs the daily-reminder job through the worker (touches data)
    python run.py --check               # the same as --jobs, using the project's virtual environment

Needs Redis running and, for the job checks, the Celery worker (`python run.py` starts everything).
Exit code 0 = every check passed.
"""
import glob
import os
import sys
import time

BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend")
sys.path.insert(0, BACKEND)

import logging  # noqa: E402

import redis  # noqa: E402

logging.getLogger("app").setLevel(logging.ERROR)   # the app's "default admin password" start-up notice is not news here
from app import create_app  # noqa: E402
from celery_app import celery  # noqa: E402
from config import Config  # noqa: E402
import cache  # noqa: E402

results = []


def check(name, ok, detail="", hint=""):
    print(("PASS  " if ok else "FAIL  ") + name + (f"  -  {detail}" if detail else ""))
    if not ok and hint:
        print("        -> " + hint)
    results.append(ok)
    return ok


def info(text):
    print("INFO  " + text)


def finish():
    print()
    if all(results):
        print(f"ALL {len(results)} CHECKS PASSED")
        sys.exit(0)
    print(f"{results.count(False)} of {len(results)} CHECKS FAILED")
    sys.exit(1)


print(f"Redis cache   : {Config.REDIS_URL}\nCelery broker : {Config.CELERY_BROKER_URL}\n")

# ---- 1. Redis itself -------------------------------------------------------------------------------------------
try:
    r = redis.Redis.from_url(Config.REDIS_URL, socket_connect_timeout=2, socket_timeout=2, decode_responses=True)
    r.ping()
    check("Redis answers PING", True, "server " + r.info("server").get("redis_version", "?"))
except redis.RedisError as e:
    check("Redis answers PING", False, str(e).splitlines()[0],
          "start Redis first (start_all.ps1 does it, or: redis-server --bind 127.0.0.1 --port 6379). "
          "Without Redis the app still runs on its in-memory fallback, but the brief's Redis features are not in use.")
    finish()

app = create_app()

# ---- 2. the app's cache really lives in Redis ------------------------------------------------------------------
with app.app_context():
    cache.put("selfcheck:value", {"hello": "world"}, 30)
    in_redis = r.get("emp:selfcheck:value")
    check("cache.put() stores the value in Redis", in_redis is not None, "key emp:selfcheck:value, TTL %ss" % r.ttl("emp:selfcheck:value"))
    check("cache.get() reads it back", cache.get("selfcheck:value") == {"hello": "world"})
    cache.invalidate("selfcheck:")
    check("cache.invalidate() removes it from Redis", r.get("emp:selfcheck:value") is None)

    first, second = cache.hit("selfcheck-limit", 30), cache.hit("selfcheck-limit", 30)
    check("rate-limit counters count in Redis", (first, second) == (1, 2) and r.get("emp:rl:selfcheck-limit") == "2")
    cache.clear("selfcheck-limit")
    check("a counter can be cleared", r.get("emp:rl:selfcheck-limit") is None)

    cache.put("revoked:selfcheck", 1, 30)
    check("token blocklist entries live in Redis (logout survives a restart)", r.get("emp:revoked:selfcheck") is not None)
    cache.invalidate("revoked:selfcheck")

keys = [k for k in r.scan_iter("emp:*") if not k.startswith(("emp:selfcheck", "emp:rl:selfcheck"))]
cached = [k for k in keys if k.startswith(("emp:exams:", "emp:schedule:", "emp:rubrics:"))]
info(f"{len(cached)} application cache entries currently in Redis"
     + (" (e.g. " + ", ".join(sorted(cached)[:2]) + ")" if cached else
        " - open the Examinations page in the browser, then run this again to see the exam list cached"))

# ---- 3. Celery broker, worker, tasks ---------------------------------------------------------------------------
try:
    celery.connection_for_read().ensure_connection(max_retries=1, timeout=3)
    check("Celery broker reachable", True)
except Exception as e:  # noqa: BLE001
    check("Celery broker reachable", False, str(e).splitlines()[0], "check CELERY_BROKER_URL")
    finish()

inspector = celery.control.inspect(timeout=1.5)   # a local worker answers in milliseconds; this is only the wait for "nobody"
workers = inspector.ping() or {}
if not check("a Celery worker is running", bool(workers), ", ".join(workers) if workers else "",
             "start everything with:  python run.py"):
    info("skipping the job checks that need a worker")
    finish()

registered = {t for tasks in (inspector.registered() or {}).values() for t in tasks}
wanted = {"tasks.send_email", "tasks.daily_reminders", "tasks.monthly_report", "tasks.export_history"}
check("the worker knows all four EMP jobs", wanted <= registered, ", ".join(sorted(wanted - registered)) or "")

# An EMP job end to end: it travels API -> Redis broker -> worker, the worker builds the Flask app and reads the database
# (this used to fail with 'No module named app'), and the result comes back through the Redis result backend.
try:
    t0 = time.time()
    users = celery.send_task("tasks.health").get(timeout=40)
    check("a job sent through Redis is executed by the worker (Flask app + database) and its result comes back",
          isinstance(users, int), f"{time.time() - t0:.2f}s; the worker sees {users} user account(s)")
except Exception as e:  # noqa: BLE001
    check("a job sent through Redis is executed by the worker (Flask app + database) and its result comes back", False,
          str(e)[:120], "look at the worker output for the error")

if "--jobs" in sys.argv:
    t0 = time.time()
    reminders = celery.send_task("tasks.daily_reminders").get(timeout=90)
    check("daily reminder job ran on the worker", isinstance(reminders, int),
          f"{reminders} new reminder(s) for tomorrow's bookings, {time.time() - t0:.1f}s (re-running sends none again)")

# ---- 4. beat ---------------------------------------------------------------------------------------------------
ROOT = os.path.dirname(BACKEND)
schedule = glob.glob(os.path.join(ROOT, "instance", "celerybeat-schedule*")) + glob.glob(os.path.join(BACKEND, "celerybeat-schedule*"))
for name, entry in celery.conf.beat_schedule.items():
    info(f"beat schedule: {name} -> {entry['task']} at {entry['schedule']}  (timezone {celery.conf.timezone})")
if schedule:
    age = time.time() - max(os.path.getmtime(f) for f in schedule)
    info(f"beat has run here before (schedule file written {age / 60:.0f} min ago); it must be running at the scheduled time")
else:
    info("beat has not been started yet (no celerybeat-schedule file): start everything with  python run.py")

# ---- 5. e-mail -------------------------------------------------------------------------------------------------
import smtplib  # noqa: E402


def check_mail():
    """Connect (and log in, if a username is set) to the configured SMTP server. Sends nothing."""
    where = f"{Config.MAIL_HOST}:{Config.MAIL_PORT}"
    try:
        smtp = (smtplib.SMTP_SSL if Config.MAIL_USE_SSL else smtplib.SMTP)(Config.MAIL_HOST, Config.MAIL_PORT, timeout=10)
        with smtp as s:
            if Config.MAIL_USE_TLS and not Config.MAIL_USE_SSL:
                s.starttls()
            if Config.MAIL_USERNAME:
                s.ehlo()
                if "PLAIN" in s.esmtp_features.get("auth", ""):
                    s.esmtp_features["auth"] = "PLAIN"
                s.login(Config.MAIL_USERNAME, Config.MAIL_PASSWORD)
        return check("e-mail server accepts us", True, where + (f", signed in as {Config.MAIL_USERNAME}" if Config.MAIL_USERNAME else ""))
    except smtplib.SMTPAuthenticationError:
        return check("e-mail server accepts us", False, f"{where}: login refused for {Config.MAIL_USERNAME}",
                     "wrong or revoked app password: create a new one and put it in .env (MAIL_PASSWORD)")
    except Exception as e:
        return check("e-mail server accepts us", False, f"{where}: {str(e).splitlines()[0] if str(e) else type(e).__name__}",
                     "is the mail server running / MAIL_HOST and MAIL_PORT right? (mails are only logged until then)")


check_mail()

finish()
