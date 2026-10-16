"""Compose file layering used by ``invoke start``.

The service catalog image is pulled by default. The dev compose file that builds it from
the checkout must be layered only on request, otherwise a plain ``invoke start`` would
build an image the user expects to pull.
"""

from __future__ import annotations

from pathlib import Path

from tasks import get_compose_command

ROOT = Path(__file__).resolve().parents[2]
OVERRIDE = ROOT / "docker-compose.override.yml"
CATALOG_DEV = ROOT / "docker-compose.catalog-dev.yml"


def test_default_invocation_layers_only_the_override_file() -> None:
    """A plain start uses the committed override and not the dev build file."""
    cmd = get_compose_command()

    assert f"-f {OVERRIDE}" in cmd
    assert str(CATALOG_DEV) not in cmd


def test_local_build_layers_the_dev_file_after_the_override() -> None:
    """A local build adds the dev file last, so its build block wins."""
    cmd = get_compose_command(local_build=True)

    assert cmd.index(str(OVERRIDE)) < cmd.index(str(CATALOG_DEV))


def test_override_names_the_published_image_and_does_not_build() -> None:
    """The override points at the published image, so a start pulls it."""
    text = OVERRIDE.read_text(encoding="utf-8")

    assert "registry.opsmill.io/opsmill/infrahub-demo-dc-streamlit" in text
    assert "build:" not in text


def test_dev_file_builds_from_the_repository_root_and_mounts_the_sources() -> None:
    """The image carries objects/dc and the logos, so the build context is the root."""
    text = CATALOG_DEV.read_text(encoding="utf-8")

    assert "context: ." in text
    assert "dockerfile: service_catalog/Dockerfile" in text
    for mount in ("./service_catalog:/app", "./objects:/objects:ro", "./docs/static/img:/app/assets:ro"):
        assert mount in text
