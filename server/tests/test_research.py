"""Every grounding query key must be a rule the scanner actually emits.

A key that does not exist is dead weight. A rule with no key silently falls back
to a generic "vulnerability remediation OWASP" query, which returns guidance
about a class of bug the finding is not.

The four mismatched keys in this file's history - `insecure_eval`,
`disabled_tls_verification`, `xss`, `unsigned_jwt` - matched no rule id at all.
Four of the eighteen rules reached Tavily with the wrong question, and four
credential rules asked about the wrong provider.
"""

from __future__ import annotations

from app.core.research import GROUNDING_QUERIES
from app.core.security import RULES


def test_every_rule_has_a_grounding_query():
    emitted = {rule.id for rule in RULES} | {"high_entropy_value"}  # entropy is a rule id too
    assert emitted <= set(GROUNDING_QUERIES), sorted(emitted - set(GROUNDING_QUERIES))


def test_no_query_key_is_for_a_rule_that_does_not_exist():
    emitted = {rule.id for rule in RULES} | {"high_entropy_value"}
    assert set(GROUNDING_QUERIES) <= emitted, sorted(set(GROUNDING_QUERIES) - emitted)


def test_each_query_names_the_bug_class_it_is_asked_about():
    """The keys that were wrong asked about a different vulnerability than the one found."""
    assert "Tls" in GROUNDING_QUERIES["insecure_transport"] or "TLS" in GROUNDING_QUERIES["insecure_transport"]
    assert "scripting" in GROUNDING_QUERIES["xss_innerhtml"]
    assert "JSON web token" in GROUNDING_QUERIES["jwt_none_algorithm"]
    assert "eval" in GROUNDING_QUERIES["code_execution"]
    assert "AWS" in GROUNDING_QUERIES["aws_access_key"]
    assert "GitHub" in GROUNDING_QUERIES["github_token"]