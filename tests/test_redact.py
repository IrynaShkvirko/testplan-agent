import pytest

from testplan_agent.redact import redact

CASES = [
    ("aws_access_key", "key = AKIAABCDEFGHIJKLMNOP"),
    ("github_token", "use ghp_" + "a1B2c3D4e5" * 4),
    ("slack_token", "hook xoxb-1234567890-abcdefghij"),
    ("bearer_token", "Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345"),
    ("jwt", "t = eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0NTY.SflKxwRJSMeKKF2QT4"),
    ("credential_assignment", 'password = "hunter2hunter2"'),
    ("email", "contact jane.doe@corp.io for access"),
]


@pytest.mark.parametrize("kind,text", CASES)
def test_each_kind_is_replaced_and_counted(kind, text):
    out, counts = redact(text)
    assert f"[REDACTED:{kind}]" in out
    assert counts[kind] >= 1


def test_the_secret_value_does_not_survive():
    out, _ = redact('password = "hunter2hunter2"\nowner: jane.doe@corp.io')
    assert "hunter2" not in out and "jane.doe" not in out
    assert out.startswith("password")  # the name stays, so the code still reads


def test_example_addresses_and_short_values_are_left_alone():
    text = "mail me at qa@example.com\ntoken = short\nthe token bucket refills"
    out, counts = redact(text)
    assert out == text and not counts


def test_private_key_blocks_are_removed_line_by_line():
    text = "a\n-----BEGIN RSA PRIVATE KEY-----\nMIIEow\nabc\n-----END RSA PRIVATE KEY-----\nb"
    out, counts = redact(text)
    assert counts["private_key"] == 1
    assert "MIIEow" not in out
    assert out.split("\n")[0] == "a" and out.split("\n")[-1] == "b"


def test_line_count_is_preserved_so_hunk_headers_stay_valid():
    text = "\n".join(
        [
            "x = 1",
            'api_key = "abcdefgh12345678"',
            "-----BEGIN PRIVATE KEY-----",
            "zz",
            "-----END PRIVATE KEY-----",
        ]
    )
    out, _ = redact(text)
    assert out.count("\n") == text.count("\n")
