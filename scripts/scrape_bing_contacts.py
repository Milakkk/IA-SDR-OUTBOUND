#!/usr/bin/env python3
"""
Crawler headless que usa Playwright para seguir links e extrair telefones WhatsApp.
"""

import argparse
import base64
import csv
import json
import logging
import os
import random
import re
import sys
import time
import unicodedata
from collections import deque
from pathlib import Path
from typing import Iterable, List, Optional, Tuple
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
PHONE_REGEX = re.compile(r"(\+?\d[\d\-\.\(\)/ ]{7,}\d)")
KEYWORDS = ["whatsapp", "celular", "fone", "telefone", "contato"]
STRUCTURED_LENGTHS = (13, 12, 11, 10)
WHATSAPP_URL_PATTERNS = [
    (re.compile(r"https?://api\.whatsapp\.com/send\?phone=(\+?\d+)"), "api.whatsapp"),
    (re.compile(r"https?://wa\.me/(\+?\d+)"), "wa.me"),
]
POLITE_SLEEP_MIN = 0.8
POLITE_SLEEP_MAX = 1.4
STOP_TOKENS = {
    "ltda",
    "sa",
    "s.a",
    "eireli",
    "me",
    "epp",
    "de",
    "da",
    "do",
    "das",
    "dos",
    "e",
    "industria",
    "industrial",
    "comercio",
    "comercial",
    "brasil",
    "brasileira",
    "brasileiro",
}
LOW_CONFIDENCE_DOMAINS = {
    "exame.com",
    "casadosdados.com.br",
    "informecadastral.com.br",
    "cnpj.biz",
    "econodata.com.br",
    "consultacnpjfacil.com.br",
    "consultascnpj.com",
    "guiadocnpj.com.br",
    "apontador.com.br",
}
BLOCKED_SOURCE_DOMAINS = {
    "econodata.com.br",
    "cnpj.biz",
    "receitaws.com.br",
    "gov.br",
}
VALID_DDDS = {
    "11", "12", "13", "14", "15", "16", "17", "18", "19",
    "21", "22", "24", "27", "28",
    "31", "32", "33", "34", "35", "37", "38",
    "41", "42", "43", "44", "45", "46", "47", "48", "49",
    "51", "53", "54", "55",
    "61", "62", "63", "64", "65", "66", "67", "68", "69",
    "71", "73", "74", "75", "77", "79",
    "81", "82", "83", "84", "85", "86", "87", "88", "89",
    "91", "92", "93", "94", "95", "96", "97", "98", "99",
}
CONTACT_PATH_HINTS = (
    "contato",
    "fale-conosco",
    "faleconosco",
    "contact",
    "atendimento",
    "suporte",
    "onde-estamos",
    "localizacao",
)


def polite_sleep() -> None:
    time.sleep(random.uniform(POLITE_SLEEP_MIN, POLITE_SLEEP_MAX))


class PlaywrightFetcher:
    def __init__(self) -> None:
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=True)
        self._context = self._browser.new_context()

    def fetch(self, url: str, timeout: int) -> Tuple[Optional[str], List[str]]:
        candidates = [url]
        if url.startswith("https://"):
            candidates.append("http://" + url[len("https://") :])

        for idx, candidate in enumerate(candidates):
            page = self._context.new_page()
            try:
                polite_sleep()
                page.set_extra_http_headers({"User-Agent": USER_AGENT})
                page.goto(candidate, wait_until="domcontentloaded", timeout=timeout * 1000)
                html = page.content()
                anchors = [a.get_attribute("href") for a in page.query_selector_all("a[href]")]
                return html, [href for href in anchors if href]
            except PlaywrightTimeoutError as exc:
                logging.debug("timeout loading %s: %s", candidate, exc)
            except Exception as exc:
                logging.debug("playwright failed for %s: %s", candidate, exc)
            finally:
                page.close()
            if idx == 0 and len(candidates) > 1:
                logging.info("retrying with http for %s", url)
        return None, []

    def search_bing(self, query: str, max_results: int, timeout: int) -> List[str]:
        page = self._context.new_page()
        try:
            polite_sleep()
            page.set_extra_http_headers({"User-Agent": USER_AGENT})
            page.goto("https://www.bing.com", wait_until="domcontentloaded", timeout=timeout * 1000)
            page.goto(
                f"https://www.bing.com/search?q={quote_plus(query)}",
                wait_until="domcontentloaded",
                timeout=timeout * 1000,
            )
            anchors = [a.get_attribute("href") for a in page.query_selector_all("li.b_algo h2 a")]
            urls: List[str] = []
            for href in anchors:
                href = unwrap_bing_redirect(href or "")
                if href and href.startswith("http") and href not in urls:
                    urls.append(href)
                if len(urls) >= max_results:
                    break
            return urls
        except Exception as exc:
            logging.warning("bing playwright search failed for %r: %s", query, exc)
            return []
        finally:
            page.close()

    def close(self) -> None:
        try:
            self._context.close()
        except Exception:
            pass
        try:
            self._browser.close()
        except Exception:
            pass
        self._playwright.stop()


def bing_search(
    query: str,
    session: requests.Session,
    fetcher: PlaywrightFetcher,
    max_results: int,
    timeout: int,
) -> List[str]:
    params = {"q": query, "count": max_results * 2, "setLang": "pt-BR"}
    try:
        polite_sleep()
        resp = session.get("https://www.bing.com/search", params=params, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logging.warning("bing requests search failed for %r: %s", query, exc)
        return fetcher.search_bing(query, max_results=max_results, timeout=timeout)

    soup = BeautifulSoup(resp.text, "html.parser")
    urls: List[str] = []
    for link in soup.select("li.b_algo h2 a"):
        href = link.get("href")
        href = unwrap_bing_redirect(href or "")
        if href and href.startswith("http") and href not in urls:
            urls.append(href)
        if len(urls) >= max_results:
            break
    if urls:
        logging.debug("bing results(requests) for %r: %s", query, urls)
        return urls

    # Fallback for pages where HTML markup changed or anti-bot response hides b_algo.
    return fetcher.search_bing(query, max_results=max_results, timeout=timeout)


def unwrap_bing_redirect(url: str) -> str:
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        if "bing.com" not in (parsed.netloc or ""):
            return url
        params = parse_qs(parsed.query or "")
        token = (params.get("u") or [""])[0]
        if not token:
            return url
        token = unquote(token)
        if token.startswith("http"):
            return token
        # Bing often packs the destination in "a1" + base64url.
        if token.startswith("a1"):
            token = token[2:]
        padding = "=" * (-len(token) % 4)
        decoded = base64.urlsafe_b64decode((token + padding).encode("utf-8")).decode("utf-8", errors="ignore")
        if decoded.startswith("http"):
            return decoded
    except Exception:
        return url
    return url


def normalize_token(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def company_tokens(company: str) -> List[str]:
    tokens: List[str] = []
    for raw in re.split(r"\s+", company.lower()):
        t = normalize_token(raw)
        if not t or t in STOP_TOKENS or len(t) < 4:
            continue
        tokens.append(t)
    return tokens


def url_confidence_score(url: str, company: str) -> int:
    try:
        parsed = urlparse(url)
    except Exception:
        return 0
    host = normalize_token(parsed.netloc or "")
    score = 0
    for token in company_tokens(company):
        if token and token in host:
            score += 4
    low_hit = is_low_confidence_url(url)
    if low_hit:
        score -= 8
    return score


def company_token_hits(url: str, company: str) -> int:
    try:
        parsed = urlparse(url)
    except Exception:
        return 0
    host = normalize_token(parsed.netloc or "")
    return sum(1 for token in company_tokens(company) if token and token in host)


def is_company_domain_match(url: str, company: str) -> bool:
    tokens = company_tokens(company)
    if not tokens:
        return False
    required_hits = 2 if len(tokens) >= 3 else 1
    return company_token_hits(url, company) >= required_hits


def is_low_confidence_url(url: str) -> bool:
    try:
        host = (urlparse(url).netloc or "").lower()
    except Exception:
        return False
    if any(host.endswith(d) for d in LOW_CONFIDENCE_DOMAINS):
        return True
    risky_tokens = ("cnpj", "consulta", "cadastro", "dados", "guia")
    return any(tok in host for tok in risky_tokens)


def prioritize_urls(urls: List[str], company: str) -> List[str]:
    normalized: List[str] = []
    seen = set()
    for raw in urls:
        try:
            parsed = urlparse(raw)
            if not parsed.scheme or not parsed.netloc:
                continue
            base = f"{parsed.scheme}://{parsed.netloc}/"
            if base in seen:
                continue
            seen.add(base)
            normalized.append(base)
        except Exception:
            continue

    decorated = [(url_confidence_score(u, company), idx, u) for idx, u in enumerate(normalized)]
    decorated.sort(key=lambda x: (-x[0], x[1]))
    return [u for _, _, u in decorated]


def _same_url_candidate(a: str, b: str) -> bool:
    try:
        pa = urlparse(a)
        pb = urlparse(b)
    except Exception:
        return False
    host_a = (pa.netloc or "").lower().replace("www.", "")
    host_b = (pb.netloc or "").lower().replace("www.", "")
    if not host_a or not host_b:
        return False
    if host_a != host_b:
        return False
    path_a = (pa.path or "/").rstrip("/") or "/"
    path_b = (pb.path or "/").rstrip("/") or "/"
    return path_a == path_b or path_a == "/" or path_b == "/"


def is_allowed_external_contact_url(url: str) -> bool:
    try:
        host = (urlparse(url).netloc or "").lower()
    except Exception:
        return False
    allowed = ("instagram.com", "facebook.com", "linkedin.com")
    return any(host.endswith(d) or f".{d}" in host for d in allowed)


def is_blocked_source_url(url: str) -> bool:
    try:
        host = (urlparse(url).netloc or "").lower()
    except Exception:
        return True
    if any(host.endswith(d) or f".{d}" in host for d in BLOCKED_SOURCE_DOMAINS):
        return True
    risky_tokens = ("cnpj", "econodata", "receita", "cadastral")
    return any(tok in host for tok in risky_tokens)


def is_acceptable_phone_source(url: str, company: str) -> bool:
    if is_blocked_source_url(url):
        return False
    return is_company_domain_match(url, company) or is_allowed_external_contact_url(url)


def guess_company_urls(company: str) -> List[str]:
    tokens = company_tokens(company)
    guesses: List[str] = []
    combos: List[str] = []
    if len(tokens) >= 2:
        combos.append("".join(tokens[:2]))
    if len(tokens) >= 3:
        combos.append("".join(tokens[:3]))
    if tokens:
        combos.append(tokens[0])
    seen = set()
    for slug in combos:
        if not slug or slug in seen:
            continue
        seen.add(slug)
        guesses.extend(
            [
                f"https://www.{slug}.com.br",
                f"https://{slug}.com.br",
                f"https://www.{slug}.com",
                f"https://{slug}.com",
            ]
        )
    return guesses


def get_env_or_dotenv(key: str, dotenv_path: str = ".env") -> str:
    val = os.getenv(key, "").strip()
    if val:
        return val
    p = Path(dotenv_path)
    if not p.exists():
        return ""
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == key:
                return v.strip().strip('"').strip("'")
    except Exception:
        return ""
    return ""


def normalize_text(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", s.lower()).strip()


def extract_digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def verify_company_signal_on_url(
    url: str,
    company: str,
    trade_name: str,
    cnpj: str,
    session: requests.Session,
    timeout: int,
) -> Tuple[bool, str]:
    if is_blocked_source_url(url) or is_allowed_external_contact_url(url):
        return False, ""
    try:
        polite_sleep()
        resp = session.get(url, timeout=timeout)
        resp.raise_for_status()
    except Exception:
        return False, ""

    text = normalize_text(resp.text)
    if not text:
        return False, ""

    checks: List[Tuple[str, str]] = []
    if company:
        checks.append((normalize_text(company), "razao_social"))
    if trade_name:
        checks.append((normalize_text(trade_name), "nome_fantasia"))
    if cnpj:
        checks.append((extract_digits(cnpj), "cnpj"))

    for needle, label in checks:
        if not needle:
            continue
        if label == "cnpj":
            if needle in extract_digits(resp.text):
                return True, label
        elif needle in text:
            return True, label
    return False, ""


def select_company_targets_with_openai(
    company: str,
    urls: List[str],
    timeout: int,
) -> Tuple[Optional[str], Optional[str], str, str]:
    api_key = get_env_or_dotenv("OPENAI_API_KEY")
    if not api_key or not urls:
        return None, None, "none", "openai_unavailable"
    top_urls = urls[:10]
    prompt = (
        "Escolha o URL que mais provavelmente é o site oficial da empresa.\n"
        "Empresa: " + company + "\n"
        "URLs:\n" + "\n".join(f"{i+1}. {u}" for i, u in enumerate(top_urls)) + "\n"
        "Se nao houver site oficial claro, escolha um perfil externo util para contato.\n"
        "So aceite perfis da propria empresa em instagram.com, facebook.com ou linkedin.com.\n"
        "Nao escolha sites de cadastro, consulta, receita, cnpj, diretórios ou agregadores.\n"
        "Se nenhuma URL servir, responda decision=none.\n"
        "Responda APENAS JSON no formato: "
        "{\"official_url\":\"...\",\"external_url\":\"...\",\"decision\":\"official|external|none\",\"confidence\":0-100,\"reason\":\"...\"}.\n"
        "Regras: se decision=official preencha official_url; se decision=external preencha external_url; "
        "se decision=none deixe ambos vazios."
    )
    payload = {
        "model": "gpt-5-nano",
        "input": [
            {
                "role": "system",
                "content": [{"type": "input_text", "text": "Você escolhe o site oficial mais provável de uma empresa brasileira."}],
            },
            {
                "role": "user",
                "content": [{"type": "input_text", "text": prompt}],
            },
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "site_choice",
                "schema": {
                    "type": "object",
                    "properties": {
                        "official_url": {"type": "string"},
                        "external_url": {"type": "string"},
                        "decision": {"type": "string", "enum": ["official", "external", "none"]},
                        "confidence": {"type": "integer"},
                        "reason": {"type": "string"},
                    },
                    "required": ["official_url", "external_url", "decision", "confidence", "reason"],
                    "additionalProperties": False,
                },
                "strict": True,
            }
        },
    }
    request_timeout = max(timeout, 30)
    for attempt in range(2):
        try:
            resp = requests.post(
                "https://api.openai.com/v1/responses",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                data=json.dumps(payload),
                timeout=request_timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            content = data.get("output_text", "")
            if not content:
                # Fallback: parse output[] items in case output_text is empty.
                chunks: List[str] = []
                for item in data.get("output", []) or []:
                    for c in item.get("content", []) or []:
                        text = c.get("text")
                        if text:
                            chunks.append(text)
                content = "\n".join(chunks)
            if not content:
                return None, None, "none", "openai_empty"
            parsed = json.loads(content)
            decision = str(parsed.get("decision") or "").strip().lower()
            reason = str(parsed.get("reason") or "").strip()
            official_url = str(parsed.get("official_url") or "").strip()
            external_url = str(parsed.get("external_url") or "").strip()
            official_match: Optional[str] = None
            external_match: Optional[str] = None
            if official_url:
                for candidate in top_urls:
                    if _same_url_candidate(official_url, candidate):
                        official_match = candidate
                        break
            if external_url:
                for candidate in top_urls:
                    if _same_url_candidate(external_url, candidate) and is_allowed_external_contact_url(candidate):
                        external_match = candidate
                        break
            return official_match, external_match, decision, reason
        except Exception as exc:
            logging.warning(
                "openai selector failed for %r (attempt %d/2): %s",
                company,
                attempt + 1,
                exc,
            )
    return None, None, "none", "openai_failed"


def find_structured_in_digits(digits: str) -> Optional[str]:
    for start in range(len(digits)):
        for length in STRUCTURED_LENGTHS:
            candidate = digits[start : start + length]
            if len(candidate) != length:
                continue
            if _structured_candidate_valid(candidate):
                return candidate
    return None


def _structured_candidate_valid(candidate: str) -> bool:
    if len(candidate) not in (10, 11, 12, 13):
        return False

    if candidate.startswith("55") and len(candidate) in (12, 13):
        ddd = candidate[2:4]
        local = candidate[4:]
    else:
        ddd = candidate[:2]
        local = candidate[2:]

    if len(ddd) != 2 or not ddd.isdigit():
        return False
    if ddd not in VALID_DDDS:
        return False
    if len(local) == 9:
        return local[0] == "9"
    if len(local) == 8:
        return local[0] in {"2", "3", "4", "5"}
    return False


def find_whatsapp_link(text: str) -> Tuple[Optional[str], str, str]:
    for pattern, label in WHATSAPP_URL_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        digits = re.sub(r"\D", "", match.group(1) or "")
        phone = find_structured_in_digits(digits)
        if phone:
            if len(phone) in (10, 11):
                phone = f"55{phone}"
            return phone, f"{label} link", match.group(0)
    return None, "", ""


def find_tel_link(text: str) -> Tuple[Optional[str], str, str]:
    matches = re.findall(r"tel:([+\d\-\s\(\)]+)", text, flags=re.IGNORECASE)
    for raw in matches:
        digits = re.sub(r"\D", "", raw)
        phone = find_structured_in_digits(digits)
        if phone:
            if len(phone) in (10, 11):
                phone = f"55{phone}"
            return phone, "tel link", f"tel:{raw}"
    return None, "", ""


def extract_phone_from_text(text: str) -> Tuple[Optional[str], str]:
    lower = text.lower()
    for keyword in KEYWORDS:
        idx = lower.find(keyword)
        if idx < 0:
            continue
        snippet = text[max(0, idx - 80) : idx + 180]
        structured = find_structured_in_digits(re.sub(r"\D", "", snippet))
        if structured:
            if len(structured) in (10, 11):
                structured = f"55{structured}"
            return structured, f'structured near keyword "{keyword}"'
    phone = _extract_formatted_phone_anywhere(text)
    if phone:
        return phone, "formatted phone in page"
    return None, ""


def _find_phone_in_snippet(snippet: str) -> Optional[str]:
    for match in PHONE_REGEX.findall(snippet):
        if not re.search(r"[+\-\.\(\)\s]", match):
            continue
        digits = re.sub(r"\D", "", match)
        candidate = find_structured_in_digits(digits)
        if candidate:
            if len(candidate) in (10, 11):
                candidate = f"55{candidate}"
            return candidate
    return None


def _extract_formatted_phone_anywhere(text: str) -> Optional[str]:
    candidates: List[Tuple[int, str]] = []
    for raw in PHONE_REGEX.findall(text):
        if not re.search(r"[+\-\.\(\)\s/]", raw):
            continue
        digits = re.sub(r"\D", "", raw)
        candidate = find_structured_in_digits(digits)
        if not candidate:
            continue
        if len(candidate) in (10, 11):
            candidate = f"55{candidate}"
        score = 0
        lowered = raw.lower()
        if "+55" in lowered:
            score += 3
        if "(" in raw and ")" in raw:
            score += 2
        if "-" in raw:
            score += 1
        if len(candidate) == 13:
            score += 1
        candidates.append((score, candidate))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def normalize_link(raw: str, base: str) -> Optional[str]:
    try:
        resolved = urljoin(base, raw.strip())
        parsed = urlparse(resolved)
        if parsed.scheme not in ("http", "https"):
            return None
        return parsed.geturl()
    except Exception:
        return None


def extract_links(hrefs: List[str], base_url: str, max_links: int) -> List[str]:
    gathered: List[str] = []
    seen = set()
    for raw in hrefs:
        if raw.startswith("#") or raw.lower().startswith("javascript:") or raw.startswith("mailto:"):
            continue
        normalized = normalize_link(raw, base_url)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        gathered.append(normalized)

    # Prioriza links com cara de "contato/fale conosco" para aumentar chance de achar telefone.
    gathered.sort(key=contact_link_score, reverse=True)
    return gathered[:max_links]


def contact_link_score(url: str) -> int:
    score = 0
    lower = url.lower()
    for hint in CONTACT_PATH_HINTS:
        if hint in lower:
            score += 10
    return score


def build_contact_fallback_urls(start_urls: List[str]) -> List[str]:
    extra: List[str] = []
    seen = set()
    for raw in start_urls:
        try:
            parsed = urlparse(raw)
            if not parsed.scheme or not parsed.netloc:
                continue
            root = f"{parsed.scheme}://{parsed.netloc}"
        except Exception:
            continue
        for suffix in CONTACT_PATH_HINTS:
            candidate = f"{root}/{suffix}"
            if candidate in seen:
                continue
            seen.add(candidate)
            extra.append(candidate)
    return extra


def crawl_for_phone(
    start_urls: List[str],
    fetcher: PlaywrightFetcher,
    timeout: int,
    max_depth: int,
    max_links: int,
    company: str,
) -> Tuple[str, str, str, List[str]]:
    queue = deque((url, [url], 0) for url in start_urls)
    visited = set()
    last_path: List[str] = []
    fallback_result: Optional[Tuple[str, str, str, List[str]]] = None
    while queue:
        current_url, path, depth = queue.popleft()
        last_path = path
        if current_url in visited:
            continue
        visited.add(current_url)
        logging.info("visitando %s (prof %d) caminho: %s", current_url, depth, " > ".join(path))
        html, anchors = fetcher.fetch(current_url, timeout)
        if html is None:
            continue
        phone, note, matched_url = find_whatsapp_link(html)
        if phone:
            source = matched_url or current_url
            if is_acceptable_phone_source(current_url, company):
                return phone, source, note, path
            if fallback_result is None and is_allowed_external_contact_url(current_url):
                fallback_result = (phone, source, f"{note} (fallback_low_confidence)", path)
        phone, note, matched_url = find_tel_link(html)
        if phone:
            source = matched_url or current_url
            if is_acceptable_phone_source(current_url, company):
                return phone, current_url, note, path
            if fallback_result is None and is_allowed_external_contact_url(current_url):
                fallback_result = (phone, source, f"{note} (fallback_low_confidence)", path)
        for href in anchors:
            phone, note, matched = find_whatsapp_link(href)
            if phone:
                source = matched or href
                if is_acceptable_phone_source(current_url, company):
                    return phone, source, note, path + [href]
                if fallback_result is None and is_allowed_external_contact_url(current_url):
                    fallback_result = (phone, source, f"{note} (fallback_low_confidence)", path + [href])
            phone, note, matched = find_tel_link(href)
            if phone:
                source = matched or href
                if is_acceptable_phone_source(current_url, company):
                    return phone, source, note, path + [href]
                if fallback_result is None and is_allowed_external_contact_url(current_url):
                    fallback_result = (phone, source, f"{note} (fallback_low_confidence)", path + [href])
        phone, note = extract_phone_from_text(html)
        if phone:
            is_contact_page = contact_link_score(current_url) > 0
            if (
                is_acceptable_phone_source(current_url, company)
                and (is_contact_page or note == "formatted phone in page")
            ):
                return phone, current_url, note, path
            if fallback_result is None and is_allowed_external_contact_url(current_url):
                fallback_result = (phone, current_url, f"{note} (fallback_text_signal)", path)
        if depth >= max_depth:
            continue
        if is_allowed_external_contact_url(current_url):
            # Em redes sociais aceitas, nao seguimos para paginas genericas da plataforma.
            continue
        for link in extract_links(anchors, current_url, max_links):
            if link not in visited:
                queue.append((link, path + [link], depth + 1))
    if fallback_result:
        return fallback_result
    return "", "", f"not found after depth {max_depth}", last_path


def scrape_lead(
    row: dict,
    session: requests.Session,
    fetcher: PlaywrightFetcher,
    timeout: int,
    bing_results: int,
    pages_per_lead: int,
    max_depth: int,
    max_links: int,
) -> Tuple[str, str, str, List[str]]:
    company = row.get("razao_social") or ""
    trade_name = (row.get("nome_fantasia") or "").strip()
    cnpj = str(row.get("cnpj") or "").strip()
    if not company:
        return "", "", "empresa inválida", []

    effective_bing_results = max(bing_results, 10)
    search_queries: List[str] = [company, f"\"{company}\" site oficial"]
    if trade_name and normalize_token(trade_name) != normalize_token(company):
        search_queries.extend([trade_name, f"\"{trade_name}\" site oficial"])

    urls: List[str] = []
    for query in search_queries:
        found = bing_search(
            query,
            session,
            fetcher=fetcher,
            max_results=effective_bing_results,
            timeout=timeout,
        )
        for u in found:
            if u not in urls:
                urls.append(u)
            if len(urls) >= effective_bing_results:
                break
        if len(urls) >= effective_bing_results:
            break

    prioritized = prioritize_urls(urls, company)
    mechanically_verified_url = ""
    mechanical_reason = ""
    for candidate in prioritized[:3]:
        verified, reason = verify_company_signal_on_url(
            candidate,
            company=company,
            trade_name=trade_name,
            cnpj=cnpj,
            session=session,
            timeout=timeout,
        )
        if verified:
            mechanically_verified_url = candidate
            mechanical_reason = reason
            logging.info("url validada mecanicamente: %s (%s)", candidate, reason)
            break

    if mechanically_verified_url:
        start_urls = [mechanically_verified_url]
        phone, url, notes, path = crawl_for_phone(
            start_urls[:pages_per_lead],
            fetcher=fetcher,
            timeout=timeout,
            max_depth=max_depth,
            max_links=max_links,
            company=company,
        )
        if not phone:
            contact_candidates = build_contact_fallback_urls(start_urls[:1])[:4]
            if contact_candidates:
                phone, url, contact_notes, path = crawl_for_phone(
                    contact_candidates,
                    fetcher=fetcher,
                    timeout=timeout,
                    max_depth=1,
                    max_links=max_links,
                    company=company,
                )
                if contact_notes:
                    notes = f"contact_fallback; {contact_notes}"
        mechanical_note = f"mechanical_site={mechanically_verified_url}; match={mechanical_reason}"
        notes = f"{mechanical_note}; {notes}" if notes else mechanical_note
        return phone, url, notes, path

    selected_official, selected_external, ai_decision, ai_reason = select_company_targets_with_openai(
        company, urls, timeout=timeout
    )
    selected_url = selected_official
    if selected_url:
        logging.info("url inicial escolhida (gpt): %s", selected_url)
    elif ai_decision == "external" and selected_external:
        logging.info("url externa escolhida (gpt): %s", selected_external)
    else:
        logging.info("gpt nao escolheu url utilizavel")
    if not urls:
        return "", "", "bing sem resultados", []
    start_urls: List[str] = []
    seen = set()
    ordered_candidates: List[str] = []

    if selected_url:
        ordered_candidates.append(selected_url)
    if ai_decision == "external" and selected_external:
        ordered_candidates.append(selected_external)

    if not ordered_candidates:
        return "", "", f"gpt sem site selecionado ({ai_decision})", []

    for u in ordered_candidates:
        if u and u not in seen:
            seen.add(u)
            start_urls.append(u)
    phone, url, notes, path = crawl_for_phone(
        start_urls[:pages_per_lead],
        fetcher=fetcher,
        timeout=timeout,
        max_depth=max_depth,
        max_links=max_links,
        company=company,
    )
    if not phone:
        # Mantém fallback de contato, mas evita explosão de tentativas em muitos domínios.
        contact_candidates = build_contact_fallback_urls(start_urls[:1])[:4]
        if contact_candidates:
            phone, url, contact_notes, path = crawl_for_phone(
                contact_candidates,
                fetcher=fetcher,
                timeout=timeout,
                max_depth=1,
                max_links=max_links,
                company=company,
            )
            if contact_notes:
                notes = f"contact_fallback; {contact_notes}"
    if selected_url:
        selection_note = f"selected_site={selected_url}"
        notes = f"{selection_note}; {notes}" if notes else selection_note
    ai_note = f"decision={ai_decision}; reason={ai_reason}"
    if ai_note:
        notes = f"{ai_note}; {notes}" if notes else ai_note
    return phone, url, notes, path


def load_leads(path: Path) -> List[dict]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        return [dict(row) for row in reader]


def write_results(path: Path, fieldnames: List[str], rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def reorder_output_fieldnames(fieldnames: List[str]) -> List[str]:
    ordered = [f for f in fieldnames if f not in {"telefone_base", "telefone_scraped"}]
    if "telefone_base" in fieldnames:
        ordered.append("telefone_base")
    if "telefone_scraped" in fieldnames:
        ordered.append("telefone_scraped")
    return ordered


def main() -> int:
    parser = argparse.ArgumentParser(description="Scrape WhatsApp phones for leads using Playwright.")
    parser.add_argument(
        "--input",
        default="leads_por_cnae_e_nome.csv",
        help="CSV with leads (default: %(default)s)",
    )
    parser.add_argument(
        "--output",
        default="leads_por_cnae_e_nome_scraped.csv",
        help="Where to dump the augmented CSV.",
    )
    parser.add_argument(
        "--offset", type=int, default=0, help="Starting row index (default: %(default)s)"
    )
    parser.add_argument(
        "--limit", type=int, default=15, help="Number of leads to scrape (default: %(default)s)"
    )
    parser.add_argument(
        "--bing-results",
        type=int,
        default=10,
        help="Maximum Bing results to inspect (default: %(default)s)",
    )
    parser.add_argument(
        "--pages-per-lead",
        type=int,
        default=5,
        help="Top N URLs per lead (default: %(default)s)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=15,
        help="Request timeout seconds (default: %(default)s)",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=2,
        help="Maximum crawl depth after each Bing result (default: %(default)s)",
    )
    parser.add_argument(
        "--max-links",
        type=int,
        default=12,
        help="Maximum links per page to enqueue (default: %(default)s)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

    input_path = Path(args.input)
    if not input_path.exists():
        logging.error("input file %s not found", args.input)
        return 1

    leads = load_leads(input_path)
    selection = leads[args.offset : args.offset + args.limit]
    if not selection:
        logging.error("no leads available in the requested range")
        return 1

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    fetcher = PlaywrightFetcher()
    augmented_rows: List[dict] = []
    success = 0
    failure = 0

    try:
        for idx, lead in enumerate(selection, start=args.offset + 1):
            logging.info("(%d/%d) scraping %s", idx, args.offset + len(selection), lead.get("nome_fantasia", "unknown"))
            phone, url, notes, path = scrape_lead(
                lead,
                session=session,
                fetcher=fetcher,
                timeout=args.timeout,
                bing_results=args.bing_results,
                pages_per_lead=args.pages_per_lead,
                max_depth=args.max_depth,
                max_links=args.max_links,
            )
            if phone:
                success += 1
            else:
                failure += 1
            augmented = dict(lead)
            augmented["telefone_base"] = (
                str(lead.get("telefone") or lead.get("telefone_base") or "").strip()
            )
            augmented["telefone_scraped"] = phone
            augmented["scrape_url"] = url
            augmented["scrape_notes"] = notes
            augmented["scrape_path"] = " > ".join(path) if path else ""
            augmented_rows.append(augmented)
    finally:
        fetcher.close()

    fieldnames = reorder_output_fieldnames(list(augmented_rows[0].keys()))
    output_path = Path(args.output)
    write_results(output_path, fieldnames, augmented_rows)

    logging.info("scraping complete: %d matches, %d misses, output=%s", success, failure, output_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
