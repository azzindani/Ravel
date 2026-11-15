"""The gate calibration against a stand-in connection: what it measures and what it records.
No database needed; the SQL is two statements and the arithmetic is the point."""

from __future__ import annotations

import json

import numpy as np
import pytest

from db import CalibrationError, calibrate_gate
from spec.corpus import CorpusSpec


class FakeConn:
    """Two orthogonal centroids; records every statement."""

    def __init__(self) -> None:
        self.statements: list[tuple[str, tuple]] = []

    def execute(self, sql: str, params: tuple = ()):
        self.statements.append((sql, params))
        rows = [("[1, 0]",), ("[0, 1]",)] if sql.lstrip().startswith("SELECT") else []
        return type("Cur", (), {"fetchall": lambda _self: rows})()


def cases(real: int = 30, off: int = 10) -> list[dict]:
    return [{"query": f"r{i}", "type": "procedural"} for i in range(real)] + [
        {"query": f"o{i}", "type": "out_of_domain"} for i in range(off)
    ]


def embed(texts: list[str]) -> np.ndarray:
    # In-domain questions sit on a centroid; off-topic ones halfway between the two.
    return np.array([[1.0, 0.0] if t.startswith("r") else [1.0, 1.0] for t in texts])


def recorded(conn: FakeConn) -> dict:
    ((_, params),) = [s for s in conn.statements if s[0].startswith("UPDATE")]
    return json.loads(params[0])


def test_the_floor_sits_below_every_real_question() -> None:
    conn = FakeConn()
    r = calibrate_gate(conn, "c", cases(), embed)
    assert r.centroid_floor == pytest.approx(0.98)
    assert r.off_topic_refused == 10  # cos 0.707 < 0.98
    assert recorded(conn)["centroid_floor"] == pytest.approx(0.98)


def test_the_declared_jurisdictions_are_recorded_beside_the_floor() -> None:
    conn = FakeConn()
    r = calibrate_gate(
        conn, "c", cases(), embed, foreign=["Malaysia", " japan ", "malaysia", ""]
    )
    assert r.foreign == ("malaysia", "japan")
    assert recorded(conn)["foreign"] == ["malaysia", "japan"]


def test_a_corpus_that_declares_none_records_an_empty_list() -> None:
    conn = FakeConn()
    calibrate_gate(conn, "c", cases(), embed)
    assert recorded(conn)["foreign"] == []


def test_a_dry_run_writes_nothing() -> None:
    conn = FakeConn()
    calibrate_gate(conn, "c", cases(), embed, foreign=["japan"], write=False)
    assert not [s for s in conn.statements if not s[0].lstrip().startswith("SELECT")]


def test_too_few_questions_is_refused() -> None:
    with pytest.raises(CalibrationError):
        calibrate_gate(FakeConn(), "c", cases(real=5), embed)


def test_the_regulation_corpus_declares_its_foreign_jurisdictions() -> None:
    foreign = CorpusSpec.find("id_reg_markdown").gate.foreign
    assert "malaysia" in foreign and "gdpr" in foreign
    assert "indonesia" not in foreign
