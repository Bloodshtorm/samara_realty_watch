from scripts.infer_missing_coordinates import _inside_samara, address_key


def test_address_key_matches_avito_and_mapped_source_formats() -> None:
    assert address_key("Пугачевский тракт, 31, р-н Куйбышевский") == address_key(
        "Самара, Куйбышевский район, Пугачевский тракт, 31"
    )


def test_address_key_normalizes_street_type_aliases() -> None:
    assert address_key("пр-т Карла Маркса, 410А, р-н Кировский") == address_key(
        "Самара, Кировский район, Карла Маркса проспект, 410а"
    )


def test_address_key_normalizes_ul_aliases() -> None:
    assert address_key("ул. Стара-Загора, 72") == address_key("Самара, улица Стара Загора, 72")


def test_address_key_keeps_street_names_starting_with_city_abbreviation_letter() -> None:
    assert address_key("ул. Георгия Димитрова, 117") == "георгия димитрова|117"


def test_address_key_ignores_district_only_addresses() -> None:
    assert address_key("р-н Кировский") is None


def test_inside_samara_accepts_city_center() -> None:
    assert _inside_samara(53.195873, 50.100193)


def test_inside_samara_rejects_remote_points() -> None:
    assert not _inside_samara(55.755864, 37.617698)
