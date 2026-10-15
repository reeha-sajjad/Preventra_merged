"""
Driver sentences for a Model Studio model's explanations.

The sandbox runner explains a hospital's own model by substitution (see
api/studio_runner._explain): each input is set to the training data's typical
value and the change in risk is measured. This turns one such result into the
"<label>: <value> (<explanation>)" string every driver on the dashboard uses.

For the 94 inputs the built-in model also reads, the label and value read
exactly as the built-in model's drivers do (models/mimic_drivers.LABEL_MAP),
so a patient's drivers look the same whichever model scored them; the
explanation says what the hospital's model found. The explanation must not
contain " (" - the API splits drivers on the last one
(api/mimic_scoring._split_driver).
"""
from __future__ import annotations

from models.mimic_drivers import LABEL_MAP, _format_value


def format_studio_driver(feature: str, value, delta: float, lowers: bool = False) -> str:
    label, _, kind = LABEL_MAP.get(feature, (feature.replace("_", " ").capitalize(), "", "number"))
    points = abs(float(delta)) * 100
    if lowers:
        why = (f"lowers this patient's risk by about {points:.1f} points compared with a "
               "typical patient in the training data")
    else:
        why = (f"raises this patient's risk by about {points:.1f} points compared with a "
               "typical patient in the training data")
    return f"{label}: {_format_value(value, kind)} ({why})"
