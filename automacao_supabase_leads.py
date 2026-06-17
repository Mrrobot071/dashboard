import argparse
import csv
import datetime as dt
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path
from urllib import error, parse, request
from urllib.parse import urlparse

import melhorar_csv_bases as base
import revisar_deduplicar_leads as reviewer


ROOT = Path(__file__).resolve().parent
DASHBOARD = ROOT / "dashboard"
CSV_PATH = DASHBOARD / "leads_dashboard_melhorado.csv"
DASHBOARD_HTML = DASHBOARD / "dash csv.html"
INBOX = ROOT / "entrada_periodica"
PROCESSED = INBOX / "processados"
LOG_DIR = ROOT / "logs"
ENV_PATH = ROOT / ".env"

DEFAULT_SUPABASE_URL = "https://cjlzzhjdkkpgzfnnatlj.supabase.co"
DEFAULT_TABLE = "leads"

LINKEDIN_RE = re.compile(r"https?://(?:[\w.-]+\.)?linkedin\.com/in/[A-Za-z0-9_-]+/?", re.I)
INSTAGRAM_RE = re.compile(r"https?://(?:www\.)?instagram\.com/[A-Za-z0-9_.]{1,30}/?", re.I)

DEFAULT_SUPABASE_COLUMNS = [
    "lead",
    "nome",
    "fonte",
    "status",
    "valor",
    "data",
    "link",
    "email",
    "perfil",
    "numero",
    "telefone",
    "categoria",
    "prioridade",
    "empresa",
    "site",
    "endereco",
    "cidade",
    "estado",
    "cnpj",
    "avaliacao",
    "qtd_avaliacoes",
    "origem_base",
    "observacao",
]

REMOVED_COLUMNS = {"score", "segmento", "proxima_acao", "dias"}

COLUMN_ALIASES = {
    "lead": "lead",
    "nome": "nome",
    "name": "nome",
    "empresa": "empresa",
    "company": "empresa",
    "fonte": "fonte",
    "source": "fonte",
    "status": "status",
    "valor": "valor",
    "data": "data",
    "date": "data",
    "link": "link",
    "url": "link",
    "linkedin": "perfil",
    "linkedin_url": "perfil",
    "perfil": "perfil",
    "profile": "perfil",
    "site": "site",
    "website": "site",
    "email": "email",
    "e_mail": "email",
    "mail": "email",
    "numero": "numero",
    "número": "numero",
    "telefone": "numero",
    "phone": "numero",
    "celular": "numero",
    "whatsapp": "numero",
    "categoria": "categoria",
    "prioridade": "prioridade",
    "endereco": "endereco",
    "endereço": "endereco",
    "cidade": "cidade",
    "estado": "estado",
    "cnpj": "cnpj",
    "observacao": "observacao",
    "observação": "observacao",
    "obs": "observacao",
}


def log(message):
    print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] {message}")


def load_env(path=ENV_PATH):
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def read_supabase_config_from_html(path=DASHBOARD_HTML):
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8", errors="ignore")
    config = {}
    for name in ("SUPABASE_URL", "SUPABASE_KEY"):
        match = re.search(rf"\bconst\s+{name}\s*=\s*['\"]([^'\"]+)['\"]", text)
        if match:
            config[name] = match.group(1).strip()
    return config


def resolve_supabase_config():
    html_config = read_supabase_config_from_html()
    url = os.environ.get("SUPABASE_URL") or html_config.get("SUPABASE_URL") or DEFAULT_SUPABASE_URL
    key = (
        os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        or os.environ.get("SUPABASE_KEY")
        or os.environ.get("SUPABASE_ANON_KEY")
        or html_config.get("SUPABASE_KEY")
    )
    source = "ambiente/.env" if (
        os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        or os.environ.get("SUPABASE_KEY")
        or os.environ.get("SUPABASE_ANON_KEY")
    ) else ("dashboard HTML" if html_config.get("SUPABASE_KEY") else "")
    return url, key, source


def clean_url(value):
    return base.clean_url(value) or ""


def title_from_url(url):
    path = urlparse(url).path.strip("/")
    handle = path.split("/")[-1] if path else url
    return handle.replace("-", " ").replace("_", " ").title() or url


def parse_money(value):
    if value in (None, ""):
        return 0
    if isinstance(value, (int, float)):
        return value
    raw = str(value).replace("R$", "").replace(" ", "").strip()
    if not raw:
        return 0
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        raw = raw.replace(",", ".")
    try:
        number = float(raw)
    except ValueError:
        return 0
    return int(number) if number.is_integer() else number


def parse_score(value):
    try:
        return int(float(str(value or 0).replace(",", ".")))
    except ValueError:
        return 0


def normalize_input_header(value):
    text = base.normalize_header(value)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text


def new_row(**kwargs):
    rows = []
    base.add_lead(rows, **kwargs)
    row = rows[0]
    row["id"] = ""
    return row


def row_from_link(link, source_name):
    url = clean_url(link)
    if not url:
        return None
    if "linkedin.com/in/" in url.lower():
        fonte = "LinkedIn"
    elif "instagram.com/" in url.lower():
        fonte = "Instagram"
    else:
        fonte = source_name or "Link"
    return new_row(
        lead=title_from_url(url),
        nome=title_from_url(url),
        fonte=fonte,
        status="Captado",
        valor="2500",
        data=base.TODAY_BR,
        link=url,
        perfil=url if fonte in {"LinkedIn", "Instagram"} else "",
        origem_base=source_name,
    )


def rows_from_text_file(path):
    text = path.read_text(encoding="utf-8", errors="ignore")
    rows = []

    for match in LINKEDIN_RE.findall(text):
        row = row_from_link(match, path.name)
        if row:
            rows.append(row)

    for match in INSTAGRAM_RE.findall(text):
        row = row_from_link(match, path.name)
        if row:
            rows.append(row)

    for email_addr in base.emails_from_text(text):
        rows.append(
            new_row(
                lead=email_addr,
                nome=email_addr.split("@", 1)[0].replace(".", " ").title(),
                fonte="E-mail",
                status="Captado",
                valor="3500",
                data=base.TODAY_BR,
                email=email_addr,
                site=base.site_from_email(email_addr),
                origem_base=path.name,
            )
        )

    for phone in base.phones_from_text(text):
        rows.append(
            new_row(
                lead=f"Telefone {phone}",
                nome=f"Telefone {phone}",
                fonte="Telefone",
                status="Captado",
                valor="1800",
                data=base.TODAY_BR,
                numero=phone,
                origem_base=path.name,
            )
        )

    return rows


def sniff_csv_dialect(path):
    sample = path.read_text(encoding="utf-8-sig", errors="ignore")[:4096]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;|\t")
    except csv.Error:
        class Fallback(csv.excel):
            delimiter = ";"

        return Fallback


def rows_from_csv_file(path):
    dialect = sniff_csv_dialect(path)
    rows = []
    with path.open("r", encoding="utf-8-sig", newline="", errors="ignore") as f:
        reader = csv.DictReader(f, dialect=dialect)
        for raw in reader:
            mapped = {name: "" for name in base.FIELDNAMES}
            extras = []
            for key, value in raw.items():
                if value is None:
                    continue
                normalized = normalize_input_header(key)
                target = COLUMN_ALIASES.get(normalized)
                if target and target in mapped:
                    mapped[target] = base.compact(value)
                elif str(value).strip():
                    extras.append(f"{key}: {base.compact(value)}")

            if not mapped["email"]:
                emails = base.emails_from_text(" ".join(str(v) for v in raw.values()))
                mapped["email"] = emails[0] if emails else ""
            if not mapped["numero"]:
                phones = base.phones_from_text(" ".join(str(v) for v in raw.values()))
                mapped["numero"] = phones[0] if phones else ""
            if not mapped["link"] and not mapped["perfil"]:
                link = base.first_url(*raw.values())
                mapped["link"] = link

            mapped["email"] = mapped["email"].lower()
            mapped["numero"] = base.clean_phone(mapped["numero"])
            mapped["link"] = clean_url(mapped["link"])
            mapped["perfil"] = clean_url(mapped["perfil"]) or mapped["link"]
            mapped["site"] = clean_url(mapped["site"]) or base.site_from_email(mapped["email"])
            mapped["data"] = base.excel_date(mapped["data"]) or base.TODAY_BR
            mapped["status"] = mapped["status"] or "Captado"
            mapped["valor"] = mapped["valor"] or "2500"
            mapped["fonte"] = mapped["fonte"] or ("LinkedIn" if "linkedin.com/in/" in mapped["perfil"].lower() else "Importado")
            mapped["lead"] = mapped["lead"] or mapped["nome"] or mapped["empresa"] or mapped["email"] or mapped["numero"] or mapped["perfil"]
            mapped["nome"] = mapped["nome"] or mapped["lead"]
            mapped["empresa"] = mapped["empresa"] or mapped["nome"]
            mapped["origem_base"] = mapped["origem_base"] or path.name
            if extras and not mapped["observacao"]:
                mapped["observacao"] = " | ".join(extras[:8])

            if mapped["lead"] or mapped["email"] or mapped["numero"] or mapped["perfil"]:
                rows.append(base.enrich_row(mapped))
    return rows


def read_inbox_rows():
    INBOX.mkdir(exist_ok=True)
    rows = []
    for path in sorted(INBOX.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() == ".csv":
            rows.extend(rows_from_csv_file(path))
        elif path.suffix.lower() in {".txt", ".log"}:
            rows.extend(rows_from_text_file(path))
    return rows


def archive_inbox_files():
    PROCESSED.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    moved = 0
    for path in sorted(INBOX.iterdir()):
        if not path.is_file():
            continue
        target = PROCESSED / f"{path.stem}_{stamp}{path.suffix}"
        shutil.move(str(path), str(target))
        moved += 1
    return moved


def strip_ids(rows):
    out = []
    for row in rows:
        item = dict(row)
        item["id"] = ""
        out.append(item)
    return out


def normalize_remote_row(row):
    item = {name: base.compact(row.get(name, "")) for name in base.FIELDNAMES}
    item["id"] = str(row.get("id") or "")
    item["numero"] = base.clean_phone(item.get("numero")) or base.clean_phone(row.get("telefone")) or item.get("numero", "")
    item["email"] = (item.get("email") or "").lower()
    item["link"] = clean_url(item.get("link"))
    item["perfil"] = clean_url(item.get("perfil")) or item["link"]
    item["site"] = clean_url(item.get("site")) or base.site_from_email(item["email"])
    item["data"] = base.excel_date(item.get("data")) or base.TODAY_BR
    item["fonte"] = item.get("fonte") or item.get("origem_base") or "Supabase"
    item["lead"] = item.get("lead") or item.get("nome") or item.get("empresa") or item.get("email") or item.get("numero")
    item["nome"] = item.get("nome") or item.get("lead")
    item["empresa"] = item.get("empresa") or item.get("nome")
    return base.enrich_row(item)


def load_ai_hook(spec):
    if not spec:
        return None
    if ":" not in spec:
        raise ValueError("Use LEADS_AI_HOOK no formato modulo:funcao")
    module_name, func_name = spec.split(":", 1)
    sys.path.insert(0, str(ROOT))
    module = importlib.import_module(module_name)
    return getattr(module, func_name)


def apply_ai_hook(rows, hook):
    if not hook:
        return rows
    enriched = []
    for row in rows:
        updates = hook(dict(row)) or {}
        merged = dict(row)
        for key, value in updates.items():
            if key in base.FIELDNAMES and value not in (None, ""):
                merged[key] = base.compact(value)
        enriched.append(base.enrich_row(merged))
    return enriched


def build_local_rows(remote_rows=None, ai_hook=None, strong_dedupe=False, preserve_remote_ids=False, include_generated=True):
    remote_rows = remote_rows or []
    current_remote = [normalize_remote_row(row) for row in remote_rows]
    existing_csv = strip_ids(base.read_existing_csv())
    generated = strip_ids(base.build_from_bases()) if include_generated else []
    inbox_rows = strip_ids(read_inbox_rows())
    rows = base.dedupe(current_remote + existing_csv + generated + inbox_rows)
    rows = apply_ai_hook(rows, ai_hook)
    duplicate_groups = []
    if strong_dedupe:
        rows, duplicate_groups = reviewer.deduplicate(rows, renumber=not preserve_remote_ids)
    else:
        rows.sort(key=lambda r: (r.get("fonte", ""), r.get("nome", "")))
    return rows, {
        "supabase": len(current_remote),
        "csv_existente": len(existing_csv),
        "bases": len(generated),
        "entrada_periodica": len(inbox_rows),
        "duplicados_mesclados": sum(len(group) - 1 for group in duplicate_groups),
        "grupos_duplicados": len(duplicate_groups),
    }


class SupabaseRest:
    def __init__(self, url, key, table):
        self.url = url.rstrip("/")
        self.key = key
        self.table = table

    def request(self, method, path, payload=None, headers=None):
        data = None
        req_headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Accept": "application/json",
        }
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            req_headers["Content-Type"] = "application/json"
        if headers:
            req_headers.update(headers)
        req = request.Request(f"{self.url}/rest/v1/{path}", data=data, headers=req_headers, method=method)
        try:
            with request.urlopen(req, timeout=90) as response:
                body = response.read().decode("utf-8", errors="ignore")
                if not body:
                    return None
                return json.loads(body)
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"Supabase {method} {path} falhou ({exc.code}): {body}") from exc

    def fetch_all(self, limit=1000):
        rows = []
        offset = 0
        table = parse.quote(self.table, safe="")
        while True:
            query = parse.urlencode({"select": "*", "order": "id.asc", "limit": limit, "offset": offset})
            page = self.request("GET", f"{table}?{query}") or []
            rows.extend(page)
            if len(page) < limit:
                break
            offset += limit
        return rows

    def update_row(self, row_id, payload):
        table = parse.quote(self.table, safe="")
        query = parse.urlencode({"id": f"eq.{row_id}"})
        self.request("PATCH", f"{table}?{query}", payload, headers={"Prefer": "return=minimal"})

    def insert_rows(self, payloads):
        if not payloads:
            return
        table = parse.quote(self.table, safe="")
        self.request("POST", table, payloads, headers={"Prefer": "return=minimal"})

    def delete_all(self):
        table = parse.quote(self.table, safe="")
        self.request("DELETE", f"{table}?id=not.is.null", headers={"Prefer": "return=minimal"})


def configured_columns(remote_rows):
    configured = os.environ.get("SUPABASE_COLUMNS", "").strip()
    if configured:
        return [col.strip() for col in configured.split(",") if col.strip() and col.strip() not in REMOVED_COLUMNS]
    if not remote_rows:
        return DEFAULT_SUPABASE_COLUMNS
    remote_columns = set()
    for row in remote_rows:
        remote_columns.update(row.keys())
    return [col for col in DEFAULT_SUPABASE_COLUMNS if col in remote_columns and col not in REMOVED_COLUMNS]


def row_payload(row, columns, touch_updated_at=False):
    payload = {}
    for col in columns:
        if col == "telefone":
            payload[col] = row.get("numero", "")
        elif col == "valor":
            payload[col] = parse_money(row.get(col))
        else:
            payload[col] = row.get(col, "")
    if touch_updated_at and "updated_at" in columns:
        payload["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    return payload


def comparable_payload(payload):
    return {key: "" if value is None else value for key, value in payload.items() if key != "updated_at"}


def remote_changed(remote_row, payload):
    wanted = comparable_payload(payload)
    current = comparable_payload(row_payload(normalize_remote_row(remote_row), list(wanted.keys())))
    return current != wanted


def sync_supabase(client, rows, remote_rows, dry_run=False, batch_size=200):
    columns = configured_columns(remote_rows)
    if "updated_at" in {key for row in remote_rows for key in row.keys()} and "updated_at" not in columns:
        columns.append("updated_at")

    remote_by_id = {str(row.get("id")): row for row in remote_rows if row.get("id") is not None}
    to_insert = []
    to_update = []

    for row in rows:
        row_id = str(row.get("id") or "")
        payload = row_payload(row, columns, touch_updated_at=False)
        if row_id and row_id in remote_by_id:
            if remote_changed(remote_by_id[row_id], payload):
                to_update.append((row_id, row_payload(row, columns, touch_updated_at=True)))
        else:
            payload.pop("updated_at", None)
            to_insert.append(payload)

    if dry_run:
        return {"updates": len(to_update), "inserts": len(to_insert), "columns": columns}

    for row_id, payload in to_update:
        client.update_row(row_id, payload)

    for start in range(0, len(to_insert), batch_size):
        client.insert_rows(to_insert[start : start + batch_size])

    return {"updates": len(to_update), "inserts": len(to_insert), "columns": columns}


def overwrite_supabase(client, rows, remote_rows, dry_run=False, batch_size=200):
    columns = configured_columns(remote_rows)
    if not columns:
        columns = DEFAULT_SUPABASE_COLUMNS

    payloads = []
    for row in rows:
        payload = row_payload(row, columns, touch_updated_at=False)
        payload.pop("updated_at", None)
        payloads.append(payload)

    if dry_run:
        return {
            "modo": "sobrescrever",
            "delete_existing": len(remote_rows),
            "updates": 0,
            "inserts": len(payloads),
            "columns": columns,
        }

    client.delete_all()
    for start in range(0, len(payloads), batch_size):
        client.insert_rows(payloads[start : start + batch_size])

    return {
        "modo": "sobrescrever",
        "delete_existing": len(remote_rows),
        "updates": 0,
        "inserts": len(payloads),
        "columns": columns,
    }


def run_extractors(enabled):
    if not enabled:
        return
    for filename in ["extrair linkedin.py", "extrair insta.py"]:
        script = ROOT / filename
        if script.exists():
            log(f"Rodando coletor: {filename}")
            subprocess.run([sys.executable, str(script)], cwd=str(ROOT), check=False)


def write_report(stats, sync_stats, dry_run):
    LOG_DIR.mkdir(exist_ok=True)
    report = LOG_DIR / f"automacao_supabase_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    report.write_text(
        json.dumps(
            {
                "gerado_em": dt.datetime.now().isoformat(),
                "dry_run": dry_run,
                "csv": str(CSV_PATH),
                "fontes": stats,
                "supabase": sync_stats,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return report


def parse_args():
    parser = argparse.ArgumentParser(description="Amplia o CSV de leads e sincroniza com Supabase.")
    parser.add_argument("--local-only", action="store_true", help="Atualiza apenas o CSV local, sem Supabase.")
    parser.add_argument("--dry-run", action="store_true", help="Mostra o que faria, sem gravar CSV nem Supabase.")
    parser.add_argument("--arquivar-entradas", action="store_true", help="Move arquivos de entrada para entrada_periodica/processados depois da execucao.")
    parser.add_argument("--coletar-links", action="store_true", help="Roda os coletores existentes de LinkedIn/Instagram antes de atualizar a base.")
    parser.add_argument("--batch-size", type=int, default=200, help="Tamanho dos lotes de insert no Supabase.")
    parser.add_argument("--dedupe-forte", action="store_true", help="Mescla duplicados antes da sincronizacao incremental preservando IDs do Supabase.")
    parser.add_argument("--sobrescrever-supabase", action="store_true", help="Apaga a tabela leads no Supabase e reinsere a base consolidada sem duplicacoes.")
    parser.add_argument("--sem-bases-locais", action="store_true", help="Nao reler MAIN.xlsx/linkedin.txt/instagram.txt; usa apenas Supabase, CSV atual e entrada_periodica.")
    return parser.parse_args()


def main():
    args = parse_args()
    load_env()

    run_extractors(args.coletar_links)

    supabase_url, supabase_key, key_source = resolve_supabase_config()
    table = os.environ.get("SUPABASE_TABLE", DEFAULT_TABLE)
    ai_hook = load_ai_hook(os.environ.get("LEADS_AI_HOOK", "").strip())

    remote_rows = []
    client = None
    if not args.local_only and supabase_key:
        client = SupabaseRest(supabase_url, supabase_key, table)
        log(f"Baixando base atual do Supabase usando chave de {key_source}.")
        remote_rows = client.fetch_all()
        log(f"{len(remote_rows)} registros encontrados no Supabase.")
    elif not args.local_only:
        log("SUPABASE_KEY nao encontrada no .env nem no dashboard HTML. Rodando apenas no CSV local.")

    log("Montando base consolidada.")
    strong_dedupe = args.local_only or args.sobrescrever_supabase or args.dedupe_forte
    preserve_remote_ids = bool(client and args.dedupe_forte and not args.sobrescrever_supabase)
    rows, stats = build_local_rows(
        remote_rows,
        ai_hook=ai_hook,
        strong_dedupe=strong_dedupe,
        preserve_remote_ids=preserve_remote_ids,
        include_generated=not args.sem_bases_locais,
    )
    log(f"Base consolidada: {len(rows)} leads.")

    sync_stats = {"updates": 0, "inserts": 0, "columns": []}
    if client:
        if args.sobrescrever_supabase:
            log("Sobrescrevendo Supabase com base consolidada sem duplicacoes.")
            sync_stats = overwrite_supabase(client, rows, remote_rows, dry_run=args.dry_run, batch_size=args.batch_size)
            log(f"Supabase: apagar {sync_stats['delete_existing']} existentes, inserir {sync_stats['inserts']} limpos.")
        else:
            log("Sincronizando alteracoes no Supabase.")
            sync_stats = sync_supabase(client, rows, remote_rows, dry_run=args.dry_run, batch_size=args.batch_size)
            log(f"Supabase: {sync_stats['updates']} updates, {sync_stats['inserts']} inserts.")

    if not args.dry_run:
        backup, report, counts = base.write_outputs(rows)
        log(f"CSV atualizado: {CSV_PATH}")
        log(f"Backup local: {backup}")
        log(f"Relatorio local: {report}")
        if args.arquivar_entradas:
            moved = archive_inbox_files()
            log(f"Arquivos de entrada arquivados: {moved}")
    else:
        log("Dry-run: CSV e Supabase nao foram gravados.")

    report = write_report(stats, sync_stats, args.dry_run)
    log(f"Log da automacao: {report}")


if __name__ == "__main__":
    main()
