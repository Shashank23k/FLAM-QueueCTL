#!/usr/bin/env python3
# queuectl.py
# A minimal production-grade background job queue with CLI, using Python + SQLite.

from __future__ import annotations
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from multiprocessing import Process, Event
from typing import Optional, List

import typer
from rich import box
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.pretty import pprint

app = typer.Typer(add_completion=False, help="queuectl - background job queue with workers, retries, and DLQ")
worker_app = typer.Typer(help="Manage workers")
dlq_app = typer.Typer(help="Dead Letter Queue")
config_app = typer.Typer(help="Configuration")

app.add_typer(worker_app, name="worker")
app.add_typer(dlq_app, name="dlq")
app.add_typer(config_app, name="config")

console = Console()

DB_PATH = os.environ.get("QUEUECTL_DB", "queue.db")
HEARTBEAT_SECS = 2
WORKER_STALE_AFTER_SECS = 10
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_BASE = 2
DEFAULT_POLL_IDLE_SECS = 0.5
DEFAULT_TIMEOUT_SECS = None

def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()

def parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts)

@contextmanager
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()

def init_db():
    with db() as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=30000;")
        conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs(
            id TEXT PRIMARY KEY,
            command TEXT NOT NULL,
            state TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            max_retries INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            available_at TEXT NOT NULL,
            priority INTEGER NOT NULL DEFAULT 0,
            run_timeout INTEGER,
            worker_id TEXT,
            started_at TEXT,
            finished_at TEXT,
            last_error TEXT,
            output TEXT
        );
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS kv(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS workers(
            id TEXT PRIMARY KEY,
            pid INTEGER NOT NULL,
            started_at TEXT NOT NULL,
            heartbeat_at TEXT NOT NULL,
            status TEXT NOT NULL
        );
        """)
        ensure_default_config(conn, "max_retries_default", str(DEFAULT_MAX_RETRIES))
        ensure_default_config(conn, "backoff_base", str(DEFAULT_BACKOFF_BASE))
        ensure_default_config(conn, "poll_idle_secs", str(DEFAULT_POLL_IDLE_SECS))

def ensure_default_config(conn, key, value):
    if conn.execute("SELECT 1 FROM kv WHERE key=?", (key,)).fetchone() is None:
        conn.execute("INSERT INTO kv(key,value) VALUES(?,?)", (key, value))

def get_config(conn, key, default=None):
    row = conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default

def set_config(conn, key, value):
    conn.execute("""
    INSERT INTO kv(key,value) VALUES(?,?)
    ON CONFLICT(key) DO UPDATE SET value=excluded.value
    """, (key, value))

def enqueue_job(job):
    init_db()
    job = dict(job)
    job.setdefault("id", str(uuid.uuid4()))
    job.setdefault("state", "pending")
    job.setdefault("attempts", 0)
    job.setdefault("max_retries", DEFAULT_MAX_RETRIES)
    job.setdefault("created_at", now_utc())
    job.setdefault("updated_at", job["created_at"])
    available_at = job.get("available_at", job["created_at"])
    priority = int(job.get("priority", 0))
    run_timeout = job.get("timeout") or job.get("run_timeout") or None

    with db() as conn:
        conn.execute("BEGIN IMMEDIATE;")
        conn.execute("""
            INSERT INTO jobs(id, command, state, attempts, max_retries, created_at, updated_at, available_at, priority, run_timeout)
            VALUES(?,?,?,?,?,?,?,?,?,?)
        """, (
            job["id"], job["command"], job["state"], int(job["attempts"]),
            int(job["max_retries"]), job["created_at"], job["updated_at"],
            available_at, priority, run_timeout
        ))
        conn.execute("COMMIT;")
    return job["id"]

def compute_backoff_delay(base, attempts):
    return int(pow(base, attempts))

def atomic_claim_next_job(conn, worker_id):
    now = now_utc()
    conn.execute("BEGIN IMMEDIATE;")
    updated = conn.execute("""
        UPDATE jobs
        SET state='processing', worker_id=?, started_at=?, updated_at=?
        WHERE id = (
            SELECT id FROM jobs
            WHERE state='pending' AND available_at <= ?
            ORDER BY priority ASC, created_at ASC LIMIT 1
        )
    """, (worker_id, now, now, now))
    if updated.rowcount == 0:
        conn.execute("COMMIT;")
        return None
    job = conn.execute("""
        SELECT * FROM jobs WHERE worker_id=? AND state='processing'
        ORDER BY started_at DESC LIMIT 1
    """, (worker_id,)).fetchone()
    conn.execute("COMMIT;")
    return job

def update_worker_heartbeat(conn, worker_id, pid, status):
    conn.execute("""
    INSERT INTO workers(id,pid,started_at,heartbeat_at,status)
    VALUES(?,?,?,?,?)
    ON CONFLICT(id) DO UPDATE SET heartbeat_at=excluded.heartbeat_at, status=excluded.status, pid=excluded.pid
    """, (worker_id, pid, now_utc(), now_utc(), status))

def finish_job_success(conn, job_id, output):
    conn.execute("""
        UPDATE jobs SET state='completed', finished_at=?, updated_at=?, output=?
        WHERE id=?
    """, (now_utc(), now_utc(), output, job_id))

def finish_job_failure(conn, job, error):
    attempts = job["attempts"] + 1
    base = int(get_config(conn, "backoff_base", str(DEFAULT_BACKOFF_BASE)))
    delay = compute_backoff_delay(base, attempts)

    if attempts > job["max_retries"]:
        conn.execute("""
            UPDATE jobs SET state='dead', attempts=?, finished_at=?, updated_at=?, last_error=?
            WHERE id=?
        """, (attempts, now_utc(), now_utc(), error, job["id"]))
    else:
        next_time = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
        conn.execute("""
            UPDATE jobs SET state='pending', attempts=?, available_at=?, updated_at=?, last_error=?
            WHERE id=?
        """, (attempts, next_time, now_utc(), error, job["id"]))

def run_command(cmd, timeout):
    proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr

_shutdown_event = Event()

def _signal_handler(sig, frame):
    _shutdown_event.set()

def worker_loop(worker_id):
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)
    pid = os.getpid()

    while not _shutdown_event.is_set():
        with db() as conn:
            update_worker_heartbeat(conn, worker_id, pid, "idle")
            job = atomic_claim_next_job(conn, worker_id)

        if not job:
            with db() as conn:
                idle = float(get_config(conn, "poll_idle_secs", str(DEFAULT_POLL_IDLE_SECS)))
            time.sleep(idle)
            continue

        with db() as conn:
            update_worker_heartbeat(conn, worker_id, pid, f"processing:{job['id']}")

        try:
            code, out, err = run_command(job["command"], job["run_timeout"])
            with db() as conn:
                finish_job_success(conn, job["id"], out) if code == 0 else finish_job_failure(conn, job, err.strip())
        except Exception as e:
            with db() as conn:
                finish_job_failure(conn, job, str(e))

    with db() as conn:
        conn.execute("UPDATE workers SET status='stopped', heartbeat_at=? WHERE id=?", (now_utc(), worker_id))

@worker_app.command("start")
def worker_start(count: int = typer.Option(1, "--count", "-c")):
    init_db()
    _shutdown_event.clear()

    procs: List[Process] = []
    for _ in range(count):
        wid = str(uuid.uuid4())
        p = Process(target=worker_loop, args=(wid,))
        p.start()
        procs.append(p)

    console.print(f"[green]Started {count} worker(s). Press Ctrl+C to stop.[/green]")
    try:
        for p in procs:
            p.join()
    except KeyboardInterrupt:
        console.print("[yellow]Stopping workers gracefully...[/yellow]")
        _shutdown_event.set()
        for p in procs:
            if p.is_alive():
                try: os.kill(p.pid, signal.SIGTERM)
                except: p.terminate()
        for p in procs:
            p.join()

@app.command("enqueue")
def enqueue_cmd(job_json: str):
    try:
        job = json.loads(job_json)
        job_id = enqueue_job(job)
        console.print(f"[green]Enqueued job:[/green] {job_id}")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")

@app.command("list")
def list_cmd(state: Optional[str] = None):
    init_db()
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE state=? ORDER BY created_at" if state else "SELECT * FROM jobs ORDER BY created_at",
            (state,) if state else ()
        ).fetchall()
    if not rows:
        console.print("[yellow]No jobs found.[/yellow]")
        return
    t = Table("id", "state", "cmd", "attempts", "max", "avail_at", "updated_at", box=box.SIMPLE)
    for r in rows:
        t.add_row(r["id"], r["state"], r["command"], str(r["attempts"]), str(r["max_retries"]), r["available_at"], r["updated_at"])
    console.print(t)

@app.command("status")
def status_cmd():
    init_db()
    with db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        pending = conn.execute("SELECT COUNT(*) FROM jobs WHERE state='pending'").fetchone()[0]
        processing = conn.execute("SELECT COUNT(*) FROM jobs WHERE state='processing'").fetchone()[0]
        completed = conn.execute("SELECT COUNT(*) FROM jobs WHERE state='completed'").fetchone()[0]
        dead = conn.execute("SELECT COUNT(*) FROM jobs WHERE state='dead'").fetchone()[0]
    t = Table("Metric", "Count", box=box.SIMPLE_HEAVY)
    t.add_row("Total Jobs", str(total))
    t.add_row("Pending", str(pending))
    t.add_row("Processing", str(processing))
    t.add_row("Completed", str(completed))
    t.add_row("Dead (DLQ)", str(dead))
    console.print(t)

@dlq_app.command("list")
def dlq_list():
    init_db()
    with db() as conn:
        rows = conn.execute("SELECT id, attempts, max_retries, last_error FROM jobs WHERE state='dead'").fetchall()
    if not rows:
        console.print("[yellow]DLQ is empty.[/yellow]")
        return
    t = Table("id", "attempts", "max", "last_error", box=box.SIMPLE)
    for r in rows:
        t.add_row(r["id"], str(r["attempts"]), str(r["max_retries"]), r["last_error"] or "")
    console.print(t)

@dlq_app.command("retry")
def dlq_retry(job_id: str):
    init_db()
    with db() as conn:
        conn.execute("""
        UPDATE jobs SET state='pending', attempts=0, available_at=?, updated_at=?, last_error=NULL, finished_at=NULL
        WHERE id=?
        """, (now_utc(), now_utc(), job_id))
    console.print(f"[green]Requeued:[/green] {job_id}")

@config_app.command("get")
def config_get(key: str):
    init_db()
    with db() as conn:
        console.print(f"{key} = {get_config(conn, key, '')}")

@config_app.command("set")
def config_set_cmd(key: str, value: str):
    init_db()
    with db() as conn:
        set_config(conn, key, value)
    console.print(f"[green]Updated[/green] {key} = {value}")

if __name__ == "__main__":
    init_db()
    app()
