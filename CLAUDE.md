# SkyOps

CLI tool for managing AWS EC2 instances with automated setup, SSH configuration, and lifecycle management.

## Critical Rules ⚠️

- **Never use `pip`** — always use `uv` for all Python operations
- **Always run `prek run`** before committing (or `prek install` to auto-run on commit)
- **Keep README.md in sync** when adding commands or features

## Quick Commands

```bash
uv sync                      # Install dependencies
prek install                 # Set up pre-commit hooks (one-time)
prek run                     # Run all checks (ruff, ty, shellcheck, etc.)
uv run pytest                # Run tests
uv run skyops --help         # CLI help
```

## Project Structure

```
src/skyops/
├── main.py          # CLI entry point (Typer, 17 commands)
├── api.py           # EC2API wrapper (boto3)
├── config.py        # Pydantic config models + file I/O
├── ssh_config.py    # SSH config file management
├── ui.py            # Rich display helpers
├── userdata.py      # Jinja2 cloud-init template rendering
├── lock.py          # File-based per-instance locking
└── templates/
    └── default-userdata.sh  # Cloud-init shell script template
tests/               # pytest tests (80% coverage required)
```

## Technology Stack

- **Python 3.13+** with `uv` (NOT pip)
- **CLI**: Typer + Rich
- **API**: boto3 (EC2 + STS)
- **Config**: YAML + Pydantic 2.x validation at `~/.config/skyops/config.yaml`
- **Templating**: Jinja2 for cloud-init userdata
- **Code Quality**: Ruff (linter + formatter), ty (types)

## Key Conventions

### Username

- **Derived from IAM ARN**, not configured
- Fetched via STS `get_caller_identity()`, sanitized for Linux compatibility
- `arn:aws:iam::123456789:user/john.doe` → `john_doe`

### SSH Hostname

- All SSH entries use `skyops.<instance-name>` format
- Centralized in `instance_host_name()` in `ssh_config.py`
- Avoids collisions with other SSH aliases

### Tags

- Owner tag key: `skyops:owner` (set to IAM username)
- Managed tag: `skyops:managed = true`
- Additional tags via `--tags` or config `extra_tags` **extend** defaults (never replace)

### SSH Keys

- Only public keys (`*.pub`) accepted
- Strict validation rejects private keys
- Auto-detects `id_ed25519.pub`, `id_rsa.pub`, `id_ecdsa.pub`

### Networking

- Uses the account's default VPC unless `vpc_id`/`subnet_id` overridden in config
- Auto-creates `skyops-default` security group (SSH-only ingress) on first use

### Hibernation

- Creates an AMI named `skyops-hibernate-<owner>-<instance-name>`
- Tags AMI with `skyops:size` and `skyops:region` to restore exact configuration
- Terminates instance after AMI is available

## Architecture Decisions

1. **Username from IAM ARN** — Derived on demand, never stored in config. Consistent across machines.

2. **SSH key validation** — Prevents accidental private key upload. Validates format (ssh-rsa, ssh-ed25519, ecdsa-sha2-*, etc.).

3. **Tags extend defaults** — `--tags` adds to defaults, never replaces. Ensures `skyops:owner` tag always present.

4. **boto3 over REST** — AWS SDK handles signing, retries, pagination, and credential chain automatically.

5. **SSH config namespacing** — `skyops.*` prefix on all SSH hosts prevents collisions with other tools.

6. **Pydantic validation** — Runtime type safety for config files. Clear errors for invalid configurations.

7. **SSH config backups** — Created at `~/.ssh/config.bak` before modifications. Each backup overwrites previous.

8. **File-based locking** — Per-instance locks at `~/.config/skyops/locks/` prevent concurrent operations on the same instance.

## Gotchas & Troubleshooting

### AMI availability polling

`create_ami()` polls until the AMI state is `available`. Default timeout is 600 seconds.
This blocks `hibernate` — large instances take longer to snapshot.

### Instance state transitions

`start_instance()` and `stop_instance()` return immediately; use `wait_instance_state()` to block
until the desired state is reached before issuing subsequent API calls.

### Security group auto-creation

`get_or_create_security_group()` is idempotent. If the `skyops-default` group already exists
in the VPC, it reuses it rather than creating a duplicate.

### SSH config backups overwrite

Only keeps one backup at `~/.ssh/config.bak`.

### known_hosts cleanup

`destroy` removes the instance's IP from `~/.ssh/known_hosts` to prevent future host key conflicts.

## Development

### Package Management

```bash
uv add <package>             # Add dependency
uv add --dev <package>       # Add dev dependency
uv sync                      # Install all
uv run <command>             # Run in venv
```

### Linting

```bash
prek run                     # Run all checks (required before commit)
prek run --all-files         # Check all files, not just staged
uv run ruff check --fix .    # Lint + autofix only
uv run ty check src/skyops/  # Type check only
```

**Ruff config**: Python 3.13, 100-char lines, modern syntax (`str | None`, `list[str]`), S701 ignored for shell script templates.

### Testing

```bash
uv run pytest                              # All tests
uv run pytest tests/test_api.py            # Specific file
uv run pytest -k "validate_ssh"            # Pattern match
uv run pytest -v                           # Verbose
```

**Coverage**: Minimum 80% enforced via `--cov-fail-under=80` in pyproject.toml.

## Pydantic Models

- **`SkyOpsConfig`** — Root config
- **`AWSConfig`** — Region, optional named profile
- **`DefaultsConfig`** — Instance type, AMI ID, key pair name, optional VPC/subnet/SG, extra tags
- **`UserDataConfig`** — Optional custom template path, SSH key file paths (min 1)
- **`SSHConfig`** — SSH config path, auto-update flag, identity file path

Config file: `~/.config/skyops/config.yaml`

Lock files: `~/.config/skyops/locks/<instance-name>.lock`

## Shell Completion

```bash
skyops --install-completion zsh  # Enable tab completion
```

Dynamic completion for instance names filtered by the current IAM user's owner tag.
