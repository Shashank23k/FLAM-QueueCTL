from flask import Flask, render_template_string
import sqlite3
from datetime import datetime, timezone

DB_PATH = "queue.db"
app = Flask(__name__)


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
  <title>Queue Dashboard</title>
  <style>
    body { font-family: Arial, sans-serif; padding: 20px; }
    h1 { margin-bottom: 5px; }
    table { border-collapse: collapse; width: 100%; margin-top: 10px; }
    th, td { border: 1px solid #aaa; padding: 6px; font-size: 14px; }
    th { background: #eee; }
  </style>
</head>
<body>

<h1>Queue Status</h1>

<h3>Job Counts</h3>
<table>
<tr><th>State</th><th>Count</th></tr>
<tr><td>Pending</td><td>{{ pending }}</td></tr>
<tr><td>Processing</td><td>{{ processing }}</td></tr>
<tr><td>Completed</td><td>{{ completed }}</td></tr>
<tr><td>Dead (DLQ)</td><td>{{ dead }}</td></tr>
<tr><td><b>Total Jobs</b></td><td><b>{{ total }}</b></td></tr>
</table>

<h3>Active Workers</h3>
<table>
<tr><th>ID</th><th>PID</th><th>Status</th><th>Last Heartbeat</th></tr>
{% for w in workers %}
<tr>
  <td>{{ w.id }}</td>
  <td>{{ w.pid }}</td>
  <td>{{ w.status }}</td>
  <td>{{ w.heartbeat_at }}</td>
</tr>
{% endfor %}
</table>

<h3>Recent Jobs</h3>
<table>
<tr>
  <th>ID</th><th>State</th><th>Command</th><th>Attempts</th><th>Max</th><th>Updated</th>
</tr>
{% for j in jobs %}
<tr>
  <td>{{ j.id }}</td>
  <td>{{ j.state }}</td>
  <td>{{ j.command }}</td>
  <td>{{ j.attempts }}</td>
  <td>{{ j.max_retries }}</td>
  <td>{{ j.updated_at }}</td>
</tr>
{% endfor %}
</table>

</body>
</html>
"""

@app.route("/")
def home():
    conn = db()

    pending = conn.execute("SELECT COUNT(*) FROM jobs WHERE state='pending'").fetchone()[0]
    processing = conn.execute("SELECT COUNT(*) FROM jobs WHERE state='processing'").fetchone()[0]
    completed = conn.execute("SELECT COUNT(*) FROM jobs WHERE state='completed'").fetchone()[0]
    dead = conn.execute("SELECT COUNT(*) FROM jobs WHERE state='dead'").fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

    workers = conn.execute("SELECT * FROM workers").fetchall()
    jobs = conn.execute("SELECT * FROM jobs ORDER BY updated_at DESC LIMIT 20").fetchall()

    return render_template_string(TEMPLATE,
        pending=pending, processing=processing, completed=completed, dead=dead, total=total,
        workers=workers, jobs=jobs)


if __name__ == "__main__":
    print("Dashboard running → http://127.0.0.1:5000")
    app.run(debug=True)
