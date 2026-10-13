import news


def shape(value):
    """Keys of nested dicts, ignoring values and list contents."""
    if isinstance(value, dict):
        return {key: shape(inner) for key, inner in value.items()}
    return None


def test_test_fixture_has_the_same_keys_as_the_real_config(config, real_config):
    assert shape(config) == shape(real_config)


def test_real_config_has_one_weekday_and_month_name_each(real_config):
    assert len(real_config["brief"]["weekdays"]) == 7
    assert len(real_config["brief"]["months"]) == 12


def test_every_news_group_is_usable(real_config):
    names = set()
    for group in real_config["news"]["groups"]:
        assert group["name"] not in names, "duplicate group name"
        names.add(group["name"])
        assert group["limit"] > 0
        urls = [f if isinstance(f, str) else f["url"] for f in group["feeds"]]
        assert urls and all(url.startswith("http") for url in urls)
        for feed in group["feeds"]:
            # Every feed is named, so the email shows "TechCrunch AI", not the feed's own title.
            assert not isinstance(feed, str) and feed["name"].strip(), feed
            assert feed.get("parser") in news.PARSERS, feed
        # A group that ignores the global keywords and filters needs its own.
        if group.get("min_score", 0) > 0 and not group.get("use_global_keywords", True):
            assert group.get("keywords"), group["name"]


def test_car_sites_without_feeds_are_configured(real_config):
    feeds = [f for g in real_config["news"]["groups"] for f in g["feeds"]]
    assert {f["parser"] for f in feeds if f.get("parser")} == {"completecar", "dongchedi", "autohome", "carwow", "sina_ent", "hupu"}


def test_there_is_no_sports_section(real_config):
    assert not [g for g in real_config["news"]["groups"] if "体育" in g["name"]]


def test_irish_car_sites_come_first_in_the_car_section(real_config):
    group = next(g for g in real_config["news"]["groups"] if g["name"].endswith("汽车") and "中文" not in g["name"])
    names = [f["name"] for f in group["feeds"]]
    assert names[:2] == ["Complete Car", "Carwow"]
    assert [isinstance(f, str) for f in group["feeds"][:2]] == [False, False]
    assert group["limit"] >= 3 and group["max_per_source"] == 1


def test_entertainment_and_chinese_sections_come_first(real_config):
    names = [g["name"].split(" ", 1)[1] for g in real_config["news"]["groups"]]
    assert names == ["内娱", "中文新闻", "中文科技", "中文汽车", "虎扑", "爱尔兰", "IT 与 AI", "技术栈", "汽车"]


def test_single_site_sections_can_fill_their_slots(real_config):
    for group in real_config["news"]["groups"]:
        sources = len(group["feeds"])
        cap = group.get("max_per_source", real_config["news"]["max_per_source"])
        assert sources * cap >= group["limit"], f"{group['name']} can never fill its {group['limit']} slots"


def test_people_only_use_windows_that_exist(real_config):
    windows = real_config["clothing"]["windows"]
    assert "day" in windows
    for start, end in windows.values():
        assert 0 <= start < end <= 24
    for person in real_config["people"]:
        assert person["windows"], person["name"]
        assert set(person["windows"]) <= set(windows), person["name"]


def test_clothing_tiers_run_warmest_first_and_end_with_a_catch_all(real_config):
    tiers = real_config["clothing"]["tiers"]
    minimums = [tier.get("min") for tier in tiers]
    assert minimums[-1] is None and None not in minimums[:-1]
    assert minimums[:-1] == sorted(minimums[:-1], reverse=True)
    assert all(tier["top"] for tier in tiers)


def test_rain_wind_and_uv_rules_are_consistent(real_config):
    clothing = real_config["clothing"]
    rain = clothing["rain"]
    assert rain["light"]["total_mm"] < rain["steady"]["total_mm"]
    assert rain["light"]["hour_mm"] < rain["steady"]["hour_mm"]
    assert rain["umbrella_max_gust"] < clothing["wind"]["gust_threshold"]
    assert "{outer}" in rain["hooded_outer"] and "{outer}" in rain["waterproof_outer"]
    steps = [step["min"] for step in clothing["wind_chill"]]
    assert steps == sorted(steps)
    assert all({"min", "advice", "child_advice"} <= set(rule) for rule in clothing["uv"])
    assert set(clothing["child"]["spare"]) == {"light", "steady"}


def test_every_alert_the_code_can_raise_has_wording(real_config):
    assert set(real_config["brief"]["alerts"]) == {"rain_light", "rain_steady", "wind", "uv"}
    assert set(real_config["brief"]["icons"]) == set(real_config["brief"]["alerts"]) | {"default"}


def test_weather_line_fills_with_the_values_the_code_provides(real_config):
    line = real_config["brief"]["weather_line"].format(
        city="X", temp="1", feels="2", rain="3", rain_mm="4", wind="5", uv="6"
    )
    assert "{" not in line


def test_feed_names_are_unique_within_a_section(real_config):
    for group in real_config["news"]["groups"]:
        names = [feed["name"] for feed in group["feeds"]]
        assert len(names) == len(set(names)), group["name"]


def test_feeds_are_fetched_in_parallel_in_production_and_in_order_in_tests(real_config, config):
    assert real_config["news"]["max_workers"] > 1
    assert config["news"]["max_workers"] == 1


def test_hupu_section_reads_two_boards_and_neither_fills_it(real_config):
    group = next(g for g in real_config["news"]["groups"] if g["name"].endswith("虎扑"))
    assert [f["url"] for f in group["feeds"]] == ["https://bbs.hupu.com/nba", "https://bbs.hupu.com/topic-daily-hot"]
    assert {f["parser"] for f in group["feeds"]} == {"hupu"}
    assert group["limit"] == 5 and group["max_per_source"] < group["limit"]
    # Forum threads match no tech keyword: the boards' own hot order ranks them.
    assert group["use_global_keywords"] is False and group.get("min_score", 0) == 0
