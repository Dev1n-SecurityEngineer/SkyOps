"""Main CLI application for skyops."""

import typer
from rich.console import Console
from rich.prompt import Confirm, Prompt

from skyops import __version__
from skyops.api import (
    EC2API,
    HIBERNATE_AMI_PREFIX,
    HIBERNATE_REGION_TAG,
    HIBERNATE_SIZE_TAG,
    SKYOPS_TAG_KEY,
    EC2APIError,
    _get_tag,
)
from skyops.config import Config
from skyops.ssh_config import (
    add_ssh_host,
    instance_host_name,
    remove_known_hosts_entry,
    remove_ssh_host,
)
from skyops.ui import (
    console,
    display_instance_info,
    display_instance_types,
    display_instances,
    display_key_pairs,
    display_regions,
)
from skyops.userdata import render_user_data
from skyops.version_check import check_for_updates

app = typer.Typer(
    name="skyops",
    help="Manage AWS EC2 instances with automated setup, SSH configuration, and lifecycle management.",
    no_args_is_help=True,
)
err_console = Console(stderr=True)


@app.callback()
def _callback() -> None:
    """Run before every command — checks for updates once per day."""
    check_for_updates()


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _load_api() -> tuple[EC2API, Config, str]:
    """Load config, create API client, and derive the IAM username.

    Returns:
        (api, config_manager, username)
    """
    cfg = Config()
    cfg.load()
    api = EC2API(region=cfg.config.aws.region, profile=cfg.config.aws.profile)
    username = api.get_username()
    return api, cfg, username


def _abort(message: str) -> None:
    err_console.print(f"[bold red]Error:[/bold red] {message}")
    raise typer.Exit(code=1)


def complete_instance_name(incomplete: str) -> list[str]:
    """Autocomplete instance names from EC2."""
    try:
        if not Config.exists():
            return []
        api, _, username = _load_api()
        instances = api.list_instances(username)
        names = [_get_tag(i, "Name") or "" for i in instances]
        return [n for n in names if n.startswith(incomplete) and n]
    except Exception:
        return []


def complete_hibernate_name(incomplete: str) -> list[str]:
    """Autocomplete hibernated instance names."""
    try:
        if not Config.exists():
            return []
        api, _, username = _load_api()
        amis = api.list_hibernate_amis(username)
        names = [_get_tag(a, "Name") or "" for a in amis]
        return [n for n in names if n.startswith(incomplete) and n]
    except Exception:
        return []


# ------------------------------------------------------------------
# Commands
# ------------------------------------------------------------------


@app.command()
def init() -> None:
    """Initialize skyops configuration (interactive wizard)."""
    console.print("[bold cyan]Welcome to skyops init[/bold cyan]")

    if Config.exists():
        overwrite = Confirm.ask("[yellow]Config already exists. Overwrite?[/yellow]", default=False)
        if not overwrite:
            console.print("Aborted.")
            raise typer.Exit

    # AWS credentials
    console.print("\n[bold]AWS Configuration[/bold]")
    profile_input = Prompt.ask("AWS profile (leave blank for default credential chain)", default="")
    profile: str | None = profile_input.strip() or None

    region = Prompt.ask("Default region", default="us-east-1")

    # Validate credentials
    console.print("  Validating AWS credentials...")
    try:
        api = EC2API(region=region, profile=profile)
        username = api.get_username()
        console.print(f"  [green]✓[/green] Authenticated as [bold]{username}[/bold]")
    except EC2APIError as e:
        _abort(f"AWS credentials validation failed: {e}")
        return

    # Instance defaults
    console.print("\n[bold]Instance Defaults[/bold]")

    console.print("  Fetching available instance types...")
    try:
        instance_types = api.list_instance_types()
        display_instance_types(instance_types)
    except EC2APIError:
        pass

    instance_type = Prompt.ask("Default instance type", default="t3.medium")

    # Resolve latest Ubuntu 24.04 AMI
    console.print("  Resolving latest Ubuntu 24.04 LTS AMI...")
    try:
        ami = api.find_latest_ubuntu_ami("24.04")
        console.print(f"  [green]✓[/green] Found AMI: {ami}")
    except EC2APIError as e:
        console.print(f"  [yellow]Warning:[/yellow] Could not resolve AMI: {e}")
        ami = Prompt.ask("Enter AMI ID manually")

    # SSH keys
    console.print("\n[bold]SSH Keys[/bold]")
    detected = Config.detect_ssh_keys()
    if detected:
        console.print("  Detected SSH public keys:")
        for i, k in enumerate(detected):
            console.print(f"    [{i}] {k}")
        key_path = Prompt.ask("SSH public key path", default=detected[0])
    else:
        key_path = Prompt.ask("SSH public key path (e.g., ~/.ssh/id_ed25519.pub)")

    try:
        Config.validate_ssh_public_key(key_path)
    except (FileNotFoundError, ValueError) as e:
        _abort(str(e))
        return

    # Key pair in AWS
    key_pair_name = f"skyops-{username}"
    console.print(f"\n  Importing SSH key as AWS key pair '{key_pair_name}'...")
    try:
        public_key_content = Config.read_ssh_key_content(key_path)
        if not api.key_pair_exists(key_pair_name):
            api.import_key_pair(key_pair_name, public_key_content)
            console.print(f"  [green]✓[/green] Key pair '{key_pair_name}' created.")
        else:
            console.print(f"  [dim]Key pair '{key_pair_name}' already exists.[/dim]")
    except EC2APIError as e:
        _abort(f"Failed to import key pair: {e}")
        return

    # VPC
    console.print("\n[bold]Networking[/bold]")
    console.print("  Looking up default VPC...")
    try:
        vpc_id = api.get_default_vpc()
        if vpc_id:
            console.print(f"  [green]✓[/green] Default VPC: {vpc_id}")
        else:
            console.print("  [yellow]No default VPC found.[/yellow]")
            vpc_id = Prompt.ask("Enter VPC ID (or leave blank to skip)", default="") or None
    except EC2APIError as e:
        console.print(f"  [yellow]Warning:[/yellow] {e}")
        vpc_id = None

    # Save config
    cfg = Config()
    cfg.create_default_config(
        region=region,
        instance_type=instance_type,
        ami=ami,
        key_pair_name=key_pair_name,
        ssh_keys=[key_path],
        profile=profile,
        vpc_id=vpc_id,
    )
    cfg.save()
    console.print(f"\n[bold green]✓ Configuration saved to {Config.CONFIG_FILE}[/bold green]")
    console.print("\nCreate your first instance with: [bold]skyops create my-instance[/bold]")


@app.command()
def create(
    name: str = typer.Argument(default="", help="Instance name (prompted if omitted)"),
    instance_type: str | None = typer.Option(None, "--type", "-t", help="EC2 instance type"),
    ami: str | None = typer.Option(None, "--ami", help="AMI ID"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
) -> None:
    """Launch a new EC2 instance with automated SSH configuration."""
    try:
        api, cfg, username = _load_api()
    except FileNotFoundError as e:
        _abort(str(e))
        return

    if not name:
        name = Prompt.ask("Instance name")
    if not name:
        _abort("Instance name is required.")
        return

    defaults = cfg.config.defaults
    resolved_type = instance_type or defaults.instance_type
    resolved_ami = ami or defaults.ami

    console.print(f"\n[bold]Creating instance '[cyan]{name}[/cyan]'[/bold]")
    console.print(f"  Type:   {resolved_type}")
    console.print(f"  AMI:    {resolved_ami}")
    console.print(f"  Region: {cfg.config.aws.region}")

    # Resolve VPC / subnet / SG
    vpc_id = defaults.vpc_id
    subnet_id = defaults.subnet_id
    sg_id = defaults.security_group_id

    if not vpc_id:
        if verbose:
            console.print("  Resolving default VPC...")
        try:
            vpc_id = api.get_default_vpc()
            if not vpc_id:
                _abort("No default VPC found. Run 'skyops init' and specify a VPC.")
                return
        except EC2APIError as e:
            _abort(str(e))
            return

    if not subnet_id:
        try:
            subnets = api.get_subnets(vpc_id)
            if not subnets:
                _abort(f"No subnets found in VPC {vpc_id}.")
                return
            supported_azs = api.get_supported_azs(resolved_type)
            compatible = [s for s in subnets if s.get("AvailabilityZone") in supported_azs]
            if not compatible:
                _abort(
                    f"No subnets in VPC {vpc_id} support instance type {resolved_type}. "
                    f"Supported AZs: {', '.join(sorted(supported_azs))}"
                )
                return
            subnet_id = compatible[0]["SubnetId"]
        except EC2APIError as e:
            _abort(str(e))
            return

    if not sg_id:
        if verbose:
            console.print("  Resolving security group...")
        try:
            sg_id = api.get_or_create_security_group(vpc_id)
        except EC2APIError as e:
            _abort(str(e))
            return

    # Render user data
    try:
        user_data = render_user_data(
            username=username,
            ssh_key_paths=cfg.config.userdata.ssh_keys,
            template_path=cfg.config.userdata.template_path,
            tailscale_enabled=cfg.config.userdata.tailscale_enabled,
        )
    except Exception as e:
        _abort(f"Failed to render user data: {e}")
        return

    # Launch
    console.print("  Launching instance...")
    try:
        instance = api.launch_instance(
            name=name,
            instance_type=resolved_type,
            ami=resolved_ami,
            key_pair_name=defaults.key_pair_name,
            subnet_id=subnet_id,
            security_group_ids=[sg_id],
            user_data=user_data,
            owner=username,
        )
        instance_id = instance["InstanceId"]
        console.print(f"  [green]✓[/green] Instance launched: {instance_id}")
    except EC2APIError as e:
        _abort(str(e))
        return

    # Wait for running
    console.print("  Waiting for instance to be running...")
    try:
        instance = api.wait_instance_running(instance_id)
        public_ip = instance.get("PublicIpAddress", "")
        console.print(f"  [green]✓[/green] Instance running — public IP: {public_ip}")
    except EC2APIError as e:
        _abort(str(e))
        return

    # SSH config
    if cfg.config.ssh.auto_update and public_ip:
        host_alias = instance_host_name(name)
        add_ssh_host(
            config_path=cfg.config.ssh.config_path,
            host_name=host_alias,
            hostname=public_ip,
            user=username,
            identity_file=cfg.config.ssh.identity_file,
        )
        console.print(
            f"  [green]✓[/green] SSH config updated — connect with: [bold]ssh {host_alias}[/bold]"
        )


@app.command(name="list")
def list_instances() -> None:
    """List your EC2 instances."""
    try:
        api, _, username = _load_api()
        instances = api.list_instances(username)
        display_instances(instances)
    except FileNotFoundError as e:
        _abort(str(e))
    except EC2APIError as e:
        _abort(str(e))


@app.command()
def info(
    name: str = typer.Argument(help="Instance name", autocompletion=complete_instance_name),
) -> None:
    """Show detailed information about an instance."""
    try:
        api, _, username = _load_api()
        instance = api.find_instance_by_name(name, username)
        display_instance_info(instance)
    except (FileNotFoundError, EC2APIError) as e:
        _abort(str(e))


@app.command(name="ssh-config")
def ssh_config(
    name: str = typer.Argument(help="Instance name", autocompletion=complete_instance_name),
) -> None:
    """Add or update SSH config for an existing instance."""
    try:
        api, cfg, username = _load_api()
        instance = api.find_instance_by_name(name, username)
        public_ip = instance.get("PublicIpAddress")
        if not public_ip:
            _abort(f"Instance '{name}' has no public IP address.")
            return
        host_alias = instance_host_name(name)
        add_ssh_host(
            config_path=cfg.config.ssh.config_path,
            host_name=host_alias,
            hostname=public_ip,
            user=username,
            identity_file=cfg.config.ssh.identity_file,
        )
        console.print(
            f"[green]✓[/green] SSH config updated. Connect with: [bold]ssh {host_alias}[/bold]"
        )
    except (FileNotFoundError, EC2APIError) as e:
        _abort(str(e))


@app.command()
def rename(
    name: str = typer.Argument(help="Current instance name", autocompletion=complete_instance_name),
    new_name: str = typer.Argument(help="New name"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Rename an instance."""
    if not yes:
        confirmed = Confirm.ask(f"Rename '[cyan]{name}[/cyan]' → '[cyan]{new_name}[/cyan]'?")
        if not confirmed:
            raise typer.Abort

    try:
        api, cfg, username = _load_api()
        instance = api.find_instance_by_name(name, username)
        api.rename_instance(instance["InstanceId"], new_name)
        console.print(f"[green]✓[/green] Renamed to '{new_name}'.")

        # Update SSH config
        if cfg.config.ssh.auto_update:
            old_alias = instance_host_name(name)
            new_alias = instance_host_name(new_name)
            public_ip = instance.get("PublicIpAddress")
            remove_ssh_host(cfg.config.ssh.config_path, old_alias)
            if public_ip:
                add_ssh_host(
                    cfg.config.ssh.config_path,
                    new_alias,
                    public_ip,
                    username,
                    cfg.config.ssh.identity_file,
                )
    except (FileNotFoundError, EC2APIError) as e:
        _abort(str(e))


@app.command()
def destroy(
    name: str = typer.Argument(help="Instance name", autocompletion=complete_instance_name),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
) -> None:
    """Terminate an instance (DESTRUCTIVE)."""
    if not yes:
        confirmed = Confirm.ask(
            f"[bold red]Terminate '[cyan]{name}[/cyan]'? This cannot be undone.[/bold red]"
        )
        if not confirmed:
            raise typer.Abort

    try:
        api, cfg, username = _load_api()
        instance = api.find_instance_by_name(name, username)
        instance_id = instance["InstanceId"]
        public_ip = instance.get("PublicIpAddress")
        private_ip = instance.get("PrivateIpAddress")

        api.terminate_instance(instance_id)
        console.print(f"[green]✓[/green] Instance '{name}' ({instance_id}) terminating.")

        # Clean up SSH config and known_hosts
        if cfg.config.ssh.auto_update:
            alias = instance_host_name(name)
            remove_ssh_host(cfg.config.ssh.config_path, alias)
            ips = [ip for ip in [public_ip, private_ip, alias] if ip]
            if ips:
                removed = remove_known_hosts_entry(
                    str(Config.get_config_dir().parent.parent / ".ssh" / "known_hosts"),
                    ips,
                )
                if removed:
                    console.print(f"  Removed {removed} known_hosts entry/entries.")
    except (FileNotFoundError, EC2APIError) as e:
        _abort(str(e))


@app.command()
def resize(
    name: str = typer.Argument(help="Instance name", autocompletion=complete_instance_name),
    instance_type: str = typer.Argument(help="New instance type (e.g., t3.large)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Resize an instance type (causes downtime)."""
    if not yes:
        confirmed = Confirm.ask(
            f"Resize '[cyan]{name}[/cyan]' to [bold]{instance_type}[/bold]? "
            "This will stop and restart the instance."
        )
        if not confirmed:
            raise typer.Abort

    try:
        api, cfg, username = _load_api()
        instance = api.find_instance_by_name(name, username)
        instance_id = instance["InstanceId"]

        console.print(f"  Stopping instance '{name}'...")
        api.stop_instance(instance_id)
        api.wait_instance_state(instance_id, "stopped")
        console.print("  [green]✓[/green] Stopped.")

        console.print(f"  Changing instance type to {instance_type}...")
        api.modify_instance_type(instance_id, instance_type)
        console.print("  [green]✓[/green] Type updated.")

        console.print("  Starting instance...")
        api.start_instance(instance_id)
        updated = api.wait_instance_running(instance_id)
        new_ip = updated.get("PublicIpAddress", "")
        console.print(f"  [green]✓[/green] Running — IP: {new_ip}")

        if cfg.config.ssh.auto_update and new_ip:
            alias = instance_host_name(name)
            add_ssh_host(
                cfg.config.ssh.config_path,
                alias,
                new_ip,
                username,
                cfg.config.ssh.identity_file,
            )
    except (FileNotFoundError, EC2APIError) as e:
        _abort(str(e))


@app.command()
def on(
    name: str = typer.Argument(help="Instance name", autocompletion=complete_instance_name),
) -> None:
    """Start a stopped instance."""
    try:
        api, cfg, username = _load_api()
        instance = api.find_instance_by_name(name, username)
        instance_id = instance["InstanceId"]
        console.print(f"  Starting '{name}'...")
        api.start_instance(instance_id)
        updated = api.wait_instance_running(instance_id)
        new_ip = updated.get("PublicIpAddress", "")
        console.print(f"  [green]✓[/green] Running — IP: {new_ip}")

        if cfg.config.ssh.auto_update and new_ip:
            alias = instance_host_name(name)
            add_ssh_host(
                cfg.config.ssh.config_path,
                alias,
                new_ip,
                username,
                cfg.config.ssh.identity_file,
            )
    except (FileNotFoundError, EC2APIError) as e:
        _abort(str(e))


@app.command()
def off(
    name: str = typer.Argument(help="Instance name", autocompletion=complete_instance_name),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Stop a running instance."""
    if not yes:
        confirmed = Confirm.ask(f"Stop '[cyan]{name}[/cyan]'?")
        if not confirmed:
            raise typer.Abort

    try:
        api, _, username = _load_api()
        instance = api.find_instance_by_name(name, username)
        api.stop_instance(instance["InstanceId"])
        console.print(f"[green]✓[/green] Stop initiated for '{name}'.")
    except (FileNotFoundError, EC2APIError) as e:
        _abort(str(e))


@app.command()
def hibernate(
    name: str = typer.Argument(help="Instance name", autocompletion=complete_instance_name),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Hibernate an instance: create AMI snapshot then terminate it (saves cost)."""
    if not yes:
        confirmed = Confirm.ask(
            f"Hibernate '[cyan]{name}[/cyan]'? The instance will be terminated after snapshotting."
        )
        if not confirmed:
            raise typer.Abort

    try:
        api, cfg, username = _load_api()
        instance = api.find_instance_by_name(name, username)
        instance_id = instance["InstanceId"]
        instance_type = instance.get("InstanceType", "")
        region = cfg.config.aws.region

        # Stop the instance before imaging
        console.print(f"  Stopping '{name}'...")
        api.stop_instance(instance_id)
        api.wait_instance_state(instance_id, "stopped")
        console.print("  [green]✓[/green] Stopped.")

        # Create AMI
        ami_name = f"{HIBERNATE_AMI_PREFIX}-{name}"
        console.print(f"  Creating AMI '{ami_name}'...")
        tags = [
            {"Key": "Name", "Value": name},
            {"Key": SKYOPS_TAG_KEY, "Value": username},
            {"Key": HIBERNATE_SIZE_TAG, "Value": instance_type},
            {"Key": HIBERNATE_REGION_TAG, "Value": region},
        ]
        ami_id = api.create_ami(instance_id, ami_name, tags)
        console.print(f"  [green]✓[/green] AMI created: {ami_id}")

        # Terminate the instance
        console.print(f"  Terminating '{name}'...")
        api.terminate_instance(instance_id)
        console.print("  [green]✓[/green] Instance terminated.")

        # Clean up SSH config
        if cfg.config.ssh.auto_update:
            alias = instance_host_name(name)
            remove_ssh_host(cfg.config.ssh.config_path, alias)

        console.print(
            f"\n[bold green]'{name}' hibernated.[/bold green] "
            f"Restore with: [bold]skyops wake {name}[/bold]"
        )
    except (FileNotFoundError, EC2APIError) as e:
        _abort(str(e))


@app.command()
def wake(
    name: str = typer.Argument(help="Instance name", autocompletion=complete_hibernate_name),
    keep_ami: bool = typer.Option(False, "--keep-ami", help="Keep the AMI after waking"),
) -> None:
    """Wake a hibernated instance: restore from AMI snapshot."""
    try:
        api, cfg, username = _load_api()
        ami = api.find_hibernate_ami(name, username)
        ami_id = ami["ImageId"]
        instance_type = _get_tag(ami, HIBERNATE_SIZE_TAG) or cfg.config.defaults.instance_type

        console.print(f"\n  Waking '{name}' from AMI {ami_id}...")

        # Resolve networking
        vpc_id = cfg.config.defaults.vpc_id
        subnet_id = cfg.config.defaults.subnet_id
        sg_id = cfg.config.defaults.security_group_id

        if not vpc_id:
            vpc_id = api.get_default_vpc()
        if vpc_id and not subnet_id:
            subnets = api.get_subnets(vpc_id)
            supported_azs = api.get_supported_azs(instance_type)
            compatible = [s for s in subnets if s.get("AvailabilityZone") in supported_azs]
            subnet_id = compatible[0]["SubnetId"] if compatible else None
        if vpc_id and not sg_id:
            sg_id = api.get_or_create_security_group(vpc_id)

        user_data = render_user_data(
            username=username,
            ssh_key_paths=cfg.config.userdata.ssh_keys,
            template_path=cfg.config.userdata.template_path,
            tailscale_enabled=cfg.config.userdata.tailscale_enabled,
        )

        instance = api.launch_instance(
            name=name,
            instance_type=instance_type,
            ami=ami_id,
            key_pair_name=cfg.config.defaults.key_pair_name,
            subnet_id=subnet_id,
            security_group_ids=[sg_id] if sg_id else [],
            user_data=user_data,
            owner=username,
        )
        instance_id = instance["InstanceId"]
        console.print(f"  [green]✓[/green] Instance launched: {instance_id}")

        updated = api.wait_instance_running(instance_id)
        public_ip = updated.get("PublicIpAddress", "")
        console.print(f"  [green]✓[/green] Running — IP: {public_ip}")

        if cfg.config.ssh.auto_update and public_ip:
            alias = instance_host_name(name)
            add_ssh_host(
                cfg.config.ssh.config_path,
                alias,
                public_ip,
                username,
                cfg.config.ssh.identity_file,
            )
            console.print(f"  SSH config updated — connect with: [bold]ssh {alias}[/bold]")

        # Optionally delete the AMI
        if not keep_ami:
            delete_ami = Confirm.ask("Delete the hibernation AMI (recommended)?", default=True)
            if delete_ami:
                snapshot_ids = api.get_ami_snapshot_ids(ami_id)
                api.deregister_ami(ami_id)
                for snap_id in snapshot_ids:
                    api.delete_snapshot(snap_id)
                console.print("  [green]✓[/green] AMI and snapshots deleted.")

    except (FileNotFoundError, EC2APIError) as e:
        _abort(str(e))


@app.command(name="list-key-pairs")
def list_key_pairs() -> None:
    """List AWS key pairs managed by skyops."""
    try:
        api, _, _ = _load_api()
        key_pairs = api.list_key_pairs()
        display_key_pairs(key_pairs)
    except (FileNotFoundError, EC2APIError) as e:
        _abort(str(e))


@app.command(name="add-key-pair")
def add_key_pair(
    key_path: str = typer.Argument(help="Path to SSH public key file"),
    pair_name: str | None = typer.Option(None, "--name", help="Key pair name in AWS"),
) -> None:
    """Import an SSH public key into AWS as a key pair."""
    try:
        Config.validate_ssh_public_key(key_path)
        public_key = Config.read_ssh_key_content(key_path)
        api, _, username = _load_api()
        name = pair_name or f"skyops-{username}"
        api.import_key_pair(name, public_key)
        console.print(f"[green]✓[/green] Key pair '{name}' imported.")
    except (FileNotFoundError, ValueError, EC2APIError) as e:
        _abort(str(e))


@app.command(name="delete-key-pair")
def delete_key_pair(
    name: str = typer.Argument(help="Key pair name"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Delete an AWS key pair."""
    if not yes:
        confirmed = Confirm.ask(f"Delete key pair '[cyan]{name}[/cyan]'?")
        if not confirmed:
            raise typer.Abort

    try:
        api, _, _ = _load_api()
        api.delete_key_pair(name)
        console.print(f"[green]✓[/green] Key pair '{name}' deleted.")
    except (FileNotFoundError, EC2APIError) as e:
        _abort(str(e))


@app.command(name="list-regions")
def list_regions() -> None:
    """List available AWS regions."""
    try:
        api, _, _ = _load_api()
        regions = api.list_regions()
        display_regions(regions)
    except (FileNotFoundError, EC2APIError) as e:
        _abort(str(e))


@app.command()
def version() -> None:
    """Show skyops version."""
    console.print(f"skyops {__version__}")


if __name__ == "__main__":
    app()
