from services.mortgage import MortgageScenario, annuity_payment, calculate_combined_mortgage


def test_annuity_payment() -> None:
    assert annuity_payment(5_000_000, 6.0, 30) == 29_978


def test_zero_rate() -> None:
    assert annuity_payment(1_200_000, 0, 10) == 10_000


def test_combined_mortgage() -> None:
    preferred = MortgageScenario("IT ипотека", 6.0, 6_000_000, 20, 30)
    market = MortgageScenario("Рыночная ипотека", 20.0, None, 20, 20)
    result = calculate_combined_mortgage(10_000_000, preferred, market)
    assert result["preferred_loan_rub"] == 6_000_000
    assert result["market_loan_rub"] == 2_000_000
    assert int(result["monthly_payment_rub"]) > 0
