from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.schemas.company_check import CompanyCheckResult


def valid_company_check_data():
    return {
        "check_id": 1,
        "company": {
            "name": "Servochron",
            "country": "USA",
            "domain": None,
        },
        "summary": {
            "short_description": "Preliminary check.",
            "overall_assessment": "No confirmed registry source was found.",
            "confidence": "low",
        },
        "sources": [],
        "domain_dns": {
            "status": "not_provided",
            "domain": None,
            "has_a_record": False,
            "has_mx_record": False,
            "has_txt_record": False,
            "https_available": False,
            "warnings": [],
        },
        "risk": {
            "preliminary_score": 45,
            "preliminary_level": "medium",
            "factors": [],
            "requires_human_review": True,
            "final_score": None,
            "final_level": None,
            "human_review_status": "pending",
        },
        "manual_verification_checklist": [
            "Check official company registry.",
        ],
        "unknowns": [
            "No official registry result was confirmed.",
        ],
        "created_at": "2026-01-01T00:00:00",
    }


def test_valid_company_check_json_passes_validation():
    model = CompanyCheckResult.model_validate(valid_company_check_data())

    assert model.company.name == "Servochron"
    assert model.risk.preliminary_level.value == "medium"


def test_invalid_risk_level_fails_validation():
    data = valid_company_check_data()
    data["risk"]["preliminary_level"] = "extreme"

    with pytest.raises(ValidationError):
        CompanyCheckResult.model_validate(data)


def test_invalid_human_review_status_fails_validation():
    data = valid_company_check_data()
    data["risk"]["human_review_status"] = "done"

    with pytest.raises(ValidationError):
        CompanyCheckResult.model_validate(data)


def test_missing_company_name_fails_validation():
    data = deepcopy(valid_company_check_data())
    del data["company"]["name"]

    with pytest.raises(ValidationError):
        CompanyCheckResult.model_validate(data)


def test_json_serialization_works():
    model = CompanyCheckResult.model_validate(valid_company_check_data())

    dumped_dict = model.model_dump()
    dumped_json = model.model_dump_json()

    assert isinstance(dumped_dict, dict)
    assert isinstance(dumped_json, str)
    assert dumped_dict["company"]["name"] == "Servochron"