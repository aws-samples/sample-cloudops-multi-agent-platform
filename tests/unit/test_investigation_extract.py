import json

from agents.shared.investigation_extract import (
    extract_investigation_references,
)


def _segment(value):
    return {"type": "tool", "value": json.dumps(value)}


def test_extracts_and_prefers_nested_kickoff_reference():
    investigation = json.dumps(
        {
            "found": True,
            "investigation": {
                "investigationId": "inv-nested",
                "requestTitle": "AWS Health: Route 53 issue",
            },
        }
    )
    segments = [
        _segment(
            {
                "name": "ops-excellence-agent",
                "output": "Completed investigation",
                "tool_trace": [
                    {
                        "tool_name": "health-events-agent",
                        "tool_trace": [
                            {
                                "tool_name": (
                                    "devops-agent___get_health_investigation"
                                ),
                                "output": investigation,
                            },
                            {
                                "tool_name": (
                                    "devops-agent___investigate_health_event"
                                ),
                                "output": investigation,
                            },
                        ],
                    }
                ],
            }
        )
    ]

    assert extract_investigation_references(segments) == [
        {
            "investigationId": "inv-nested",
            "title": "AWS Health: Route 53 issue",
            "toolName": "investigate_health_event",
        }
    ]


def test_ignores_investigation_metadata_from_health_listing():
    segments = [
        _segment(
            {
                "name": "health-events-agent",
                "tool_trace": [
                    {
                        "tool_name": "health-events___get_recent_events",
                        "output": json.dumps(
                            {
                                "events": [
                                    {
                                        "investigation": {
                                            "investigationId": "inv-listing"
                                        }
                                    }
                                ]
                            }
                        ),
                    }
                ],
            }
        )
    ]

    assert extract_investigation_references(segments) == []


def test_extracts_generic_operational_investigation():
    segments = [
        _segment(
            {
                "name": "cloudwatch-agent",
                "tool_trace": [
                    {
                        "tool_name": (
                            "devops-agent___investigate_operational_issue"
                        ),
                        "output": json.dumps(
                            {
                                "found": True,
                                "investigation": {
                                    "investigationId": "inv-alarm",
                                    "requestTitle": "Alarm: High API errors",
                                },
                            }
                        ),
                    }
                ],
            }
        )
    ]

    assert extract_investigation_references(segments) == [
        {
            "investigationId": "inv-alarm",
            "title": "Alarm: High API errors",
            "toolName": "investigate_operational_issue",
        }
    ]
