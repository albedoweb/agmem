"""Tests for content analyzer."""

from agmem.content_analyzer import (
    analyze_file,
    analyze_tf,
    extract_tags_from_blocks,
    TfBlock,
)


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
