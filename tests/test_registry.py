"""Tests for the data-driven install component registry."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from codex_orchestrator import cli
from codex_orchestrator.registry import (
    BundledSource,
    GitHubSource,
    RegistryError,
    load_registry,
    resolve_components,
)


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

    def test_packaged_registry_declares_v2_catalog_and_optional_components(self) -> None:
        registry = cli.default_registry()

        self.assertEqual(
            [component.id for component in registry.components],
            ["orchestrated-delivery", "code-review", "grill-me", "handoff", "teach"],
        )
        delivery = registry.by_id["orchestrated-delivery"]
        self.assertTrue(all(component.active for component in registry.components))
        self.assertEqual(len(delivery.resources), 7)
        self.assertEqual(delivery.depends_on, ())
        self.assertTrue(
            all(isinstance(resource.source, BundledSource) for resource in delivery.resources)
        )

        repository = registry.repositories["mattpocock-skills"]
        self.assertEqual(
            (
                repository.owner,
                repository.repository,
                repository.ref,
                repository.display_name,
                repository.homepage,
                repository.licence,
            ),
            (
                "mattpocock",
                "skills",
                "main",
                "Matt Pocock skills",
                "https://github.com/mattpocock/skills",
                "MIT",
            ),
        )

        expected_remote_resources = {
            "grill-me": (
                ("skills/productivity/grill-me/SKILL.md", "grill-me/SKILL.md"),
                ("skills/productivity/grill-me/agents/openai.yaml", "grill-me/agents/openai.yaml"),
                ("skills/productivity/grilling/SKILL.md", "grilling/SKILL.md"),
                ("skills/productivity/grilling/agents/openai.yaml", "grilling/agents/openai.yaml"),
            ),
            "handoff": (
                ("skills/productivity/handoff/SKILL.md", "handoff/SKILL.md"),
                ("skills/productivity/handoff/agents/openai.yaml", "handoff/agents/openai.yaml"),
            ),
            "teach": (
                ("skills/productivity/teach/SKILL.md", "teach/SKILL.md"),
                ("skills/productivity/teach/GLOSSARY-FORMAT.md", "teach/GLOSSARY-FORMAT.md"),
                ("skills/productivity/teach/LEARNING-RECORD-FORMAT.md", "teach/LEARNING-RECORD-FORMAT.md"),
                ("skills/productivity/teach/MISSION-FORMAT.md", "teach/MISSION-FORMAT.md"),
                ("skills/productivity/teach/RESOURCES-FORMAT.md", "teach/RESOURCES-FORMAT.md"),
                ("skills/productivity/teach/agents/openai.yaml", "teach/agents/openai.yaml"),
            ),
        }
        for component_id, expected_resources in expected_remote_resources.items():
            with self.subTest(component=component_id):
                resources = registry.by_id[component_id].resources
                self.assertEqual(
                    tuple(
                        (resource.source.path.as_posix(), resource.destination.as_posix())
                        for resource in resources
                        if isinstance(resource.source, GitHubSource)
                    ),
                    expected_resources,
                )
                self.assertTrue(
                    all(
                        isinstance(resource.source, GitHubSource)
                        and resource.source.repository == "mattpocock-skills"
                        for resource in resources
                    )
                )

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

    def test_loads_v2_bundled_and_github_sources_without_remote_path(self) -> None:
        self.write_registry(
            """\
schema_version: 2
repositories:
  upstream:
    owner: example-owner
    repository: example.repo
    ref: feature/foo
    display_name: Example skills
    licence: MIT
components:
  - id: bundled
    title: Bundled
    active: true
    description: Bundled component.
    includes: One file.
    depends_on: []
    resources:
      - source:
          kind: bundled
          path: one.txt
        install_root: skills
        destination: bundled/one.txt
  - id: remote
    title: Remote
    active: true
    description: Remote component.
    includes: One remote directory.
    depends_on: []
    resources:
      - source:
          kind: github
          repository: upstream
          path: skills/not-yet-materialized
        install_root: skills
        destination: remote
"""
        )

        registry = load_registry(self.registry_path, self.root)

        self.assertEqual(set(registry.repositories), {"upstream"})
        self.assertEqual(registry.repositories["upstream"].ref, "feature/foo")
        self.assertEqual(
            registry.by_id["bundled"].resources[0].source,
            BundledSource(kind="bundled", path=Path("one.txt")),
        )
        self.assertEqual(
            registry.by_id["remote"].resources[0].source,
            GitHubSource(
                kind="github",
                repository="upstream",
                path=Path("skills/not-yet-materialized"),
            ),
        )

    def test_schema_v1_fixture_remains_compatible(self) -> None:
        self.write_registry(
            """\
schema_version: 1
components:
  - id: legacy
    title: Legacy
    active: true
    description: Legacy component.
    includes: One file.
    depends_on: []
    resources:
      - source: one.txt
        install_root: skills
        destination: legacy/one.txt
"""
        )

        registry = load_registry(self.registry_path, self.root)

        self.assertEqual(registry.repositories, {})
        self.assertEqual(registry.by_id["legacy"].resources[0].source, Path("one.txt"))

    def test_rejects_v2_unknown_fields_and_unknown_repository(self) -> None:
        cases = {
            "registry": ("unexpected: field\n", "", "", ""),
            "repository": ("", "    unexpected: field\n", "", ""),
            "obsolete homepage": ("", "    homepage: https://github.com/example/skills\n", "", ""),
            "obsolete update policy": ("", "    update_policy: on_install\n", "", ""),
            "source": ("", "", "          unexpected: field\n", ""),
            "resource": ("", "", "", "        unexpected: field\n"),
            "unknown repository": ("", "", "", ""),
        }
        for case, (registry_extra, repository_extra, source_extra, resource_extra) in cases.items():
            with self.subTest(case=case):
                repository = "missing" if case == "unknown repository" else "upstream"
                self.write_registry(
                    f"""\
schema_version: 2
repositories:
  upstream:
    owner: example
    repository: skills
    ref: main
    display_name: Example
    licence: MIT
{repository_extra}{registry_extra}components:
  - id: remote
    title: Remote
    active: true
    description: Remote component.
    includes: One remote directory.
    depends_on: []
    resources:
      - source:
          kind: github
          repository: {repository}
          path: skills/remote
{source_extra}{resource_extra}        install_root: skills
        destination: remote
"""
                )
                expected = "unknown fields" if case != "unknown repository" else "unknown repository"
                with self.assertRaisesRegex(RegistryError, expected):
                    load_registry(self.registry_path, self.root)

    def test_rejects_unsafe_v2_source_and_destination_paths(self) -> None:
        for field, value in (
            ("path", "../escape"),
            ("path", "skills\\escape"),
            ("destination", "/absolute"),
            ("destination", "remote//file"),
        ):
            with self.subTest(field=field, value=value):
                source_path = value if field == "path" else "skills/remote"
                destination = value if field == "destination" else "remote"
                self.write_registry(
                    f"""\
schema_version: 2
repositories:
  upstream:
    owner: example
    repository: skills
    ref: main
    display_name: Example
    licence: MIT
components:
  - id: remote
    title: Remote
    active: true
    description: Remote component.
    includes: One remote directory.
    depends_on: []
    resources:
      - source:
          kind: github
          repository: upstream
          path: {source_path}
        install_root: skills
        destination: {destination}
"""
                )
                with self.assertRaisesRegex(RegistryError, "safe relative path"):
                    load_registry(self.registry_path, self.root)

    def test_rejects_malformed_repository_metadata(self) -> None:
        cases = {
            "repository id": ("upstream_1", "owner: example", "only lowercase letters"),
            "owner": ("upstream", "owner: -bad", "valid GitHub owner"),
            "repository": ("upstream", "repository: skills.git", "valid GitHub repository"),
            "ref traversal": ("upstream", "ref: feature..branch", "safe Git ref"),
            "ref empty": ("upstream", 'ref: ""', "must be a non-empty string"),
            "ref empty segment": ("upstream", "ref: feature//branch", "safe Git ref"),
            "ref leading slash": ("upstream", "ref: /feature", "safe Git ref"),
            "ref trailing slash": ("upstream", "ref: feature/", "safe Git ref"),
            "ref invalid character": ("upstream", "ref: feature~branch", "safe Git ref"),
            "ref non-ascii": ("upstream", "ref: café", "safe Git ref"),
            "ref trailing dot": ("upstream", "ref: feature.", "safe Git ref"),
            "ref lock suffix": ("upstream", "ref: feature.lock", "safe Git ref"),
            "ref surrounding whitespace": ("upstream", 'ref: " feature/foo"', "safe Git ref"),
        }
        for case, (repository_id, replacement, error) in cases.items():
            with self.subTest(case=case):
                field, value = replacement.split(": ", 1)
                metadata = {
                    "owner": "example",
                    "repository": "skills",
                    "ref": "main",
                    "display_name": "Example",
                    "licence": "MIT",
                }
                metadata[field] = value
                self.write_registry(
                    f"""\
schema_version: 2
repositories:
  {repository_id}:
    owner: {metadata['owner']}
    repository: {metadata['repository']}
    ref: {metadata['ref']}
    display_name: {metadata['display_name']}
    licence: {metadata['licence']}
components:
  - id: remote
    title: Remote
    active: true
    description: Remote component.
    includes: One remote directory.
    depends_on: []
    resources:
      - source:
          kind: github
          repository: {repository_id}
          path: skills/remote
        install_root: skills
        destination: remote
"""
                )
                with self.assertRaisesRegex(RegistryError, error):
                    load_registry(self.registry_path, self.root)

    def test_rejects_duplicate_v2_resource_sources_and_destinations(self) -> None:
        for duplicate, expected_error in (
            ("source", "duplicate resource source"),
            ("destination", "duplicate resource destination"),
        ):
            with self.subTest(duplicate=duplicate):
                second_path = "skills/first" if duplicate == "source" else "skills/second"
                second_destination = "first" if duplicate == "destination" else "second"
                self.write_registry(
                    f"""\
schema_version: 2
repositories:
  upstream:
    owner: example
    repository: skills
    ref: main
    display_name: Example
    licence: MIT
components:
  - id: first
    title: First
    active: true
    description: First component.
    includes: One remote directory.
    depends_on: []
    resources:
      - source:
          kind: github
          repository: upstream
          path: skills/first
        install_root: skills
        destination: first
  - id: second
    title: Second
    active: true
    description: Second component.
    includes: One remote directory.
    depends_on: []
    resources:
      - source:
          kind: github
          repository: upstream
          path: {second_path}
        install_root: skills
        destination: {second_destination}
"""
                )
                with self.assertRaisesRegex(RegistryError, expected_error):
                    load_registry(self.registry_path, self.root)


if __name__ == "__main__":
    unittest.main()
