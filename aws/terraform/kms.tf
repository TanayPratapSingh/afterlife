# One customer managed key for the whole project.
#
# Why a CMK and not S3 managed keys: the key policy is a second, independent
# access control on every encrypted byte (bucket, table, queues, logs, topic,
# Athena results), every use is a CloudTrail event, and disabling the key is
# one action that makes all of it unreadable. One key keeps that to a single
# monthly key charge; S3 bucket keys keep the per request KMS calls low.
#
# The policy delegates to IAM (the account statement), so each role's own
# policy decides which of them may use the key. Service principals that
# encrypt on their own behalf (CloudWatch Logs, alarm and event delivery to
# the encrypted SNS topic) are granted here because they have no IAM role.

data "aws_iam_policy_document" "kms" {
  #checkov:skip=CKV_AWS_109:This is a KMS key policy, where Resource "*" means the key it is attached to; kms:* for the account root is the AWS default statement that delegates use to IAM, and removing it can make the key unmanageable
  #checkov:skip=CKV_AWS_111:Same key policy; the write actions apply only to this key, and each role's IAM policy narrows them further
  #checkov:skip=CKV_AWS_356:Same key policy; a key policy cannot name any resource other than its own key, so "*" is the only valid value
  statement {
    sid       = "AccountManagesKeyThroughIAM"
    actions   = ["kms:*"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = ["arn:${local.partition}:iam::${local.account_id}:root"]
    }
  }

  statement {
    sid       = "CloudWatchLogsEncryptsLogGroups"
    actions   = ["kms:Encrypt*", "kms:Decrypt*", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:Describe*"]
    resources = ["*"]
    principals {
      type        = "Service"
      identifiers = ["logs.${local.region}.amazonaws.com"]
    }
    condition {
      test     = "ArnLike"
      variable = "kms:EncryptionContext:aws:logs:arn"
      values   = ["arn:${local.partition}:logs:${local.region}:${local.account_id}:log-group:*"]
    }
  }

  statement {
    sid       = "AlarmsAndEventsPublishToEncryptedTopic"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey*"]
    resources = ["*"]
    principals {
      type        = "Service"
      identifiers = ["cloudwatch.amazonaws.com", "events.amazonaws.com"]
    }
  }
}

resource "aws_kms_key" "lake" {
  description             = "${var.name_prefix}: landing zone, registry table, queues, logs, alerts"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy                  = data.aws_iam_policy_document.kms.json
}

resource "aws_kms_alias" "lake" {
  name          = "alias/${var.name_prefix}"
  target_key_id = aws_kms_key.lake.key_id
}
