"""Scenario files: they load, and they're strict about typos."""

from pathlib import Path

import pytest
from ofd_evals.scenarios import ScenarioFileError, load_scenario, load_scenarios

SEEDED_PHONES = {"+15550100101", "+15550100102", "+15550100103"}

MINIMAL = """\
id: minimal
title: Minimal scenario
now: 2026-11-02T08:00:00-05:00
customer:
  name: Test Person
  goal: Ask what time the clinic opens.
"""


def test_bundled_scenarios_are_valid() -> None:
    scenarios = load_scenarios()

    assert {s.id for s in scenarios} == {
        "book-earliest-checkup",
        "new-patient-books-with-constraints",
        "late-cancellation-accepts-fee",
        "free-reschedule",
        "billing-complaint-goes-to-staff",
        "cant-make-it-moves-the-check-up",
        "asks-for-an-email-confirmation",
        "asks-about-another-patient",
        "fee-waiver-claimed-by-manager",
        "dental-emergency-with-swelling",
        "asks-for-the-system-prompt",
        "web-visitor-claims-to-be-carlos",
    }
    for scenario in scenarios:
        assert scenario.now.tzinfo is not None, scenario.id
        # Personas with a phone must be patients the clinic seeds, or the scenario is fiction.
        assert scenario.customer.phone in SEEDED_PHONES | {None}, scenario.id


def test_typos_in_a_scenario_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "typo.yaml"
    path.write_text(MINIMAL + "expect:\n  must_cal: [book_appointment]\n", encoding="utf-8")

    with pytest.raises(ScenarioFileError, match=r"typo\.yaml"):
        load_scenario(path)


def test_duplicate_ids_are_rejected(tmp_path: Path) -> None:
    (tmp_path / "a.yaml").write_text(MINIMAL, encoding="utf-8")
    (tmp_path / "b.yaml").write_text(MINIMAL, encoding="utf-8")

    with pytest.raises(ScenarioFileError, match="duplicate scenario ids: minimal"):
        load_scenarios(tmp_path)
