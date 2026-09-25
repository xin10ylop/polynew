"""One-shot AWS setup for the Polymarket deep-maker bot (PAPER mode). Run it in AWS CloudShell:

    curl -sL https://raw.githubusercontent.com/xin10ylop/polynew/claude/quirky-rubin-rxd14g/scripts/aws_create_polybot.py | python3 - --email YOUR_EMAIL

What it does (idempotent; safe to re-run):
  1. Region eu-west-1 (Dublin). London is NOT used: UK IPs are close-only on Polymarket's API.
  2. Picks a free-tier-eligible instance type if your account has one (prefers c7i-flex.large / m7i-flex.large),
     otherwise c7i-flex.large (~$0.09/hour).
  3. Security group that only allows SSH from AWS's own "EC2 Instance Connect" service (browser login).
  4. Launches Ubuntu 24.04 (20 GB disk) named "polybot". At boot it installs the bot, runs the latency probe
     (-> /home/ubuntu/latency_probe.txt) and starts the bot in PAPER mode as a service (no keys, no money).
  5. Creates a monthly cost budget with email alerts.
Options: --budget 15 (USD/month)  --type t3.small (force an instance type)  --no-budget
"""
import argparse
import sys
import time

import boto3
from botocore.exceptions import ClientError

REGION = "eu-west-1"
REPO = "https://github.com/xin10ylop/polynew.git"
BRANCH = "claude/quirky-rubin-rxd14g"
PREFERRED = ["c7i-flex.large", "m7i-flex.large", "t3.small", "t3.micro", "t2.micro"]

USER_DATA = f"""#!/bin/bash
set -x
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3-venv git tmux
sudo -u ubuntu bash -lc 'cd /home/ubuntu && git clone {REPO} polynew && cd polynew && git checkout {BRANCH} \
  && python3 -m venv .venv && .venv/bin/pip install -q aiohttp websockets py-clob-client && mkdir -p logs'
sudo -u ubuntu bash -lc 'cd /home/ubuntu/polynew && .venv/bin/python -m bot.latency_probe > /home/ubuntu/latency_probe.txt 2>&1'
cat > /etc/systemd/system/polybot-paper.service <<'EOF'
[Unit]
Description=Polymarket deep-maker bot (PAPER mode - no keys, no money)
After=network-online.target
Wants=network-online.target
[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/polynew
Environment=BOT_MODE=paper
Environment=BOT_PAPER_LAT_MS=30
Environment=PYTHONUNBUFFERED=1
ExecStart=/home/ubuntu/polynew/.venv/bin/python -m bot.run
Restart=always
RestartSec=5
StandardOutput=append:/home/ubuntu/polynew/logs/paper.log
StandardError=append:/home/ubuntu/polynew/logs/paper.log
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now polybot-paper
echo done > /home/ubuntu/SETUP_DONE
"""


def pick_type(ec2, forced):
    if forced:
        return forced, None
    try:
        r = ec2.describe_instance_types(Filters=[{"Name": "free-tier-eligible", "Values": ["true"]}])
        free = {t["InstanceType"] for t in r.get("InstanceTypes", [])}
    except ClientError:
        free = set()
    for t in PREFERRED:
        if t in free:
            return t, True
    return "c7i-flex.large", False


def ensure_sg(ec2):
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"]
    if not vpcs:
        sys.exit("No default VPC in eu-west-1. Create one: VPC console -> Actions -> Create default VPC. Then re-run.")
    vpc = vpcs[0]["VpcId"]
    existing = ec2.describe_security_groups(Filters=[{"Name": "group-name", "Values": ["polybot-sg"]},
                                                     {"Name": "vpc-id", "Values": [vpc]}])["SecurityGroups"]
    if existing:
        return existing[0]["GroupId"]
    sg = ec2.create_security_group(GroupName="polybot-sg", Description="polybot: SSH only via EC2 Instance Connect",
                                   VpcId=vpc)["GroupId"]
    pls = ec2.describe_managed_prefix_lists(
        Filters=[{"Name": "prefix-list-name", "Values": [f"com.amazonaws.{REGION}.ec2-instance-connect"]}])["PrefixLists"]
    if pls:
        perm = {"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22,
                "PrefixListIds": [{"PrefixListId": pls[0]["PrefixListId"], "Description": "EC2 Instance Connect"}]}
    else:
        print("! EC2 Instance Connect prefix list not found; allowing SSH from anywhere (key-based login only).")
        perm = {"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}
    ec2.authorize_security_group_ingress(GroupId=sg, IpPermissions=[perm])
    return sg


def ensure_instance(ec2, ssm, itype, sg):
    live = ec2.describe_instances(Filters=[{"Name": "tag:Name", "Values": ["polybot"]},
                                           {"Name": "instance-state-name",
                                            "Values": ["pending", "running", "stopping", "stopped"]}])
    for res in live["Reservations"]:
        for i in res["Instances"]:
            print(f"= Instance 'polybot' already exists: {i['InstanceId']} ({i['State']['Name']}). Not creating another.")
            return i["InstanceId"]
    ami = ssm.get_parameter(Name="/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id")
    r = ec2.run_instances(
        ImageId=ami["Parameter"]["Value"], InstanceType=itype, MinCount=1, MaxCount=1,
        SecurityGroupIds=[sg], UserData=USER_DATA,
        BlockDeviceMappings=[{"DeviceName": "/dev/sda1", "Ebs": {"VolumeSize": 20, "VolumeType": "gp3",
                                                                 "DeleteOnTermination": True}}],
        MetadataOptions={"HttpTokens": "required", "HttpEndpoint": "enabled"},
        TagSpecifications=[{"ResourceType": "instance", "Tags": [{"Key": "Name", "Value": "polybot"}]}])
    return r["Instances"][0]["InstanceId"]


def ensure_budget(account, email, amount):
    b = boto3.client("budgets", region_name="us-east-1")
    subs = [{"SubscriptionType": "EMAIL", "Address": email}]
    try:
        b.create_budget(
            AccountId=account,
            Budget={"BudgetName": "polybot-monthly", "BudgetLimit": {"Amount": str(amount), "Unit": "USD"},
                    "TimeUnit": "MONTHLY", "BudgetType": "COST"},
            NotificationsWithSubscribers=[
                {"Notification": {"NotificationType": "ACTUAL", "ComparisonOperator": "GREATER_THAN",
                                  "Threshold": 50.0, "ThresholdType": "PERCENTAGE"}, "Subscribers": subs},
                {"Notification": {"NotificationType": "FORECASTED", "ComparisonOperator": "GREATER_THAN",
                                  "Threshold": 100.0, "ThresholdType": "PERCENTAGE"}, "Subscribers": subs}])
        print(f"+ Budget 'polybot-monthly' ${amount}/month created; alerts go to {email}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "DuplicateRecordException":
            print("= Budget 'polybot-monthly' already exists.")
        else:
            print(f"! Budget not created ({e.response['Error']['Code']}): create it by hand in Billing -> Budgets.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", help="email for budget alerts")
    ap.add_argument("--budget", type=float, default=15.0)
    ap.add_argument("--type", default=None)
    ap.add_argument("--no-budget", action="store_true")
    a = ap.parse_args()
    s = boto3.session.Session(region_name=REGION)
    ec2, ssm = s.client("ec2"), s.client("ssm")
    account = s.client("sts").get_caller_identity()["Account"]
    print(f"Account {account}, region {REGION}")
    itype, free = pick_type(ec2, a.type)
    print(f"Instance type: {itype} " + ("(free-tier eligible on your account)" if free else
                                        "(NOT free-tier: ~$0.09/hour while running; stop it when unused)" if free is False
                                        else "(forced)"))
    sg = ensure_sg(ec2)
    iid = ensure_instance(ec2, ssm, itype, sg)
    if not a.no_budget:
        if a.email:
            ensure_budget(account, a.email, a.budget)
        else:
            print("! No --email given: budget not created.")
    ip = None
    for _ in range(30):
        d = ec2.describe_instances(InstanceIds=[iid])["Reservations"][0]["Instances"][0]
        ip = d.get("PublicIpAddress")
        if d["State"]["Name"] == "running" and ip:
            break
        time.sleep(5)
    print(f"\nDONE. Instance {iid} ({itype}) public IP {ip}")
    print(f"Connect in your browser: https://{REGION}.console.aws.amazon.com/ec2/home?region={REGION}"
          f"#ConnectToInstance:instanceId={iid}  -> tab 'EC2 Instance Connect' -> Connect")
    print("Setup continues on the server for ~3-5 minutes. Then, in the server terminal:")
    print("  cat ~/latency_probe.txt              # the speed test (decides everything)")
    print("  tail -f ~/polynew/logs/paper.log     # the paper-trading bot (Ctrl+C to stop watching)")
    print("To stop paying for compute: EC2 console -> select polybot -> Instance state -> Stop instance.")


if __name__ == "__main__":
    main()
