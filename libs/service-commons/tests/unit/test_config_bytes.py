"""File and verified-byte configuration parsing retain the same YAML contract."""

from pathlib import Path

import pytest

from service_commons import config

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("content", [b"service: {name: ui}\n", b'{"port": 8008}', b"flag: false\n"])
def test_verified_bytes_parse_like_the_file_loader(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "source.yaml"
    path.write_bytes(content)
    expected = config.load_yaml_config(path)
    path.unlink()
    assert config.parse_yaml_config_bytes(content, source="captured config") == expected


@pytest.mark.parametrize("content", [b"", b"[]", b"42", b"broken: [", b"\xff"])
def test_byte_parser_rejects_empty_nonmapping_and_invalid_configuration(content: bytes) -> None:
    with pytest.raises(config.ConfigurationError):
        config.parse_yaml_config_bytes(content, source="captured config")


def test_file_loader_keeps_its_missing_file_error(tmp_path: Path) -> None:
    with pytest.raises(config.ConfigurationError, match="Configuration file not found"):
        config.load_yaml_config(tmp_path / "absent.yaml")
