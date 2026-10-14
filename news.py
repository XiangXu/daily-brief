"""News headlines, grouped by topic, filtered and ranked by keyword.

Each group in config.yaml has its own feeds and slot count, so every topic
gets its place however busy the others are. Headlines keep the language of
their feed.
"""

import calendar
import html
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urldefrag, urlencode, urljoin, urlsplit, urlunsplit

import feedparser
import requests

log = logging.getLogger(__name__)

TAGS = re.compile(r"<[^>]+>")
ARTICLE = re.compile(r"<article\b.*?</article>", re.DOTALL)
ARTICLE_HEADLINE = re.compile(r"<h[1-4][^>]*>\s*<a\s+[^>]*?href=\"([^\"]+)\"[^>]*>(.*?)</a>", re.DOTALL)
ARTICLE_TIME = re.compile(r'<time[^>]*datetime="([^"]+)"')
PARAGRAPH = re.compile(r"<p[^>]*>(.*?)</p>", re.DOTALL)
NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL)
AUTOHOME_ITEM = re.compile(r"<li[^>]*data-artidanchor[^>]*>.*?</li>", re.DOTALL)
AUTOHOME_LINK = re.compile(r'<a href="([^"]+/news/\d+/\d+\.html[^"]*)"')
AUTOHOME_TITLE = re.compile(r"<h3[^>]*>(.*?)</h3>", re.DOTALL)
AUTOHOME_WHEN = re.compile(r'<span class="fn-left">(.*?)</span>', re.DOTALL)
CHINA = timezone(timedelta(hours=8))
# Group 1 is the opening tag (it holds the href), group 2 the title.
CARWOW_TITLE = re.compile(
    r"(<a\s[^>]*class=['\"]card-minimalist__title['\"][^>]*>)(.*?)</a>", re.DOTALL
)
HREF = re.compile(r"href=['\"]([^'\"]+)['\"]")
POST_ID = re.compile(r"/news/(\d+)/")
SINA_CARD = re.compile(r'<a class="f_card[^"]*"[^>]*?href="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL)
SINA_TITLE = re.compile(r'<h2[^>]*class="cm_tit"[^>]*>(.*?)</h2>', re.DOTALL)
URL_DATE = re.compile(r"/(20\d{2})-(\d{2})-(\d{2})/")
HUPU_THREAD = re.compile(
    r'<a\s+href="([^"]+)"[^>]*class="p-title"[^>]*>(.*?)</a>', re.DOTALL
)
TITLE_WEIGHT = 2
SUMMARY_WEIGHT = 1


def get_headlines(config) -> list[dict] | None:
    """Return `[{"name", "items"}, ...]` for the groups that have news.

    A group with no items (feeds down, or nothing passed its filter) is left
    out. None means there is no news at all.
    """
    settings = config["news"]
    groups = settings["groups"]
    # One batch for every group, so slow sites overlap instead of queueing.
    pages = iter(_download([feed for group in groups for feed in group["feeds"]], settings))

    found = []
    for group in groups:
        items = _read_feeds(group["feeds"], [next(pages) for _ in group["feeds"]], settings)
        shared = settings["keywords"] if group.get("use_global_keywords", True) else []
        picked = select(
            items,
            limit=group["limit"],
            keywords=[*shared, *group.get("keywords", [])],
            max_per_source=group.get("max_per_source") or settings["max_per_source"],
            min_score=group.get("min_score", 0),
        )
        if picked:
            found.append({"name": group["name"], "items": picked})
        else:
            log.warning("News group %r has no items", group["name"])
    if not found:
        log.error("News unavailable: no items from any group")
        return None
    return found


def fetch_feeds(feeds, settings) -> list[dict]:
    """Fetch items from the feeds, in feed order. A failing feed is logged and skipped.

    Each feed is a URL (RSS or Atom) or a mapping with `url`, an optional
    `name`, and `parser` (a key of `PARSERS`) for pages that have no feed.
    """
    return _read_feeds(feeds, _download(feeds, settings), settings)


def _download(feeds, settings) -> list:
    """Fetch every feed at once. Per feed: the body, the error, or None (not fetched).

    Results come back in feed order whatever order the sites answer in.
    """

    def get(feed):
        url, parser, _ = _describe(feed)
        if parser not in PARSERS:
            return None
        try:
            # Some sites reject requests that do not look like a browser.
            resp = requests.get(
                url,
                headers={"User-Agent": settings["user_agent"]},
                timeout=settings["timeout_seconds"],
            )
            resp.raise_for_status()
            return resp.content
        except requests.RequestException as exc:
            return exc

    with ThreadPoolExecutor(max_workers=settings["max_workers"]) as pool:
        return list(pool.map(get, feeds))


def _read_feeds(feeds, pages, settings) -> list[dict]:
    """Turn downloaded pages into items: named, cleaned, fresh, and capped per feed."""
    oldest = time.time() - settings["max_age_hours"] * 3600
    items = []
    for feed, page in zip(feeds, pages):
        url, parser, name = _describe(feed)
        if parser not in PARSERS:
            log.error("Feed %s: unknown parser %r", url, parser)
            continue
        if isinstance(page, Exception):
            log.warning("Feed %s failed: %s", url, type(page).__name__)
            continue
        try:
            entries = PARSERS[parser](page, url)
        except Exception as exc:  # noqa: BLE001 - a site redesign breaks only its own feed
            log.warning("Feed %s could not be read: %s", url, type(exc).__name__)
            continue
        if not entries:
            log.warning("Feed %s returned no items (did the site change?)", url)
        fresh = [e for e in entries if _is_fresh(e, oldest)]
        for entry in fresh[: settings["max_items_per_feed"]]:
            title = _clean(entry["title"])
            if title:
                items.append(
                    {
                        "source": name or entry["source"] or _domain(url),
                        "title": title,
                        "link": _strip_tracking(entry["link"]),
                        "summary": _clean(entry["summary"])[: settings["summary_max_chars"]],
                        "published": entry["published"],
                    }
                )
    return items


def _describe(feed):
    if isinstance(feed, str):
        return feed, None, None
    return feed["url"], feed.get("parser"), feed.get("name")


def _item(title, link, summary="", published=None, source=None) -> dict:
    """One raw entry from a parser. `published` is a UTC struct_time or None (undated)."""
    return {"source": source, "title": title, "link": link, "summary": summary, "published": published}


# ---- parsers: (page bytes, page url) -> raw entries -------------------------


def _read_rss(content, url):
    parsed = feedparser.parse(content)
    return [
        _item(
            entry.get("title", ""),
            entry.get("link", ""),
            entry.get("summary", ""),
            # feedparser gives dates as UTC struct_time.
            entry.get("published_parsed") or entry.get("updated_parsed"),
            source=parsed.feed.get("title"),
        )
        for entry in parsed.entries
    ]


def _read_hupu(content, url):
    """Threads on a bbs.hupu.com board, in the board's own (hot) order.

    Boards list the last 24 hours and show no machine-readable date, so the
    items count as fresh.
    """
    page = content.decode("utf-8", errors="replace")
    return [_item(title, urljoin(url, href)) for href, title in HUPU_THREAD.findall(page)]


def _read_completecar(content, url):
    """Articles on Complete Car's pages (Irish car site, no feed). Dated by `<time>`."""
    items = []
    for block in ARTICLE.findall(content.decode("utf-8", errors="replace")):
        headline = ARTICLE_HEADLINE.search(block)
        if not headline:
            continue
        when = ARTICLE_TIME.search(block)
        copy = PARAGRAPH.search(block)
        items.append(
            _item(
                headline.group(2),
                urljoin(url, html.unescape(headline.group(1))),
                copy.group(1) if copy else "",
                _iso_time(html.unescape(when.group(1))) if when else None,
            )
        )
    return items


def _read_dongchedi(content, url):
    """Articles in the data 懂车帝's Next.js pages embed. Videos need a login, so are skipped."""
    match = NEXT_DATA.search(content.decode("utf-8", errors="replace"))
    if not match:
        return []
    try:
        data = json.loads(match.group(1))
    except ValueError:
        return []
    items, seen = [], set()
    for node in _walk(data):
        group_id = str(node["group_id"])
        if group_id in seen or str(node.get("article_type", "1")) != "1":
            continue
        seen.add(group_id)
        items.append(_item(node["title"], f"https://www.dongchedi.com/article/{group_id}"))
    return items


def _read_autohome(content, url):
    """News on 汽车之家's news page (GB18030). Dated from its "8小时前" labels."""
    now = time.time()
    items = []
    for block in AUTOHOME_ITEM.findall(content.decode("gb18030", errors="replace")):
        link, title = AUTOHOME_LINK.search(block), AUTOHOME_TITLE.search(block)
        if not (link and title):
            continue
        when = AUTOHOME_WHEN.search(block)
        copy = PARAGRAPH.search(block)
        items.append(
            _item(
                title.group(1),
                urldefrag(urljoin(url, html.unescape(link.group(1)))).url,
                copy.group(1) if copy else "",
                _chinese_ago(_clean(when.group(1)) if when else "", now),
            )
        )
    return items


def _read_carwow(content, url):
    """Cards on Carwow's news page (no feed, no dates), newest post first.

    Post ids only ever grow, so the id in each link orders them.
    """
    cards = []
    for block in ARTICLE.findall(content.decode("utf-8", errors="replace")):
        anchor = CARWOW_TITLE.search(block)
        href = HREF.search(anchor.group(1)) if anchor else None
        if not href:
            continue
        link = urljoin(url, html.unescape(href.group(1)))
        post_id = POST_ID.search(link)
        cards.append((int(post_id.group(1)) if post_id else 0, _item(anchor.group(2), link)))
    return [item for _, item in sorted(cards, key=lambda card: -card[0])]


def _read_sina_ent(content, url):
    """Cards on Sina's mobile entertainment page, the fresh one (the desktop site stalled).

    Articles carry their date in the URL, so they are dated to midday in China;
    the video clips have no date and are kept.
    """
    items = []
    for href, inner in SINA_CARD.findall(content.decode("utf-8", errors="replace")):
        title = SINA_TITLE.search(inner)
        if not title:
            continue
        link = urldefrag(html.unescape(href)).url
        day = URL_DATE.search(link)
        published = _china_time(*map(int, day.groups()), hour=12) if day else None
        items.append(_item(title.group(1), link, published=published))
    return items


PARSERS = {
    None: _read_rss,
    "hupu": _read_hupu,
    "completecar": _read_completecar,
    "dongchedi": _read_dongchedi,
    "autohome": _read_autohome,
    "carwow": _read_carwow,
    "sina_ent": _read_sina_ent,
}


def _walk(node):
    """Every dict under `node` that has a text `title` and a `group_id`."""
    if isinstance(node, dict):
        if isinstance(node.get("title"), str) and "group_id" in node:
            yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def _utc(moment):
    return moment.astimezone(timezone.utc).timetuple()


def _iso_time(value):
    try:
        return _utc(datetime.fromisoformat(value))
    except ValueError:
        return None


def _china_time(year, month, day, hour=0):
    """A date on China's clock as a UTC struct_time, or None if it is not a real date."""
    try:
        return _utc(datetime(year, month, day, hour, tzinfo=CHINA))
    except ValueError:
        return None


def _chinese_ago(text, now):
    """"8小时前", "30分钟前", "刚刚", "昨天", "10-01" as a UTC struct_time; None if unknown."""
    if text == "刚刚":
        return time.gmtime(now)
    if text in ("昨天", "前天"):
        return time.gmtime(now - (86400 if text == "昨天" else 172800))
    if match := re.fullmatch(r"(\d+)\s*(分钟|小时)前", text):
        return time.gmtime(now - int(match.group(1)) * (60 if match.group(2) == "分钟" else 3600))
    if match := re.fullmatch(r"(\d{1,2})-(\d{1,2})", text):
        year = datetime.fromtimestamp(now, CHINA).year
        return _china_time(year, int(match.group(1)), int(match.group(2)))
    return None


# ---- choosing what to show -------------------------------------------------


def select(items, limit, keywords, max_per_source, min_score=0) -> list[dict]:
    """Pick up to `limit` items: best keyword matches first, then feed order.

    Items scoring below `min_score` are dropped. Duplicates are dropped and no
    source may fill more than `max_per_source` slots, so one busy feed cannot
    crowd out the rest.
    """
    patterns = [_pattern(word) for word in keywords]
    scored = [(_score(item, patterns), item) for item in items]
    # sorted() is stable: equal scores keep their feed order.
    ranked = sorted(scored, key=lambda pair: -pair[0])

    picked, per_source, seen = [], {}, set()
    for score, item in ranked:
        if score < min_score:
            continue
        keys = {item["link"], " ".join(item["title"].lower().split())} - {""}
        if keys & seen or per_source.get(item["source"], 0) >= max_per_source:
            continue
        seen |= keys
        per_source[item["source"]] = per_source.get(item["source"], 0) + 1
        picked.append(item)
        if len(picked) == limit:
            break
    return picked


def _pattern(word):
    """Whole-word for English (also next to Chinese text), plain match for Chinese."""
    escaped = re.escape(word)
    if word.isascii():
        escaped = rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])"
    return re.compile(escaped, re.IGNORECASE)


def _score(item, patterns) -> int:
    return sum(
        (TITLE_WEIGHT if p.search(item["title"]) else 0)
        + (SUMMARY_WEIGHT if p.search(item["summary"]) else 0)
        for p in patterns
    )


def _domain(url) -> str:
    host = urlsplit(url).netloc
    return host.removeprefix("www.") or url


def _is_fresh(entry, oldest) -> bool:
    # Undated items are kept.
    published = entry["published"]
    return published is None or calendar.timegm(published) >= oldest


def _clean(text) -> str:
    return " ".join(html.unescape(TAGS.sub("", text)).split())


def _strip_tracking(url) -> str:
    """Drop utm_* tracking parameters so links are short and clean."""
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if not k.startswith("utm_")]
    return urlunsplit(parts._replace(query=urlencode(query)))
