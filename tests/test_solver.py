"""Schnelltest der Kernlogik:  python -m pytest tests  (oder: python tests/test_solver.py)"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.solver import EmployeeInput, ShiftInput, solve  # noqa: E402


def _shift(i, day, t, start="19:00", end="21:00", req=1):
    d = f"2026-10-{day:02d}"
    return ShiftInput(i, d, t, datetime.fromisoformat(f"{d}T{start}"), datetime.fromisoformat(f"{d}T{end}"), req)


def test_hard_constraints():
    emps = [
        EmployeeInput(1, "Kasse", "KASSE", 13.90, blocked={"2026-10-02"}),
        EmployeeInput(2, "Einlass", "EINLASS", 13.90),
        EmployeeInput(3, "Beide", "BEIDE", 13.90, weekend_exclusion="SA"),
    ]
    shifts = [_shift(1, 2, "ABENDKASSE"), _shift(2, 3, "ABENDKASSE"), _shift(3, 3, "EINLASS"),
              _shift(4, 5, "EINLASS", req=2)]
    res = solve(emps, shifts, minijob_limit_eur=603, time_limit_s=5)
    pairs = set(res.assignments)
    assert (1, 1) not in pairs                          # Sperrtag
    assert all(not (e == 2 and s in (1, 2)) for s, e in pairs)  # Einlass nie an der Kasse
    assert (2, 3) not in pairs and (3, 3) not in pairs  # 03.10.26 = Samstag, Person 3 schließt Sa aus
    assert (1, 3) in pairs                              # Kasse am 02.10.: nur Person 3 verfügbar
    assert (2, 1) in pairs and (3, 2) in pairs          # Sa: Kasse -> Person 1, Einlass -> Person 2
    assert res.unfilled == {}                           # alles besetzbar


def test_minijob_cap():
    emps = [EmployeeInput(1, "A", "BEIDE", 13.90, max_shifts=31)]
    shifts = [_shift(i, i, "EINLASS", "10:00", "18:00") for i in range(1, 11)]  # 10 x 8 h
    res = solve(emps, shifts, minijob_limit_eur=603, time_limit_s=5)
    hours = 8 * len(res.assignments)
    assert hours * 13.90 <= 603                         # max. 5 Schichten = 556 €


def test_min_wish_and_groups():
    # 6 Einlass-Abende, 3 Personen; A wünscht mind. 4, B max. 1 Kasse-Gruppe egal, C nur Kasse
    emps = [EmployeeInput(1, "A", "BEIDE", 13.90, min_shifts=4, max_shifts=6),
            EmployeeInput(2, "B", "BEIDE", 13.90, max_shifts=6, type_limits={"EINLASS": {"min": 0, "max": 1}}),
            EmployeeInput(3, "C", "EINLASS", 13.90, max_shifts=6)]
    shifts = [_shift(i, i, "EINLASS") for i in range(5, 11)]
    res = solve(emps, shifts, minijob_limit_eur=603, time_limit_s=5)
    count = {e: sum(1 for _, x in res.assignments if x == e) for e in (1, 2, 3)}
    assert count[1] >= 4                                   # Wunsch-Minimum erfüllt
    assert count[2] <= 1                                   # Gruppen-Obergrenze Einlass


def test_partial_day_blocks():
    # Person kann am 06.10. nur tagsüber, am 07.10. nur abends
    e = EmployeeInput(1, "A", "BEIDE", 13.90, max_shifts=10,
                      blocked={"2026-10-06": "ABEND", "2026-10-07": "TAG"})
    shifts = [_shift(1, 6, "TAGESKASSE", "11:00", "13:00"), _shift(2, 6, "ABENDKASSE", "17:30", "19:45"),
              _shift(3, 7, "TAGESKASSE", "11:00", "13:00"), _shift(4, 7, "EINLASS", "17:30", "22:30")]
    res = solve([e], shifts, minijob_limit_eur=603, max_shifts_per_day=1, time_limit_s=5)
    got = {s for s, _ in res.assignments}
    assert got == {1, 4}                                   # 06.: nur Tageskasse, 07.: nur abends


if __name__ == "__main__":
    test_partial_day_blocks()
    test_hard_constraints()
    test_minijob_cap()
    test_min_wish_and_groups()
    print("Alle Tests bestanden.")
