#!/usr/bin/env python3
"""OpenClaw on AgentCore Runtime — CDK Application entry point.

Architecture: Per-user AgentCore Runtime sessions with webhook-based
channel ingestion via Router Lambda. No keepalive needed — sessions
idle-terminate naturally.
"""

import os

import aws_cdk as cdk
import cdk_nag

from stacks import cross_region_model_id
from stacks.vpc_stack import VpcStack
from stacks.security_stack import SecurityStack
from stacks.agentcore_stack import AgentCoreStack
from stacks.router_stack import RouterStack
from stacks.cron_stack import CronStack
from stacks.observability_stack import ObservabilityStack
from stacks.token_monitoring_stack import TokenMonitoringStack

app = cdk.App()

_account = app.node.try_get_context("account")
_region = app.node.try_get_context("region")
_base_model_id = app.node.try_get_context("default_model_id") or "anthropic.claude-sonnet-4-6"

# Validate that placeholders have been replaced
if not _account or _account == "YOUR_AWS_ACCOUNT_ID":
    raise SystemExit("ERROR: Set 'account' in cdk.json to your AWS account ID")
if not _region or _region == "YOUR_REGION":
    raise SystemExit("ERROR: Set 'region' in cdk.json to your target AWS region")

# Resolve cross-region model ID (e.g. "anthropic.claude-sonnet-4-6" -> "ap.anthropic.claude-sonnet-4-6")
_default_model_id = cross_region_model_id(_region, _base_model_id)

env = cdk.Environment(
    account=app.node.try_get_context("account") or os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=app.node.try_get_context("region") or os.environ.get("CDK_DEFAULT_REGION"),
)

# --- Look up CloudFront origin-facing managed prefix list for the target region ---
_cf_prefix_list_id = ""
try:
    _ec2 = boto3.client("ec2", region_name=_region)
    _resp = _ec2.describe_managed_prefix_lists(
        Filters=[{"Name": "prefix-list-name", "Values": ["com.amazonaws.global.cloudfront.origin-facing"]}]
    )
    _cf_prefix_list_id = _resp["PrefixLists"][0]["PrefixListId"]
except Exception:
    pass  # Lookup failed (no creds or region) — will cause synth error if empty

# --- Foundation ---
vpc_stack = VpcStack(app, "OpenClawVpc", env=env)

security_stack = SecurityStack(app, "OpenClawSecurity", env=env)

# --- AgentCore (hosts OpenClaw container, per-user sessions) ---
agentcore_stack = AgentCoreStack(
    app,
    "OpenClawAgentCore",
    cmk_arn=security_stack.cmk.key_arn,
    vpc=vpc_stack.vpc,
    private_subnet_ids=[s.subnet_id for s in vpc_stack.vpc.private_subnets],
    cognito_issuer_url=security_stack.cognito_issuer_url,
    cognito_client_id=security_stack.user_pool_client_id,
    cognito_user_pool_id=security_stack.user_pool_id,
    cognito_password_secret_name=security_stack.cognito_password_secret.secret_name,
    gateway_token_secret_name=security_stack.gateway_token_secret.secret_name,
    env=env,
)

# --- Router (Lambda + API Gateway HTTP API for Telegram/Slack webhooks) ---
router_stack = RouterStack(
    app,
    "OpenClawRouter",
    runtime_arn=agentcore_stack.runtime_arn,
    runtime_endpoint_id=agentcore_stack.runtime_endpoint_id,
    gateway_token_secret_name=security_stack.gateway_token_secret.secret_name,
    telegram_token_secret_name=security_stack.channel_secrets["telegram"].secret_name,
    slack_token_secret_name=security_stack.channel_secrets["slack"].secret_name,
    webhook_secret_name=security_stack.webhook_secret.secret_name,
    cmk_arn=security_stack.cmk.key_arn,
    user_files_bucket_name=agentcore_stack.user_files_bucket.bucket_name,
    user_files_bucket_arn=agentcore_stack.user_files_bucket.bucket_arn,
    env=env,
)

# --- Cron (EventBridge Scheduler + Lambda executor) ---
# Use deterministic string ARNs for identity table to avoid cyclic dependency
# (AgentCore <- Router already exists; CronStack adds policies to AgentCore role)
_region = env.region or os.environ.get("CDK_DEFAULT_REGION", "us-west-2")
_account = env.account or os.environ.get("CDK_DEFAULT_ACCOUNT", "")
_identity_table_name = "openclaw-identity"
_identity_table_arn = f"arn:aws:dynamodb:{_region}:{_account}:table/{_identity_table_name}"

cron_stack = CronStack(
    app,
    "OpenClawCron",
    runtime_arn=agentcore_stack.runtime_arn,
    runtime_endpoint_id=agentcore_stack.runtime_endpoint_id,
    identity_table_name=_identity_table_name,
    identity_table_arn=_identity_table_arn,
    telegram_token_secret_name=security_stack.channel_secrets["telegram"].secret_name,
    slack_token_secret_name=security_stack.channel_secrets["slack"].secret_name,
    cmk_arn=security_stack.cmk.key_arn,
    agentcore_execution_role=agentcore_stack.execution_role,
    env=env,
)

# --- Observability (dashboards + alarms) ---
observability_stack = ObservabilityStack(
    app,
    "OpenClawObservability",
    env=env,
)

# --- Token Monitoring ---
token_monitoring_stack = TokenMonitoringStack(
    app,
    "OpenClawTokenMonitoring",
    invocation_log_group=observability_stack.invocation_log_group,
    alarm_topic=observability_stack.alarm_topic,
    env=env,
)

# --- cdk-nag security checks ---
cdk.Aspects.of(app).add(cdk_nag.AwsSolutionsChecks(verbose=True))

app.synth()
