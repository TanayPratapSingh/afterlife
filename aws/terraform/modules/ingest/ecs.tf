# The OpenAlex fetch on ECS Fargate.
#
# The keyless fetch of every retracted work took 709 metered calls and
# 9 minutes 42 seconds on a laptop. Lambda stops at 15 minutes, and the partitioned
# fetch grows with OpenAlex's count of retracted works, so the margin shrinks
# on its own. Fargate has no run time ceiling and bills per second.

locals {
  task_family = "${var.name_prefix}-openalex"
  vpc_id      = var.vpc_id != "" ? var.vpc_id : data.aws_vpc.default[0].id
  subnet_ids  = length(var.subnet_ids) > 0 ? var.subnet_ids : data.aws_subnets.default[0].ids
}

data "aws_vpc" "default" {
  count   = var.vpc_id == "" ? 1 : 0
  default = true
}

data "aws_subnets" "default" {
  count = length(var.subnet_ids) == 0 ? 1 : 0
  filter {
    name   = "vpc-id"
    values = [local.vpc_id]
  }
  filter {
    name   = "default-for-az"
    values = ["true"]
  }
}

resource "aws_ecr_repository" "openalex" {
  name                 = "${var.name_prefix}/openalex"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = var.kms_key_arn
  }
}

resource "aws_ecr_lifecycle_policy" "openalex" {
  repository = aws_ecr_repository.openalex.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the ten most recent images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 10
      }
      action = { type = "expire" }
    }]
  })
}

resource "aws_ecs_cluster" "this" {
  #checkov:skip=CKV_AWS_65:One short task a day; its logs, exit code, and the state machine failure alarm cover it, and Container Insights bills its metrics as custom metrics
  name = var.name_prefix

  setting {
    name  = "containerInsights"
    value = "disabled"
  }
}

resource "aws_cloudwatch_log_group" "openalex" {
  name              = "/ecs/${local.task_family}"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn
}

data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.account_id]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:${var.partition}:ecs:${var.region}:${var.account_id}:*"]
    }
  }
}

# Execution role: what ECS itself needs to start the container (pull the
# image, open the log stream, resolve the secret).
resource "aws_iam_role" "task_execution" {
  name               = "${local.task_family}-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

data "aws_iam_policy_document" "task_execution" {
  statement {
    sid       = "EcrAuth"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"] # GetAuthorizationToken has no resource level permissions
  }

  statement {
    sid       = "PullImage"
    actions   = ["ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage"]
    resources = [aws_ecr_repository.openalex.arn]
  }

  statement {
    sid       = "WriteTaskLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.openalex.arn}:*"]
  }

  statement {
    sid       = "DecryptImageAndSecret"
    actions   = ["kms:Decrypt"]
    resources = [var.kms_key_arn]
  }

  dynamic "statement" {
    for_each = var.openalex_api_key_enabled ? [1] : []
    content {
      sid       = "InjectOpenAlexKey"
      actions   = ["secretsmanager:GetSecretValue"]
      resources = [local.openalex_secret_arn]
    }
  }
}

resource "aws_iam_role_policy" "task_execution" {
  name   = "execution"
  role   = aws_iam_role.task_execution.id
  policy = data.aws_iam_policy_document.task_execution.json
}

# Task role: what the fetcher code may do. The same landing permissions as the
# Lambda, so both runtimes are held to one contract.
resource "aws_iam_role" "task" {
  name               = "${local.task_family}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy" "task" {
  name   = "landing-writer"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.landing_writer.json
}

resource "aws_security_group" "openalex" {
  #checkov:skip=CKV2_AWS_5:Attached at run time through the Step Functions and Scheduler network configuration, which checkov does not trace
  name        = "${local.task_family}-task"
  description = "OpenAlex fetch task: no inbound, HTTPS outbound only"
  vpc_id      = local.vpc_id

  egress {
    description = "HTTPS to OpenAlex, S3, ECR, CloudWatch Logs, and Secrets Manager"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_ecs_task_definition" "openalex" {
  family                   = local.task_family
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.fargate_cpu
  memory                   = var.fargate_memory
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  # Writable scratch for run reports; the root filesystem is read only.
  volume {
    name = "tmp"
  }

  container_definitions = jsonencode([{
    name                   = "openalex"
    image                  = "${aws_ecr_repository.openalex.repository_url}:${var.openalex_image_tag}"
    essential              = true
    command                = ["--budget", tostring(var.openalex_budget_usd)]
    user                   = "10001"
    readonlyRootFilesystem = true
    privileged             = false
    mountPoints            = [{ sourceVolume = "tmp", containerPath = "/tmp", readOnly = false }]
    environment = [
      { name = "AFTERLIFE_LANDING", value = "s3://${var.bucket_id}" },
      { name = "AFTERLIFE_RUNS", value = "s3://${var.bucket_id}/runs" },
      { name = "AWS_DEFAULT_REGION", value = var.region },
    ]
    secrets = var.openalex_api_key_enabled ? [
      { name = "OPENALEX_API_KEY", valueFrom = local.openalex_secret_arn },
    ] : []
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.openalex.name
        awslogs-region        = var.region
        awslogs-stream-prefix = "openalex"
      }
    }
  }])

  depends_on = [aws_iam_role_policy.task_execution, aws_iam_role_policy.task]
}
