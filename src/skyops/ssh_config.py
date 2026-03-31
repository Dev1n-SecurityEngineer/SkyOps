"""SSH config file management for skyops instances."""

import shutil
import subprocess
from pathlib import Path

SSH_HOST_PREFIX = "skyops"


def _backup_config(config_file: Path) -> None:
    if not config_file.exists():
        return
    backup_file = config_file.parent / f"{config_file.name}.bak"
    shutil.copy2(config_file, backup_file)
    backup_file.chmod(config_file.stat().st_mode)


def instance_host_name(instance_name: str) -> str:
    """Return the SSH host alias for an instance: skyops.<name>."""
    return f"{SSH_HOST_PREFIX}.{instance_name}"


def add_ssh_host(
    config_path: str,
    host_name: str,
    hostname: str,
    user: str,
    identity_file: str | None = None,
) -> None:
    """Add or update an SSH host entry in the SSH config file.

    Args:
        config_path: Path to SSH config file (e.g., ~/.ssh/config)
        host_name: Host alias (e.g., 'skyops.my-instance')
        hostname: IP address or hostname
        user: SSH username
        identity_file: Path to SSH private key (optional)
    """
    config_file = Path(config_path).expanduser()
    config_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _backup_config(config_file)

    existing_content = config_file.read_text() if config_file.exists() else ""

    if f"Host {host_name}" in existing_content:
        lines = existing_content.split("\n")
        new_lines = []
        skip = False
        for line in lines:
            if line.startswith("Host "):
                if line == f"Host {host_name}":
                    skip = True
                else:
                    skip = False
                    new_lines.append(line)
            elif skip:
                continue
            else:
                new_lines.append(line)
        existing_content = "\n".join(new_lines).rstrip() + "\n\n"

    host_entry = f"Host {host_name}\n"
    host_entry += f"    HostName {hostname}\n"
    host_entry += "    ForwardAgent yes\n"
    host_entry += f"    User {user}\n"
    if identity_file:
        host_entry += f"    IdentityFile {identity_file}\n"

    if existing_content and not existing_content.endswith("\n"):
        existing_content += "\n"

    new_content = existing_content + host_entry + "\n"

    with open(config_file, "w") as f:
        f.write(new_content)
    config_file.chmod(0o600)


def get_ssh_host_ip(config_path: str, host_name: str) -> str | None:
    """Return the HostName (IP) for the given SSH host alias, or None."""
    config_file = Path(config_path).expanduser()
    if not config_file.exists():
        return None

    in_target = False
    for line in config_file.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("Host "):
            in_target = host_name in stripped[5:].strip().split()
        elif in_target and stripped.startswith("HostName "):
            return stripped[9:].strip()
        elif in_target and stripped and not line.startswith((" ", "\t")):
            in_target = False
    return None


def host_exists(config_path: str, host_name: str) -> bool:
    """Return True if the SSH host alias exists in the config file."""
    config_file = Path(config_path).expanduser()
    if not config_file.exists():
        return False
    return f"Host {host_name}" in config_file.read_text()


def remove_ssh_host(config_path: str, host_name: str) -> bool:
    """Remove an SSH host entry from the config file.

    Returns:
        True if the host was found and removed, False otherwise.
    """
    config_file = Path(config_path).expanduser()
    if not config_file.exists():
        return False

    _backup_config(config_file)
    lines = config_file.read_text().splitlines(keepends=True)
    new_lines: list[str] = []
    skip = False
    found = False

    for line in lines:
        if line.strip().startswith("Host "):
            if line.strip() == f"Host {host_name}":
                skip = True
                found = True
            else:
                skip = False
                new_lines.append(line)
        elif skip:
            if line.strip() and not line.startswith((" ", "\t")):
                skip = False
                new_lines.append(line)
        else:
            new_lines.append(line)

    if found:
        with open(config_file, "w") as f:
            f.writelines(new_lines)
    return found


def remove_known_hosts_entry(known_hosts_path: str, hostnames: list[str]) -> int:
    """Remove entries for the given hostnames from known_hosts.

    Returns:
        Number of entries removed.
    """
    known_hosts_file = Path(known_hosts_path).expanduser()
    if not known_hosts_file.exists():
        return 0

    removed = 0
    for hostname in hostnames:
        result = subprocess.run(
            ["ssh-keygen", "-R", hostname, "-f", str(known_hosts_file)],
            capture_output=True,
            text=True,
        )
        if "updated" in result.stdout:
            removed += 1
    return removed
