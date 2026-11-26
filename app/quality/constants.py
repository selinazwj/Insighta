"""Quality rule thresholds, dictionaries, and import metadata."""
from __future__ import annotations


LOW_QUALITY_TEXT_TERMS = {
    "test",
    "n/a",
    "na",
    "none",
    "idk",
    "dont know",
    "don't know",
    "dunno",
    "no idea",
    "whatever",
    "nah",
    "nothing",
    "skip",
    "asdf",
    "xxx",
}

NEGATIVE_ANSWERS = {
    "no",
    "none",
    "n/a",
    "na",
    "no car",
    "don't have a car",
    "do not have a car",
    "i don't have a car",
    "not applicable",
}

LOGIC_CONFLICT_RULES = [
    {
        "rule_id": "no_car_but_car_related",
        "negative_q_keywords": ["own a car", "have a car", "do you drive", "vehicle owner"],
        "negative_answers": NEGATIVE_ANSWERS,
        "conflict_q_keywords": [
            "car insurance",
            "license plate",
            "vehicle maintenance",
            "gas station",
            "parking",
        ],
    },
    {
        "rule_id": "student_but_retired",
        "negative_q_keywords": ["employment status", "occupation", "job status", "work status"],
        "negative_answers": {"student", "full-time student", "part-time student", "in school"},
        "conflict_q_keywords": ["retired", "retirement", "pension"],
    },
]

BULK_SUBMIT_WINDOW_HOURS = 24

BULK_SUBMIT_THRESHOLD = 3

IP_BULK_THRESHOLD = 5

EXCEL_METADATA_HEADERS = {
    "timestamp",
    "time",
    "submitted at",
    "submission time",
    "date",
    "email",
    "email address",
    "username",
    "name",
    "duration",
    "duration_seconds",
    "time_spent",
    "completion_seconds",
}
