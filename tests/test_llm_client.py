from types import SimpleNamespace as NS

from drinsta.content.llm_client import extract_citations, strip_tracking


def test_strip_tracking_removes_only_utm_params():
    assert strip_tracking("https://a.com/x?utm_source=openai") == "https://a.com/x"
    assert strip_tracking("https://a.com/x?id=3&utm_medium=y") == "https://a.com/x?id=3"
    assert strip_tracking("https://a.com/x") == "https://a.com/x"


def test_extract_citations_reads_url_citation_annotations_only():
    cite = NS(type="url_citation", url="https://news.example/a", title="A")
    other = NS(type="file_citation", url="ignored", title="ignored")
    response = NS(
        output=[
            NS(type="web_search_call"),
            NS(type="message", content=[NS(type="output_text", annotations=[cite, other, cite])]),
        ]
    )
    assert extract_citations(response) == {"https://news.example/a": "A"}
