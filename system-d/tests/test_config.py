from dataclasses import FrozenInstanceError

import pytest

from system_d.config import Settings
from system_d.stopping import Config


def test_settings_defaults_and_stopping_config():
    settings = Settings.from_env({})
    assert settings.llm_model == "claude-sonnet-5-5"
    assert settings.simulator_model == settings.llm_model
    assert settings.embedding_model == "text-embedding-3-small"
    assert settings.embedding_dim == 1536
    assert settings.seed_batch == 30
    assert settings.next_batch == 20
    assert settings.target_half_width == 0.07
    assert settings.max_calls == 400
    assert settings.min_per_stratum == 5
    assert settings.fleet_concurrency == 50
    assert settings.llm_max_retries == 6
    assert settings.llm_backoff_base_s == 1.0
    assert settings.llm_backoff_max_s == 60
    assert settings.aggregate_similarity == 0.9
    assert settings.max_usd == 5
    assert settings.personas_per_call == 8
    assert settings.prices_usd_per_million["claude-sonnet-5-5"] == {"input": 2.0, "output": 10.0}
    assert settings.stopping_config() == Config(
        seed_batch=30,
        next_batch=20,
        target_half_width=0.07,
        max_calls=400,
        min_per_stratum=5,
    )
    with pytest.raises(FrozenInstanceError):
        settings.max_calls = 10


def test_settings_load_env_overrides_and_simulator_model_default():
    settings = Settings.from_env(
        {
            "DATABASE_URL": "test-database-url",
            "ANTHROPIC_API_KEY": "anthropic-placeholder",
            "OPENAI_API_KEY": "openai-placeholder",
            "LLM_MODEL": "custom-model",
            "SIMULATOR_MODEL": "sim-model",
            "EMBEDDING_DIM": "3",
            "SEED_BATCH": "12",
            "NEXT_BATCH": "8",
            "TARGET_HALF_WIDTH": "0.05",
            "MAX_CALLS": "120",
            "MIN_PER_STRATUM": "3",
            "FLEET_CONCURRENCY": "4",
            "LLM_MAX_RETRIES": "2",
            "LLM_BACKOFF_BASE_S": "0.25",
            "LLM_BACKOFF_MAX_S": "4",
            "AGGREGATE_SIMILARITY": "0.95",
            "MAX_USD": "2.5",
            "PERSONAS_PER_CALL": "3",
        }
    )
    assert settings.database_url == "test-database-url"
    assert settings.anthropic_api_key == "anthropic-placeholder"
    assert settings.openai_api_key == "openai-placeholder"
    assert settings.llm_model == "custom-model"
    assert settings.simulator_model == "sim-model"
    assert settings.embedding_dim == 3
    assert settings.seed_batch == 12
    assert settings.next_batch == 8
    assert settings.target_half_width == 0.05
    assert settings.max_calls == 120
    assert settings.min_per_stratum == 3
    assert settings.fleet_concurrency == 4
    assert settings.llm_max_retries == 2
    assert settings.llm_backoff_base_s == 0.25
    assert settings.llm_backoff_max_s == 4
    assert settings.aggregate_similarity == 0.95
    assert settings.max_usd == 2.5
    assert settings.personas_per_call == 3

    default_simulator = Settings.from_env({"LLM_MODEL": "chosen"})
    assert default_simulator.simulator_model == "chosen"
