"""Rich display helpers for skyops CLI output."""

from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from skyops.api import _get_tag

console = Console()
err_console = Console(stderr=True)


# ------------------------------------------------------------------
# Instance tables
# ------------------------------------------------------------------


def display_instances(instances: list[dict[str, Any]]) -> None:
    """Render a Rich table of EC2 instances."""
    if not instances:
        console.print("[dim]No instances found.[/dim]")
        return

    table = Table(title="EC2 Instances", show_lines=False, highlight=True)
    table.add_column("Name", style="bold cyan", no_wrap=True)
    table.add_column("Instance ID", style="dim")
    table.add_column("Type", style="green")
    table.add_column("State", no_wrap=True)
    table.add_column("Public IP")
    table.add_column("Region")
    table.add_column("AMI")

    for inst in instances:
        name = _get_tag(inst, "Name") or "[dim]—[/dim]"
        state = inst.get("State", {}).get("Name", "unknown")
        state_str = _colorize_state(state)
        table.add_row(
            name,
            inst.get("InstanceId", ""),
            inst.get("InstanceType", ""),
            state_str,
            inst.get("PublicIpAddress") or "[dim]—[/dim]",
            inst.get("Placement", {}).get("AvailabilityZone", ""),
            inst.get("ImageId", ""),
        )

    console.print(table)


def display_instance_info(inst: dict[str, Any]) -> None:
    """Render a detailed panel for a single EC2 instance."""
    name = _get_tag(inst, "Name") or "—"
    state = inst.get("State", {}).get("Name", "unknown")
    tags = "\n".join(f"  {t['Key']} = {t['Value']}" for t in inst.get("Tags", []))
    text = (
        f"[bold]Instance ID:[/bold]   {inst.get('InstanceId', '—')}\n"
        f"[bold]Name:[/bold]          {name}\n"
        f"[bold]State:[/bold]         {_colorize_state(state)}\n"
        f"[bold]Type:[/bold]          {inst.get('InstanceType', '—')}\n"
        f"[bold]AMI:[/bold]           {inst.get('ImageId', '—')}\n"
        f"[bold]Public IP:[/bold]     {inst.get('PublicIpAddress') or '—'}\n"
        f"[bold]Private IP:[/bold]    {inst.get('PrivateIpAddress') or '—'}\n"
        f"[bold]Key Pair:[/bold]      {inst.get('KeyName') or '—'}\n"
        f"[bold]VPC:[/bold]           {inst.get('VpcId') or '—'}\n"
        f"[bold]Subnet:[/bold]        {inst.get('SubnetId') or '—'}\n"
        f"[bold]AZ:[/bold]            {inst.get('Placement', {}).get('AvailabilityZone', '—')}\n"
        f"[bold]Launch time:[/bold]   {inst.get('LaunchTime', '—')}\n"
        f"[bold]Tags:[/bold]\n{tags or '  (none)'}"
    )
    console.print(Panel(text, title=f"[bold cyan]{name}[/bold cyan]", expand=False))


# ------------------------------------------------------------------
# AMI / hibernate tables
# ------------------------------------------------------------------


def display_hibernate_amis(amis: list[dict[str, Any]]) -> None:
    """Render a table of hibernated-instance AMIs."""
    if not amis:
        console.print("[dim]No hibernated instances found.[/dim]")
        return

    table = Table(title="Hibernated Instances", show_lines=False, highlight=True)
    table.add_column("Name", style="bold cyan", no_wrap=True)
    table.add_column("AMI ID", style="dim")
    table.add_column("Original Type", style="green")
    table.add_column("Region")
    table.add_column("Created")

    for ami in amis:
        table.add_row(
            _get_tag(ami, "Name") or "[dim]—[/dim]",
            ami.get("ImageId", ""),
            _get_tag(ami, "skyops:size") or "[dim]—[/dim]",
            _get_tag(ami, "skyops:region") or "[dim]—[/dim]",
            ami.get("CreationDate", ""),
        )

    console.print(table)


# ------------------------------------------------------------------
# Key pair table
# ------------------------------------------------------------------


def display_key_pairs(key_pairs: list[dict[str, Any]]) -> None:
    """Render a table of AWS key pairs."""
    if not key_pairs:
        console.print("[dim]No key pairs found.[/dim]")
        return

    table = Table(title="Key Pairs", show_lines=False, highlight=True)
    table.add_column("Name", style="bold cyan", no_wrap=True)
    table.add_column("Key ID", style="dim")
    table.add_column("Fingerprint")
    table.add_column("Created")

    for kp in key_pairs:
        table.add_row(
            kp.get("KeyName", ""),
            kp.get("KeyPairId", ""),
            kp.get("KeyFingerprint", ""),
            kp.get("CreateTime", ""),
        )

    console.print(table)


# ------------------------------------------------------------------
# Instance type / region selectors
# ------------------------------------------------------------------


def display_instance_types(instance_types: list[dict[str, Any]]) -> None:
    """Render a table of available instance types."""
    table = Table(title="Instance Types", show_lines=False)
    table.add_column("Type", style="bold cyan")
    table.add_column("vCPUs", justify="right")
    table.add_column("Memory (GiB)", justify="right")
    table.add_column("Architecture")

    for it in sorted(instance_types, key=lambda x: x.get("InstanceType", "")):
        vcpu = str(it.get("VCpuInfo", {}).get("DefaultVCpus", "—"))
        mem_mib = it.get("MemoryInfo", {}).get("SizeInMiB", 0)
        mem_gib = f"{mem_mib / 1024:.1f}" if mem_mib else "—"
        arch = ", ".join(it.get("ProcessorInfo", {}).get("SupportedArchitectures", []))
        table.add_row(it.get("InstanceType", ""), vcpu, mem_gib, arch)

    console.print(table)


def display_regions(regions: list[str]) -> None:
    """Print available AWS regions in columns."""
    table = Table(title="AWS Regions", show_lines=False, show_header=False)
    table.add_column("Region", style="cyan")
    for region in regions:
        table.add_row(region)
    console.print(table)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _colorize_state(state: str) -> str:
    colors = {
        "running": "bold green",
        "stopped": "yellow",
        "stopping": "yellow",
        "pending": "blue",
        "terminated": "dim red",
        "shutting-down": "dim red",
    }
    color = colors.get(state, "white")
    return f"[{color}]{state}[/{color}]"
