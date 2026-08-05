from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import pandas as pd

from .database import (
    create_template_record,
    delete_template_record,
    duplicate_template_record,
    get_template_record,
    list_template_records,
    update_template_record,
)


class TemplateValidationError(ValueError):
    """Raised when a template cannot be saved."""


@dataclass(frozen=True)
class TemplateInput:
    name: str
    description: str
    system: str
    groups: tuple[str, ...]


def normalize_template_input(
    name: str,
    description: str,
    system: str,
    groups: Iterable[str],
) -> TemplateInput:
    clean_name = " ".join(str(name or "").split())
    clean_description = str(description or "").strip()
    clean_system = str(system or "Other").strip() or "Other"
    clean_groups = tuple(sorted({str(group).strip() for group in groups if str(group).strip()}))

    if not clean_name:
        raise TemplateValidationError("Template name is required.")
    if len(clean_name) > 100:
        raise TemplateValidationError("Template name must be 100 characters or fewer.")
    if not clean_groups:
        raise TemplateValidationError("Select at least one user group.")

    return TemplateInput(
        name=clean_name,
        description=clean_description,
        system=clean_system,
        groups=clean_groups,
    )


def list_templates() -> pd.DataFrame:
    return list_template_records()


def get_template(template_id: int) -> dict | None:
    return get_template_record(template_id)


def create_template(
    name: str,
    description: str,
    system: str,
    groups: Iterable[str],
    actor: str,
) -> int:
    value = normalize_template_input(name, description, system, groups)
    return create_template_record(
        name=value.name,
        description=value.description,
        system=value.system,
        groups=list(value.groups),
        actor=actor,
    )


def update_template(
    template_id: int,
    name: str,
    description: str,
    system: str,
    groups: Iterable[str],
    actor: str,
) -> None:
    value = normalize_template_input(name, description, system, groups)
    update_template_record(
        template_id=template_id,
        name=value.name,
        description=value.description,
        system=value.system,
        groups=list(value.groups),
        actor=actor,
    )


def duplicate_template(template_id: int, new_name: str, actor: str) -> int:
    clean_name = " ".join(str(new_name or "").split())
    if not clean_name:
        raise TemplateValidationError("A name is required for the duplicated template.")
    if len(clean_name) > 100:
        raise TemplateValidationError("Template name must be 100 characters or fewer.")
    return duplicate_template_record(template_id, clean_name, actor)


def delete_template(template_id: int) -> None:
    delete_template_record(template_id)
