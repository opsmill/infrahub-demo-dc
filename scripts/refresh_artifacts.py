"""Regenerate every artifact on a branch.

Loading objects with ``infrahubctl object load`` changes the data an artifact was rendered from,
but does not re-render it. Nothing is visibly wrong until a proposed change opens: its artifact
diff compares the branch against ``main``'s *stale* artifact, so unrelated earlier data changes
appear inside somebody else's request. On a demo that reads as the change touching far more than it
does.

Run this after loading or editing data on a branch, so the baseline every later diff is measured
against is current.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from infrahub_sdk import Config, InfrahubClient

TRANSIENT = {"pending", "processing"}
"""Artifact states that mean "not finished yet", lowercased."""


async def refresh(branch: str, timeout: int) -> int:
    """Regenerate every artifact definition on a branch and wait for the results to settle.

    Args:
        branch: Branch to regenerate on.
        timeout: Seconds to wait for artifacts to leave a transient state.

    Returns:
        Process exit code: 0 when every artifact settled, 1 otherwise.
    """
    client = InfrahubClient(
        config=Config(
            address=os.environ.get("INFRAHUB_ADDRESS", "http://localhost:8000"),
            api_token=os.environ.get("INFRAHUB_API_TOKEN"),
            timeout=180,
        )
    )

    definitions = await client.all(kind="CoreArtifactDefinition", branch=branch)
    if not definitions:
        print(f"No artifact definitions on {branch!r}; is the repository imported?")
        return 1

    for definition in definitions:
        await definition.generate()
    print(f"Requested regeneration of {len(definitions)} artifact definition(s) on {branch!r}")

    waited = 0
    while waited < timeout:
        artifacts = await client.all(kind="CoreArtifact", branch=branch)
        pending = [a.name.value for a in artifacts if a.status.value.lower() in TRANSIENT]
        if artifacts and not pending:
            failed = [a.name.value for a in artifacts if a.status.value.lower() != "ready"]
            if failed:
                print(f"Artifacts finished but are not ready: {sorted(set(failed))}")
                return 1
            print(f"{len(artifacts)} artifact(s) regenerated on {branch!r}")
            return 0
        await asyncio.sleep(5)
        waited += 5

    print(f"Timed out after {timeout}s waiting for artifacts to settle on {branch!r}")
    return 1


def main() -> int:
    """Parse arguments and refresh the branch's artifacts.

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--branch", default="main", help="Branch to regenerate artifacts on")
    parser.add_argument("--timeout", type=int, default=300, help="Seconds to wait for artifacts")
    args = parser.parse_args()

    return asyncio.run(refresh(branch=args.branch, timeout=args.timeout))


if __name__ == "__main__":
    sys.exit(main())
