"""
Model Studio sandbox runner.

Runs as a SEPARATE PROCESS, started by api/model_studio.py, and is the only
place model code written by the LLM - or a model pickled by that code - is
ever executed or loaded. The API process itself never imports either: it reads
the numbers this runner writes, nothing more.

It is a standalone script on purpose: it imports nothing from the `api`
package, so a generated module cannot reach the API's database handle, its
settings or its secrets through an import. The parent also starts it with a
scrubbed environment, a time limit and a memory limit (see model_studio._run).

    python -I studio_runner.py train   <workdir>
    python -I studio_runner.py predict <workdir>

The workdir holds `pipeline_code.py` (the generated module, or a template) and
`job.json`, which says which files to read and write. The generated module
must define:

    preprocess(df, config) -> DataFrame   clean raw columns; same rows, same order
    transform(df, config)  -> DataFrame   engineer the model's input features
    train(X, y, groups, config) -> (estimator, report_dict)

The same preprocess and transform run again at prediction time, so the saved
estimator only ever sees features built exactly as they were in training.

PATIENT HISTORY
---------------
When the plan has a patient id and a date/week column (config["history"]), the
frame passed to preprocess/transform also carries those two as
config["history_group_column"] and config["history_order_column"], and its rows
arrive sorted by patient, then time. transform may then use a patient's EARLIER
rows (lags, changes, running counts, carrying a value forward) - never a later
row and never another patient's. check_no_lookahead() enforces that before any
model is trained: it removes later rows and confirms no earlier row's features
move. Predictions are written back in the file's original row order.
"""

from __future__ import annotations

import json
import sys
import time
import traceback
import warnings


def _emit(kind: str, **fields) -> None:
    """One JSON line per event on stdout; the parent turns these into the log."""
    print(json.dumps({"kind": kind, "t": round(time.time(), 2), **fields}, default=str),
          flush=True)


def _load_module(workdir: str):
    sys.path.insert(0, workdir)
    import pipeline_code  # noqa: E402  (the generated or template module)
    for name in ("preprocess", "transform", "train"):
        if not callable(getattr(pipeline_code, name, None)):
            raise RuntimeError(f"pipeline_code.py does not define {name}(...)")
    return pipeline_code


def _context(config) -> list:
    """The patient and time-order columns, when history is enabled."""
    if not config.get("history"):
        return []
    return [config["history_group_column"], config["history_order_column"]]


def _in_history_order(df, config):
    """Rows sorted by patient then time (stable), plus the permutation that
    puts results back in the file's own order; (df, None) without history."""
    import numpy as np

    if not config.get("history"):
        return df.reset_index(drop=True), None
    group, order = _context(config)
    missing = [c for c in (group, order) if c not in df.columns]
    if missing:
        raise RuntimeError("This model uses patient history, but the data has no "
                           f"{' or '.join(missing)} column")
    ranked = df.assign(__pos__=np.arange(len(df))).sort_values(
        [group, order, "__pos__"], kind="mergesort", na_position="last")
    return ranked.drop(columns="__pos__").reset_index(drop=True), ranked["__pos__"].values


def _restore(values, positions):
    import numpy as np

    if positions is None:
        return values
    out = np.empty_like(values)
    out[positions] = values
    return out


def _features(module, raw, config):
    """Raw input columns -> model features, with the contract checks the model
    cannot be trusted to keep on its own."""
    import pandas as pd

    n = len(raw)
    clean = module.preprocess(raw.copy(), config)
    if not isinstance(clean, pd.DataFrame):
        raise RuntimeError("preprocess() must return a pandas DataFrame")
    if len(clean) != n:
        raise RuntimeError(f"preprocess() changed the number of rows ({n} -> {len(clean)}); "
                           "it must clean values, not drop or add rows")
    X = module.transform(clean, config)
    if not isinstance(X, pd.DataFrame):
        raise RuntimeError("transform() must return a pandas DataFrame")
    if len(X) != n:
        raise RuntimeError(f"transform() changed the number of rows ({n} -> {len(X)})")
    label = config.get("label_column")
    if label and label in X.columns:
        raise RuntimeError(f"transform() output contains the label column '{label}'")
    # The patient and time columns give the code something to group and order
    # by; they are not features (an id would let a model memorise patients).
    X = X.drop(columns=[c for c in _context(config) if c in X.columns])
    X.columns = [str(c) for c in X.columns]
    return X.reset_index(drop=True)


def _same(a, b) -> bool:
    import numpy as np
    import pandas as pd

    if pd.api.types.is_numeric_dtype(a) and pd.api.types.is_numeric_dtype(b):
        x, y = a.to_numpy(dtype=float), b.to_numpy(dtype=float)
        return bool(np.all(np.isclose(x, y, rtol=1e-6, atol=1e-9, equal_nan=True)))
    x, y = a.astype("object"), b.astype("object")
    both_missing = x.isna().to_numpy() & y.isna().to_numpy()
    equal = (x.astype(str).to_numpy() == y.astype(str).to_numpy())
    return bool(np.all(both_missing | equal))


def check_no_lookahead(module, raw, config, max_groups: int = 300) -> None:
    """
    A row's features may depend only on that row and - with history - the same
    patient's EARLIER rows. Tested, not trusted: features are built twice, once
    on the full rows and once after removing data a row must not depend on
    (each sampled patient's later rows; without history, half the other rows),
    and every column of the rows present both times must come out identical.
    A column that moves used the future, another patient, or a statistic of the
    whole dataset.
    """
    import numpy as np

    rng = np.random.RandomState(0)
    if config.get("history"):
        group = _context(config)[0]
        sizes = raw.groupby(group, sort=False).size()
        multi = sizes[sizes >= 2].index.to_numpy()
        if not len(multi):
            return
        pick = set(rng.choice(multi, min(len(multi), max_groups), replace=False))
        full = raw[raw[group].isin(pick)].reset_index(drop=True)
        position = full.groupby(group, sort=False).cumcount().to_numpy()
        cut = full[group].map({g: rng.randint(1, sizes[g]) for g in pick}).to_numpy()
        keep = position < cut                    # each patient loses its later rows
        what = "a later row of the same patient"
    else:
        full = raw.sample(min(len(raw), 2000), random_state=0).reset_index(drop=True)
        keep = rng.rand(len(full)) < 0.5         # half of the other rows vanish
        what = "other rows"
    if keep.all() or not keep.any():
        return
    before = _features(module, full, config)
    after = _features(module, full[keep].reset_index(drop=True), config)
    if list(before.columns) != list(after.columns):
        raise RuntimeError("transform() returned different columns for different inputs; "
                           "its output columns must not depend on the data")
    moved = [c for c in before.columns
             if not _same(before.loc[keep].reset_index(drop=True)[c], after[c])]
    if moved:
        raise RuntimeError(
            f"Look-ahead check failed: {', '.join(moved[:10])} changed when {what} was removed, "
            "so these features use information a prediction must not see (a later week, "
            "another patient, or a statistic of the whole dataset). Use only the row itself"
            + (" and the same patient's EARLIER rows: groupby(patient).shift(k) with k >= 1, "
               "cumulative or expanding windows, ffill" if config.get("history") else "")
            + ". The same failure appears when per-patient values are put on the wrong rows: "
            "results of groupby(...).expanding() / rolling() / apply() come back in a different "
            "order, so assign them by index (groupby(...)[col].transform(...), or "
            "reset_index(level=0, drop=True)), never with .values or .to_numpy().")


def _read(path, dtypes=None):
    import pandas as pd
    return pd.read_csv(path, low_memory=False, dtype=dtypes)


def run_train(workdir: str, job: dict) -> None:
    import joblib
    import numpy as np
    import pandas as pd

    config = job["config"]
    module = _load_module(workdir)
    df, _ = _in_history_order(_read(f"{workdir}/{job['train_file']}", job.get("dtypes")), config)
    feature_cols = config["feature_columns"] + _context(config)
    y = pd.Series(df[job["target_field"]].astype(int).values, name="label")
    groups = (pd.Series(df[job["group_field"]].astype(str).values, name="group")
              if job.get("group_field") else None)
    _emit("log", message=f"Training rows: {len(df):,}, positives: {int(y.sum()):,}")

    check_no_lookahead(module, df[feature_cols], config)
    _emit("log", message="Look-ahead check passed: every feature uses only the row itself"
                          + (" and the same patient's earlier rows" if config.get("history") else ""))

    started = time.time()
    X = _features(module, df[feature_cols], config)
    _emit("log", message=f"Preprocess + transform produced {X.shape[1]} features "
                          f"in {time.time() - started:.1f}s")

    started = time.time()
    result = module.train(X, y, groups, config)
    if not (isinstance(result, tuple) and len(result) == 2):
        raise RuntimeError("train() must return (estimator, report_dict)")
    estimator, report = result
    if not hasattr(estimator, "predict_proba"):
        raise RuntimeError("train() returned an estimator without predict_proba")
    probe = np.asarray(estimator.predict_proba(X.head(50)))
    if probe.ndim != 2 or probe.shape[1] != 2:
        raise RuntimeError("predict_proba must return two columns (not readmitted, readmitted)")
    _emit("log", message=f"Model trained in {time.time() - started:.1f}s")

    joblib.dump({"estimator": estimator, "feature_columns": list(X.columns)},
                f"{workdir}/model.joblib", compress=3)
    with open(f"{workdir}/train_result.json", "w") as fh:
        json.dump({"report": report if isinstance(report, dict) else {"note": str(report)},
                   "n_features": int(X.shape[1]),
                   "feature_names": list(X.columns)[:500]}, fh, default=str)
    _emit("done")


def _install_sklearn_compat() -> None:
    """The built-in Phase 1 model was pickled under an older scikit-learn that
    had a private class the current one dropped. Same shim as
    models/mimic_drivers.py, repeated here because this runner imports nothing
    from the project."""
    import sklearn.compose._column_transformer as ct

    if not hasattr(ct, "_RemainderColsList"):
        class _RemainderColsList(list):
            def __init__(self, cols=(), *args, **kwargs):
                super().__init__(cols)

        ct._RemainderColsList = _RemainderColsList


def run_predict(workdir: str, job: dict) -> None:
    """Score one or more files; optionally rank feature importance on one."""
    import joblib
    import numpy as np
    import pandas as pd

    module = _load_module(workdir)
    if job.get("sklearn_compat"):
        _install_sklearn_compat()
    bundle = joblib.load(job.get("model_file") or f"{workdir}/model.joblib")
    # Studio bundles carry estimator + feature_columns; the original Phase 1
    # bundle (the built-in model) carries model + features.
    estimator = bundle.get("estimator", bundle.get("model"))
    columns = bundle.get("feature_columns") or bundle.get("features")
    config = job["config"]

    for item in job["inputs"]:
        df, positions = _in_history_order(_read(item["path"], job.get("dtypes")), config)
        raw = df.reindex(columns=config["feature_columns"] + _context(config))
        missing = [c for c in config["feature_columns"] if c not in df.columns]
        X = _features(module, raw, config).reindex(columns=columns)
        proba = np.asarray(estimator.predict_proba(X))[:, 1]
        pd.DataFrame({"score": _restore(proba, positions)}).to_csv(item["out"], index=False)
        _emit("log", message=f"Scored {len(df):,} rows from {item['name']}",
              missing_inputs=missing)

        if item.get("importance_label_field"):
            from sklearn.inspection import permutation_importance
            y = df[item["importance_label_field"]].astype(int).values
            if 0 < y.sum() < len(y):
                take = np.random.RandomState(0).permutation(len(X))[:3000]
                imp = permutation_importance(estimator, X.iloc[take], y[take],
                                             scoring="roc_auc", n_repeats=3, random_state=0)
                ranked = sorted(zip(columns, imp.importances_mean, imp.importances_std),
                                key=lambda t: -t[1])[:20]
                with open(item["importance_out"], "w") as fh:
                    json.dump([{"feature": f, "importance": float(m), "std": float(s)}
                               for f, m, s in ranked], fh)
    _emit("done")


def _apply_limits(limits: dict) -> None:
    """CPU, memory and file-size caps, set before any project or generated code
    is imported. Hard limit = soft limit, so nothing loaded later can lift them."""
    import resource

    def cap(kind, value):
        if value:
            resource.setrlimit(kind, (value, value))

    cap(resource.RLIMIT_CPU, int(limits.get("cpu_seconds") or 0))
    cap(resource.RLIMIT_AS, int(limits.get("memory_mb") or 0) * 1024 * 1024)
    cap(resource.RLIMIT_FSIZE, int(limits.get("file_mb") or 0) * 1024 * 1024)


def main() -> int:
    warnings.filterwarnings("ignore")
    mode, workdir = sys.argv[1], sys.argv[2]
    with open(f"{workdir}/job.json") as fh:
        job = json.load(fh)
    _apply_limits(job.get("limits") or {})
    try:
        {"train": run_train, "predict": run_predict}[mode](workdir, job)
        return 0
    except Exception as exc:  # reported to the parent, which may hand it to the LLM
        _emit("error", message=f"{type(exc).__name__}: {exc}",
              traceback=traceback.format_exc()[-4000:])
        return 1


if __name__ == "__main__":
    sys.exit(main())
