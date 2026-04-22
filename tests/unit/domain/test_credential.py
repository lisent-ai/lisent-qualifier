"""Phase 2.E — Credential crypto unit tests."""

from __future__ import annotations

import time

import pytest

from app.domain.tenant import credential as cred


class TestIssueCredential:
    def test_returns_three_segment_jwt(self):
        t = cred.issue_credential(
            lead_id="l1",
            tenant_id="t1",
            score=78,
            threshold=70,
            framework="champ",
            email="ali@example.com",
            secret="s1",
        )
        assert t.count(".") == 2

    def test_payload_roundtrip(self):
        t = cred.issue_credential(
            lead_id="l1",
            tenant_id="t1",
            score=82,
            threshold=70,
            framework="champ",
            email="ali@example.com",
            secret="secret",
        )
        p = cred.verify_credential(t, "secret")
        assert p.lead_id == "l1"
        assert p.tenant_id == "t1"
        assert p.score == 82
        assert p.threshold == 70
        assert p.qualified is True
        assert p.framework == "champ"
        assert len(p.email_hash) == 16  # sha256[:16]

    def test_email_hash_is_anonymous(self):
        t = cred.issue_credential(
            lead_id="l1", tenant_id="t1", score=80, threshold=70,
            framework="champ", email="alice@corp.com", secret="x",
        )
        assert "alice" not in t
        assert "corp.com" not in t


class TestVerifyCredential:
    def test_wrong_secret_raises_signature_mismatch(self):
        t = cred.issue_credential(
            lead_id="l", tenant_id="t", score=50, threshold=70,
            framework="champ", email=None, secret="right",
        )
        with pytest.raises(cred.CredentialSignatureMismatch):
            cred.verify_credential(t, "wrong")

    def test_tampered_payload_detected(self):
        t = cred.issue_credential(
            lead_id="l", tenant_id="t", score=50, threshold=70,
            framework="champ", email=None, secret="s",
        )
        # Flip a char in payload section
        h, p, s = t.split(".")
        tampered_payload = p[:-3] + "xxx"
        forged = f"{h}.{tampered_payload}.{s}"
        with pytest.raises(cred.CredentialSignatureMismatch):
            cred.verify_credential(forged, "s")

    def test_expired_credential_rejected(self):
        t = cred.issue_credential(
            lead_id="l", tenant_id="t", score=50, threshold=70,
            framework="champ", email=None, secret="s",
            expires_in=-1,  # already expired
        )
        with pytest.raises(cred.CredentialExpired):
            cred.verify_credential(t, "s")

    def test_malformed_token_rejected(self):
        with pytest.raises(cred.CredentialInvalid):
            cred.verify_credential("not-a-jwt", "s")
        with pytest.raises(cred.CredentialInvalid):
            cred.verify_credential("one.two", "s")


class TestQualifiedFlag:
    def test_qualified_when_score_above_threshold(self):
        t = cred.issue_credential(
            lead_id="l", tenant_id="t", score=80, threshold=70,
            framework="champ", email=None, secret="s",
        )
        p = cred.verify_credential(t, "s")
        assert p.qualified is True

    def test_not_qualified_when_score_below(self):
        t = cred.issue_credential(
            lead_id="l", tenant_id="t", score=50, threshold=70,
            framework="champ", email=None, secret="s",
        )
        p = cred.verify_credential(t, "s")
        assert p.qualified is False
