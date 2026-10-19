#!/usr/bin/env bash
# Run Terraform with credentials from `aws login`.
#
# `aws login` (AWS CLI 2.32 and later) stores credentials in a format the
# Terraform AWS provider (5.x) cannot read yet: with AWS_PROFILE alone, plan
# fails with "No valid credential sources found" (seen 2026-10-19). The CLI can
# export the same short lived session as environment variables, so this
# script does that for one Terraform process and nothing else. The values are
# never printed and never written to disk.
#
#   AWS_PROFILE=afterlife aws/tf.sh plan -out tfplan
set -euo pipefail

profile="${AWS_PROFILE:-afterlife}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
terraform="${TERRAFORM:-$here/../.tools/terraform}"

eval "$(aws configure export-credentials --profile "$profile" --format env)"
unset AWS_PROFILE
cd "$here/terraform"
exec "$terraform" "$@"
