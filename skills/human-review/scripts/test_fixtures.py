"""DB fixture colours and the fixture each E2E test starts from (hrbuild/shared/fixtures.py)."""
import json
import subprocess
from pathlib import Path

from hrbuild.shared import fixtures as fx

GLUE = """import {Given, When} from '@cucumber/cucumber';
// a comment that says seed must not count
Given('the clinic has these owners', async function () {
  expect(found, 'is the DB seeded?').toBeTruthy();
});
Given('a pet born on {word}', async function (d) { await axios.post('/owners', {}); });
Given('the green household', async function () { await post('/__reset/green'); });
When('I search owners for {string}', async function (s) {});
"""

FEATURE = """Feature: owners
  Background:
    Given the clinic has these owners

  Scenario: Search
    When I search owners for "Pot"

  @fixture:green
  Scenario: Tagged
    When I search owners for "x"
"""

OWN_DATA = """Feature: own
  Background:
    Given a pet born on 2020-01-01

  Scenario: Makes its own rows
    When I search owners for "a"

  Scenario: Loads green itself
    Given the green household
"""

SPEC = """import {test} from './support/trace-fixture';
import * as dsl from './my.dsl';

test('loads green through the DSL', async () => {
  await dsl.owner();
});
"""

DSL = """// the seed's owner 1
export async function owner() { await post('/__reset/green'); }
"""

OWN_SPEC = """import {test} from './support/trace-fixture';
// Mentions the seed only in a comment.
test('creates its own owner', async () => {
  await axios.post('/owners', {});
});
test('asks for blue explicitly', async () => {
  await loadFixture('blue');
});
"""


def repo(tmp_path: Path, colours=None) -> Path:
    files = {
        "be/db/fixtures/green.sql": "-- green",
        "be/db/fixtures/blue.sql": "-- blue",
        "e2e/src/owners.feature": FEATURE,
        "e2e/src/own.feature": OWN_DATA,
        "e2e/src/owners.glue.ts": GLUE,
        "e2e/src/a.spec.ts": SPEC,
        "e2e/src/my.dsl.ts": DSL,
        "e2e/src/b.spec.ts": OWN_SPEC,
    }
    if colours is not None:
        files["be/db/fixtures/fixture-colors.json"] = json.dumps(colours)
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    return tmp_path


def test_an_unconfigured_fixture_wears_the_colour_its_name_hashes_to(tmp_path):
    reg = fx.fixture_colors(repo(tmp_path))
    assert reg["seed"] == fx.FIXTURE_SEED
    assert reg["colors"] == {n: fx.fixture_hue(n, fx.FIXTURE_PALETTE) for n in ("blue", "green")}


def test_the_hash_is_stable_across_processes():
    # `hash()` would differ per run (PYTHONHASHSEED); fixtures.js mirrors this arithmetic.
    h = 0
    for c in "minimal":
        h = (h * 31 + ord(c)) & 0xFFFFFFFF
    assert fx.fixture_hue("minimal", fx.FIXTURE_PALETTE) == fx.FIXTURE_PALETTE[h % 7]


def test_configured_colour_wins_and_the_palette_skips_it(tmp_path):
    reg = fx.fixture_colors(repo(tmp_path, {"green": fx.FIXTURE_PALETTE[0], "seed": "#777777"}))
    assert reg["seed"] == "#777777"
    assert reg["colors"]["green"] == fx.FIXTURE_PALETTE[0]
    assert reg["colors"]["blue"] == fx.fixture_hue("blue", fx.FIXTURE_PALETTE[1:])


def test_a_colour_that_is_not_one_is_ignored(tmp_path):
    reg = fx.fixture_colors(repo(tmp_path, {"green": "red;}</style><script>"}))
    assert reg["colors"]["green"] in fx.FIXTURE_PALETTE


def test_each_test_starts_from_what_its_code_says(tmp_path):
    tests = fx.fixtures_by_test(repo(tmp_path))
    assert tests["e2e/src/owners.feature:9"] == "green"   # a tag on the scenario
    # A scenario whose steps load the fixture through the reset endpoint.
    assert tests["e2e/src/own.feature:8"] == "green"
    # Through an imported DSL, whose code loads it.
    assert tests["e2e/src/a.spec.ts:4"] == "green"
    assert tests["e2e/src/b.spec.ts:6"] == "blue"


def test_a_test_that_declares_no_fixture_starts_empty_and_gets_no_dot(tmp_path):
    """Victor, 21 Oct 2026: a test that does not use a DB fixture starts on an empty
    database. Leaning on seeded rows without saying so is not read as the seed any more —
    the Background below asserts "is the DB seeded?" and still gets nothing."""
    reg = fx.fixture_registry(repo(tmp_path))
    for k in ("e2e/src/owners.feature:5", "e2e/src/own.feature:5", "e2e/src/b.spec.ts:3"):
        assert k not in reg["tests"], k
    assert set(reg) == {"seed", "colors", "palette", "tests", "data"}, "no hollow or dashed marks"


def test_a_feature_tag_and_a_default_name_the_seed(tmp_path):
    root = repo(tmp_path)
    (root / "e2e/src/all.feature").write_text(
        "@fixture:default\nFeature: all\n\n  Scenario: one\n    When I search owners for \"a\"\n\n"
        "  @fixture:green\n  Scenario: two\n    When I search owners for \"b\"\n")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    tests = fx.fixtures_by_test(root)
    assert tests["e2e/src/all.feature:4"] == "seed"
    assert tests["e2e/src/all.feature:8"] == "green"       # the scenario's own tag wins


USE_SPEC = """import {test} from './support/trace-fixture';
test.use({dbFixture: 'default'});

test('on the file default', async () => {});

test.describe('green ones', () => {
  test.use({dbFixture: 'green', viewport: {width: 1, height: 1}});
  test('on green', async () => {});
  test.describe('inner', () => {
    test.use({viewport: {width: 2, height: 2}});
    test('still green', async () => {});
  });
});
"""

SCOPED_SPEC = """import {test} from './support/trace-fixture';
test('empty', async () => {});
test.describe('seeded', () => {
  test.use({dbFixture: 'default'});
  test('on the seed', async () => {});
});
test('empty again', async () => {});
"""


def test_a_playwright_dbfixture_option_is_read_in_the_scope_it_is_used_in(tmp_path):
    root = repo(tmp_path)
    (root / "e2e/src/use.spec.ts").write_text(USE_SPEC)
    (root / "e2e/src/scoped.spec.ts").write_text(SCOPED_SPEC)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    tests = fx.fixtures_by_test(root)
    assert tests["e2e/src/use.spec.ts:4"] == "seed"
    assert tests["e2e/src/use.spec.ts:8"] == "green"        # the describe's own wins
    assert tests["e2e/src/use.spec.ts:11"] == "green"       # a use naming no fixture is skipped
    assert tests["e2e/src/scoped.spec.ts:5"] == "seed"
    assert "e2e/src/scoped.spec.ts:2" not in tests and "e2e/src/scoped.spec.ts:7" not in tests


def test_registry_block_is_script_safe_and_absent_without_fixtures(tmp_path):
    block = fx.render_fixture_registry(repo(tmp_path))
    assert block.startswith('<script type="application/json" id="hr-fixtures">')
    assert "</" not in block[len('<script'):-len("</script>")]
    empty = tmp_path / "empty"
    empty.mkdir()
    subprocess.run(["git", "init", "-q", str(empty)], check=True)
    assert fx.render_fixture_registry(empty) == ""


def test_the_script_words_the_tip_and_the_dot_opens_nothing():
    js = (Path(fx.__file__).resolve().parent.parent / "assets" / "fixtures.js").read_text()
    assert "'DB Fixture: ' + (name === 'seed' ? 'Default' : name)" in js
    assert "hrOpenDataset" not in js and "click to view" not in js, "no dataset view to open"
    assert "MutationObserver" in js
    assert "fx-free" not in js and "fx-empty" not in js, "no dot is how a test says it starts empty"


# ── pytest: the tests/data sets a Python test loads ─────────────────────────

ROOT_CONFTEST = '''import pytest

@pytest.fixture(autouse=True)
def fake_api(monkeypatch):
    def offer():
        with open("tests/data/service_matrix.json") as f:
            return f.read()
    monkeypatch.setattr("x.offer", offer)
'''

ONBOARDING_CONFTEST = '''import pytest
from tests.utils.load import load_data_from_csv

@pytest.fixture
def customers(aurora):
    load_data_from_csv(aurora.session, 'tests/data/ML-1570/customer_fiscal_year.csv', X)
    load_data_from_csv(aurora.session, 'tests/data/onboarding_update/customers.csv', Y)
'''

ONBOARDING_TESTS = '''import pytest
from tests.utils.load import load_facts

DEADLINES = Path('tests/data/facts/deviating_deadlines.json')


def test_onboards(customers):
    assert True


def test_reads_the_deadlines():
    assert DEADLINES.read_text()


@pytest.mark.usefixtures("customers")
class TestRegeneration:
    def test_regenerates(self):
        load_facts()

    def helper(self):
        pass


def test_plain(fake_api):
    assert True
'''

LOAD = '''def load_facts():
    return open("tests/data/facts/activity_facts.csv").read()

def load_data_from_csv(session, path, model):
    pass
'''


def _py_repo(tmp_path):
    files = {
        "conftest.py": ROOT_CONFTEST,
        "tests/features/onboarding/conftest.py": ONBOARDING_CONFTEST,
        "tests/features/onboarding/test_onboarding.py": ONBOARDING_TESTS,
        "tests/utils/load.py": LOAD,
        "tests/data/service_matrix.json": "{}",
        "tests/data/ML-1570/customer_fiscal_year.csv": "a",
        "tests/data/onboarding_update/customers.csv": "a",
        "tests/data/facts/deviating_deadlines.json": "{}",
        "tests/data/facts/activity_facts.csv": "a",
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    return tmp_path, sorted(files)


def test_a_pytest_test_wears_the_test_data_it_loads_beyond_the_shared_baseline(tmp_path):
    root, files = _py_repo(tmp_path)
    assert fx.py_data_roots(files) == ["tests/data"]
    got = fx.pytest_data_by_test(root, tuple(files))
    t = "tests/features/onboarding/test_onboarding.py"
    assert got == {
        f"{t}:7": "ML-1570 + onboarding_update",     # through the fixture it requests
        f"{t}:11": "facts",                          # a module constant it names
        f"{t}:17": "facts + ML-1570 + onboarding_update",  # usefixtures + a helper it calls
    }, "test_plain reads only the autouse baseline (service_matrix): no dot"


def test_the_registry_colours_and_labels_test_data(tmp_path):
    root, files = _py_repo(tmp_path)
    reg = fx.fixture_colors(root, files)
    assert "ML-1570 + onboarding_update" in reg["colors"]
    assert reg["data"] == ["ML-1570 + onboarding_update", "facts",
                           "facts + ML-1570 + onboarding_update"]
    js = (Path(fx.__file__).resolve().parent.parent / "assets" / "fixtures.js").read_text()
    assert "'Test data: ' + name" in js


def test_no_data_folder_no_scan(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_x.py").write_text("def test_x():\n    open('tests/data/a.json')\n")
    assert fx.pytest_data_by_test(tmp_path, ("tests/test_x.py",)) == {}
