"""The contract other workstreams code against.

Workstream A owns the JSON shapes for mandate, charge, evaluator output and
verdict. They are committed as fixtures so B, C and D can build against files
rather than against each other's running services — nobody blocks on anybody.

Import `build_scenarios()` to get them as live objects, or read the generated
JSON in `contracts/fixtures/`.
"""

from trustrail.contracts.scenarios import Scenario, build_scenarios, demo_config

__all__ = ["Scenario", "build_scenarios", "demo_config"]
