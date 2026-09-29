import pytest

from app.companies.classification import sic_division
from app.providers.base import ProviderError
from app.providers.sec_edgar import parse_submission_profile
from tests.fakes import sec_client_with, submissions_payload


def test_directory_parses_rows_and_rejects_malformed_ones() -> None:
    client = sec_client_with(
        directory_rows=[
            [320193, "Apple Inc.", "aapl", "Nasdaq"],
            [789019, "MICROSOFT CORP", "MSFT", "Nasdaq"],
            [1, "No Exchange Co", "NOEX", None],
            ["not-a-cik", "Broken", "BRK", "NYSE"],
            [5, "", "EMPTY", "NYSE"],
        ]
    )

    result = client.fetch_directory()

    assert [e.ticker for e in result.data] == ["AAPL", "MSFT", "NOEX"]
    assert result.data[2].exchange is None
    assert result.rejected_count == 2
    assert result.meta.provider == "sec_edgar"
    assert result.meta.content_sha256 is not None
    assert result.meta.retrieved_at is not None


def test_missing_user_agent_fails_before_any_request() -> None:
    calls: list = []
    client = sec_client_with(directory_rows=[], user_agent=None, calls=calls)

    with pytest.raises(ProviderError) as error:
        client.fetch_directory()

    assert error.value.code == "sec_user_agent_missing"
    assert calls == []


def test_access_denied_is_reported_with_guidance() -> None:
    client = sec_client_with(status=403)

    with pytest.raises(ProviderError) as error:
        client.fetch_directory()

    assert error.value.code == "provider_access_denied"
    assert error.value.meta is not None and error.value.meta.http_status == 403


def test_submission_profile_normalizes_fields() -> None:
    profile = parse_submission_profile(submissions_payload(320193, website="  "))

    assert profile.cik == 320193
    assert profile.sic_code == "3571"
    assert profile.fiscal_year_end == "0926"
    assert profile.website is None
    assert profile.hq_is_foreign is False
    assert profile.former_names[0]["name"] == "Example Computer Inc"


@pytest.mark.parametrize(
    ("code", "division"),
    [
        ("3571", "Manufacturing"),
        ("6022", "Finance, Insurance & Real Estate"),
        ("7372", "Services"),
        ("4911", "Transportation, Communications & Utilities"),
        ("0100", "Agriculture, Forestry & Fishing"),
        ("9995", "Nonclassifiable"),
        ("", None),
        ("abc", None),
        ("9000", None),
    ],
)
def test_sic_division_mapping(code: str, division: str | None) -> None:
    assert sic_division(code) == division
