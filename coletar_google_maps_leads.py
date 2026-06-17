import argparse
import csv
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from urllib import error, parse, request
from urllib.parse import urlparse

import melhorar_csv_bases as base


ROOT = Path(__file__).resolve().parent
CSV_PATH = ROOT / "dashboard" / "leads_dashboard_melhorado.csv"
INBOX = ROOT / "entrada_periodica"
PROCESSED = INBOX / "processados"
LOG_DIR = ROOT / "logs"
ENV_PATH = ROOT / ".env"

DEFAULT_CITY = "Salvador BA"
DEFAULT_LANGUAGE = "pt-BR"
DEFAULT_REGION = "BR"
PLACES_TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"

FIELD_MASK = ",".join(
    [
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.nationalPhoneNumber",
        "places.internationalPhoneNumber",
        "places.websiteUri",
        "places.googleMapsUri",
        "places.rating",
        "places.userRatingCount",
        "places.businessStatus",
        "places.types",
        "places.primaryType",
        "places.primaryTypeDisplayName",
        "places.addressComponents",
        "nextPageToken",
    ]
)

TERMS_BY_SEGMENT = {
    "Hospitais e clinicas": [
        "hospital",
        "clinica",
        "clinica medica",
        "centro medico",
        "laboratorio clinico",
        "diagnostico por imagem",
        "clinica odontologica",
    ],
    "Administradoras e condominios": [
        "administradora de condominios",
        "administracao de condominios",
        "adm de condominio",
        "sindico profissional",
        "condominio residencial",
        "condominio empresarial",
        "complexo de apartamentos",
    ],
    "Construtoras e incorporadoras": [
        "construtora",
        "incorporadora",
        "construcao civil",
        "empreendimento imobiliario",
        "loteadora",
        "imobiliaria alto padrao",
    ],
    "Facilities e manutencao": [
        "manutencao predial",
        "facilities",
        "limpeza predial",
        "seguranca patrimonial",
        "automacao predial",
    ],
    "Engenharia e arquitetura": [
        "engenharia",
        "engenharia eletrica",
        "engenharia mecanica",
        "arquitetura",
        "projetos prediais",
    ],
    "Instituicoes": [
        "instituicao de ensino",
        "faculdade",
        "universidade",
        "escola tecnica",
        "colegio",
    ],
    "Industria e logistica": [
        "industria",
        "centro logistico",
        "galpao logistico",
        "operador logistico",
    ],
}

FAST_TERMS_BY_SEGMENT = {
    "Hospitais e clinicas": [
        "hospital",
        "clinica",
        "centro medico",
    ],
    "Administradoras e condominios": [
        "administradora de condominios",
        "condominio residencial",
        "sindico profissional",
    ],
    "Construtoras e incorporadoras": [
        "construtora",
        "incorporadora",
    ],
    "Facilities e manutencao": [
        "manutencao predial",
        "facilities",
    ],
    "Engenharia e arquitetura": [
        "engenharia",
        "arquitetura",
    ],
    "Instituicoes": [
        "faculdade",
        "escola tecnica",
    ],
    "Industria e logistica": [
        "industria",
        "centro logistico",
    ],
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

TYPE_LABELS = {
    "accounting": "Contabilidade",
    "apartment_building": "Condominio",
    "condominium_complex": "Complexo de condominio",
    "corporate_office": "Escritorio da empresa",
    "dentist": "Clinica odontologica",
    "doctor": "Clinica medica",
    "general_contractor": "Construtora",
    "health": "Saude",
    "hospital": "Hospital",
    "real_estate_agency": "Imobiliaria",
    "school": "Instituicao de ensino",
    "university": "Universidade",
}


@dataclass(frozen=True)
class SearchJob:
    segment: str
    term: str
    city: str

    @property
    def query(self):
        return f"{self.term} em {self.city}"


def log(message):
    print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] {message}", flush=True)


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


def normalize_text(value):
    text = str(value or "").strip().lower()
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_name(value):
    text = normalize_text(value)
    text = re.sub(r"\b(ltda|me|epp|eireli|sa|s a|ss|mei)\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def clean_url(value):
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("www."):
        text = f"https://{text}"
    return text if text.startswith(("http://", "https://")) else ""


def canonical_site(value):
    url = clean_url(value)
    if not url:
        return ""
    parsed = urlparse(url)
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = parsed.path.rstrip("/")
    return f"{netloc}{path}".lower()


def read_csv_rows(path):
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def read_pending_inbox_rows(path=INBOX):
    if not path.exists():
        return []
    rows = []
    for item in sorted(path.iterdir()):
        if item.is_file() and item.suffix.lower() == ".csv":
            rows.extend(read_csv_rows(item))
    return rows


def load_supabase_base_rows(enabled=True, return_details=False):
    if not enabled:
        return ([], [], None, None) if return_details else []
    try:
        import automacao_supabase_leads as sync_base

        sync_base.load_env()
        supabase_url, supabase_key, key_source = sync_base.resolve_supabase_config()
        if not supabase_key:
            log("Supabase sem chave disponivel; dedupe inicial usara apenas CSV local.")
            return ([], [], None, sync_base) if return_details else []
        client = sync_base.SupabaseRest(
            supabase_url,
            supabase_key,
            os.environ.get("SUPABASE_TABLE", sync_base.DEFAULT_TABLE),
        )
        log(f"Checando Supabase antes da coleta usando chave de {key_source}.")
        raw_rows = client.fetch_all()
        rows = [sync_base.normalize_remote_row(row) for row in raw_rows]
        log(f"Supabase carregado para ignorar duplicados: {len(rows)} registros.")
        if return_details:
            return rows, raw_rows, client, sync_base
        return rows
    except Exception as exc:
        log(f"Nao consegui carregar Supabase para dedupe inicial: {exc}. Vou usar apenas CSV local.")
        return ([], [], None, None) if return_details else []


def extract_place_ids(value):
    text = parse.unquote(str(value or ""))
    patterns = [
        r"place_id[:=]([A-Za-z0-9_-]+)",
        r"[!&](?:1s|19s)(ChI[A-Za-z0-9_-]+)",
        r"\b(ChI[A-Za-z0-9_-]{12,})\b",
    ]
    found = []
    for pattern in patterns:
        for match in re.findall(pattern, text):
            if match not in found:
                found.append(match)
    return found


def maps_search_page_url(query, language_code):
    return f"https://www.google.com/maps/search/{parse.quote(query)}?hl={parse.quote(language_code)}"


def maps_place_url(name, place_id):
    if not place_id:
        return ""
    query = parse.quote(name or place_id)
    place = parse.quote(place_id)
    return f"https://www.google.com/maps/search/?api=1&query={query}&query_place_id={place}"


def normalize_maps_url(url):
    text = clean_url(url)
    if not text:
        return ""
    if "google.com/maps" in text:
        parsed = urlparse(text)
        return parse.urlunparse((parsed.scheme, parsed.netloc.lower(), parsed.path.rstrip("/"), "", "", ""))
    text = text.split("&ved=", 1)[0]
    text = text.split("&sa=", 1)[0]
    return text


def existing_index(rows):
    index = {
        "place_ids": set(),
        "phones": set(),
        "sites": set(),
        "name_city": set(),
        "maps_urls": set(),
    }
    for row in rows:
        for col in ("link", "perfil", "site", "observacao"):
            index["place_ids"].update(extract_place_ids(row.get(col)))
        for col in ("link", "perfil"):
            url = normalize_maps_url(row.get(col))
            if "google.com/maps" in url:
                index["maps_urls"].add(url)
        phone = base.clean_phone(row.get("numero")) or base.clean_phone(row.get("telefone"))
        if phone:
            index["phones"].add(phone)
        site = canonical_site(row.get("site"))
        if site and "google.com/maps" not in site and "maps.app.goo.gl" not in site:
            index["sites"].add(site)
        name = normalize_name(row.get("nome") or row.get("lead") or row.get("empresa"))
        city = normalize_text(row.get("cidade"))
        if name and city:
            index["name_city"].add((name, city))
    return index


def place_duplicate_reason(row, place_id, index):
    if place_id and place_id in index["place_ids"]:
        return "place_id"
    maps_url = normalize_maps_url(row.get("link"))
    if maps_url and maps_url in index["maps_urls"]:
        return "maps_url"
    phone = base.clean_phone(row.get("numero"))
    if phone and phone in index["phones"]:
        return "telefone"
    site = canonical_site(row.get("site"))
    if site and site in index["sites"]:
        return "site"
    name = normalize_name(row.get("nome"))
    city = normalize_text(row.get("cidade"))
    if name and city and (name, city) in index["name_city"]:
        return "nome+cidade"
    return ""


def link_duplicate_reason(link, index):
    for place_id in extract_place_ids(link):
        if place_id in index["place_ids"]:
            return "place_id"
    maps_url = normalize_maps_url(link)
    if maps_url and maps_url in index["maps_urls"]:
        return "maps_url"
    return ""


def remember_row(row, place_id, index):
    if place_id:
        index["place_ids"].add(place_id)
    maps_url = normalize_maps_url(row.get("link"))
    if maps_url:
        index["maps_urls"].add(maps_url)
    phone = base.clean_phone(row.get("numero"))
    if phone:
        index["phones"].add(phone)
    site = canonical_site(row.get("site"))
    if site and "google.com/maps" not in site and "maps.app.goo.gl" not in site:
        index["sites"].add(site)
    name = normalize_name(row.get("nome"))
    city = normalize_text(row.get("cidade"))
    if name and city:
        index["name_city"].add((name, city))


def segment_for_csv_term(term):
    text = normalize_text(term)
    if any(x in text for x in ("hospital", "clinica", "medic", "saude", "laboratorio")):
        return "Hospitais e clinicas"
    if any(x in text for x in ("administr", "condominio", "sindico", "apartamento")):
        return "Administradoras e condominios"
    if any(x in text for x in ("construt", "incorpor", "imobili", "empreendimento")):
        return "Construtoras e incorporadoras"
    if any(x in text for x in ("facilities", "manutencao", "limpeza", "seguranca")):
        return "Facilities e manutencao"
    if any(x in text for x in ("engenharia", "arquitetura", "projeto")):
        return "Engenharia e arquitetura"
    if any(x in text for x in ("instituicao", "faculdade", "universidade", "escola", "colegio")):
        return "Instituicoes"
    if any(x in text for x in ("industria", "logistica", "galpao")):
        return "Industria e logistica"
    return ""


def csv_terms(rows, max_terms):
    counts = Counter()
    for row in rows:
        for col in ("categoria", "segmento", "fonte"):
            value = base.compact(row.get(col, ""))
            if value:
                segment = segment_for_csv_term(value)
                if segment:
                    counts[(segment, value)] += 1
    return [(segment, term) for (segment, term), _count in counts.most_common(max_terms)]


def selected_segments(raw_segments):
    if not raw_segments:
        return list(TERMS_BY_SEGMENT.keys())
    lookup = {normalize_text(name): name for name in TERMS_BY_SEGMENT}
    picked = []
    for raw in raw_segments:
        key = normalize_text(raw)
        if key not in lookup:
            valid = ", ".join(TERMS_BY_SEGMENT)
            raise SystemExit(f"Segmento desconhecido: {raw}. Use um destes: {valid}")
        picked.append(lookup[key])
    return list(dict.fromkeys(picked))


def infer_csv_locations(rows, max_locations):
    counts = Counter()
    for row in rows:
        city = base.compact(row.get("cidade", ""))
        state = base.compact(row.get("estado", ""))
        if city:
            counts[f"{city} {state}".strip()] += 1
    return [label for label, _count in counts.most_common(max_locations)]


def build_jobs(args, rows):
    cities = list(args.cidades or [])
    if args.usar_cidades_csv:
        cities.extend(infer_csv_locations(rows, args.max_cidades_csv))
    if not cities:
        cities = [args.cidade_padrao]
    cities = list(dict.fromkeys(base.compact(city) for city in cities if base.compact(city)))

    terms = []
    if not args.somente_termos:
        terms_by_segment = TERMS_BY_SEGMENT if args.perfil == "completo" else FAST_TERMS_BY_SEGMENT
        for segment in selected_segments(args.segmentos):
            for term in terms_by_segment[segment]:
                terms.append((segment, term))
    if args.usar_termos_csv:
        terms.extend(csv_terms(rows, args.max_termos_csv))
    for term in args.termos or []:
        segment = segment_for_csv_term(term) or "Empresas gerais"
        terms.append((segment, term))

    if args.somente_termos and not terms:
        raise SystemExit("Informe pelo menos um --termo quando usar --somente-termos.")

    seen_terms = set()
    unique_terms = []
    for segment, term in terms:
        marker = (segment, normalize_text(term))
        if marker not in seen_terms:
            unique_terms.append((segment, term))
            seen_terms.add(marker)

    jobs = [SearchJob(segment, term, city) for city in cities for segment, term in unique_terms]
    if args.limite_consultas:
        jobs = jobs[: args.limite_consultas]
    return jobs


def import_playwright():
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SystemExit(
            "Playwright nao esta instalado neste Python. Rode com Python 3.13 deste Windows "
            "ou instale: py -3.13 -m pip install playwright && py -3.13 -m playwright install chromium"
        ) from exc
    return sync_playwright, PlaywrightTimeoutError


def safe_count(locator):
    try:
        return locator.count()
    except Exception:
        return 0


def first_text(page, selectors, timeout=900):
    for selector in selectors:
        locator = page.locator(selector).first
        if not safe_count(locator):
            continue
        try:
            text = locator.inner_text(timeout=timeout).strip()
        except Exception:
            text = ""
        if text:
            return re.sub(r"\s+", " ", text)
    return ""


def first_attr(page, selectors, attr, timeout=900):
    for selector in selectors:
        locator = page.locator(selector).first
        if not safe_count(locator):
            continue
        try:
            value = locator.get_attribute(attr, timeout=timeout)
        except Exception:
            value = ""
        if value:
            return value.strip()
    return ""


def strip_label(value):
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(
        r"^(Endereco|Endere[cç]o|Address|Telefone|Phone|Website|Site|Categoria|Category):\s*",
        "",
        text,
        flags=re.I,
    )
    return text.strip()


def parse_rating_and_count(*values):
    text = " ".join(str(value or "") for value in values)
    rating = ""
    count = ""
    rating_match = re.search(r"(\d+(?:[,.]\d+)?)\s*(?:estrel|star|stars)", text, flags=re.I)
    if not rating_match:
        rating_match = re.search(r"\b(\d+[,.]\d+)\b", text)
    if rating_match:
        rating = rating_match.group(1).replace(".", ",")
    count_match = re.search(r"([\d.,]+)\s*(?:avaliacoes|avaliacao|reviews|review)", text, flags=re.I)
    if count_match:
        count = re.sub(r"\D", "", count_match.group(1))
    return rating, count


def safe_int(value):
    digits = re.sub(r"\D", "", str(value or ""))
    return int(digits) if digits else 0


def safe_float(value):
    text = str(value or "").replace(",", ".").strip()
    match = re.search(r"\d+(?:\.\d+)?", text)
    return float(match.group(0)) if match else 0.0


def city_state_from_address(address, fallback_city):
    city = ""
    state = ""
    text = re.sub(r"\s+", " ", str(address or ""))
    patterns = [
        r"[-,]\s*([^,-]+?)\s*-\s*([A-Z]{2})\b",
        r"\b([^,-]+?),\s*([A-Z]{2})\s*\d{5}",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            city = match.group(1).strip()
            state = match.group(2).strip()
    if not city:
        match = re.search(r"(.+?)\s+([A-Z]{2})$", fallback_city.strip())
        if match:
            city = match.group(1).strip()
            state = match.group(2).strip()
        else:
            city = fallback_city.strip()
    return city, state


def category_from_page(page, fallback):
    selectors = [
        'button[jsaction*="pane.rating.category"]',
        'button[aria-label*="Categoria"]',
        'button[aria-label*="Category"]',
        'button.DkEaL',
    ]
    text = first_text(page, selectors)
    text = strip_label(text)
    if text and len(text) <= 80:
        return text
    return fallback.replace("_", " ").title()


def accept_consent(page):
    names = [
        r"Aceitar tudo",
        r"Accept all",
        r"Concordo",
        r"I agree",
        r"Rejeitar tudo",
        r"Reject all",
    ]
    for name in names:
        try:
            page.get_by_role("button", name=re.compile(name, re.I)).click(timeout=1500)
            page.wait_for_timeout(700)
            return
        except Exception:
            pass


def launch_browser(playwright, args):
    launch_kwargs = {
        "headless": args.headless,
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    if args.browser_channel:
        launch_kwargs["channel"] = args.browser_channel
    try:
        return playwright.chromium.launch(**launch_kwargs)
    except Exception as exc:
        if args.browser_channel:
            log(f"Nao consegui abrir o canal {args.browser_channel}; tentando Chromium padrao.")
            launch_kwargs.pop("channel", None)
            try:
                return playwright.chromium.launch(**launch_kwargs)
            except Exception:
                pass
        raise SystemExit(
            "Nao consegui abrir o navegador do Playwright. Tente instalar o Chromium: "
            "py -3.13 -m playwright install chromium"
        ) from exc


def city_state_from_components(components):
    city = ""
    state = ""
    for component in components or []:
        types = set(component.get("types") or [])
        long_text = component.get("longText") or component.get("shortText") or ""
        short_text = component.get("shortText") or long_text
        if not city and ("locality" in types or "administrative_area_level_2" in types):
            city = long_text
        if not state and "administrative_area_level_1" in types:
            state = short_text
    return city, state


def display_name(place):
    value = place.get("displayName")
    if isinstance(value, dict):
        return value.get("text", "")
    return str(value or "")


def primary_type_label(place):
    display = place.get("primaryTypeDisplayName")
    if isinstance(display, dict) and display.get("text"):
        return display["text"]
    primary = place.get("primaryType") or ""
    if primary in TYPE_LABELS:
        return TYPE_LABELS[primary]
    for place_type in place.get("types") or []:
        if place_type in TYPE_LABELS:
            return TYPE_LABELS[place_type]
    if primary:
        return primary.replace("_", " ").title()
    types = place.get("types") or []
    return types[0].replace("_", " ").title() if types else ""


def places_post(payload, api_key, timeout):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        PLACES_TEXT_SEARCH_URL,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": FIELD_MASK,
        },
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Google Places HTTP {exc.code}: {body[:800]}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Falha de rede no Google Places: {exc}") from exc


def search_places_api(job, api_key, args):
    places = []
    next_token = ""
    for _page in range(args.paginas):
        payload = {
            "textQuery": job.query,
            "languageCode": args.language_code,
            "regionCode": args.region_code,
            "pageSize": args.page_size,
        }
        if next_token:
            payload["pageToken"] = next_token

        for attempt in range(1, args.tentativas + 1):
            try:
                data = places_post(payload, api_key, args.timeout)
                break
            except RuntimeError:
                if attempt >= args.tentativas:
                    raise
                time.sleep(args.pausa_erro * attempt)

        places.extend(data.get("places") or [])
        next_token = data.get("nextPageToken") or ""
        if not next_token:
            break
        time.sleep(args.pausa_pagina)
    return places


def api_place_to_row(place, job):
    place_id = place.get("id") or ""
    name = display_name(place)
    city, state = city_state_from_components(place.get("addressComponents"))
    phone = base.clean_phone(place.get("nationalPhoneNumber")) or base.clean_phone(place.get("internationalPhoneNumber"))
    maps_url = clean_url(place.get("googleMapsUri")) or maps_place_url(name, place_id)
    website = clean_url(place.get("websiteUri"))
    rating = place.get("rating")
    rating_count = place.get("userRatingCount")
    status = place.get("businessStatus") or ""
    types = place.get("types") or []
    obs_parts = [
        f"busca: {job.query}",
        f"place_id: {place_id}" if place_id else "",
        f"status_maps: {status}" if status else "",
        f"tipos: {', '.join(types[:8])}" if types else "",
        "coleta: Google Places API",
    ]

    row = {field: "" for field in base.FIELDNAMES}
    row.update(
        {
            "lead": name,
            "nome": name,
            "fonte": "Google Maps API",
            "status": "Captado",
            "valor": VALUE_BY_SEGMENT.get(job.segment, "5000"),
            "data": base.TODAY_BR,
            "link": maps_url,
            "perfil": maps_url,
            "numero": phone,
            "categoria": primary_type_label(place),
            "segmento": job.segment,
            "empresa": name,
            "site": website,
            "endereco": place.get("formattedAddress") or "",
            "cidade": city,
            "estado": state,
            "avaliacao": "" if rating is None else str(rating).replace(".", ","),
            "qtd_avaliacoes": "" if rating_count is None else str(rating_count),
            "origem_base": "Google Places API",
            "observacao": " | ".join(part for part in obs_parts if part),
        }
    )
    return base.enrich_row(row), place_id


def collect_rows_api(args, jobs, base_rows):
    api_key = args.api_key or os.environ.get("GOOGLE_MAPS_API_KEY") or os.environ.get("GOOGLE_PLACES_API_KEY")
    if not api_key:
        raise SystemExit("Defina GOOGLE_MAPS_API_KEY no .env ou passe --api-key para usar --modo api.")

    started_at = time.monotonic()
    searches_without_new = 0
    index = existing_index(base_rows)
    rows = []
    stats = {
        "consultas": 0,
        "lugares_recebidos": 0,
        "novos": 0,
        "duplicados": 0,
        "duplicados_pre_ficha": 0,
        "fechados_ignorados": 0,
        "sem_nome": 0,
        "erros": 0,
        "novos_por_consulta": {},
    }

    for job in jobs:
        if args.max_segundos and time.monotonic() - started_at >= args.max_segundos:
            log(f"Tempo maximo atingido ({args.max_segundos}s). Encerrando coleta.")
            break
        stats["consultas"] += 1
        log(f"Buscando via API: {job.query}")
        before_job = stats["novos"]
        try:
            places = search_places_api(job, api_key, args)
        except Exception as exc:
            stats["erros"] += 1
            log(f"Erro na busca '{job.query}': {exc}")
            if args.parar_apos_erros and stats["erros"] >= args.parar_apos_erros:
                log(f"Limite de erros atingido ({args.parar_apos_erros}). Encerrando coleta.")
                break
            continue

        stats["lugares_recebidos"] += len(places)
        for place in places:
            if args.somente_ativos and place.get("businessStatus") == "CLOSED_PERMANENTLY":
                stats["fechados_ignorados"] += 1
                continue
            row, place_id = api_place_to_row(place, job)
            if not row.get("nome"):
                stats["sem_nome"] += 1
                continue
            if args.exigir_telefone and not row.get("numero"):
                continue
            if args.min_avaliacoes and safe_int(row.get("qtd_avaliacoes")) < args.min_avaliacoes:
                continue
            if args.min_nota and safe_float(row.get("avaliacao")) < args.min_nota:
                continue

            reason = place_duplicate_reason(row, place_id, index)
            if reason:
                stats["duplicados"] += 1
                continue

            remember_row(row, place_id, index)
            rows.append(row)
            persist_new_row(args, row)
            stats["novos"] += 1
            log(f"Novo lead: {row.get('nome')} | {row.get('numero') or 'sem telefone'}")
            if args.min_novos_por_consulta and stats["novos"] - before_job >= args.min_novos_por_consulta:
                break
            if args.limite_total and len(rows) >= args.limite_total:
                return rows, stats

        if stats["novos"] == before_job:
            searches_without_new += 1
            if args.parar_sem_novos and searches_without_new >= args.parar_sem_novos:
                log(f"{searches_without_new} buscas seguidas sem novos leads. Encerrando coleta.")
                break
        else:
            searches_without_new = 0
        job_new = stats["novos"] - before_job
        stats["novos_por_consulta"][job.query] = job_new
        if args.min_novos_por_consulta and job_new < args.min_novos_por_consulta:
            log(f"Meta nao atingida para '{job.query}': {job_new}/{args.min_novos_por_consulta} novos apos candidatos disponiveis.")
        elif args.min_novos_por_consulta:
            log(f"Meta atingida para '{job.query}': {job_new}/{args.min_novos_por_consulta} novos.")
    return rows, stats


def collect_result_links(page, args):
    urls = []
    no_new_rounds = 0
    for _round in range(args.max_scrolls):
        found = page.evaluate(
            """
            () => Array.from(document.querySelectorAll('a[href]'))
              .map(a => a.href)
              .filter(h => h.includes('/maps/place/'))
            """
        )
        before = len(urls)
        for href in found:
            url = normalize_maps_url(href)
            if url and url not in urls:
                urls.append(url)
                if len(urls) >= args.max_resultados_consulta:
                    return urls
        if len(urls) == before:
            no_new_rounds += 1
        else:
            no_new_rounds = 0
        if no_new_rounds >= 3 and urls:
            break

        feed = page.locator('div[role="feed"]').first
        if safe_count(feed):
            try:
                feed.evaluate("(el) => { el.scrollTop = el.scrollHeight; }")
            except Exception:
                page.mouse.wheel(0, 2600)
        else:
            page.mouse.wheel(0, 2600)
        page.wait_for_timeout(int(args.pausa_scroll * 1000))
    return urls


def search_maps_links(page, job, args):
    url = maps_search_page_url(job.query, args.language_code)
    page.goto(url, wait_until="domcontentloaded", timeout=args.timeout * 1000)
    accept_consent(page)
    try:
        page.wait_for_load_state("networkidle", timeout=7000)
    except Exception:
        pass
    page.wait_for_timeout(int(args.pausa_busca * 1000))

    links = collect_result_links(page, args)
    if not links and "/maps/place/" in page.url:
        links = [normalize_maps_url(page.url)]
    return links


def extract_detail(page, url, job, args):
    page.goto(url, wait_until="domcontentloaded", timeout=args.timeout * 1000)
    accept_consent(page)
    try:
        page.wait_for_selector("h1", timeout=args.timeout * 1000)
    except Exception:
        pass
    try:
        page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass
    page.wait_for_timeout(int(args.pausa_ficha * 1000))

    name = first_text(page, ["h1"])
    if name:
        name = name.splitlines()[0].strip()
    address_label = first_attr(page, ['button[data-item-id="address"]'], "aria-label")
    address_text = first_text(page, ['button[data-item-id="address"]'])
    address = strip_label(address_label or address_text)

    phone_label = first_attr(
        page,
        ['button[data-item-id^="phone"]', 'button[aria-label*="Telefone"]', 'button[aria-label*="Phone"]'],
        "aria-label",
    )
    phone_text = first_text(page, ['button[data-item-id^="phone"]'])
    phone = base.clean_phone(strip_label(phone_label or phone_text))

    website = first_attr(page, ['a[data-item-id="authority"]', 'a[aria-label*="Website"]', 'a[aria-label*="Site"]'], "href")
    website = clean_url(website)

    category = category_from_page(page, job.term)
    rating_label = first_attr(
        page,
        ['div[role="img"][aria-label*="estrel"]', 'span[role="img"][aria-label*="estrel"]', 'div[role="img"][aria-label*="star"]'],
        "aria-label",
    )
    reviews_text = first_text(page, ['button[aria-label*="avali"]', 'button[aria-label*="review"]'])
    visible_text = first_text(page, ['div[role="main"]'], timeout=1200)
    rating, rating_count = parse_rating_and_count(rating_label, reviews_text, visible_text[:1500])
    city, state = city_state_from_address(address, job.city)
    maps_url = normalize_maps_url(page.url)
    place_ids = extract_place_ids(maps_url)
    place_id = place_ids[0] if place_ids else ""

    obs_parts = [
        f"busca: {job.query}",
        f"place_id: {place_id}" if place_id else "",
        "coleta: navegador Google Maps sem API",
    ]

    row = {field: "" for field in base.FIELDNAMES}
    row.update(
        {
            "lead": name,
            "nome": name,
            "fonte": "Google Maps",
            "status": "Captado",
            "valor": VALUE_BY_SEGMENT.get(job.segment, "5000"),
            "data": base.TODAY_BR,
            "link": maps_url,
            "perfil": maps_url,
            "numero": phone,
            "categoria": category,
            "segmento": job.segment,
            "empresa": name,
            "site": website,
            "endereco": address,
            "cidade": city,
            "estado": state,
            "avaliacao": rating,
            "qtd_avaliacoes": rating_count,
            "origem_base": "Google Maps navegador",
            "observacao": " | ".join(obs_parts),
        }
    )
    return base.enrich_row(row), place_id


def collect_rows_browser(args, jobs, base_rows):
    sync_playwright, _timeout_error = import_playwright()
    started_at = time.monotonic()
    searches_without_new = 0
    index = existing_index(base_rows)
    rows = []
    stats = {
        "consultas": 0,
        "links_encontrados": 0,
        "fichas_lidas": 0,
        "novos": 0,
        "duplicados": 0,
        "duplicados_pre_ficha": 0,
        "sem_nome": 0,
        "erros": 0,
        "novos_por_consulta": {},
    }

    with sync_playwright() as playwright:
        browser = None
        context = None
        try:
            browser = launch_browser(playwright, args)
            context = browser.new_context(
                locale=args.language_code,
                viewport={"width": 1366, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
                ),
            )
            page = context.new_page()

            for job in jobs:
                if args.max_segundos and time.monotonic() - started_at >= args.max_segundos:
                    log(f"Tempo maximo atingido ({args.max_segundos}s). Encerrando coleta.")
                    break
                stats["consultas"] += 1
                log(f"Buscando: {job.query}")
                before_job = stats["novos"]
                try:
                    links = search_maps_links(page, job, args)
                except Exception as exc:
                    stats["erros"] += 1
                    log(f"Erro na busca '{job.query}': {exc}")
                    if args.parar_apos_erros and stats["erros"] >= args.parar_apos_erros:
                        log(f"Limite de erros atingido ({args.parar_apos_erros}). Encerrando coleta.")
                        break
                    continue

                stats["links_encontrados"] += len(links)
                log(f"{len(links)} fichas candidatas encontradas.")
                for link in links:
                    reason = link_duplicate_reason(link, index)
                    if reason:
                        stats["duplicados_pre_ficha"] += 1
                        stats["duplicados"] += 1
                        continue
                    try:
                        row, place_id = extract_detail(page, link, job, args)
                        stats["fichas_lidas"] += 1
                    except Exception as exc:
                        stats["erros"] += 1
                        log(f"Erro lendo ficha: {exc}")
                        continue

                    if not row.get("nome"):
                        stats["sem_nome"] += 1
                        continue
                    if args.exigir_telefone and not row.get("numero"):
                        continue
                    if args.min_avaliacoes and safe_int(row.get("qtd_avaliacoes")) < args.min_avaliacoes:
                        continue
                    if args.min_nota and safe_float(row.get("avaliacao")) < args.min_nota:
                        continue

                    reason = place_duplicate_reason(row, place_id, index)
                    if reason:
                        stats["duplicados"] += 1
                        continue

                    remember_row(row, place_id, index)
                    rows.append(row)
                    persist_new_row(args, row)
                    stats["novos"] += 1
                    log(f"Novo lead: {row.get('nome')} | {row.get('numero') or 'sem telefone'}")
                    if args.min_novos_por_consulta and stats["novos"] - before_job >= args.min_novos_por_consulta:
                        break
                    if args.limite_total and len(rows) >= args.limite_total:
                        return rows, stats

                    time.sleep(args.pausa_lead)

                if stats["novos"] == before_job:
                    searches_without_new += 1
                    if args.parar_sem_novos and searches_without_new >= args.parar_sem_novos:
                        log(f"{searches_without_new} buscas seguidas sem novos leads. Encerrando coleta.")
                        break
                else:
                    searches_without_new = 0
                job_new = stats["novos"] - before_job
                stats["novos_por_consulta"][job.query] = job_new
                if args.min_novos_por_consulta and job_new < args.min_novos_por_consulta:
                    log(f"Meta nao atingida para '{job.query}': {job_new}/{args.min_novos_por_consulta} novos apos candidatos disponiveis.")
                elif args.min_novos_por_consulta:
                    log(f"Meta atingida para '{job.query}': {job_new}/{args.min_novos_por_consulta} novos.")
        finally:
            if context:
                context.close()
            if browser:
                browser.close()
    return rows, stats


def collect_rows(args, jobs, base_rows):
    if args.modo == "api":
        return collect_rows_api(args, jobs, base_rows)
    return collect_rows_browser(args, jobs, base_rows)


def write_csv(rows, output_path):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=base.FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def append_csv_row(row, output_path):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not output_path.exists() or output_path.stat().st_size == 0
    with output_path.open("a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=base.FIELDNAMES)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


class DirectUpdater:
    def __init__(self, csv_path, existing_rows, supabase_client=None, sync_module=None, remote_rows=None):
        self.csv_path = Path(csv_path)
        self.supabase_client = supabase_client
        self.sync_module = sync_module
        self.remote_rows = remote_rows or []
        self.columns = sync_module.configured_columns(self.remote_rows) if sync_module else []
        self.next_id = self._next_id(existing_rows)
        self.csv_count = 0
        self.supabase_count = 0
        self.supabase_errors = 0

    def _next_id(self, rows):
        max_id = 0
        for row in rows or []:
            try:
                max_id = max(max_id, int(float(str(row.get("id") or "0"))))
            except ValueError:
                pass
        return max_id + 1

    def append_main_csv(self, row):
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        item = {field: row.get(field, "") for field in base.FIELDNAMES}
        if not item.get("id"):
            item["id"] = str(self.next_id)
            self.next_id += 1
        write_header = not self.csv_path.exists() or self.csv_path.stat().st_size == 0
        encoding = "utf-8-sig" if write_header else "utf-8"
        with self.csv_path.open("a", encoding=encoding, newline="") as f:
            writer = csv.DictWriter(f, fieldnames=base.FIELDNAMES)
            if write_header:
                writer.writeheader()
            writer.writerow(item)
        self.csv_count += 1
        return item

    def insert_supabase(self, row):
        if not self.supabase_client or not self.sync_module:
            return None
        payload = self.sync_module.row_payload(row, self.columns, touch_updated_at=False)
        payload.pop("updated_at", None)
        table = parse.quote(self.supabase_client.table, safe="")
        response = self.supabase_client.request(
            "POST",
            table,
            [payload],
            headers={"Prefer": "return=representation"},
        )
        self.supabase_count += 1
        if isinstance(response, list) and response:
            return response[0]
        return {}

    def persist(self, row):
        item = {field: row.get(field, "") for field in base.FIELDNAMES}
        inserted_remote = None
        supabase_failed = False
        try:
            inserted_remote = self.insert_supabase(item)
        except Exception as exc:
            self.supabase_errors += 1
            supabase_failed = True
            log(f"Supabase falhou para '{item.get('nome')}', salvando no CSV principal para nao perder: {exc}")
        if isinstance(inserted_remote, dict) and inserted_remote.get("id"):
            item["id"] = str(inserted_remote.get("id"))
        item = self.append_main_csv(item)
        if supabase_failed:
            log(f"Atualizado direto: CSV principal | Supabase pendente por erro | {item.get('nome')}")
        elif inserted_remote is None:
            log(f"Atualizado direto: CSV principal | Supabase sem chave/client | {item.get('nome')}")
        elif inserted_remote:
            log(f"Atualizado direto: Supabase + CSV principal | {item.get('nome')}")
        else:
            log(f"Atualizado direto: Supabase + CSV principal | {item.get('nome')}")
        return item

    def stats(self):
        return {
            "csv_principal_inserts": self.csv_count,
            "supabase_inserts_imediatos": self.supabase_count,
            "supabase_erros_imediatos": self.supabase_errors,
        }


def persist_new_row(args, row):
    if getattr(args, "atualizar_direto", False) and getattr(args, "direct_updater", None):
        return args.direct_updater.persist(row)
    if getattr(args, "salvar_imediato", False):
        append_csv_row(row, args.output_path)
    return row


def pending_inbox_files(path=INBOX):
    if not path.exists():
        return []
    return sorted(item for item in path.iterdir() if item.is_file() and item.suffix.lower() == ".csv")


def process_pending_direct(args, known_rows):
    if not getattr(args, "direct_updater", None):
        return []
    files = pending_inbox_files()
    if not files:
        return []

    index = existing_index(known_rows)
    processed = []
    archived = 0
    for path in files:
        rows = read_csv_rows(path)
        new_in_file = 0
        for raw in rows:
            row = base.enrich_row(raw)
            place_ids = []
            for col in ("link", "perfil", "site", "observacao"):
                place_ids.extend(extract_place_ids(row.get(col)))
            place_id = place_ids[0] if place_ids else ""
            reason = place_duplicate_reason(row, place_id, index)
            if reason:
                continue
            persisted = persist_new_row(args, row)
            remember_row(persisted, place_id, index)
            processed.append(persisted)
            new_in_file += 1
        PROCESSED.mkdir(parents=True, exist_ok=True)
        target = PROCESSED / f"{path.stem}_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}{path.suffix}"
        shutil.move(str(path), str(target))
        archived += 1
        log(f"Pendencia processada: {path.name} | novos={new_in_file} | arquivada em {target.name}")

    if processed or archived:
        log(f"Pendencias diretas: {len(processed)} leads novos processados; {archived} arquivos arquivados.")
    return processed


def write_log(args, output_path, jobs, stats):
    LOG_DIR.mkdir(exist_ok=True)
    report = LOG_DIR / f"google_maps_leads_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    payload = {
        "data": dt.datetime.now().isoformat(timespec="seconds"),
        "modo": args.modo,
        "saida": str(output_path),
        "csv_base": str(args.csv_base),
        "consultas_planejadas": [job.query for job in jobs],
        "stats": stats,
    }
    report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def run_sync(args):
    cmd = [sys.executable, str(ROOT / "automacao_supabase_leads.py")]
    if not args.sync_sem_dedupe_forte:
        cmd.append("--dedupe-forte")
    if args.sync_sem_bases_locais:
        cmd.append("--sem-bases-locais")
    if not args.sync_manter_entradas:
        cmd.append("--arquivar-entradas")
    if args.sync_dry_run:
        cmd.append("--dry-run")
    if args.sync_local_only:
        cmd.append("--local-only")
    subprocess.run(cmd, cwd=ROOT, check=True)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Coleta leads no Google Maps sem API de Maps e salva CSV no padrao Supabase."
    )
    parser.add_argument("--modo", choices=["navegador", "api"], default="navegador", help="navegador automatiza Google Maps sem chave; api usa Google Places API.")
    parser.add_argument("--perfil", choices=["rapido", "completo"], default="rapido", help="rapido usa menos termos por segmento; completo varre todos os termos.")
    parser.add_argument("--api-key", help="Chave Google Places, usada apenas com --modo api.")
    parser.add_argument("--csv-base", type=Path, default=CSV_PATH, help="CSV atual usado como base para formato e dedupe.")
    parser.add_argument("--saida", type=Path, help="CSV de saida. Padrao: entrada_periodica/google_maps_leads_DATA.csv")
    parser.add_argument("--cidade", dest="cidades", action="append", help="Cidade/UF para buscar. Pode repetir.")
    parser.add_argument("--cidade-padrao", default=DEFAULT_CITY, help=f"Cidade usada se nenhuma for informada. Padrao: {DEFAULT_CITY}.")
    parser.add_argument("--usar-cidades-csv", action="store_true", help="Tambem usa as cidades mais frequentes do CSV base.")
    parser.add_argument("--max-cidades-csv", type=int, default=5, help="Maximo de cidades inferidas do CSV.")
    parser.add_argument("--segmento", dest="segmentos", action="append", help="Segmento a buscar. Pode repetir.")
    parser.add_argument("--termo", dest="termos", action="append", help="Termo extra de busca. Pode repetir.")
    parser.add_argument("--somente-termos", action="store_true", help="Busca apenas os termos informados em --termo, sem varrer segmentos padrao.")
    parser.add_argument("--usar-termos-csv", action=argparse.BooleanOptionalAction, default=False, help="Inclui termos/categorias relevantes encontrados no CSV.")
    parser.add_argument("--max-termos-csv", type=int, default=12, help="Maximo de termos extras vindos do CSV.")
    parser.add_argument("--limite-consultas", type=int, default=0, help="Limita a quantidade de buscas feitas no Maps.")
    parser.add_argument("--limite-total", type=int, default=50, help="Para ao atingir esta quantidade de novos leads.")
    parser.add_argument("--min-novos-por-consulta", type=int, default=0, help="Tenta coletar pelo menos N leads novos em cada consulta antes de passar para a proxima.")
    parser.add_argument("--max-resultados-consulta", type=int, default=12, help="Maximo de fichas abertas por busca.")
    parser.add_argument("--paginas", type=int, default=1, help="Paginas por consulta no modo API.")
    parser.add_argument("--page-size", type=int, default=10, choices=range(1, 21), metavar="1-20", help="Resultados por pagina no modo API.")
    parser.add_argument("--max-scrolls", type=int, default=5, help="Rolagens na lista de resultados por busca.")
    parser.add_argument("--min-avaliacoes", type=int, default=0, help="Ignora lugares com menos avaliacoes.")
    parser.add_argument("--min-nota", type=float, default=0, help="Ignora lugares com nota menor que este valor.")
    parser.add_argument("--somente-ativos", action=argparse.BooleanOptionalAction, default=True, help="Ignora lugares marcados como fechados permanentemente no modo API.")
    parser.add_argument("--exigir-telefone", action="store_true", help="Salva somente leads com telefone encontrado.")
    parser.add_argument("--language-code", default=DEFAULT_LANGUAGE)
    parser.add_argument("--region-code", default=DEFAULT_REGION)
    parser.add_argument("--browser-channel", default="chrome", help="Canal Playwright: chrome, msedge ou vazio para Chromium.")
    parser.add_argument("--headless", action="store_true", help="Roda o navegador invisivel. O padrao abre uma janela para reduzir bloqueios.")
    parser.add_argument("--timeout", type=int, default=25)
    parser.add_argument("--tentativas", type=int, default=2)
    parser.add_argument("--pausa-erro", type=float, default=1.0)
    parser.add_argument("--pausa-pagina", type=float, default=1.0)
    parser.add_argument("--max-segundos", type=int, default=900, help="Tempo maximo da coleta antes de sincronizar o que ja encontrou. Use 0 para sem limite.")
    parser.add_argument("--parar-sem-novos", type=int, default=10, help="Para depois de N buscas seguidas sem leads novos. Use 0 para desativar.")
    parser.add_argument("--parar-apos-erros", type=int, default=3, help="Para depois de N erros de API/navegacao. Use 0 para desativar.")
    parser.add_argument("--pausa-busca", type=float, default=3.0)
    parser.add_argument("--pausa-scroll", type=float, default=1.0)
    parser.add_argument("--pausa-ficha", type=float, default=1.5)
    parser.add_argument("--pausa-lead", type=float, default=0.6)
    parser.add_argument("--dry-run", action="store_true", help="Mostra o plano de buscas sem abrir navegador nem escrever CSV.")
    parser.add_argument("--salvar-imediato", action=argparse.BooleanOptionalAction, default=False, help="Grava cada lead novo na entrada_periodica assim que ele passa no dedupe.")
    parser.add_argument("--atualizar-direto", action="store_true", help="Para cada lead novo, grava direto no CSV principal e insere no Supabase imediatamente.")
    parser.add_argument("--processar-pendencias-direto", action="store_true", help="Antes da busca, envia CSVs pendentes de entrada_periodica direto para CSV principal e Supabase.")
    parser.add_argument("--sincronizar", action="store_true", help="Depois de gerar o CSV, roda automacao_supabase_leads.py com dedupe forte.")
    parser.add_argument("--sync-sem-dedupe-forte", action="store_true", help="Nao passa --dedupe-forte para a automacao Supabase.")
    parser.add_argument("--sync-sem-bases-locais", action="store_true", help="Na sincronizacao, usa apenas Supabase, CSV atual e entrada_periodica.")
    parser.add_argument("--sync-manter-entradas", action="store_true", help="Nao arquiva arquivos de entrada_periodica depois da sincronizacao.")
    parser.add_argument("--sync-dry-run", action="store_true", help="Quando usar --sincronizar, roda a sync em modo dry-run.")
    parser.add_argument("--sync-local-only", action="store_true", help="Quando usar --sincronizar, atualiza apenas CSV local.")
    parser.add_argument("--consultar-supabase-base", action=argparse.BooleanOptionalAction, default=True, help="Carrega Supabase antes da coleta para ignorar leads ja existentes.")
    return parser.parse_args()


def main():
    args = parse_args()
    load_env()
    if args.browser_channel.strip().lower() in {"", "none", "chromium"}:
        args.browser_channel = ""

    csv_rows = read_csv_rows(args.csv_base)
    supabase_rows = []
    supabase_raw_rows = []
    supabase_client = None
    sync_module = None
    if not args.dry_run and not args.sync_local_only:
        if args.atualizar_direto:
            supabase_rows, supabase_raw_rows, supabase_client, sync_module = load_supabase_base_rows(
                args.consultar_supabase_base,
                return_details=True,
            )
        else:
            supabase_rows = load_supabase_base_rows(args.consultar_supabase_base)
    args.direct_updater = None
    if args.atualizar_direto and not args.dry_run:
        args.direct_updater = DirectUpdater(
            args.csv_base,
            csv_rows,
            supabase_client=supabase_client,
            sync_module=sync_module,
            remote_rows=supabase_raw_rows,
        )
    processed_pending_rows = []
    if args.processar_pendencias_direto and args.direct_updater:
        processed_pending_rows = process_pending_direct(args, csv_rows + supabase_rows)
    pending_rows = read_pending_inbox_rows()
    base_rows = csv_rows + supabase_rows + processed_pending_rows + pending_rows
    jobs = build_jobs(args, csv_rows)

    log(f"CSV base: {args.csv_base} ({len(csv_rows)} linhas)")
    if pending_rows:
        log(f"Entrada periodica pendente tambem usada no dedupe inicial: {len(pending_rows)} registros.")
    if supabase_rows or pending_rows:
        log(f"Dedupe inicial: CSV + Supabase + entradas pendentes = {len(base_rows)} registros conhecidos.")
    log(
        f"Consultas planejadas: {len(jobs)} | modo={args.modo} | perfil={args.perfil} | "
        f"page_size={args.page_size} | limite_total={args.limite_total or 'sem limite'} | "
        f"max_segundos={args.max_segundos or 'sem limite'} | "
        f"min_novos_por_consulta={args.min_novos_por_consulta or 'sem meta'}"
    )
    for job in jobs[:25]:
        print(f" - {job.query} [{job.segment}]")
    if len(jobs) > 25:
        print(f" - ... mais {len(jobs) - 25} consultas")

    if args.dry_run:
        log("Dry-run concluido. Nenhuma busca real foi feita e nenhum CSV foi escrito.")
        return

    output_path = args.saida or INBOX / f"google_maps_leads_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    args.output_path = output_path
    rows, stats = collect_rows(args, jobs, base_rows)
    if args.direct_updater:
        stats.update(args.direct_updater.stats())
    if rows:
        if args.atualizar_direto:
            log(f"Atualizacao direta concluida: {len(rows)} novos leads enviados ao CSV principal/API quando possivel.")
        elif args.salvar_imediato:
            log(f"CSV incremental atualizado: {output_path} ({len(rows)} novos leads)")
        else:
            write_csv(rows, output_path)
            log(f"CSV gerado: {output_path} ({len(rows)} novos leads)")
    else:
        log("Nenhum lead novo encontrado; CSV nao foi gerado.")

    report = write_log(args, output_path, jobs, stats)
    log(f"Log gerado: {report}")
    log(f"Resumo: {stats}")

    if args.sincronizar:
        log("Sincronizando CSV local e Supabase com dedupe forte.")
        run_sync(args)


if __name__ == "__main__":
    main()
