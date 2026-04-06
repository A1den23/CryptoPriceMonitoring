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

    def test_deployment_docs_cover_stablecoin_universe_refresh_workflow(self) -> None:
        deployment = (REPO_ROOT / "DEPLOYMENT.md").read_text(encoding="utf-8")

        self.assertIn("STABLECOIN_UNIVERSE_CACHE_PATH", deployment)
        self.assertIn("python -m common.stablecoin_universe refresh", deployment)
        self.assertIn("STABLECOIN_UNIVERSE_AUTO_REFRESH_ENABLED", deployment)
        self.assertIn("STABLECOIN_UNIVERSE_REFRESH_HOUR", deployment)
        self.assertIn("STABLECOIN_UNIVERSE_REFRESH_MINUTE", deployment)
        self.assertIn("stablecoin-cache", deployment)
        self.assertIn("docker compose up -d --build", deployment)
        self.assertIn("首次执行 `docker compose up -d --build` 时，如果共享缓存不存在，会自动生成 stablecoin universe 缓存", deployment)
        self.assertIn("crypto-monitor` 会按 `TIMEZONE` 每天自动刷新一次", deployment)
        self.assertIn("仍可手动执行 `python -m common.stablecoin_universe refresh` 立即刷新缓存", deployment)
        self.assertNotIn("仍建议通过每天 `0 2 * * *` 的 cron 做后续日常刷新", deployment)
        self.assertNotIn("每次启动都会刷新 stablecoin universe 缓存", deployment)

    def test_readme_documents_stablecoin_universe_cache_path(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("STABLECOIN_UNIVERSE_CACHE_PATH", readme)
        self.assertIn("/app/data/stablecoin_top25.json", readme)
        self.assertIn("python -m common.stablecoin_universe refresh", readme)
        self.assertIn("STABLECOIN_UNIVERSE_AUTO_REFRESH_ENABLED", readme)
        self.assertIn("STABLECOIN_UNIVERSE_REFRESH_HOUR", readme)
        self.assertIn("STABLECOIN_UNIVERSE_REFRESH_MINUTE", readme)
        self.assertIn("stablecoin-cache", readme)
        self.assertIn("docker compose up -d --build", readme)
        self.assertIn("首次执行 `docker compose up -d --build` 时，如果共享缓存不存在，会自动生成 stablecoin universe 缓存", readme)
        self.assertIn("crypto-monitor` 运行中会按 `TIMEZONE` 每天自动刷新一次", readme)
        self.assertIn("仍可手动执行 `python -m common.stablecoin_universe refresh` 立即刷新缓存", readme)
        self.assertNotIn("仍建议通过每天 `0 2 * * *` 的 cron 做后续日常刷新", readme)
        self.assertNotIn("每次启动都会刷新 stablecoin universe 缓存", readme)

    def test_container_startup_bootstrap_contract_uses_startup_wrapper(self) -> None:
        dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
        compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

        self.assertIn('CMD ["python", "-m", "common.startup", "python", "-m", "monitor"]', dockerfile)
        self.assertIn('["python", "-m", "common.startup", "python", "-m", "monitor"]', compose)
        self.assertIn('["python", "-m", "common.startup", "python", "-m", "bot"]', compose)


if __name__ == "__main__":
    unittest.main()
