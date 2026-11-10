"""Tests for security utilities: Argon2id hashing and CSRF tokens."""
import pytest
from app.core.security import (
    hash_password, verify_password, password_needs_rehash,
    generate_csrf_token, verify_csrf_token,
    generate_session_token, safe_str_compare,
)


class TestArgon2Hashing:

    def test_hash_is_not_plaintext(self):
        """Hashed password must not contain the original string."""
        h = hash_password("MySecret1")
        assert "MySecret1" not in h

    def test_hash_contains_argon2id_identifier(self):
        """Hash must use Argon2id algorithm identifier."""
        h = hash_password("MySecret1")
        assert "$argon2id$" in h

    def test_verify_correct_password(self):
        """Correct password verifies successfully."""
        h = hash_password("CorrectPass1")
        assert verify_password("CorrectPass1", h) is True

    def test_verify_wrong_password(self):
        """Wrong password returns False."""
        h = hash_password("CorrectPass1")
        assert verify_password("WrongPass1", h) is False

    def test_verify_empty_password(self):
        """Empty password returns False, does not raise."""
        h = hash_password("AnyPassword1")
        assert verify_password("", h) is False

    def test_different_hashes_for_same_password(self):
        """Two hashes of the same password must differ (unique salts)."""
        h1 = hash_password("SamePassword1")
        h2 = hash_password("SamePassword1")
        assert h1 != h2

    def test_verify_both_hashes(self):
        """Both hashes of the same password verify correctly."""
        h1 = hash_password("SamePassword1")
        h2 = hash_password("SamePassword1")
        assert verify_password("SamePassword1", h1) is True
        assert verify_password("SamePassword1", h2) is True

    def test_needs_rehash_returns_false_for_current_params(self):
        """Fresh hash with current params does not need rehashing."""
        h = hash_password("AnyPassword1")
        assert password_needs_rehash(h) is False


class TestCsrfToken:

    def test_generated_token_verifies(self):
        """A freshly generated CSRF token passes verification."""
        token = generate_csrf_token()
        assert verify_csrf_token(token) is True

    def test_tampered_token_fails(self):
        """Tampered CSRF token fails verification."""
        token = generate_csrf_token()
        parts = token.split(":")
        parts[2] = "tampered_signature"
        tampered = ":".join(parts)
        assert verify_csrf_token(tampered) is False

    def test_wrong_format_token_fails(self):
        """Malformed token returns False without raising."""
        assert verify_csrf_token("not-a-token") is False
        assert verify_csrf_token("") is False
        assert verify_csrf_token("a:b") is False

    def test_expired_token_fails(self):
        """Token that exceeds max_age fails verification."""
        import time
        token = generate_csrf_token()
        # Force-check with 0 seconds max age
        assert verify_csrf_token(token, max_age_seconds=0) is False


class TestSessionToken:

    def test_session_token_is_unique(self):
        """Two session tokens must never be equal."""
        t1 = generate_session_token()
        t2 = generate_session_token()
        assert t1 != t2

    def test_session_token_is_long_enough(self):
        """Session token must be at least 64 characters."""
        token = generate_session_token()
        assert len(token) >= 64

    def test_session_token_is_string(self):
        """Session token must be a URL-safe string."""
        token = generate_session_token()
        assert isinstance(token, str)
        # URL-safe base64 characters only
        import re
        assert re.match(r'^[A-Za-z0-9_-]+$', token)


class TestSafeCompare:

    def test_equal_strings_return_true(self):
        assert safe_str_compare("abc", "abc") is True

    def test_different_strings_return_false(self):
        assert safe_str_compare("abc", "xyz") is False

    def test_empty_strings(self):
        assert safe_str_compare("", "") is True

    def test_case_sensitive(self):
        assert safe_str_compare("ABC", "abc") is False
