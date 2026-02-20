import ipaddress
import json
import os
from typing import Any

import pulumi
import pulumi_aws as aws
import pulumi_tls as tls


def _save_key_to_file(key_pem: str) -> str:
    with open("private_key.pem", "w", encoding="utf-8") as file:
        file.write(key_pem)

    os.chmod("private_key.pem", 0o600)
    return "private_key.pem"


def _default_alb_subnet_cidrs(vpc_cidr: str) -> list[str]:
    """Return two /24 CIDRs inside the VPC CIDR.

    This mirrors the comexsoft/prefect-server convenience default.
    For production, set `comparator:albSubnetCidrs` explicitly.
    """

    network = ipaddress.ip_network(vpc_cidr)
    if network.prefixlen > 24:
        raise ValueError(
            f"VPC CIDR {vpc_cidr} is smaller than /24; set albSubnetCidrs explicitly"
        )

    subnets = list(network.subnets(new_prefix=24))
    if len(subnets) < 2:
        raise ValueError(
            f"VPC CIDR {vpc_cidr} cannot be split into 2x /24; set albSubnetCidrs"
        )

    return [str(subnets[-2]), str(subnets[-1])]


config = pulumi.Config()

# Existing network resources (bring-your-own VPC/Subnet)
# Override via stack config if needed:
# - pulumi config set comparator:vpcId vpc-...
# - pulumi config set comparator:subnetId subnet-... (EC2 placement subnet)
#
# Defaults are taken from flake.nix env vars for convenience.
vpc_id = config.get("vpcId") or os.getenv("PREFECT_VPC_ID")
subnet_id = config.get("subnetId")
if subnet_id is None:
    # Prefer public subnets for SSH bootstrap (like comexsoft exports).
    # You can override with comparator:subnetId at any time.
    public_subnets = config.get_object("publicSubnetIds")
    if not public_subnets:
        env_public = os.getenv("PREFECT_PUBLIC_SUBNETS")
        if env_public:
            public_subnets = json.loads(env_public)
    if not public_subnets:
        # Fallback: reuse comexsoft public subnet ids if present as env vars
        # or hardcode via Pulumi config.
        public_subnets = []

    if public_subnets:
        subnet_id = public_subnets[0]
    else:
        subnets = config.get_object("subnetIds")
        if not subnets:
            subnets = json.loads(os.getenv("PREFECT_SUBNETS", "[]"))
        if subnets:
            subnet_id = subnets[0]

if not vpc_id:
    raise ValueError("Missing VPC id. Set comparator:vpcId or PREFECT_VPC_ID")
if not subnet_id:
    raise ValueError(
        "Missing subnet id. Set comparator:subnetId, comparator:subnetIds, or PREFECT_SUBNETS"
    )

# Create a dedicated SG for this instance so we can always SSH.
# This avoids depending on PREFECT_SECURITY_GROUP, which may be locked down.
# SSH ingress CIDRs (bootstrap). Mirror comexsoft's allowlist style.
ssh_ingress_cidrs = config.get_object("sshIngressCidrs") or [
    "181.173.146.1/32",
    "186.107.26.117/32",
    "186.107.31.185/32",
    "213.99.219.191/32",
    "104.28.202.235/32",
]

# Generate new private key (written locally for ease of use)
ssh_key = tls.PrivateKey("comparator-ec2-keypair-key", algorithm="ED25519")

# Create a new AWS Key Pair and upload the public portion of the key.
key_pair = aws.ec2.KeyPair(
    "comparator-ec2-keypair",
    public_key=ssh_key.public_key_openssh,
    tags={"Name": "comparator-ec2-keypair"},
)

private_key_filename = ssh_key.private_key_pem.apply(_save_key_to_file)

# Lookup the latest NixOS AMI
region = aws.config.region
ami = aws.ec2.get_ami(
    owners=["427812963091"],
    most_recent=True,
    filters=[
        aws.ec2.GetAmiFilterArgs(name="name", values=["nixos/25.05*"]),
        aws.ec2.GetAmiFilterArgs(name="architecture", values=["x86_64"]),
    ],
)

# Import the existing VPC/Subnet into the graph (no changes made).
vpc = aws.ec2.Vpc.get("comparator-ec2-vpc", vpc_id)
subnet = aws.ec2.Subnet.get("comparator-ec2-subnet", subnet_id)

instance_sg = aws.ec2.SecurityGroup(
    "comparator-ec2-sg",
    vpc_id=vpc.id,
    description="Comparator EC2 security group (ssh bootstrap)",
    ingress=[
        aws.ec2.SecurityGroupIngressArgs(
            protocol="tcp",
            from_port=22,
            to_port=22,
            cidr_blocks=ssh_ingress_cidrs,
            description="SSH access",
        )
    ],
    egress=[
        aws.ec2.SecurityGroupEgressArgs(
            protocol="-1",
            from_port=0,
            to_port=0,
            cidr_blocks=["0.0.0.0/0"],
            description="Allow all egress",
        )
    ],
    tags={"Name": "comparator-ec2-sg"},
)

# Optional ALB bootstrap (disabled by default)
create_alb = bool(config.get_bool("createAlb") or False)

alb = None
listener = None
alb_subnet_cidrs = None

target_group = None
alb_sg = None

if create_alb:
    alb_subnet_cidrs = config.get_object("albSubnetCidrs")
    if alb_subnet_cidrs is not None and len(alb_subnet_cidrs) != 2:
        raise ValueError("comparator:albSubnetCidrs must have exactly 2 CIDRs")

    alb_ingress_cidrs = config.get_object("albIngressCidrs") or ["0.0.0.0/0"]

    vpc_info = aws.ec2.get_vpc(id=vpc_id)
    if alb_subnet_cidrs is None:
        alb_subnet_cidrs = _default_alb_subnet_cidrs(vpc_info.cidr_block)

    azs = aws.get_availability_zones(state="available")
    if len(azs.names) < 2:
        raise RuntimeError("Need at least two availability zones for an ALB")

    alb_subnet_a = aws.ec2.Subnet(
        "comparator-alb-subnet-a",
        vpc_id=vpc.id,
        cidr_block=alb_subnet_cidrs[0],
        availability_zone=azs.names[0],
        map_public_ip_on_launch=True,
        tags={"Name": "comparator-alb-subnet-a"},
    )

    alb_subnet_b = aws.ec2.Subnet(
        "comparator-alb-subnet-b",
        vpc_id=vpc.id,
        cidr_block=alb_subnet_cidrs[1],
        availability_zone=azs.names[1],
        map_public_ip_on_launch=True,
        tags={"Name": "comparator-alb-subnet-b"},
    )

    try:
        existing_igw = aws.ec2.get_internet_gateway(
            filters=[
                aws.ec2.GetInternetGatewayFilterArgs(
                    name="attachment.vpc-id",
                    values=[vpc_id],
                )
            ]
        )
        igw_id: Any = existing_igw.id
    except Exception:
        igw = aws.ec2.InternetGateway("comparator-igw", vpc_id=vpc.id)
        igw_id = igw.id

    alb_route_table = aws.ec2.RouteTable(
        "comparator-alb-public-rt",
        vpc_id=vpc.id,
        routes=[
            aws.ec2.RouteTableRouteArgs(
                cidr_block="0.0.0.0/0",
                gateway_id=igw_id,
            )
        ],
        tags={"Name": "comparator-alb-public-rt"},
    )

    aws.ec2.RouteTableAssociation(
        "comparator-alb-rta-a",
        route_table_id=alb_route_table.id,
        subnet_id=alb_subnet_a.id,
    )

    aws.ec2.RouteTableAssociation(
        "comparator-alb-rta-b",
        route_table_id=alb_route_table.id,
        subnet_id=alb_subnet_b.id,
    )

    alb_sg = aws.ec2.SecurityGroup(
        "comparator-alb-sg",
        vpc_id=vpc.id,
        description="Public ALB for Comparator API",
        ingress=[
            aws.ec2.SecurityGroupIngressArgs(
                protocol="tcp",
                from_port=80,
                to_port=80,
                cidr_blocks=alb_ingress_cidrs,
                description="HTTP access to ALB",
            )
        ],
        egress=[
            aws.ec2.SecurityGroupEgressArgs(
                protocol="-1",
                from_port=0,
                to_port=0,
                cidr_blocks=["0.0.0.0/0"],
                description="Allow all egress",
            )
        ],
        tags={"Name": "comparator-alb-sg"},
    )

    alb = aws.lb.LoadBalancer(
        "comparator-alb",
        load_balancer_type="application",
        internal=False,
        security_groups=[alb_sg.id],
        subnets=[alb_subnet_a.id, alb_subnet_b.id],
        tags={"Name": "comparator-alb"},
    )

    target_group = aws.lb.TargetGroup(
        "comparator-api-tg",
        target_type="instance",
        port=8000,
        protocol="HTTP",
        vpc_id=vpc.id,
        health_check=aws.lb.TargetGroupHealthCheckArgs(
            path="/health",
            port="8000",
            protocol="HTTP",
            matcher="200-399",
            interval=30,
            timeout=5,
            healthy_threshold=2,
            unhealthy_threshold=2,
        ),
        tags={"Name": "comparator-api-tg"},
    )

    listener = aws.lb.Listener(
        "comparator-alb-http",
        load_balancer_arn=alb.arn,
        port=80,
        protocol="HTTP",
        default_actions=[
            aws.lb.ListenerDefaultActionArgs(
                type="forward",
                target_group_arn=target_group.arn,
            )
        ],
    )

# --- EC2 Instance (ssh-only bootstrap for now) ---

instance = aws.ec2.Instance(
    "comparator",
    ami=ami.id,
    instance_type=config.get("instanceType") or "t3.medium",
    key_name=key_pair.key_name,
    subnet_id=subnet.id,
    vpc_security_group_ids=[instance_sg.id],
    associate_public_ip_address=True,
    root_block_device=aws.ec2.InstanceRootBlockDeviceArgs(
        volume_size=int(config.get("rootVolumeGb") or 40),
        volume_type="gp3",
        tags={"Name": "comparator-root-volume"},
    ),
    tags={
        "Name": "comparator",
        "Project": "comparator",
        "Region": str(region),
    },
)

if create_alb and alb is not None and listener is not None:
    assert target_group is not None
    assert alb_sg is not None

    aws.lb.TargetGroupAttachment(
        "comparator-api-tg-attachment",
        target_group_arn=target_group.arn,
        target_id=instance.id,
        port=8000,
    )

    aws.ec2.SecurityGroupRule(
        "comparator-api-from-alb",
        type="ingress",
        security_group_id=instance_sg.id,
        protocol="tcp",
        from_port=8000,
        to_port=8000,
        source_security_group_id=alb_sg.id,
        description="Allow ALB to reach Comparator API",
    )

pulumi.export("region", region)
pulumi.export("vpc_id", vpc_id)
pulumi.export("subnet_id", subnet_id)
pulumi.export("security_group_id", instance_sg.id)
pulumi.export("public_ip", instance.public_ip)
pulumi.export("public_dns", instance.public_dns)
pulumi.export("key_pair_name", key_pair.key_name)
pulumi.export("private_key_path", pulumi.Output.unsecret(private_key_filename))

if alb is not None and listener is not None:
    pulumi.export("alb_dns_name", alb.dns_name)
    pulumi.export("alb_listener_arn", listener.arn)
    pulumi.export("alb_subnet_cidrs", alb_subnet_cidrs)

public_ip = instance.public_ip.apply(lambda ip: str(ip))
public_dns = instance.public_dns.apply(lambda dns: str(dns))
key_pair_name = key_pair.key_name.apply(lambda key: str(key))

pulumi_state = pulumi.Output.all(
    public_ip,
    public_dns,
    key_pair_name,
    private_key_filename,
).apply(
    lambda outputs: json.dumps(
        {
            "publicip": outputs[0],
            "publicdns": outputs[1],
            "ssh": outputs[2],
            "region": str(region),
        },
        indent=2,
    )
)


def write_to_json(state: str) -> None:
    with open("pulumi_state.json", "w", encoding="utf-8") as file:
        file.write(json.dumps(json.loads(state), indent=2))


pulumi_state.apply(write_to_json)
