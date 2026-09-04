"""AWS EC2 API wrapper using boto3."""

import ipaddress
import re
import time
import urllib.request
from typing import Any, cast

import boto3
from botocore.exceptions import BotoCoreError, ClientError

SKYOPS_TAG_KEY = "skyops:owner"
SKYOPS_MANAGED_TAG = "skyops:managed"
HIBERNATE_AMI_PREFIX = "skyops-hibernate"
HIBERNATE_SIZE_TAG = "skyops:size"
HIBERNATE_REGION_TAG = "skyops:region"

# Seconds between polling loops
_POLL_INTERVAL = 5


class EC2APIError(Exception):
    """Raised when an AWS API call fails."""

    def __init__(self, message: str, code: str | None = None) -> None:
        self.code = code
        super().__init__(message)


class EC2API:
    """Thin boto3 wrapper for EC2 and STS operations used by skyops."""

    def __init__(self, region: str, profile: str | None = None) -> None:
        session = boto3.Session(profile_name=profile, region_name=region)
        self._ec2 = session.client("ec2")
        self._sts = session.client("sts")
        self.region = region

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    def get_caller_identity(self) -> dict[str, Any]:
        try:
            return cast("dict[str, Any]", self._sts.get_caller_identity())
        except (BotoCoreError, ClientError) as e:
            raise EC2APIError(f"Failed to get caller identity: {e}")

    def get_caller_ip(self) -> str:
        """Fetch the caller's current public IP from checkip.amazonaws.com."""
        try:
            with urllib.request.urlopen("https://checkip.amazonaws.com", timeout=5) as resp:
                return resp.read().decode().strip()
        except Exception as e:
            raise EC2APIError(f"Failed to fetch caller public IP: {e}")

    def get_username(self) -> str:
        """Derive a Linux-safe username from the IAM ARN."""
        identity = self.get_caller_identity()
        arn = identity.get("Arn", "")
        # arn:aws:iam::123456789:user/alice  or  assumed-role/role/session
        username = arn.split("/")[-1] if "/" in arn else "user"
        username = re.sub(r"[^a-z0-9_]", "_", username.lower()).strip("_")
        if username and not username[0].isalpha():
            username = "u" + username
        return username or "user"

    # ------------------------------------------------------------------
    # Instances
    # ------------------------------------------------------------------

    def launch_instance(
        self,
        name: str,
        instance_type: str,
        ami: str,
        key_pair_name: str,
        subnet_id: str | None,
        security_group_ids: list[str],
        user_data: str,
        owner: str,
        extra_tags: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        tags: list[dict[str, str]] = [
            {"Key": "Name", "Value": name},
            {"Key": SKYOPS_TAG_KEY, "Value": owner},
            {"Key": SKYOPS_MANAGED_TAG, "Value": "true"},
            *(extra_tags or []),
        ]
        params: dict[str, Any] = {
            "ImageId": ami,
            "InstanceType": instance_type,
            "KeyName": key_pair_name,
            "MinCount": 1,
            "MaxCount": 1,
            "UserData": user_data,
            "TagSpecifications": [
                {"ResourceType": "instance", "Tags": tags},
                {"ResourceType": "volume", "Tags": tags},
            ],
            "SecurityGroupIds": security_group_ids,
        }
        if subnet_id:
            params["SubnetId"] = subnet_id
        try:
            resp = self._ec2.run_instances(**params)
            return cast("dict[str, Any]", resp["Instances"][0])
        except (BotoCoreError, ClientError) as e:
            raise EC2APIError(f"Failed to launch instance: {e}")

    def list_instances(self, owner: str) -> list[dict[str, Any]]:
        """List running/stopped instances owned by *owner*."""
        try:
            resp = self._ec2.describe_instances(
                Filters=[
                    {"Name": f"tag:{SKYOPS_TAG_KEY}", "Values": [owner]},
                    {
                        "Name": "instance-state-name",
                        "Values": ["pending", "running", "stopping", "stopped"],
                    },
                ]
            )
            instances = []
            for reservation in resp.get("Reservations", []):
                instances.extend(reservation.get("Instances", []))
            return instances
        except (BotoCoreError, ClientError) as e:
            raise EC2APIError(f"Failed to list instances: {e}")

    def describe_instance(self, instance_id: str) -> dict[str, Any]:
        try:
            resp = self._ec2.describe_instances(InstanceIds=[instance_id])
            reservations = resp.get("Reservations", [])
            if not reservations or not reservations[0].get("Instances"):
                raise EC2APIError(f"Instance not found: {instance_id}")
            return cast("dict[str, Any]", reservations[0]["Instances"][0])
        except (BotoCoreError, ClientError) as e:
            raise EC2APIError(f"Failed to describe instance {instance_id}: {e}")

    def find_instance_by_name(self, name: str, owner: str) -> dict[str, Any]:
        """Find an active instance by Name tag and owner.

        Raises:
            EC2APIError: If the instance is not found or multiple match.
        """
        instances = self.list_instances(owner)
        matches = [i for i in instances if _get_tag(i, "Name") == name]
        if not matches:
            raise EC2APIError(f"Instance '{name}' not found for owner '{owner}'.")
        if len(matches) > 1:
            ids = ", ".join(i["InstanceId"] for i in matches)
            raise EC2APIError(f"Multiple instances named '{name}': {ids}")
        return matches[0]

    def terminate_instance(self, instance_id: str) -> None:
        try:
            self._ec2.terminate_instances(InstanceIds=[instance_id])
        except (BotoCoreError, ClientError) as e:
            raise EC2APIError(f"Failed to terminate instance {instance_id}: {e}")

    def stop_instance(self, instance_id: str) -> None:
        try:
            self._ec2.stop_instances(InstanceIds=[instance_id])
        except (BotoCoreError, ClientError) as e:
            raise EC2APIError(f"Failed to stop instance {instance_id}: {e}")

    def start_instance(self, instance_id: str) -> None:
        try:
            self._ec2.start_instances(InstanceIds=[instance_id])
        except (BotoCoreError, ClientError) as e:
            raise EC2APIError(f"Failed to start instance {instance_id}: {e}")

    def rename_instance(self, instance_id: str, new_name: str) -> None:
        try:
            self._ec2.create_tags(
                Resources=[instance_id],
                Tags=[{"Key": "Name", "Value": new_name}],
            )
        except (BotoCoreError, ClientError) as e:
            raise EC2APIError(f"Failed to rename instance {instance_id}: {e}")

    def modify_instance_type(self, instance_id: str, instance_type: str) -> None:
        """Change the instance type. Instance must be stopped first."""
        try:
            self._ec2.modify_instance_attribute(
                InstanceId=instance_id,
                InstanceType={"Value": instance_type},
            )
        except (BotoCoreError, ClientError) as e:
            raise EC2APIError(f"Failed to modify instance type for {instance_id}: {e}")

    def wait_instance_state(
        self,
        instance_id: str,
        state: str,
        timeout: int = 300,
    ) -> dict[str, Any]:
        """Poll until the instance reaches *state*.

        Returns:
            The final instance dict.

        Raises:
            EC2APIError: On timeout or AWS error.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            instance = self.describe_instance(instance_id)
            current = instance.get("State", {}).get("Name", "")
            if current == state:
                return instance
            if current in ("terminated", "shutting-down") and state not in (
                "terminated",
                "shutting-down",
            ):
                raise EC2APIError(f"Instance {instance_id} entered terminal state '{current}'.")
            time.sleep(_POLL_INTERVAL)
        raise EC2APIError(
            f"Timed out waiting for instance {instance_id} to reach state '{state}' "
            f"(timeout={timeout}s)."
        )

    def wait_instance_running(self, instance_id: str, timeout: int = 300) -> dict[str, Any]:
        """Wait until the instance is running and has a public IP."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            instance = self.describe_instance(instance_id)
            state = instance.get("State", {}).get("Name", "")
            public_ip = instance.get("PublicIpAddress")
            if state == "running" and public_ip:
                return instance
            if state in ("terminated", "shutting-down"):
                raise EC2APIError(f"Instance {instance_id} terminated unexpectedly.")
            time.sleep(_POLL_INTERVAL)
        raise EC2APIError(
            f"Timed out waiting for instance {instance_id} to be running with a public IP "
            f"(timeout={timeout}s)."
        )

    # ------------------------------------------------------------------
    # AMI / Hibernate
    # ------------------------------------------------------------------

    def create_ami(self, instance_id: str, name: str, tags: list[dict[str, str]]) -> str:
        """Create an AMI from the instance and wait until available.

        Returns:
            The AMI ID.
        """
        try:
            resp = self._ec2.create_image(
                InstanceId=instance_id,
                Name=name,
                NoReboot=False,
                TagSpecifications=[
                    {"ResourceType": "image", "Tags": tags},
                    {"ResourceType": "snapshot", "Tags": tags},
                ],
            )
            ami_id = resp["ImageId"]
        except (BotoCoreError, ClientError) as e:
            raise EC2APIError(f"Failed to create AMI from {instance_id}: {e}")

        self._wait_ami_available(ami_id)
        return ami_id

    def _wait_ami_available(self, ami_id: str, timeout: int = 600) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                resp = self._ec2.describe_images(ImageIds=[ami_id])
                images = resp.get("Images", [])
                if images and images[0].get("State") == "available":
                    return
                if images and images[0].get("State") == "failed":
                    raise EC2APIError(f"AMI {ami_id} creation failed.")
            except (BotoCoreError, ClientError) as e:
                raise EC2APIError(f"Error checking AMI state: {e}")
            time.sleep(_POLL_INTERVAL)
        raise EC2APIError(f"Timed out waiting for AMI {ami_id} to become available.")

    def list_hibernate_amis(self, owner: str) -> list[dict[str, Any]]:
        """List hibernated-instance AMIs owned by *owner*."""
        try:
            resp = self._ec2.describe_images(
                Owners=["self"],
                Filters=[{"Name": f"tag:{SKYOPS_TAG_KEY}", "Values": [owner]}],
            )
            return [
                cast("dict[str, Any]", img)
                for img in resp.get("Images", [])
                if img.get("Name", "").startswith(HIBERNATE_AMI_PREFIX)
            ]
        except (BotoCoreError, ClientError) as e:
            raise EC2APIError(f"Failed to list hibernate AMIs: {e}")

    def find_hibernate_ami(self, instance_name: str, owner: str) -> dict[str, Any]:
        amis = self.list_hibernate_amis(owner)
        matches = [a for a in amis if _get_tag(a, "Name") == instance_name]
        if not matches:
            raise EC2APIError(f"No hibernated snapshot found for '{instance_name}'.")
        return matches[0]

    def deregister_ami(self, ami_id: str) -> None:
        try:
            self._ec2.deregister_image(ImageId=ami_id)
        except (BotoCoreError, ClientError) as e:
            raise EC2APIError(f"Failed to deregister AMI {ami_id}: {e}")

    def get_ami_snapshot_ids(self, ami_id: str) -> list[str]:
        try:
            resp = self._ec2.describe_images(ImageIds=[ami_id])
            images = resp.get("Images", [])
            if not images:
                return []
            return [
                bdm["Ebs"]["SnapshotId"]
                for bdm in images[0].get("BlockDeviceMappings", [])
                if "Ebs" in bdm and "SnapshotId" in bdm["Ebs"]
            ]
        except (BotoCoreError, ClientError) as e:
            raise EC2APIError(f"Failed to describe AMI {ami_id}: {e}")

    def delete_snapshot(self, snapshot_id: str) -> None:
        try:
            self._ec2.delete_snapshot(SnapshotId=snapshot_id)
        except (BotoCoreError, ClientError) as e:
            raise EC2APIError(f"Failed to delete snapshot {snapshot_id}: {e}")

    # ------------------------------------------------------------------
    # Key Pairs
    # ------------------------------------------------------------------

    def import_key_pair(self, name: str, public_key_material: str) -> dict[str, Any]:
        try:
            return cast(
                "dict[str, Any]",
                self._ec2.import_key_pair(
                    KeyName=name,
                    PublicKeyMaterial=public_key_material.encode(),
                    TagSpecifications=[
                        {
                            "ResourceType": "key-pair",
                            "Tags": [{"Key": SKYOPS_MANAGED_TAG, "Value": "true"}],
                        }
                    ],
                ),
            )
        except (BotoCoreError, ClientError) as e:
            raise EC2APIError(f"Failed to import key pair '{name}': {e}")

    def delete_key_pair(self, name: str) -> None:
        try:
            self._ec2.delete_key_pair(KeyName=name)
        except (BotoCoreError, ClientError) as e:
            raise EC2APIError(f"Failed to delete key pair '{name}': {e}")

    def list_key_pairs(self) -> list[dict[str, Any]]:
        try:
            resp = self._ec2.describe_key_pairs(
                Filters=[{"Name": f"tag:{SKYOPS_MANAGED_TAG}", "Values": ["true"]}]
            )
            return [cast("dict[str, Any]", kp) for kp in resp.get("KeyPairs", [])]
        except (BotoCoreError, ClientError) as e:
            raise EC2APIError(f"Failed to list key pairs: {e}")

    def key_pair_exists(self, name: str) -> bool:
        try:
            resp = self._ec2.describe_key_pairs(KeyNames=[name])
            return bool(resp.get("KeyPairs"))
        except ClientError as e:
            if e.response["Error"]["Code"] == "InvalidKeyPair.NotFound":
                return False
            raise EC2APIError(f"Error checking key pair '{name}': {e}")
        except BotoCoreError as e:
            raise EC2APIError(f"Error checking key pair '{name}': {e}")

    # ------------------------------------------------------------------
    # VPC / Networking
    # ------------------------------------------------------------------

    def get_default_vpc(self) -> str | None:
        """Return the ID of the default VPC, or None if not found."""
        try:
            resp = self._ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])
            vpcs = resp.get("Vpcs", [])
            return vpcs[0]["VpcId"] if vpcs else None
        except (BotoCoreError, ClientError) as e:
            raise EC2APIError(f"Failed to describe VPCs: {e}")

    def get_subnets(self, vpc_id: str) -> list[dict[str, Any]]:
        try:
            resp = self._ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])
            return [cast("dict[str, Any]", s) for s in resp.get("Subnets", [])]
        except (BotoCoreError, ClientError) as e:
            raise EC2APIError(f"Failed to describe subnets for VPC {vpc_id}: {e}")

    def get_supported_azs(self, instance_type: str) -> set[str]:
        """Return the set of AZ names that support the given instance type."""
        try:
            resp = self._ec2.describe_instance_type_offerings(
                LocationType="availability-zone",
                Filters=[{"Name": "instance-type", "Values": [instance_type]}],
            )
            return {o["Location"] for o in resp.get("InstanceTypeOfferings", [])}
        except (BotoCoreError, ClientError) as e:
            raise EC2APIError(f"Failed to describe instance type offerings: {e}")

    def get_or_create_security_group(
        self,
        vpc_id: str,
        caller_ip: str | None = None,
        instance_name: str | None = None,
    ) -> str:
        """Return existing SG ID or create it.

        When *caller_ip* is provided (with *instance_name*), creates a per-instance
        security group named ``skyops-<instance_name>`` that restricts SSH ingress to
        ``<caller_ip>/32`` instead of ``0.0.0.0/0``.  Otherwise uses/creates the shared
        ``skyops-default`` group.
        """
        if caller_ip and instance_name:
            sg_name = f"skyops-{instance_name}"
            addr = ipaddress.ip_address(caller_ip)
            prefix = 128 if isinstance(addr, ipaddress.IPv6Address) else 32
            ssh_cidr = f"{caller_ip}/{prefix}"
            description = f"skyops per-instance security group for {instance_name} - SSH restricted"
        else:
            sg_name = "skyops-default"
            ssh_cidr = "0.0.0.0/0"
            description = "skyops managed security group - SSH ingress"

        try:
            resp = self._ec2.describe_security_groups(
                Filters=[
                    {"Name": "group-name", "Values": [sg_name]},
                    {"Name": "vpc-id", "Values": [vpc_id]},
                ]
            )
            groups = resp.get("SecurityGroups", [])
            if groups:
                return groups[0]["GroupId"]
        except (BotoCoreError, ClientError) as e:
            raise EC2APIError(f"Failed to describe security groups: {e}")

        try:
            resp = self._ec2.create_security_group(
                GroupName=sg_name,
                Description=description,
                VpcId=vpc_id,
                TagSpecifications=[
                    {
                        "ResourceType": "security-group",
                        "Tags": [{"Key": SKYOPS_MANAGED_TAG, "Value": "true"}],
                    }
                ],
            )
            sg_id = resp["GroupId"]
            self._ec2.authorize_security_group_ingress(
                GroupId=sg_id,
                IpPermissions=[
                    {
                        "IpProtocol": "tcp",
                        "FromPort": 22,
                        "ToPort": 22,
                        "IpRanges": [{"CidrIp": ssh_cidr, "Description": "SSH"}],
                    }
                ],
            )
            return sg_id
        except (BotoCoreError, ClientError) as e:
            raise EC2APIError(f"Failed to create security group: {e}")

    # ------------------------------------------------------------------
    # AMI discovery
    # ------------------------------------------------------------------

    def find_latest_ubuntu_ami(self, version: str = "24.04") -> str:
        """Find the latest official Ubuntu *version* LTS AMI for the region.

        Returns:
            AMI ID of the most recent matching image.
        """
        name_pattern = f"ubuntu/images/hvm-ssd*/ubuntu-*-{version}-amd64-server-*"
        try:
            resp = self._ec2.describe_images(
                Owners=["099720109477"],  # Canonical's AWS account
                Filters=[
                    {"Name": "name", "Values": [name_pattern]},
                    {"Name": "state", "Values": ["available"]},
                    {"Name": "architecture", "Values": ["x86_64"]},
                    {"Name": "virtualization-type", "Values": ["hvm"]},
                ],
            )
            images = sorted(
                resp.get("Images", []),
                key=lambda x: x.get("CreationDate", ""),
                reverse=True,
            )
            if not images:
                raise EC2APIError(f"No Ubuntu {version} AMI found in region {self.region}.")
            return images[0]["ImageId"]
        except (BotoCoreError, ClientError) as e:
            raise EC2APIError(f"Failed to find Ubuntu AMI: {e}")

    def list_instance_types(self) -> list[dict[str, Any]]:
        """Return a curated list of common instance types with pricing hints."""
        common_types = [
            "t3.micro",
            "t3.small",
            "t3.medium",
            "t3.large",
            "t3.xlarge",
            "t3.2xlarge",
            "m6i.large",
            "m6i.xlarge",
            "c6i.large",
            "c6i.xlarge",
            "r6i.large",
        ]
        try:
            resp = self._ec2.describe_instance_types(InstanceTypes=common_types)
            return [cast("dict[str, Any]", it) for it in resp.get("InstanceTypes", [])]
        except (BotoCoreError, ClientError) as e:
            raise EC2APIError(f"Failed to describe instance types: {e}")

    def list_regions(self) -> list[str]:
        try:
            resp = self._ec2.describe_regions(AllRegions=False)
            return sorted(r["RegionName"] for r in resp.get("Regions", []))
        except (BotoCoreError, ClientError) as e:
            raise EC2APIError(f"Failed to list regions: {e}")


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _get_tag(resource: dict[str, Any], key: str) -> str | None:
    """Return the value of the first tag matching *key*, or None."""
    for tag in resource.get("Tags", []):
        if tag.get("Key") == key:
            return tag.get("Value")
    return None
