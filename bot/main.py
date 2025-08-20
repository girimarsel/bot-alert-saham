import os
import re
import json
import hashlib
import requests
from html import escape as html_escape
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse

# ================== Secrets (dari GitHub Actions) ==================
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# ================== Kata kunci ==================
KEYWORDS = [
    # Akuisisi / MTO / Pengendali
    "akuisisi", "tender offer", "mto", "mandatory tender offer",
    "pengambilalihan", "perubahan pengendali", "takeover",

    # Rights Issue (variasi umum)
    "rights issue", "right issue", "hmetd", "pmhmetd",
    "penambahan modal dengan hmetd", "penambahan modal melalui hmetd",
    "penawaran umum terbatas", "put i", "put ii", "put iii",
]

# ================== Sumber berita ==================
SOURCES = [
    "https://investasi.kontan.co.id/news/saham",
    "https://investor.id/market-and-corporate",
    "https://market.bisnis.com/market",
    "https://www.cnbcindonesia.com/market/indeks/idx",
    "https://www.idnfinancials.com/id/news",
    "https://www.emitennews.com/",
    "https://www.indopremier.com/ipotnews/newsList.php?group_news=RESEARCHNEWS",
]

# ================== Cache anti-duplikat ==================
CACHE_PATH = "data/sent.json"


# ------------------ Util ------------------
def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def key_for(title: str, url: str) -> str:
    raw = norm(title).lower() + "|" + url
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def load_cache():
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return set(data.get("sent", []))
    except Exception:
        return set()


def save_cache(sent_set):
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump({"sent": sorted(list(sent_set))}, f, ensure_ascii=False, indent=2)


def domain_of(url: str) -> str:
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""


def send_telegram(text: str):
    """Kirim pakai HTML agar aman dari karakter spesial."""
    api = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    r = requests.post(api, json=payload, timeout=25)
    print("TG status:", r.status_code, r.text[:200])
    r.raise_for_status()


# ------------------ Scraper ------------------
def scrape():
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://www.google.com/",
    }
    found = []

    for src in SOURCES:
        try:
            r = requests.get(src, headers=headers, timeout=30)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")

            for a in soup.find_all("a"):
                title = norm(a.get_text() or "")
                href = (a.get("href") or "").strip()
                if not title or not href:
                    continue

                tnorm = title.lower()
                if any(k in tnorm for k in KEYWORDS):
                    # Normalisasi SEMUA href non-absolute:
                    # contoh "/path" atau "newsDetail.php?..." → jadi full URL
                    if not href.lower().startswith(("http://", "https://")):
                        href = urljoin(src, href)

                    found.append((title, href))

        except Exception as e:
            print("Scrape error:", src, "-", e)

    return found


def escape_html_title(s: str) -> str:
    return html_escape(s or "", quote=True)


def format_messages(items):
    now = datetime.now(timezone.utc).astimezone().strftime("%d %b %Y %H:%M")
    lines = [f"📣 <b>ALERT SAHAM – Akuisisi / MTO / Rights Issue</b>\n🕒 {now}"]

    for title, url in items[:8]:  # batasi 8 item/run
        dom = domain_of(url) or "sumber"
        safe_title = escape_html_title(title)
        safe_url = html_escape(url, quote=True)
        lines.append(
            f"• <b>{safe_title}</b>\n"
            f"  📎 <a href=\"{safe_url}\">Baca di {dom}</a>"
        )
    return "\n\n".join(lines)


# ------------------ Main ------------------
def main():
    sent = load_cache()
    new_items = []

    for title, url in scrape():
        k = key_for(title, url)
        if k not in sent:
            new_items.append((title, url))
            sent.add(k)

    if new_items:
        send_telegram(format_messages(new_items))
        save_cache(sent)
    # kalau tidak ada item baru → diam (tidak kirim pesan)


if __name__ == "__main__":
    main()
