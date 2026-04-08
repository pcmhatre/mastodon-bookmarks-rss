#!/usr/bin/env python3
import os
import sys
import textwrap
from datetime import datetime, timezone, timedelta
import html as pyhtml

import requests

INSTANCE_URL = os.environ.get("MASTODON_INSTANCE_URL", "").rstrip("/")
ACCESS_TOKEN = os.environ.get("MASTODON_ACCESS_TOKEN", "")

raw_max = os.environ.get("MAX_STATUSES", "").strip()
MAX_STATUSES = int(raw_max) if raw_max.isdigit() else 80

if not INSTANCE_URL or not ACCESS_TOKEN:
    print("Missing MASTODON_INSTANCE_URL or MASTODON_ACCESS_TOKEN", file=sys.stderr)
    sys.exit(1)

SESSION = requests.Session()
SESSION.headers.update({
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Accept": "application/json",
})

PAGES_BASE_URL = "https://pcmhatre.github.io/mastodon-bookmarks-rss/"  # <-- change YOUR-USERNAME


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


def extract_first_link(html: str) -> str | None:
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
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def cdata(text: str) -> str:
    return "<![CDATA[" + text.replace("]]>", "]]]]><![CDATA[>") + "]]>"


def parse_link_header(header: str | None) -> dict:
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
        url = url_part[1:-1]
        rel = None
        for a in section[1:]:
            a = a.strip()
            if a.startswith("rel="):
                rel = a.split("=", 1)[1].strip('"')
        if rel:
            links[rel] = url
    return links


def get_own_account_id(instance: str) -> str:
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
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=1)

    account_id = get_own_account_id(instance)

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
            if st.get("visibility") == "direct":
                continue

            created_at_str = st.get("created_at")
            if created_at_str:
                try:
                    created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                except Exception:
                    created_at = now
            else:
                created_at = now

            if created_at < cutoff:
                reached_cutoff = True
                break

            results.append(st)
            if len(results) >= max_items:
                break

        if len(results) >= max_items or reached_cutoff:
            break

        links = parse_link_header(r.headers.get("Link"))
        url = links.get("next")

    return results[:max_items]


def _mime_for_attachment(att: dict) -> str:
    mtype = (att.get("type") or "").lower()
    if mtype == "image":
        return "image/jpeg"
    if mtype in ("gifv", "video"):
        return "video/mp4"
    if mtype == "audio":
        return "audio/mpeg"
    return "application/octet-stream"


def extract_media_urls(st: dict, limit: int = 4) -> list[str]:
    urls: list[str] = []
    media_attachments = st.get("media_attachments") or []
    for att in media_attachments[:limit]:
        if not isinstance(att, dict):
            continue
        u = att.get("url") or att.get("preview_url")
        if u:
            urls.append(u)
    return urls


def media_rss_blocks(st: dict, limit: int = 4) -> str:
    media_attachments = st.get("media_attachments") or []
    if not media_attachments:
        return ""

    lines: list[str] = []
    for att in media_attachments[:limit]:
        if not isinstance(att, dict):
            continue
        media_url = att.get("url") or att.get("preview_url")
        if not media_url:
            continue
        esc_url = escape_xml(media_url)
        mime = _mime_for_attachment(att)
        lines.append(f'      <enclosure url="{esc_url}" length="0" type="{mime}" />')
        lines.append(f'      <media:content url="{esc_url}" />')

    return ("\n" + "\n".join(lines)) if lines else ""


def build_description_html(text: str, media_urls: list[str]) -> str:
    safe_text = pyhtml.escape(text or "")
    parts: list[str] = []
    if safe_text:
        parts.append(f"<p>{safe_text}</p>")

    if media_urls:
        first = pyhtml.escape(media_urls[0], quote=True)
        parts.append(f'<p><img src="{first}" alt="image" /></p>')

        if len(media_urls) > 1:
            parts.append("<p>More images:</p><ul>")
            for u in media_urls[1:4]:
                esc = pyhtml.escape(u, quote=True)
                parts.append(f'<li><a href="{esc}">{esc}</a></li>')
            parts.append("</ul>")

    return "\n".join(parts) if parts else "<p></p>"


def build_rss(instance: str, statuses: list[dict]) -> str:
    now = datetime.now(timezone.utc)
    items: list[str] = []

    for st in statuses:
        content_html = st.get("content") or ""
        content_text = strip_html(content_html).strip()

        external_link = extract_first_link(content_html)
        link = external_link or PAGES_BASE_URL

        account = st.get("account") or {}
        handle = account.get("acct") or "me"

        spoiler = (st.get("spoiler_text") or "").strip()
        if spoiler:
            title = spoiler
        else:
            title = content_text.split("\n", 1)[0] if content_text else f"Post by @{handle}"
        if len(title) > 120:
            title = title[:117] + "..."

        pub_date = now.strftime("%a, %d %b %Y %H:%M:%S GMT")
        guid = escape_xml(f"status-{st.get('id')}")

        media_urls = extract_media_urls(st, limit=4)
        media_block = media_rss_blocks(st, limit=4)
        desc_html = build_description_html(content_text or f"Post by @{handle}", media_urls)

        item = textwrap.dedent(
            f"""
            <item>
              <title>{escape_xml(title)}</title>
              <link>{escape_xml(link)}</link>{media_block}
              <guid isPermaLink="false">{guid}</guid>
              <pubDate>{pub_date}</pubDate>
              <description>{cdata(desc_html)}</description>
            </item>
            """
        ).strip()

        items.append(item)

    rss_items = "\n".join(items)

    rss = (
        f'<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">\n'
        f'<channel>\n'
        f'  <title>Mastodon Posts RSS (last 24h, no replies/boosts)</title>\n'
        f'  <link>{escape_xml(instance)}</link>\n'
        f'  <description>RSS feed generated from my Mastodon posts (last 24 hours, originals only)</description>\n'
        f'  <lastBuildDate>{now.strftime("%a, %d %b %Y %H:%M:%S GMT")}</lastBuildDate>\n'
        f'{rss_items}\n'
        f'</channel>\n'
        f'</rss>\n'
    )
    return rss


def main():
    print(
        f"Fetching up to {MAX_STATUSES} statuses from {INSTANCE_URL} "
        "(no replies, no boosts, last 24 hours only) ...",
        file=sys.stderr,
    )
    statuses = fetch_statuses(INSTANCE_URL, MAX_STATUSES)
    print(f"Fetched {len(statuses)} statuses after filtering", file=sys.stderr)

    rss = build_rss(INSTANCE_URL, statuses)
    with open("mastodon-statuses.xml", "w", encoding="utf-8") as f:
        f.write(rss)

    print("Wrote RSS to mastodon-statuses.xml", file=sys.stderr)


if __name__ == "__main__":
    main()
