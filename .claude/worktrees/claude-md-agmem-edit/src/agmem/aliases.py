"""Service alias mappings — expands search queries to find infrastructure resources.

Maps common service names to their cloud provider resource names (and vice versa).
Built-ins are generic (cloud + framework names). Project-specific aliases
(``core → citadel-backend`` etc.) live in ``.agmem/aliases.yaml`` and are merged
on top at load time.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ALIASES: dict[str, list[str]] = {
    # MongoDB / DocumentDB
    "mongodb": ["docdb", "documentdb", "document_db", "mongo", "aws_docdb"],
    "docdb": ["mongodb", "documentdb", "mongo"],
    "documentdb": ["mongodb", "docdb", "mongo"],

    # PostgreSQL / RDS / Aurora
    "postgres": ["rds", "aurora", "postgresql", "aws_db_instance", "aws_rds"],
    "postgresql": ["postgres", "rds", "aurora", "aws_db_instance"],
    "rds": ["postgres", "aurora", "postgresql", "database", "aws_db_instance"],
    "aurora": ["postgres", "rds", "postgresql", "aws_rds"],

    # MySQL
    "mysql": ["rds", "aurora", "aws_db_instance", "database"],

    # Redis / ElastiCache
    "redis": ["elasticache", "elastic_cache", "aws_elasticache"],
    "elasticache": ["redis", "elastic_cache"],

    # Kafka / MSK
    "kafka": ["msk", "aws_msk", "kinesis"],
    "msk": ["kafka", "aws_msk"],

    # S3 / Storage
    "s3": ["aws_s3", "bucket", "storage"],
    "storage": ["s3", "aws_s3", "bucket", "efs"],

    # Lambda / Serverless
    "lambda": ["aws_lambda", "serverless", "function"],
    "serverless": ["lambda", "aws_lambda"],

    # API Gateway
    "api": ["apigateway", "api_gateway", "aws_apigateway", "gateway"],
    "apigateway": ["api", "gateway", "aws_apigateway"],

    # SQS / Queue
    "queue": ["sqs", "aws_sqs", "rabbitmq"],
    "sqs": ["queue", "aws_sqs"],

    # SNS / Notifications
    "sns": ["notification", "topic", "aws_sns"],
    "notification": ["sns", "aws_sns"],

    # ECS / Containers
    "ecs": ["aws_ecs", "docker", "container", "fargate"],
    "docker": ["ecs", "ecr", "container", "fargate"],

    # EKS / Kubernetes
    "kubernetes": ["eks", "k8s", "aws_eks"],
    "eks": ["kubernetes", "k8s", "aws_eks"],
    "k8s": ["kubernetes", "eks"],

    # VPC / Network
    "vpc": ["aws_vpc", "network", "subnet", "cidr"],
    "network": ["vpc", "aws_vpc", "subnet", "sg", "security_group"],

    # IAM / Security
    "iam": ["aws_iam", "role", "policy", "permission"],
    "security": ["iam", "sg", "security_group", "waf", "kms"],

    # Monitoring / CloudWatch
    "monitoring": ["cloudwatch", "alarm", "dashboard", "metrics", "grafana"],
    "cloudwatch": ["monitoring", "alarm", "metrics", "aws_cloudwatch"],

    # CI/CD
    "cicd": ["pipeline", "deploy", "github_actions", "codebuild", "codepipeline"],
    "pipeline": ["cicd", "codebuild", "codepipeline", "deploy"],

    # DNS / Route53
    "dns": ["route53", "aws_route53", "domain"],
    "route53": ["dns", "domain", "aws_route53"],

    # Certificates / ACM
    "certificate": ["acm", "aws_acm", "tls", "ssl"],
    "acm": ["certificate", "tls", "ssl", "aws_acm"],

    # Secrets / SSM / Vault
    "secret": ["ssm", "aws_ssm", "vault", "secrets_manager"],
    "ssm": ["secret", "parameter_store", "aws_ssm"],

    # Terraform-specific
    "module": ["modules", "terraform_module"],
    "provider": ["terraform_provider", "aws_provider"],
    "variable": ["terraform_variable", "input", "tfvar"],
    "output": ["terraform_output"],
    "data": ["data_source", "terraform_data"],
    "locals": ["terraform_locals", "local_variable"],

    # HTTP / FastAPI
    "endpoint": ["route", "router", "handler", "url", "api"],
    "route": ["endpoint", "router", "handler", "url"],
    "handler": ["route", "endpoint", "router"],
    "fastapi": ["api", "route", "endpoint", "router"],
    "webhook": ["webhooks", "callback", "hook"],

    # Background tasks / async
    "task": ["taskiq", "background", "job", "worker", "queue", "celery"],
    "job": ["task", "taskiq", "background"],
    "background": ["task", "taskiq", "async", "worker"],

    # Models / data layer
    "model": ["models", "document", "schema", "entity", "table"],
    "document": ["model", "models", "beanie", "mongodb"],
    "schema": ["model", "pydantic", "basemodel", "dto"],
    "enum": ["strenum", "intenum", "enums", "values"],

    # Tests
    "test": ["tests", "pytest", "spec", "unittest"],
    "tests": ["test", "pytest", "spec"],

    # Auth / security
    "auth": ["authentication", "authorization", "login", "session"],
    "authentication": ["auth", "login", "session"],
}


def _load_one_aliases_file(path: Path) -> dict[str, list[str]]:
    """Read a single ``aliases.yaml``-shaped file, returning ``{}`` on any error."""
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, list[str]] = {}
    for key, val in data.items():
        if not isinstance(key, str):
            continue
        if isinstance(val, list):
            synonyms = [str(v).lower() for v in val if isinstance(v, str | int | float)]
        elif isinstance(val, str):
            synonyms = [val.lower()]
        else:
            continue
        if synonyms:
            out[key.lower()] = synonyms
    return out


def load_user_aliases(agmem_dir: Path) -> dict[str, list[str]]:
    """Read project aliases from ``.agmem/``.

    Two files are supported and merged (the curated file wins on conflicts):

    - ``aliases.yaml`` — hand-curated, edited by the user.
    - ``aliases.auto.yaml`` — auto-generated by ``agmem suggest-aliases``.

    Schema is a flat mapping ``term -> [synonym, ...]``. Invalid YAML or wrong
    shape returns ``{}`` silently — never blocks search.
    """
    auto = _load_one_aliases_file(agmem_dir / "aliases.auto.yaml")
    curated = _load_one_aliases_file(agmem_dir / "aliases.yaml")
    if not auto and not curated:
        return {}
    # Curated overrides auto; merge_aliases extends, so seed from auto first.
    return merge_aliases(auto, curated)


def merge_aliases(*sources: dict[str, list[str]]) -> dict[str, list[str]]:
    """Merge multiple alias dicts. Later sources extend (not replace) earlier ones.

    Useful for combining built-in ALIASES with user-supplied entries from
    ``.agmem/aliases.yaml``.
    """
    merged: dict[str, list[str]] = {}
    for src in sources:
        for key, values in src.items():
            existing = merged.setdefault(key, [])
            for v in values:
                if v not in existing and v != key:
                    existing.append(v)
    return merged


def expand_query(query: str, aliases: dict[str, list[str]] | None = None) -> str:
    """Expand query tokens with known aliases. Returns augmented query string."""
    table = aliases if aliases is not None else ALIASES
    tokens = query.lower().split()
    expanded: list[str] = []
    seen: set[str] = set()

    for token in tokens:
        if token not in seen:
            expanded.append(token)
            seen.add(token)
        if token in table:
            for alias in table[token]:
                if alias not in seen:
                    expanded.append(alias)
                    seen.add(alias)

    return " ".join(expanded)
