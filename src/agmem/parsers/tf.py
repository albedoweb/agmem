"""Terraform (.tf) parser."""

from __future__ import annotations

import re

from .types import Block

_TF_RESOURCE = re.compile(r'^\s*resource\s+"([^"]+)"\s+"([^"]+)"')
_TF_DATA = re.compile(r'^\s*data\s+"([^"]+)"\s+"([^"]+)"')
_TF_MODULE = re.compile(r'^\s*module\s+"([^"]+)"')
_TF_VARIABLE = re.compile(r'^\s*variable\s+"([^"]+)"')
_TF_OUTPUT = re.compile(r'^\s*output\s+"([^"]+)"')
_TF_PROVIDER = re.compile(r'^\s*provider\s+"([^"]+)"')
_TF_LOCALS = re.compile(r'^\s*locals\s*\{')


SERVICE_HINTS: dict[str, str] = {
    "aws_docdb_cluster": "mongodb",
    "aws_docdb_subnet_group": "mongodb",
    "aws_elasticache_cluster": "redis",
    "aws_msk_cluster": "kafka",
    "aws_db_instance": "database",
    "aws_rds_cluster": "database",
    "aws_s3_bucket": "storage",
    "aws_lambda_function": "lambda",
    "aws_apigateway_rest_api": "api",
    "aws_sqs_queue": "queue",
    "aws_sns_topic": "notification",
    "aws_ecs_service": "ecs",
    "aws_eks_cluster": "kubernetes",
    "aws_vpc": "network",
    "aws_iam_role": "iam",
    "aws_cloudwatch_alarm": "monitoring",
}


def analyze(content: str) -> list[Block]:
    blocks: list[Block] = []
    for line in content.split("\n"):
        for pattern, block_type in [
            (_TF_RESOURCE, "resource"),
            (_TF_DATA, "data"),
            (_TF_MODULE, "module"),
            (_TF_VARIABLE, "variable"),
            (_TF_OUTPUT, "output"),
            (_TF_PROVIDER, "provider"),
        ]:
            m = pattern.match(line)
            if m:
                blocks.append(Block(
                    block_type=block_type,
                    name=m.group(2) if block_type in ("resource", "data") else m.group(1),
                    labels=[m.group(1)] if block_type in ("resource", "data") else [],
                ))
                break
        if _TF_LOCALS.match(line):
            blocks.append(Block(block_type="locals", name="(block)"))
    return blocks


def summary(blocks: list[Block]) -> str:
    resource_types = sorted({b.resource_type for b in blocks if b.resource_type})
    parts: list[str] = []
    if resource_types:
        parts.append(f"Resources: {', '.join(resource_types)}")
    parts.append(f"{len(blocks)} Terraform blocks total")
    return "; ".join(parts)


def extract_tags(path: str, blocks: list[Block]) -> list[str]:  # noqa: ARG001
    tags: set[str] = set()
    for b in blocks:
        tags.add(b.block_type)
        if b.resource_type:
            res = b.resource_type.lower().replace("aws_", "").replace("_", "-")
            tags.add(res)
            tags.add(b.resource_type.lower())
            hint = SERVICE_HINTS.get(b.resource_type)
            if hint:
                tags.add(hint)
    return list(tags)
