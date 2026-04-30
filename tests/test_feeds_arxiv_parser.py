from datetime import datetime, timezone

from app.services.feeds.sources.arxiv import _parse_atom


SAMPLE_ATOM = """
<feed>
  <entry>
    <title>Spectral methods for 2D NS singularities</title>
    <id>http://arxiv.org/abs/2604.12345</id>
    <summary>We present a pseudo-spectral solver for...</summary>
    <published>2026-04-30T08:12:00Z</published>
  </entry>
  <entry>
    <title>Another paper</title>
    <id>http://arxiv.org/abs/2604.99999</id>
    <summary>Short summary.</summary>
    <published>2026-04-29T10:00:00Z</published>
  </entry>
</feed>
"""


def test_parse_atom_basic():
    items = _parse_atom(SAMPLE_ATOM, "math.AP")
    assert len(items) == 2
    first = items[0]
    assert first.title == "Spectral methods for 2D NS singularities"
    assert first.link == "http://arxiv.org/abs/2604.12345"
    assert first.source == "arxiv:math.AP"
    assert first.published_utc == datetime(2026, 4, 30, 8, 12, tzinfo=timezone.utc)
    assert first.fingerprint and len(first.fingerprint) == 12


def test_parse_atom_handles_missing_summary():
    xml = (
        "<feed><entry>"
        "<title>T</title><id>http://x/abs/1</id>"
        "</entry></feed>"
    )
    items = _parse_atom(xml, "cs.CC")
    assert len(items) == 1
    assert items[0].summary == ""
    assert items[0].published_utc is None


def test_parse_atom_skips_entry_without_link():
    xml = "<feed><entry><title>No link</title></entry></feed>"
    assert _parse_atom(xml, "math.NT") == []
