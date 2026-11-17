"""Redis helpers: TTL cache, rate-limit counters and a broker health check.

If Redis is unreachable everything falls back to in-process dicts so the app keeps working. After a failure Redis
is skipped for 30 s instead of paying the connection timeout on every call."""
import json
import time

import redis
from flask import current_app

_client = None
_local = {}            # key -> (expires_at, value)
_down_until = 0.0      # Redis is skipped until this time after an error
_broker = (0.0, False)  # (checked_at, reachable)


def _r():
    global _client
    if _client is None:
        _client = redis.Redis.from_url(current_app.config["REDIS_URL"], socket_connect_timeout=0.3,
                                       socket_timeout=0.3, decode_responses=True)
    return _client


def _usable():
    return time.time() >= _down_until


def _failed():
    global _down_until
    _down_until = time.time() + 30


def get(key):
    if _usable():
        try:
            v = _r().get("emp:" + key)
            return json.loads(v) if v else None
        except redis.RedisError:
            _failed()
    item = _local.get(key)
    return item[1] if item and item[0] > time.time() else None


def put(key, value, ttl=None):
    ttl = ttl or current_app.config["CACHE_TTL"]
    if _usable():
        try:
            _r().setex("emp:" + key, ttl, json.dumps(value))
            return
        except redis.RedisError:
            _failed()
    _local[key] = (time.time() + ttl, value)


def invalidate(prefix):
    """Delete every cached key that starts with prefix."""
    if _usable():
        try:
            for k in _r().scan_iter("emp:" + prefix + "*"):
                _r().delete(k)
        except redis.RedisError:
            _failed()
    for k in [k for k in _local if k.startswith(prefix)]:
        _local.pop(k, None)


def hit(key, window):
    """Count one event for key in a fixed window of `window` seconds and return the count so far."""
    if _usable():
        try:
            k = "emp:rl:" + key
            n = _r().incr(k)
            if n == 1:
                _r().expire(k, window)
            return n
        except redis.RedisError:
            _failed()
    now = time.time()
    expires, n = _local.get("rl:" + key, (0, 0))
    if expires <= now:
        expires, n = now + window, 0
    _local["rl:" + key] = (expires, n + 1)
    return n + 1


def count(key):
    """Events recorded for key by hit() in its current window, without counting a new one."""
    if _usable():
        try:
            return int(_r().get("emp:rl:" + key) or 0)
        except redis.RedisError:
            _failed()
    expires, n = _local.get("rl:" + key, (0, 0))
    return n if expires > time.time() else 0


def clear(key):
    """Forget the counter for key (e.g. after a successful login)."""
    if _usable():
        try:
            _r().delete("emp:rl:" + key)
        except redis.RedisError:
            _failed()
    _local.pop("rl:" + key, None)


def broker_available():
    """True if the Celery broker answers within 0.3 s (result cached 15 s). Without this check, publishing a task
    to a dead broker blocks the request for minutes while kombu retries."""
    global _broker
    now = time.time()
    if now - _broker[0] < 15:
        return _broker[1]
    url = current_app.config["CELERY_BROKER_URL"]
    ok = True
    if url.startswith("redis"):
        try:
            redis.Redis.from_url(url, socket_connect_timeout=0.3, socket_timeout=0.3).ping()
        except redis.RedisError:
            ok = False
    _broker = (now, ok)
    return ok
