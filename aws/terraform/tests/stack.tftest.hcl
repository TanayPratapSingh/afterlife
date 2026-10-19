# `terraform test` against a mock AWS provider: no credentials, no API calls.
#
# `terraform validate` checks types and references but never evaluates the
# expressions that build the state machine or choose the schedules. These runs
# do, by applying the whole stack to a mock provider, which invents values for
# computed attributes instead of calling AWS. What they prove is that the
# configuration renders and wires up as intended in each orchestrator mode,
# not that AWS would accept it.
#
#   python aws/build.py && cd aws/terraform && terraform init -backend=false && terraform test

mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "123456789012"
      arn        = "arn:aws:iam::123456789012:user/test"
      user_id    = "AIDATEST"
    }
  }

  mock_data "aws_partition" {
    defaults = {
      partition  = "aws"
      dns_suffix = "amazonaws.com"
    }
  }

  mock_data "aws_region" {
    defaults = {
      name = "us-east-1"
    }
  }

  mock_data "aws_vpc" {
    defaults = {
      id = "vpc-0123456789abcdef0"
    }
  }

  mock_data "aws_subnets" {
    defaults = {
      ids = ["subnet-0aaaaaaaaaaaaaaaa", "subnet-0bbbbbbbbbbbbbbbb"]
    }
  }

  # The provider still validates resource arguments against a mock, so every
  # rendered policy document must at least be a JSON policy, and every ARN one
  # resource hands to another must look like an ARN.
  mock_data "aws_iam_policy_document" {
    defaults = {
      json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}"
    }
  }

  mock_resource "aws_kms_key" {
    defaults = { arn = "arn:aws:kms:us-east-1:123456789012:key/00000000-0000-0000-0000-000000000000" }
  }
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::123456789012:role/mock" }
  }
  mock_resource "aws_iam_openid_connect_provider" {
    defaults = { arn = "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com" }
  }
  mock_resource "aws_sqs_queue" {
    defaults = { arn = "arn:aws:sqs:us-east-1:123456789012:mock" }
  }
  mock_resource "aws_sns_topic" {
    defaults = { arn = "arn:aws:sns:us-east-1:123456789012:mock" }
  }
  mock_resource "aws_s3_bucket" {
    defaults = { arn = "arn:aws:s3:::mock-bucket" }
  }
  mock_resource "aws_cloudwatch_log_group" {
    defaults = { arn = "arn:aws:logs:us-east-1:123456789012:log-group:mock" }
  }
  mock_resource "aws_lambda_function" {
    defaults = {
      arn        = "arn:aws:lambda:us-east-1:123456789012:function:mock"
      invoke_arn = "arn:aws:apigateway:us-east-1:lambda:path/2015-03-31/functions/arn:aws:lambda:us-east-1:123456789012:function:mock/invocations"
    }
  }
  mock_resource "aws_dynamodb_table" {
    defaults = { arn = "arn:aws:dynamodb:us-east-1:123456789012:table/mock" }
  }
  mock_resource "aws_ecr_repository" {
    defaults = {
      arn            = "arn:aws:ecr:us-east-1:123456789012:repository/mock"
      repository_url = "123456789012.dkr.ecr.us-east-1.amazonaws.com/mock"
    }
  }
  mock_resource "aws_ecs_cluster" {
    defaults = { arn = "arn:aws:ecs:us-east-1:123456789012:cluster/mock" }
  }
  mock_resource "aws_ecs_task_definition" {
    defaults = { arn = "arn:aws:ecs:us-east-1:123456789012:task-definition/mock:1" }
  }
  mock_resource "aws_sfn_state_machine" {
    defaults = { arn = "arn:aws:states:us-east-1:123456789012:stateMachine:mock" }
  }
  mock_resource "aws_secretsmanager_secret" {
    defaults = { arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:mock" }
  }
  mock_resource "aws_cloudwatch_event_connection" {
    defaults = {
      arn        = "arn:aws:events:us-east-1:123456789012:connection/mock"
      secret_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:events!connection/mock"
    }
  }
  mock_resource "aws_apigatewayv2_api" {
    defaults = { execution_arn = "arn:aws:execute-api:us-east-1:123456789012:mockapi" }
  }
}

variables {
  alert_email = "alerts@example.com"
}

run "step_functions_mode_renders_a_closed_state_machine" {
  command = apply

  assert {
    condition     = module.ingest.schedule_names == ["afterlife-daily"]
    error_message = "step_functions mode must create exactly one schedule, for the state machine."
  }

  assert {
    condition     = jsondecode(module.ingest.state_machine_definition).StartAt == "Fetch"
    error_message = "The state machine must start with the parallel fetch."
  }

  assert {
    condition     = length(jsondecode(module.ingest.state_machine_definition).States.Fetch.Branches) == 3
    error_message = "The parallel state must have one branch per source."
  }

  assert {
    condition = alltrue([
      for name, state in jsondecode(module.ingest.state_machine_definition).States :
      # HCL's || does not short circuit, so a missing Next becomes "" via try().
      contains(concat(keys(jsondecode(module.ingest.state_machine_definition).States), [""]), try(state.Next, ""))
    ])
    error_message = "Every top level Next must name a state that exists."
  }

  assert {
    condition     = jsondecode(module.ingest.state_machine_definition).States.AllLanded.Choices[0].Next == "Done"
    error_message = "Without a Databricks host, a full landing goes straight to Done."
  }

  assert {
    condition     = !contains(keys(jsondecode(module.ingest.state_machine_definition).States), "TriggerDatabricks")
    error_message = "The Databricks state must not exist when databricks_host is empty."
  }

  assert {
    condition = alltrue([
      for branch in jsondecode(module.ingest.state_machine_definition).States.Fetch.Branches :
      alltrue([for name, state in branch.States : state.Type != "Task" || try(length(state.Catch), 0) == 1])
    ])
    error_message = "Every fetch task must catch its own failure so the other branches finish."
  }

  assert {
    condition = alltrue([
      for branch in jsondecode(module.ingest.state_machine_definition).States.Fetch.Branches :
      alltrue([
        for name, state in branch.States :
        state.Resource != "arn:aws:states:::lambda:invoke" || (
          state.Retry[0].MaxAttempts == 0 && contains(state.Retry[0].ErrorEquals, "ImmutableWriteError")
        ) if state.Type == "Task"
      ])
    ])
    error_message = "A changed snapshot (ImmutableWriteError) must never be retried."
  }
}

run "scheduler_mode_schedules_each_source" {
  command = apply

  variables {
    orchestrator = "scheduler"
  }

  assert {
    # Compared as sets: Terraform's == treats a list and a tuple literal as unequal.
    condition = toset(module.ingest.schedule_names) == toset([
      "afterlife-fetch-openalex", "afterlife-fetch-pubmed", "afterlife-fetch-retraction-watch",
    ])
    error_message = "scheduler mode must create one schedule per source and none for the state machine."
  }
}

run "databricks_step_runs_only_after_every_fetch" {
  command = apply

  variables {
    databricks_host   = "dbc-test.cloud.databricks.com"
    databricks_job_id = 42
    # checkov:skip=CKV_SECRET_6:synthetic placeholder for a mock provider test, not a credential
    databricks_token = "dapi-test-not-a-real-token" #checkov:skip=CKV_SECRET_6:synthetic test value
  }

  assert {
    condition     = jsondecode(module.ingest.state_machine_definition).States.AllLanded.Choices[0].Next == "TriggerDatabricks"
    error_message = "With a host set, a full landing must lead to the Databricks step."
  }

  assert {
    condition     = jsondecode(module.ingest.state_machine_definition).States.AllLanded.Default == "FetchFailed"
    error_message = "A partial landing must fail the execution, not start the Databricks job."
  }

  assert {
    condition     = jsondecode(module.ingest.state_machine_definition).States.TriggerDatabricks.Parameters.ApiEndpoint == "https://dbc-test.cloud.databricks.com/api/2.1/jobs/run-now"
    error_message = "The HTTP task must call the Jobs run-now endpoint on the configured host."
  }

  assert {
    condition     = jsondecode(module.ingest.state_machine_definition).States.TriggerDatabricks.Parameters.RequestBody.job_id == 42
    error_message = "The HTTP task must send the configured job id."
  }
}

run "rejects_an_unknown_orchestrator" {
  command = plan

  variables {
    orchestrator = "cron"
  }

  expect_failures = [var.orchestrator]
}

run "rejects_an_athena_cutoff_below_the_service_minimum" {
  command = plan

  variables {
    athena_bytes_scanned_cutoff = 1024
  }

  expect_failures = [var.athena_bytes_scanned_cutoff]
}
