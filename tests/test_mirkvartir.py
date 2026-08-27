from pathlib import Path

from collectors.html_extract import parsed_from_mirkvartir_cards


def test_mirkvartir_fixture_cards() -> None:
    html = Path("tests/fixtures/mirkvartir_sample.html").read_text(encoding="utf-8")
    listings = parsed_from_mirkvartir_cards(
        "mirkvartir",
        html,
        "https://www.mirkvartir.ru/Самарская+область/Самара/Трехкомнатные/",
    )

    assert len(listings) == 1
    listing = listings[0]
    assert listing.source_listing_id == "362268238"
    assert listing.url == "https://www.mirkvartir.ru/362268238/"
    assert listing.price_rub == 14_900_000
    assert listing.price_per_m2 == 201_351
    assert listing.rooms == 3
    assert listing.area_total_m2 == 74
    assert listing.area_kitchen_m2 == 15
    assert listing.floor == 3
    assert listing.floors_total == 9
    assert listing.address_normalized == "самара, галактионовская ул., 130"
