"""User Story 4 tests: architectural guarantees against the four regression
classes (FR-012..FR-015) from the earlier implementation of this demo do not
recur.

Most are structural (checked by file inspection); one is runtime-level and
needs RUN_INTEGRATION=1.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .conftest import skip_unless_integration

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_fr013_no_number_pool_race():
    """SecurityPolicyRule.index is a plain Number, not a NumberPool.

    FR-013 (NumberPool race) is trivially satisfied by bundle-dc's schema
    choice — no wait loop is needed. This test locks the schema choice in
    place: if a future change turns `index` into a NumberPool the test
    fails and flags the regression risk.
    """
    import yaml

    security = yaml.safe_load((REPO_ROOT / "schemas/extensions/security/security.yml").read_text())
    rule = next(
        n for n in (security.get("nodes") or []) if n.get("name") == "PolicyRule" and n.get("namespace") == "Security"
    )
    idx_attr = next(a for a in rule["attributes"] if a["name"] == "index")
    assert idx_attr["kind"] == "Number", (
        f"SecurityPolicyRule.index must stay kind=Number to keep FR-013 "
        f"trivially satisfied (current kind={idx_attr['kind']!r})"
    )


def test_fr014_paramiko_scp_pinned_in_listener_image():
    """The listener image's pyproject.toml pins paramiko and scp so the
    ansible-playbook process inside the container can find them. FR-014.
    """
    import tomllib

    data = tomllib.loads((REPO_ROOT / "listener/pyproject.toml").read_text())
    deps = data["project"]["dependencies"]
    assert any(d.startswith("paramiko") for d in deps), "listener must pin paramiko (FR-014)"
    assert any(d.startswith("scp") for d in deps), "listener must pin scp (FR-014)"


def test_fr012_listener_container_filesystem_is_ephemeral():
    """The listener compose service declares no persistent volume mount, so
    the container's /root/.ssh/known_hosts is reset on every recreate. FR-012.
    """
    import yaml

    data = yaml.safe_load((REPO_ROOT / "docker-compose.override.yml").read_text())
    listener = data["services"]["webhook-listener"]
    volumes = listener.get("volumes") or []
    assert not volumes, (
        f"webhook-listener must not declare persistent volumes so known_hosts "
        f"stays ephemeral (got {volumes!r}). FR-012."
    )


def test_fr016_infrahub_yml_declares_no_object_imports():
    """bundle-dc's .infrahub.yml does not `objects:`-load anything at
    repo-import time; bootstrap owns object loading. FR-016.
    """
    import yaml

    data = yaml.safe_load((REPO_ROOT / ".infrahub.yml").read_text())
    assert "objects" not in data, (
        "bundle-dc's .infrahub.yml must not carry a top-level `objects:` list "
        "(FR-016); bootstrap handles object loading separately."
    )


@skip_unless_integration
def test_fr014_runtime_paramiko_scp_importable_in_listener():
    """Runtime check: inside the running listener container, `python -c
    'import paramiko, scp'` exits 0. Complement to the structural test.
    """
    result = subprocess.run(
        ["docker", "exec", "webhook-listener", "uv", "run", "python", "-c", "import paramiko, scp"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"paramiko/scp must be importable inside the listener container. "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
