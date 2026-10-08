#!/usr/bin/env python3
"""
Synthetic datasets for trying Model Studio end to end.

Everything here is generated; no row comes from MIMIC or any real patient, so
the files are safe to share, commit and demo. The outcome is driven by a
known, clinically plausible risk function plus noise, which is what lets the
tests check that a trained model actually found the signal.

    python scripts/make_studio_demo_data.py                 # writes both files
    python scripts/make_studio_demo_data.py --out /tmp/demo --patients 4000

discharge_demo.csv   one row per discharge, outcome readmit_30d
weekly_demo.csv      one row per patient-week (weeks 1-4), outcome readmitted_30d

Each file also carries a deliberately leaky column (readmission_date /
readmit_visit_flag) so the leakage check has something to catch.
"""

from __future__ import annotations

import argparse
import os

import numpy as np
import pandas as pd

CONDITIONS = ["Heart failure", "COPD", "Pneumonia", "Diabetes", "Kidney disease",
              "Stroke", "Sepsis", "Hip fracture", "Other"]
CONDITION_RISK = {"Heart failure": 0.55, "COPD": 0.45, "Pneumonia": 0.2, "Diabetes": 0.25,
                  "Kidney disease": 0.5, "Stroke": 0.3, "Sepsis": 0.4, "Hip fracture": 0.15,
                  "Other": 0.0}


def _sigmoid(x):
    return 1 / (1 + np.exp(-x))


def discharges(n: int, rng) -> pd.DataFrame:
    age = np.clip(rng.normal(68, 14, n), 18, 100).round()
    sex = rng.choice(["Female", "Male"], n)
    insurance = rng.choice(["Medicare", "Medicaid", "Private", "Self-pay"], n,
                           p=[0.5, 0.18, 0.27, 0.05])
    condition = rng.choice(CONDITIONS, n, p=[.14, .11, .12, .12, .09, .08, .07, .07, .20])
    prior = rng.poisson(0.8 + (age > 75) * 0.6, n)
    los = np.clip(rng.gamma(2.2, 2.4, n), 1, 45).round()
    charlson = np.clip(rng.poisson(np.clip(2 + (age - 60) / 15, 0.2, None), n), 0, 15)
    sodium = rng.normal(138, 4, n).round(1)
    creatinine = np.clip(rng.lognormal(0.05, 0.45, n), 0.3, 9).round(2)
    hemoglobin = rng.normal(12, 1.9, n).round(1)
    meds = np.clip(rng.poisson(9 + charlson, n), 0, 40)
    emergency = rng.random(n) < 0.62
    disch = rng.choice(["Home", "Home with services", "Skilled nursing", "Left against advice"],
                       n, p=[.55, .25, .17, .03])
    followup_booked = rng.random(n) < 0.7

    # Scaled so the best possible AUROC is ~0.76, the upper end of what real
    # readmission models reach - a signal worth finding, not a toy one.
    logit = -3.6 + 1.6 * (0.018 * (age - 65) + 0.32 * np.minimum(prior, 5) + 0.03 * los
             + 0.11 * charlson + 0.06 * np.clip(135 - sodium, 0, None)
             + 0.35 * np.log(creatinine) + 0.08 * np.clip(11 - hemoglobin, 0, None)
             + 0.25 * emergency + np.array([CONDITION_RISK[c] for c in condition])
             + np.select([disch == "Skilled nursing", disch == "Left against advice"], [0.3, 0.9], 0)
             - 0.35 * followup_booked + 0.1 * (insurance == "Medicaid"))
    readmit = rng.random(n) < _sigmoid(logit)

    discharge_date = pd.Timestamp("2024-01-01") + pd.to_timedelta(rng.integers(0, 600, n), "D")
    # Leakage on purpose: only known AFTER the outcome.
    readmission_date = np.where(readmit, (discharge_date + pd.to_timedelta(
        rng.integers(2, 30, n), "D")).strftime("%Y-%m-%d"), "")

    # Missing values the way real extracts have them.
    sodium = np.where(rng.random(n) < 0.06, np.nan, sodium)
    hemoglobin = np.where(rng.random(n) < 0.08, np.nan, hemoglobin)

    return pd.DataFrame({
        "patient_id": [f"P{100000 + i}" for i in range(n)],
        "discharge_date": discharge_date.strftime("%Y-%m-%d"),
        "age": age.astype(int), "sex": sex, "insurance": insurance,
        "primary_condition": condition, "prior_admissions_12m": prior,
        "length_of_stay_days": los.astype(int), "charlson_index": charlson,
        "sodium_last": sodium, "creatinine_last": creatinine, "hemoglobin_last": hemoglobin,
        "n_discharge_medications": meds, "emergency_admission": np.where(emergency, "Yes", "No"),
        "discharge_disposition": disch,
        "followup_booked": np.where(followup_booked, "Yes", "No"),
        "readmission_date": readmission_date,
        "readmit_30d": readmit.astype(int),
    })


def weekly(n_patients: int, rng) -> pd.DataFrame:
    base = discharges(n_patients, rng)
    rows = []
    for p in base.itertuples():
        frailty = rng.normal(0, 1)
        hf = p.primary_condition == "Heart failure"
        weight = 0.0
        for week in range(1, 5):
            weight += rng.normal(0.35 if hf and frailty > 0.5 else 0.0, 0.6)
            adherence = float(np.clip(rng.normal(88 - 12 * (frailty > 1), 10), 20, 100))
            rows.append({
                "patient_id": p.patient_id, "week_number": week,
                "week_date": (pd.Timestamp(p.discharge_date) + pd.Timedelta(days=7 * week))
                .strftime("%Y-%m-%d"),
                "age": p.age, "primary_condition": p.primary_condition,
                "discharge_risk_pct": None,
                "weight_change_kg": round(weight, 1),
                "adherence_pct": round(adherence),
                "refill_status": rng.choice(["collected", "late", "missed"],
                                            p=[.8, .12, .08] if frailty < 1 else [.55, .25, .2]),
                "followup_status": rng.choice(["attended", "scheduled", "missed"],
                                              p=[.6, .3, .1] if frailty < 1 else [.4, .3, .3]),
                "sbp": round(rng.normal(132 + 8 * (frailty > 1), 14)),
                "heart_rate": round(rng.normal(78 + 6 * (frailty > 1), 11)),
                "spo2": round(float(np.clip(rng.normal(96 - (frailty > 1.2), 1.8), 82, 100))),
                "_frailty": frailty,
            })
    df = pd.DataFrame(rows)
    patient = df.groupby("patient_id").agg(
        weight=("weight_change_kg", "max"), adherence=("adherence_pct", "mean"),
        missed=("followup_status", lambda s: (s == "missed").sum()),
        refill=("refill_status", lambda s: (s != "collected").sum()),
        spo2=("spo2", "min"), frailty=("_frailty", "first"))
    patient = patient.join(base.set_index("patient_id")[["prior_admissions_12m", "charlson_index"]])
    logit = (-2.9 + 0.45 * np.clip(patient.weight, 0, None) - 0.03 * (patient.adherence - 85)
             + 0.35 * patient.missed + 0.25 * patient.refill + 0.15 * (94 - patient.spo2).clip(0)
             + 0.25 * patient.prior_admissions_12m + 0.08 * patient.charlson_index
             + 0.3 * patient.frailty)
    readmit = (rng.random(len(patient)) < _sigmoid(logit)).astype(int)
    df["readmitted_30d"] = df.patient_id.map(dict(zip(patient.index, readmit)))
    # Leakage on purpose: a visit recorded because the patient came back.
    df["readmit_visit_flag"] = np.where((df.readmitted_30d == 1) & (df.week_number >= 3),
                                        "Y", "N")
    risk = base.set_index("patient_id")
    df["discharge_risk_pct"] = df.patient_id.map(
        (_sigmoid(-2 + 0.3 * risk.prior_admissions_12m + 0.1 * risk.charlson_index) * 100)
        .round(1))
    return df.drop(columns="_frailty")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "data",
                                                  "studio_demo"))
    ap.add_argument("--discharges", type=int, default=12000)
    ap.add_argument("--patients", type=int, default=3000, help="patients in the weekly file")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    rng = np.random.default_rng(args.seed)
    os.makedirs(args.out, exist_ok=True)
    d = discharges(args.discharges, rng)
    w = weekly(args.patients, rng)
    d.to_csv(os.path.join(args.out, "discharge_demo.csv"), index=False)
    w.to_csv(os.path.join(args.out, "weekly_demo.csv"), index=False)
    print(f"discharge_demo.csv: {len(d):,} rows, {d.readmit_30d.mean():.1%} readmitted")
    print(f"weekly_demo.csv:    {len(w):,} rows ({args.patients:,} patients), "
          f"{w.groupby('patient_id').readmitted_30d.first().mean():.1%} readmitted")
    print(f"written to {os.path.abspath(args.out)}")


if __name__ == "__main__":
    main()
