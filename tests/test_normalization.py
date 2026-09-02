from services.normalization import (
    calc_price_per_m2,
    detect_district,
    is_fractional_share_listing,
    is_low_rise_building,
    is_outside_samara_city_listing,
    is_wrong_room_count,
    normalize_address,
    parse_area_m2,
    parse_floor,
    parse_price_rub,
    parse_rooms,
)


def test_parse_russian_price_formats() -> None:
    assert parse_price_rub("8 900 000 ₽") == 8_900_000
    assert parse_price_rub("12\u00a0340\u00a0000 руб.") == 12_340_000


def test_parse_area_formats() -> None:
    assert parse_area_m2("73,2 м²") == 73.2
    assert parse_area_m2("81.5 кв. м") == 81.5


def test_parse_floor() -> None:
    assert parse_floor("7/16 этаж") == (7, 16)


def test_parse_rooms_detects_studio() -> None:
    assert parse_rooms("Студия · 17,6 м²") == 0


def test_calc_price_per_m2() -> None:
    assert calc_price_per_m2(8_900_000, 73.2) == 121_585


def test_normalize_address() -> None:
    assert normalize_address("г. Самара, ул. Ново-Садовая, д. 100") == (
        "самара, улица ново-садовая, дом 100"
    )


def test_detect_fractional_share_listings() -> None:
    assert is_fractional_share_listing("Доля в 3-к. квартире, 12 м²")
    assert is_fractional_share_listing("Продам 1/2 квартиры")
    assert is_fractional_share_listing("Продается доля, 540 000 ₽")
    assert not is_fractional_share_listing("3-к. квартира, 72 м²")
    assert not is_fractional_share_listing("Полноценная квартира, не доля")


def test_detect_outside_samara_city_listings() -> None:
    assert is_outside_samara_city_listing("Новокуйбышевск, улица Суворова")
    assert is_outside_samara_city_listing("поселок городского типа Петра Дубрава")
    assert is_outside_samara_city_listing("Самара, поселок Мехзавод")
    assert not is_outside_samara_city_listing("Самара, Ново-Садовая улица, 10")
    assert not is_outside_samara_city_listing("улица Советской Армии, 242")


def test_detect_district_prefers_explicit_address_district() -> None:
    assert (
        detect_district(
            "Самара, СНТ Ракитовские Дачи-1, 20-я ул., 10 р-н Красноглинский",
            "рядом железнодорожная станция",
        )
        == "красноглинский"
    )


def test_detect_low_rise_buildings() -> None:
    assert is_low_rise_building(1)
    assert is_low_rise_building(5)
    assert not is_low_rise_building(6)
    assert not is_low_rise_building(None)


def test_detect_wrong_room_count() -> None:
    assert is_wrong_room_count(0, 3, "Студия · 17,6 м²")
    assert is_wrong_room_count(2, 3, "2-комн. квартира")
    assert is_wrong_room_count(None, 3, "Студия · 17,6 м²")
    assert not is_wrong_room_count(3, 3, "3-комн. квартира")
