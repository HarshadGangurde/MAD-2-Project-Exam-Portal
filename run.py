#!/usr/bin/env python3
"""EMP - one command runs everything.

    python run.py                 first run: creates the virtual environment, installs the requirements, loads the demo
                                  data, then starts Redis (if installed), the API + UI, the Celery worker and Celery
                                  beat in this one terminal and checks that they work together. Ctrl+C stops it all.
    python run.py --demo          also show the one-click demo logins on the sign-in page
    python run.py --seed          wipe and reload the demo data first (courses, exams, slots, bookings, non-admin users)
    python run.py --no-seed       do not load demo data on a first run (start with just the admin)
    python run.py --no-redis      API + UI only (cache and jobs fall back to memory / run inline; no worker, no beat)
    python run.py --stop          stop a stack left running from an earlier run (also done automatically at start-up)
    python run.py --check         only run the Redis/Celery self-check against a running stack
    python run.py --verbose       show every request and the full Celery output

Needs only Python 3.10+ and an internet connection on the very first run (to install the packages).
Redis is found automatically (PATH, REDIS_SERVER, Memurai, ~/redis-win, ...). Without it everything except Redis
caching and the Celery worker/beat still works, and this script says so.
"""
import argparse
import hashlib
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from collections import deque
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
INSTANCE = ROOT / "instance"
VENV = ROOT / "venv"
IS_WIN = os.name == "nt"
VENV_PY = VENV / ("Scripts/python.exe" if IS_WIN else "bin/python")

PRINT_LOCK = threading.Lock()
COLORS = {"api": "36", "worker": "35", "beat": "33", "redis": "31", "run": "32"}
USE_COLOR = sys.stdout.isatty() and (os.environ.get("NO_COLOR") is None)
if IS_WIN and USE_COLOR:
    os.system("")  # switches the Windows console to ANSI colours


def say(msg="", tag="run"):
    prefix = f"\033[{COLORS.get(tag, '0')}m{tag:<6}\033[0m | " if USE_COLOR else f"{tag:<6} | "
    with PRINT_LOCK:
        print(prefix + msg if msg else "", flush=True)


def die(msg, code=1):
    say("ERROR: " + msg)
    sys.exit(code)


# ------------------------------------------------------------------------------------------------ arguments
def parse_args():
    p = argparse.ArgumentParser(description="Run the whole EMP stack with one command.", add_help=True)
    p.add_argument("--demo", action="store_true", help="show the one-click demo logins on the sign-in page")
    p.add_argument("--seed", action="store_true", help="wipe and reload the demo data before starting")
    p.add_argument("--no-seed", action="store_true", help="do not load demo data on a first run")
    p.add_argument("--no-redis", action="store_true", help="API + UI only: no Redis, worker or beat")
    p.add_argument("--no-check", action="store_true", help="skip the start-up self-check")
    p.add_argument("--stop", action="store_true", help="stop a stack left running and exit")
    p.add_argument("--check", action="store_true", help="only run the Redis/Celery self-check and exit")
    p.add_argument("--open", action="store_true", help="open the browser when ready")
    p.add_argument("--verbose", action="store_true", help="show every request and the full Celery output")
    p.add_argument("--port", type=int, default=int(os.environ.get("EMP_PORT", "5000")), help="API/UI port (default 5000)")
    return p.parse_args()


# ------------------------------------------------------------------------------------------------ first-run setup
def in_venv():
    return Path(sys.prefix).resolve() == VENV.resolve()


def bootstrap():
    """Make sure ./venv exists with the pinned requirements, then run this same script inside it."""
    if sys.version_info < (3, 10):
        die(f"Python 3.10 or newer is required (this is {sys.version.split()[0]}).")
    if not VENV_PY.exists():
        say("Creating the virtual environment (first run only) ...")
        subprocess.check_call([sys.executable, "-m", "venv", str(VENV)])
    req = ROOT / "requirements.txt"
    digest = hashlib.sha256(req.read_bytes()).hexdigest()
    marker = VENV / ".emp-requirements"
    if not marker.exists() or marker.read_text().strip() != digest:
        say("Installing the requirements (first run, or requirements.txt changed) - about a minute ...")
        rc = subprocess.call([str(VENV_PY), "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", str(req)])
        if rc != 0:
            die("pip could not install the requirements. Check your internet connection and run again.")
        marker.write_text(digest)
    child = subprocess.Popen([str(VENV_PY), str(Path(__file__).resolve()), *sys.argv[1:]])
    presses = 0
    while True:
        try:
            sys.exit(child.wait())
        except KeyboardInterrupt:       # the child got the same Ctrl+C and is shutting down: wait for it to finish
            presses += 1
            if presses >= 2:            # pressed again: stop waiting politely
                child.terminate()
                sys.exit(130)


# ------------------------------------------------------------------------------------------------ helpers
def port_open(port, host="127.0.0.1", timeout=0.6):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def http_ok(url, timeout=2):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def kill_tree(proc):
    """Stop a child and everything it started (on Windows the venv python is a launcher that spawns the real one)."""
    if proc.poll() is not None:
        return
    try:
        if IS_WIN:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:  # noqa: BLE001
        proc.kill()


def stop_stale(include_redis=False):
    """Stop processes of THIS project left over from an earlier run (matched by their command line). Returns the count."""
    root = str(ROOT)
    if IS_WIN:
        extra = " -or $_.CommandLine -like '*redis.log*'" if include_redis else ""
        # $PID is excluded too: this very PowerShell process has the same patterns in its own command line
        # (the command line must contain this project's folder, i.e. its venv or its scripts: other projects are never touched)
        ps = (
            "Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne %d -and $_.ProcessId -ne $PID -and "
            "$_.CommandLine -and $_.CommandLine.Contains('%s') -and ($_.CommandLine -like '*app.py*' -or "
            "$_.CommandLine -like '*celery_app.celery*'%s) } | ForEach-Object { $_.ProcessId }"
        ) % (os.getpid(), root.replace("'", "''"), extra)
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True).stdout
        pids = [int(x) for x in out.split() if x.isdigit()]
        for pid in pids:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        return len(pids)
    pattern = re.escape(root) + r".*(app\.py|celery_app\.celery" + (r"|redis\.log" if include_redis else "") + ")"
    return 1 if subprocess.run(["pkill", "-f", pattern], capture_output=True).returncode == 0 else 0


def find_redis():
    cands = [os.environ.get("REDIS_SERVER"), shutil.which("redis-server"), shutil.which("memurai")]
    if IS_WIN:
        cands += [Path.home() / "redis-win" / "redis-server.exe", r"C:\Program Files\Redis\redis-server.exe",
                  r"C:\Program Files\Memurai\memurai.exe", r"C:\Redis\redis-server.exe"]
    else:
        cands += ["/usr/bin/redis-server", "/usr/local/bin/redis-server", "/opt/homebrew/bin/redis-server"]
    for c in cands:
        if c and Path(c).exists():
            return str(c)
    return None


# ------------------------------------------------------------------------------------------------ child processes
class Service:
    """One child process whose output is relayed to this terminal with a name prefix."""

    def __init__(self, name, cmd, cwd, env, verbose, ready=None):
        self.name, self.cmd, self.cwd, self.env, self.verbose = name, cmd, cwd, env, verbose
        self.ready_re = re.compile(ready) if ready else None
        self.ready = threading.Event()
        self.tail = deque(maxlen=25)
        self.proc = None
        self.in_banner = name in ("worker", "beat") and not verbose

    def start(self):
        kw = {}
        if IS_WIN:
            kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        else:
            kw["start_new_session"] = True
        self.proc = subprocess.Popen(self.cmd, cwd=self.cwd, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace", bufsize=1, **kw)
        threading.Thread(target=self._relay, daemon=True).start()

    NOISE = re.compile(r"development server|Serving Flask app|Debug mode|Press CTRL\+C|^\s*$|"
                       r"\* Running on all|CPendingDeprecationWarning|warnings\.warn|broker_connection_retry|"
                       r"If you wish to retain|whether broker connection|you should set")
    OK_REQUEST = re.compile(r'"(GET|POST|PUT|DELETE|HEAD|OPTIONS) [^"]*" [23]\d\d ')
    CELERY_FIRST_LOG = re.compile(r"^\[\d{4}-\d\d-\d\d ")

    def _relay(self):
        for raw in self.proc.stdout:
            line = raw.rstrip()
            self.tail.append(line)
            if self.ready_re and self.ready_re.search(line):
                self.ready.set()
            if self.verbose:
                say(line, self.name)
                continue
            if self.in_banner:                       # the big Celery ASCII banner and config dump
                if not self.CELERY_FIRST_LOG.match(line):
                    continue
                self.in_banner = False
            if self.NOISE.search(line) or self.OK_REQUEST.search(line):
                continue
            say(line, self.name)


def stop_all(services, redis_proc):
    for s in reversed(services):
        if s.proc:
            kill_tree(s.proc)
    if redis_proc is not None:
        kill_tree(redis_proc)


# ------------------------------------------------------------------------------------------------ main
def seed_demo(reason):
    say(f"Loading the demo data ({reason}) ...")
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONWARNINGS="ignore")
    r = subprocess.run([sys.executable, str(ROOT / "seed_data.py"), "--yes"], cwd=str(ROOT), env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        die("loading the demo data failed:\n" + (r.stdout + r.stderr)[-1500:])
    summary = [ln.strip("- ").strip() for ln in r.stdout.splitlines() if re.match(r"\s*- \d+ ", ln)]
    say("Demo data: " + ", ".join(summary))


def demo_accounts_present():
    import sqlite3
    try:
        con = sqlite3.connect(str(ROOT / "emp.db"))
        n = con.execute("SELECT COUNT(*) FROM users WHERE email='rajesh@university.edu'").fetchone()[0]
        con.close()
        return n > 0
    except Exception:  # noqa: BLE001
        return False


def main():
    args = parse_args()
    if not in_venv():
        bootstrap()
    os.chdir(ROOT)
    sys.path.insert(0, str(BACKEND))
    from config import Config

    if args.stop:
        n = stop_stale(include_redis=True)
        say(f"Stopped {n} process(es) of an earlier run." if n else "Nothing of this project was running.")
        return 0
    if args.check:
        return subprocess.call([sys.executable, "-W", "ignore", str(ROOT / "check_services.py"), "--jobs"])

    stale = stop_stale()
    if stale:
        say(f"Stopped {stale} process(es) left over from an earlier run.")
        time.sleep(1.5)
    if port_open(args.port):
        die(f"port {args.port} is already used by another program. Close it, or run:  python run.py --port {args.port + 1}")

    # ---- demo data
    if args.seed:
        seed_demo("--seed")
    elif not (ROOT / "emp.db").exists() and not args.no_seed:
        seed_demo("first run; use --no-seed to start empty")

    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8", PYTHONWARNINGS="ignore", EMP_PORT=str(args.port))
    if args.demo:
        env["EMP_DEMO_LOGIN"] = "1"

    # ---- Redis
    redis_proc, redis_up = None, False
    ru = urlparse(Config.REDIS_URL)
    r_host, r_port = ru.hostname or "localhost", ru.port or 6379
    local_redis = r_host in ("localhost", "127.0.0.1", "::1")
    if args.no_redis:
        say("--no-redis: running without Redis (in-memory cache, jobs run inline, no worker/beat).", "redis")
    elif port_open(r_port, "127.0.0.1" if local_redis else r_host):
        redis_up = True
        say(f"Redis is already running on {r_host}:{r_port} - using it.", "redis")
    elif local_redis:
        exe = find_redis()
        if exe:
            INSTANCE.mkdir(exist_ok=True)
            log = INSTANCE / "redis.log"
            say(f"Starting Redis: {exe}", "redis")
            kw = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW} if IS_WIN else {"start_new_session": True}
            redis_proc = subprocess.Popen([exe, "--bind", "127.0.0.1", "--port", str(r_port), "--save", "", "--appendonly", "no",
                                           "--logfile", str(log)], cwd=str(Path(exe).parent), stdin=subprocess.DEVNULL,
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)
            for _ in range(40):
                if port_open(r_port) or redis_proc.poll() is not None:
                    break
                time.sleep(0.25)
            redis_up = port_open(r_port)
            if not redis_up:
                say(f"Redis did not start (see {log}). Continuing without it.", "redis")
                redis_proc = None
        else:
            say("No Redis found, continuing WITHOUT it: the app works (in-memory cache, jobs run inline) but the Redis cache,", "redis")
            say("the Celery worker and beat are off. Install Redis (Windows: Memurai or a redis-server.exe in ~/redis-win,", "redis")
            say("Linux: apt install redis-server, macOS: brew install redis) or set REDIS_SERVER=path, then run again.", "redis")

    # ---- services
    py = sys.executable
    services = [Service("api", [py, str(BACKEND / "app.py")], str(BACKEND), env, args.verbose)]
    if redis_up:
        INSTANCE.mkdir(exist_ok=True)
        celery = [py, "-m", "celery", "-A", "celery_app.celery"]
        # --without-mingle/--without-gossip: cluster features of no use with one worker; they only delay start-up by seconds
        services.append(Service("worker", celery + ["worker", "-l", "info", "-P", "solo", "--without-mingle", "--without-gossip"],
                                str(BACKEND), env, args.verbose, ready=r" ready\."))
        services.append(Service("beat", celery + ["beat", "-l", "info", "-s", str(INSTANCE / "celerybeat-schedule")],
                                str(BACKEND), env, args.verbose, ready=r"beat: Starting"))

    def shutdown(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, shutdown)
    if IS_WIN:
        signal.signal(signal.SIGBREAK, shutdown)

    try:
        for s in services:
            s.start()
        url = f"http://localhost:{args.port}"
        api_up, deadline = False, time.time() + 60
        while time.time() < deadline:
            if http_ok(f"http://127.0.0.1:{args.port}/"):
                api_up = True
                break
            if services[0].proc.poll() is not None:     # crashed while starting
                break
            time.sleep(0.3)
        if not api_up:
            say("The API did not come up. Its last output:")
            for ln in services[0].tail:
                say(ln, "api")
            return 1                                    # the finally-block below stops everything that was started
        say(f"API + UI ready at {url}", "api")
        for s in services[1:]:
            if not s.ready.wait(timeout=60):
                say(f"{s.name} did not report ready within 60 s (see its output above).", s.name)

        # ---- self-check
        checked = ""
        if redis_up and not args.no_check:
            r = subprocess.run([py, "-W", "ignore", str(ROOT / "check_services.py")], cwd=str(ROOT), env=env,
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            last = [ln for ln in r.stdout.splitlines() if "CHECKS" in ln]
            fails = [ln.strip() for ln in r.stdout.splitlines() if ln.startswith("FAIL")]
            checked = last[-1].strip() if last else "self-check produced no result"
            say("Self-check: " + checked)
            for f in fails:
                say("   " + f)
            if fails:
                say("   run  python check_services.py  for the details")

        # ---- banner
        with PRINT_LOCK:
            print()
            print("=" * 72)
            print(f"  EMP is running:  {url}")
            print("=" * 72)
            print("  Sign in: choose your role first, then email and password")
            print(f"    Admin     {Config.ADMIN_EMAIL}  /  {Config.ADMIN_PASSWORD}   (ADMIN_PASSWORD env var changes it)")
            if demo_accounts_present():
                print("    Examiner  rajesh@university.edu  /  examiner123")
                print("    Student   rahul.verma@student.edu  /  student123")
            else:
                print("    Examiners are created by the admin; students register themselves.")
            parts = ["API+UI"] + (["Redis cache", "Celery worker", "Celery beat"] if redis_up else [])
            print("  Running:  " + ", ".join(parts) + ("" if redis_up else "   (Redis/Celery OFF)"))
            print("  Press Ctrl+C to stop everything.")
            print("=" * 72 + "\n", flush=True)
        if args.open:
            import webbrowser
            webbrowser.open(url)

        # ---- supervise
        while True:
            time.sleep(0.5)
            dead = [s for s in services if s.proc.poll() is not None]
            if redis_proc is not None and redis_proc.poll() is not None:
                say("Redis stopped unexpectedly (reason: instance/redis.log). Shutting down.", "redis")
                dead = dead or services
            if dead:
                for s in dead:
                    say(f"{s.name} stopped unexpectedly (exit code {s.proc.returncode}). Last output:", s.name)
                    for ln in list(s.tail)[-8:]:
                        say("   " + ln, s.name)
                return 1
    except KeyboardInterrupt:
        say("Stopping ...")
        return 0
    finally:
        stop_all(services, redis_proc)
        say("Stopped.")


if __name__ == "__main__":
    sys.exit(main())
