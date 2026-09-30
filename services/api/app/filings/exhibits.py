"""Exhibits listed on an EDGAR filing index page ({accession}-index.htm)."""

from dataclasses import dataclass

from lxml import etree, html

from app.providers.sec_edgar import DOCUMENT_NAME


@dataclass(frozen=True)
class Exhibit:
    type: str
    document: str
    description: str | None


def parse_exhibits(content: bytes) -> list[Exhibit]:
    """Rows of the "Document Format Files" table whose type starts with EX-."""
    try:
        root = html.fromstring(content)
    except (etree.ParserError, ValueError):
        return []
    exhibits: list[Exhibit] = []
    for row in root.xpath('//table[contains(@class, "tableFile")]//tr'):
        cells = [" ".join(td.text_content().split()) for td in row.xpath("./td")]
        links = row.xpath(".//a/@href")
        if len(cells) < 4 or not links or not cells[3].upper().startswith("EX-"):
            continue
        document = str(links[0]).rsplit("/", 1)[-1]
        if DOCUMENT_NAME.fullmatch(document):
            exhibits.append(Exhibit(cells[3].upper(), document, cells[1] or None))
    return exhibits


def press_release(exhibits: list[Exhibit]) -> Exhibit | None:
    """The earnings press release of an 8-K: the first EX-99 exhibit in HTML or text."""
    return next(
        (
            e
            for e in exhibits
            if e.type.startswith("EX-99") and e.document.lower().endswith((".htm", ".html", ".txt"))
        ),
        None,
    )
