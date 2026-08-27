from collectors.html_extract import (
    parsed_from_cian_state,
    parsed_from_etagi_state,
    parsed_from_n1_state,
)


def test_cian_state_parser() -> None:
    html = """
    <script>
    window._cianConfig = [{
      "results": {"offers": [{
        "cianId": 330638143,
        "fullUrl": "https:\\/\\/samara.cian.ru\\/sale\\/flat\\/330638143\\/",
        "price": 11308390,
        "totalArea": "74.89",
        "kitchenArea": "8.8",
        "roomsCount": 3,
        "floorNumber": 1,
        "building": {"floorsCount": 24, "buildYear": 2028, "materialType": "monolith"},
        "geo": {"lat": 53.2, "lng": 50.1, "address": [
          {"title": "Самарская область"}, {"title": "Самара"},
          {"title": "р-н Октябрьский"}, {"title": "улица Советской Армии"}
        ]},
        "formattedFullInfo": "3-комн.кв. · 74,9 м² · 1/24 этаж",
        "photosCount": 7
      }]}
    }];
    </script>
    """

    listing = parsed_from_cian_state(
        "cian",
        html,
        "https://samara.cian.ru/kupit-3-komnatnuyu-kvartiru/",
    )[0]

    assert listing.source_listing_id == "330638143"
    assert listing.price_rub == 11_308_390
    assert listing.area_total_m2 == 74.89
    assert listing.floor == 1
    assert listing.floors_total == 24
    assert listing.address_normalized == (
        "самарская область, самара, р-н октябрьский, улица советской армии"
    )


def test_n1_state_parser() -> None:
    html = """
    <script>
    window.realty = {"items": [{
      "id": 119591380,
      "url": "\\/\\/samara-1.n1.ru\\/view\\/119591380\\/",
      "rubric": "flats",
      "is_agency": true,
      "objectType": "offer",
      "location": {"lat": 53.293838, "lon": 50.28809},
      "geo_links": {
        "city": {"title": "Самара"},
        "district": {"title": "Красноглинский район"},
        "street": {"title": "Мехзавод"}
      },
      "params": {
        "rooms_count": 3,
        "price": 9980000,
        "total_area": 7717,
        "living_area": 3400,
        "kitchen_area": 1660,
        "floor": 7,
        "floors_count": 24,
        "description": "Квартира с ремонтом"
      },
      "images": [{}, {}]
    }]};
    </script>
    """

    listing = parsed_from_n1_state(
        "n1",
        html,
        "https://samara-1.n1.ru/kupit/kvartiry/rooms-trehkomnatnye/",
    )[0]

    assert listing.source_listing_id == "119591380"
    assert listing.price_rub == 9_980_000
    assert listing.area_total_m2 == 77.17
    assert listing.area_living_m2 == 34
    assert listing.area_kitchen_m2 == 16.6
    assert listing.floor == 7
    assert listing.floors_total == 24
    assert listing.seller_type == "agent"


def test_etagi_state_parser() -> None:
    html = """
    <script>
    window.__data = {"objects": [{
      "object_id": 13814392,
      "price": "9908000",
      "price_m2": "149894",
      "square": 66.1,
      "floor": 9,
      "floors": 12,
      "rooms": 3,
      "building_year": 1982,
      "la": "53.201186",
      "lo": "50.144884",
      "house_num": "136",
      "type": "flat",
      "media": {"photos": 26},
      "meta": {
        "city": "Самара",
        "district": "Ленинский",
        "street": "Осипенко",
        "walls": "Панельные"
      }
    }]};
    </script>
    """

    listing = parsed_from_etagi_state(
        "etagi",
        html,
        "https://samara.etagi.com/realty/trehkomnatnye-kvartiry/",
    )[0]

    assert listing.source_listing_id == "13814392"
    assert listing.price_rub == 9_908_000
    assert listing.area_total_m2 == 66.1
    assert listing.floor == 9
    assert listing.floors_total == 12
    assert listing.photos_count == 26
    assert listing.address_normalized == "самара, ленинский, осипенко, 136"
