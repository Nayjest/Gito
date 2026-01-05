import os
import pytest

@pytest.fixture(autouse=True)
def mock_env_vars():
    """Set dummy environment variables for tests."""
    # Set a dummy API key to pass microcore configuration validation
    os.environ["LLM_API_KEY"] = "dummy_key"
    # Ensure we don't actually try to call any API
    os.environ["LLM_API_TYPE"] = "openai" 
