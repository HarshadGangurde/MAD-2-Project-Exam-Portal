from celery import Celery
from celery.schedules import crontab

from config import Config

celery = Celery("emp", broker=Config.CELERY_BROKER_URL, backend=Config.CELERY_RESULT_BACKEND,
                include=["tasks"])
celery.conf.timezone = Config.TIMEZONE
celery.conf.broker_connection_retry_on_startup = True   # keep retrying while Redis is still starting (and silences a Celery 6 warning)
# Fail fast if the broker is down instead of retrying for minutes (the API also checks the broker first).
celery.conf.broker_connection_timeout = 1
celery.conf.broker_transport_options = {"socket_connect_timeout": 1, "socket_timeout": 2, "max_retries": 1}
celery.conf.task_publish_retry_policy = {"max_retries": 1, "interval_start": 0, "interval_step": 0.2,
                                         "interval_max": 0.5}
celery.conf.beat_schedule = {
    "daily-exam-reminder": {"task": "tasks.daily_reminders",
                            "schedule": crontab(hour=Config.REMINDER_HOUR, minute=Config.REMINDER_MINUTE)},
    "monthly-exam-report": {"task": "tasks.monthly_report", "schedule": crontab(day_of_month=1, hour=7, minute=0)},
}
