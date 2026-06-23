from app.schemas.risk import BusinessRiskLevel, RiskLevel, RiskScoreInput
from app.tools.risk_score import calculate_risk_score


def _mock_mvp_input(**overrides) -> RiskScoreInput:
    defaults = {
        "has_website": False,
        "domain_resolves": False,
        "has_mx_record": False,
        "https_available": False,
        "registry_found": False,
        "registry_is_mock": True,
        "multiple_sources_confirm": False,
        "source_count": 3,
        "all_sources_mock": True,
    }
    defaults.update(overrides)
    return RiskScoreInput(**defaults)


def test_mock_only_mvp_has_low_confidence_high_verification_risk_unknown_business_risk():
    result = calculate_risk_score(_mock_mvp_input())

    assert result.verification_confidence == RiskLevel.low
    assert result.verification_risk == RiskLevel.high
    assert result.business_risk == BusinessRiskLevel.unknown
    assert result.level == result.verification_risk
    assert result.score >= 61


def test_mock_negative_signals_do_not_increase_business_risk():
    result = calculate_risk_score(
        _mock_mvp_input(
            negative_snippets_count=3,
            suspicious_keywords_found=["scam", "fraud", "lawsuit"],
        )
    )

    assert result.business_risk == BusinessRiskLevel.unknown
    assert result.verification_risk == RiskLevel.high


def test_missing_registry_and_domain_increase_verification_risk_not_business_risk():
    result = calculate_risk_score(
        RiskScoreInput(
            has_website=False,
            domain_resolves=False,
            registry_found=False,
            registry_is_mock=True,
            source_count=0,
            all_sources_mock=True,
        )
    )

    assert result.verification_risk == RiskLevel.high
    assert result.business_risk == BusinessRiskLevel.unknown
    assert any(factor.name == "registry_not_found" for factor in result.factors)
    assert any(factor.name == "domain_does_not_resolve" for factor in result.factors)


def test_verified_negative_indicators_can_set_business_risk():
    result = calculate_risk_score(
        RiskScoreInput(
            has_website=True,
            domain_resolves=True,
            registry_found=True,
            registry_is_mock=False,
            source_count=5,
            all_sources_mock=False,
            negative_snippets_count=3,
            suspicious_keywords_found=["scam", "fraud", "lawsuit"],
        )
    )

    assert result.business_risk == BusinessRiskLevel.high
    assert result.verification_confidence in {RiskLevel.medium, RiskLevel.high}


def test_low_verification_risk_when_positive_verified_signals_exist():
    result = calculate_risk_score(
        RiskScoreInput(
            has_website=True,
            domain_resolves=True,
            has_mx_record=True,
            https_available=True,
            registry_found=True,
            registry_is_mock=False,
            multiple_sources_confirm=True,
            source_count=5,
            all_sources_mock=False,
        )
    )

    assert 0 <= result.score <= 30
    assert result.verification_risk == RiskLevel.low
    assert result.level == RiskLevel.low
    assert result.verification_confidence == RiskLevel.high
    assert result.business_risk == BusinessRiskLevel.unknown
    assert result.factors


def test_medium_verification_risk_for_partial_information():
    result = calculate_risk_score(
        RiskScoreInput(
            has_website=True,
            domain_resolves=True,
            has_mx_record=False,
            https_available=False,
            registry_found=False,
            registry_is_mock=True,
            multiple_sources_confirm=True,
            source_count=2,
            all_sources_mock=False,
        )
    )

    assert 31 <= result.score <= 60
    assert result.verification_risk == RiskLevel.medium
    assert result.level == RiskLevel.medium


def test_high_verification_risk_for_weak_unverified_signals():
    result = calculate_risk_score(
        RiskScoreInput(
            has_website=False,
            domain_resolves=False,
            has_mx_record=False,
            https_available=False,
            registry_found=False,
            registry_is_mock=True,
            multiple_sources_confirm=False,
            source_count=0,
            all_sources_mock=True,
        )
    )

    assert 61 <= result.score <= 100
    assert result.verification_risk == RiskLevel.high
    assert result.business_risk == BusinessRiskLevel.unknown
    assert result.requires_human_review is True


def test_score_is_clamped_to_0_100():
    high_result = calculate_risk_score(
        RiskScoreInput(
            has_website=False,
            domain_resolves=False,
            has_mx_record=False,
            https_available=False,
            registry_found=False,
            registry_is_mock=True,
            multiple_sources_confirm=False,
            source_count=0,
            all_sources_mock=True,
        )
    )

    low_result = calculate_risk_score(
        RiskScoreInput(
            has_website=True,
            domain_resolves=True,
            has_mx_record=True,
            https_available=True,
            registry_found=True,
            registry_is_mock=False,
            multiple_sources_confirm=True,
            source_count=100,
            all_sources_mock=False,
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
        registry_is_mock=True,
        multiple_sources_confirm=True,
        source_count=2,
        all_sources_mock=False,
    )

    result_1 = calculate_risk_score(input_data)
    result_2 = calculate_risk_score(input_data)

    assert result_1.score == result_2.score
    assert result_1.verification_risk == result_2.verification_risk
    assert result_1.business_risk == result_2.business_risk
