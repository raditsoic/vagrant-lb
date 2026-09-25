"""Demo app: counts visits in ONE shared PostgreSQL database.

Ships as a container image (see Dockerfile). All configuration arrives via
environment variables at run time — the image contains no credentials and
no lab-specific values, so the same pushed tag works everywhere:

    DB_HOST      default db.tiket.lab
    DB_NAME      required (tiketdb)
    DB_USER      required (tiket)
    DB_PASSWORD  required (injected by Ansible from the vault at run time)

Every web VM runs this exact same image and connects to the same database
server — the shared state is what makes the backends interchangeable: it
no longer matters which node the load balancer picks, the visit total is
the same everywhere.
"""

import os
import socket

import psycopg2
from flask import Flask

DB_HOST = os.environ.get("DB_HOST", "db.tiket.lab")
DSN = (
    f"dbname={os.environ['DB_NAME']}"
    f" user={os.environ['DB_USER']}"
    f" password={os.environ['DB_PASSWORD']}"
    f" host={DB_HOST}"
)

app = Flask(__name__)


def connect():
    return psycopg2.connect(DSN, connect_timeout=3)


@app.get("/healthz")
def healthz():
    # What a real health endpoint looks like: 200 only while the app can
    # actually reach its database — the thing that breaks in practice.
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
    except psycopg2.Error:
        return "db unreachable\n", 503
    finally:
        conn.close()
    return "ok\n", 200


@app.get("/")
def index():
    host = socket.gethostname()
    # psycopg2's `with conn` commits but does NOT close — close explicitly.
    conn = connect()
    try:
        with conn.cursor() as cur:
            # Idempotent: whoever gets the first request creates the table.
            cur.execute(
                "CREATE TABLE IF NOT EXISTS visits ("
                " id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,"
                " host TEXT NOT NULL,"
                " ts TIMESTAMPTZ DEFAULT now())"
            )
            cur.execute("INSERT INTO visits (host) VALUES (%s)", (host,))
            cur.execute("SELECT COUNT(*) FROM visits WHERE host = %s", (host,))
            mine = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM visits")
            total = cur.fetchone()[0]
        conn.commit()
    finally:
        conn.close()
    return (
        "<!DOCTYPE html><html><head><title>Served by "
        + host
        + "</title></head><body>"
        + "<h1>Hello from " + host + "</h1>"
        + "<p>Requests handled by me: " + str(mine) + "</p>"
        + "<p>Rows in the shared DB: " + str(total) + "</p>"
        + "<p>DB: postgresql://" + DB_HOST + "/" + os.environ["DB_NAME"] + "</p>"
        + "</body></html>"
    )
