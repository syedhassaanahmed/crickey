from __future__ import annotations

import re
from dataclasses import dataclass

from crickey.parsers.common import (
    StatsguruParseError,
    document_from_html,
    element_text,
    options_from_select,
)

_SPECIAL_SELECT_RE = re.compile(
    r"^(?P<kind>havingselect|orderbyselect)_(?P<stat_type>[^_]+)_(?P<view>.+)$"
)


@dataclass(frozen=True)
class FormOption:
    value: str
    label: str
    selected: bool = False


@dataclass(frozen=True)
class FormControl:
    name: str
    kind: str
    value: str | None = None
    label: str | None = None
    options: tuple[FormOption, ...] = ()


@dataclass(frozen=True)
class FilterForm:
    controls: tuple[FormControl, ...]
    hidden_fields: dict[str, str]
    select_lists: dict[str, tuple[FormOption, ...]]
    checkbox_lists: dict[str, tuple[FormOption, ...]]
    radio_lists: dict[str, tuple[FormOption, ...]]
    minimum_lists: dict[tuple[str, str], tuple[FormOption, ...]]
    sort_lists: dict[tuple[str, str], tuple[FormOption, ...]]


def parse_filter_form(page: str) -> FilterForm:
    doc = document_from_html(page)
    controls: list[FormControl] = []
    hidden: dict[str, str] = {}
    selects: dict[str, tuple[FormOption, ...]] = {}
    checks: dict[str, list[FormOption]] = {}
    radios: dict[str, list[FormOption]] = {}
    minimum_lists: dict[tuple[str, str], tuple[FormOption, ...]] = {}
    sort_lists: dict[tuple[str, str], tuple[FormOption, ...]] = {}

    for select in doc.xpath("//select[@name]"):
        name = select.get("name", "")
        options = _options(select)
        controls.append(FormControl(name=name, kind="select", options=options))
        selects[name] = options
        select_id = select.get("id", "")
        if match := _SPECIAL_SELECT_RE.match(select_id):
            key = (match.group("stat_type"), match.group("view"))
            if match.group("kind") == "havingselect":
                minimum_lists[key] = options
            else:
                sort_lists[key] = options

    for input_element in doc.xpath("//input[@name]"):
        name = input_element.get("name", "")
        kind = (input_element.get("type") or "text").lower()
        value = input_element.get("value", "")
        if kind == "hidden":
            hidden[name] = value
            controls.append(FormControl(name=name, kind="hidden", value=value))
        elif kind in {"checkbox", "radio"}:
            option = FormOption(
                value=value,
                label=_input_label(input_element),
                selected=input_element.get("checked") is not None,
            )
            controls.append(FormControl(name=name, kind=kind, value=value, label=option.label))
            if kind == "checkbox":
                checks.setdefault(name, []).append(option)
            else:
                radios.setdefault(name, []).append(option)
        else:
            controls.append(
                FormControl(name=name, kind=kind, value=value, label=_input_label(input_element))
            )

    if not controls:
        raise StatsguruParseError("filter form controls are missing")
    return FilterForm(
        controls=tuple(controls),
        hidden_fields=hidden,
        select_lists=selects,
        checkbox_lists={key: tuple(value) for key, value in checks.items()},
        radio_lists={key: tuple(value) for key, value in radios.items()},
        minimum_lists=minimum_lists,
        sort_lists=sort_lists,
    )


def _options(select) -> tuple[FormOption, ...]:
    return tuple(FormOption(**option) for option in options_from_select(select))


def _input_label(input_element) -> str:
    label_id = input_element.get("id")
    if label_id:
        explicit = input_element.xpath(f'//label[@for="{label_id}"]')
        if explicit:
            return element_text(explicit[0])
    text = (input_element.tail or "").strip()
    if text:
        return " ".join(text.split())
    return element_text(input_element.getparent()).replace(element_text(input_element), "").strip()
