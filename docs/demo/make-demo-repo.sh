#!/bin/sh
# Rebuilds the sample repo used by demo.tape, then renders the GIF:
#   sh docs/demo/make-demo-repo.sh && vhs docs/demo/demo.tape
set -e
DEMO=/tmp/agmem-demo/payments-infra
rm -rf /tmp/agmem-demo && mkdir -p "$DEMO/terraform/rds" "$DEMO/terraform/modules/aws/s3" "$DEMO/services" "$DEMO/app"

cat > "$DEMO/terraform/rds/rds_bastion.tf" <<'TF'
# Bastion in front of the payments RDS cluster.
resource "aws_instance" "rds_bastion" {
  ami           = data.aws_ami.al2023.id
  instance_type = "t3.micro"
  subnet_id     = module.vpc.private_subnets[0]
  tags = {
    Name = "rds-bastion"
    team = "platform"
  }
}
TF

cat > "$DEMO/terraform/modules/aws/s3/variables.tf" <<'TF'
variable "s3_bucket_name" {
  type = string
}
variable "mandatory_tags" {
  type = map(string)
}
variable "kms_deletion_window_in_days" {
  type    = number
  default = 30
}
TF

cat > "$DEMO/services/s3.md" <<'MD'
# S3 service notes

## Overview

Buckets are provisioned through terraform/modules/aws/s3.

## S3 module variables

s3_bucket_name, mandatory_tags, kms_deletion_window_in_days;
module path: terraform/modules/aws/s3. All buckets get SSE-KMS.

## Lifecycle rules

Logs transition to Glacier after 90 days.

## Access patterns

Only the ingest service writes; analytics reads via Athena.
MD

cat > "$DEMO/app/billing.py" <<'PY'
def compute_invoice_total(items):
    """Sum line items, integer cents only."""
    return sum(i.amount_cents for i in items)
PY

cd "$DEMO" && git init -q && git add -A && git commit -qm "demo repo"
echo "demo repo ready at $DEMO"
