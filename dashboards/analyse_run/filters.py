"""Parameter filters for one axis x weekday table.

The filters are the parameters that actually vary across the table's variants,
read from their recorded configs, so every axis gets its own without anything
here naming a parameter. Weekday-keyed parameters are shown by the table's own
weekday's value.

Mode / Method narrows the options of every other filter, so picking one mode
offers only that mode's values rather than a list mixing scales.

An empty filter means no restriction, so the default is every variant and each
dropdown lists all of its values to pick from.
"""

import hashlib
import json

import streamlit as st

WEEKDAY_KEYS = {"0", "1", "2", "3", "4"}


def _for_weekday(value, weekday: str):
    if isinstance(value, dict) and WEEKDAY_KEYS <= set(value):
        return value[weekday]
    return value


def _display(value) -> str:
    if isinstance(value, dict) and "type" in value:
        return value["type"] if value.get("value") is None else f"{value['type']} {_display(value['value'])}"
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    if abs(value) >= 1000 and float(value).is_integer():
        return f"{value:,.0f}"
    return f"{value:g}"


def _ordered(pairs: dict[str, object]) -> list[str]:
    """Distinct display values, in the order of their underlying values."""
    first = {}
    for shown, raw in pairs.values():
        first.setdefault(shown, raw)
    try:
        return sorted(first, key=lambda shown: first[shown])
    except TypeError:
        return sorted(first, key=lambda shown: json.dumps(first[shown], sort_keys=True, default=str))


def _varying(configs: dict[str, dict],
             weekday_of: dict[str, str]) -> dict[str, dict[str, tuple[str, object]]]:
    """parameter -> {variant key -> (display, raw)}, for parameters that vary."""
    names = sorted(set().union(*(c.keys() for c in configs.values()))) if configs else []
    out = {}
    for name in names:
        values = {}
        for key, config in configs.items():
            raw = _for_weekday(config.get(name), weekday_of[key])
            values[key] = (_display(raw), raw)
        if len({shown for shown, _ in values.values()}) > 1:
            out[name] = values
    return out


def _explained_by_mode(values: dict[str, tuple], mode_of: dict[str, str]) -> bool:
    """True when a parameter just restates the mode, one value per mode."""
    pairs = {(mode_of[key], values[key][0]) for key in values}
    return (len(pairs) == len({m for m, _ in pairs}) == len({v for _, v in pairs}))


def render(scope: str, configs: dict[str, dict], mode_of: dict[str, str],
           weekday_of: dict[str, str]) -> set[str]:
    """Draw the filters for these variants and return the keys that pass them.

    `weekday_of` gives each variant's weekday key ("0" = Monday), so one set of
    filters can span several weekdays and still read each variant's own value.
    """
    passing = set(configs)
    modes = sorted({mode for mode in mode_of.values() if mode})
    varying = _varying(configs, weekday_of)
    if len(modes) < 2 and not varying:
        return passing

    with st.container(horizontal=True, gap="medium"):
        if len(modes) > 1:
            picked_modes = st.multiselect("Mode / Method", modes, default=[],
                                          placeholder="All", key=f"{scope}:filter:mode")
            if picked_modes:
                passing = {key for key in passing if mode_of[key] in picked_modes}
            varying = {name: values for name, values in varying.items()
                       if not _explained_by_mode(values, mode_of)}
        in_modes = set(passing)
        for name, values in varying.items():
            options = _ordered({key: values[key] for key in in_modes})
            if len(options) < 2:
                continue
            # Keyed by its own options: a mode change that alters them starts
            # this filter afresh, one that leaves them alone keeps the pick.
            signature = hashlib.sha256("|".join(options).encode()).hexdigest()[:8]
            picked = st.multiselect(name, options, default=[], placeholder="All",
                                    key=f"{scope}:filter:{name}:{signature}")
            if picked:
                passing = {key for key in passing if values[key][0] in picked}
    return passing
