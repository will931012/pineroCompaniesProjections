import json

import httpx
import pytest

from app.events.linking import mention, search_query, short_name
from app.events.service import overlap
from app.events.taxonomy import classify_eight_k, classify_headline
from app.filings.exhibits import parse_exhibits, press_release
from app.providers.email import EmailError, ResendSender
from app.providers.gdelt import normalise_title, parse_articles
from tests.fixtures_filings import EIGHT_K_INDEX
from tests.fixtures_news import gdelt_payload


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        (
            "Apple ( NASDAQ : AAPL ) Faces Challenge With $1 , 999 iPhone",
            "Apple (NASDAQ: AAPL) Faces Challenge With $1,999 iPhone",
        ),
        (
            "What a trillion - dollar stock really worth ? - The Globe",
            "What a trillion-dollar stock really worth? - The Globe",
        ),
        (
            "Nvidia ' s CEO sells 1 , 200 , 000 shares ; stock up 3 . 5 %",
            "Nvidia's CEO sells 1,200,000 shares; stock up 3.5%",
        ),
    ],
)
def test_gdelt_titles_are_de_tokenised(raw: str, clean: str) -> None:
    assert normalise_title(raw) == clean


def test_gdelt_rows_are_validated() -> None:
    articles, rejected = parse_articles(gdelt_payload())

    assert rejected == 1 and len(articles) == 4
    assert (
        articles[0].title == "Example Devices (NASDAQ: EXDV) fourth-quarter results beat estimates"
    )
    assert articles[0].seen_at.tzinfo is not None and articles[0].domain == "news.example.com"
    assert parse_articles("nonsense") == ([], 0)


@pytest.mark.parametrize(
    ("legal", "short"),
    [
        ("Apple Inc.", "Apple"),
        ("JPMORGAN CHASE & CO", "Jpmorgan Chase"),
        ("AMAZON COM INC", "Amazon"),
        ("Meta Platforms, Inc.", "Meta Platforms"),
        ("Procter & Gamble Co", "Procter & Gamble"),
    ],
)
def test_company_names_are_shortened_as_headlines_write_them(legal: str, short: str) -> None:
    assert short_name(legal) == short


def test_links_record_how_a_headline_names_the_company() -> None:
    assert (
        mention("Example (NASDAQ: EXDV) rallies", "Example Devices Inc.", ["EXDV"]).method
        == "title_ticker"
    )
    assert mention("$EXDV hits a record", "Example Devices Inc.", ["EXDV"]).method == "title_ticker"
    assert mention("Example Devices's margins widen", "Example Devices Inc.", ["EXDV"]) == mention(
        "Example Devices to cut jobs", "Example Devices Inc.", ["EXDV"]
    )
    low = mention("Tech stocks rally as the Fed holds rates", "Example Devices Inc.", ["EXDV"])
    assert (low.method, low.confidence) == ("query_only", "low")
    # One- and two-letter tickers are too ambiguous to count on their own.
    assert mention("F is for Friday", "Ford Motor Co", ["F"]).confidence == "low"
    assert search_query("Example Devices Inc.", "EXDV") == '("Example Devices" OR "EXDV stock")'
    assert search_query("Ford Motor Co", "F") == '"Ford Motor"'


@pytest.mark.parametrize(
    ("title", "event_type"),
    [
        ("Example Devices fourth-quarter results beat estimates", "earnings"),
        ("Example Devices to acquire Widget Co for $2 billion", "m_and_a"),
        ("Example Devices names new chief financial officer", "leadership"),
        ("Buffett has stepped down as chairman", "leadership"),
        ("Buffett sells stocks in his final quarter as CEO", "other"),
        ("Analyst upgrades Example Devices to overweight", "analyst"),
        ("Windows 12 is the next major platform upgrade", "other"),
        ("Morgan Stanley downgrades Example Devices to equal-weight", "analyst"),
        ("Example Devices files for Chapter 11 after lawsuit", "bankruptcy"),
        ("Example Devices raises dividend 10%", "capital_return"),
        ("Tech stocks rally as the Fed holds rates", "other"),
    ],
)
def test_headline_rules_are_ordered_and_explainable(title: str, event_type: str) -> None:
    label = classify_headline(title)
    assert label.event_type == event_type
    assert (label.matched is None) == (event_type == "other")


def test_eight_k_items_pick_the_most_consequential_type() -> None:
    assert classify_eight_k(["2.02", "9.01"]).event_type == "earnings"
    assert classify_eight_k(["5.02", "2.02"]).event_type == "earnings"
    assert classify_eight_k(["8.01", "1.03"]).event_type == "bankruptcy"
    assert classify_eight_k(["9.01"]).event_type == "disclosure"
    assert classify_eight_k([]).event_type == "disclosure"


def test_word_overlap_groups_rewrites_of_one_story() -> None:
    first = "Example Devices to acquire Widget Co for $2,000 million"
    assert overlap(first, "Example Devices to buy Widget Co in $2,000 million deal") >= 0.5
    assert overlap(first, "Example Devices fourth-quarter results beat estimates") < 0.5


def test_press_release_exhibit_is_found_on_the_index_page() -> None:
    exhibits = parse_exhibits(EIGHT_K_INDEX)

    assert [(e.type, e.document) for e in exhibits] == [("EX-99.1", "exdv-ex991.htm")]
    assert press_release(exhibits) == exhibits[0]
    assert parse_exhibits(b"<html><body>No tables</body></html>") == []


def test_resend_sender_posts_one_message_and_reports_errors() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = json.loads(request.content)
        if body["to"] == ["bad@example.com"]:
            return httpx.Response(422, json={"message": "Invalid `to` field."})
        return httpx.Response(200, json={"id": "msg_123"})

    sender = ResendSender(
        "re_test_key",
        "Pinero <alerts@example.com>",
        httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert sender.send("me@example.com", "Subject", "text", "<p>html</p>") == "msg_123"
    assert requests[0].headers["Authorization"] == "Bearer re_test_key"
    assert json.loads(requests[0].content)["from"] == "Pinero <alerts@example.com>"
    with pytest.raises(EmailError, match="Invalid"):
        sender.send("bad@example.com", "Subject", "text", "<p>html</p>")
