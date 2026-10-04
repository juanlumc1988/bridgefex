# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import os
from pathlib import Path

import pytest

from bridgefex import libclang
from bridgefex.errors import LibclangError
from tests.support import toolchain as toolchain_module


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--update-goldens",
        action="store_true",
        help="rewrite tests/golden/*/expected from the current generator output",
    )


@pytest.fixture(scope="session")
def update_goldens(request: pytest.FixtureRequest) -> bool:
    return bool(request.config.getoption("--update-goldens"))


@pytest.fixture(scope="session")
def libclang_loaded() -> str:
    """Load libclang once. Missing libclang is a failure, not a skip."""
    try:
        return libclang.load()
    except LibclangError as error:
        pytest.fail(f"libclang is required for this test: {error}")


@pytest.fixture(scope="session")
def toolchain() -> toolchain_module.Toolchain:
    """Native compilers. Set BRIDGEFEX_SKIP_NATIVE_TESTS=1 to skip these tests."""
    if os.environ.get("BRIDGEFEX_SKIP_NATIVE_TESTS") == "1":
        pytest.skip("native tests disabled by BRIDGEFEX_SKIP_NATIVE_TESTS=1")
    try:
        return toolchain_module.detect()
    except toolchain_module.ToolchainError as error:
        pytest.fail(f"{error} (set BRIDGEFEX_SKIP_NATIVE_TESTS=1 to skip the native tests)")


@pytest.fixture(scope="session")
def probe_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("probe")
