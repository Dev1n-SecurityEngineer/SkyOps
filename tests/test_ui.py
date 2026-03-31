"""Tests for skyops.ui module."""

from io import StringIO

from rich.console import Console

from skyops.ui import (
    _colorize_state,
    display_hibernate_amis,
    display_instance_info,
    display_instance_types,
    display_instances,
    display_key_pairs,
    display_regions,
)


def _capture(fn, *args, **kwargs) -> str:
    """Run fn with a wide captured Rich console, return rendered text."""
    buf = StringIO()
    con = Console(file=buf, highlight=False, no_color=True, width=200)
    import skyops.ui as ui_mod

    original = ui_mod.console
    ui_mod.console = con
    try:
        fn(*args, **kwargs)
    finally:
        ui_mod.console = original
    return buf.getvalue()


SAMPLE_INSTANCE = {
    "InstanceId": "i-abc123",
    "InstanceType": "t3.medium",
    "State": {"Name": "running"},
    "PublicIpAddress": "1.2.3.4",
    "PrivateIpAddress": "10.0.0.1",
    "ImageId": "ami-12345678",
    "KeyName": "skyops-alice",
    "VpcId": "vpc-abc",
    "SubnetId": "subnet-abc",
    "LaunchTime": "2024-01-01T00:00:00Z",
    "Placement": {"AvailabilityZone": "us-east-1a"},
    "Tags": [
        {"Key": "Name", "Value": "my-instance"},
        {"Key": "skyops:owner", "Value": "alice"},
    ],
}

SAMPLE_AMI = {
    "ImageId": "ami-snapshot123",
    "Name": "skyops-hibernate-my-instance",
    "CreationDate": "2024-01-01T00:00:00Z",
    "Tags": [
        {"Key": "Name", "Value": "my-instance"},
        {"Key": "skyops:owner", "Value": "alice"},
        {"Key": "skyops:size", "Value": "t3.medium"},
        {"Key": "skyops:region", "Value": "us-east-1"},
    ],
}

SAMPLE_KEY_PAIR = {
    "KeyName": "skyops-alice",
    "KeyPairId": "key-abc123",
    "KeyFingerprint": "aa:bb:cc:dd",
    "CreateTime": "2024-01-01T00:00:00Z",
}

SAMPLE_INSTANCE_TYPE = {
    "InstanceType": "t3.medium",
    "VCpuInfo": {"DefaultVCpus": 2},
    "MemoryInfo": {"SizeInMiB": 4096},
    "ProcessorInfo": {"SupportedArchitectures": ["x86_64"]},
}


class TestDisplayInstances:
    def test_renders_table(self):
        out = _capture(display_instances, [SAMPLE_INSTANCE])
        assert "i-abc123" in out
        assert "t3.medium" in out
        assert "1.2.3.4" in out

    def test_empty_message(self):
        out = _capture(display_instances, [])
        assert "No instances found" in out

    def test_missing_tags(self):
        inst = {**SAMPLE_INSTANCE, "Tags": []}
        out = _capture(display_instances, [inst])
        assert "i-abc123" in out


class TestDisplayInstanceInfo:
    def test_renders_panel(self):
        out = _capture(display_instance_info, SAMPLE_INSTANCE)
        assert "i-abc123" in out
        assert "t3.medium" in out
        assert "1.2.3.4" in out
        assert "vpc-abc" in out

    def test_no_public_ip(self):
        inst = {**SAMPLE_INSTANCE}
        del inst["PublicIpAddress"]
        out = _capture(display_instance_info, inst)
        assert "i-abc123" in out


class TestDisplayHibernateAMIs:
    def test_renders_table(self):
        out = _capture(display_hibernate_amis, [SAMPLE_AMI])
        assert "ami-snapshot123" in out
        assert "t3.medium" in out

    def test_empty_message(self):
        out = _capture(display_hibernate_amis, [])
        assert "No hibernated" in out


class TestDisplayKeyPairs:
    def test_renders_table(self):
        out = _capture(display_key_pairs, [SAMPLE_KEY_PAIR])
        assert "skyops-alice" in out
        assert "key-abc123" in out

    def test_empty_message(self):
        out = _capture(display_key_pairs, [])
        assert "No key pairs found" in out


class TestDisplayInstanceTypes:
    def test_renders_table(self):
        out = _capture(display_instance_types, [SAMPLE_INSTANCE_TYPE])
        assert "t3.medium" in out
        assert "4.0" in out  # 4096 MiB -> 4.0 GiB


class TestDisplayRegions:
    def test_renders_regions(self):
        out = _capture(display_regions, ["us-east-1", "us-west-2"])
        assert "us-east-1" in out
        assert "us-west-2" in out


class TestColorizeState:
    def test_running(self):
        assert "running" in _colorize_state("running")

    def test_stopped(self):
        assert "stopped" in _colorize_state("stopped")

    def test_unknown(self):
        assert "unknown" in _colorize_state("unknown")
