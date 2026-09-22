"""Tests for the data-driven install component registry."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from codex_orchestrator import cli
from codex_orchestrator.registry import RegistryError, load_registry, resolve_components


class RegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        (self.root / "one.txt").write_text("one", encoding="utf-8")
        (self.root / "two.txt").write_text("two", encoding="utf-8")
        self.registry_path = self.root / "registry.yaml"

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def write_registry(self, content: str) -> None:
        self.registry_path.write_text(content, encoding="utf-8")

    def test_packaged_registry_defines_initial_components(self) -> None:
        registry = cli.default_registry()

        self.assertEqual(
            [component.id for component in registry.components],
            ["orchestrated-delivery", "code-review"],
        )
        delivery = registry.by_id["orchestrated-delivery"]
        self.assertTrue(all(component.active for component in registry.components))
        self.assertEqual(len(delivery.resources), 7)
        self.assertEqual(delivery.depends_on, ())

    def test_resolves_transitive_dependencies_in_registry_order(self) -> None:
        self.write_registry(
            """\
schema_version: 1
components:
  - id: base
    title: Base
    active: true
    description: Base component.
    includes: One file.
    depends_on: []
    resources:
      - source: one.txt
        install_root: skills
        destination: base/one.txt
  - id: feature
    title: Feature
    active: true
    description: Feature component.
    includes: One file.
    depends_on: [base]
    resources:
      - source: two.txt
        install_root: skills
        destination: feature/two.txt
"""
        )
        registry = load_registry(self.registry_path, self.root)

        resolved = resolve_components(registry, ("feature",))

        self.assertEqual([component.id for component in resolved], ["base", "feature"])

    def test_install_includes_transitive_dependency_resources(self) -> None:
        self.write_registry(
            """\
schema_version: 1
components:
  - id: base
    title: Base
    active: true
    description: Base component.
    includes: One file.
    depends_on: []
    resources:
      - source: one.txt
        install_root: skills
        destination: base/one.txt
  - id: feature
    title: Feature
    active: true
    description: Feature component.
    includes: One file.
    depends_on: [base]
    resources:
      - source: two.txt
        install_root: skills
        destination: feature/two.txt
"""
        )
        registry = load_registry(self.registry_path, self.root)
        agents_root = self.root / "install" / "codex-home"
        skills_root = self.root / "install" / "skills"

        self.assertEqual(
            cli.install(agents_root, skills_root, ("feature",), registry), 0
        )

        self.assertEqual((skills_root / "base/one.txt").read_text(), "one")
        self.assertEqual((skills_root / "feature/two.txt").read_text(), "two")
        manifest = json.loads(
            (agents_root / cli.MANIFEST_NAME).read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["components"], ["base", "feature"])

    def test_rejects_unknown_and_cyclic_dependencies(self) -> None:
        for dependency in ("missing", "feature"):
            with self.subTest(dependency=dependency):
                self.write_registry(
                    f"""\
schema_version: 1
components:
  - id: feature
    title: Feature
    active: true
    description: Feature component.
    includes: One file.
    depends_on: [{dependency}]
    resources:
      - source: one.txt
        install_root: skills
        destination: feature/one.txt
"""
                )
                with self.assertRaisesRegex(
                    RegistryError, "unknown dependency|dependency cycle"
                ):
                    load_registry(self.registry_path, self.root)

    def test_rejects_non_boolean_active_value(self) -> None:
        self.write_registry(
            """\
schema_version: 1
components:
  - id: feature
    title: Feature
    active: "yes"
    description: Feature component.
    includes: One file.
    depends_on: []
    resources:
      - source: one.txt
        install_root: skills
        destination: feature/one.txt
"""
        )

        with self.assertRaisesRegex(RegistryError, "active must be true or false"):
            load_registry(self.registry_path, self.root)

    def test_rejects_active_component_with_inactive_dependency(self) -> None:
        self.write_registry(
            """\
schema_version: 1
components:
  - id: retired-base
    title: Retired base
    active: false
    description: Retired component.
    includes: One file.
    depends_on: []
    resources:
      - source: one.txt
        install_root: skills
        destination: base/one.txt
  - id: feature
    title: Feature
    active: true
    description: Feature component.
    includes: One file.
    depends_on: [retired-base]
    resources:
      - source: two.txt
        install_root: skills
        destination: feature/two.txt
"""
        )

        with self.assertRaisesRegex(
            RegistryError, "active component 'feature' depends on inactive component"
        ):
            load_registry(self.registry_path, self.root)

    def test_rejects_unsafe_paths_before_installation(self) -> None:
        self.write_registry(
            """\
schema_version: 1
components:
  - id: unsafe
    title: Unsafe
    active: true
    description: Unsafe component.
    includes: One file.
    depends_on: []
    resources:
      - source: ../one.txt
        install_root: skills
        destination: unsafe/one.txt
"""
        )

        with self.assertRaisesRegex(RegistryError, "safe relative path"):
            load_registry(self.registry_path, self.root)

    def test_rejects_duplicate_destinations(self) -> None:
        self.write_registry(
            """\
schema_version: 1
components:
  - id: one
    title: One
    active: true
    description: First component.
    includes: One file.
    depends_on: []
    resources:
      - source: one.txt
        install_root: skills
        destination: shared.txt
  - id: two
    title: Two
    active: true
    description: Second component.
    includes: One file.
    depends_on: []
    resources:
      - source: two.txt
        install_root: skills
        destination: shared.txt
"""
        )

        with self.assertRaisesRegex(RegistryError, "duplicate resource destination"):
            load_registry(self.registry_path, self.root)

    def test_rejects_yaml_aliases(self) -> None:
        self.write_registry(
            """\
schema_version: 1
components:
  - &component
    id: one
    title: One
    active: true
    description: First component.
    includes: One file.
    depends_on: []
    resources:
      - source: one.txt
        install_root: skills
        destination: one.txt
  - *component
"""
        )

        with self.assertRaisesRegex(RegistryError, "aliases are not allowed"):
            load_registry(self.registry_path, self.root)


if __name__ == "__main__":
    unittest.main()
