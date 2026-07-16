"""Tests for pi_sms.core.config."""

import pytest
from pydantic import ValidationError

from pi_sms.core.config import Config, validate_config


def _base_data() -> dict:
    return {
        "trello": {"key": "k", "token": "t", "list_id": "l"},
    }


def test_config_uses_defaults_when_optional_sections_missing() -> None:
    config = validate_config(_base_data())

    assert config.modem.base_url == "http://192.168.8.1"
    assert config.poll.interval_seconds == 30
    assert config.filter.exclude_patterns == []
    assert config.debug is None


def test_config_requires_trello_section() -> None:
    with pytest.raises(ValidationError):
        Config.model_validate({})


def test_config_accepts_full_example() -> None:
    data = {
        "modem": {"base_url": "http://192.168.8.1", "request_timeout_seconds": 5},
        "poll": {"interval_seconds": 15},
        "filter": {"exclude_patterns": ['^Messagerie "666" Free:']},
        "trello": {
            "key": "k",
            "token": "t",
            "list_id": "l",
            "card_name_template": "SMS from {phone}",
            "card_desc_template": "{content}",
        },
        "debug": {"poll_interval_seconds": 2},
    }

    config = validate_config(data)

    assert config.poll.interval_seconds == 15
    assert config.filter.exclude_patterns == ['^Messagerie "666" Free:']
    assert config.debug is not None
    assert config.debug.poll_interval_seconds == 2
