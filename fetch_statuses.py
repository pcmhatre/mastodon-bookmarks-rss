#!/usr/bin/env python3
import os
import sys
import textwrap
from datetime import datetime, timezone, timedelta

import requests

# Read configuration from environment variables (set via GitHub Secrets)
INSTANCE_URL = os.environ.get("MASTODON_INSTANCE_URL", "").rstrip("/")
ACCESS_TOKEN = os.environ.get("MASTODON_ACCESS_TOKEN", "")
MAX_STATUSES = int(os.environ.get("MAX_STATUSES", "80"))

if not INSTANCE_URL or not ACCESS_TOKEN:
    print("Missing MASTODON_INSTANCE_URL or MASTODON_ACCESS_TOKEN", file=sys.stderr)
    sys.exit(1)

SESSION = requests.Session()
SESSION.headers.update({
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Accept": "application/json",
})


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
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("'", "&apos;")
    )


def format_date(dt_str: str | None) -> str:
    """Format an ISO date string as RFC 822 (for RSS pubDate)."""
    if not dt_str:
        return datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        return dt.strftime("%a, %d %b %Y %H:%M:%S GMT")
    except Exception:
        return datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")


def parse_link_header(header: str | None) -> dict:
    """
    Parse Mastodon's HTTP Link header for pagination links.
    Example:
      <https://.../api/v1/accounts/ID/statuses?max_id=123>; rel="next"
    """
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


def get_own_account_id(instance: str) -> str:
    """Use /api/v1/accounts/verify_credentials to get your own account ID."""
    url = f"{instance}/api/v1/accounts/verify_credentials"
    r = SESSION.get(url, timeout=30)
    r.raise_for_status()
    data = r.json()
    account_id = data.get("id")
    if not account_id:
        print("Could not determine account ID from verify_credentials", file=sys.stderr)
        sys.exit(1)
    return str(account_id)


def fetch_statuses(instance: str, max_items: int):
    """
    Fetch up to max_items of YOUR OWN STATUSES:
      - Excludes reblogs/boosts
      - Excludes replies
      - Excludes direct messages
      - Only keeps posts from the last 2 days
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=2)

    account_id = get_own_account_id(instance)

    # exclude_reblogs=true → drops boosts
    # exclude_replies=true → drops replies
    url = (
        f"{instance}/api/v1/accounts/{account_id}/statuses"
        f"?limit=40&exclude_reblogs=true&exclude_replies=true"
    )

    results: list[dict] = []
    reached_cutoff = False

    while url and len(results) < max_items and not reached_cutoff:
        r = SESSION.get(url, timeout=30)
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, list) or not data:
            break

        for st in data:
            # Skip direct messages (DMs)
            if st.get("visibility") == "direct":
                continue

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

            # Stop once we hit posts older than 2 days
            if created_at < cutoff:
                reached_cutoff = True
                break

            results.append(st)
            if len(results) >= max_items:
                break

        if len(results) >= max_items or reached_cutoff:
            break

        # Follow pagination via Link header
        links = parse_link_header(r.headers.get("Link"))
        url = links.get("next")

    return results[:max_items]


def build_rss(instance: str, statuses: list[dict]) -> str:
    """
    Build an RSS 2.0 feed from a list of your Mastodon status objects.
    Note: we intentionally omit the XML declaration.
    """
    now = datetime.now(timezone.utc)
    items = []

    for st in statuses:
        content_html = st.get("content") or ""
        content_text = strip_html(content_html).strip()

        link = extract_first_link(content_html) or st.get("url") or instance
        account = st.get("account") or {}
        handle = account.get("acct") or "me"

        # Choose a title: use CW/spoiler if present, else first line, else fallback
        spoiler = (st.get("spoiler_text") or "").strip()
        if spoiler:
            title = spoiler
        else:
            if content_text:
                title = content_text.split("\n", 1)[0]
            else:
                title = f"Post by @{handle}"

        if len(title) > 120:
            title = title[:117] + "..."

        description = content_text or f"Post by @{handle}"
        pub_date = format_date(st.get("created_at"))

        item = textwrap.dedent(
            f"""
            <item>
              <title>{escape_xml(title)}</title>
              <link>{escape_xml(link)}</link>
              <guid isPermaLink="false">{escape_xml(st.get("id") or link)}</guid>
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
        f'  <title>Mastodon Posts RSS (last 2 days, no replies/boosts)</title>\n'
        f'  <link>{escape_xml(instance)}</link>\n'
        f'  <description>RSS feed generated from my Mastodon posts (last 2 days, originals only)</description>\n'
        f'  <lastBuildDate>{now.strftime("%a, %d %b %Y %H:%M:%S GMT")}</lastBuildDate>\n'
        f'{rss_items}\n'
        f'</channel>\n'
        f'</rss>\n'
    )

    return rss


def main():
    print(
        f"Fetching up to {MAX_STATUSES} statuses from {INSTANCE_URL} "
        "(no replies, no boosts, last 2 days only) ...",
        file=sys.stderr,
    )
    statuses = fetch_statuses(INSTANCE_URL, MAX_STATUSES)
    print(f"Fetched {len(statuses)} statuses after filtering", file=sys.stderr)

    rss = build_rss(INSTANCE_URL, statuses)
    output_path = "mastodon-statuses.xml"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(rss)

    print(f"Wrote RSS to {output_path}", file=sys.stderr)


if __name__ == "__main__":
    main()