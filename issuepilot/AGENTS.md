# Maintainer instructions

Read README.md and docs/architecture.md before changing scope or adding a service.
All repository code comments and documentation are in English.
Preserve the distinction between tested behavior, synthetic evaluation, and live evidence.
Never claim model performance from the rules baseline. Never weaken approval or
signature checks to make a demo pass. Keep GitHub writes limited to the configured
sandbox and reviewed proposal. Do not add secrets, virtual environments, raw private
issues, or runtime artifacts to git. Keep one root README and a small cohesive docs set.
Run `uv run ruff check .`, `uv run ruff format --check .`, and `uv run pytest -q`.
Use the dedicated PostgreSQL CI database only for integration tests: fixtures drop tables.
