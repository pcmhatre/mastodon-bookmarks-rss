#!/usr/bin/env python3
import os
import sys
import textwrap
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs

import requests

INSTANCE_URL = os.environ.get("MASTODON_INSTANCE_URL", "").rstrip("/")
ACCESS_TOKEN = os.environ.get("MASTODON_ACCESS_TOKEN", "")
MAX_BOOKMARKS = int(os.environ.get("MAX_BOOKMARKS", "80"))  # hard cap

if not INSTANCE_URL or not ACCESS_TOKEN:
    print("Missing MASTODON_INSTANCE_URL or MASTODON_ACCESS_TOKEN", file=sys.stderr)
    sys.exit(1)

SESSION = requests.Session()
SESSION.headers.update({
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Accept": "application/json",
})


def strip_html(html: str) -> str:
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


def extract_first_link_from_html(html: str) -> str | None:
    from html.parser import HTMLParser

    class LinkFinder(HTMLParser):
        def __init__(self):
            super().__init__()
            self.first_href = None

        def handle_starttag(self, tag, attrs):
            if self.first_href is not None:
                return
            if tag.lower() != "a":
                return
            for k, v in attrs:
                if k.lower() == "href":
                    self.first_href = v
                    break

    lf = LinkFinder()
    lf.feed(html or "")
    return lf.first_href


def escape_xml(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def format_rfc822(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")


def parse_link_header(link_header: str | None) -> dict:
    """Parse Mastodon Link header for pagination."""
    links = {}
    if not link_header:
        return links
    parts = link_header.split(",")
    for part in parts:
        section = part.strip().split(";")
        if len(section) < 2:
            continue
        url_part = section[0].strip()
        if not (url_part.startswith("<") and url_part.endswith(">")):
            continue
        url = url_part[1:-1]
        rel = None
        for attr in section[1:]:
            attr = attr.strip()
            if attr.startswith("rel="):
                rel = attr.split("=", 1)[1].strip('"')
        if rel:
            links[rel] = url
    return links


def fetch_bookmarks(instance_url: str, max_items: int):
    url = f"{instance_url}/api/v1/bookmarks?limit=40"
    collected = []

    while url and len(collected) < max_items:
        resp = SESSION.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list) or not data:
            break

        collected.extend(data)
        if len(collected) >= max_items:
            break

        links = parse_link_header(resp.headers.get("Link"))
        url = links.get("next")

    return collected[:max_items]


def build_rss(instance_url: str, bookmarks: list[dict]) -> str:
    now = datetime.now(timezone.utc)
    channel_title = f"Mastodon bookmarks ({instance_url})"
    channel_link = instance_url
    channel_desc = "RSS feed generated automatically from Mastodon bookmarks"

    items_xml = []

    for status in bookmarks:
        content_html = status.get("content") or ""
        content_text = strip_html(content_html).strip()
        status_url = status.get("url") or ""
        external_link = extract_first_link_from_html(content_html)
        link = external_link or status_url or instance_url

        account = status.get("account") or {}
        acct = account.get("acct") or "unknown"

        spoiler = (status.get("spoiler_text") or "").strip()
        if spoiler:
            title = spoiler
        else:
            first_line = content_text.splitlines()[0] if content_text else ""
            title = first_line or f"Toot by @{acct}"

        if len(title) > 120:
            title = title[:117] + "..."

        created_at = status.get("created_at")
        bookmarked_at = status.get("bookmarked_at")
        pub_dt = None
        for candidate in (bookmarked_at, created_at):
            if not candidate:
                continue
            try:
                pub_dt = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
                break
            except Exception:
                continue
        if pub_dt is None:
            pub_dt = now

        description = content_text or f"Toot by @{acct} at {status_url}"

        item_xml = textwrap.dedent(
            f"""\
            <item>
              <title>{escape_xml(title)}</title>
              <link>{escape_xml(link)}</link>
              <guid isPermaLink="false">{escape_xml(status.get("id") or link)}</guid>
              <pubDate>{format_rfc822(pub_dt)}</pubDate>
              <description>{escape_xml(description)}</description>
            </item>"""
        )
        items_xml.append(item_xml)

    items_joined = "\n".join(items_xml)

    rss = textwrap.dedent(
        f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
        <channel>
          <title>{escape_xml(channel_title)}</title>
          <link>{escape_xml(channel_link)}</link>
          <description>{escape_xml(channel_desc)}</description>
          <lastBuildDate>{format_rfc822(now)}</lastBuildDate>
        {items_joined}
        </channel>
        </rss>
        """
    )
    return rss


def main():
    print(f"Fetching bookmarks from {INSTANCE_URL}", file=sys.stderr)
    bookmarks = fetch_bookmarks(INSTANCE_URL, MAX_BOOKMARKS)
    print(f"Fetched {len(bookmarks)} bookmarks", file=sys.stderr)

    rss = build_rss(INSTANCE_URL, bookmarks)
    output_path = "mastodon-bookmarks.xml"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(rss)

    print(f"Wrote RSS to {output_path}", file=sys.stderr)


if __name__ == "__main__":
    main()