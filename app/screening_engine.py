"""Study eligibility screening.

The implementation lives in the domain modules below. This facade preserves
the original import path, including private helpers and patched collaborators.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
import secrets
from typing import Any, Iterable, Mapping, Optional

from app.compat import install_compatibility_exports as _install_compatibility_exports
from app.screening import constants as _constants
from app.screening import types as _types
from app.screening import normalization as _normalization
from app.screening import drafting as _drafting
from app.screening import validation as _validation
from app.screening import generation as _generation
from app.screening import evaluation as _evaluation

_install_compatibility_exports(
    __name__,
    {
        'dataclass': (_types, 'dataclass'),
        'json': (_generation, 'json'),
        'os': (_generation, 'os'),
        'secrets': (_drafting, 'secrets'),
        'Any': (_normalization, 'Any'),
        'Iterable': (_drafting, 'Iterable'),
        'Mapping': (_validation, 'Mapping'),
        'PROHIBITED_PROMPT_TERMS': (_constants, 'PROHIBITED_PROMPT_TERMS'),
        'EDITABLE_ATTRIBUTES': (_constants, 'EDITABLE_ATTRIBUTES'),
        'OCCUPATION_LABELS': (_constants, 'OCCUPATION_LABELS'),
        'ScreeningDecision': (_types, 'ScreeningDecision'),
        '_split_values': (_normalization, '_split_values'),
        '_question': (_drafting, '_question'),
        'deterministic_draft': (_drafting, 'deterministic_draft'),
        '_contains_prohibited_term': (_validation, '_contains_prohibited_term'),
        'validate_ai_draft': (_validation, 'validate_ai_draft'),
        'validate_researcher_edit': (_validation, 'validate_researcher_edit'),
        'generate_screening_draft': (_generation, 'generate_screening_draft'),
        'evaluate_screening': (_evaluation, 'evaluate_screening'),
    },
    consumers=(
        _constants,
        _types,
        _normalization,
        _drafting,
        _validation,
        _generation,
        _evaluation,
    ),
)

# Keep wildcard imports identical to the legacy module.
__all__ = [
    'Any',
    'EDITABLE_ATTRIBUTES',
    'Iterable',
    'Mapping',
    'OCCUPATION_LABELS',
    'Optional',
    'PROHIBITED_PROMPT_TERMS',
    'ScreeningDecision',
    'annotations',
    'dataclass',
    'deterministic_draft',
    'evaluate_screening',
    'generate_screening_draft',
    'json',
    'os',
    'secrets',
    'validate_ai_draft',
    'validate_researcher_edit',
]
