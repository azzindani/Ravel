"""Generic dialect and identity rules, independent of any source: `units.flat.unless`, a
`path~pattern` source, and an identifier given as templates tried in order."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from extract.xml import _compiled_dialects, _source
from spec import default_registry
from spec.models import MarkupIdentity
from spec.xml_dialect import ByAttribute


def test_unless_keeps_an_element_that_states_another_path_out_of_the_unit() -> None:
    values = {r"^\d{3}$": "division1"}
    rule = ByAttribute(
        tag="^norm$", path="m/k", match="regex", values=values, unless=["m/enbez"]
    )
    outline = ET.fromstring("<norm><m><k>010</k></m></norm>")
    provision = ET.fromstring("<norm><m><k>010</k><enbez>§ 1</enbez></m></norm>")
    blank = ET.fromstring("<norm><m><k>010</k><enbez> </enbez></m></norm>")
    assert rule.unit_for(outline) == "division1"
    assert rule.unit_for(provision) is None
    assert rule.unit_for(blank) == "division1"


def test_an_empty_unless_is_left_out_of_the_dump_so_no_dialect_hash_moves() -> None:
    rule = ByAttribute(tag="^norm$", path="m/k", values={"010": "division1"})
    assert "unless" not in rule.model_dump(mode="json")
    assert ByAttribute(tag="^n$", path="k", values={}, unless=["e"]).model_dump()["unless"] == [
        "e"
    ]


def test_a_pattern_source_takes_the_text_only_where_the_pattern_is_found() -> None:
    d = _compiled_dialects(default_registry().get("de_gii@1.0"))[0]
    art = ET.fromstring("<norm><g><bez>Art 229</bez></g></norm>")
    dash = ET.fromstring("<norm><g><bez>-</bez></g></norm>")
    assert _source(art, r"g/bez~[^\s-]", d) == "Art 229"
    assert _source(dash, r"g/bez~[^\s-]", d) is None
    assert _source(dash, "g/bez", d) == "-"


def test_identifier_templates_are_tried_in_order() -> None:
    markup = MarkupIdentity(
        scope="law",
        identifier={"article": ["{article.outline} {article.number} {law.number}",
                                "{article.number} {law.number}"], "law": "{law.number}"},
    )  # fmt: skip
    inner = {"law": {"number": "BGBEG"}, "article": {"number": "§ 1", "outline": "Art 229"}}
    plain = {"law": {"number": "BGB"}, "article": {"number": "§ 7"}}
    first, second = markup.templates("article")
    assert MarkupIdentity.render(first, inner) == "Art 229 § 1 BGBEG"
    assert MarkupIdentity.render(first, plain) is None
    assert MarkupIdentity.render(second, plain) == "§ 7 BGB"
    assert markup.templates("law") == ["{law.number}"] and markup.templates("part") == []
