"""Tests for skyops.userdata module."""

import base64
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from skyops.userdata import render_user_data


def _make_pub_key() -> str:
    private_key = Ed25519PrivateKey.generate()
    raw = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    key_type = b"ssh-ed25519"
    inner = len(key_type).to_bytes(4, "big") + key_type + len(raw).to_bytes(4, "big") + raw
    return f"ssh-ed25519 {base64.b64encode(inner).decode()} test@skyops"


@pytest.fixture
def pub_key_file(tmp_path: Path) -> Path:
    key_file = tmp_path / "id_ed25519.pub"
    key_file.write_text(_make_pub_key())
    return key_file


class TestRenderUserData:
    def test_renders_default_template(self, pub_key_file: Path):
        result = render_user_data(
            username="alice",
            ssh_key_paths=[str(pub_key_file)],
        )
        assert "alice" in result
        assert "ssh-ed25519" in result
        assert "#!/bin/bash" in result

    def test_custom_template(self, pub_key_file: Path, tmp_path: Path):
        template_dir = tmp_path / "templates"
        template_dir.mkdir()
        custom = template_dir / "custom.sh"
        custom.write_text("#!/bin/bash\n# user: {{ username }}\n{% for k in ssh_keys %}{{ k }}\n{% endfor %}")
        result = render_user_data(
            username="bob",
            ssh_key_paths=[str(pub_key_file)],
            template_path=str(custom),
        )
        assert "bob" in result
        assert "ssh-ed25519" in result

    def test_invalid_key_raises(self, tmp_path: Path):
        bad_key = tmp_path / "bad.pub"
        bad_key.write_text("not a valid key")
        with pytest.raises(ValueError):
            render_user_data(username="alice", ssh_key_paths=[str(bad_key)])

    def test_missing_key_raises(self):
        with pytest.raises(FileNotFoundError):
            render_user_data(username="alice", ssh_key_paths=["/nonexistent/key.pub"])

    def test_multiple_keys(self, tmp_path: Path):
        keys = []
        for i in range(3):
            kf = tmp_path / f"key{i}.pub"
            kf.write_text(_make_pub_key())
            keys.append(str(kf))
        result = render_user_data(username="alice", ssh_key_paths=keys)
        assert result.count("ssh-ed25519") >= 3
