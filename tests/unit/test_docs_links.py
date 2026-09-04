"""Guards that keep the documentation portable to the infrahub-docs site.

This repository's Docusaurus site serves the docs at the root of its own domain
(``baseUrl: '/'`` with ``routeBasePath: '/'`` in ``docs/docusaurus.config.ts``). The same files are
also synced into ``opsmill/infrahub-docs``, which mounts them under ``/demo-dc/``. A site-absolute
link target such as ``/virtualization`` therefore resolves here and 404s there, and because that
site sets ``onBrokenLinks: 'throw'`` it fails the downstream build rather than the pull request that
introduced it. ``invoke docs`` cannot catch it: the link is genuinely valid against this site's
route base.

Relative targets (``./virtualization.mdx``) are resolved against the source file, so they survive
being re-hosted under any prefix. That is the convention this module enforces.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DOCS_CONTENT_DIRECTORY = Path(__file__).resolve().parents[2] / "docs" / "docs"

# Markdown inline links and images: ``[text](target)`` and ``![alt](target)``. The target stops at
# the first whitespace so an optional ``"title"`` is left out, and at ``)`` so the target is bare.
MARKDOWN_TARGET_PATTERN = re.compile(r"!?\[[^\]]*\]\(\s*([^)\s]+)")

# JSX/HTML attributes in MDX, which Docusaurus resolves the same way as a markdown target.
JSX_TARGET_PATTERN = re.compile(r"\b(?:href|src)=[\"']([^\"']+)[\"']")

# ``//example.com`` is protocol-relative and leaves the site entirely, so it is not site-absolute.
SITE_ABSOLUTE_PATTERN = re.compile(r"^/(?!/)")


def _documentation_files() -> list[Path]:
    """Collect the markdown sources that get synced to infrahub-docs.

    Returns:
        Every ``.md`` and ``.mdx`` file under ``docs/docs``, sorted for a stable test order.
    """
    return sorted(path for path in DOCS_CONTENT_DIRECTORY.rglob("*") if path.suffix in {".md", ".mdx"})


def _site_absolute_targets(path: Path) -> list[str]:
    """Find every site-absolute link target in one documentation file.

    Args:
        path: The markdown file to scan.

    Returns:
        One ``file:line -> target`` string per offending link, in file order.
    """
    offenders: list[str] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        targets = [match.group(1) for match in MARKDOWN_TARGET_PATTERN.finditer(line)]
        targets += [match.group(1) for match in JSX_TARGET_PATTERN.finditer(line)]
        offenders += [f"{path.name}:{number} -> {target}" for target in targets if SITE_ABSOLUTE_PATTERN.match(target)]
    return offenders


def test_documentation_directory_is_discoverable() -> None:
    """Fail loudly if the docs move, rather than passing on an empty file list."""
    assert DOCS_CONTENT_DIRECTORY.is_dir(), f"{DOCS_CONTENT_DIRECTORY} is missing"
    assert _documentation_files(), f"no markdown files found under {DOCS_CONTENT_DIRECTORY}"


@pytest.mark.parametrize("path", _documentation_files(), ids=lambda path: path.name)
def test_no_site_absolute_links(path: Path) -> None:
    """Every internal link is relative, so it survives being re-hosted under a path prefix.

    Args:
        path: The markdown file under test.
    """
    offenders = _site_absolute_targets(path)
    assert not offenders, (
        "Site-absolute link targets break the infrahub-docs build, which mounts these files under "
        "/demo-dc/. Use a relative target instead, for example ./virtualization.mdx:\n  " + "\n  ".join(offenders)
    )
