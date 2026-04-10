"""Configuration management for skyops."""

import base64
import hashlib
import os
from pathlib import Path

import yaml
from cryptography.hazmat.primitives import serialization
from pydantic import BaseModel, ConfigDict, Field, field_validator

DEFAULT_REGION = "us-east-1"
DEFAULT_INSTANCE_TYPE = "t3.medium"
DEFAULT_AMI = "ami-0c02fb55956c7d316"  # Ubuntu 24.04 LTS us-east-1 (resolved at init)


class AWSConfig(BaseModel):
    """AWS connection configuration."""

    region: str = Field(..., min_length=1, description="AWS region")
    profile: str | None = Field(
        default=None,
        description="AWS named profile (uses default credential chain if null)",
    )


class DefaultsConfig(BaseModel):
    """Default settings for EC2 instance creation."""

    region: str = Field(..., min_length=1, description="Default AWS region")
    instance_type: str = Field(..., min_length=1, description="Default EC2 instance type")
    ami: str = Field(..., min_length=1, description="Default AMI ID")
    key_pair_name: str = Field(..., min_length=1, description="AWS key pair name")
    vpc_id: str | None = Field(default=None, description="VPC ID (null = use default VPC)")
    subnet_id: str | None = Field(
        default=None, description="Subnet ID (null = auto-select from VPC)"
    )
    security_group_id: str | None = Field(
        default=None, description="Security group ID (null = auto-create skyops-default)"
    )
    extra_tags: list[str] = Field(
        default_factory=list,
        description="Extra tag keys/values as 'Key=Value' strings",
    )
    restrict_ssh: bool = Field(
        default=False,
        description="Restrict SSH ingress to caller's public IP (instead of 0.0.0.0/0)",
    )


class UserDataConfig(BaseModel):
    """User data (cloud-init) configuration."""

    template_path: str | None = Field(
        default=None, description="Path to user data template (null = use package default)"
    )
    ssh_keys: list[str] = Field(..., min_length=1, description="Paths to SSH public key files")
    tailscale_enabled: bool = Field(
        default=False,
        description="Install Tailscale daemon during instance setup",
    )

    @field_validator("ssh_keys")
    @classmethod
    def ssh_keys_not_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("At least one SSH key must be configured")
        return v


class SSHConfig(BaseModel):
    """SSH client configuration."""

    config_path: str = Field(..., description="Path to SSH config file")
    auto_update: bool = Field(default=True, description="Auto-update SSH config on create/destroy")
    identity_file: str = Field(..., description="SSH private key path")


class SkyOpsConfig(BaseModel):
    """Root skyops configuration."""

    model_config = ConfigDict(extra="forbid")

    aws: AWSConfig
    defaults: DefaultsConfig
    userdata: UserDataConfig
    ssh: SSHConfig


class Config:
    """Manages skyops configuration with Pydantic validation."""

    CONFIG_DIR = Path.home() / ".config" / "skyops"
    CONFIG_FILE = CONFIG_DIR / "config.yaml"

    def __init__(self) -> None:
        self._config: SkyOpsConfig | None = None

    @property
    def config(self) -> SkyOpsConfig:
        if self._config is None:
            raise ValueError("Configuration not loaded. Call load() first.")
        return self._config

    @classmethod
    def exists(cls) -> bool:
        return cls.CONFIG_FILE.exists()

    @classmethod
    def get_config_dir(cls) -> Path:
        return cls.CONFIG_DIR

    @staticmethod
    def get_default_template_path() -> Path:
        return Path(__file__).parent / "templates" / "default-userdata.sh"

    @classmethod
    def ensure_config_dir(cls) -> None:
        cls.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        cls.CONFIG_DIR.chmod(0o700)

    def load(self) -> None:
        if not self.CONFIG_FILE.exists():
            raise FileNotFoundError(
                f"Config not found at {self.CONFIG_FILE}. Run 'skyops init' first."
            )
        with open(self.CONFIG_FILE) as f:
            data = yaml.safe_load(f) or {}
        self._config = SkyOpsConfig(**data)

    def save(self) -> None:
        if self._config is None:
            raise ValueError("No configuration to save")
        self.ensure_config_dir()
        data = self._config.model_dump(mode="python")
        with open(self.CONFIG_FILE, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)
        self.CONFIG_FILE.chmod(0o600)

    @staticmethod
    def get_system_username() -> str:
        return os.environ.get("USER", "user")

    @staticmethod
    def detect_ssh_keys() -> list[str]:
        ssh_dir = Path.home() / ".ssh"
        if not ssh_dir.exists():
            return []
        found = [str(p) for p in ssh_dir.glob("*.pub")]
        found.sort(key=lambda p: Path(p).stat().st_mtime, reverse=True)
        return found

    @staticmethod
    def validate_ssh_public_key(key_path: str) -> None:
        path = Path(key_path).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"SSH key not found: {key_path}")
        if not path.name.endswith(".pub"):
            raise ValueError(
                f"SSH key file must be a public key (*.pub): {key_path}\n"
                "Private keys should NEVER be uploaded to AWS."
            )
        try:
            with open(path) as f:
                content = f.read().strip()
        except Exception as e:
            raise ValueError(f"Cannot read SSH key file: {e}")
        if not content:
            raise ValueError(f"SSH key file is empty: {key_path}")
        valid_prefixes = (
            "ssh-rsa ",
            "ssh-ed25519 ",
            "ecdsa-sha2-nistp256 ",
            "ecdsa-sha2-nistp384 ",
            "ecdsa-sha2-nistp521 ",
            "sk-ssh-ed25519@openssh.com ",
            "sk-ecdsa-sha2-nistp256@openssh.com ",
        )
        if not content.startswith(valid_prefixes):
            raise ValueError(
                f"File does not appear to be a valid SSH public key: {key_path}\n"
                f"Public keys should start with: {', '.join(p.strip() for p in valid_prefixes)}"
            )

    @staticmethod
    def read_ssh_key_content(key_path: str) -> str:
        path = Path(key_path).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"SSH key not found: {key_path}")
        with open(path) as f:
            return f.read().strip()

    @staticmethod
    def compute_ssh_key_fingerprint(public_key: str) -> str:
        try:
            serialization.load_ssh_public_key(public_key.encode())
            parts = public_key.strip().split()
            if len(parts) < 2:
                raise ValueError("Invalid SSH public key format")
            key_data = base64.b64decode(parts[1])
            fingerprint = hashlib.md5(key_data).hexdigest()
            return ":".join(fingerprint[i : i + 2] for i in range(0, len(fingerprint), 2))
        except Exception as e:
            raise ValueError(f"Failed to compute SSH key fingerprint: {e}")

    def create_default_config(
        self,
        region: str,
        instance_type: str,
        ami: str,
        key_pair_name: str,
        ssh_keys: list[str],
        profile: str | None = None,
        vpc_id: str | None = None,
        subnet_id: str | None = None,
        security_group_id: str | None = None,
        extra_tags: list[str] | None = None,
    ) -> None:
        if not ssh_keys:
            raise ValueError("No SSH keys provided.")
        self._config = SkyOpsConfig(
            aws=AWSConfig(region=region, profile=profile),
            defaults=DefaultsConfig(
                region=region,
                instance_type=instance_type,
                ami=ami,
                key_pair_name=key_pair_name,
                vpc_id=vpc_id,
                subnet_id=subnet_id,
                security_group_id=security_group_id,
                extra_tags=extra_tags or [],
            ),
            userdata=UserDataConfig(ssh_keys=ssh_keys),
            ssh=SSHConfig(
                config_path=str(Path.home() / ".ssh" / "config"),
                auto_update=True,
                identity_file=ssh_keys[0].replace(".pub", "") if ssh_keys else "~/.ssh/id_ed25519",
            ),
        )
