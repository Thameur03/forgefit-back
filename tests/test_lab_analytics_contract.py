from services.analytics_events import (
    CANONICAL_EVENT_NAMES,
    FAILURE_EVENT_NAMES,
    canonical_event_name,
    sanitize_event_properties,
)


def test_v2_structural_events_are_canonical_and_privacy_bounded():
    expected = {
        "lab_insights_viewed",
        "insight_refreshed",
        "insight_impression",
        "insight_opened",
        "evidence_expanded",
        "action_opened",
        "generation_failed",
    }
    assert expected.issubset(CANONICAL_EVENT_NAMES)
    assert "generation_failed" in FAILURE_EVENT_NAMES

    properties = sanitize_event_properties(
        "insight_impression",
        {
            "detector_id": "training_frequency",
            "lifecycle": "Ongoing",
            "confidence": "High",
            "raw_workout_data": {"weight": 100},
            "advice": "private generated prose",
        },
    )
    assert properties == {
        "detector_id": "training_frequency",
        "lifecycle": "Ongoing",
        "confidence": "High",
    }


def test_legacy_fetch_and_failure_names_map_to_correct_v2_semantics():
    assert canonical_event_name("lab_insights_loaded") == "insight_refreshed"
    assert canonical_event_name("lab_insights_failed") == "generation_failed"
