#!/usr/bin/env python3
"""
Recreate (docker create, nothing is started) the model containers in the config so that
  - the model is published on 127.0.0.1:<backend_port> (the sleeper owns the public port),
  - Docker does not restart it on its own (--restart no; the sleeper starts it on demand).
Everything else is copied from the existing container (image, command, environment, mounts, GPU, ...).
The old container is kept stopped as <name>.pre-sleeper until you remove it. Run as root:
  python3 recreate_containers.py /etc/model-sleeper.json          (add --dry-run to only print)
"""
import json
import shlex
import subprocess
import sys


def sh(*a):
    return subprocess.run(a, capture_output=True, text=True)


def inspect(name):
    r = sh("docker", "inspect", name)
    return json.loads(r.stdout)[0] if r.returncode == 0 else None


def build(d, backend_port):
    c, h = d["Config"], d["HostConfig"]
    img = json.loads(sh("docker", "image", "inspect", c["Image"]).stdout)[0]["Config"]
    args = ["docker", "create", "--name", d["Name"].lstrip("/"), "--restart", "no"]
    if h.get("DeviceRequests"):
        args += ["--gpus", "all"]
    if h.get("IpcMode") and h["IpcMode"] not in ("private", "shareable", ""):
        args += ["--ipc", h["IpcMode"]]
    if h.get("ShmSize") and h["ShmSize"] != 67108864:
        args += ["--shm-size", str(h["ShmSize"])]
    for opt in h.get("SecurityOpt") or []:
        args += ["--security-opt", opt]
    for b in h.get("Binds") or []:
        args += ["-v", b]
    for e in c["Env"]:
        if e not in (img.get("Env") or []):
            args += ["-e", e]
    (port,) = [p.split("/")[0] for p in (h.get("PortBindings") or {})]
    args += ["-p", f"127.0.0.1:{backend_port}:{port}"]
    if c.get("Entrypoint") != img.get("Entrypoint") and c.get("Entrypoint"):
        args += ["--entrypoint", c["Entrypoint"][0]]
    args += [c["Image"], *(c.get("Cmd") or [])]
    return args


def main():
    cfg = json.load(open(sys.argv[1]))
    dry = "--dry-run" in sys.argv
    for m in cfg:
        name = m["container"]
        d = inspect(name)
        if d is None:
            print(f"!! {name}: no such container, skipped")
            continue
        bindings = d["HostConfig"].get("PortBindings") or {}
        if any(b["HostPort"] == str(m["backend_port"]) for v in bindings.values() for b in v):
            print(f"{name}: already on backend port {m['backend_port']}")
            continue
        args = build(d, m["backend_port"])
        print(f"{name}:\n  " + " ".join(shlex.quote(a) for a in args))
        if dry:
            continue
        sh("docker", "stop", "-t", "60", name)
        old = name + ".pre-sleeper"
        sh("docker", "rm", "-f", old)
        sh("docker", "rename", name, old)
        r = sh(*args)
        if r.returncode:
            print(f"!! {name}: {r.stderr.strip()} -- restoring the old container")
            sh("docker", "rename", old, name)
            continue
        print(f"{name}: recreated, not started: the sleeper wakes it on demand (old one kept as {old})")


if __name__ == "__main__":
    main()
