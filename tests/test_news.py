import calendar
import json
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest.mock import Mock, patch

import pytest
import requests

import news

RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>{source}</title>
{items}
</channel></rss>"""


def rss(source, entries):
    """entries: titles, or (title, link) pairs."""
    items = ""
    for entry in entries:
        title, link = entry if isinstance(entry, tuple) else (entry, f"https://x.example/{entry}")
        items += (
            f"<item><title>{title}</title><link>{link}</link>"
            f"<description>About {title}</description></item>"
        )
    return RSS.format(source=source, items=items).encode()


def feed_response(content, status=200):
    resp = Mock(status_code=status, content=content)
    if status >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(f"{status}")
    return resp


def item(title, source="S", summary="", link=None):
    return {
        "source": source,
        "title": title,
        "summary": summary,
        "link": link if link is not None else f"https://x.example/{title}",
    }


@pytest.fixture
def cfg(config):
    news_cfg = config["news"]
    news_cfg["keywords"] = ["java", "kubernetes", "AI", "人工智能"]
    news_cfg["max_per_source"] = 10
    news_cfg["groups"] = [
        {"name": "Group A", "limit": 10, "feeds": ["https://a.example/rss", "https://b.example/rss"]}
    ]
    return config


def fetch(cfg, urls=("https://a.example/rss", "https://b.example/rss")):
    return news.fetch_feeds(list(urls), cfg["news"])


# ---- fetching ----------------------------------------------------------


def test_fetch_feeds_collects_all_feeds_with_links(cfg):
    responses = [
        feed_response(rss("Feed A", ["A1", "A2"])),
        feed_response(rss("Feed B", [("B1", "https://b.example/post")])),
    ]
    with patch.object(news.requests, "get", side_effect=responses) as get:
        items = fetch(cfg)

    assert [(i["source"], i["title"], i["link"]) for i in items] == [
        ("Feed A", "A1", "https://x.example/A1"),
        ("Feed A", "A2", "https://x.example/A2"),
        ("Feed B", "B1", "https://b.example/post"),
    ]
    assert items[0]["summary"] == "About A1"
    assert get.call_args.kwargs["timeout"] == cfg["news"]["timeout_seconds"]


def test_fetch_feeds_limits_items_per_feed(cfg):
    cfg["news"]["max_items_per_feed"] = 2
    content = rss("Feed A", ["A1", "A2", "A3"])
    with patch.object(news.requests, "get", return_value=feed_response(content)):
        assert [i["title"] for i in fetch(cfg, ["https://a.example/rss"])] == ["A1", "A2"]


def test_fetch_feeds_drops_old_items_and_keeps_undated_ones(cfg):
    cfg["news"]["max_age_hours"] = 30
    now = datetime(2026, 10, 13, 7, 30, tzinfo=timezone.utc)

    def dated(title, age_hours):
        date = format_datetime(now - timedelta(hours=age_hours))
        return f"<item><title>{title}</title><pubDate>{date}</pubDate></item>"

    content = RSS.format(
        source="Feed A",
        items=dated("fresh", 29) + dated("stale", 31) + "<item><title>undated</title></item>",
    ).encode()
    with (
        patch.object(news.requests, "get", return_value=feed_response(content)),
        patch.object(news.time, "time", return_value=now.timestamp()),
    ):
        assert [i["title"] for i in fetch(cfg, ["https://a.example/rss"])] == ["fresh", "undated"]


def test_fetch_feeds_strips_html_and_cuts_long_summaries(cfg):
    cfg["news"]["summary_max_chars"] = 20
    content = RSS.format(
        source="Feed A",
        items="<item><title>T</title><description>&lt;p&gt;Hello &lt;b&gt;world&lt;/b&gt; "
        + "x" * 500
        + "&lt;/p&gt;</description></item>",
    ).encode()
    with patch.object(news.requests, "get", return_value=feed_response(content)):
        (found,) = fetch(cfg, ["https://a.example/rss"])

    assert found["summary"].startswith("Hello world x")
    assert len(found["summary"]) == 20


def test_fetch_feeds_strips_utm_tracking_but_keeps_other_query_params(cfg):
    link = "https://a.example/post?id=7&amp;utm_source=feed&amp;utm_medium=rss"
    content = rss("Feed A", [("T", link)])
    with patch.object(news.requests, "get", return_value=feed_response(content)):
        (found,) = fetch(cfg, ["https://a.example/rss"])
    assert found["link"] == "https://a.example/post?id=7"


def test_fetch_feeds_reads_chinese_feeds(cfg):
    content = rss("少数派", [("小米手表 S5 发布", "https://sspai.com/post/1")])
    with patch.object(news.requests, "get", return_value=feed_response(content)):
        (found,) = fetch(cfg, ["https://sspai.com/feed"])
    assert (found["source"], found["title"]) == ("少数派", "小米手表 S5 发布")


def test_fetch_feeds_skips_a_failing_feed(cfg):
    responses = [requests.ConnectionError("down"), feed_response(rss("Feed B", ["B1"]))]
    with patch.object(news.requests, "get", side_effect=responses):
        assert [i["title"] for i in fetch(cfg)] == ["B1"]


def test_fetch_feeds_skips_http_errors_and_garbage(cfg):
    responses = [feed_response(b"", status=503), feed_response(b"not a feed")]
    with patch.object(news.requests, "get", side_effect=responses):
        assert fetch(cfg) == []


# ---- selecting ---------------------------------------------------------


def titles(items):
    return [i["title"] for i in items]


def pick(items, cfg, limit=10, keywords=None, max_per_source=None, min_score=0):
    """`select` with the config's keywords and source cap unless given."""
    settings = cfg["news"]
    return news.select(
        items,
        limit,
        settings["keywords"] if keywords is None else keywords,
        max_per_source or settings["max_per_source"],
        min_score,
    )


def test_select_ranks_keyword_matches_first_and_keeps_feed_order_otherwise(cfg):
    items = [
        item("Cooking tips"),
        item("Java 27 released"),
        item("Gardening"),
        item("Kubernetes 1.40"),
    ]
    assert titles(pick(items, cfg)) == [
        "Java 27 released",
        "Kubernetes 1.40",
        "Cooking tips",
        "Gardening",
    ]


def test_select_weights_title_matches_above_summary_matches(cfg):
    items = [
        item("Plain title", summary="mentions java in passing"),
        item("Java in the title"),
    ]
    assert titles(pick(items, cfg)) == ["Java in the title", "Plain title"]


def test_select_counts_distinct_keywords(cfg):
    items = [item("Java java java"), item("Java on Kubernetes")]
    assert titles(pick(items, cfg)) == ["Java on Kubernetes", "Java java java"]


def test_select_matches_whole_english_words_only(cfg):
    items = [item("Said the JavaScript team"), item("Java news")]
    assert titles(pick(items, cfg)) == ["Java news", "Said the JavaScript team"]


def test_select_matches_english_words_next_to_chinese_characters(cfg):
    items = [item("今天的天气"), item("使用Java开发后端"), item("基于Kubernetes的部署")]
    assert titles(pick(items, cfg)) == ["使用Java开发后端", "基于Kubernetes的部署", "今天的天气"]


def test_select_matches_chinese_keywords_anywhere_in_the_text(cfg):
    items = [item("体育新闻"), item("人工智能进入课堂"), item("普通", summary="谈谈人工智能")]
    assert titles(pick(items, cfg)) == ["人工智能进入课堂", "普通", "体育新闻"]


def test_select_is_case_insensitive(cfg):
    items = [item("something else"), item("KUBERNETES day")]
    assert titles(pick(items, cfg))[0] == "KUBERNETES day"


def test_select_caps_items_per_source_and_total(cfg):
    cfg["news"]["max_per_source"] = 2
    items = [item(f"Java {n}", source="A") for n in range(5)] + [
        item("Other 1", source="B"),
        item("Other 2", source="B"),
        item("Other 3", source="B"),
    ]
    assert titles(pick(items, cfg, limit=4)) == ["Java 0", "Java 1", "Other 1", "Other 2"]


def test_select_drops_duplicate_links_and_titles(cfg):
    items = [
        item("Java 27", source="A", link="https://x.example/1"),
        item("Java 27 - different title", source="B", link="https://x.example/1"),
        item("  JAVA 27 ", source="C", link="https://x.example/3"),
        item("Different", source="D", link="https://x.example/4"),
    ]
    assert titles(pick(items, cfg)) == ["Java 27", "Different"]


def test_select_with_no_keywords_keeps_feed_order(cfg):
    cfg["news"]["keywords"] = []
    assert titles(pick([item("B"), item("A")], cfg)) == ["B", "A"]


# ---- headlines ---------------------------------------------------------


def test_get_headlines_returns_one_entry_per_group_with_its_own_limit(cfg):
    cfg["news"]["groups"] = [
        {"name": "Ireland", "limit": 1, "feeds": ["https://ie.example/rss"]},
        {"name": "Tech", "limit": 2, "feeds": ["https://tech.example/rss"]},
    ]
    responses = [
        feed_response(rss("RTE", ["Weather", "Budget"])),
        feed_response(rss("InfoQ", ["Cooking", "Java 27", "Gardening"])),
    ]
    with patch.object(news.requests, "get", side_effect=responses):
        groups = news.get_headlines(cfg)

    assert [g["name"] for g in groups] == ["Ireland", "Tech"]
    assert titles(groups[0]["items"]) == ["Weather"]
    assert titles(groups[1]["items"]) == ["Java 27", "Cooking"]
    assert groups[1]["items"][0]["link"] == "https://x.example/Java 27"


def test_get_headlines_leaves_out_a_group_whose_feeds_all_fail(cfg):
    cfg["news"]["groups"] = [
        {"name": "Down", "limit": 3, "feeds": ["https://down.example/rss"]},
        {"name": "Up", "limit": 3, "feeds": ["https://up.example/rss"]},
    ]
    responses = [requests.ConnectionError("down"), feed_response(rss("Up feed", ["U1"]))]
    with patch.object(news.requests, "get", side_effect=responses):
        groups = news.get_headlines(cfg)
    assert [g["name"] for g in groups] == ["Up"]


def test_get_headlines_is_none_when_every_feed_is_down(cfg, caplog):
    with patch.object(news.requests, "get", side_effect=requests.ConnectionError("down")):
        assert news.get_headlines(cfg) is None
    assert "News unavailable" in caplog.text


def test_select_ranks_by_whatever_keywords_it_is_given(cfg):
    items = [item("Plain"), item("Lakers win again")]
    with_lakers = [*cfg["news"]["keywords"], "Lakers"]
    assert titles(pick(items, cfg, keywords=with_lakers)) == ["Lakers win again", "Plain"]
    assert titles(pick(items, cfg)) == ["Plain", "Lakers win again"]


def test_select_min_score_drops_items_with_too_few_matches(cfg):
    items = [item("Cooking"), item("Java news"), item("Gardening")]
    assert titles(pick(items, cfg, min_score=1)) == ["Java news"]
    assert pick([item("Cooking")], cfg, min_score=1) == []


def test_get_headlines_applies_group_keywords_and_min_score(cfg):
    cfg["news"]["groups"] = [
        {
            "name": "Film",
            "limit": 5,
            "min_score": 1,
            "keywords": ["电影"],
            "feeds": ["https://c.example/rss"],
        },
        {"name": "Empty after filter", "limit": 5, "min_score": 1, "feeds": ["https://d.example/rss"]},
    ]
    responses = [
        feed_response(rss("中新网文化", ["大遗址保护", "新电影官宣", "舞台剧亮相"])),
        feed_response(rss("Other", ["Cooking"])),
    ]
    with patch.object(news.requests, "get", side_effect=responses):
        groups = news.get_headlines(cfg)

    assert [g["name"] for g in groups] == ["Film"]
    assert titles(groups[0]["items"]) == ["新电影官宣"]


def test_fetch_feeds_sends_the_configured_user_agent(cfg):
    cfg["news"]["user_agent"] = "daily-brief-test/1.0"
    with patch.object(news.requests, "get", return_value=feed_response(rss("A", ["A1"]))) as get:
        fetch(cfg, ["https://a.example/rss"])
    assert get.call_args.kwargs["headers"] == {"User-Agent": "daily-brief-test/1.0"}


def test_fetch_feeds_names_an_untitled_feed_after_its_domain(cfg):
    content = b"<rss version='2.0'><channel><item><title>T</title></item></channel></rss>"
    with patch.object(news.requests, "get", return_value=feed_response(content)):
        (found,) = fetch(cfg, ["https://www.news.example/path/feed.xml"])
    assert found["source"] == "news.example"


def test_select_does_not_know_about_global_keywords(cfg):
    items = [item("Plain"), item("Java news"), item("Lakers win")]
    kept = pick(items, cfg, keywords=["Lakers"])
    assert titles(kept) == ["Lakers win", "Plain", "Java news"]


def test_get_headlines_group_can_opt_out_of_global_keywords(cfg):
    cfg["news"]["groups"] = [
        {"name": "Sport", "limit": 2, "use_global_keywords": False, "keywords": ["Lakers"],
         "feeds": ["https://a.example/rss"]},
    ]
    with patch.object(news.requests, "get", return_value=feed_response(rss("S", ["Java news", "Lakers win"]))):
        groups = news.get_headlines(cfg)
    assert titles(groups[0]["items"]) == ["Lakers win", "Java news"]


def test_select_accepts_a_per_call_source_cap(cfg):
    items = [item(f"Java {n}", source="A") for n in range(3)] + [item("Other", source="B")]
    assert titles(pick(items, cfg, max_per_source=1)) == ["Java 0", "Other"]


def test_get_headlines_group_can_set_its_own_source_cap(cfg):
    cfg["news"]["groups"] = [
        {"name": "Cars", "limit": 3, "max_per_source": 1, "feeds": ["https://a.example/rss", "https://b.example/rss"]},
    ]
    responses = [
        feed_response(rss("Electrek", ["Java 1", "Java 2", "Java 3"])),
        feed_response(rss("Autocar", ["Plain"])),
    ]
    with patch.object(news.requests, "get", side_effect=responses):
        groups = news.get_headlines(cfg)
    assert titles(groups[0]["items"]) == ["Java 1", "Plain"]


# ---- Hupu (an HTML board, not RSS) -------------------------------------

HUPU_PAGE = """
<html><body><ul>
<li class="bbs-sl-web-post-body"><div class="bbs-sl-web-post-layout"><div class="post-title">
<a href="/642924102.html" target="_blank" class="p-title" style="color:;">拆解开拓者给杨瀚森设定的3个针对性战术布置</a></div>
<div class="post-datum">35 / 25829</div><div class="post-time">10-14 01:40</div></div></li>
<li class="bbs-sl-web-post-body"><div class="bbs-sl-web-post-layout"><div class="post-title">
<a href="/642917987.html" class="p-title">原创 | 热火 &amp; <b>勇士</b> 的季前赛</a></div></div></li>
<li class="bbs-sl-web-post-body"><div class="post-title"><a href="/642900000.html" class="p-title">  </a></div></li>
<li class="bbs-sl-web-post-body"><div class="post-title"><a href="https://my.hupu.com/x.html" class="p-title">站外链接</a></div></li>
</ul></body></html>
""".encode("utf-8")

HUPU_FEED = {"url": "https://bbs.hupu.com/nba", "parser": "hupu", "name": "虎扑 NBA"}


def hupu_items(cfg, content=HUPU_PAGE, feed=HUPU_FEED):
    with patch.object(news.requests, "get", return_value=feed_response(content)) as get:
        return news.fetch_feeds([feed], cfg["news"]), get


def test_hupu_board_threads_become_items(cfg):
    items, get = hupu_items(cfg)

    assert [(i["source"], i["title"], i["link"]) for i in items] == [
        ("虎扑 NBA", "拆解开拓者给杨瀚森设定的3个针对性战术布置", "https://bbs.hupu.com/642924102.html"),
        ("虎扑 NBA", "原创 | 热火 & 勇士 的季前赛", "https://bbs.hupu.com/642917987.html"),
        ("虎扑 NBA", "站外链接", "https://my.hupu.com/x.html"),
    ]
    assert get.call_args.args[0] == "https://bbs.hupu.com/nba"
    assert get.call_args.kwargs["headers"] == {"User-Agent": cfg["news"]["user_agent"]}


def test_hupu_source_falls_back_to_the_domain(cfg):
    items, _ = hupu_items(cfg, feed={"url": "https://bbs.hupu.com/nba", "parser": "hupu"})
    assert items[0]["source"] == "bbs.hupu.com"


def test_hupu_respects_the_per_feed_limit(cfg):
    cfg["news"]["max_items_per_feed"] = 1
    items, _ = hupu_items(cfg)
    assert len(items) == 1


@pytest.mark.parametrize("content", [b"", b"<html>no threads here</html>", b"\xff\xfe not utf-8"])
def test_hupu_page_without_threads_gives_no_items(cfg, content):
    items, _ = hupu_items(cfg, content=content)
    assert items == []


def test_hupu_http_error_is_skipped_like_any_feed(cfg):
    with patch.object(news.requests, "get", return_value=feed_response(b"", status=503)):
        assert news.fetch_feeds([HUPU_FEED], cfg["news"]) == []


def test_unknown_parser_is_skipped_and_logged(cfg, caplog):
    items, _ = hupu_items(cfg, feed={"url": "https://x.example", "parser": "nope"})
    assert items == [] and "nope" in caplog.text


def test_plain_urls_and_html_feeds_can_be_mixed(cfg):
    responses = [feed_response(rss("Feed A", ["A1"])), feed_response(HUPU_PAGE)]
    with patch.object(news.requests, "get", side_effect=responses):
        items = news.fetch_feeds(["https://a.example/rss", HUPU_FEED], cfg["news"])
    assert [i["title"] for i in items][:2] == ["A1", "拆解开拓者给杨瀚森设定的3个针对性战术布置"]


# ---- Complete Car (Irish car site, no feed) -----------------------------

COMPLETECAR_PAGE = """
<main>
<article class="landing-nib-item" data-content-identity="nib&#x3A;216">
  <h3 class="landing-nib-item__headline"><a href="&#x2F;news-in-brief&#x2F;216&#x2F;cupra-raval-kit" data-x="1">Dave can&#039;t decide what to call the <b>Cupra</b> Raval kit</a></h3>
  <time datetime="2026-10-13T14&#x3A;53&#x3A;00&#x2B;01&#x3A;00" title="13 October 2026">7 hours ago</time>
  <p class="landing-nib-item__copy">A quick look at the new kit.</p>
</article>
<article class="old"><h3><a href="/news/1/old-story">Old story</a></h3>
  <time datetime="2026-10-01T09:00:00+01:00">12 days ago</time></article>
<article class="undated"><h2><a href="/reviews/2/undated-review">Undated review</a></h2></article>
<article class="no-headline"><p>Just text</p></article>
</main>
""".encode("utf-8")

COMPLETECAR = {"url": "https://www.completecar.ie/", "parser": "completecar", "name": "Complete Car"}
NOW = datetime(2026, 10, 13, 20, 0, tzinfo=timezone.utc)


def read_page(cfg, content, feed):
    with (
        patch.object(news.requests, "get", return_value=feed_response(content)),
        patch.object(news.time, "time", return_value=NOW.timestamp()),
    ):
        return news.fetch_feeds([feed], cfg["news"])


def test_completecar_articles_become_items_with_dates(cfg):
    items = read_page(cfg, COMPLETECAR_PAGE, COMPLETECAR)

    assert [(i["source"], i["title"], i["link"]) for i in items] == [
        ("Complete Car", "Dave can't decide what to call the Cupra Raval kit", "https://www.completecar.ie/news-in-brief/216/cupra-raval-kit"),
        ("Complete Car", "Undated review", "https://www.completecar.ie/reviews/2/undated-review"),
    ]
    assert items[0]["summary"] == "A quick look at the new kit."
    assert calendar.timegm(items[0]["published"]) == datetime(2026, 10, 13, 13, 53, tzinfo=timezone.utc).timestamp()


def test_completecar_page_without_articles_gives_nothing(cfg):
    assert read_page(cfg, b"<html>maintenance</html>", COMPLETECAR) == []


# ---- 懂车帝 (Next.js page with the data embedded) -------------------------

def dongchedi_page(nodes):
    data = {"props": {"pageProps": {"feed": {"list": nodes}, "other": [{"title": "no id here" * 2}]}}}
    return (
        '<html><script id="__NEXT_DATA__" type="application/json">'
        + json.dumps(data, ensure_ascii=False)
        + "</script></html>"
    ).encode("utf-8")


DONGCHEDI = {"url": "https://www.dongchedi.com/", "parser": "dongchedi", "name": "懂车帝"}


def test_dongchedi_headlines_come_from_the_embedded_data(cfg):
    page = dongchedi_page([
        {"group_id": "7694875470768849470", "title": "红旗天工07正式上市", "article_type": "1"},
        {"group_id": "7694875470768849470", "title": "红旗天工07正式上市", "article_type": "1"},
        {"group_id": "111", "title": "视频内容需要登录观看才行", "article_type": "2"},
        {"group_id": 7694288581578244632, "title": "2027款深蓝L06正式上市"},
    ])
    items = read_page(cfg, page, DONGCHEDI)

    assert [(i["source"], i["title"], i["link"]) for i in items] == [
        ("懂车帝", "红旗天工07正式上市", "https://www.dongchedi.com/article/7694875470768849470"),
        ("懂车帝", "2027款深蓝L06正式上市", "https://www.dongchedi.com/article/7694288581578244632"),
    ]
    assert all(i["published"] is None for i in items)


@pytest.mark.parametrize(
    "content",
    [b"", b"<html>no data</html>", b'<script id="__NEXT_DATA__">{broken</script>'],
)
def test_dongchedi_page_without_usable_data_gives_nothing(cfg, content):
    assert read_page(cfg, content, DONGCHEDI) == []


# ---- 汽车之家 (GB18030 page with relative times) ---------------------------

def autohome_li(art_id, title, when, text="摘要"):
    return (
        f'<li data-artidanchor="{art_id}"><a href="//www.autohome.com.cn/news/202610/{art_id}.html#pvareaid=1">'
        f'<h3>{title}</h3><div class="article-bar"><span class="fn-left">{when}</span></div><p>{text}</p></a></li>'
    )


AUTOHOME = {"url": "https://www.autohome.com.cn/news/", "parser": "autohome", "name": "汽车之家"}


def test_autohome_news_items_get_dates_from_relative_times(cfg):
    page = "<ul>" + autohome_li(1, "比亚迪大汉EV上市", "8小时前") + autohome_li(2, "旧闻", "10-01") + autohome_li(3, "刚发布", "刚刚") + autohome_li(4, "昨天的消息", "昨天") + autohome_li(5, "未知时间", "") + "</ul>"
    items = read_page(cfg, page.encode("gb18030"), AUTOHOME)

    assert [i["title"] for i in items] == ["比亚迪大汉EV上市", "刚发布", "昨天的消息", "未知时间"]
    assert items[0]["link"] == "https://www.autohome.com.cn/news/202610/1.html"
    assert items[0]["source"] == "汽车之家" and items[0]["summary"] == "摘要"
    assert calendar.timegm(items[0]["published"]) == NOW.timestamp() - 8 * 3600


def test_autohome_minutes_ago(cfg):
    items = read_page(cfg, ("<ul>" + autohome_li(1, "t", "30分钟前") + "</ul>").encode("gb18030"), AUTOHOME)
    assert calendar.timegm(items[0]["published"]) == NOW.timestamp() - 30 * 60


# ---- Carwow (UK car site, no feed, no dates) ------------------------------

def carwow_card(href, title):
    return (
        "<article class='card-minimalist'>"
        f"<a aria-hidden='true' href='{href}' tabindex='-1'><img alt='' src='x.jpg'></a>"
        "<div class='card-minimalist__body'>"
        f"<a class='card-minimalist__title' data-x='1' href='{href}'>{title}</a></div></article>"
    )


CARWOW = {"url": "https://www.carwow.co.uk/news", "parser": "carwow", "name": "Carwow"}


def test_carwow_cards_become_items_newest_post_first(cfg):
    page = (
        carwow_card("https://www.carwow.co.uk/news/9217/electric-car-grant", "Electric car grant explained")
        + carwow_card("https://www.carwow.co.uk/byd/atto-3-evo/news/11399/living-with-byd", "Living with the BYD &amp; its Atto 3")
        + carwow_card("/toyota/rav4/news/11398/used-car-of-the-day", "Used car of the day: RAV4")
        + "<article class='other'><p>no title link</p></article>"
    )
    items = read_page(cfg, page.encode("utf-8"), CARWOW)

    assert [(i["source"], i["title"]) for i in items] == [
        ("Carwow", "Living with the BYD & its Atto 3"),
        ("Carwow", "Used car of the day: RAV4"),
        ("Carwow", "Electric car grant explained"),
    ]
    assert items[1]["link"] == "https://www.carwow.co.uk/toyota/rav4/news/11398/used-car-of-the-day"
    assert all(i["published"] is None for i in items)


def test_carwow_page_without_cards_gives_nothing(cfg):
    assert read_page(cfg, b"<html>blocked</html>", CARWOW) == []


def test_a_page_that_parses_to_nothing_is_logged_so_a_redesign_is_noticed(cfg, caplog):
    read_page(cfg, b"<html>redesigned</html>", COMPLETECAR)
    assert "https://www.completecar.ie/ returned no items" in caplog.text


def test_a_feed_with_items_does_not_warn(cfg, caplog):
    read_page(cfg, COMPLETECAR_PAGE, COMPLETECAR)
    assert "returned no items" not in caplog.text


# ---- Sina entertainment, mobile site (ent.sina.cn) -------------------------

def sina_card(href, title):
    return (
        f'<a class="f_card m_f_a_no" href="{href}" data-docid="x">'
        f'<h2 class="cm_tit">{title}</h2><blockquote class="cm_f_ic"></blockquote></a>'
    )


SINA = {"url": "https://ent.sina.cn/", "parser": "sina_ent", "name": "新浪娱乐"}


def test_sina_ent_cards_become_items_dated_from_the_url(cfg):
    page = (
        sina_card("https://ent.sina.cn/2026-10-13/detail-inivcpix8675253.d.html?vt=4&amp;from=x#top", "《玉簟秋》7-8集什么时候播出？")
        + sina_card("https://ent.sina.cn/2026-10-11/detail-old.d.html?vt=4", "三天前的消息")
        + sina_card("https://k.sina.cn/article_7879924004_m1d5.html?vt=4&amp;from=ent", "沈腾栽易烊千玺身上了 &amp; 何炅")
        + '<a class="other" href="/x"><h2 class="cm_tit">not a card</h2></a>'
        + '<a class="f_card" href="/y"><p>no title</p></a>'
    )
    items = read_page(cfg, page.encode("utf-8"), SINA)

    assert [(i["source"], i["title"]) for i in items] == [
        ("新浪娱乐", "《玉簟秋》7-8集什么时候播出？"),
        ("新浪娱乐", "沈腾栽易烊千玺身上了 & 何炅"),
    ]
    assert items[0]["link"] == "https://ent.sina.cn/2026-10-13/detail-inivcpix8675253.d.html?vt=4&from=x"
    # Dated items count as published at midday in China, undated clips are kept.
    assert calendar.timegm(items[0]["published"]) == datetime(2026, 10, 13, 4, 0, tzinfo=timezone.utc).timestamp()
    assert items[1]["published"] is None


def test_sina_ent_page_without_cards_gives_nothing(cfg):
    assert read_page(cfg, b"<html>blocked</html>", SINA) == []


def test_an_rss_feed_can_be_given_a_readable_name(cfg):
    feed = {"url": "https://a.example/rss", "name": "TechCrunch AI"}
    with patch.object(news.requests, "get", return_value=feed_response(rss("AI News | TechCrunch", ["A1"]))):
        (found,) = news.fetch_feeds([feed], cfg["news"])
    assert found["source"] == "TechCrunch AI"


# ---- downloading in parallel ------------------------------------------


def by_url(pages):
    """A `requests.get` stand-in that answers by URL, so thread order does not matter."""

    def get(url, **kwargs):
        reply = pages[url]
        if isinstance(reply, Exception):
            raise reply
        return feed_response(reply)

    return get


def test_feeds_fetched_in_parallel_still_come_back_in_feed_order(cfg):
    cfg["news"]["max_workers"] = 8
    urls = [f"https://site{n}.example/rss" for n in range(12)]
    pages = {url: rss(f"Site {n}", [f"Story {n}"]) for n, url in enumerate(urls)}
    with patch.object(news.requests, "get", side_effect=by_url(pages)):
        items = news.fetch_feeds(urls, cfg["news"])
    assert [i["title"] for i in items] == [f"Story {n}" for n in range(12)]


def test_a_failing_feed_in_a_parallel_batch_only_loses_itself(cfg, caplog):
    cfg["news"]["max_workers"] = 4
    pages = {
        "https://a.example/rss": rss("A", ["A1"]),
        "https://down.example/rss": requests.ConnectionError("down"),
        "https://c.example/rss": rss("C", ["C1"]),
    }
    with patch.object(news.requests, "get", side_effect=by_url(pages)):
        items = news.fetch_feeds(list(pages), cfg["news"])
    assert [i["title"] for i in items] == ["A1", "C1"]
    assert "https://down.example/rss failed: ConnectionError" in caplog.text


def test_get_headlines_downloads_every_group_in_one_batch(cfg):
    cfg["news"]["max_workers"] = 4
    cfg["news"]["groups"] = [
        {"name": "One", "limit": 2, "feeds": ["https://a.example/rss", "https://b.example/rss"]},
        {"name": "Two", "limit": 2, "feeds": ["https://c.example/rss"]},
    ]
    pages = {
        "https://a.example/rss": rss("A", ["A1"]),
        "https://b.example/rss": rss("B", ["B1"]),
        "https://c.example/rss": rss("C", ["C1"]),
    }
    with (
        patch.object(news.requests, "get", side_effect=by_url(pages)) as get,
        patch.object(news, "ThreadPoolExecutor", wraps=news.ThreadPoolExecutor) as pool,
    ):
        groups = news.get_headlines(cfg)

    assert [(g["name"], titles(g["items"])) for g in groups] == [("One", ["A1", "B1"]), ("Two", ["C1"])]
    assert get.call_count == 3
    pool.assert_called_once_with(max_workers=4)


def test_an_unknown_parser_is_not_even_requested(cfg):
    with patch.object(news.requests, "get") as get:
        assert news.fetch_feeds([{"url": "https://x.example", "parser": "nope"}], cfg["news"]) == []
    get.assert_not_called()
