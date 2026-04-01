"""User data (cloud-init) rendering for EC2 instances."""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from skyops.config import Config


def render_user_data(
    username: str,
    ssh_key_paths: list[str],
    template_path: str | None = None,
    tailscale_enabled: bool = False,
) -> str:
    """Render the user data shell script for a new EC2 instance.

    Args:
        username: Linux username to create on the instance.
        ssh_key_paths: Paths to SSH public key files to inject.
        template_path: Path to a custom Jinja2 template, or None to use the
            package default.
        tailscale_enabled: Whether to install the Tailscale daemon during setup.

    Returns:
        Rendered user data script as a string.
    """
    if template_path is not None:
        tmpl_file = Path(template_path).expanduser().resolve()
    else:
        tmpl_file = Config.get_default_template_path()

    ssh_keys = []
    for key_path in ssh_key_paths:
        Config.validate_ssh_public_key(key_path)
        ssh_keys.append(Config.read_ssh_key_content(key_path))

    env = Environment(
        loader=FileSystemLoader(str(tmpl_file.parent)),
        undefined=StrictUndefined,
        autoescape=False,
    )
    template = env.get_template(tmpl_file.name)
    return template.render(
        username=username,
        ssh_keys=ssh_keys,
        tailscale_enabled=tailscale_enabled,
    )
