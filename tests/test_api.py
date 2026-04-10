"""Tests for skyops.api module (boto3 mocked)."""

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from skyops.api import EC2API, EC2APIError, _get_tag

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _client_error(code: str, message: str = "Error") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": message}}, "operation")


def _make_api(region: str = "us-east-1") -> tuple[EC2API, MagicMock, MagicMock]:
    with (
        patch("skyops.api.boto3.Session") as mock_session_cls,
    ):
        mock_session = MagicMock()
        mock_ec2 = MagicMock()
        mock_sts = MagicMock()
        mock_session.client.side_effect = lambda service, **_: (
            mock_ec2 if service == "ec2" else mock_sts
        )
        mock_session_cls.return_value = mock_session
        api = EC2API(region=region)
        return api, mock_ec2, mock_sts


# ------------------------------------------------------------------
# _get_tag helper
# ------------------------------------------------------------------


class TestGetTag:
    def test_returns_value(self):
        resource = {"Tags": [{"Key": "Name", "Value": "my-instance"}]}
        assert _get_tag(resource, "Name") == "my-instance"

    def test_returns_none_for_missing_key(self):
        resource = {"Tags": [{"Key": "Name", "Value": "x"}]}
        assert _get_tag(resource, "Missing") is None

    def test_returns_none_for_no_tags(self):
        assert _get_tag({}, "Name") is None


# ------------------------------------------------------------------
# get_username
# ------------------------------------------------------------------


class TestGetUsername:
    def test_user_arn(self):
        api, _, mock_sts = _make_api()
        mock_sts.get_caller_identity.return_value = {"Arn": "arn:aws:iam::123456789012:user/alice"}
        assert api.get_username() == "alice"

    def test_sanitizes_special_chars(self):
        api, _, mock_sts = _make_api()
        mock_sts.get_caller_identity.return_value = {
            "Arn": "arn:aws:sts::123456789012:assumed-role/role/alice.smith@example"
        }
        username = api.get_username()
        assert username.isidentifier() or username.replace("_", "").isalnum()

    def test_sts_error_raises(self):
        api, _, mock_sts = _make_api()
        mock_sts.get_caller_identity.side_effect = _client_error("AccessDenied")
        with pytest.raises(EC2APIError):
            api.get_username()


# ------------------------------------------------------------------
# list_instances
# ------------------------------------------------------------------


class TestListInstances:
    def test_returns_instances(self):
        api, mock_ec2, _ = _make_api()
        mock_ec2.describe_instances.return_value = {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "InstanceId": "i-12345678",
                            "State": {"Name": "running"},
                            "Tags": [{"Key": "skyops:owner", "Value": "alice"}],
                        }
                    ]
                }
            ]
        }
        instances = api.list_instances("alice")
        assert len(instances) == 1
        assert instances[0]["InstanceId"] == "i-12345678"

    def test_empty_reservations(self):
        api, mock_ec2, _ = _make_api()
        mock_ec2.describe_instances.return_value = {"Reservations": []}
        assert api.list_instances("alice") == []

    def test_api_error_raises(self):
        api, mock_ec2, _ = _make_api()
        mock_ec2.describe_instances.side_effect = _client_error("UnauthorizedAccess")
        with pytest.raises(EC2APIError):
            api.list_instances("alice")


# ------------------------------------------------------------------
# find_instance_by_name
# ------------------------------------------------------------------


class TestFindInstanceByName:
    def test_finds_instance(self):
        api, mock_ec2, _ = _make_api()
        mock_ec2.describe_instances.return_value = {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "InstanceId": "i-aaaa",
                            "State": {"Name": "running"},
                            "Tags": [
                                {"Key": "skyops:owner", "Value": "alice"},
                                {"Key": "Name", "Value": "dev"},
                            ],
                        }
                    ]
                }
            ]
        }
        instance = api.find_instance_by_name("dev", "alice")
        assert instance["InstanceId"] == "i-aaaa"

    def test_not_found_raises(self):
        api, mock_ec2, _ = _make_api()
        mock_ec2.describe_instances.return_value = {"Reservations": []}
        with pytest.raises(EC2APIError, match="not found"):
            api.find_instance_by_name("missing", "alice")

    def test_multiple_matches_raises(self):
        api, mock_ec2, _ = _make_api()
        mock_ec2.describe_instances.return_value = {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "InstanceId": "i-1",
                            "State": {"Name": "running"},
                            "Tags": [
                                {"Key": "skyops:owner", "Value": "alice"},
                                {"Key": "Name", "Value": "dev"},
                            ],
                        },
                        {
                            "InstanceId": "i-2",
                            "State": {"Name": "running"},
                            "Tags": [
                                {"Key": "skyops:owner", "Value": "alice"},
                                {"Key": "Name", "Value": "dev"},
                            ],
                        },
                    ]
                }
            ]
        }
        with pytest.raises(EC2APIError, match="Multiple"):
            api.find_instance_by_name("dev", "alice")


# ------------------------------------------------------------------
# key_pair_exists
# ------------------------------------------------------------------


class TestKeyPairExists:
    def test_exists(self):
        api, mock_ec2, _ = _make_api()
        mock_ec2.describe_key_pairs.return_value = {"KeyPairs": [{"KeyName": "skyops-alice"}]}
        assert api.key_pair_exists("skyops-alice") is True

    def test_not_found(self):
        api, mock_ec2, _ = _make_api()
        mock_ec2.describe_key_pairs.side_effect = _client_error("InvalidKeyPair.NotFound")
        assert api.key_pair_exists("skyops-alice") is False


# ------------------------------------------------------------------
# get_default_vpc
# ------------------------------------------------------------------


class TestGetDefaultVPC:
    def test_returns_vpc_id(self):
        api, mock_ec2, _ = _make_api()
        mock_ec2.describe_vpcs.return_value = {"Vpcs": [{"VpcId": "vpc-abc123"}]}
        assert api.get_default_vpc() == "vpc-abc123"

    def test_returns_none_when_no_default(self):
        api, mock_ec2, _ = _make_api()
        mock_ec2.describe_vpcs.return_value = {"Vpcs": []}
        assert api.get_default_vpc() is None


# ------------------------------------------------------------------
# terminate_instance
# ------------------------------------------------------------------


class TestTerminateInstance:
    def test_calls_terminate(self):
        api, mock_ec2, _ = _make_api()
        mock_ec2.terminate_instances.return_value = {}
        api.terminate_instance("i-12345")
        mock_ec2.terminate_instances.assert_called_once_with(InstanceIds=["i-12345"])

    def test_api_error_raises(self):
        api, mock_ec2, _ = _make_api()
        mock_ec2.terminate_instances.side_effect = _client_error("UnauthorizedAccess")
        with pytest.raises(EC2APIError):
            api.terminate_instance("i-12345")


# ------------------------------------------------------------------
# find_latest_ubuntu_ami
# ------------------------------------------------------------------


class TestFindLatestUbuntuAMI:
    def test_returns_most_recent(self):
        api, mock_ec2, _ = _make_api()
        mock_ec2.describe_images.return_value = {
            "Images": [
                {"ImageId": "ami-old", "CreationDate": "2023-01-01T00:00:00Z"},
                {"ImageId": "ami-new", "CreationDate": "2024-01-01T00:00:00Z"},
            ]
        }
        assert api.find_latest_ubuntu_ami() == "ami-new"

    def test_no_images_raises(self):
        api, mock_ec2, _ = _make_api()
        mock_ec2.describe_images.return_value = {"Images": []}
        with pytest.raises(EC2APIError, match="No Ubuntu"):
            api.find_latest_ubuntu_ami()


# ------------------------------------------------------------------
# get_or_create_security_group
# ------------------------------------------------------------------


class TestGetOrCreateSG:
    def test_returns_existing(self):
        api, mock_ec2, _ = _make_api()
        mock_ec2.describe_security_groups.return_value = {
            "SecurityGroups": [{"GroupId": "sg-existing"}]
        }
        assert api.get_or_create_security_group("vpc-abc") == "sg-existing"
        mock_ec2.create_security_group.assert_not_called()

    def test_creates_when_not_found(self):
        api, mock_ec2, _ = _make_api()
        mock_ec2.describe_security_groups.return_value = {"SecurityGroups": []}
        mock_ec2.create_security_group.return_value = {"GroupId": "sg-new"}
        mock_ec2.authorize_security_group_ingress.return_value = {}
        assert api.get_or_create_security_group("vpc-abc") == "sg-new"
        mock_ec2.create_security_group.assert_called_once()
        mock_ec2.authorize_security_group_ingress.assert_called_once()

    def test_does_not_restrict_egress_on_new_sg(self):
        """AWS default SG policy allows all outbound — SkyOps must not restrict it."""
        api, mock_ec2, _ = _make_api()
        mock_ec2.describe_security_groups.return_value = {"SecurityGroups": []}
        mock_ec2.create_security_group.return_value = {"GroupId": "sg-new"}
        mock_ec2.authorize_security_group_ingress.return_value = {}

        api.get_or_create_security_group("vpc-abc")

        mock_ec2.authorize_security_group_egress.assert_not_called()

    def test_restricted_sg_only_allows_caller_ip(self):
        """--restrict-ssh creates a per-instance SG with /32 CIDR, not 0.0.0.0/0."""
        api, mock_ec2, _ = _make_api()
        mock_ec2.describe_security_groups.return_value = {"SecurityGroups": []}
        mock_ec2.create_security_group.return_value = {"GroupId": "sg-restricted"}
        mock_ec2.authorize_security_group_ingress.return_value = {}

        api.get_or_create_security_group("vpc-abc", caller_ip="1.2.3.4", instance_name="mybox")

        call_args = mock_ec2.authorize_security_group_ingress.call_args
        ip_permissions = call_args.kwargs["IpPermissions"]
        cidr = ip_permissions[0]["IpRanges"][0]["CidrIp"]
        assert cidr == "1.2.3.4/32"
        assert "0.0.0.0/0" not in cidr
