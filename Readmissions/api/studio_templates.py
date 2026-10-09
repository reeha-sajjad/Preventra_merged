"""
Model Studio templates: the code that is not generated.

STANDARD_PIPELINE is two things at once. It is the reference implementation the
LLM is shown when it writes a pipeline for a new dataset, and it is the
fallback that runs when the generated code still fails after its retries, so
a training run always ends with a model rather than a stack trace.

BUILTIN_MIMIC_ADAPTER lets the original Phase 1 model run through the same
sandbox as everything else, so it can be scored against a new dataset on the
same test rows as a candidate.
"""

# Offered as "known models" when starting a run. `auto` lets the pipeline
# cross-validate several and keep the best.
ALGORITHMS = {
    "auto": "Let the pipeline choose (cross-validated comparison)",
    "logistic_regression": "Logistic regression",
    "random_forest": "Random forest",
    "gradient_boosting": "Gradient boosting (HistGradientBoosting)",
}

STANDARD_PIPELINE = '''"""
Preventra standard pipeline.

preprocess and transform are stateless: they look at one row at a time and
learn nothing from the data, so they behave identically at training and at
scoring time. Everything learned from data - imputation values, encodings,
the model itself, its calibration - lives inside the estimator train() returns.
"""
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedGroupKFold, StratifiedKFold, cross_val_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, OrdinalEncoder, StandardScaler

MISSING_TOKENS = {"", "nan", "none", "null", "na", "n/a", "?", "unknown"}
# Yes/no written as words or booleans become 1/0, so a numeric column is always
# a float column - True minus False is not a number a model can use.
BOOLEAN_WORDS = {"true": 1.0, "false": 0.0, "yes": 1.0, "no": 0.0, "y": 1.0, "n": 0.0}


def _as_number(value):
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        return BOOLEAN_WORDS.get(value.strip().lower(), value)
    return value


def preprocess(df, config):
    """Clean raw values: numbers to numbers, text trimmed, missing markers to NaN."""
    df = df.copy()
    numeric = set(config.get("numeric_columns", []))
    for col in df.columns:
        if col in numeric:
            values = df[col].astype("object").map(_as_number)
            df[col] = pd.to_numeric(values, errors="coerce").astype(float)
        else:
            text = df[col].astype("object")
            text = text.where(text.isna(), text.astype(str).str.strip())
            df[col] = text.where(~text.astype(str).str.lower().isin(MISSING_TOKENS), np.nan)
    return df


def transform(df, config):
    """Model input features. Numeric columns as numbers, the rest as text
    categories; encoding happens inside the model so it is learned on training
    rows only.

    With patient history (rows sorted by patient, then time), each numeric
    column also gets its change since the patient's previous row, plus a count
    of how many rows came before - earlier rows only, never later ones."""
    numeric = [c for c in config.get("numeric_columns", []) if c in df.columns]
    categorical = [c for c in config.get("categorical_columns", []) if c in df.columns]
    out = df[numeric + categorical].copy()
    for col in categorical:
        out[col] = out[col].astype("object")
    if config.get("history"):
        earlier = df.groupby(config["history_group_column"], sort=False)
        for col in numeric:
            out[col + "_change"] = df[col] - earlier[col].shift(1)
        out["rows_before"] = earlier.cumcount()
    return out


def _columns(X):
    numeric = [c for c in X.columns if pd.api.types.is_numeric_dtype(X[c])]
    return numeric, [c for c in X.columns if c not in numeric]


def _candidates(X, preference):
    numeric, categorical = _columns(X)
    text_missing = SimpleImputer(strategy="constant", fill_value="missing")

    logistic = Pipeline([
        ("prep", ColumnTransformer([
            ("num", Pipeline([("impute", SimpleImputer(strategy="median", add_indicator=True)),
                              ("scale", StandardScaler())]), numeric),
            ("cat", Pipeline([("impute", text_missing),
                              ("encode", OneHotEncoder(handle_unknown="infrequent_if_exist",
                                                       min_frequency=20))]), categorical),
        ])),
        ("model", LogisticRegression(max_iter=2000, C=0.5)),
    ])
    forest = Pipeline([
        ("prep", ColumnTransformer([
            ("num", SimpleImputer(strategy="median"), numeric),
            ("cat", Pipeline([("impute", text_missing),
                              ("encode", OrdinalEncoder(handle_unknown="use_encoded_value",
                                                        unknown_value=-1))]), categorical),
        ])),
        ("model", RandomForestClassifier(n_estimators=300, min_samples_leaf=20,
                                         max_features="sqrt", n_jobs=2, random_state=0)),
    ])
    boosting = Pipeline([
        ("prep", ColumnTransformer([
            ("cat", Pipeline([("impute", text_missing),
                              ("encode", OrdinalEncoder(handle_unknown="use_encoded_value",
                                                        unknown_value=-1,
                                                        max_categories=250))]), categorical),
            ("num", "passthrough", numeric),
        ])),
        ("model", HistGradientBoostingClassifier(
            learning_rate=0.06, max_iter=300, min_samples_leaf=40, l2_regularization=1.0,
            early_stopping=True, random_state=0,
            categorical_features=[True] * len(categorical) + [False] * len(numeric))),
    ])
    every = {"logistic_regression": logistic, "random_forest": forest,
             "gradient_boosting": boosting}
    if preference in every:
        return {preference: every[preference]}
    return every


def train(X, y, groups, config):
    """Cross-validate the candidates, keep the best, then calibrate it so the
    score reads as a probability."""
    preference = config.get("algorithm_preference", "auto")
    candidates = _candidates(X, preference)

    # Model selection on at most 60,000 rows keeps a large file from turning a
    # minutes-long run into an hour; the chosen model is refit on everything.
    rng = np.random.RandomState(0)
    take = np.arange(len(X)) if len(X) <= 60000 else np.sort(rng.permutation(len(X))[:60000])
    Xs, ys = X.iloc[take], y.iloc[take]
    if groups is not None:
        splitter = StratifiedGroupKFold(n_splits=3, shuffle=True, random_state=0)
        split_groups = groups.iloc[take]
    else:
        splitter = StratifiedKFold(n_splits=3, shuffle=True, random_state=0)
        split_groups = None

    scores = {}
    if len(candidates) == 1:
        scores = {name: None for name in candidates}
    else:
        for name, pipe in candidates.items():
            cv = cross_val_score(pipe, Xs, ys, scoring="roc_auc", cv=splitter,
                                 groups=split_groups)
            scores[name] = round(float(np.mean(cv)), 4)
    best = max(scores, key=lambda k: scores[k] if scores[k] is not None else 0)

    method = "isotonic" if int(y.sum()) >= 1000 else "sigmoid"
    # Calibrate on patients the model was not fitted on: with several rows per
    # patient, plain folds put the same patient on both sides.
    calibration_cv = (list(StratifiedGroupKFold(n_splits=3, shuffle=True, random_state=0)
                           .split(X, y, groups)) if groups is not None else 3)
    model = CalibratedClassifierCV(candidates[best], method=method, cv=calibration_cv)
    model.fit(X, y)
    report = {
        "algorithm": best,
        "cv_auroc": scores,
        "calibration": method,
        "n_train": int(len(X)),
        "notes": "Candidates compared by 3-fold cross-validated AUROC"
                 + (" grouped by patient" if groups is not None else "")
                 + "; the winner was refit on all training rows and calibrated.",
    }
    return model, report
'''

# The built-in Phase 1 model already contains its own encoding; it only needs
# the categorical columns cast to the exact category sets it was trained on.
BUILTIN_MIMIC_ADAPTER = '''"""Adapter that lets the built-in Phase 1 model score a Studio dataset."""
import pandas as pd


def preprocess(df, config):
    df = df.copy()
    categoricals = config.get("categoricals", {})
    for col in df.columns:
        if col in categoricals:
            df[col] = pd.Categorical(df[col].astype("object"), categories=categoricals[col])
        else:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def transform(df, config):
    return df[[c for c in config["feature_columns"] if c in df.columns]]


def train(X, y, groups, config):
    raise RuntimeError("The built-in model is not retrained through its adapter")
'''
