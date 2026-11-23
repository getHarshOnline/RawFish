"""Exercise documentation distinctions in the repository's static contract gate."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


CHECKER_PATH = Path(__file__).resolve().parents[3] / "process/check_rawfish_contract.py"


class DocumentationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        spec = importlib.util.spec_from_file_location("rawfish_contract_gate", CHECKER_PATH)
        assert spec is not None and spec.loader is not None
        cls.gate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.gate)

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / "any-clone-name"
        self.root.mkdir()
        vars(self.gate).update(
            PRODUCT_ROOT=self.root,
            WORKSPACE_ROOT=self.root.parent,
            HYGIENE_CONTRACT=self.root / "process/contracts/rawfish_hygiene_contract.json",
        )

    def scan(self, source: str, relative: str = "docs/example.md") -> list[str]:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
        failures: list[str] = []
        self.gate.check_text_drift(failures)
        return failures

    def test_upstream_mentions_do_not_exempt_the_whole_line_or_file(self) -> None:
        cases = [
            ("The public MiroFish fork does not contain RawFish's changes.", False),
            ("Derived from upstream MiroFish.", False),
            ("MiroFish upstream supplies the inherited implementation.", False),
            ("Open the MiroFish dashboard.", True),
            ("MiroFish is our product.", True),
            ("Derived from upstream MiroFish. Open the MiroFish dashboard.", True),
            ("Derived from upstream MiroFish.\nOpen the MIROFISH dashboard.", True),
        ]
        for text, rejected in cases:
            with self.subTest(text=text):
                self.assertEqual(bool(self.scan(text)), rejected)

    def test_upstream_policy_quotes_remain_narrow(self) -> None:
        text = (
            "### Where the name MiroFish may appear\n\n"
            "MiroFish's name may appear only where the text is genuinely about the upstream work: "
            "the inherited licence text and upstream attribution.\n"
        )
        path = "docs/POLICY_PRODUCT_SPEC.md"
        self.assertEqual(self.scan(text, path), [])
        self.assertTrue(self.scan(text + "Open MiroFish.\n", path))
        self.assertTrue(self.scan(text.replace("attribution.", "attribution. MiroFish UI."), path))

    def test_attribution_does_not_allow_runtime_branding(self) -> None:
        self.assertTrue(self.scan('print("upstream MiroFish")', "process/example.py"))

    def test_readme_keeps_the_linked_upstream_credit(self) -> None:
        credit = "RawFish descends from [MiroFish](https://github.com/666ghj/MiroFish)."
        self.assertEqual(self.scan(credit, "README.md"), [])
        self.assertTrue(self.scan(credit + " Open MiroFish.", "README.md"))

    def test_repository_links_and_actual_removed_routes(self) -> None:
        cases = [
            ("[Process checks](../process/README.md)", False),
            ("[Process checks](./process/README.md#checks)", False),
            ("[Policy](../policy/README.md)", False),
            ("Open `/process/`.", True),
            ("[Old page](/process/README.md)", True),
            ("Open `/policy`.", True),
            ("Open http://localhost:3000/process/", True),
            ("[Checks](../process/README.md); open `/policy/`.", True),
            ("Open `/simulation/:runId`.", True),
        ]
        for text, rejected in cases:
            with self.subTest(text=text):
                self.assertEqual(bool(self.scan(text)), rejected)

    def external_example(self, mode: str = "external") -> str:
        return (
            "To use an external provider instead:\n\n```env\n"
            f"RAWFISH_LLM_PROVIDER_MODE={mode}\n"
            "RAWFISH_LLM_PROVIDER=openai\n"
            "RAWFISH_LLM_BASE_URL=https://api.openai.com/v1\n"
            "RAWFISH_CONTAINER_LLM_BASE_URL=https://api.openai.com/v1\n"
            "```\n"
        )

    def test_external_example_requires_explicit_mode_in_the_same_fence(self) -> None:
        self.assertEqual(self.scan(self.external_example()), [])
        rejected = [
            self.external_example("local_ollama"),
            self.external_example().replace("RAWFISH_LLM_PROVIDER_MODE=external\n", ""),
            self.external_example().replace("```env", "```sh"),
            self.external_example().replace("```\n", "", 1),
            self.external_example().replace(
                "RAWFISH_LLM_PROVIDER=openai", "RAWFISH_LLM_PROVIDER_MODE=local_ollama"
            ),
            "RAWFISH_LLM_PROVIDER_MODE=external\n```env\n"
            "RAWFISH_LLM_BASE_URL=https://api.openai.com/v1\n```\n",
            self.external_example() + "Default: https://api.openai.com/v1\n",
            self.external_example() + "```env\nRAWFISH_LLM_BASE_URL=https://api.openai.com/v1\n```\n",
        ]
        for text in rejected:
            with self.subTest(text=text):
                self.assertTrue(self.scan(text))

    def test_external_example_does_not_exempt_other_settings_or_env_defaults(self) -> None:
        self.assertTrue(self.scan(self.external_example(), ".env.example"))
        self.assertTrue(self.scan(self.external_example().replace(
            "RAWFISH_CONTAINER_LLM_BASE_URL", "OPENAI_BASE_URL"
        )))
        self.assertTrue(self.scan(self.external_example().replace(
            "RAWFISH_LLM_PROVIDER=openai", "Default: OpenAI"
        )))

    def test_repository_contract_uses_the_same_external_example_distinction(self) -> None:
        contract = {
            "schema_version": 1,
            "legacy_decision_count_labels_allowed": False,
            "active_registry_kinds": ["lane"],
            "non_registry_concepts": ["preset"],
            "source_documents": ["README.md"],
        }
        path = self.gate.HYGIENE_CONTRACT
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(contract), encoding="utf-8")
        self.scan(self.external_example(), "README.md")
        failures: list[str] = []
        self.gate.check_repository_contract(failures)
        self.assertEqual(failures, [])
        self.scan(self.external_example("local_ollama"), "README.md")
        self.gate.check_repository_contract(failures)
        self.assertEqual(len(failures), 2)


if __name__ == "__main__":
    unittest.main()
