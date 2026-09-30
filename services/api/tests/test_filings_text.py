from datetime import date
from decimal import Decimal

import pytest

from app.analytics.text_diff import diff_paragraphs, summarise, word_diff
from app.filings.chunks import MAX_CHARS, chunk_section
from app.filings.form4 import Form4Error, parse_form4, raw_xml_name
from app.filings.html_text import paragraphs_from_html, paragraphs_from_text
from app.filings.sections import extract_sections, form_family
from app.providers.sec_edgar import parse_filings
from tests.fixtures_filings import (
    EIGHT_K,
    FILINGS,
    RISK_A,
    RISK_B_2023,
    RISK_B_2024,
    RISK_C,
    RISK_D,
    TEN_K_2024,
    TEN_Q,
    XXE_FORM4,
    columns,
    form4_xml,
)


def test_html_paragraphs_drop_hidden_script_and_page_furniture() -> None:
    paragraphs = paragraphs_from_html(TEN_K_2024)
    text = "\n".join(paragraphs)

    assert "EntityCentralIndexKey" not in text  # hidden inline-XBRL header
    assert "tracking" not in text  # script
    assert "margin: 0" not in text  # style
    assert "12" not in paragraphs and "2" not in paragraphs  # page numbers
    assert not any(p.lower() == "table of contents" for p in paragraphs)
    assert "Item 1A. | Risk Factors | 12" in paragraphs  # a contents row stays one line
    assert "Net sales | $ | 1,200" in paragraphs
    # Non-breaking space and typographic apostrophe are normalised or kept, never mangled.
    assert any("increased 8% in fiscal 2024" in p for p in paragraphs)


def test_text_documents_split_on_blank_lines() -> None:
    assert paragraphs_from_text(b"FORM 10-K\r\n\r\nItem 1.\r\nBusiness text\n\n\n7\n") == [
        "FORM 10-K",
        "Item 1. Business text",
    ]


def test_ten_k_sections_skip_the_table_of_contents() -> None:
    sections = extract_sections(paragraphs_from_html(TEN_K_2024), "10-K", "10-K")
    by_key = {s.key: s for s in sections}

    assert [s.key for s in sections] == ["item_1", "item_1a", "item_7", "item_8"]
    assert by_key["item_1a"].title == "Item 1A. Risk Factors"
    assert by_key["item_1a"].text.split("\n\n") == [RISK_A, RISK_B_2024, RISK_D]
    # "Item 1." and "Business" on separate lines: the title line is not body text.
    assert by_key["item_1"].text == "We design and sell connected devices and services."
    # A cross-reference ("See Item 1A") inside MD&A does not start a section.
    assert "See Item 1A for risks" in by_key["item_7"].text
    # The signature block after the last item is excluded.
    assert "SIGNATURES" not in by_key["item_8"].text
    assert by_key["item_8"].text == "The consolidated financial statements are included below."


def test_ten_q_items_are_keyed_by_part() -> None:
    sections = extract_sections(paragraphs_from_html(TEN_Q), "10-Q", "10-Q")

    assert [s.key for s in sections] == [
        "part1_item_1",
        "part1_item_2",
        "part2_item_1",
        "part2_item_1a",
    ]
    assert sections[2].title == "Part II · Item 1. Legal Proceedings"


def test_eight_k_items_use_official_titles() -> None:
    sections = extract_sections(paragraphs_from_html(EIGHT_K), "8-K", "8-K")

    assert [(s.key, s.title) for s in sections] == [
        ("item_2.02", "Item 2.02. Results of Operations and Financial Condition"),
        ("item_9.01", "Item 9.01. Financial Statements and Exhibits"),
    ]
    assert "Jane Doe" not in sections[-1].text


def test_items_follow_official_order_when_the_contents_precede_a_long_preamble() -> None:
    # Microsoft-like: the contents list ends with Item 9C, a long preamble follows, and
    # Item 9C's own section is a single line. The contents entry has the longer span.
    preamble = ["Note about forward-looking statements. " * 40] * 12
    paragraphs = [
        "Item 1. | Business | 3",
        "Item 1A. | Risk Factors | 20",
        "Item 9B. | Other Information | 90",
        "Item 9C. | Disclosure Regarding Foreign Jurisdictions | 91",
        *preamble,
        "Item 1. Business",
        "We develop software. " * 50,
        "Item 1A. Risk Factors",
        "Competition is intense. " * 80,
        "Item 9B. Other Information",
        "None.",
        "Item 9C. Disclosure Regarding Foreign Jurisdictions that Prevent Inspections",
        "Not applicable.",
    ]
    sections = extract_sections(paragraphs, "10-K", "10-K")

    assert [s.key for s in sections] == ["item_1", "item_1a", "item_9b", "item_9c"]
    assert sections[-1].text == "Not applicable."
    assert "forward-looking" not in "".join(s.text for s in sections)


def test_running_page_headers_do_not_split_an_item() -> None:
    # Microsoft-like: "PART II" / "Item 7" repeats at the top of every page of MD&A.
    page = "Revenue increased due to cloud growth. " * 40
    paragraphs = [
        "Item 6. [Reserved]",
        "PART II",
        "Item 7",
        "ITEM 7. MANAGEMENT'S DISCUSSION AND ANALYSIS OF FINANCIAL CONDITION",
        page,
        "PART II",
        "Item 7",
        page + "Second page.",
        "PART II",
        "Item 7A",
        "ITEM 7A. QUANTITATIVE AND QUALITATIVE DISCLOSURES ABOUT MARKET RISK",
        "We are exposed to foreign exchange risk.",
    ]
    sections = {s.key: s for s in extract_sections(paragraphs, "10-K", "10-K")}

    assert sections["item_6"].text == ""
    assert sections["item_7"].text.split("\n\n") == [page, page + "Second page."]
    assert sections["item_7a"].text == "We are exposed to foreign exchange risk."


def test_reports_not_organised_by_items_are_not_mislabelled() -> None:
    # JPMorgan-like 10-Q: MD&A and statements without item headings, then a cross-reference
    # index at the end. Labelling everything "Item 6. Exhibits" would be wrong.
    paragraphs = [
        "Management's discussion and analysis. " * 60,
        *(["Consolidated results of operations. " * 60] * 20),
        "Part I | Item 1. | Financial Statements | 90",
        "Item 2. | Management's Discussion and Analysis | 10",
        "Part II | Item 1A. | Risk Factors | 150",
        "Item 6. | Exhibits | 160",
        *(["Exhibit 31.1 Certification of the Chief Executive Officer. " * 30] * 20),
    ]
    sections = extract_sections(paragraphs, "10-Q", "Quarterly report")

    assert [s.key for s in sections] == ["document"]


def test_documents_without_items_become_one_section() -> None:
    sections = extract_sections(["Proxy statement.", "Annual meeting details."], "DEF 14A", "Proxy")

    assert [(s.key, s.title) for s in sections] == [("document", "Proxy")]
    assert extract_sections([], "10-K", "10-K") == []
    assert form_family("10-K/A") == "10-K" and form_family("10-QT") == "10-Q"


def test_chunks_follow_paragraphs_and_offsets_are_exact() -> None:
    paragraphs = [f"Paragraph {i} " + "word " * 60 for i in range(12)]
    text = "\n\n".join(p.strip() for p in paragraphs)
    chunks = chunk_section(text)

    assert len(chunks) > 1
    for chunk in chunks:
        assert text[chunk.char_start : chunk.char_end] == chunk.text
        assert len(chunk.text) <= MAX_CHARS
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))
    # Every paragraph is covered exactly once, in order.
    assert "".join(c.text.replace("\n\n", "") for c in chunks) == text.replace("\n\n", "")


def test_long_paragraphs_split_at_sentences_then_spaces() -> None:
    sentence = "The company may not realize the anticipated benefits of the acquisition. "
    long_paragraph = (sentence * 60).strip()
    no_breaks = "x" * 50 + " " + "y" * 4500
    for text in (long_paragraph, no_breaks):
        chunks = chunk_section(text)
        assert all(len(c.text) <= MAX_CHARS for c in chunks)
        assert all(text[c.char_start : c.char_end] == c.text for c in chunks)
    assert all(c.text.endswith(".") for c in chunk_section(long_paragraph))


def test_section_diff_classifies_paragraphs() -> None:
    blocks = diff_paragraphs([RISK_A, RISK_B_2023, RISK_C], [RISK_A, RISK_B_2024, RISK_D])

    assert [b.kind for b in blocks] == ["same", "changed", "removed", "added"]
    changed = blocks[1]
    inserted = "".join(op.text for op in changed.words if op.op == "insert")
    assert "materially" in inserted and "cash flow" in inserted
    summary = summarise(blocks)
    assert (summary.same, summary.changed, summary.added, summary.removed) == (1, 1, 1, 1)
    assert summary.words_added >= len(RISK_D.split())


def test_word_diff_reconstructs_both_versions() -> None:
    before, after = RISK_B_2023, RISK_B_2024
    ops = word_diff(before, after)

    assert "".join(o.text for o in ops if o.op != "delete").strip() == after
    assert "".join(o.text for o in ops if o.op != "insert").split() == before.split()


def test_whitespace_only_changes_are_not_changes() -> None:
    assert [b.kind for b in diff_paragraphs(["A  b c"], ["A b c"])] in (["same"], ["changed"])
    assert [b.kind for b in diff_paragraphs(["A   b c"], ["A b c"])] == ["same"]


def test_form4_transactions_are_parsed_as_reported() -> None:
    parsed = parse_form4(form4_xml())

    assert parsed.issuer_cik == 999001 and parsed.rule_10b5_1 is True
    assert [(o.name, o.is_officer, o.officer_title) for o in parsed.owners] == [
        ("Doe Jane", True, "Chief Financial Officer")
    ]
    sale, purchase, exercise = parsed.transactions
    assert (sale.transaction_code, sale.shares, sale.price, sale.acquired_disposed) == (
        "S",
        Decimal(1000),
        Decimal("150.25"),
        "D",
    )
    assert sale.transaction_date == date(2024, 11, 4) and sale.ownership == "D"
    assert (purchase.transaction_code, purchase.ownership, purchase.ownership_nature) == (
        "P",
        "I",
        "By Trust",
    )
    assert exercise.is_derivative and exercise.exercise_price == Decimal("35.5")
    # The price exists only as a footnote reference: it stays empty.
    assert exercise.price is None
    assert exercise.underlying_security == "Common Stock"


def test_form4_parser_rejects_entities_and_foreign_documents() -> None:
    parsed = parse_form4(XXE_FORM4)
    assert parsed.owners[0].name != "" and "root:" not in parsed.owners[0].name
    with pytest.raises(Form4Error):
        parse_form4(b"<html><body>not xml")
    with pytest.raises(Form4Error):
        parse_form4(b"<?xml version='1.0'?><edgarSubmission/>")
    assert raw_xml_name("xslF345X05/wk-form4_1.xml") == "wk-form4_1.xml"


def test_filing_index_columns_are_parsed_and_validated() -> None:
    data = columns(FILINGS)
    data["accessionNumber"].append("not-an-accession")
    data["form"].append("8-K")
    data["filingDate"].append("2024-01-01")
    filings, rejected = parse_filings(data)

    assert rejected == 1 and len(filings) == len(FILINGS)
    eight_k = next(f for f in filings if f.form == "8-K")
    assert eight_k.items == "2.02,9.01" and eight_k.filed_date == date(2024, 10, 30)
    assert eight_k.accepted_at is not None and eight_k.accepted_at.tzinfo is not None
    assert next(f for f in filings if f.form == "4").description is None
