#!/usr/bin/env python3
"""
Apartment Scout — Daily StreetEasy email parser.

Checks Gmail for StreetEasy saved search alert emails, extracts listing
details, adds them to a Notion database, and archives the email.

Usage:
    python apartment_scout.py          # Run once (for cron)
    python apartment_scout.py --dry-run # Preview without writing to Notion
"""

import os
import re
import base64
import pickle
import argparse
import logging
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from notion_client import Client as NotionClient

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

SEARCH_SUBJECT = (
    "For Rent: listings under $7,000 (base rent) in NYC and NJ "
    "with at least 2 bedrooms with at least 1.5 bathrooms "
    "with washer / dryer in unit, dishwasher, and private outdoor space"
)

NOTION_DATABASE_ID = "2e3c589608ba80258aa2d583c468fd91"

# Neighborhood options that exist in the Notion database
KNOWN_NEIGHBORHOODS = [
    "Ditmas Park", "Boreum Hill", "Flatbush", "Bed-Stuy",
    "Stuyvesant Heights", "Cobble Hill", "Brooklyn Heights", "Park Slope",
    "Prospect Lefferts Gardens", "Prospect Heights", "Kensington",
    "Crown Heights", "East Flatbush", "Windsor Terrace", "Clinton Hill",
]

TOKEN_PATH = Path(__file__).parent / "token.pickle"
CREDENTIALS_PATH = Path(__file__).parent / "credentials.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("apartment_scout")

# ---------------------------------------------------------------------------
# Gmail helpers
# ---------------------------------------------------------------------------


def get_gmail_service():
    """Authenticate and return a Gmail API service instance."""
    creds = None
    if TOKEN_PATH.exists():
        with open(TOKEN_PATH, "rb") as f:
            creds = pickle.load(f)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CREDENTIALS_PATH), GMAIL_SCOPES
            )
            creds = flow.run_local_server(port=0)
        with open(TOKEN_PATH, "wb") as f:
            pickle.dump(creds, f)

    return build("gmail", "v1", credentials=creds)


def search_emails(service):
    """Search for unread StreetEasy saved-search alert emails."""
    query = f'subject:"{SEARCH_SUBJECT}" is:unread'
    results = (
        service.users()
        .messages()
        .list(userId="me", q=query, maxResults=10)
        .execute()
    )
    return results.get("messages", [])


def get_email_body(service, msg_id):
    """Fetch and decode the HTML body of a Gmail message."""
    msg = (
        service.users()
        .messages()
        .get(userId="me", id=msg_id, format="full")
        .execute()
    )
    payload = msg.get("payload", {})
    html_body = _extract_html(payload)
    if html_body:
        return base64.urlsafe_b64decode(html_body).decode("utf-8", errors="replace")
    return ""


def _extract_html(payload):
    """Recursively find the text/html part in a Gmail payload."""
    mime = payload.get("mimeType", "")
    if mime == "text/html":
        return payload.get("body", {}).get("data", "")
    for part in payload.get("parts", []):
        result = _extract_html(part)
        if result:
            return result
    return ""


def archive_email(service, msg_id):
    """Archive an email by removing the INBOX label."""
    service.users().messages().modify(
        userId="me",
        id=msg_id,
        body={"removeLabelIds": ["INBOX", "UNREAD"]},
    ).execute()
    log.info("Archived email %s", msg_id)


# ---------------------------------------------------------------------------
# Email parser — extracts apartment listings from StreetEasy alert HTML
# ---------------------------------------------------------------------------


def parse_listings(html: str) -> list[dict]:
    """
    Parse StreetEasy saved-search alert email HTML into structured listings.

    StreetEasy alert emails typically contain listing cards with:
    - Address (with link to listing page)
    - Price
    - Beds / Baths
    - Square footage (sometimes absent)
    - Neighborhood

    Returns a list of dicts with keys:
        address, neighborhood, beds, baths, sq_ft, cost, streeteasy_url
    """
    soup = BeautifulSoup(html, "html.parser")
    listings = []

    # Strategy 1: Find listing links (streeteasy.com/building/ or /rental/)
    listing_links = set()
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        if "streeteasy.com" in href and (
            "/building/" in href or "/rental/" in href
        ):
            # Normalize — strip tracking params
            clean_url = re.split(r"[?&]utm_", href)[0]
            listing_links.add(clean_url)

    # Strategy 2: Walk the HTML looking for listing blocks
    # StreetEasy emails use table-based layouts; each listing is typically
    # a table row or div containing price, address, and details.
    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.split("\n") if line.strip()]

    # Try to extract structured data from the text content
    # Pattern: address lines, price lines ($X,XXX), bed/bath lines
    price_pattern = re.compile(r"\$[\d,]+(?:/\s*(?:mo|month))?")
    bed_bath_pattern = re.compile(
        r"(\d+)\s*(?:bed|br|bedroom)s?\s*[,/|·]\s*"
        r"([\d.]+)\s*(?:bath|ba|bathroom)s?",
        re.IGNORECASE,
    )
    sqft_pattern = re.compile(r"([\d,]+)\s*(?:sq\.?\s*ft|sf|sqft)", re.IGNORECASE)
    address_pattern = re.compile(
        r"(\d+[\w\s.-]+(?:street|st|avenue|ave|place|pl|road|rd|"
        r"boulevard|blvd|drive|dr|court|ct|terrace|ter|way)\.?"
        r"(?:\s*#?\s*\w+)?)",
        re.IGNORECASE,
    )

    # Build listing objects by scanning the text
    # Group content by proximity to listing URLs
    for url in listing_links:
        listing = {
            "address": "",
            "neighborhood": "",
            "beds": None,
            "baths": None,
            "sq_ft": None,
            "cost": None,
            "streeteasy_url": url,
        }

        # Try to extract address from the URL path
        url_match = re.search(
            r"/building/([^/]+)/([^/?]+)", url
        ) or re.search(r"/rental/(\d+)", url)
        if url_match:
            # Building URL: /building/address-slug/unit
            slug = url_match.group(1).replace("-", " ").title()
            unit = url_match.group(2) if url_match.lastindex >= 2 else ""
            listing["address"] = f"{slug} #{unit}".strip(" #")

        # Find the anchor tag for this URL to get surrounding context
        for a_tag in soup.find_all("a", href=True):
            if url in a_tag["href"] or a_tag["href"] in url:
                # Get the parent container (usually a table cell or div)
                container = a_tag
                for _ in range(8):  # Walk up to 8 levels
                    if container.parent:
                        container = container.parent
                    else:
                        break

                block_text = container.get_text(separator="\n")

                # Extract address from link text if it looks like an address
                link_text = a_tag.get_text(strip=True)
                if link_text and not link_text.startswith("http"):
                    addr_match = address_pattern.search(link_text)
                    if addr_match:
                        listing["address"] = link_text

                # Extract price
                price_match = price_pattern.search(block_text)
                if price_match:
                    price_str = price_match.group().replace("$", "").replace(",", "")
                    price_str = re.sub(r"/\s*(?:mo|month)", "", price_str)
                    try:
                        listing["cost"] = int(price_str)
                    except ValueError:
                        pass

                # Extract beds/baths
                bb_match = bed_bath_pattern.search(block_text)
                if bb_match:
                    listing["beds"] = int(bb_match.group(1))
                    listing["baths"] = float(bb_match.group(2))

                # Extract square footage
                sf_match = sqft_pattern.search(block_text)
                if sf_match:
                    listing["sq_ft"] = int(
                        sf_match.group(1).replace(",", "")
                    )

                # Extract neighborhood
                for hood in KNOWN_NEIGHBORHOODS:
                    if hood.lower() in block_text.lower():
                        listing["neighborhood"] = hood
                        break

                break  # Found relevant container

        if listing["address"] or listing["cost"]:
            listings.append(listing)

    # Deduplicate by URL
    seen_urls = set()
    unique = []
    for lst in listings:
        if lst["streeteasy_url"] not in seen_urls:
            seen_urls.add(lst["streeteasy_url"])
            unique.append(lst)

    return unique


# ---------------------------------------------------------------------------
# Notion helpers
# ---------------------------------------------------------------------------


def get_notion_client():
    """Return an authenticated Notion client."""
    api_key = os.getenv("NOTION_API_KEY")
    if not api_key:
        raise RuntimeError("NOTION_API_KEY not set in environment")
    return NotionClient(auth=api_key)


def get_existing_urls(notion: NotionClient) -> set[str]:
    """Fetch all StreetEasy URLs already in the Notion database."""
    urls = set()
    cursor = None
    while True:
        kwargs = {"database_id": NOTION_DATABASE_ID, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = notion.databases.query(**kwargs)
        for page in resp["results"]:
            props = page.get("properties", {})
            url_prop = props.get("StreetEasy URL", {})
            if url_prop.get("url"):
                urls.add(url_prop["url"])
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")
    return urls


def add_listing_to_notion(notion: NotionClient, listing: dict):
    """Create a new page in the Notion apartment database."""
    properties = {
        "Address": {"title": [{"text": {"content": listing["address"] or "Unknown"}}]},
        "Status": {"status": {"name": "Interested"}},
        "StreetEasy URL": {"url": listing["streeteasy_url"]},
    }

    if listing.get("cost") is not None:
        properties["Cost"] = {"number": listing["cost"]}

    if listing.get("beds") is not None:
        properties["Beds"] = {"number": listing["beds"]}

    if listing.get("baths") is not None:
        properties["Baths"] = {"number": listing["baths"]}

    if listing.get("sq_ft") is not None:
        properties["Sq. Ft."] = {"number": listing["sq_ft"]}

    if listing.get("neighborhood"):
        properties["Neighborhood"] = {"select": {"name": listing["neighborhood"]}}

    notion.pages.create(
        parent={"database_id": NOTION_DATABASE_ID},
        properties=properties,
    )
    log.info("Added to Notion: %s", listing["address"])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run(dry_run=False):
    """Execute the daily apartment scout pipeline."""
    log.info("Starting apartment scout run...")

    # 1. Connect to Gmail
    gmail = get_gmail_service()

    # 2. Search for alert emails
    messages = search_emails(gmail)
    if not messages:
        log.info("No new StreetEasy alert emails found. Done.")
        return

    log.info("Found %d alert email(s)", len(messages))

    # 3. Connect to Notion
    if not dry_run:
        notion = get_notion_client()
        existing_urls = get_existing_urls(notion)
        log.info("Found %d existing listings in Notion", len(existing_urls))
    else:
        existing_urls = set()

    # 4. Process each email
    total_added = 0
    for msg_meta in messages:
        msg_id = msg_meta["id"]
        log.info("Processing email %s", msg_id)

        html = get_email_body(gmail, msg_id)
        if not html:
            log.warning("Could not extract HTML body from email %s", msg_id)
            continue

        listings = parse_listings(html)
        log.info("Parsed %d listing(s) from email", len(listings))

        for listing in listings:
            # Skip duplicates
            if listing["streeteasy_url"] in existing_urls:
                log.info("Skipping duplicate: %s", listing["streeteasy_url"])
                continue

            if dry_run:
                log.info("[DRY RUN] Would add: %s — $%s", listing["address"], listing.get("cost"))
            else:
                add_listing_to_notion(notion, listing)
                existing_urls.add(listing["streeteasy_url"])
                total_added += 1

        # 5. Archive the email
        if not dry_run:
            archive_email(gmail, msg_id)

    log.info("Done. Added %d new listing(s) to Notion.", total_added)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Apartment Scout")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview parsed listings without writing to Notion or archiving",
    )
    args = parser.parse_args()
    run(dry_run=args.dry_run)
