import json
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

import geo_report_semantic_census_service as census  # noqa: E402

_HEADING_ID_RE = re.compile(r"^### cluster_id: (\S+)", re.MULTILINE)


def _mock_azure_client(response_fields: dict):
    client = MagicMock()

    def _create(*args, messages, **kwargs):
        user_content = messages[-1]["content"]
        ids = _HEADING_ID_RE.findall(user_content)
        results = [{"cluster_id": cid, **response_fields} for cid in ids]
        payload = {"results": results}
        return MagicMock(choices=[MagicMock(message=MagicMock(content=json.dumps(payload)))], usage=None)

    client.chat.completions.create.side_effect = _create
    return client


def test_semantic_census_item_schema_excludes_generative_fields():
    schema = census._semantic_census_item_schema("sc_classification")
    props = schema["properties"]
    for f in census.SEMANTIC_CENSUS_FIELDS_SC:
        assert f in props
        assert f"{f}_confidence" in props
    for f in census.GENERATIVE_FIELDS_SC:
        assert f not in props  # classification_reason等は含めない


def test_classify_semantic_census_batch_returns_field_and_confidence():
    sc_response = {
        "theme_relevance": "relevant", "theme_relevance_confidence": 0.9,
        "source_class": "Public Voice", "source_class_confidence": 0.8,
        "reaction_type": "Evaluation", "reaction_type_confidence": 0.7,
        "attitude": "positive", "attitude_confidence": 0.85,
        "cluster_consistency": "High", "cluster_consistency_confidence": 0.95,
        "apply_to_cluster": True, "apply_to_cluster_confidence": 0.99,
    }
    azure_client = _mock_azure_client(sc_response)
    clusters = [{"cluster_row": {"cluster_id": "SC_1", "signal_id": "S1"}, "representatives": []}]

    result = census.classify_semantic_census_batch(azure_client, "gpt-4o-mini", "sc_classification", clusters)
    items = result["data"]["results"]
    assert len(items) == 1
    preds, confs = census.extract_field_predictions("sc_classification", items[0])
    assert preds["theme_relevance"] == "relevant"
    assert confs["theme_relevance"] == 0.9
    assert preds["apply_to_cluster"] is True
    assert "classification_reason" not in preds


def test_field_classifier_logistic_regression_predicts_known_classes():
    X = np.array([[1.0, 0.0], [0.9, 0.1], [0.0, 1.0], [0.1, 0.9]])
    y = ["A", "A", "B", "B"]
    clf = census.FieldClassifier("logistic_regression").fit(X, y)
    preds = clf.predict_with_confidence(np.array([[0.95, 0.05], [0.05, 0.95]]))
    assert preds[0][0] == "A"
    assert preds[1][0] == "B"
    assert 0.0 <= preds[0][1] <= 1.0


def test_field_classifier_falls_back_to_constant_when_single_class():
    X = np.array([[1.0, 0.0], [0.9, 0.1]])
    y = ["A", "A"]
    clf = census.FieldClassifier("gradient_boosting").fit(X, y)
    preds = clf.predict_with_confidence(np.array([[0.5, 0.5]]))
    assert preds[0] == ("A", 0.0)


def test_train_and_predict_field_classifiers_end_to_end():
    task = "sc_classification"
    X = np.array([[1.0, 0.0], [0.9, 0.1], [0.0, 1.0], [0.1, 0.9]])
    labels_by_field = {f: (["relevant"] * 2 + ["irrelevant"] * 2) for f in census.SEMANTIC_CENSUS_FIELDS_SC}
    classifiers = census.train_field_classifiers("logistic_regression", task, X, labels_by_field)
    assert set(classifiers.keys()) == set(census.SEMANTIC_CENSUS_FIELDS_SC)

    results = census.predict_with_field_classifiers(classifiers, task, np.array([[0.95, 0.05]]))
    assert len(results) == 1
    preds, confs = results[0]
    assert preds["theme_relevance"] == "relevant"
    assert 0.0 <= confs["theme_relevance"] <= 1.0


def test_build_feature_matrix_concatenates_numeric_features_for_hybrid():
    centroid_by_cluster = {"SC_1": np.array([1.0, 2.0])}
    numeric = {"SC_1": {"fallback_share": 0.5, "source_entropy": 0.0}}
    X = census._build_feature_matrix(["SC_1"], centroid_by_cluster, numeric_features_by_cluster=numeric)
    assert X.shape == (1, 2 + len(census.HYBRID_NUMERIC_FEATURES))
    assert X[0, 0] == 1.0 and X[0, 1] == 2.0
