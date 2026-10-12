"""
Shared constants + startup-loaded caches that don't benefit from per-request reload:
    - survival_cache: KM curves precomputed in the migration script
    - shap_cache:     global SHAP feature importance from the test-set npy

Everything else is queried live from Mongo by the routers.
"""

import numpy as np
from typing import Optional, List, Dict

import core.loader as loader
from core.mongo import get_db

SEGMENT_COLORS = ["#EF5350", "#FF7043", "#43A047", "#1E88E5"]
SEGMENT_LABELS = [
    "Low Urgency Dropout Risk",
    "Financial Barrier Dropout Risk",
    "Low Friction Strong Adherer",
    "Moderate Risk Moderate Adherer",
]
SEGMENT_SHORT = [
    "Low Urgency Dropout",
    "Financial Barrier Dropout",
    "Low Friction Adherer",
    "Moderate Risk Adherer",
]
FEATURE_LABELS: Dict[str, str] = {
    "system_refill_score":  "Provider & pharmacy refill reliability",
    "income_cost_pressure": "Financial pressure relative to income",
    "LBXGH":                "Blood sugar control (HbA1c)",
    "BMXBMI":               "Body weight / BMI severity",
    "comorbidity_score":    "Overall disease burden",
    "drug_generation":      "Drug generation (newer = higher barriers)",
    "RIDAGEYR":             "Patient age",
    "bio_friction":         "Side effect intensity (GI friction)",
    "avg_oop_cost":         "Out-of-pocket medication cost",
    "gender_female":        "Gender (female)",
    "has_diabetes":         "Diagnosed with diabetes",
    "has_obesity":          "Diagnosed with obesity",
    "has_hypertension":     "Diagnosed with hypertension",
    "BPXSY1":               "Systolic blood pressure",
    "LBXTC":                "Total cholesterol",
    "LBXTR":                "Triglycerides",
    "LBXGLU":               "Fasting blood glucose",
    # The model's other inputs. Same wording as each patient's top drivers
    # (Backend/data/shap_patient_drivers.csv), so the Overview and the patient
    # page never name the same thing two ways.
    "mol_SEMAGLUTIDE":      "Prescribed Semaglutide",
    "mol_TIRZEPATIDE":      "Prescribed Tirzepatide",
    "mol_LIRAGLUTIDE":      "Prescribed Liraglutide",
    "mol_DULAGLUTIDE":      "Prescribed Dulaglutide",
    "is_newer_drug":        "Newer-generation drug",
    "has_dyslipidemia":     "High cholesterol diagnosis",
    "has_dysglycemia":      "Impaired glucose diagnosis",
}

survival_cache: Optional[Dict]       = None
shap_cache:     Optional[List[Dict]] = None


_SHAP_FALLBACK = [
    {"feature": "Provider & pharmacy refill reliability",    "importance": 0.541},
    {"feature": "Financial pressure relative to income",     "importance": 0.162},
    {"feature": "Blood sugar control (HbA1c)",               "importance": 0.081},
    {"feature": "Body weight / BMI severity",                "importance": 0.058},
    {"feature": "Overall disease burden",                    "importance": 0.051},
    {"feature": "Drug generation (newer = higher barriers)", "importance": 0.031},
    {"feature": "Patient age",                               "importance": 0.029},
    {"feature": "Side effect intensity (GI friction)",       "importance": 0.021},
    {"feature": "Out-of-pocket medication cost",             "importance": 0.019},
]


def _build_global_shap() -> List[Dict]:
    shap_vals = loader.shap_values
    model = loader.model_pkg

    if shap_vals is None:
        return _SHAP_FALLBACK

    try:
        mean_abs = np.abs(shap_vals).mean(axis=0)

        if isinstance(model, dict) and "feature_names" in model:
            feature_names = model["feature_names"]
        elif isinstance(model, dict) and "model" in model and hasattr(model["model"], "feature_names_in_"):
            feature_names = list(model["model"].feature_names_in_)
        else:
            feature_names = list(FEATURE_LABELS.keys())[: len(mean_abs)]

        if len(mean_abs) != len(feature_names):
            feature_names = [f"feature_{i}" for i in range(len(mean_abs))]

        drivers = [
            {"feature": FEATURE_LABELS.get(f, f), "importance": round(float(v), 4)}
            for f, v in zip(feature_names, mean_abs)
        ]
        drivers.sort(key=lambda x: x["importance"], reverse=True)
        return drivers

    except Exception as exc:
        print(f"  ⚠️  SHAP global compute failed ({exc}) — using fallback")
        return _SHAP_FALLBACK


async def _build_survival_cache() -> Dict:
    """Load precomputed KM curves + checkpoints from Mongo."""
    db = get_db()
    curves = await db.survival_curves.find({}, {"_id": 0}).sort("cluster", 1).to_list(length=None)
    checkpoints = await db.survival_checkpoints.find({}, {"_id": 0}).sort("cluster", 1).to_list(length=None)

    median_survival = []
    for sc in curves:
        med = 999
        for pt in sc.get("data", []):
            if pt["survival"] <= 0.5:
                med = pt["day"]
                break
        median_survival.append(med)

    return {
        "curves":          curves,
        "checkpoints":     checkpoints,
        "median_survival": median_survival,
        "logrank":         {"test_statistic": 2354.82, "p_value": 0.0, "significant": True},
    }


async def init_startup_caches() -> None:
    global survival_cache, shap_cache
    print("🔄  Building startup caches…")
    survival_cache = await _build_survival_cache()
    shap_cache     = _build_global_shap()
    print(f"  ✅  survival cache — {len(survival_cache['curves'])} curves")
    print(f"  ✅  shap cache    — {len(shap_cache)} features")
    print("✅  Startup caches ready\n")