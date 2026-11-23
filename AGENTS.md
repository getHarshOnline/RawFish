# Working on RawFish

RawFish rehearses how a content, product or policy decision could go wrong. This checkout is the Flask/Vue reference prototype for a fresh rebuild. Creator currently writes a deterministic completion marker and uses a separate model call for its report; generated reactions are hypotheses, not observed audience behaviour.

Start with [README.md](README.md), [architecture](docs/architecture.md), [lane extensions](docs/extending-rawfish.md) and the [runbook](RUNBOOK.md#maintainer-qualification). `backend/app/` holds the service and lane adapters, `frontend/src/` the cockpit, `backend/tests/` the backend tests, and `process/` the checks and local-stack tools.

## Build and check

Use Node 20.19+ in 20.x or 22.12+, Python 3.11–3.12 and uv. From this root:

```sh
npm run setup:all
make test-backend
make test-frontend
make build-frontend
```

Choose focused checks from the runbook. `make ci` additionally runs static gates, desktop/mobile browser smoke, mock smoke, a provider probe and required local report smoke. The last two use real local Ollama requests and load `.env.local` when present. They need the configured model already available. `make up` starts the Docker stack and may pull models; `make purge` deletes its Neo4j data volume; `make ollama-stop` stops matching Ollama server processes.

## Extend the prototype through its existing interfaces

Keep model policy in `backend/app/seam/llm_patches.py` around `utils/llm_client.py`, and lane startup in `simulation_platform/runner_factory.py` around `services/simulation_runner.py`. Behaviour-preserving typing fixes, default-locale changes and upstream security fixes remain ordinary maintenance. A change to either underlying implementation needs an explanation and tests for the affected behaviour; a file name alone is no reason to defer requested work.

Platform adapters may extend `simulation_platform/error_codes.py`, `guardrails/capabilities.py` and the shared types in `guardrails/`. New wire error codes need a lane test and an entry in `backend/tests/platform/test_platform_error_codes_stability.py`; new shared fields need stability tests. Select the existing capability hooks so dispatch does not apply a side effect twice.

Keep numbered workstreams, gate IDs, annex citations and retired planning paths out of code and filenames. Domain terms such as `plan`, `planning` and `plan_id` remain valid. The [hygiene runner](process/run_hygiene_gates.sh) names the checks: retired browser/API routes stay retired, model claims keep their labels, Vue computed refs are used as refs, and new shared/cockpit CSS avoids `transition: all`. Existing upstream motion exceptions retain their reasons. Add a new check to that runner when adding a gate.

Format only changed repository-owned Markdown with the installed Prettier and this repository's configuration. Leave retained upstream material, generated output, licence texts and original records in their existing form.

<!-- BEGIN shared repository rules -->

- Review a source file above 1,000 lines and split it by concern before 1,500.
- Keep Rust tests under `tests/`; keep Go's `_test.go` files beside their package.
- Finish a goal with the codebase the same size or smaller unless a requested feature requires growth. Preserve the behaviour, tests, cases and examples.
- Every engine must build from a fresh clone on Linux and macOS.
- Keep generated API reference and build output out of Git; change their source or generator.
- Write comments and API documentation for the code's reader. A crate or module comment first says what it is for in the system, what it builds on and what builds on it, then its purpose, inputs, results and useful edge cases, with the purpose written problem-first in the present tense: the problem it solves and what solves it today. One plain line after that opening says what is not built yet, as the problem now in front of it, and a negative appears only where a reader would expect the opposite: a limit stated as a property of what exists stays ("it runs on loopback with no caller authentication"), while a negative measured against the finished design is a hedge. Keep them self-contained, with links inside the repository.
- Push each repository as one commit whose whole message is `init`, with Harsh as author and committer under the account the folder uses and no `Co-Authored-By` or `Claude-Session` trailers; later commits stay short and plain.
- Describe the present design and research. The repository neither links nor ships lineage: earlier papers, predecessor versions, history tags and the commits behind them.
- Public repository files, including generated text, link within the repository or to public addresses their readers can open.
- A README is the repository's front door: it carries the product account for the technical reader who arrives there, opening with the product's plain name and the problem it solves, in the words its published pages use.
- Name every operation, command, tool, type, field and variable so a reader without its author's context can tell what it does, what it enables and when to use it; docstrings say the same. Keep full-field record schemas for the engine's consistency, named in plain words like everything else, and author each surface's schemas separately, one per intent, in its caller's terms: SDKs and APIs, the command line, the language server and the agent protocols (MCP, A2A, ACP) are different surfaces with different callers, and the agent protocols compose intents over the operations the others expose.
- Write each Markdown paragraph on one source line. Run the repository's formatter with `proseWrap: never` on changed Markdown.

<!-- END shared repository rules -->
