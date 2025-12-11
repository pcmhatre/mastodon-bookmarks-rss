#!/usr/bin/env python3
import os
import sys
import textwrap
from datetime import datetime, timezone, timedelta

import requests

# Configuration from environment (set as GitHub Secrets)
INSTANCE_URL = os.environ.get("MASTODON_INSTANCE_URL", "").rstrip("/")
ACCESS_TOKEN = os.environ.get("MASTODON_ACCESS_TOKEN", "")

raw_max = os.environ.get("MAX_BOOKMARKS", "").strip()
MAX_BOOKMARKS = int(raw_max) if raw_max.isdigit() else 80

if not INSTANCE_URL or not ACCESS_TOKEN:
    print("Missing MASTODON_INSTANCE_URL or MASTODON_ACCESS_TOKEN", file=sys.stderr)
    sys.exit(1)

SESSION = requests.Session()
SESSION.headers.update({
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Accept": "application/json",
})

# Public URL of your GitHub Pages site (for fallback links)
PAGES_BASE_URL = "https://pcmhatre.github.io/mastodon-bookmarks-rss/"  # <-- change YOUR-USERNAME


def strip_html(html: str) -> str:
    """Remove HTML tags and return plain text."""
    from html.parser import HTMLParser

    class Stripper(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts = []

        def handle_data(self, data):
            self.parts.append(data)

    s = Stripper()
    s.feed(html or "")
    return "".join(s.parts)


def extract_first_link(html: str) -> str | None:
    """Extract the first <a href="..."> link from HTML, if any."""
    from html.parser import HTMLParser

    class Finder(HTMLParser):
        def __init__(self):
            super().__init__()
            self.href = None

        def handle_starttag(self, tag, attrs):
            if self.href is not None:
                return
            if tag.lower() != "a":
                return
            for k, v in attrs:
                if k.lower() == "href":
                    self.href = v
                    break

    f = Finder()
    f.feed(html or "")
    return f.href


def escape_xml(text: str) -> str:
    """Escape special XML characters."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt>")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def parse_link_header(header: str | None) -> dict:
    """Parse Mastodon's HTTP Link header for pagination links."""
    if not header:
        return {}
    links = {}
    parts = header.split(",")
    for part in parts:
        section = part.strip().split(";")
        if len(section) < 2:
            continue
        url_part = section[0].strip()
        if not (url_part.startswith("<") and url_part.endswith(">")):
            continue
        url = url_part[1:-1]  # remove <>
        rel = None
        for a in section[1:]:
            a = a.strip()
            if a.startswith("rel="):
                rel = a.split("=", 1)[1].strip('"')
        if rel:
            links[rel] = url
    return links


def fetch_bookmarks(instance: str, max_items: int):
    """
    Fetch up to max_items bookmarks from the Mastodon API, following pagination.
    """
    url = f"{instance}/api/v1/bookmarks?limit=40"
    results: list[dict] = []

    while url and len(results) < max_items:
        r = SESSION.get(url, timeout=30)
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, list) or not data:
            break

        results.extend(data)

        if len(results) >= max_items:
            break

        links = parse_link_header(r.headers.get("Link"))
        url = links.get("next")

    return results[:max_items]


def build_rss(instance: str, statuses: list[dict]) -> str:
    """
    Build an RSS 2.0 feed from a list of Mastodon bookmark status objects.
    Only include items from the last 24 hours.
    Note: no XML declaration; IFTTT-friendly.
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=1)
    items = []

    for st in statuses:
        # Filter by created_at – skip items older than 24 hours
        created_at_str = st.get("created_at")
        if created_at_str:
            try:
                created_at = datetime.fromisoformat(
                    created_at_str.replace("Z", "+00:00")
                )
            except Exception:
                created_at = now
        else:
            created_at = now

        if created_at < cutoff:
            continue

        content_html = st.get("content") or ""
        content_text = strip_html(content_html).strip()

        external_link = extract_first_link(content_html)

        # Prefer the external URL (the thing being bookmarked), otherwise fallback to your Pages index.
        link = external_link or PAGES_BASE_URL

        account = st.get("account") or {}
        handle = account.get("acct") or "unknown"

        # Title: spoiler/CW if present, else first line, else fallback
        spoiler = (st.get("spoiler_text") or "").strip()
        if spoiler:
            title = spoiler
        else:
            if content_text:
                title = content_text.split("\n", 1)[0]
            else:
                title = f"Bookmark from @{handle}"

        if len(title) > 120:
            title = title[:117] + "..."

        description = content_text or f"Bookmark from @{handle}"

        # pubDate = time of this run (good for IFTTT freshness)
        pub_date = now.strftime("%a, %d %b %Y %H:%M:%S GMT")

        # Stable GUID per bookmark
        guid_val = f"bookmark-{st.get('id')}"
        guid = escape_xml(guid_val)

        item = textwrap.dedent(
            f"""
            <item>
              <title>{escape_xml(title)}</title>
              <link>{escape_xml(link)}</link>
              <guid isPermaLink="false">{guid}</guid>
              <pubDate>{pub_date}</pubDate>
              <description>{escape_xml(description)}</description>
            </item>
            """
        ).strip()

        items.append(item)

    rss_items = "\n".join(items)

    rss = (
        f'<rss version="2.0">\n'
        f'<channel>\n'
        f'  <title>Mastodon Bookmarks RSS (last 24h)</title>\n'
        f'  <link>{escape_xml(instance)}</link>\n'
        f'  <description>RSS feed generated from my Mastodon bookmarks (last 24 hours)</description>\n'
        f'  <lastBuildDate>{now.strftime("%a, %d %b %Y %H:%M:%S GMT")}</lastBuildDate>\n'
        f'{rss_items}\n'
        f'</channel>\n'
        f'</rss>\n'
    )

    return rss


def main():
    print(
        f"Fetching up to {MAX_BOOKMARKS} bookmarks from {INSTANCE_URL} ...",
        file=sys.stderr,
    )
    bookmarks = fetch_bookmarks(INSTANCE_URL, MAX_BOOKMARKS)
    print(f"Fetched {len(bookmarks)} bookmarks", file=sys.stderr)

    rss = build_rss(INSTANCE_URL, bookmarks)
    output_path = "mastodon-bookmarks.xml"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(rss)

    print(f"Wrote RSS to {output_path}", file=sys.stderr)


if __name__ == "__main__":
    main()