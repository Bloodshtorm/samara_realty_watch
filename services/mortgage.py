from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MortgageScenario:
    name: str
    annual_rate_percent: float
    max_loan_rub: int | None
    down_payment_percent: float
    term_years: int


@dataclass(frozen=True)
class MortgageResult:
    scenario: str
    down_payment_rub: int
    loan_rub: int
    monthly_payment_rub: int
    total_payments_rub: int
    overpayment_rub: int
    above_limit_loan_rub: int
    note: str = (
        "Ориентировочный расчет: реальные условия, ПСК, страховка "
        "и право на программу проверяются отдельно."
    )


def annuity_payment(loan_rub: int, annual_rate_percent: float, term_years: int) -> int:
    months = term_years * 12
    if months <= 0:
        raise ValueError("term_years must be positive")
    if loan_rub <= 0:
        return 0
    monthly_rate = annual_rate_percent / 100 / 12
    if monthly_rate == 0:
        return round(loan_rub / months)
    payment = (
        loan_rub * monthly_rate * (1 + monthly_rate) ** months / ((1 + monthly_rate) ** months - 1)
    )
    return round(payment)


def calculate_mortgage(price_rub: int, scenario: MortgageScenario) -> MortgageResult:
    down_payment = round(price_rub * scenario.down_payment_percent / 100)
    loan = max(price_rub - down_payment, 0)
    above_limit = max(loan - scenario.max_loan_rub, 0) if scenario.max_loan_rub else 0
    monthly = annuity_payment(loan, scenario.annual_rate_percent, scenario.term_years)
    total = monthly * scenario.term_years * 12 + down_payment
    return MortgageResult(
        scenario=scenario.name,
        down_payment_rub=down_payment,
        loan_rub=loan,
        monthly_payment_rub=monthly,
        total_payments_rub=total,
        overpayment_rub=max(total - price_rub, 0),
        above_limit_loan_rub=above_limit,
    )


def calculate_combined_mortgage(
    price_rub: int,
    preferred: MortgageScenario,
    market: MortgageScenario,
) -> dict[str, int | str]:
    down_payment = round(price_rub * preferred.down_payment_percent / 100)
    loan = max(price_rub - down_payment, 0)
    preferred_loan = min(loan, preferred.max_loan_rub or loan)
    market_loan = max(loan - preferred_loan, 0)
    preferred_payment = annuity_payment(
        preferred_loan, preferred.annual_rate_percent, preferred.term_years
    )
    market_payment = annuity_payment(market_loan, market.annual_rate_percent, market.term_years)
    return {
        "scenario": f"{preferred.name} + {market.name}",
        "down_payment_rub": down_payment,
        "preferred_loan_rub": preferred_loan,
        "market_loan_rub": market_loan,
        "monthly_payment_rub": preferred_payment + market_payment,
        "note": "Ориентировочный расчет, условия проверяются отдельно.",
    }
