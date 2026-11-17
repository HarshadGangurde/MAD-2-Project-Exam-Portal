import os
import secrets

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _load_dotenv():
    """Read KEY=VALUE lines from the project's .env file (if any) into the environment, without overriding real
    environment variables. Keeps mail passwords out of the command line and out of git (.env is git-ignored)."""
    try:
        with open(os.path.join(BASE_DIR, ".env"), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except OSError:
        pass


_load_dotenv()


def _jwt_secret():
    """JWT_SECRET_KEY from the environment, else a random 32-byte secret generated once and kept in instance/.
    Persisting it means restarts do not log everyone out, and no weak secret is ever committed."""
    env = os.getenv("JWT_SECRET_KEY")
    if env:
        return env
    path = os.path.join(BASE_DIR, "instance", "secret.key")
    try:
        with open(path) as f:
            value = f.read().strip()
            if len(value) >= 32:
                return value
    except OSError:
        pass
    value = secrets.token_hex(32)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # owner-only on POSIX
        with os.fdopen(fd, "w") as f:
            f.write(value)
    except OSError:
        pass  # read-only folder: fall back to a per-process secret
    return value


class Config:
    SQLALCHEMY_DATABASE_URI = "sqlite:///" + os.path.join(BASE_DIR, "emp.db")
    JWT_SECRET_KEY = _jwt_secret()
    JWT_ACCESS_TOKEN_EXPIRES = 60 * 60 * 8
    # 127.0.0.1, not "localhost": on Windows "localhost" tries IPv6 first and every new connection then waits ~2 s
    # (measured: 2.04 s vs 0.00 s), which slowed the worker, the self-check and every reconnect.
    REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://127.0.0.1:6379/1")
    CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://127.0.0.1:6379/2")
    CACHE_TTL = 120  # seconds

    # Celery beat schedule: the reminder runs at REMINDER_HOUR:REMINDER_MINUTE in TIMEZONE (an IANA name).
    TIMEZONE = os.getenv("TIMEZONE", "Asia/Kolkata")
    REMINDER_HOUR = int(os.getenv("REMINDER_HOUR", "8"))
    REMINDER_MINUTE = int(os.getenv("REMINDER_MINUTE", "0"))

    # The single admin is created on first start. Set both env vars to choose your own credentials.
    # With EMP_ENV=production the app refuses to start while the default password is still in use.
    ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@gmail.com")
    # Real mailbox that receives the admin's e-mails (monthly report). The admin LOGIN can stay admin@gmail.com.
    ADMIN_NOTIFY_EMAIL = os.getenv("ADMIN_NOTIFY_EMAIL") or ADMIN_EMAIL
    DEFAULT_ADMIN_PASSWORD = "admin@123"
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", DEFAULT_ADMIN_PASSWORD)
    ENV = os.getenv("EMP_ENV", "development")

    # Login-page shortcut buttons for the seeded demo accounts: off unless EMP_DEMO_LOGIN=1.
    DEMO_LOGIN = os.getenv("EMP_DEMO_LOGIN", "0") == "1"
    DEMO_ACCOUNTS = [  # the accounts created by seed_data.py; only ever sent to the browser when DEMO_LOGIN is on
        dict(role="Admin", email=ADMIN_EMAIL, password=ADMIN_PASSWORD),
        dict(role="Examiner", email="rajesh@university.edu", password="examiner123"),
        dict(role="Student", email="rahul.verma@student.edu", password="student123"),
    ]
    # Number of reverse proxies in front of the app (X-Forwarded-For is trusted for that many hops); 0 = none.
    TRUSTED_PROXIES = int(os.getenv("TRUSTED_PROXIES", "0"))
    # Append-only log of sensitive actions (status changes, publication, evaluations, assignments).
    AUDIT_LOG = os.path.join(BASE_DIR, "instance", "audit.log")

    # Mail goes to a local SMTP sink (e.g. MailHog on 1025); failures are logged, never fatal.
    MAIL_HOST = os.getenv("MAIL_HOST", "127.0.0.1")   # not "localhost": on Windows that tries IPv6 first, doubling the wait when no server runs
    MAIL_PORT = int(os.getenv("MAIL_PORT", "1025"))
    MAIL_FROM = os.getenv("MAIL_FROM") or os.getenv("MAIL_USERNAME") or "emp@example.com"   # Gmail only sends as the logged-in account
    MAIL_FROM_NAME = os.getenv("MAIL_FROM_NAME", "Examination Portal")
    # Real SMTP server (e.g. Gmail: smtp.gmail.com, 587, MAIL_USE_TLS=1, an app password). Empty username = no login (MailHog/Mailpit).
    MAIL_USERNAME = os.getenv("MAIL_USERNAME", "")
    MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")
    MAIL_USE_TLS = os.getenv("MAIL_USE_TLS", "0") == "1"    # STARTTLS (usually port 587)
    MAIL_USE_SSL = os.getenv("MAIL_USE_SSL", "0") == "1"    # implicit TLS (usually port 465)
    GCHAT_WEBHOOK = os.getenv("GCHAT_WEBHOOK", "")  # optional Google Chat webhook URL
    EXPORT_DIR = os.path.join(BASE_DIR, "exports")
