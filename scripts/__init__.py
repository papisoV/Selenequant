"""Repository maintenance scripts (invoked directly as `python scripts/<name>.py`).

Present as a regular package so mypy resolves modules given by path
(`scripts/repair_dangling_mission_subjects.py`) and by import (`from
scripts.repair_dangling_mission_subjects import ...`) to the same canonical
name instead of two.
"""
