#!/usr/bin/env python3
"""What `test-changes.py` promises: a test case's fate, read off the diff.

The classification is the whole point of the script — a reviewer reads a new test and a
tweaked one differently — so the cases below pin each of the four states, including the
two that are easy to get wrong: a *new test inside an existing file* (which `git diff
--name-status` calls `M`, and which must not be reported as "modified"), and a deleted
test, which has no line of its own left and has to borrow the place its removal landed.

Run with:  python3 -m pytest test_test_changes.py
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent

_spec = importlib.util.spec_from_file_location("test_changes", HERE / "test-changes.py")
tc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tc)


# --------------------------------------------------------------------------- #
# which files are tests at all
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("rel", [
    "activities-backend/tests/unit/test_visit.py",
    "petclinic-test/src/add-visit.spec.ts",
    "petclinic-test/features/add-visit.feature",
    "docs/scripts/db/test_db_schema_to_puml.py",
    "internal/store/store_test.go",
])
def test_a_test_file_is_recognised_by_where_it_sits_or_what_it_is_called(rel):
    assert tc.is_test_file(rel)


@pytest.mark.parametrize("rel", [
    "activities-backend/src/activities/handler.py",
    "petclinic-frontend/src/app/visits/vet-name.pipe.ts",
    "README.md",
])
def test_production_code_is_not_swept_in(rel):
    assert not tc.is_test_file(rel)


# --------------------------------------------------------------------------- #
# finding the test cases
# --------------------------------------------------------------------------- #
def test_playwright_and_jest_cases_come_back_by_their_written_title():
    src = ("test('Add a visit attended by a vet', async ({ page }) => {\n"
           "});\n"
           "it.each([1])('renders the attending vet', () => {});\n")
    cases = tc.test_cases("add-visit.spec.ts", src)
    assert cases == {"Add a visit attended by a vet": 1, "renders the attending vet": 3}


def test_an_escaped_quote_does_not_end_a_js_title():
    """Eval run 12: `it('… the newer one\\'s loading')` came back as `… the newer one\\`,
    matched no coverage row, and the spec fell out of the matrix (a false "partly"). The
    title runs to the first unescaped quote and is decoded as the runner reports it —
    template literals and double quotes alike."""
    src = ("  it('an older request completing does not end the newer one\\'s loading', () => {\n"
           "  });\n"
           '  it("says \\"hi\\" back", () => {});\n'
           "  test(`a \\` tick and ${n} kept`, () => {});\n"
           "  it('a \\\\ backslash', () => {});\n")
    assert tc.test_cases("owner-list.component.spec.ts", src) == {
        "an older request completing does not end the newer one's loading": 1,
        'says "hi" back': 3,
        "a ` tick and ${n} kept": 4,
        "a \\ backslash": 5,
    }


def test_python_go_and_gherkin_each_have_a_shape():
    assert tc.test_cases("test_x.py", "def helper():\n    pass\ndef test_one():\n    pass\n") \
        == {"test_one": 3}
    assert tc.test_cases("x_test.go", "func helper() {}\nfunc TestOne(t *testing.T) {}\n") \
        == {"TestOne": 2}
    assert tc.test_cases("a.feature", "Feature: x\n  Scenario: A visit remembers the vet\n") \
        == {"A visit remembers the vet": 2}


# --------------------------------------------------------------------------- #
# reading the diff
# --------------------------------------------------------------------------- #
def test_hunk_lines_reports_added_lines_and_where_removals_landed():
    diff = (
        "--- a/x.py\n"
        "+++ b/x.py\n"
        "@@ -10,0 +11,2 @@\n"
        "+  one\n"
        "+  two\n"
        "@@ -30,2 +32,0 @@\n"
        "-  gone one\n"
        "-  gone two\n"
    )
    added, removed = tc.hunk_lines(diff)
    assert added == {11, 12}
    # Both removals sat where line 32 of the new file now is — the place a reader opens
    # to see the gap.
    assert removed == {30: 32, 31: 32}


def test_the_file_header_is_not_mistaken_for_an_added_line():
    added, removed = tc.hunk_lines("--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n")
    assert added == {1} and removed == {1: 1}


# --------------------------------------------------------------------------- #
# the four states
# --------------------------------------------------------------------------- #
BEFORE = """class TestVisit:
    def test_update_ok(self):
        old()

    def test_delete_ok(self):
        pass

    def test_obsolete(self):
        pass
"""

AFTER = """class TestVisit:
    def test_update_ok(self):
        fresh()

    def test_delete_ok(self):
        pass

    def test_create_with_vet(self):
        pass
"""


def _rows(status="M"):
    added, removed = tc.hunk_lines(
        "--- a/test_visit.py\n+++ b/test_visit.py\n"
        "@@ -3 +3 @@\n-        old()\n+        fresh()\n"
        "@@ -8 +8 @@\n-    def test_obsolete(self):\n+    def test_create_with_vet(self):\n"
    )
    return {r["name"]: r for r in
            tc.classify_file("test_visit.py", status, BEFORE, AFTER, added, removed)}


def test_a_new_test_inside_a_modified_file_is_added_not_modified():
    """The whole reason this works per test case rather than per file: `--name-status`
    calls the file `M`, and reporting that would bury the row the reviewer came for."""
    assert _rows()["test_create_with_vet"]["status"] == "added"
    assert _rows()["test_create_with_vet"]["line"] == 8


def test_a_test_whose_body_the_diff_touched_is_modified():
    assert _rows()["test_update_ok"]["status"] == "modified"


def test_a_test_the_diff_never_reached_is_unchanged():
    assert _rows()["test_delete_ok"]["status"] == "unchanged"


LAST_BEFORE = """class TestProxy:
    def test_first(self):
        ok()

    @pytest.mark.parametrize("n", [1, 2])
    def test_sizes(self, n):
        ok()

    def helper(self):
        old()

    def test_owner_search_through_proxy(self, client):
        response = client.get("/api/owners")
        assert response.status_code == 200
        assert len(response.json()) >= 10
"""


def test_a_body_edit_under_an_unchanged_signature_is_modified_even_in_the_last_test():
    """Eval run 8: OwnerSearchThroughLatencyProxyTest's only test had its assertion
    rewritten and was reported `unchanged` — the last test's body was taken to be its
    declaration line alone. A decorator edit belongs to the test it decorates, and a
    helper edited between two tests belongs to neither."""
    after = (LAST_BEFORE.replace("[1, 2]", "[1, 2, 3]")
             .replace("old()", "fresh()")
             .replace("        assert len(response.json()) >= 10\n",
                      '        assert len(response.json()["content"]) == 10\n'
                      '        assert response.json()["totalElements"] >= 10\n'))
    added, removed = tc.hunk_lines(_unified0(LAST_BEFORE, after))
    rows = {r["name"]: r["status"] for r in
            tc.classify_file("test_proxy.py", "M", LAST_BEFORE, after, added, removed)}
    assert rows == {"test_first": "unchanged", "test_sizes": "modified",
                    "test_owner_search_through_proxy": "modified"}


@pytest.mark.parametrize("rel, text, line, span", [
    ("a.spec.ts", "describe('x', () => {\n  it('a', () => {\n    expect(1).toBe(1);\n  });\n"
                  "\n  const helper = () => 1;\n});\n", 2, (2, 4)),
    ("test_a.py", "@pytest.mark.slow\ndef test_a():\n    x = 1\n\n    assert x\n\ndef helper():\n"
                  "    pass\n", 2, (1, 5)),
    ("a.feature", "Feature: f\n  @smoke\n  Scenario: one\n    Given x\n    Then y\n\n  @wip\n", 3,
     (2, 5)),
])
def test_a_case_s_own_lines_end_where_its_body_does(rel, text, line, span):
    assert tc.case_span(rel, text.splitlines(), line, None) == span


def test_a_deleted_test_keeps_a_line_to_open_in_the_surviving_file():
    row = _rows()["test_obsolete"]
    assert row["status"] == "deleted"
    assert row["line"] == 8 and not row.get("gone")


def test_a_test_in_a_file_that_was_deleted_outright_says_there_is_nothing_to_open():
    rows = {r["name"]: r for r in
            tc.classify_file("test_visit.py", "D", BEFORE, None, set(), {})}
    assert rows["test_update_ok"] == {"name": "test_update_ok", "path": "test_visit.py",
                                      "status": "deleted", "line": None, "gone": True,
                                      "baseLine": 2}


def test_every_case_of_an_added_file_is_added():
    rows = tc.classify_file("test_new.py", "A", None, AFTER, {1, 2, 3}, {})
    assert {r["status"] for r in rows} == {"added"}


# --------------------------------------------------------------------------- #
# a test edited through a helper it calls
# --------------------------------------------------------------------------- #
# Eval run 10: AddVisitApiTest kept every line of its own while `anOwnerWithAPet()`, the
# private helper it calls, was rewritten from one unpaged GET into a page-walking loop —
# and the test was counted among those "left exactly as they were".
HELPER_BEFORE = """class TestAddVisitApi:
    def test_adds_a_visit(self):
        owner = an_owner_with_a_pet()
        post(owner)

    def test_lists_vets(self):
        get("/api/vets")

    def test_via_another_helper(self):
        wrapper()

    def test_by_reference(self):
        list(map(an_owner_with_a_pet, [1]))


def wrapper():
    keep()


def an_owner_with_a_pet():
    return first(get("/api/owners"))
"""


def _helper_rows():
    after = HELPER_BEFORE.replace(
        '    return first(get("/api/owners"))\n',
        '    for page in itertools.count():\n'
        '        owners = get(f"/api/owners?page={page}")["content"]\n'
        '        if owners:\n'
        '            return first(owners)\n')
    added, removed = tc.hunk_lines(_unified0(HELPER_BEFORE, after))
    return {r["name"]: r for r in
            tc.classify_file("test_add_visit_api.py", "M", HELPER_BEFORE, after, added, removed)}


def test_a_test_whose_same_file_helper_was_rewritten_is_edited_via_that_helper():
    rows = _helper_rows()
    assert rows["test_adds_a_visit"]["status"] == "modified"
    assert rows["test_adds_a_visit"]["viaHelper"] == [
        {"name": "an_owner_with_a_pet", "line": 20, "added": 4, "removed": 1}]
    assert rows["test_lists_vets"]["status"] == "unchanged"
    assert "viaHelper" not in rows["test_lists_vets"]


def test_only_a_direct_call_makes_a_helper_edit_the_test_s():
    """Conservative: a helper reached through another helper, or passed by name rather
    than called, is not a call written in the test — the page would be guessing."""
    rows = _helper_rows()
    assert rows["test_via_another_helper"]["status"] == "unchanged"
    assert rows["test_by_reference"]["status"] == "unchanged"


def test_an_edit_through_a_helper_is_counted_with_the_edited_and_on_its_own():
    t = tc.totals(list(_helper_rows().values()))
    assert t["modified"] == 1 and t["viaHelper"] == 1 and t["unchanged"] == 3


def test_a_playwright_spec_s_helper_function_counts_too():
    before = ("async function addVisit(page) {\n  await page.click('#add');\n}\n\n"
              "test('books a visit', async ({ page }) => {\n  await addVisit(page);\n});\n\n"
              "test('lists vets', async ({ page }) => {\n  await page.goto('/vets');\n});\n")
    after = before.replace("await page.click('#add');",
                           "await page.click('#add');\n  await page.fill('#vet', 'Helen');")
    added, removed = tc.hunk_lines(_unified0(before, after))
    rows = {r["name"]: r for r in
            tc.classify_file("add-visit.spec.ts", "M", before, after, added, removed)}
    assert rows["books a visit"]["status"] == "modified"
    assert rows["books a visit"]["viaHelper"][0]["name"] == "addVisit"
    assert rows["lists vets"]["status"] == "unchanged"


# --------------------------------------------------------------------------- #
# end to end, against a real repository
# --------------------------------------------------------------------------- #
def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


def test_it_reads_a_real_branch(tmp_path):
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    f = repo / "tests" / "test_visit.py"
    _git_init = ["git", "init", "-q", "-b", "main", str(repo)]
    subprocess.run(_git_init, check=True, capture_output=True)
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    f.write_text(BEFORE)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    base = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    f.write_text(AFTER)
    _git(repo, "commit", "-qam", "change")

    rows = {r["name"]: r["status"] for r in tc.collect(repo, base, [])}
    assert rows == {"test_update_ok": "modified", "test_delete_ok": "unchanged",
                    "test_create_with_vet": "added", "test_obsolete": "deleted"}


def test_the_cli_writes_the_manifest_the_page_reads(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True, capture_output=True)
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "tests" / "a.spec.ts").write_text("it('one', () => {});\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    (repo / "tests" / "a.spec.ts").write_text("it('one', () => {});\nit('two', () => {});\n")

    out = tmp_path / "assets" / "test-changes.json"
    monkeypatch.chdir(repo)
    assert tc.main(["--base", "HEAD", "--out", str(out)]) == 0
    doc = json.loads(out.read_text())
    assert doc["base"] == "HEAD"
    assert {t["name"]: t["status"] for t in doc["tests"]} == {"one": "unchanged", "two": "added"}


# --------------------------------------------------------------------------- #
# the tests that are still written and no longer run
# --------------------------------------------------------------------------- #
# Deletion is the loud way to lose a test and the only one a diff makes obvious. These
# pin the two quiet ways, in every language the script claims to read, because a chip
# that says "2 lost" while three more sit under a skip marker is worse than no chip.
SILENCED = {
    "visits.spec.ts": ("""describe('visits', () => {
  it('adds one', () => {});
  it.skip('is skipped', () => {});
  xit('is x-skipped', () => {});
});
describe.skip('vets', () => {
  it('sits in a skipped suite', () => {});
});
""", {"is skipped", "is x-skipped", "sits in a skipped suite"}, {"adds one"}),
    "test_visits.py": ("""@pytest.mark.skip(reason="flaky")
def test_off(): pass

def test_on(): pass

@pytest.mark.skipif(SLOW, reason="slow")
class TestGroup:
    def test_in_a_skipped_class(self): pass
""", {"test_off", "test_in_a_skipped_class"}, {"test_on"}),
    "store_test.go": ("""func TestOff(t *testing.T) {
\tt.Skip("needs docker")
}
func TestOn(t *testing.T) {
\tok()
}
""", {"TestOff"}, {"TestOn"}),
    "add-visit.feature": ("""Feature: visits

  @wip
  Scenario: parked
    Given a

  Scenario: live
    Given b
""", {"parked"}, {"live"}),
}


@pytest.mark.parametrize("rel", sorted(SILENCED))
def test_a_test_that_is_still_written_but_switched_off_says_so(rel):
    text, off, on = SILENCED[rel]
    scanned = tc.scan_cases(rel, text)
    assert {n for n, (_, why) in scanned.items() if why} == off
    assert {n for n, (_, why) in scanned.items() if not why} == on


def test_a_tag_on_the_feature_reaches_every_scenario_under_it():
    """Feature-level is the case worth pinning: one `@wip` at the top of the file turns
    off scenarios that carry no marker of their own anywhere near them."""
    scanned = tc.scan_cases("x.feature", "@wip\nFeature: x\n\n  Scenario: a\n    Given b\n")
    assert scanned["a"][1] == "disabled"


@pytest.mark.parametrize("rel, text, name, line", [
    ("visits.spec.ts", "describe('x', () => {\n// it('parked', () => {});\n});\n", "parked", 2),
    ("test_visits.py", "# def test_parked():\n#     pass\n", "test_parked", 1),
])
def test_a_test_that_exists_only_inside_a_comment_is_found(rel, text, name, line):
    """Commenting a test out costs the run exactly as much as deleting it, and costs the
    diff nothing — the body is still there, so it reads as kept. The line is the one it
    occupies on disk, so the row stays clickable straight to the comment."""
    assert tc.commented_cases(rel, text) == {name: line}
    assert name not in tc.scan_cases(rel, text)


def test_live_code_is_not_reported_as_commented_out():
    assert tc.commented_cases("test_t.py", "def test_runs():\n    pass\n") == {}


# --------------------------------------------------------------------------- #
# what the diff did to the run, not just to the file
# --------------------------------------------------------------------------- #
RUNNING = """def test_kept():
    pass


def test_about_to_be_disabled():
    pass


def test_about_to_be_commented():
    pass


@pytest.mark.skip
def test_about_to_come_back():
    pass
"""

SILENT = """def test_kept():
    pass


@pytest.mark.skip
def test_about_to_be_disabled():
    pass


# def test_about_to_be_commented():
#     pass


def test_about_to_come_back():
    pass
"""


def _silenced_rows():
    return {r["name"]: r for r in
            tc.classify_file("test_visit.py", "M", RUNNING, SILENT, set(), {})}


def test_a_test_left_in_place_under_a_skip_marker_is_flagged_where_it_stands():
    row = _silenced_rows()["test_about_to_be_disabled"]
    assert row["silenced"] == "disabled" and not row.get("wasSilenced")
    assert row["status"] != "deleted", "it is still declared; it just does not run"


def test_a_commented_out_test_is_a_deletion_that_can_still_be_opened():
    row = _silenced_rows()["test_about_to_be_commented"]
    assert row["status"] == "deleted" and row["silenced"] == "commented"
    assert row["line"] == 10, "the comment itself — the thing the reviewer has to judge"


def test_a_test_switched_back_on_says_where_it_came_from():
    row = _silenced_rows()["test_about_to_come_back"]
    assert row["wasSilenced"] == "disabled" and not row.get("silenced")


def test_the_totals_reconcile_with_the_rows_behind_them():
    """The chip states `+gained / −lost`, and a reader is entitled to assume those two
    numbers are the difference between the run before and the run after. They are, by
    construction — this is the arithmetic that says so."""
    t = tc.totals(list(_silenced_rows().values()))
    assert t["runningBefore"] == 3 and t["runningAfter"] == 2
    assert t["gained"] == 1 and t["lost"] == 2
    assert t["runningAfter"] - t["runningBefore"] == t["gained"] - t["lost"]
    assert t["disabled"] == 1 and t["commented"] == 1 and t["reenabled"] == 1


def test_deleting_a_test_nobody_was_running_moves_nothing():
    """A skipped test that this change set finally removes is housekeeping, not a
    loss: the run did not have it before and does not have it now."""
    t = tc.totals(tc.classify_file(
        "test_t.py", "M", "import pytest\n\n\n@pytest.mark.skip\ndef test_dead():\n    pass\n",
        "import pytest\n", set(), {4: 1, 5: 1, 6: 1}))
    assert t["deleted"] == 1 and t["lost"] == 0
    assert t["runningBefore"] == 0 and t["runningAfter"] == 0


# --------------------------------------------------------------------------- #
# the key is the name: a test retitled is one gone and one new
# --------------------------------------------------------------------------- #
# Run 6: owner-search.feature:26 'Searching with an empty last name lists every owner'
# became '… shows the first page of every owner' — same line, same @generate_sequence tag,
# its last step rewritten. Runs 6 to 12 paired such a retitle into one `modified` row with
# heuristics on place, tags and body; Victor (3 Oct 2026): keep it as simple as possible.
# A name at the base and not at HEAD is deleted, a name at HEAD and not at the base is new,
# and the reader sees both halves — the deleted one opens as it was at the base commit.
FEATURE_BEFORE = """Feature: Search owners

  Scenario: Search by prefix
    When I search owners for "Pot"
    Then Harry Potter is listed

  @generate_sequence
  Scenario: Searching with an empty last name lists every owner
    When I open the owners page
    And I search owners for ""
    Then every owner in the clinic is listed

  Scenario: Retired scenario
    When I open the vets page
    Then the vets are listed
"""

FEATURE_AFTER = """Feature: Browse owners

  Scenario: Search by prefix
    When I search owners for "Pot"
    Then Harry Potter is listed

  @generate_sequence
  Scenario: Searching with an empty last name shows the first page of every owner
    When I open the owners page
    And I search owners for ""
    Then the first 10 owners by "name,asc" are listed, in order
    And the range reads "1 – 10" of every owner in the clinic

  Scenario: Every owner is reachable page by page
    When I open the owners page
    And I walk to the last page
    Then every owner was listed once
"""


def _git_repo(tmp_path, rel, before, after, remote=None):
    repo = tmp_path / "repo"
    (repo / Path(rel).parent).mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True,
                   capture_output=True)
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    if remote:
        _git(repo, "remote", "add", "origin", remote)
    (repo / rel).write_text(before)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    base = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    (repo / rel).write_text(after)
    return repo, base


def test_a_scenario_retitled_in_place_is_one_deleted_and_one_new(tmp_path):
    rel = "features/owner-search.feature"
    repo, base = _git_repo(tmp_path, rel, FEATURE_BEFORE, FEATURE_AFTER)
    rows = {r["name"]: r for r in tc.collect(repo, base, [])}
    new = rows["Searching with an empty last name shows the first page of every owner"]
    assert new["status"] == "added" and new["line"] == 8
    old = rows["Searching with an empty last name lists every owner"]
    assert old["status"] == "deleted" and old["baseLine"] == 8 and old["baseSha"] == base
    assert rows["Retired scenario"]["status"] == "deleted"
    assert rows["Every owner is reachable page by page"]["status"] == "added"
    assert not any(k in r for r in rows.values() for k in ("renamedFrom", "rewrittenFrom"))
    t = tc.totals(list(rows.values()))
    assert (t["added"], t["modified"], t["deleted"]) == (2, 0, 2)
    assert "renamed" not in t and "rewritten" not in t
    assert (t["gained"], t["lost"]) == (2, 2)
    assert t["runningAfter"] - t["runningBefore"] == t["gained"] - t["lost"]


def test_a_method_renamed_with_its_body_kept_is_deleted_and_added():
    """However much of the body it kept: the name is the key, nothing else."""
    before = "def test_x():\n    a = owner()\n    a.save()\n    assert a.id > 0\n"
    after = before.replace("def test_x()", "def test_save_assigns_an_id()")
    added, removed = tc.hunk_lines(_unified0(before, after))
    rows = tc.classify_file("test_t.py", "M", before, after, added, removed)
    assert rows == [{"name": "test_save_assigns_an_id", "path": "test_t.py",
                     "status": "added", "line": 1},
                    {"name": "test_x", "path": "test_t.py", "status": "deleted", "line": 1,
                     "baseLine": 1}]


def test_the_same_name_with_its_own_lines_changed_is_edited():
    before = "def test_x():\n    a = owner()\n    assert a.id > 0\n"
    after = before.replace("> 0", "== 1")
    added, removed = tc.hunk_lines(_unified0(before, after))
    assert tc.classify_file("test_t.py", "M", before, after, added, removed) == [
        {"name": "test_x", "path": "test_t.py", "status": "modified", "line": 1}]


def _unified0(before: str, after: str) -> str:
    import difflib
    out = ["--- a/f", "+++ b/f"]
    sm = difflib.SequenceMatcher(None, before.splitlines(), after.splitlines(), autojunk=False)
    b, a = before.splitlines(), after.splitlines()
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        out.append(f"@@ -{i1 + (i2 == i1 and 0 or 1)},{i2 - i1} "
                   f"+{j1 + (j2 == j1 and 0 or 1)},{j2 - j1} @@")
        out += ["-" + x for x in b[i1:i2]] + ["+" + x for x in a[j1:j2]]
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- #
# a deleted test is linked where it stood at the base, never at HEAD
# --------------------------------------------------------------------------- #
def test_a_deleted_test_carries_its_base_line_and_a_blob_url_at_the_base_commit(tmp_path):
    """Run 6: 'should create OwnerListComponent' linked owner-list.component.spec.ts:100,
    where HEAD has `describe('initial state'` — the test was at line 89 of the base."""
    rel = "features/owner-search.feature"
    repo, base = _git_repo(tmp_path, rel, FEATURE_BEFORE, FEATURE_AFTER,
                           remote="git@github.com:acme/clinic.git")
    gone = next(r for r in tc.collect(repo, base, []) if r["name"] == "Retired scenario")
    assert gone["status"] == "deleted" and gone["baseLine"] == 13
    assert gone["baseSha"] == base
    assert gone["baseUrl"] == f"https://github.com/acme/clinic/blob/{base}/{rel}#L13"


def test_without_a_github_remote_a_deleted_test_gets_no_guessed_url(tmp_path):
    rel = "features/owner-search.feature"
    repo, base = _git_repo(tmp_path, rel, FEATURE_BEFORE, FEATURE_AFTER)
    gone = next(r for r in tc.collect(repo, base, []) if r["name"] == "Retired scenario")
    assert gone["status"] == "deleted" and gone["baseLine"] == 13 and gone["baseSha"] == base and "baseUrl" not in gone


@pytest.mark.parametrize("remote, repo", [
    ("git@github.com:acme/clinic.git", "https://github.com/acme/clinic"),
    ("https://github.com/acme/clinic.git", "https://github.com/acme/clinic"),
    ("https://github.com/acme/clinic", "https://github.com/acme/clinic"),
    ("ssh://git@github.com/acme/clinic.git\n", "https://github.com/acme/clinic"),
    ("https://gitlab.com/acme/clinic.git", "https://gitlab.com/acme/clinic"),
    ("https://code.example.org/acme/clinic.git", ""),
    ("", ""),
])
def test_only_a_known_forge_s_remote_becomes_a_blob_url(remote, repo):
    assert tc.forge_repo(remote) == repo


# --------------------------------------------------------------------------- #
# a test is edited by a change to its own lines, never by its neighbour's
# --------------------------------------------------------------------------- #
# Eval run 11: owner.service.spec.ts lost its last test, 'search owners by last name prefix',
# and the blank line above it. `--unified=0` lands a pure deletion on the line *before* the
# gap — the closing `});` of 'delete Owner', whose body was byte-identical — and the ledger
# filed 'delete Owner' under edited.
SERVICE_BEFORE = """describe('OwnerService', () => {
  function ownerUrl(id) {
    return '/api/owners/' + id;
  }

  it('delete Owner', () => {
    ownerService.deleteOwner('1').subscribe();
    const req = http.expectOne(ownerUrl(1));
    expect(req.request.method).toEqual('DELETE');
  });

  it('search owners by last name prefix', () => {
    ownerService.searchOwners('Fr').subscribe();
    const req = http.expectOne('/api/owners?lastName=Fr');
    expect(req.request.method).toEqual('GET');
  });
});
"""


def test_deleting_the_next_test_does_not_edit_the_one_above_it(tmp_path):
    after = SERVICE_BEFORE.replace(SERVICE_BEFORE[SERVICE_BEFORE.index(
        "\n  it('search owners"):SERVICE_BEFORE.index("});\n", SERVICE_BEFORE.index(
            "it('search owners")) + 4], "")
    assert "search owners" not in after and after.endswith("  });\n});\n")
    rel = "src/app/owner.service.spec.ts"
    repo, base = _git_repo(tmp_path, rel, SERVICE_BEFORE, after)
    rows = {r["name"]: r for r in tc.collect(repo, base, [])}
    assert rows["delete Owner"]["status"] == "unchanged"
    assert rows["search owners by last name prefix"]["status"] == "deleted"


def test_a_helper_right_above_a_deletion_was_not_rewritten_by_it():
    """The same landing, one level down: a helper whose closing brace sits right above a
    deleted test is not a helper this change set rewrote, and the test calling it is not
    edited through it."""
    before = ("async function addVisit(page) {\n  await page.click('#add');\n}\n\n"
              "test('books a visit', async ({ page }) => {\n  await addVisit(page);\n});\n\n"
              "async function gone(page) {\n  await page.goto('/x');\n}\n")
    after = before[:before.index("\nasync function gone")]
    added, removed = tc.hunk_lines(_unified0(before, after))
    rows = {r["name"]: r for r in
            tc.classify_file("v.spec.ts", "M", before, after, added, removed)}
    assert rows["books a visit"]["status"] == "unchanged"
    assert "viaHelper" not in rows["books a visit"]


@pytest.mark.parametrize("rel, text, line, span", [
    ("a.spec.ts", "it('a', () => expect(1).toBe(1));\n\n// the next one\n", 1, (1, 1)),
    ("a.feature", "  Scenario: one\n    Given x\n    # parked: Then y\n\n", 1, (1, 2)),
])
def test_a_case_s_own_lines_never_end_on_a_blank_or_a_comment(rel, text, line, span):
    assert tc.case_span(rel, text.splitlines(), line, None) == span


# --------------------------------------------------------------------------- #
# a test rewritten under a new name is one gone and one new — wherever it moved
# --------------------------------------------------------------------------- #
# Eval run 11: one test kept its subject and moved sixty lines into a new describe, its
# title reworded; another kept its place and its skeleton under a new title. Runs 11 and 12
# paired both on title words, body tokens and statement shape. By name, each is a deletion
# and an addition — the reviewer opens the deleted one at the base and judges the pair.
LIST_BEFORE = """describe('OwnerListComponent', () => {
  it('should create OwnerListComponent', () => {
    expect(component).toBeTruthy();
  });

  it('a search is not overwritten by the initial load answering late', () => {
    const initialLoad = new Subject<Owner[]>();
    getOwnersSpy.and.returnValue(initialLoad);
    fixture.detectChanges();
    component.searchByLastName('Franklin');
    initialLoad.next([testOwner, davis]);
    expect(component.owners).toEqual([testOwner]);
  });

  it('should return expected owners (called once)', () => {
    ownerService.getOwners().subscribe((owners) => expect(owners).toEqual(expectedOwners), fail);
    const req = httpTestingController.expectOne(ownerService.entityUrl);
    expect(req.request.method).toEqual('GET');
    req.flush(expectedOwners);
  });

  it('search owners by last name prefix', () => {
    ownerService.searchOwners('Fr').subscribe((owners) => {
      expect(owners).toEqual(expectedOwners);
    });
    const req = httpTestingController.expectOne(ownerService.entityUrl + '?lastName=Fr');
    expect(req.request.method).toEqual('GET');
    req.flush(expectedOwners);
  });
});
"""

LIST_AFTER = """describe('OwnerListComponent', () => {
  it('lists the first page by name by default, as one typed page', () => {
    ownerService.listOwners().subscribe((page) => expect(page).toEqual(expectedPage), fail);
    const req = httpTestingController.expectOne((r) => r.url === ownerService.entityUrl);
    expect(req.request.method).toEqual('GET');
    expect(req.request.urlWithParams).toEqual(ownerService.entityUrl + '?page=0');
    req.flush(expectedPage);
  });

  it('sends the filter, page, size and sort it is given', () => {
    ownerService.listOwners({ lastName: 'Fr', page: 2 }).subscribe((page) => expect(page).toEqual(expectedPage), fail);
    const req = httpTestingController.expectOne((r) => r.url === ownerService.entityUrl);
    expect(req.request.urlWithParams).toEqual(ownerService.entityUrl + '?lastName=Fr&page=2');
    req.flush(expectedPage);
  });

  describe('only the latest request answers', () => {
    it('so a late initial load does not overwrite a search', async () => {
      await typeAndSubmit('Franklin');
      initialLoad.next(pageOf([george, betty], 26));
      fixture.detectChanges();
      expect(component.page).toEqual(pageOf([george], 1));
    });

    it('so a late failure does not show an error', async () => {
      await typeAndSubmit('Franklin');
      initialLoad.error('boom');
      expect(exists('#ownersError')).toBeFalse();
    });
  });
});
"""


def test_a_test_reworded_moved_or_retitled_is_gone_and_new(tmp_path):
    rel = "src/app/owner-list.component.spec.ts"
    repo, base = _git_repo(tmp_path, rel, LIST_BEFORE, LIST_AFTER)
    rows = {r["name"]: r for r in tc.collect(repo, base, [])}
    assert {n for n, r in rows.items() if r["status"] == "deleted"} == {
        "should create OwnerListComponent",
        "a search is not overwritten by the initial load answering late",
        "should return expected owners (called once)",
        "search owners by last name prefix"}
    assert {n for n, r in rows.items() if r["status"] == "added"} == {
        "lists the first page by name by default, as one typed page",
        "sends the filter, page, size and sort it is given",
        "so a late initial load does not overwrite a search",
        "so a late failure does not show an error"}
    assert all(r["baseLine"] and r["baseSha"] == base
               for r in rows.values() if r["status"] == "deleted")
    t = tc.totals(list(rows.values()))
    assert (t["added"], t["modified"], t["deleted"]) == (4, 0, 4)
    assert (t["gained"], t["lost"]) == (4, 4)
    assert t["runningAfter"] - t["runningBefore"] == t["gained"] - t["lost"]
