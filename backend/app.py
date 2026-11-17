import logging
import os

from flask import Flask, abort, jsonify, render_template, request, send_from_directory
from flask_jwt_extended import JWTManager
from sqlalchemy import text
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

import cache
from config import Config, BASE_DIR
from models import db, User


def setup_audit_log(path):
    """One INFO line per sensitive action in instance/audit.log (logger 'emp.audit'); never raises."""
    log = logging.getLogger("emp.audit")
    log.setLevel(logging.INFO)
    log.propagate = False
    for h in list(log.handlers):  # create_app() may run more than once (tests): keep exactly one handler
        log.removeHandler(h)
        h.close()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        h = logging.FileHandler(path, encoding="utf-8")
    except OSError:
        h = logging.NullHandler()
    h.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    log.addHandler(h)


def create_app():
    app = Flask(__name__, template_folder=os.path.join(BASE_DIR, "templates"),
                static_folder=os.path.join(BASE_DIR, "static"))
    app.config.from_object(Config)
    if app.config["ENV"] == "production" and app.config["ADMIN_PASSWORD"] == app.config["DEFAULT_ADMIN_PASSWORD"]:
        raise RuntimeError("EMP_ENV=production: set the ADMIN_PASSWORD environment variable to a non-default value.")
    if app.config["TRUSTED_PROXIES"] > 0:  # request.remote_addr (rate limits) then comes from X-Forwarded-For
        n = app.config["TRUSTED_PROXIES"]
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=n, x_proto=n, x_host=n)
    setup_audit_log(app.config["AUDIT_LOG"])
    jwt = JWTManager(app)
    db.init_app(app)

    from api import api, ApiError, error_response
    app.register_blueprint(api, url_prefix="/api")
    app.register_error_handler(ApiError, error_response)

    @jwt.token_in_blocklist_loader
    def is_revoked(jwt_header, jwt_payload):  # tokens revoked by /auth/logout (Redis, so it survives restarts)
        return cache.get("revoked:" + jwt_payload["jti"]) is not None

    @app.errorhandler(Exception)
    def unexpected(e):
        """API routes always answer with JSON, never an HTML traceback page."""
        is_api = request.path.startswith("/api/")
        if isinstance(e, HTTPException):
            return (jsonify(error=e.description), e.code) if is_api else e
        app.logger.exception("Unhandled error on %s %s", request.method, request.path)
        db.session.rollback()
        if not is_api:
            return "Internal Server Error", 500
        if isinstance(e, (ValueError, TypeError, KeyError, AttributeError)):
            return jsonify(error="Invalid input"), 400
        return jsonify(error="Internal server error"), 500

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        # 'unsafe-eval' is required: Vue's CDN build compiles the in-page templates at runtime (new Function).
        # Removing it needs a build step with precompiled templates, which this project deliberately does not have.
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; form-action 'self'; "
            "script-src 'self' 'unsafe-eval' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "font-src 'self' https://cdn.jsdelivr.net; "
            "img-src 'self' data:; "
            "connect-src 'self';"
        )
        return resp

    @app.route("/sw.js")
    def service_worker():  # served from the root so its scope covers the whole app
        return send_from_directory(app.static_folder, "sw.js", mimetype="application/javascript")

    @app.route("/", defaults={"path": ""})
    @app.route("/<path:path>")
    def index(path):  # single entry point; Vue router handles the rest
        if path.startswith("api/"):
            abort(404, description="Unknown API endpoint")
        demo = app.config["DEMO_ACCOUNTS"] if app.config["DEMO_LOGIN"] else None
        return render_template("index.html", demo_accounts=demo)

    with app.app_context():
        db.create_all()
        ensure_indexes(app)
        seed_admin(app)
        admin = User.query.filter_by(role="admin").first()
        if admin and admin.check_password(app.config["DEFAULT_ADMIN_PASSWORD"]):
            app.logger.warning("The admin account still uses the default password. Change it (Profile) or set "
                               "ADMIN_PASSWORD before real use.")
    return app


def ensure_indexes(app):
    """create_all() never adds indexes to tables that already exist, so create them here (idempotent): the booking
    rule (one active booking per student and exam) and indexes on the columns the API filters and joins on."""
    try:
        db.session.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_active_booking ON bookings (student_id, exam_id) "
            "WHERE status != 'Cancelled'"))
        for name, table, cols in (("ix_bookings_slot", "bookings", "slot_id"), ("ix_bookings_exam", "bookings", "exam_id"),
                                  ("ix_slots_exam", "slots", "exam_id"), ("ix_slots_examiner_date", "slots", "examiner_id, date"),
                                  ("ix_rubrics_exam", "rubrics", "exam_id"), ("ix_evals_examiner", "evaluations", "examiner_id"),
                                  ("ix_evals_student", "evaluations", "student_id")):
            db.session.execute(text(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({cols})"))
        db.session.commit()
    except Exception as e:  # existing data already breaks the rule: keep running, the API check still applies
        db.session.rollback()
        app.logger.warning("Could not create uq_active_booking: %s", e)


def seed_admin(app):
    """Exactly one admin, created programmatically."""
    if not User.query.filter_by(role="admin").first():
        admin = User(email=app.config["ADMIN_EMAIL"], name="Administrator", role="admin")
        admin.set_password(app.config["ADMIN_PASSWORD"])
        db.session.add(admin)
        db.session.commit()


if __name__ == "__main__":
    # Debugger/reloader only when explicitly requested: FLASK_DEBUG=1. Port: EMP_PORT (default 5000).
    create_app().run(debug=os.getenv("FLASK_DEBUG") == "1", port=int(os.getenv("EMP_PORT", "5000")))
