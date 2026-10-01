from nlxpg.auth import check_password_policy, hash_password, temp_password, verify_password


def test_hash_and_verify():
    h = hash_password("s3cret-pass")
    assert h.startswith("scrypt$") and "s3cret" not in h
    assert verify_password("s3cret-pass", h)
    assert not verify_password("wrong-pass", h)
    assert hash_password("s3cret-pass") != h  # 매번 다른 salt


def test_verify_rejects_garbage():
    assert not verify_password("x", "not-a-hash")
    assert not verify_password("x", "bcrypt$1$2$3$4$5")


def test_password_policy():
    assert check_password_policy("short1") is not None
    assert check_password_policy("12345678") is not None
    assert check_password_policy("abcdefgh") is not None
    assert check_password_policy("abcd1234") is None
    assert all(check_password_policy(temp_password()) is None for _ in range(200))
