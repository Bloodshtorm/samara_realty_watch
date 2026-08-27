from services.reporting import format_rub, pct


def test_report_money_formatting() -> None:
    assert format_rub(None) == "-"
    assert format_rub(12345678) == "12 345 678 ₽"


def test_report_percent_formatting() -> None:
    assert pct(None) == "-"
    assert pct(3.456) == "+3.5%"
    assert pct(-2.0) == "-2.0%"
