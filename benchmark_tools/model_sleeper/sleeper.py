#!/usr/bin/env python3
"""
model-sleeper: sleep model containers when idle, wake them on the first request.

For every model in the config it listens on the model's public port, starts the Docker container
when a connection arrives (holding the connection until the model answers), pipes the bytes
through, and stops the container after `idle_seconds` without traffic, which frees its memory
(on a unified-memory machine, RAM and GPU memory). Works for any HTTP model server (vLLM,
llama-server, ...), including streamed answers, because it forwards plain TCP.

Status and manual control (never wakes anything by itself). Loopback by default; set SLEEPER_CONTROL_HOST=0.0.0.0
and SLEEPER_TOKEN=<secret> to let another machine (the web app's backend) read the state and wake a model:
  curl http://127.0.0.1:8099/status
  curl -X POST http://127.0.0.1:8099/wake/qwen36
  curl -X POST http://127.0.0.1:8099/sleep/qwen36

Config: JSON list in /etc/model-sleeper.json (or $SLEEPER_CONFIG), see config.example.json.
No dependencies beyond Python 3.10+ and the docker CLI.
"""

import asyncio
import json
import logging
import os
import signal
import time

CONFIG = os.environ.get("SLEEPER_CONFIG", "/etc/model-sleeper.json")
CONTROL = (os.environ.get("SLEEPER_CONTROL_HOST", "127.0.0.1"), int(os.environ.get("SLEEPER_CONTROL_PORT", "8099")))
TOKEN = os.environ.get("SLEEPER_TOKEN", "")  # when set, the control API needs "Authorization: Bearer <token>"
log = logging.getLogger("sleeper")


class Model:
    def __init__(self, c):
        self.name = c["name"]
        self.container = c["container"]
        self.listen = (c.get("listen_host", "0.0.0.0"), c["listen_port"])
        self.backend = ("127.0.0.1", c["backend_port"])
        self.idle = c.get("idle_seconds", 900)  # longer than any single request
        self.start_timeout = c.get("start_timeout", 600)
        self.ready_path = c.get("ready_path")  # GET must return 200 when the model is loaded
        self.lock = asyncio.Lock()  # one wake-up at a time; other connections wait behind it
        self.last = time.monotonic()  # last traffic (or start) seen
        self.was_running = False
        self.wakes = 0
        self.sleeps = 0
        self.connections = 0
        self.last_wake = None
        self.wake_started = None  # monotonic time the current wake-up began
        self.last_sleep = None

    def touch(self):
        self.last = time.monotonic()


async def docker(*args, timeout=180):
    p = await asyncio.create_subprocess_exec(
        "docker", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await asyncio.wait_for(p.communicate(), timeout)
    return p.returncode, out.decode().strip(), err.decode().strip()


async def running(m):
    rc, out, _ = await docker("inspect", "-f", "{{.State.Running}}", m.container)
    return rc == 0 and out == "true"


async def ready(m):
    try:
        r, w = await asyncio.wait_for(asyncio.open_connection(*m.backend), 3)
    except (OSError, asyncio.TimeoutError):
        return False
    try:
        if not m.ready_path:
            return True
        w.write(f"GET {m.ready_path} HTTP/1.0\r\nHost: sleeper\r\n\r\n".encode())
        await w.drain()
        line = await asyncio.wait_for(r.readline(), 5)
        return b" 200" in line
    except (OSError, asyncio.TimeoutError):
        return False
    finally:
        w.close()


async def wake(m):
    """True once the model answers. Starts the container if it is stopped."""
    async with m.lock:
        if await running(m) and await ready(m):
            return True
        if not await running(m):
            log.info("waking %s (%s)", m.name, m.container)
            rc, _, err = await docker("start", m.container)
            if rc:
                log.error("cannot start %s: %s", m.container, err)
                return False
            m.wakes += 1
            m.last_wake = time.time()
            m.wake_started = time.monotonic()
        started = time.monotonic()
        while time.monotonic() - started < m.start_timeout:
            if await ready(m):
                m.touch()
                m.wake_started = None
                log.info("%s is ready after %.0fs", m.name, time.monotonic() - started)
                return True
            if not await running(m):
                log.error("%s exited while starting", m.container)
                return False
            await asyncio.sleep(2)
        log.error("%s did not become ready within %ss", m.name, m.start_timeout)
        return False


async def sleep(m, reason):
    if await running(m):
        log.info("sleeping %s: %s", m.name, reason)
        await docker("stop", "-t", "60", m.container, timeout=240)
        m.sleeps += 1
        m.last_sleep = time.time()
    m.was_running = False


async def pipe(reader, writer, m):
    try:
        while data := await reader.read(65536):
            m.touch()
            writer.write(data)
            await writer.drain()
    except (OSError, asyncio.CancelledError):
        pass
    finally:
        writer.close()


async def handle(m, cr, cw):
    m.connections += 1
    m.touch()
    try:
        if not await wake(m):
            cw.write(b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            return
        br, bw = await asyncio.open_connection(*m.backend)
        await asyncio.gather(pipe(cr, bw, m), pipe(br, cw, m))
    except OSError as exc:
        log.warning("%s: %s", m.name, exc)
    finally:
        m.connections -= 1
        m.touch()
        cw.close()


async def reaper(models):
    while True:
        await asyncio.sleep(15)
        for m in models:
            try:
                now_running = await running(m)
                if now_running and not m.was_running:
                    m.touch()  # started by someone else (or just now): a full idle window from here
                m.was_running = now_running
                if now_running and not m.lock.locked() and time.monotonic() - m.last > m.idle:
                    await sleep(m, f"idle for {m.idle}s")
            except Exception:
                log.exception("reaper %s", m.name)


async def status(models):
    states = {}
    for m in models:
        if not await running(m):
            states[m.name] = "asleep"
        else:
            states[m.name] = "ready" if await ready(m) else "starting"
    return {
        m.name: {
            "container": m.container,
            "listen_port": m.listen[1],
            "state": states[m.name],  # asleep (stopped, memory free) | starting (loading) | ready
            "starting_seconds": round(time.monotonic() - m.wake_started) if m.wake_started and states[m.name] == "starting" else None,
            "running": states[m.name] != "asleep",
            "idle_seconds": m.idle,
            "seconds_until_sleep": max(0, round(m.idle - (time.monotonic() - m.last))) if states[m.name] == "ready" else None,
            "open_connections": m.connections,
            "wakes": m.wakes,
            "sleeps": m.sleeps,
            "last_wake": m.last_wake,
            "last_sleep": m.last_sleep,
        }
        for m in models
    }


async def control(models, reader, writer):
    try:
        line = (await asyncio.wait_for(reader.readline(), 5)).decode().split()
        authorised = not TOKEN
        while (header := (await reader.readline()).strip()):
            if header.lower().startswith(b"authorization:") and header.split(b":", 1)[1].strip() == f"Bearer {TOKEN}".encode():
                authorised = True
        method, path = (line + ["", ""])[:2]
        body, code = {"error": "not found"}, 404
        by_name = {m.name: m for m in models}
        parts = path.strip("/").split("/")
        if not authorised:
            body, code = {"error": "unauthorised"}, 401
        elif method == "GET" and parts == ["status"]:
            body, code = await status(models), 200
        elif method == "POST" and len(parts) == 2 and parts[1] in by_name and parts[0] in ("wake", "sleep"):
            m = by_name[parts[1]]
            if parts[0] == "wake":
                body, code = {"ready": await wake(m)}, 200
            else:
                await sleep(m, "asked to")
                body, code = {"sleeping": True}, 200
        data = json.dumps(body, indent=1).encode()
        writer.write(
            f"HTTP/1.1 {code} OK\r\nContent-Type: application/json\r\nContent-Length: {len(data)}\r\n"
            f"Connection: close\r\n\r\n".encode() + data
        )
        await writer.drain()
    except (OSError, asyncio.TimeoutError):
        pass
    finally:
        writer.close()


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    models = [Model(c) for c in json.load(open(CONFIG))]
    servers = []
    for m in models:
        servers.append(await asyncio.start_server(lambda r, w, m=m: handle(m, r, w), *m.listen))
        log.info("%s: %s:%d -> container %s (port %d), sleeps after %ds idle",
                 m.name, m.listen[0], m.listen[1], m.container, m.backend[1], m.idle)
    servers.append(await asyncio.start_server(lambda r, w: control(models, r, w), *CONTROL))
    log.info("control API on %s:%d (%s)", *CONTROL, "token required" if TOKEN else "no token")
    asyncio.create_task(reaper(models))
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(sig, stop.set)
    await stop.wait()
    for s in servers:
        s.close()


if __name__ == "__main__":
    asyncio.run(main())
