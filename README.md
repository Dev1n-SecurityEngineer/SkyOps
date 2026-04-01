# SkyOps

CLI tool for managing AWS EC2 instances with automated setup, SSH configuration, and lifecycle management.

## Requirements

- Python 3.13+
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- AWS credentials configured (`~/.aws/credentials`, environment variables, or an IAM role)
- An SSH public key (`~/.ssh/id_ed25519.pub` or similar)

## Installation

```bash
git clone https://github.com/Dev1n-SecurityEngineer/SkyOps.git
cd SkyOps
uv sync
```

Verify it works:

```bash
uv run skyops --help
```

## First-time setup

Run the interactive wizard. It validates your AWS credentials, discovers your default VPC, finds available Ubuntu AMIs, and writes `~/.config/skyops/config.yaml`.

```bash
uv run skyops init
```

You will be prompted for:

- **AWS profile** - leave blank to use the default credential chain
- **Region** - e.g. `us-east-1`
- **Instance type** - default is `t3.medium`
- **SSH public key** - auto-detected from `~/.ssh/*.pub`
- **AWS key pair name** - the name under which your public key is registered in EC2

## Usage

### Create an instance

```bash
uv run skyops create my-dev-box
```

This will:

1. Launch a `t3.medium` Ubuntu 24.04 instance
2. Run cloud-init: create your user, install Docker, configure UFW, set up zsh
3. Add a `skyops.my-dev-box` entry to `~/.ssh/config`
4. Print the public IP when ready

SSH in immediately after:

```bash
ssh skyops.my-dev-box
```

Override defaults for a single run:

```bash
uv run skyops create my-dev-box --type t3.large --region eu-west-1
```

### List instances

```bash
uv run skyops list
```

### Instance details

```bash
uv run skyops info my-dev-box
```

### Start / stop

```bash
uv run skyops off my-dev-box   # stop (billing pauses for compute, EBS continues)
uv run skyops on  my-dev-box   # start again
```

### Resize

Stops the instance, changes the type, starts it again:

```bash
uv run skyops resize my-dev-box t3.xlarge
```

### Rename

```bash
uv run skyops rename my-dev-box better-name
```

### Hibernate (save cost, preserve state)

Creates an AMI from the instance then terminates it. Storage cost only while hibernated.

```bash
uv run skyops hibernate my-dev-box
```

Restore later:

```bash
uv run skyops wake my-dev-box
```

### Destroy

```bash
uv run skyops destroy my-dev-box
```

Prompts for confirmation. Removes the SSH config entry and cleans up `~/.ssh/known_hosts`.

### SSH config management

Add or refresh the SSH config entry for an already-running instance:

```bash
uv run skyops ssh-config my-dev-box
```

### Key pair management

```bash
uv run skyops list-key-pairs
uv run skyops add-key-pair ~/.ssh/id_ed25519.pub
uv run skyops delete-key-pair skyops-mykey
```

### Regions

```bash
uv run skyops list-regions
```

## Configuration

Config lives at `~/.config/skyops/config.yaml`. Edit it directly or re-run `skyops init`.

```yaml
aws:
  region: us-east-1
  profile: null          # null = default credential chain

defaults:
  region: us-east-1
  instance_type: t3.medium
  ami: ami-0c02fb55956c7d316   # Ubuntu 24.04 LTS (resolved at init)
  key_pair_name: my-key
  vpc_id: null           # null = use account default VPC
  subnet_id: null
  security_group_id: null
  extra_tags: []

userdata:
  template_path: null    # null = use built-in template
  ssh_keys:
    - /Users/you/.ssh/id_ed25519.pub
  tailscale_enabled: false

ssh:
  config_path: /Users/you/.ssh/config
  auto_update: true
  identity_file: /Users/you/.ssh/id_ed25519
```

### Custom cloud-init template

Point `userdata.template_path` at a Jinja2 shell script. Available variables:

| Variable | Type | Description |
| -------- | ---- | ----------- |
| `username` | `str` | Linux username derived from your IAM ARN |
| `ssh_keys` | `list[str]` | Public key contents (one per line) |
| `tailscale_enabled` | `bool` | Whether to install Tailscale |

## AWS permissions required

SkyOps needs the following IAM actions:

```text
sts:GetCallerIdentity
ec2:DescribeInstances
ec2:RunInstances
ec2:TerminateInstances
ec2:StartInstances
ec2:StopInstances
ec2:CreateTags
ec2:DescribeTags
ec2:ModifyInstanceAttribute
ec2:DescribeKeyPairs
ec2:ImportKeyPair
ec2:DeleteKeyPair
ec2:DescribeVpcs
ec2:DescribeSubnets
ec2:DescribeSecurityGroups
ec2:CreateSecurityGroup
ec2:AuthorizeSecurityGroupIngress
ec2:DescribeImages
ec2:CreateImage
ec2:DeregisterImage
ec2:DescribeSnapshots
ec2:DeleteSnapshot
ec2:DescribeInstanceTypes
ec2:DescribeRegions
```

## SSH conventions

All SSH entries use the `skyops.<name>` prefix to avoid collisions with other tools:

```ssh-config
Host skyops.my-dev-box
    HostName 1.2.3.4
    User john_doe
    IdentityFile ~/.ssh/id_ed25519
    ForwardAgent yes
```

## Troubleshooting

**`NoCredentialsError` / `Unable to locate credentials`**
Configure AWS credentials: `aws configure` or set `AWS_PROFILE`.

**SSH connection refused after create**
Cloud-init is still running. Wait ~2 minutes for the instance to finish setup, then retry.

**`skyops-default` security group already exists but SSH fails**
The group may be in a different VPC. Set `defaults.vpc_id` in your config to pin the VPC.

**Instance not found after rename**
SkyOps finds instances by the `Name` tag. Confirm the rename with `skyops list`.

## Development

```bash
uv sync
uv run pytest           # unit tests (80% coverage required)
make lint               # ruff + ty
make e2e                # real AWS lifecycle test (creates + destroys an instance)
```

See [CLAUDE.md](CLAUDE.md) for architecture decisions and conventions.
