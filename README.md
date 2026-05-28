# mastodon-bookmarks-rss

Generates RSS feeds from your Mastodon bookmarks and posts, deployed daily to GitHub Pages via GitHub Actions.

**Live feeds:** https://pcmhatre.github.io/mastodon-bookmarks-rss/

- [`mastodon-bookmarks.xml`](https://pcmhatre.github.io/mastodon-bookmarks-rss/mastodon-bookmarks.xml) — bookmarks from the last 24 hours
- [`mastodon-statuses.xml`](https://pcmhatre.github.io/mastodon-bookmarks-rss/mastodon-statuses.xml) — your own posts from the last 24 hours (no replies or boosts)

## How it works

Two Python scripts call the Mastodon API and write RSS 2.0 XML files. A GitHub Actions workflow runs them on a schedule and deploys the output to GitHub Pages.

```
fetch_bookmarks.py   →  mastodon-bookmarks.xml
fetch_statuses.py    →  mastodon-statuses.xml
```

The workflow runs daily at 06:00 UTC and can also be triggered manually from the Actions tab.

## Setup

### 1. Fork this repository

### 2. Enable GitHub Pages

Go to **Settings → Pages** and set the source to **GitHub Actions**.

### 3. Create a Mastodon access token

In your Mastodon account go to **Preferences → Development → New Application**.

Grant these scopes:
- `read:bookmarks`
- `read:statuses`

Copy the access token from the application page.

### 4. Add GitHub secrets

Go to **Settings → Secrets and variables → Actions** and add:

| Secret | Example value |
|--------|---------------|
| `MASTODON_INSTANCE_URL` | `https://mastodon.social` |
| `MASTODON_ACCESS_TOKEN` | your token from step 3 |
| `MAX_BOOKMARKS` | `80` (optional, defaults to 80) |
| `MAX_STATUSES` | `80` (optional, defaults to 80) |

> **Important:** `MASTODON_INSTANCE_URL` must include the `https://` scheme.

### 5. Update `PAGES_BASE_URL`

In both `fetch_bookmarks.py` and `fetch_statuses.py`, update the `PAGES_BASE_URL` constant to match your GitHub Pages URL:

```python
PAGES_BASE_URL = "https://YOUR-USERNAME.github.io/mastodon-bookmarks-rss/"
```

### 6. Run the workflow

Trigger it manually from **Actions → Generate Mastodon RSS feeds → Run workflow**, or wait for the next scheduled run.

## Requirements

- Python 3.12+
- [`requests`](https://pypi.org/project/requests/)
