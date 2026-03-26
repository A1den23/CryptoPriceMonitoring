import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class DeploymentContractsTests(unittest.TestCase):
    def test_dockerignore_excludes_dot_venv_directory(self) -> None:
        dockerignore = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")

        self.assertIn(".venv/", dockerignore)

    def test_dockerignore_excludes_runtime_local_state(self) -> None:
        dockerignore = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")

        self.assertIn(".env", dockerignore)
        self.assertIn("logs/", dockerignore)

    def test_dockerignore_keeps_core_project_docs_and_docker_files_in_context(self) -> None:
        dockerignore = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")

        self.assertNotIn("README.md", dockerignore)
        self.assertNotIn("*.md", dockerignore)
        self.assertNotIn("Dockerfile", dockerignore)
        self.assertNotIn("docker-compose.yml", dockerignore)

    def test_readme_documents_stable_unittest_entrypoint(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("python3 -m unittest discover -s tests -p 'test_*.py'", readme)

    def test_deployment_guide_documents_stable_unittest_entrypoint(self) -> None:
        deployment = (REPO_ROOT / "DEPLOYMENT.md").read_text(encoding="utf-8")

        self.assertIn("python3 -m unittest discover -s tests -p 'test_*.py'", deployment)

    def test_deployment_guide_documents_deployment_contract_self_check_command(self) -> None:
        deployment = (REPO_ROOT / "DEPLOYMENT.md").read_text(encoding="utf-8")

        self.assertIn("tests/test_deployment_contracts.py", deployment)
        self.assertIn(
            "python3 -m unittest discover -s tests -p 'test_deployment_contracts.py'",
            deployment,
        )

    def test_deployment_guide_clarifies_compose_deploy_resources_scope(self) -> None:
        deployment = (REPO_ROOT / "DEPLOYMENT.md").read_text(encoding="utf-8")

        self.assertRegex(
            deployment,
            r"deploy\.resources[\s\S]{0,200}docker compose up[\s\S]{0,200}(验证|校验|确认)",
        )

    def test_readme_documents_price_picker_and_direct_lookup(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("/price", readme)
        self.assertIn("/price BTC", readme)
        self.assertRegex(readme, r"/price[\s\S]{0,80}(选择|弹出)")

    def test_deployment_guide_documents_price_picker_and_direct_lookup(self) -> None:
        deployment = (REPO_ROOT / "DEPLOYMENT.md").read_text(encoding="utf-8")

        self.assertIn("/price", deployment)
        self.assertIn("/price BTC", deployment)
        self.assertRegex(deployment, r"/price[\s\S]{0,80}(选择|弹出)")


if __name__ == "__main__":
    unittest.main()
