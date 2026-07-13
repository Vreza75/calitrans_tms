from services.workflow_constants import normalize_service_flow, requires_port_pin


def test_import_requires_port_pin():
    assert requires_port_pin("Import") is True


def test_export_requires_port_pin():
    assert requires_port_pin("Export") is True


def test_local_import_does_not_require_port_pin():
    assert requires_port_pin("Local Import") is False


def test_local_export_does_not_require_port_pin():
    assert requires_port_pin("Local Export") is False


def test_requires_port_pin_normalizes_legacy_values_first():
    assert requires_port_pin("import") is True
    assert requires_port_pin("drayage import") is True
    assert requires_port_pin("local import move") is False


def test_requires_port_pin_unknown_value_is_false():
    assert requires_port_pin("") is False
    assert requires_port_pin("Something Else") is False
