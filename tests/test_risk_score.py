from app.schemas.risk import RiskLevel, RiskScoreInput
from app.tools.risk_score import calculate_risk_score


def test_low_risk_when_positive_signals_exist():
    result = calculate_risk_score(
        RiskScoreInput(
            has_website=True,
            domain_resolves=True,
            has_mx_record=True,
            https_available=True,
            registry_found=True,
            multiple_sources_confirm=True,
            source_count=5,
        )
    )

    assert 0 <= result.score <= 30
    assert result.level == RiskLevel.low
    assert result.factors


def test_medium_risk_for_partial_information():
    result = calculate_risk_score(
        RiskScoreInput(
            has_website=True,
            domain_resolves=True,
            has_mx_record=False,
            https_available=False,
            registry_found=False,
            multiple_sources_confirm=True,
            source_count=2,
        )
    )

    assert 31 <= result.score <= 60
    assert result.level == RiskLevel.medium


def test_high_risk_for_weak_or_suspicious_signals():
    result = calculate_risk_score(
        RiskScoreInput(
            has_website=False,
            domain_resolves=False,
            has_mx_record=False,
            https_available=False,
            registry_found=False,
            multiple_sources_confirm=False,
            negative_snippets_count=3,
            suspicious_keywords_found=["scam", "fraud", "lawsuit"],
            source_count=0,
        )
    )

    assert 61 <= result.score <= 100
    assert result.level == RiskLevel.high
    assert result.requires_human_review is True


def test_score_is_clamped_to_0_100():
    high_result = calculate_risk_score(
        RiskScoreInput(
            has_website=False,
            domain_resolves=False,
            has_mx_record=False,
            https_available=False,
            registry_found=False,
            multiple_sources_confirm=False,
            negative_snippets_count=100,
            suspicious_keywords_found=["x"] * 100,
            source_count=0,
        )
    )

    low_result = calculate_risk_score(
        RiskScoreInput(
            has_website=True,
            domain_resolves=True,
            has_mx_record=True,
            https_available=True,
            registry_found=True,
            multiple_sources_confirm=True,
            source_count=100,
        )
    )

    assert 0 <= high_result.score <= 100
    assert 0 <= low_result.score <= 100


def test_risk_score_is_deterministic():
    input_data = RiskScoreInput(
        has_website=True,
        domain_resolves=True,
        has_mx_record=False,
        https_available=False,
        registry_found=False,
        multiple_sources_confirm=True,
        source_count=2,
    )

    result_1 = calculate_risk_score(input_data)
    result_2 = calculate_risk_score(input_data)

    assert result_1.score == result_2.score
    assert result_1.level == result_2.level