import csv
import datetime as dt
import math
import re
import shutil
import unicodedata
from pathlib import Path
from urllib.parse import urlparse, urlunparse


ROOT = Path(__file__).resolve().parent
DASHBOARD = ROOT / "dashboard"
CSV_PATH = DASHBOARD / "leads_dashboard_melhorado.csv"
MAIN_XLSX = ROOT / "MAIN.xlsx"
TODAY_BR = dt.datetime.now().strftime("%d/%m/%Y")

FIELDNAMES = [
    "id",
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

HEADER_ALIASES = {
    "lead": "lead",
    "nome": "nome",
    "name": "nome",
    "empresa": "empresa",
    "company": "empresa",
    "fonte": "fonte",
    "source": "fonte",
    "status": "status",
    "valor": "valor",
    "value": "valor",
    "data": "data",
    "date": "data",
    "link": "link",
    "url": "link",
    "site": "site",
    "website": "site",
    "email": "email",
    "e_mail": "email",
    "mail": "email",
    "perfil": "perfil",
    "profile": "perfil",
    "linkedin": "perfil",
    "instagram": "perfil",
    "numero": "numero",
    "nÃºmero": "numero",
    "telefone": "numero",
    "phone": "numero",
    "celular": "numero",
    "whatsapp": "numero",
    "categoria": "categoria",
    "category": "categoria",
    "prioridade": "prioridade",
    "priority": "prioridade",
    "endereco": "endereco",
    "endereÃ§o": "endereco",
    "address": "endereco",
    "cidade": "cidade",
    "city": "cidade",
    "estado": "estado",
    "uf": "estado",
    "state": "estado",
    "cnpj": "cnpj",
    "avaliacao": "avaliacao",
    "qtd_avaliacoes": "qtd_avaliacoes",
    "origem_base": "origem_base",
    "observacao": "observacao",
    "observaÃ§Ã£o": "observacao",
    "obs": "observacao",
}

VALUE_BY_SEGMENT = {
    "Hospitais e clinicas": "6500",
    "Administradoras e condominios": "5000",
    "Construtoras e incorporadoras": "8000",
    "Facilities e manutencao": "4500",
    "Engenharia e arquitetura": "4500",
    "Instituicoes": "5000",
    "Industria e logistica": "7000",
}

PERSONAL_EMAIL_DOMAINS = {
    "gmail.com",
    "hotmail.com",
    "outlook.com",
    "live.com",
    "yahoo.com",
    "icloud.com",
    "bol.com.br",
    "uol.com.br",
}


def compact(value):
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    text = str(value).replace("\ufeff", "").strip()
    if text.lower() in {"nan", "none", "null"}:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def normalize_header(value):
    text = compact(value).lower()
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text


def normalize_text(value):
    text = compact(value).lower()
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def clean_phone(value):
    digits = re.sub(r"\D", "", compact(value))
    if len(digits) > 11 and digits.startswith("55"):
        digits = digits[2:]
    if len(digits) < 8:
        return ""
    return digits[:11] if len(digits) > 11 else digits


def emails_from_text(value):
    text = str(value or "")
    found = re.findall(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", text, flags=re.I)
    out = []
    seen = set()
    for email in found:
        item = email.strip(".,;:()[]{}<>").lower()
        if item not in seen:
            out.append(item)
            seen.add(item)
    return out


def phones_from_text(value):
    text = str(value or "")
    candidates = re.findall(r"(?:\+?55\s*)?(?:\(?\d{2}\)?\s*)?\d{4,5}[-.\s]?\d{4}", text)
    out = []
    seen = set()
    for candidate in candidates:
        phone = clean_phone(candidate)
        if phone and phone not in seen:
            out.append(phone)
            seen.add(phone)
    return out


def clean_url(value):
    text = compact(value)
    if not text:
        return ""
    urls = re.findall(r"https?://[^\s\"'<>]+|www\.[^\s\"'<>]+", text, flags=re.I)
    if urls:
        text = urls[0]
    if text.lower().startswith(("mailto:", "tel:")):
        return ""
    if text.startswith("www."):
        text = f"https://{text}"
    if not text.lower().startswith(("http://", "https://")):
        return ""
    text = text.strip(".,;:()[]{}<>\"'")
    parsed = urlparse(text)
    if not parsed.netloc:
        return ""
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, "", parsed.query, ""))


def first_url(*values):
    for value in values:
        url = clean_url(value)
        if url:
            return url
    return ""


def site_from_email(email):
    emails = emails_from_text(email)
    if not emails:
        return ""
    domain = emails[0].split("@", 1)[1].lower()
    if domain in PERSONAL_EMAIL_DOMAINS:
        return ""
    return f"https://{domain}"


def excel_date(value):
    if isinstance(value, dt.datetime):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, dt.date):
        return value.strftime("%d/%m/%Y")
    text = compact(value)
    if not text:
        return ""
    if re.fullmatch(r"\d+(?:\.0+)?", text):
        try:
            number = int(float(text))
            if 20000 <= number <= 80000:
                date = dt.datetime(1899, 12, 30) + dt.timedelta(days=number)
                return date.strftime("%d/%m/%Y")
        except (OverflowError, ValueError):
            pass
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%Y/%m/%d", "%d/%m/%y"):
        try:
            return dt.datetime.strptime(text[:10], fmt).strftime("%d/%m/%Y")
        except ValueError:
            pass
    return text


def infer_segment(row):
    text = normalize_text(
        " ".join(
            [
                row.get("segmento", ""),
                row.get("categoria", ""),
                row.get("fonte", ""),
                row.get("lead", ""),
                row.get("empresa", ""),
            ]
        )
    )
    if any(item in text for item in ("hospital", "clinica", "medic", "saude", "laboratorio", "odontolog")):
        return "Hospitais e clinicas"
    if any(item in text for item in ("administr", "condominio", "sindico", "apartamento")):
        return "Administradoras e condominios"
    if any(item in text for item in ("construt", "incorpor", "imobili", "empreendimento")):
        return "Construtoras e incorporadoras"
    if any(item in text for item in ("facilities", "manutencao", "limpeza", "seguranca patrimonial")):
        return "Facilities e manutencao"
    if any(item in text for item in ("engenharia", "arquitetura", "projeto")):
        return "Engenharia e arquitetura"
    if any(item in text for item in ("faculdade", "universidade", "escola", "colegio", "ensino")):
        return "Instituicoes"
    if any(item in text for item in ("industria", "logistica", "galpao")):
        return "Industria e logistica"
    return row.get("segmento", "")


def score_row(row):
    score = 25
    if row.get("numero"):
        score += 20
    if row.get("email"):
        score += 15
    if row.get("site"):
        score += 10
    if row.get("link") or row.get("perfil"):
        score += 10
    if row.get("endereco") or row.get("cidade"):
        score += 5
    if row.get("segmento"):
        score += 5
    if row.get("avaliacao"):
        score += 5
    return min(score, 100)


def priority_from_score(score):
    try:
        number = int(float(str(score or 0).replace(",", ".")))
    except ValueError:
        number = 0
    if number >= 70:
        return "Quente"
    if number >= 45:
        return "Morno"
    return "Frio"


def enrich_row(row):
    item = {field: compact(row.get(field, "")) for field in FIELDNAMES}
    item["email"] = (emails_from_text(item.get("email")) or [""])[0]
    item["numero"] = clean_phone(item.get("numero")) or clean_phone(row.get("telefone", ""))
    item["link"] = clean_url(item.get("link"))
    item["perfil"] = clean_url(item.get("perfil")) or item["link"]
    item["site"] = clean_url(item.get("site")) or site_from_email(item["email"])
    item["data"] = excel_date(item.get("data")) or TODAY_BR
    item["status"] = item.get("status") or "Captado"
    item["fonte"] = item.get("fonte") or item.get("origem_base") or "Importado"
    item["lead"] = item.get("lead") or item.get("nome") or item.get("empresa") or item.get("email") or item.get("numero") or item.get("perfil")
    item["nome"] = item.get("nome") or item.get("lead")
    item["empresa"] = item.get("empresa") or item.get("nome")
    segment = infer_segment({**dict(row), **item})
    item["valor"] = item.get("valor") or VALUE_BY_SEGMENT.get(segment, "2500")
    item["prioridade"] = item.get("prioridade") or priority_from_score(score_row({**dict(row), **item}))
    return item


def add_lead(rows, **kwargs):
    row = {field: "" for field in FIELDNAMES}
    row.update(kwargs)
    rows.append(enrich_row(row))


def canonical_key_url(value):
    url = clean_url(value)
    if not url:
        return ""
    parsed = urlparse(url)
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = re.sub(r"/+", "/", parsed.path).rstrip("/")
    return urlunparse((parsed.scheme.lower(), netloc, path, "", "", "")).lower()


def row_keys(row):
    keys = []
    for col in ("link", "perfil"):
        url = canonical_key_url(row.get(col))
        if url:
            keys.append(("url", url))
    email = (emails_from_text(row.get("email")) or [""])[0]
    if email:
        keys.append(("email", email))
    phone = clean_phone(row.get("numero"))
    if phone:
        keys.append(("telefone", phone))
    site = canonical_key_url(row.get("site"))
    if site and "google.com/maps" not in site and "maps.app.goo.gl" not in site:
        keys.append(("site", site))
    cnpj = re.sub(r"\D", "", compact(row.get("cnpj")))
    if len(cnpj) == 14:
        keys.append(("cnpj", cnpj))
    name = normalize_text(row.get("nome") or row.get("lead") or row.get("empresa"))
    city = normalize_text(row.get("cidade"))
    if name and city and len(name) >= 4:
        keys.append(("nome_cidade", name, city))
    return list(dict.fromkeys(keys))


def unique_join(values, sep="; "):
    out = []
    seen = set()
    for value in values:
        text = compact(value)
        marker = normalize_text(text)
        if text and marker not in seen:
            out.append(text)
            seen.add(marker)
    return sep.join(out)


def merge_rows(current, incoming):
    merged = dict(current)
    incoming = enrich_row(incoming)
    if not merged.get("id") and incoming.get("id"):
        merged["id"] = incoming["id"]
    for field in FIELDNAMES:
        if field in {"id", "origem_base", "observacao"}:
            continue
        if not merged.get(field) and incoming.get(field):
            merged[field] = incoming[field]
    merged["origem_base"] = unique_join([merged.get("origem_base"), incoming.get("origem_base")])
    merged["observacao"] = unique_join([merged.get("observacao"), incoming.get("observacao")], sep=" | ")
    return enrich_row(merged)


def dedupe(rows):
    cleaned = []
    owners = {}
    for raw in rows:
        row = enrich_row(raw)
        keys = row_keys(row)
        match_index = next((owners[key] for key in keys if key in owners), None)
        if match_index is None:
            match_index = len(cleaned)
            cleaned.append(row)
        else:
            cleaned[match_index] = merge_rows(cleaned[match_index], row)
        for key in row_keys(cleaned[match_index]):
            owners[key] = match_index
    return cleaned


def read_existing_csv(path=CSV_PATH):
    path = Path(path)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return [enrich_row(row) for row in csv.DictReader(f)]


def row_from_mapping(raw, source_name):
    row = {field: "" for field in FIELDNAMES}
    extras = []
    values_text = " ".join(compact(value) for value in raw.values())
    for key, value in raw.items():
        text = compact(value)
        if not text:
            continue
        target = HEADER_ALIASES.get(normalize_header(key))
        if target and target in row:
            row[target] = text
        else:
            extras.append(f"{key}: {text}")
    if not row["email"]:
        row["email"] = (emails_from_text(values_text) or [""])[0]
    if not row["numero"]:
        row["numero"] = (phones_from_text(values_text) or [""])[0]
    if not row["link"] and not row["perfil"]:
        row["link"] = first_url(values_text)
    row["origem_base"] = row["origem_base"] or source_name
    row["observacao"] = row["observacao"] or " | ".join(extras[:8])
    if not any(row.get(col) for col in ("lead", "nome", "empresa", "email", "numero", "link", "perfil")):
        return None
    return enrich_row(row)


def rows_from_xlsx(path):
    if not path.exists():
        return []
    try:
        import pandas as pd
    except ImportError:
        return []
    rows = []
    sheets = pd.read_excel(path, sheet_name=None, dtype=str)
    for sheet_name, frame in sheets.items():
        for raw in frame.fillna("").to_dict(orient="records"):
            row = row_from_mapping(raw, f"{path.name}/{sheet_name}")
            if row:
                if not row.get("fonte") or row.get("fonte") == "Importado":
                    row["fonte"] = sheet_name
                rows.append(enrich_row(row))
    return rows


def rows_from_text(path, default_source):
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="ignore")
    rows = []
    for email in emails_from_text(text):
        add_lead(rows, lead=email, nome=email.split("@", 1)[0], fonte="Email", email=email, origem_base=path.name)
    for phone in phones_from_text(text):
        add_lead(rows, lead=f"Telefone {phone}", nome=f"Telefone {phone}", fonte="WhatsApp", numero=phone, origem_base=path.name)
    for url in re.findall(r"https?://[^\s\"'<>]+|www\.[^\s\"'<>]+", text, flags=re.I):
        clean = clean_url(url)
        if not clean:
            continue
        fonte = default_source
        if "linkedin.com" in clean.lower():
            fonte = "LinkedIn"
        elif "instagram.com" in clean.lower():
            fonte = "Instagram"
        name = clean.rstrip("/").split("/")[-1].replace("-", " ").replace("_", " ").title() or clean
        add_lead(rows, lead=name, nome=name, fonte=fonte, link=clean, perfil=clean, origem_base=path.name)
    return rows


def build_from_bases():
    rows = []
    rows.extend(rows_from_xlsx(MAIN_XLSX))
    rows.extend(rows_from_text(ROOT / "linkedin.txt", "LinkedIn"))
    rows.extend(rows_from_text(ROOT / "instagram.txt", "Instagram"))
    return dedupe(rows)


def write_outputs(rows):
    DASHBOARD.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = CSV_PATH.with_name(f"{CSV_PATH.stem}_backup_{timestamp}.csv")
    if CSV_PATH.exists():
        shutil.copy2(CSV_PATH, backup)
    rows = [enrich_row(row) for row in rows]
    with CSV_PATH.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    counts = {}
    for row in rows:
        fonte = row.get("fonte") or "Nao informado"
        counts[fonte] = counts.get(fonte, 0) + 1

    report = DASHBOARD / "relatorio_melhoria_csv.txt"
    lines = [
        f"Gerado em: {dt.datetime.now().strftime('%d/%m/%Y %H:%M:%S')}",
        f"CSV atualizado: {CSV_PATH}",
        f"Backup criado: {backup}",
        f"Total de leads finais: {len(rows)}",
        "",
        "Leads por fonte:",
    ]
    for fonte, count in sorted(counts.items(), key=lambda item: item[1], reverse=True):
        lines.append(f"- {fonte}: {count}")
    report.write_text("\n".join(lines), encoding="utf-8")
    return backup, report, counts
