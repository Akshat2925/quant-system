from quant_system.core.idempotency import make_client_order_id, short_client_order_id


def test_same_inputs_produce_same_id():
    a = short_client_order_id("run-1", 12345, 1)
    b = short_client_order_id("run-1", 12345, 1)
    assert a == b


def test_different_sequence_produces_different_id():
    a = short_client_order_id("run-1", 12345, 1)
    b = short_client_order_id("run-1", 12345, 2)
    assert a != b


def test_different_run_produces_different_id():
    a = short_client_order_id("run-1", 12345, 1)
    b = short_client_order_id("run-2", 12345, 1)
    assert a != b


def test_short_id_is_broker_safe_length():
    short_id = short_client_order_id("run-1", 12345, 1)
    assert len(short_id) <= 20
    assert short_id.isalnum()


def test_long_form_returns_full_uuid_too():
    short_id, full_uuid = make_client_order_id("run-1", 12345, 1)
    assert len(full_uuid) == 36  # standard UUID string length
    assert short_id in short_client_order_id("run-1", 12345, 1)
