import argparse
import csv
import datetime as dt
import re
import shutil
import unicodedata
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import melhorar_csv_bases as base


ROOT = Path(__file__).resolve().parent
DASHBOARD = ROOT / "dashboard"
CSV_PATH = DASHBOARD / "leads_dashboard_melhorado.csv"
REPORT_DIR = DASHBOARD / "deduplicacao"

STATUS_RANK = {
    "Captado": 1,
    "Visualizado": 2,
    "Tratado": 3,
    "Retorno": 4,
    "Fechado-Perda": 5,
    "Fechado-Venda": 6,
}

PRIORITY_RANK = {
    "Frio": 1,
    "Morno": 2,
    "Quente": 3,
}

GENERIC_NAMES = {
    "",
    "sem nome",
    "nao informado",
    "n a",
    "telefone",
    "email",
    "lead",
    "www",
}


def log(message):
    print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] {message}")


def normalize_text(value):
    text = str(value or "").strip().lower()
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_name(value):
    text = normalize_text(value)
    text = re.sub(r"\b(ltda|me|epp|eireli|sa|s a|ss|mei)\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def canonical_url(value):
    url = base.clean_url(value)
    if not url:
        return ""
    parsed = urlparse(url)
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = re.sub(r"/+", "/", parsed.path).rstrip("/")
    return urlunparse((parsed.scheme.lower() or "https", netloc, path, "", "", "")).lower()


def clean_email(value):
    emails = base.emails_from_text(value)
    return emails[0] if emails else ""


def clean_cnpj(value):
    digits = re.sub(r"\D", "", str(value or ""))
    return digits if len(digits) == 14 else ""


def parse_date(value):
    text = str(value or "").strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(text[:10], fmt).date()
        except ValueError:
            pass
    return None


def parse_number(value):
    text = str(value or "").replace("R$", "").replace(" ", "").strip()
    if not text:
        return 0
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return 0


def normalize_row(row):
    item = {name: base.compact(row.get(name, "")) for name in base.FIELDNAMES}
    item["email"] = clean_email(item.get("email")) or clean_email(" ".join(str(v) for v in row.values()))
    item["numero"] = base.clean_phone(item.get("numero")) or base.clean_phone(row.get("telefone")) or item.get("numero", "")
    item["link"] = base.clean_url(item.get("link"))
    item["perfil"] = base.clean_url(item.get("perfil")) or item["link"]
    item["site"] = base.clean_url(item.get("site")) or base.site_from_email(item["email"])
    item["data"] = base.excel_date(item.get("data")) or base.TODAY_BR
    item["status"] = item.get("status") or "Captado"
    item["fonte"] = item.get("fonte") or item.get("origem_base") or "Nao informado"
    item["lead"] = item.get("lead") or item.get("nome") or item.get("empresa") or item.get("email") or item.get("numero") or item.get("perfil")
    item["nome"] = item.get("nome") or item.get("lead")
    item["empresa"] = item.get("empresa") or item.get("nome")
    return base.enrich_row(item)


def read_csv_rows(path):
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return [normalize_row(row) for row in csv.DictReader(f)]


def strong_keys(row):
    keys = []
    for col in ("link", "perfil"):
        url = canonical_url(row.get(col))
        if url:
            keys.append(("url", url))
    email = clean_email(row.get("email"))
    if email:
        keys.append(("email", email))
    phone = base.clean_phone(row.get("numero"))
    if phone:
        keys.append(("telefone", phone))
    cnpj = clean_cnpj(row.get("cnpj"))
    if cnpj:
        keys.append(("cnpj", cnpj))
    return list(dict.fromkeys(keys))


def weak_keys(row):
    name = normalize_name(row.get("nome") or row.get("lead") or row.get("empresa"))
    if len(name) < 4 or name in GENERIC_NAMES:
        return []
    source = normalize_text(row.get("fonte"))
    return [("nome_fonte", name, source)]


def row_keys(row):
    keys = strong_keys(row)
    return keys if keys else weak_keys(row)


def key_text(key):
    return ":".join(str(part) for part in key)


class UnionFind:
    def __init__(self, size):
        self.parent = list(range(size))

    def find(self, item):
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, a, b):
        ra = self.find(a)
        rb = self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def best_quality(row):
    score = 0
    for col in base.FIELDNAMES:
        if row.get(col):
            score += 1
    if row.get("email"):
        score += 20
    if row.get("numero"):
        score += 20
    if row.get("link") or row.get("perfil"):
        score += 15
    if row.get("site"):
        score += 8
    if row.get("cnpj"):
        score += 10
    score += parse_number(row.get("score")) / 10
    score += STATUS_RANK.get(row.get("status"), 0)
    return score


def unique_join(values, sep="; "):
    out = []
    seen = set()
    for value in values:
        text = base.compact(value)
        if not text:
            continue
        marker = normalize_text(text)
        if marker and marker not in seen:
            out.append(text)
            seen.add(marker)
    return sep.join(out)


def choose_latest_date(values):
    dated = [(parse_date(value), value) for value in values if value]
    dated = [(date, value) for date, value in dated if date]
    if not dated:
        return next((value for value in values if value), "")
    return max(dated, key=lambda item: item[0])[1]


def merge_group(rows):
    ordered = sorted(rows, key=best_quality, reverse=True)
    merged = dict(ordered[0])

    for row in ordered[1:]:
        for col in base.FIELDNAMES:
            value = row.get(col, "")
            if not merged.get(col) and value:
                merged[col] = value

    merged["origem_base"] = unique_join(row.get("origem_base") for row in ordered)
    merged["observacao"] = unique_join((row.get("observacao") for row in ordered), sep=" | ")
    merged["data"] = choose_latest_date(row.get("data") for row in ordered)

    merged["valor"] = str(max(parse_number(row.get("valor")) for row in ordered) or merged.get("valor") or "")
    merged["status"] = max((row.get("status") or "Captado" for row in ordered), key=lambda value: STATUS_RANK.get(value, 0))
    merged["prioridade"] = max((row.get("prioridade") or "Frio" for row in ordered), key=lambda value: PRIORITY_RANK.get(value, 0))
    merged["score"] = ""
    return base.enrich_row(merged)


def group_reason(rows):
    keys = defaultdict(int)
    for row in rows:
        for key in row_keys(row):
            keys[key_text(key)] += 1
    repeated = [key for key, count in keys.items() if count > 1]
    return " | ".join(repeated[:5]) or "nome/fonte semelhante"


def deduplicate(rows, renumber=True):
    uf = UnionFind(len(rows))
    owners = {}

    for index, row in enumerate(rows):
        for key in row_keys(row):
            marker = key_text(key)
            if marker in owners:
                uf.union(owners[marker], index)
            else:
                owners[marker] = index

    grouped = defaultdict(list)
    for index, row in enumerate(rows):
        grouped[uf.find(index)].append(row)

    cleaned = []
    duplicate_groups = []
    for group_rows in grouped.values():
        if len(group_rows) > 1:
            duplicate_groups.append(group_rows)
        cleaned.append(merge_group(group_rows))

    cleaned.sort(key=lambda row: (row.get("fonte", ""), row.get("nome", "")))
    for index, row in enumerate(cleaned, 1):
        row["id"] = str(index) if renumber else str(row.get("id") or "")
        for col in base.FIELDNAMES:
            row.setdefault(col, "")
    return cleaned, duplicate_groups


def write_csv(path, rows):
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = path.with_name(f"{path.stem}_backup_deduplicacao_{timestamp}.csv")
    if path.exists():
        shutil.copy2(path, backup)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=base.FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    return backup


def write_reports(original_count, cleaned_count, duplicate_groups, dry_run):
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    report_txt = REPORT_DIR / f"relatorio_deduplicacao_{timestamp}.txt"
    report_csv = REPORT_DIR / f"duplicados_detalhe_{timestamp}.csv"

    with report_csv.open("w", encoding="utf-8-sig", newline="") as f:
        fieldnames = ["grupo", "acao", "id_original", "nome", "fonte", "email", "numero", "link", "motivo"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for group_index, group_rows in enumerate(duplicate_groups, 1):
            reason = group_reason(group_rows)
            best = max(group_rows, key=best_quality)
            for row in group_rows:
                writer.writerow(
                    {
                        "grupo": group_index,
                        "acao": "mantido_mesclado" if row is best else "removido_mesclado",
                        "id_original": row.get("id", ""),
                        "nome": row.get("nome", ""),
                        "fonte": row.get("fonte", ""),
                        "email": row.get("email", ""),
                        "numero": row.get("numero", ""),
                        "link": row.get("link") or row.get("perfil", ""),
                        "motivo": reason,
                    }
                )

    removed = original_count - cleaned_count
    report_txt.write_text(
        "\n".join(
            [
                f"Gerado em: {dt.datetime.now().strftime('%d/%m/%Y %H:%M:%S')}",
                f"Modo: {'dry-run' if dry_run else 'aplicado'}",
                f"Leads antes: {original_count}",
                f"Leads depois: {cleaned_count}",
                f"Duplicados removidos por mesclagem: {removed}",
                f"Grupos duplicados: {len(duplicate_groups)}",
                f"Detalhe CSV: {report_csv}",
            ]
        ),
        encoding="utf-8",
    )
    return report_txt, report_csv


def parse_args():
    parser = argparse.ArgumentParser(description="Revisa a base de leads e remove duplicidades.")
    parser.add_argument("--csv", default=str(CSV_PATH), help="Caminho do CSV de leads.")
    parser.add_argument("--dry-run", action="store_true", help="Gera relatorio sem sobrescrever o CSV.")
    parser.add_argument("--csv-only", action="store_true", help="Deduplica apenas o CSV atual, sem reler MAIN.xlsx/linkedin.txt/instagram.txt.")
    return parser.parse_args()


def main():
    args = parse_args()
    csv_path = Path(args.csv)

    log(f"Lendo CSV: {csv_path}")
    rows = read_csv_rows(csv_path)

    if not args.csv_only:
        log("Relendo bases locais: MAIN.xlsx, linkedin.txt e instagram.txt")
        rows.extend(normalize_row(row) for row in base.build_from_bases())

    original_count = len(rows)
    log(f"Revisando {original_count} registros.")
    cleaned, duplicate_groups = deduplicate(rows)
    report_txt, report_csv = write_reports(original_count, len(cleaned), duplicate_groups, args.dry_run)

    if args.dry_run:
        log("Dry-run: CSV nao foi alterado.")
    else:
        backup = write_csv(csv_path, cleaned)
        log(f"Backup criado: {backup}")
        log(f"CSV deduplicado: {csv_path}")

    log(f"Leads antes: {original_count}")
    log(f"Leads depois: {len(cleaned)}")
    log(f"Duplicados removidos por mesclagem: {original_count - len(cleaned)}")
    log(f"Grupos duplicados: {len(duplicate_groups)}")
    log(f"Relatorio: {report_txt}")
    log(f"Detalhe dos duplicados: {report_csv}")


if __name__ == "__main__":
    main()
