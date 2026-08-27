from app.reporting_format import format_percent, format_rub


def test_report_money_formatting() -> None:
    assert format_rub(None) == "-"
    assert format_rub(12345678) == "12 345 678 ₽"


def test_report_percent_formatting() -> None:
    assert format_percent(None) == "-"
    assert format_percent(3.456) == "+3.5%"
    assert format_percent(-2.0) == "-2.0%"
