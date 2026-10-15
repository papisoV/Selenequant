# Ported from AlphaPurify (MIT)
"""Cross-sectional factor neutralization toolbox, ported from AlphaPurify.

15 registered methods (13 sklearn bridge + multiOLS pure-numpy + partialcorrelation
pure-scipy) plus 3 legacy methods migrated from Selene's legacy neutralization code.
Note: ``ransac_neutralize`` is the RANSAC regression method (registry key ``rank`` in
AlphaPurify); the legacy ``rank_neutralize`` is true cross-sectional rank
standardization. Both coexist with separated semantics.
"""

import numpy as np
import pandas as pd
from scipy.stats import pearsonr, spearmanr
from sklearn.decomposition import FastICA, PCA
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from sklearn.kernel_ridge import KernelRidge
from sklearn.linear_model import (
    BayesianRidge,
    ElasticNet,
    HuberRegressor,
    Lasso,
    LinearRegression,
    RANSACRegressor,
    Ridge,
    TheilSenRegressor,
)
from sklearn.preprocessing import PolynomialFeatures

__all__ = [
    "multiOLS_neutralize",
    "lasso_neutralize",
    "ridge_neutralize",
    "elasticnet_neutralize",
    "polynomial_neutralize",
    "kernelridge_neutralize",
    "huber_neutralize",
    "ransac_neutralize",
    "theilsen_neutralize",
    "randomforest_neutralize",
    "GBDT_neutralize",
    "PCA_neutralize",
    "ICA_neutralize",
    "bayesianridge_neutralize",
    "partialcorrelation_neutralize",
    "rank_neutralize",
    "cluster_neutralize",
    "apply_ind_neutralize",
    "_demean_by_group",
]


def _preprocess_for_neutralization(
    df: pd.DataFrame,
    factor_col: str,
    neutralizer_cols: list[str],
    dummy_cols: list[str] | None = None,
    scale_X: bool = False,
) -> tuple[pd.DataFrame, list[str]]:
    """Clean inputs for cross-sectional neutralization.

    Optionally z-score standardize neutralizers globally (sample std, ddof=1,
    matching the AlphaPurify polars ``pl.col(c).std()`` default), generate
    drop-first dummies, drop rows with nulls, and coerce regressors/factor to
    numeric. Returns (cleaned_df, X_cols) with the same shape as AlphaPurify's
    source.
    """
    dummy_cols = dummy_cols or []

    df = df.copy()

    if scale_X:
        for c in neutralizer_cols:
            std = df[c].std(ddof=1)
            mean = df[c].mean()
            if std == 0:
                df[c] = df[c] - mean
            else:
                df[c] = (df[c] - mean) / std

    if dummy_cols:
        df_dummy = pd.get_dummies(df[dummy_cols], drop_first=True, dtype=float)
        df = pd.concat([df, df_dummy], axis=1)
        dummy_cols_final = df_dummy.columns.to_list()
    else:
        dummy_cols_final = []

    X_cols = neutralizer_cols + dummy_cols_final
    df_pd = df.dropna(subset=[factor_col] + X_cols).copy()
    for col in X_cols:
        df_pd[col] = pd.to_numeric(df_pd[col], errors="coerce")
    df_pd[factor_col] = pd.to_numeric(df_pd[factor_col], errors="coerce")

    return df_pd, X_cols


def multiOLS_neutralize(
    df: pd.DataFrame,
    trade_date_col: str,
    factor_col: str,
    neutralizer_cols: list[str],
    dummy_cols: list[str] | None = None,
    scale_X: bool = False,
) -> pd.DataFrame:
    """Cross-sectional multi-factor OLS neutralization via pseudoinverse."""
    df_pd, X_cols = _preprocess_for_neutralization(
        df, factor_col, neutralizer_cols, dummy_cols, scale_X
    )

    def _neutralize_group(g: pd.DataFrame) -> pd.DataFrame:
        y = g[factor_col].to_numpy()

        if X_cols:
            X = g[X_cols].to_numpy()
            X = np.column_stack([np.ones(len(g)), X])
        else:
            X = np.ones((len(g), 1))

        beta = np.linalg.lstsq(X, y, rcond=None)[0]
        y_pred = X @ beta

        g[factor_col] = y - y_pred

        return g

    groups = [_neutralize_group(g) for _, g in df_pd.groupby(trade_date_col, sort=False)]

    return pd.concat(groups, ignore_index=True)


def lasso_neutralize(
    df: pd.DataFrame,
    trade_date_col: str,
    factor_col: str,
    neutralizer_cols: list[str],
    dummy_cols: list[str] | None = None,
    scale_X: bool = False,
    alpha: float = 0.01,
    fit_intercept: bool = True,
    max_iter: int = 1000,
    tol: float = 0.0001,
    warm_start: bool = False,
    random_state: int | None = None,
) -> pd.DataFrame:
    """Cross-sectional factor neutralization using Lasso regression."""
    df_pd, X_cols = _preprocess_for_neutralization(
        df, factor_col, neutralizer_cols, dummy_cols, scale_X
    )
    model = Lasso(
        alpha=alpha,
        fit_intercept=fit_intercept,
        max_iter=max_iter,
        tol=tol,
        warm_start=warm_start,
        random_state=random_state,
    )

    def _neutralize_group(group: pd.DataFrame) -> pd.DataFrame:
        X = group[X_cols].values
        y = group[factor_col].values

        if X.shape[1] > 0:
            model.fit(X, y)
            y_pred = model.predict(X)
            group[factor_col] = y - y_pred
        return group

    groups = [_neutralize_group(g) for _, g in df_pd.groupby(trade_date_col, sort=False)]

    return pd.concat(groups, ignore_index=True)


def ridge_neutralize(
    df: pd.DataFrame,
    trade_date_col: str,
    factor_col: str,
    neutralizer_cols: list[str],
    dummy_cols: list[str] | None = None,
    scale_X: bool = False,
    alpha: float = 1.0,
    fit_intercept: bool = True,
    max_iter: int = 1000,
    tol: float = 0.0001,
    random_state: int | None = None,
) -> pd.DataFrame:
    """Cross-sectional factor neutralization using Ridge regression."""
    df_pd, X_cols = _preprocess_for_neutralization(
        df, factor_col, neutralizer_cols, dummy_cols, scale_X
    )
    reg = Ridge(
        alpha=alpha,
        fit_intercept=fit_intercept,
        max_iter=max_iter,
        tol=tol,
        random_state=random_state,
    )

    def _neutralize_group(group: pd.DataFrame) -> pd.DataFrame:
        X = group[X_cols].values
        y = group[factor_col].values

        if X.shape[1] > 0:
            reg.fit(X, y)
            y_pred = reg.predict(X)
            group[factor_col] = y - y_pred
        return group

    groups = [_neutralize_group(g) for _, g in df_pd.groupby(trade_date_col, sort=False)]

    return pd.concat(groups, ignore_index=True)


def elasticnet_neutralize(
    df: pd.DataFrame,
    trade_date_col: str,
    factor_col: str,
    neutralizer_cols: list[str],
    dummy_cols: list[str] | None = None,
    scale_X: bool = False,
    alpha: float = 1.0,
    l1_ratio: float = 0.5,
    fit_intercept: bool = True,
    max_iter: int = 300,
    tol: float = 0.0001,
    warm_start: bool = False,
    random_state: int | None = None,
) -> pd.DataFrame:
    """Cross-sectional factor neutralization using Elastic Net regression."""
    df_pd, X_cols = _preprocess_for_neutralization(
        df, factor_col, neutralizer_cols, dummy_cols, scale_X
    )
    model = ElasticNet(
        alpha=alpha,
        l1_ratio=l1_ratio,
        fit_intercept=fit_intercept,
        max_iter=max_iter,
        tol=tol,
        warm_start=warm_start,
        random_state=random_state,
    )

    def _neutralize_group(group: pd.DataFrame) -> pd.DataFrame:
        X = group[X_cols].values
        y = group[factor_col].values
        if X.shape[1] > 0:
            model.fit(X, y)
            y_pred = model.predict(X)
            group[factor_col] = y - y_pred
        return group

    groups = [_neutralize_group(g) for _, g in df_pd.groupby(trade_date_col, sort=False)]

    return pd.concat(groups, ignore_index=True)


def polynomial_neutralize(
    df: pd.DataFrame,
    trade_date_col: str,
    factor_col: str,
    neutralizer_cols: list[str],
    dummy_cols: list[str] | None = None,
    scale_X: bool = False,
    degree: int = 2,
    interaction_only: bool = False,
    include_bias: bool = True,
) -> pd.DataFrame:
    """Cross-sectional factor neutralization using polynomial projection."""
    df_pd, X_cols = _preprocess_for_neutralization(
        df, factor_col, neutralizer_cols, dummy_cols, scale_X
    )

    poly = PolynomialFeatures(
        degree=degree,
        interaction_only=interaction_only,
        include_bias=include_bias,
    )

    def _neutralize_group(group: pd.DataFrame) -> pd.DataFrame:
        y = group[factor_col].values
        X = group[X_cols].values

        if X.shape[1] > 0:
            X_poly = poly.fit_transform(X)

            XtX_inv = np.linalg.pinv(X_poly.T @ X_poly)
            P = X_poly @ XtX_inv @ X_poly.T
            y_pred = P @ y
            group[factor_col] = y - y_pred

            return group

    groups = [_neutralize_group(g) for _, g in df_pd.groupby(trade_date_col, sort=False)]

    return pd.concat(groups, ignore_index=True)


def kernelridge_neutralize(
    df: pd.DataFrame,
    trade_date_col: str,
    factor_col: str,
    neutralizer_cols: list[str],
    dummy_cols: list[str] | None = None,
    scale_X: bool = False,
    kernel: str = "rbf",
    alpha: float = 1.0,
    gamma: float | None = None,
    degree: int = 3,
    coef0: float = 1.0,
) -> pd.DataFrame:
    """Cross-sectional factor neutralization using Kernel Ridge regression."""
    df_pd, X_cols = _preprocess_for_neutralization(
        df, factor_col, neutralizer_cols, dummy_cols, scale_X
    )
    model = KernelRidge(
        kernel=kernel,
        alpha=alpha,
        gamma=gamma,
        degree=degree,
        coef0=coef0,
    )

    def _neutralize_group(group: pd.DataFrame) -> pd.DataFrame:
        X = group[X_cols].values
        y = group[factor_col].values

        if X.shape[1] > 0:
            model.fit(X, y)
            y_pred = model.predict(X)

            group[factor_col] = y - y_pred
            return group

    groups = [_neutralize_group(g) for _, g in df_pd.groupby(trade_date_col, sort=False)]

    return pd.concat(groups, ignore_index=True)


def huber_neutralize(
    df: pd.DataFrame,
    trade_date_col: str,
    factor_col: str,
    neutralizer_cols: list[str],
    dummy_cols: list[str] | None = None,
    scale_X: bool = True,
    epsilon: float = 1.35,
    max_iter: int = 100,
    alpha: float = 0.0,
    warm_start: bool = False,
    fit_intercept: bool = True,
    tol: float = 0.00001,
) -> pd.DataFrame:
    """Robust cross-sectional factor neutralization using Huber regression."""
    df_pd, X_cols = _preprocess_for_neutralization(
        df, factor_col, neutralizer_cols, dummy_cols, scale_X
    )
    model = HuberRegressor(
        epsilon=epsilon,
        alpha=alpha,
        max_iter=max_iter,
        warm_start=warm_start,
        fit_intercept=fit_intercept,
        tol=tol,
    )

    def _neutralize_group(group: pd.DataFrame) -> pd.DataFrame:
        X = group[X_cols].values
        y = group[factor_col].values

        if X.shape[1] > 0:
            model.fit(X, y)
            y_pred = model.predict(X)

            group[factor_col] = y - y_pred
            return group

    groups = [_neutralize_group(g) for _, g in df_pd.groupby(trade_date_col, sort=False)]

    return pd.concat(groups, ignore_index=True)


def ransac_neutralize(
    df: pd.DataFrame,
    trade_date_col: str,
    factor_col: str,
    neutralizer_cols: list[str],
    dummy_cols: list[str] | None = None,
    scale_X: bool = False,
    residual_threshold: float = 2.5,
    max_trials: int = 100,
    min_samples: float = 0.5,
    random_state: int | None = None,
) -> pd.DataFrame:
    """Robust cross-sectional factor neutralization using RANSAC regression.

    Ported from AlphaPurify's ``rank_neutralize`` (registry key ``rank``), which is
    RANSAC regression despite its name; renamed here for clarity.
    """
    df_pd, X_cols = _preprocess_for_neutralization(
        df, factor_col, neutralizer_cols, dummy_cols, scale_X
    )
    estimator = LinearRegression(fit_intercept=True)
    ransac = RANSACRegressor(
        estimator=estimator,
        residual_threshold=residual_threshold,
        max_trials=max_trials,
        min_samples=min_samples,
        random_state=random_state,
    )

    def _neutralize_group(group: pd.DataFrame) -> pd.DataFrame:
        X = group[X_cols].values
        y = group[factor_col].values
        if X.shape[1] > 0:
            ransac.fit(X, y)
            y_pred = ransac.predict(X)
            group[factor_col] = y - y_pred
        return group

    groups = [_neutralize_group(g) for _, g in df_pd.groupby(trade_date_col, sort=False)]

    return pd.concat(groups, ignore_index=True)


def theilsen_neutralize(
    df: pd.DataFrame,
    trade_date_col: str,
    factor_col: str,
    neutralizer_cols: list[str],
    dummy_cols: list[str] | None = None,
    scale_X: bool = False,
    fit_intercept: bool = True,
    max_subpopulation: int | float = 1000,
    n_subsamples: int | None = None,
    max_iter: int = 300,
    tol: float = 0.001,
    random_state: int | None = None,
) -> pd.DataFrame:
    """Robust cross-sectional factor neutralization using Theil-Sen regression."""
    df_pd, X_cols = _preprocess_for_neutralization(
        df, factor_col, neutralizer_cols, dummy_cols, scale_X
    )
    model = TheilSenRegressor(
        fit_intercept=fit_intercept,
        max_subpopulation=max_subpopulation,
        n_subsamples=n_subsamples,
        max_iter=max_iter,
        tol=tol,
        random_state=random_state,
    )

    def _neutralize_group(group: pd.DataFrame) -> pd.DataFrame:
        X = group[X_cols].values
        y = group[factor_col].values

        if X.shape[1] > 0:
            model.fit(X, y)
            y_pred = model.predict(X)
            group[factor_col] = y - y_pred
        return group

    groups = [_neutralize_group(g) for _, g in df_pd.groupby(trade_date_col, sort=False)]

    return pd.concat(groups, ignore_index=True)


def randomforest_neutralize(
    df: pd.DataFrame,
    trade_date_col: str,
    factor_col: str,
    neutralizer_cols: list[str],
    dummy_cols: list[str] | None = None,
    scale_X: bool = False,
    n_estimators: int = 50,
    max_depth: int | None = None,
    min_samples_split: float = 2,
    min_samples_leaf: float = 1,
    min_weight_fraction_leaf: float = 0,
    max_features: float = 1,
    max_leaf_nodes: int | None = None,
    min_impurity_decrease: float = 0,
    bootstrap: bool = True,
    oob_score: bool = False,
    random_state: int | None = None,
    warm_start: bool = False,
    ccp_alpha: float = 0,
    max_samples: float | None = None,
) -> pd.DataFrame:
    """Nonlinear cross-sectional factor neutralization using Random Forest."""
    df_pd, X_cols = _preprocess_for_neutralization(
        df, factor_col, neutralizer_cols, dummy_cols, scale_X
    )
    rf = RandomForestRegressor(
        n_estimators=n_estimators,
        max_depth=max_depth,
        min_samples_leaf=min_samples_leaf,
        random_state=random_state,
        n_jobs=1,
        min_samples_split=min_samples_split,
        min_weight_fraction_leaf=min_weight_fraction_leaf,
        max_features=max_features,
        max_leaf_nodes=max_leaf_nodes,
        min_impurity_decrease=min_impurity_decrease,
        bootstrap=bootstrap,
        oob_score=oob_score,
        warm_start=warm_start,
        ccp_alpha=ccp_alpha,
        max_samples=max_samples,
    )

    def _neutralize_group(group: pd.DataFrame) -> pd.DataFrame:
        X = group[X_cols].values
        y = group[factor_col].values

        if X.shape[1] > 0:
            rf.fit(X, y)
            y_pred = rf.predict(X)
            group[factor_col] = y - y_pred
        return group

    groups = [_neutralize_group(g) for _, g in df_pd.groupby(trade_date_col, sort=False)]

    return pd.concat(groups, ignore_index=True)


def GBDT_neutralize(
    df: pd.DataFrame,
    trade_date_col: str,
    factor_col: str,
    neutralizer_cols: list[str],
    dummy_cols: list[str] | None = None,
    scale_X: bool = False,
    learning_rate: float = 0.1,
    n_estimators: int = 50,
    subsample: float = 1,
    min_samples_split: int = 2,
    min_samples_leaf: int = 1,
    min_weight_fraction_leaf: float = 0,
    max_depth: int = 3,
    min_impurity_decrease: float = 0,
    init: object | None = None,
    random_state: int | None = None,
    alpha: float = 0.9,
    max_leaf_nodes: int | None = None,
    warm_start: bool = False,
    validation_fraction: float = 0.1,
    n_iter_no_change: int | None = None,
    tol: float = 0.0001,
    ccp_alpha: float = 0,
) -> pd.DataFrame:
    """Nonlinear cross-sectional factor neutralization using Gradient Boosting."""
    df_pd, X_cols = _preprocess_for_neutralization(
        df, factor_col, neutralizer_cols, dummy_cols, scale_X
    )
    model = GradientBoostingRegressor(
        learning_rate=learning_rate,
        n_estimators=n_estimators,
        subsample=subsample,
        min_samples_split=min_samples_split,
        min_samples_leaf=min_samples_leaf,
        min_weight_fraction_leaf=min_weight_fraction_leaf,
        max_depth=max_depth,
        min_impurity_decrease=min_impurity_decrease,
        init=init,
        random_state=random_state,
        alpha=alpha,
        max_leaf_nodes=max_leaf_nodes,
        warm_start=warm_start,
        validation_fraction=validation_fraction,
        n_iter_no_change=n_iter_no_change,
        tol=tol,
        ccp_alpha=ccp_alpha,
    )

    def _neutralize_group(group: pd.DataFrame) -> pd.DataFrame:
        X = group[X_cols].values
        y = group[factor_col].values

        if X.shape[1] > 0:
            model.fit(X, y)
            y_pred = model.predict(X)
            group[factor_col] = y - y_pred
        return group

    groups = [_neutralize_group(g) for _, g in df_pd.groupby(trade_date_col, sort=False)]

    return pd.concat(groups, ignore_index=True)


def PCA_neutralize(
    df: pd.DataFrame,
    trade_date_col: str,
    factor_col: str,
    neutralizer_cols: list[str],
    dummy_cols: list[str] | None = None,
    scale_X: bool = True,
    n_components: int | None = None,
    tol: float = 0,
    n_oversamples: int = 10,
    random_state: int | None = None,
) -> pd.DataFrame:
    """Cross-sectional factor neutralization via PCA component regression."""
    df_pd, X_cols = _preprocess_for_neutralization(
        df, factor_col, neutralizer_cols, dummy_cols, scale_X
    )

    def _neutralize_group(group: pd.DataFrame) -> pd.DataFrame:
        X = group[X_cols].values
        y = group[factor_col].values

        if X.shape[1] > 0:
            n_comp = n_components or min(10, X.shape[1], X.shape[0])
            pca = PCA(
                n_components=n_comp,
                tol=tol,
                n_oversamples=n_oversamples,
                random_state=random_state,
            )
            comps = pca.fit_transform(X)
            beta = np.linalg.lstsq(comps, y, rcond=None)[0]
            y_pred = comps @ beta

            group[factor_col] = y - y_pred
            return group

    groups = [_neutralize_group(g) for _, g in df_pd.groupby(trade_date_col, sort=False)]

    return pd.concat(groups, ignore_index=True)


def ICA_neutralize(
    df: pd.DataFrame,
    trade_date_col: str,
    factor_col: str,
    neutralizer_cols: list[str],
    dummy_cols: list[str] | None = None,
    scale_X: bool = False,
    n_components: int | None = None,
    max_iter: int = 200,
    tol: float = 0.0001,
    random_state: int | None = None,
    return_components: bool = False,
) -> pd.DataFrame:
    """Cross-sectional factor neutralization via ICA component regression."""
    df_pd, X_cols = _preprocess_for_neutralization(
        df, factor_col, neutralizer_cols, dummy_cols, scale_X
    )

    def _neutralize_group(group: pd.DataFrame) -> pd.DataFrame:
        X = group[X_cols].values
        y = group[factor_col].values

        n_comp = n_components or min(X.shape[1], X.shape[0])
        if X.shape[1] > 0:
            ica = FastICA(
                n_components=n_comp,
                max_iter=max_iter,
                tol=tol,
                random_state=random_state,
            )
            S = ica.fit_transform(X)

            X_ica = np.column_stack([np.ones(len(S)), S])
            beta = np.linalg.pinv(X_ica.T @ X_ica) @ X_ica.T @ y
            y_pred = X_ica @ beta
            resid = y - y_pred
            group[factor_col] = resid

        if return_components:
            for i in range(S.shape[1]):
                group[f"ICA_{i + 1}"] = S[:, i]
            return group
        else:
            return group

    groups = [_neutralize_group(g) for _, g in df_pd.groupby(trade_date_col, sort=False)]

    return pd.concat(groups, ignore_index=True)


def bayesianridge_neutralize(
    df: pd.DataFrame,
    trade_date_col: str,
    factor_col: str,
    neutralizer_cols: list[str],
    dummy_cols: list[str] | None = None,
    scale_X: bool = False,
    n_iter: int = 300,
    tol: float = 0.001,
    alpha_1: float = 1e-6,
    alpha_2: float = 1e-6,
    lambda_1: float = 1e-6,
    lambda_2: float = 1e-6,
    alpha_init: float | None = None,
    lambda_init: float | None = None,
    fit_intercept: bool = True,
    compute_score: bool = False,
    return_coef: bool = False,
) -> pd.DataFrame:
    """Cross-sectional factor neutralization using Bayesian Ridge regression."""
    df_pd, X_cols = _preprocess_for_neutralization(
        df, factor_col, neutralizer_cols, dummy_cols, scale_X
    )
    model = BayesianRidge(
        max_iter=n_iter,
        tol=tol,
        alpha_1=alpha_1,
        alpha_2=alpha_2,
        lambda_1=lambda_1,
        lambda_2=lambda_2,
        alpha_init=alpha_init,
        lambda_init=lambda_init,
        fit_intercept=fit_intercept,
        compute_score=compute_score,
    )

    def _neutralize_group(group: pd.DataFrame) -> pd.DataFrame:
        X = group[X_cols].values
        y = group[factor_col].values

        if X.shape[1] > 0:
            model.fit(X, y)
            y_pred = model.predict(X)
            group[factor_col] = y - y_pred

        if return_coef:
            group["coef_mean"] = np.mean(model.coef_)
            return group
        else:
            return group

    groups = [_neutralize_group(g) for _, g in df_pd.groupby(trade_date_col, sort=False)]

    return pd.concat(groups, ignore_index=True)


def partialcorrelation_neutralize(
    df: pd.DataFrame,
    trade_date_col: str,
    factor_col: str,
    neutralizer_cols: list[str],
    dummy_cols: list[str] | None = None,
    scale_X: bool = False,
    target_col: str | None = None,
    method: str = "pearson",
    return_type: str = "neutralized_factor",
) -> pd.DataFrame:
    """Cross-sectional factor neutralization using partial correlation residuals."""
    df_pd, X_cols = _preprocess_for_neutralization(
        df, factor_col, neutralizer_cols, dummy_cols, scale_X
    )

    def _partial_corr(x: np.ndarray, y: np.ndarray) -> float:
        if np.std(x) == 0 or np.std(y) == 0:
            return np.nan
        if method == "spearman":
            return float(spearmanr(x, y)[0])
        else:
            return float(pearsonr(x, y)[0])

    def _neutralize_group(group: pd.DataFrame) -> pd.DataFrame:
        X = group[X_cols].values
        y = group[factor_col].values

        if X.shape[1] > 0:
            P = X @ np.linalg.pinv(X.T @ X) @ X.T
            y_resid = y - P @ y
        else:
            y_resid = y.copy()

        if target_col is None or return_type == "neutralized_factor":
            group[factor_col] = y_resid
            return group

        else:
            r = group[target_col].values
            if X.shape[1] > 0:
                r_resid = r - P @ r
            else:
                r_resid = r.copy()
            pcorr = _partial_corr(y_resid, r_resid)
            return pd.DataFrame({trade_date_col: [group[trade_date_col].iloc[0]], factor_col: [pcorr]})

    groups = [_neutralize_group(g) for _, g in df_pd.groupby(trade_date_col, sort=False)]

    return pd.concat(groups, ignore_index=True)


# --- Legacy methods migrated from Selene legacy neutralize module ---


def _demean_by_group(series: pd.Series, groups: pd.Series) -> pd.Series:
    """Subtract group means from a Series.

    Groups with a single member produce 0.0 (no within-group variance).
    """
    group_means = series.groupby(groups, sort=False).transform("mean")
    return series - group_means


def rank_neutralize(series: pd.Series) -> pd.Series:
    """Cross-sectional rank neutralization: (rank - mean) / std(rank).

    When all values are identical (std == 0), returns zeros.
    """
    ranked = series.rank(method="average")
    mean = ranked.mean()
    std = ranked.std()
    if std < 1e-12:
        return pd.Series(0.0, index=series.index)
    return (ranked - mean) / std


def _kmeans_labels(
    df: pd.DataFrame,
    n_clusters: int,
    features: list[str] | None,
) -> pd.Series:
    """Produce KMeans cluster labels, with numpy fallback."""
    cols = features if features is not None else list(df.columns)
    X = df[cols].fillna(0.0).values

    n_samples = X.shape[0]
    if n_samples == 0:
        return pd.Series(dtype=int, index=df.index)
    effective_k = min(n_clusters, n_samples)

    try:
        from sklearn.cluster import KMeans

        km = KMeans(n_clusters=effective_k, n_init=10, random_state=42)
        labels = km.fit_predict(X)
    except ImportError:
        labels = _numpy_kmeans(X, effective_k)

    return pd.Series(labels, index=df.index, name="cluster")


def _numpy_kmeans(X: np.ndarray, k: int, max_iter: int = 50) -> np.ndarray:
    """Minimal KMeans using numpy (fallback when sklearn is unavailable)."""
    n_samples = X.shape[0]
    if n_samples == 0:
        return np.array([], dtype=int)
    if k <= 1:
        return np.zeros(n_samples, dtype=int)

    rng = np.random.RandomState(42)
    indices = rng.choice(n_samples, size=k, replace=False)
    centers = X[indices].copy()

    labels = np.zeros(n_samples, dtype=int)
    for _ in range(max_iter):
        dists = np.linalg.norm(X[:, np.newaxis, :] - centers[np.newaxis, :, :], axis=2)
        new_labels = np.argmin(dists, axis=1)
        if np.array_equal(new_labels, labels):
            break
        labels = new_labels
        for c in range(k):
            mask = labels == c
            if mask.any():
                centers[c] = X[mask].mean(axis=0)

    return labels


def cluster_neutralize(
    df: pd.DataFrame,
    n_clusters: int = 5,
    features: list[str] | None = None,
) -> pd.DataFrame:
    """KMeans cluster neutralization: demean each column within its cluster."""
    if df.empty:
        return df.copy()

    clusters = _kmeans_labels(df, n_clusters, features)
    result = df.copy()
    for col in df.columns:
        result[col] = _demean_by_group(df[col], clusters)
    return result


def apply_ind_neutralize(
    factor_df: pd.DataFrame,
    group_series: pd.Series | None = None,
    n_clusters: int = 5,
) -> pd.DataFrame:
    """Unified industry-neutralization entry point.

    Uses explicit group labels if provided; otherwise falls back to KMeans
    clustering on the factor columns.
    """
    if factor_df.empty:
        return factor_df.copy()

    if group_series is not None:
        result = factor_df.copy()
        for col in factor_df.columns:
            result[col] = _demean_by_group(factor_df[col], group_series)
        return result

    return cluster_neutralize(factor_df, n_clusters=n_clusters)
