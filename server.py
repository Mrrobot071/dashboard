#!/usr/bin/env python3
# Servidor simples para Dashboard de Leads Construtec
# Lê e grava o arquivo leads.csv em tempo real via API HTTP.

import csv
import json
import os
import shutil
from datetime import datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent
CSV_PATH = BASE_DIR / "leads.csv"
BACKUP_DIR = BASE_DIR / "backups"
DASHBOARD_PATH = BASE_DIR / "dashboard_online.html"
PORT = int(os.environ.get("PORT", "8000"))
API_TOKEN = os.environ.get("API_TOKEN", "").strip()  # opcional: protege gravações POST

DEFAULT_FIELDS = ["id", "lead", "nome", "fonte", "status", "valor", "data", "link", "email", "perfil", "numero"]


def ensure_csv():
    if not CSV_PATH.exists():
        with CSV_PATH.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=DEFAULT_FIELDS)
            writer.writeheader()


def read_rows():
    ensure_csv()
    with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames or DEFAULT_FIELDS
    return fieldnames, rows


def backup_csv():
    ensure_csv()
    BACKUP_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copy2(CSV_PATH, BACKUP_DIR / f"leads_{stamp}.csv")


def write_rows(fieldnames, rows):
    backup_csv()
    tmp_path = CSV_PATH.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})
    tmp_path.replace(CSV_PATH)


def next_id(rows):
    max_id = 0
    for row in rows:
        try:
            max_id = max(max_id, int(str(row.get("id", "0")).strip() or 0))
        except ValueError:
            continue
    return str(max_id + 1)


def json_response(handler, payload, status=200):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type, X-API-Key")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def text_response(handler, text, status=200, content_type="text/plain; charset=utf-8"):
    data = text.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {self.address_string()} - {fmt % args}")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-API-Key")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def authorized(self):
        if not API_TOKEN:
            return True
        return self.headers.get("X-API-Key", "").strip() == API_TOKEN

    def read_json_body(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        return json.loads(raw or "{}")

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/", "/dashboard", "/dashboard_online.html"):
            if not DASHBOARD_PATH.exists():
                return text_response(self, "dashboard_online.html não encontrado", 404)
            html = DASHBOARD_PATH.read_text(encoding="utf-8")
            return text_response(self, html, 200, "text/html; charset=utf-8")

        if path == "/api/leads":
            fieldnames, rows = read_rows()
            modified = datetime.fromtimestamp(CSV_PATH.stat().st_mtime).isoformat(timespec="seconds")
            return json_response(self, {"fields": fieldnames, "rows": rows, "total": len(rows), "modified": modified})

        if path in ("/leads.csv", "/api/export"):
            ensure_csv()
            data = CSV_PATH.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", "attachment; filename=leads.csv")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        return text_response(self, "Rota não encontrada", 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path not in ("/api/leads/update", "/api/leads/add", "/api/leads/delete"):
            return json_response(self, {"ok": False, "error": "Rota não encontrada"}, 404)

        if not self.authorized():
            return json_response(self, {"ok": False, "error": "Token inválido ou ausente"}, 401)

        try:
            payload = self.read_json_body()
            fieldnames, rows = read_rows()

            if path == "/api/leads/update":
                row_id = str(payload.get("id", "")).strip()
                updates = payload.get("updates", {}) or {}
                if not row_id:
                    return json_response(self, {"ok": False, "error": "ID obrigatório"}, 400)

                changed = False
                for row in rows:
                    if str(row.get("id", "")).strip() == row_id:
                        for key, value in updates.items():
                            if key not in fieldnames:
                                fieldnames.append(key)
                            row[key] = value
                        changed = True
                        break

                if not changed:
                    return json_response(self, {"ok": False, "error": "Lead não encontrado"}, 404)

                write_rows(fieldnames, rows)
                return json_response(self, {"ok": True, "message": "Lead atualizado"})

            if path == "/api/leads/add":
                new_row = payload.get("row", {}) or {}
                if not new_row.get("id"):
                    new_row["id"] = next_id(rows)
                for key in new_row.keys():
                    if key not in fieldnames:
                        fieldnames.append(key)
                rows.append(new_row)
                write_rows(fieldnames, rows)
                return json_response(self, {"ok": True, "message": "Lead adicionado", "id": new_row["id"]})

            if path == "/api/leads/delete":
                row_id = str(payload.get("id", "")).strip()
                before = len(rows)
                rows = [r for r in rows if str(r.get("id", "")).strip() != row_id]
                if len(rows) == before:
                    return json_response(self, {"ok": False, "error": "Lead não encontrado"}, 404)
                write_rows(fieldnames, rows)
                return json_response(self, {"ok": True, "message": "Lead excluído"})

        except Exception as exc:
            return json_response(self, {"ok": False, "error": str(exc)}, 500)


if __name__ == "__main__":
    ensure_csv()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Dashboard online em: http://localhost:{PORT}")
    print(f"CSV conectado: {CSV_PATH}")
    if API_TOKEN:
        print("API_TOKEN ativo: gravações POST protegidas.")
    server.serve_forever()
