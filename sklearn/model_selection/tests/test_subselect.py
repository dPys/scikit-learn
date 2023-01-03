import numpy as np
import pytest

from sklearn.utils._testing import ignore_warnings

from sklearn.datasets import make_classification
from sklearn.model_selection import GridSearchCV
from sklearn.model_selection import RandomizedSearchCV
from sklearn.model_selection import (
    Refitter,
    by_standard_error,
    by_percentile_rank,
    by_signed_rank,
    by_fixed_window,
    constrain,
)

from sklearn.svm import LinearSVC, SVC
from sklearn.decomposition import PCA
from sklearn.metrics import make_scorer
from sklearn.pipeline import Pipeline


@pytest.fixture(scope="function")
def grid_search_simulated():
    X, y = make_classification(n_samples=50, n_features=4, random_state=42)

    n_splits = 3
    params = [
        dict(
            kernel=[
                "rbf",
            ],
            C=[1, 10],
            gamma=[0.1, 1],
        ),
        dict(
            kernel=[
                "poly",
            ],
            degree=[1, 2],
        ),
    ]

    search = GridSearchCV(
        SVC(), cv=n_splits, param_grid=params, return_train_score=True
    )
    search.fit(X, y)

    cv_results = search.cv_results_

    yield {"cv_results": cv_results, "n_splits": n_splits}


@pytest.fixture(scope="function")
def generate_fit_params(grid_search_simulated):
    cv_results = grid_search_simulated["cv_results"]
    n_splits = grid_search_simulated["n_splits"]
    ss = Refitter(cv_results)

    yield {
        "score_grid": ss._score_grid,
        "n_folds": n_splits,
        "cv_means": ss._cv_means,
        "best_score_idx": ss._best_score_idx,
        "lowest_score_idx": ss._lowest_score_idx,
    }


def test_refitter_methods(grid_search_simulated):
    cv_results = grid_search_simulated["cv_results"]
    n_splits = grid_search_simulated["n_splits"]

    ss = Refitter(cv_results)

    # Test that the _get_splits method extracts the correct subgrid
    assert len(ss._get_splits()) == n_splits

    # Test that the _n_folds property returns the correct number of folds
    assert ss._n_folds == n_splits

    # Test that the _score_grid property returns the correct subgrid of scores
    assert ss._score_grid.shape == (6, n_splits)

    # Test that the _cv_means property returns the correct array of mean scores
    assert ss._cv_means.shape == (6,)

    # Test that the _lowest_score_idx property returns the correct index
    assert ss._lowest_score_idx == 5

    # Test that the _best_score_idx property returns the correct index
    assert ss._best_score_idx == 0

    assert ss._apply_thresh(0.93, 0.96) == 1

    # Test that the fit method returns the correct scores
    assert ss.fit(by_standard_error(sigma=1)) == (
        0.9243126424613448,
        0.9923540242053219,
    )

    # Test that the transform method returns the correct model
    assert ss.transform() == 1


def test_refitter_errors(grid_search_simulated):
    cv_results = grid_search_simulated["cv_results"]
    n_splits = grid_search_simulated["n_splits"]

    with pytest.raises(KeyError):
        ss = Refitter(cv_results, scoring="Not_a_scoring_metric")
        assert len(ss._get_splits()) == n_splits

    with pytest.raises(ValueError):
        ss = Refitter(cv_results, scoring="score")
        assert ss._apply_thresh(0.98, 0.99) == 1

    with pytest.raises(TypeError):
        ss = Refitter(cv_results, scoring="score")
        assert ss.fit("Not_a_rule") == (0.9243126424613448, 0.9923540242053219)

    with pytest.raises(ValueError):
        ss = Refitter(cv_results, scoring="score")
        assert ss.transform() == 1

    del cv_results["params"]
    ss = Refitter(cv_results)
    with pytest.raises(TypeError):
        assert len(ss._get_splits()) == n_splits


@ignore_warnings
@pytest.mark.parametrize(
    "param",
    [
        "reduce_dim__n_components",
        None,
        pytest.mark.xfail("Not_a_param"),
    ],
)
@pytest.mark.parametrize(
    "scoring",
    [
        "roc_auc",
        "neg_log_loss",
        "neg_mean_squared_log_error",
        ["roc_auc", "neg_mean_squared_log_error"],
        pytest.mark.xfail("Not_a_scoring_metric"),
    ],
)
@pytest.mark.parametrize(
    "rule",
    [
        by_standard_error(sigma=1),
        by_signed_rank(alpha=0.01),
        by_percentile_rank(eta=0.68),
        by_fixed_window(min_cut=0.80, max_cut=0.91),
        pytest.mark.xfail("Not_a_rule"),
    ],
)
@pytest.mark.parametrize(
    "search_cv",
    [GridSearchCV, RandomizedSearchCV],
)
def test_constrain(param, scoring, rule, search_cv):
    """
    A function tests `refit=callable` interface where the callable is the `simplify`
    method of the `Refitter` refit class that returnsthe most parsimonious,
    highest-performing model.
    """

    X, y = make_classification(n_samples=350, n_features=16, random_state=42)

    # Instantiate a pipeline with parameter grid representing different levels of
    # complexity
    clf = LinearSVC(random_state=42)
    if param == "reduce_dim__n_components":
        param_grid = {"reduce_dim__n_components": [4, 8, 12]}
        pipe = Pipeline([("reduce_dim", PCA(random_state=42)), ("classify", clf)])
    else:
        param_grid = {"classify__C": [0.1, 1], "reduce_dim__n_components": [4, 8, 12]}
        pipe = Pipeline(
            [("reduce_dim", PCA(random_state=42)), ("classify", SVC(random_state=42))]
        )

    scoring = make_scorer(scoring, greater_is_better=True)

    # Instantiate a refitted grid search object
    grid_simplified = search_cv(
        pipe,
        param_grid,
        scoring=scoring,
        refit=constrain(rule, scoring=scoring),
    )

    # Instantiate a non-refitted grid search object for comparison
    grid = search_cv(pipe, param_grid, scoring=scoring, n_jobs=-1)
    grid.fit(X, y)

    # If the cv results were not all NaN, then we can test the refit callable
    if not np.isnan(grid.fit(X, y).cv_results_["split0_test_score"]).all():
        grid_simplified.fit(X, y)
        simplified_best_score_ = grid_simplified.cv_results_["mean_test_score"][
            grid_simplified.best_index_
        ]
        # Ensure that if the refit callable subselected a lower scoring model,
        # it was because it was only because it was a simpler model.
        if abs(grid.best_score_) > abs(simplified_best_score_):
            if param:
                assert grid.best_params_[param] > grid_simplified.best_params_[param]


def test_by_standard_error(generate_fit_params):
    # Test that the by_standard_error function returns the correct rule
    assert pytest.approx(
        by_standard_error(sigma=1.5).__call__(**generate_fit_params), rel=1e-2
    ) == (0.9243126424613448, 0.9923540242053219)

    # Test that the by_standard_error function raises a ValueError
    with pytest.raises(ValueError):
        by_standard_error(sigma=-1)


def test_by_signed_rank(generate_fit_params):
    # Test that the by_signed_rank function returns the correct rule
    assert pytest.approx(
        by_signed_rank(alpha=0.01).__call__(**generate_fit_params), rel=1e-2
    ) == (0.9583333333333334, 0.9583333333333334)

    # Test that the by_signed_rank function raises a ValueError
    with pytest.raises(ValueError):
        by_signed_rank(alpha=-1)


def test_by_percentile_rank(generate_fit_params):
    # Test that the by_percentile_rank function returns the correct rule
    assert pytest.approx(
        by_percentile_rank(eta=0.68).__call__(**generate_fit_params), rel=1e-2
    ) == (0.955, 1.0)

    # Test that the by_percentile_rank function raises a ValueError
    with pytest.raises(ValueError):
        by_percentile_rank(eta=-1)


def test_by_fixed_window(generate_fit_params):
    # Test that the by_fixed_window function returns the correct rule
    assert by_fixed_window(min_cut=0.80, max_cut=0.91).__call__(
        **generate_fit_params
    ) == (0.8, 0.91)

    # Test that the by_fixed_window function raises a ValueError
    with pytest.raises(ValueError):
        by_fixed_window(min_cut=0.99, max_cut=0.92)
