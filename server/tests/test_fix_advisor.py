"""A diff that a model writes is only useful if git accepts it.

Two defects were found by running the real Nemotron fix-advisor against a real
clone, and neither is visible in the response shape, only in whether
``git apply --check`` accepts the result:

* ``_clean_diff`` dropped every whitespace-only line. In a unified diff a blank
  context line is a single space and a blank added line is a bare ``+``, so the
  hunk body was silently shortened and the header counts stopped matching.
* The model gets hunk-header arithmetic wrong. Asked to patch Flask's
  ``_lazy_sha1`` it wrote ``@@ -273,11 +273,11 @@`` over a body of eight lines,
  which git rejects as ``corrupt patch`` before it ever opens the file.
"""

from __future__ import annotations

import subprocess
import textwrap

import pytest

from app.core.fix_advisor import _clean_diff, _reanchor


def _patched_clone(tmp_path):
    """A git clone containing the exact lines the live Flask diff was written against."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(
        textwrap.dedent(
            '''\
            session_json_serializer = TaggedJSONSerializer()


            def _lazy_sha1(string: bytes = b"") -> t.Any:
                """Don't access ``hashlib.sha1`` until runtime. FIPS builds may not include
                SHA-1, in which case the import and use as a default would fail before the
                developer can configure something else.
                """
                return hashlib.sha1(string)


            class SecureCookieSessionInterface(SessionInterface):
                pass
            '''
        ),
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    return repo


def _applies(repo, diff: str) -> tuple[bool, str]:
    patch = repo / "candidate.diff"
    patch.write_text(diff, encoding="utf-8")
    done = subprocess.run(
        ["git", "apply", "--check", "--whitespace=nowarn", str(patch)],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    return done.returncode == 0, (done.stderr or done.stdout).strip()


def test_a_blank_context_line_survives_cleaning():
    """A blank context line is one space. Dropping it shortens the hunk."""
    cleaned = _clean_diff(
        "--- a/app.py\n+++ b/app.py\n@@ -1,5 +1,5 @@\n import os\n \n import sys\n-old\n+new\n"
    )

    assert " \n" in cleaned
    assert cleaned.count("\n \n") == 1


def test_a_blank_added_line_survives_cleaning():
    cleaned = _clean_diff("--- a/app.py\n+++ b/app.py\n@@ -1,2 +1,3 @@\n import os\n+\n import sys\n")

    assert "\n+\n" in cleaned


def test_prose_around_the_diff_is_still_stripped():
    cleaned = _clean_diff(
        "Here is the patch you asked for:\n```diff\n--- a/app.py\n+++ b/app.py\n@@ -1,1 +1,1 @@\n-a\n+b\n```\nHope that helps."
    )

    assert cleaned.startswith("--- a/app.py")
    assert "Hope" not in cleaned


def test_hunk_counts_the_model_got_wrong_are_recomputed():
    cleaned = _clean_diff(
        "--- a/app.py\n+++ b/app.py\n@@ -1,11 +1,11 @@\n import os\n import sys\n-old\n+new\n"
    )

    assert "@@ -1,3 +1,3 @@" in cleaned, cleaned


def test_the_start_line_and_section_heading_are_left_as_written():
    cleaned = _clean_diff(
        "--- a/app.py\n+++ b/app.py\n@@ -42,99 +42,99 @@ def handler():\n import os\n-old\n+new\n"
    )

    assert "@@ -42,2 +42,2 @@ def handler():" in cleaned


def test_a_patch_with_a_blank_line_in_it_really_applies(tmp_path):
    """End to end: the reason the two defects above matter is git rejecting the patch."""
    repo = _patched_clone(tmp_path)
    diff = _clean_diff(
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -4,9 +4,9 @@\n"
        " def _lazy_sha1(string: bytes = b\"\") -> t.Any:\n"
        "     \"\"\"Don't access ``hashlib.sha1`` until runtime. FIPS builds may not include\n"
        "     SHA-1, in which case the import and use as a default would fail before the\n"
        "     developer can configure something else.\n"
        "     \"\"\"\n"
        "-    return hashlib.sha1(string)\n"
        "+    return hashlib.sha256(string)\n"
        " \n"
        " \n"
        " class SecureCookieSessionInterface(SessionInterface):\n"
    )

    ok, error = _applies(repo, diff)
    assert ok, error


def test_reanchoring_uses_the_files_own_blank_lines():
    """The model omits blank context lines; difflib puts the file's real ones back."""
    source = "import os\n\n\ndef f():\n    return 1\n"
    rebuilt = _reanchor(
        _clean_diff("--- a/app.py\n+++ b/app.py\n@@ -1,4 +1,4 @@\n def f():\n-    return 1\n+    return 2\n"),
        source,
    )

    assert rebuilt.splitlines().count(" ") == 2, rebuilt


def test_reanchoring_leaves_a_diff_alone_when_the_old_lines_are_not_in_the_file():
    """No fabrication: if the edit cannot be located, git still gets to refuse it."""
    diff = _clean_diff("--- a/app.py\n+++ b/app.py\n@@ -1,2 +1,2 @@\n keep\n-never in the file\n+replacement\n")

    assert _reanchor(diff, "keep\nsomething else\n") == diff


def test_reanchoring_is_a_no_op_when_the_diff_has_no_hunk_body():
    diff = _clean_diff("--- a/app.py\n+++ b/app.py\n@@ -1,1 +1,1 @@\n")

    assert _reanchor(diff, "keep\n") == diff


def test_reanchoring_is_idempotent_and_leaves_its_own_output_alone():
    source = "a\nb\nc\nd\ne\n"
    once = _reanchor(
        _clean_diff("--- a/app.py\n+++ b/app.py\n@@ -1,3 +1,3 @@\n b\n-c\n+C\n"),
        source,
    )

    assert _reanchor(once, source) == once


def test_the_model_diff_from_the_live_run_applies_after_recounting(tmp_path):
    """The exact diff Nemotron-3-Ultra returned for flask's _lazy_sha1, before and after.

    As written it declares eleven lines on each side over a body of eight, which
    git rejects as a corrupt patch. After cleaning, the counts are recomputed from
    the body and the same patch applies.
    """
    repo = _patched_clone(tmp_path)
    as_written = (
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -4,11 +4,11 @@\n"
        " session_json_serializer = TaggedJSONSerializer()\n"
        " def _lazy_sha1(string: bytes = b\"\") -> t.Any:\n"
        '-    """Don\'t access ``hashlib.sha1`` until runtime. FIPS builds may not include\n'
        "-    SHA-1, in which case the import and use as a default would fail before the\n"
        "-    developer can configure something else.\n"
        '-    """\n'
        "-    return hashlib.sha1(string)\n"
        '+    """Don\'t access ``hashlib.sha256`` until runtime. FIPS builds may not include\n'
        "+    SHA-256, in which case the import and use as a default would fail before the\n"
        "+    developer can configure something else.\n"
        '+    """\n'
        "+    return hashlib.sha256(string)\n"
        " class SecureCookieSessionInterface(SessionInterface):\n"
    )

    ok, error = _applies(repo, as_written)
    assert not ok, "the counts the model wrote are wrong, so this should fail as written"
    assert "corrupt" in error

    ok, error = _applies(repo, _reanchor(_clean_diff(as_written), (repo / "app.py").read_text()))
    assert ok, error


@pytest.mark.parametrize(
    "path",
    ["--- /etc/passwd\n+++ /etc/passwd\n@@ -1 +1 @@\n-a\n+b\n", "--- a/../etc/passwd\n+++ b/../etc/passwd\n@@ -1 +1 @@\n-a\n+b\n"],
)
def test_a_diff_reaching_outside_the_clone_is_refused(path: str):
    assert _clean_diff(path) == ""


def test_a_diff_touching_the_git_directory_is_refused():
    assert _clean_diff("--- a/.git/config\n+++ b/.git/config\n@@ -1 +1 @@\n-a\n+b\n") == ""


def text_is_not_mistaken_for_a_diff():
    assert _clean_diff("Change the digest method to hashlib.sha256 and update the docstring.") == ""
    assert _clean_diff("") == ""