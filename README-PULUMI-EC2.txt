Pulumi EC2 bootstrap (ssh-only)

1) Enter dev shell:
   direnv allow

2) Initialize pulumi:
   pulumi stack init prod
   pulumi stack select prod

3) Configure network (recommended; defaults read from env vars like PREFECT_VPC_ID):
   pulumi config set comparator:vpcId vpc-...
   pulumi config set comparator:subnetId subnet-...
   pulumi config set comparator:securityGroupId sg-...

4) Provision EC2:
   # Run via direnv so Pulumi sees LD_LIBRARY_PATH (grpc needs libstdc++)
   direnv exec . pulumi up

Optional (SSH bootstrap):
- Use a public subnet id (tagged `SubnetType=Public`) for `comparator:subnetId`.
- Restrict SSH ingress:
  pulumi config set --path 'comparator:sshIngressCidrs[0]' <your.ip.addr>/32

If you see a Pulumi error about "failed to discover package requirements",
ensure Pulumi is using the devenv venv by keeping `Pulumi.yaml` pointing at
`.devenv/state/venv`.

5) SSH:
   ./connect2ec2.sh

Artifacts:
- private_key.pem and pulumi_state.json are created locally and ignored by git.
