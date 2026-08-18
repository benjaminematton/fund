import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "eval: spends real money on real LLM turns — excluded from make test"
        " alongside @live; run with: make eval")
