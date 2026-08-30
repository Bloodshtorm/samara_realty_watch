from collectors.avito import _page_url


def test_page_url_adds_or_replaces_page_param() -> None:
    url = "https://www.avito.ru/samara/kvartiry/prodam-ASgBAgICAUSSA8YQ?context=abc"
    assert _page_url(url, 12) == (
        "https://www.avito.ru/samara/kvartiry/prodam-ASgBAgICAUSSA8YQ?context=abc&p=12"
    )
    assert _page_url(f"{url}&p=2", 13) == (
        "https://www.avito.ru/samara/kvartiry/prodam-ASgBAgICAUSSA8YQ?context=abc&p=13"
    )
