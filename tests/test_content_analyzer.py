"""Tests for content analyzer."""

from agmem.content_analyzer import (
    analyze_file,
    analyze_tf,
    extract_tags_from_blocks,
    TfBlock,
)
from agmem.parsers.tf import extract_header


TF_FILE = '''
resource "aws_docdb_cluster" "primary" {
  cluster_identifier = "prod"
  engine             = "docdb"
}

resource "aws_docdb_subnet_group" "main" {
  name = "docdb-subnet"
}

module "vpc" {
  source = "./modules/vpc"
}

variable "instance_class" {
  type = string
}

output "cluster_endpoint" {
  value = aws_docdb_cluster.primary.endpoint
}

data "aws_vpc" "main" {
  id = "vpc-123"
}

locals {
  name = "myapp"
}
'''


def test_analyze_tf_resources():
    blocks = analyze_tf(TF_FILE)
    assert len(blocks) == 7

    resource_blocks = [b for b in blocks if b.block_type == "resource"]
    assert len(resource_blocks) == 2
    assert resource_blocks[0].resource_type == "aws_docdb_cluster"
    assert resource_blocks[0].name == "primary"
    assert resource_blocks[1].resource_type == "aws_docdb_subnet_group"

    module_blocks = [b for b in blocks if b.block_type == "module"]
    assert len(module_blocks) == 1
    assert module_blocks[0].name == "vpc"

    var_blocks = [b for b in blocks if b.block_type == "variable"]
    assert len(var_blocks) == 1
    assert var_blocks[0].name == "instance_class"

    output_blocks = [b for b in blocks if b.block_type == "output"]
    assert len(output_blocks) == 1

    data_blocks = [b for b in blocks if b.block_type == "data"]
    assert len(data_blocks) == 1
    assert data_blocks[0].resource_type == "aws_vpc"

    locals_blocks = [b for b in blocks if b.block_type == "locals"]
    assert len(locals_blocks) == 1


def test_analyze_file_tf():
    analysis = analyze_file("modules/docdb/main.tf", TF_FILE)
    assert analysis is not None
    assert analysis.path == "modules/docdb/main.tf"
    assert analysis.ext == "tf"
    assert len(analysis.blocks) == 7
    assert "Resources: aws_docdb_cluster, aws_docdb_subnet_group" in analysis.summary


def test_analyze_file_unknown_ext():
    result = analyze_file("foo.txt", "content")
    assert result is None


def test_extract_tags_from_blocks():
    blocks = analyze_tf(TF_FILE)
    tags = extract_tags_from_blocks(blocks)
    assert "resource" in tags
    assert "mongodb" in tags
    assert "aws_docdb_cluster" in tags


def test_tf_block_full_name():
    b = TfBlock(block_type="resource", name="primary", labels=["aws_docdb_cluster"])
    assert b.full_name == "resource primary (aws_docdb_cluster)"
    assert b.resource_type == "aws_docdb_cluster"


def test_empty_tf():
    blocks = analyze_tf("# just a comment")
    assert blocks == []


def test_extract_header_basic():
    content = '''# WAF in monitoring (count) mode for the public istio-gateway-external ALB.
# All rules start with override_action=count so we can observe what would fire.

resource "aws_wafv2_web_acl" "external" {
  name = "mytruv-prod"
}
'''
    h = extract_header(content)
    assert "WAF in monitoring (count) mode" in h
    assert "istio-gateway-external" in h
    assert "override_action=count" in h
    assert "aws_wafv2_web_acl" not in h


def test_extract_header_stops_at_code():
    content = '''# leading line
resource "foo" "bar" {
# this comment is INSIDE the body and must NOT appear
}
'''
    assert extract_header(content) == "leading line"


def test_extract_header_handles_blank_lines_inside_block():
    content = '''# paragraph one
#
# paragraph two
resource "x" "y" {}
'''
    h = extract_header(content)
    assert "paragraph one" in h
    assert "paragraph two" in h


def test_extract_header_no_comment():
    assert extract_header('resource "x" "y" {}\n') == ""


def test_extract_header_truncates_long():
    long_line = "# " + "word " * 500
    h = extract_header(long_line + "\nresource \"x\" \"y\" {}\n")
    assert 0 < len(h) <= 200


def test_analyze_file_tf_includes_header():
    content = '''# Purpose-prefix line about the file.
# Second comment line.
resource "aws_docdb_cluster" "primary" {}
'''
    analysis = analyze_file("modules/docdb/main.tf", content)
    assert analysis is not None
    assert "Purpose-prefix" in analysis.header_comment
    assert "Second comment line" in analysis.header_comment
