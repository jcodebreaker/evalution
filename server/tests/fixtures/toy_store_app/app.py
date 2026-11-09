"""A toy assistant that keeps what it's told in three places, for the isolation tests.

* ``data/chat.db``: a SQLite file the code names (the scan should find it).
* ``cache/notes.txt``: a file in the project the code never calls a store (only the probe can).
* A SQLite file at a path given on the command line, outside the project, held open from start
  (no literal names it: only the process's open files lead to it).

It also reads ``DATABASE_URL`` and never connects, so that store stays unconfirmed.
"""

import json
import os
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HERE = Path(__file__).parent
DATABASE_URL = os.getenv("DATABASE_URL", "")
chat = sqlite3.connect(str(HERE / "data" / "chat.db"), check_same_thread=False)
chat.execute("CREATE TABLE IF NOT EXISTS messages (text TEXT)")
hidden = sqlite3.connect(sys.argv[2], check_same_thread=False)
hidden.execute("PRAGMA journal_mode=WAL")
hidden.execute("CREATE TABLE IF NOT EXISTS memory (fact TEXT)")
hidden.commit()


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        text = json.loads(self.rfile.read(int(self.headers["Content-Length"])))["message"]
        chat.execute("INSERT INTO messages VALUES (?)", (text,))
        chat.commit()
        hidden.execute("INSERT INTO memory VALUES (?)", (text,))
        hidden.commit()
        (HERE / "cache").mkdir(exist_ok=True)
        with (HERE / "cache" / "notes.txt").open("a") as f:
            f.write(text + "\n")
        body = json.dumps({"reply": "noted"}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


HTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
