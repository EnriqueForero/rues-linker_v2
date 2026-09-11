"""Regression tests for parameterized and catalog-validated reporting SQL."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from record_linkage.reporting._sqlite import (
    SQLiteTableNotFoundError,
    open_readonly_sqlite,
    quote_existing_table,
    validate_row_limit,
)
from record_linkage.reporting.dashboard import ExecutiveDashboard
from record_linkage.reporting.reports import ReportGenerator
from record_linkage.reporting.suite import EnhancedReportingSuite
from record_linkage.reporting.visualizer import DataVisualizer


class _LoggerStub:
    def debug(self, *_args, **_kwargs) -> None:
        pass

    def error(self, *_args, **_kwargs) -> None:
        pass

    def info(self, *_args, **_kwargs) -> None:
        pass

    def warning(self, *_args, **_kwargs) -> None:
        pass


@pytest.fixture
def reporting_db(tmp_path: Path) -> Path:
    # Un metacarácter real de URI verifica que una ruta local no pueda
    # inyectar query/fragment. Windows prohíbe '?' en nombres, pero permite
    # '#'; POSIX permite probar directamente el separador de query.
    uri_metacharacter = "#" if os.name == "nt" else "?"
    db_path = tmp_path / f"report{uri_metacharacter}mode=rw.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE correlative_table (ID_GRUPO TEXT, SRC TEXT)")
        conn.executemany(
            "INSERT INTO correlative_table VALUES (?, ?)",
            [
                ("G1", "X' OR 1=1 --"),
                ("G2", "X' OR 1=1 --"),
                ("G1", "OTHER"),
                ("G3", "OTHER"),
            ],
        )
        conn.execute('CREATE TABLE "golden""records" (ID_GRUPO TEXT, CONFIDENCE_SCORE REAL)')
        conn.executemany('INSERT INTO "golden""records" VALUES (?, ?)', [("G1", 0.5), ("G2", 0.95)])
        conn.commit()
    finally:
        conn.close()
    return db_path


def test_readonly_uri_and_catalog_quoting(reporting_db: Path) -> None:
    with open_readonly_sqlite(reporting_db) as conn:
        assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
        quoted = quote_existing_table(conn, 'golden"records')
        assert quoted == '"golden""records"'
        assert conn.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0] == 2
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("CREATE TABLE must_not_write (x INTEGER)")
        with pytest.raises(SQLiteTableNotFoundError):
            quote_existing_table(conn, 'golden"records; DROP TABLE correlative_table;--')


@pytest.mark.parametrize("invalid", [True, 1.5, "1; DROP TABLE x", -1])
def test_row_limit_rejects_non_integer_or_negative_values(invalid: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        validate_row_limit(invalid)


def test_all_reporting_loaders_support_quoted_catalog_table(reporting_db: Path) -> None:
    logger = _LoggerStub()

    report = ReportGenerator.__new__(ReportGenerator)
    report.sample_size = 10
    report.logger = logger
    report._create_empty_dataframe = lambda _name: pd.DataFrame()

    visualizer = DataVisualizer.__new__(DataVisualizer)
    visualizer.sample_size = 10
    visualizer.logger = logger

    dashboard = ExecutiveDashboard.__new__(ExecutiveDashboard)
    dashboard.logger = logger

    suite = EnhancedReportingSuite.__new__(EnhancedReportingSuite)
    suite.logger = logger

    table_name = 'golden"records'
    assert len(report._load_from_sqlite(str(reporting_db), table_name)) == 2
    assert len(visualizer._load_from_sqlite(str(reporting_db), table_name)) == 2
    assert len(dashboard._load_from_sqlite(str(reporting_db), table_name, 10)) == 2
    assert len(suite._load_smart_sample(str(reporting_db), table_name, 10)) == 2


def test_table_name_injection_is_rejected_without_modifying_database(reporting_db: Path) -> None:
    report = ReportGenerator.__new__(ReportGenerator)
    report.sample_size = 10
    report.logger = _LoggerStub()
    report._create_empty_dataframe = lambda _name: pd.DataFrame()

    attack = "correlative_table; DROP TABLE correlative_table;--"
    assert report._load_from_sqlite(str(reporting_db), attack).empty

    conn = sqlite3.connect(reporting_db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM correlative_table").fetchone()[0] == 4
    finally:
        conn.close()


def test_source_values_are_bound_not_interpolated(reporting_db: Path) -> None:
    suite = EnhancedReportingSuite.__new__(EnhancedReportingSuite)
    suite.correlative_data_ref = str(reporting_db)
    suite.logger = _LoggerStub()

    matrix = suite._calculate_intersection_from_db()

    malicious_source = "X' OR 1=1 --"
    assert matrix.loc[malicious_source, malicious_source] == 2
    assert matrix.loc["OTHER", "OTHER"] == 2
    assert matrix.loc[malicious_source, "OTHER"] == 1
    assert matrix.loc["OTHER", malicious_source] == 1
