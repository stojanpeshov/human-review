# Ludvig port — progress

Where the port of `/human-review` to Ludvig's stack stands: what is done, what is left.
Setup and what each tab shows are in [`ludvig.md`](ludvig.md); this file is the log.
Update it in the same commit as the work it describes.

The original Java/Spring fork state is commit `870eb000` (and upstream
`victorrentea/human-review`): look there for how a removed feature used to work.

## Done

| Phase | What | Commit |
| --- | --- | --- |
| 0 | Runs on Linux/WSL (`desktop.py`); CI gate reads CircleCI and never pushes; page de-authored | `6107431a` |
| 1a | Logs tab reads Python logging, with PII chips from Ludvig's logging standard | `7dcb9d4c` |
| 1b | Per-test coverage for pytest suites (`testcov.pytest`) | `313305cf` |
| 1c | Complexity: cognitive complexity per Serverless entry point | `3d386442` |
| 1d | JUnit/JaCoCo runner dropped; `human-review.ludvig.json` config template | `53f742ea` |
| 2a | API tab: OpenAPI derived from `serverless*.yml` + handlers, both sides, diffed by oasdiff | `5cb1eca1` |
| 2b | Data tab: ER diagram from the SQLAlchemy models | `8c1f3ecd` |
| 2c | Data tab: Alembic migrations card | `52dacac9` |
| 3a | Structure tab: Packages (Python imports) and Containers (`serverless*.yml`), generated | `be2e073c` |
| 3b | Sequence tab: pytest runs drawn by the `hr_genseq` plugin | `1ea145e5` |
| 4a | Per-test coverage of mitt-ludvig's Vitest suite | `0dc07abf` |
| 5a | Tests tab reads its ticket from Jira | `f7377c4e` |
| 5b | `ludvig:code-review` in `/record-review`; setup guide `docs/ludvig.md` | `c0008075` |
| fixes | CircleCI `[ENV]` job prefixes; no-ticket branches; flat Containers; sequence waits for diagrams | PRs #1, #2 |

### Java / Spring / Angular removed (16 Nov 2026)

Every Java-ecosystem code path is gone, not kept as a fallback:

- **Tests & Sequence:** the JUnit/Spring/Flyway/`@Sql` parts of the DB-fixture reader
  (`shared/fixtures.py`). The Spring controller scan (`spring_handlers`) and route→handler
  lookup (`shared/genseq.py`). The JUnit test kinds, Java import resolution and
  `@GenerateSequence` (`tabs/sequence.py`). The Karma template proxy (`tabs/tests.py`).
- **Test changes & coverage:** the Java test scanner (`test-changes.py`); the JUnit/JaCoCo/
  Karma leftovers and `testcov/karma/` (`testcov.py`); Java step definitions (`semcov.py`,
  `code_xref.py`); the `.java` lexer and Angular template detection (`extract-snippet.py`).
  The JavaParser-only "not followed" column (`endpoint-complexity-delta.py`).
- **Steps & gates:** the Maven step lock is now `pytest`. `{class}` is gone. No-tests
  detection reads pytest (exit 5), Playwright and Vitest instead of Maven/JUnit. The
  Angular unlisted-screen check is gone (`run-steps.py`). `pom.xml` Sonar lookup removed
  (`preflight.py`).
- **API:** oasdiff is the only differ; the Java openapi-diff fallback (Maven jar, docker,
  `--jar/--docker/--no-oasdiff`, "PARTIAL LIST") is gone, and a missing oasdiff fails
  clearly (`openapi-compat.py`).
- **Structure & diagrams:** Java packages / Maven modules / ArchUnit in `shared/folders.py`
  (a box's stereotype is now a folder path), `shared/diagrams.py`, `shared/c4.py`,
  `structurizr-views.py`; the domain-model and Maven-modules pieces in `shared/adopt.py`.
- **Review & server:** Java constants and statement suffixes (`tabs/review.py`). Spring
  Boot stack traces → Python tracebacks (`serve-review.py`).
- **UX (postponed tab, minimal):** Angular class-list stamps and the template locator
  (`ds-audit.py`).
- **Config & docs:** petclinic's `human-review.example.json` deleted; `human-review.ludvig.json`
  is the one template. The reference prompts, README and tab docs use Python/pytest examples.
- **Demo (postponed tab):** the Flyway-backed DB Fixture card (`dataset_view.py`,
  `dataset-view.js`) and the Running-app band's Seed / Reset DB controls removed. The
  Tests and Sequence tabs' fixture dots are plain marks now; they opened that card.

Suite after the removal: all green except the 6 browser-layout tests that already failed
before it (`test_no_page_hscroll` masthead ×4, `test_title_icons_centred` ×2).

### pytest on the Sequence and Tests tabs, Linux fonts (17 Nov 2026)

- **Sequence: `@pytest.mark.genseq` is a tag.** A marked test is listed under *Tagged for
  tracing* until a diagram names it. It is quoted from its markers to the end of its body.
  A parametrised test's diagram (`test_x[a]`) finds its `def` (`tabs/sequence.py`).
- **A Python test is read whole.** Snippet windows follow a `def`'s indented body, not
  only braces (`extract-snippet._closing_line`). So semcov's pairing reads the body, and
  the Tests and Review cards quote all of it.
- **Test-data dots.** A pytest test's dot names the `tests/data/<dataset>` entries it loads:
  through its body, the fixtures it requests (conftest included), the project helpers they
  call, and module path constants. What every test reads through autouse fixtures is the
  baseline and gets no dot. On activities-backend 50 tests get one, e.g.
  `ML-1570 + onboarding_update` ×35. The hover says *Test data: …*
  (`shared/fixtures.py: pytest_data_by_test`).
- **Linux fonts.** The page's font stack falls back to Liberation Sans/Arial before the
  generic sans-serif. DejaVu Sans is wide enough to wrap the masthead. `test_panel_swap`
  passes with Playwright's system dependencies installed.

Suite: all green except the two `test_title_icons_centred` checks (see below).

## Left to do

- **Two icon-centring tests fail on Linux.** `test_title_icons_centred` holds the rerun
  rings and the voice pills within 0.5px of the title's capitals. The nudges are tuned to
  macOS/Windows fonts (SF, Segoe UI); with Linux's fonts they sit 1.1px off. A
  font-independent fix needs CSS `text-box-trim`, which Playwright's Chromium 131 lacks.
- **Postponed tabs still carry Java/Angular:**
  - Code City (`regenerate-codecity.sh`, `city-coverage.py`) is a JaCoCo-based tool.
  - Demo film scripts (`record-feature-video.sh`: Spring `/actuator/info`, Angular router)
    and the Running-app/JDBC wording in `shared/commands.py`.
  - The UX audit's UI-kit catalogue still lists Angular Material/PrimeNG (inert on React).
  - The film step's `steps.video.app.reset` (resets the stack before recording).

  Each is reworked or dropped when its tab is picked up.
- **Postponed:** IntelliJ plugin, Code City, Demo film, UX audit.
- **Known limits** (from `ludvig.md`):
  - The derived OpenAPI misses fields read deep in services.
  - `hr_genseq` does not see `aiohttp` or mocks.
  - The Complexity call graph cannot bind every untyped receiver.
