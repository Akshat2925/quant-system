"""Fix the two rate-limit tests that fail in CI due to shared state."""
import re

content = open("tests/test_notifier.py", encoding="utf-8").read()

old1 = '''def test_error_rate_limited(tmp_path):
    notifier = _make_notifier(tmp_path)
    notifier._error_times = {}  # fresh state
    notifier.notify_error(E.LOGIN_FAILED, "bad credentials")
    notifier.notify_error(E.LOGIN_FAILED, "bad credentials")   # within 30 min
    assert len(notifier._sender.sent) == 1'''

new1 = '''def test_error_rate_limited(tmp_path):
    notifier = _make_notifier(tmp_path)
    notifier._error_times = {}  # guaranteed fresh — isolated from other tests
    notifier._sender.sent.clear()
    notifier.notify_error(E.LOGIN_FAILED, "bad credentials")
    first_count = len(notifier._sender.sent)
    notifier.notify_error(E.LOGIN_FAILED, "bad credentials")   # within 30 min
    second_count = len(notifier._sender.sent)
    assert first_count == 1, f"first call should send 1, got {first_count}"
    assert second_count == 1, f"second call should be rate-limited, got {second_count}"'''

old2 = '''def test_different_error_types_not_rate_limited_together(tmp_path):
    notifier = _make_notifier(tmp_path)
    notifier._error_times = {}  # fresh state
    notifier.notify_error(E.LOGIN_FAILED,      "error 1")
    notifier.notify_error(E.PRICE_FEED_FAILING, "error 2")
    assert len(notifier._sender.sent) == 2'''

new2 = '''def test_different_error_types_not_rate_limited_together(tmp_path):
    notifier = _make_notifier(tmp_path)
    notifier._error_times = {}  # guaranteed fresh — isolated from other tests
    notifier._sender.sent.clear()
    notifier.notify_error(E.LOGIN_FAILED,       "error 1")
    notifier.notify_error(E.PRICE_FEED_FAILING, "error 2")
    assert len(notifier._sender.sent) == 2, \
        f"two different error types should both send, got {len(notifier._sender.sent)}"'''

assert old1 in content, f"old1 not found"
assert old2 in content, f"old2 not found"
content = content.replace(old1, new1).replace(old2, new2)
open("tests/test_notifier.py", "w", encoding="utf-8").write(content)
print("done")
