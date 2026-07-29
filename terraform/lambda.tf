# Packages the handler + the pure `fraud_rules` module it shares with
# streaming/local_backtest.py. For a larger codebase this would be a proper
# CI-built artifact (or container image); for this project's small, dependency-free
# (boto3 ships with the Lambda runtime) rule module, an explicit archive_file
# keeps the deployment self-contained in Terraform with no separate build step.

data "archive_file" "lambda_fraud_detector" {
  type        = "zip"
  output_path = "${path.module}/build/lambda_fraud_detector.zip"

  source {
    content  = file("${path.module}/../streaming/__init__.py")
    filename = "streaming/__init__.py"
  }
  source {
    content  = file("${path.module}/../streaming/fraud_rules/__init__.py")
    filename = "streaming/fraud_rules/__init__.py"
  }
  source {
    content  = file("${path.module}/../streaming/fraud_rules/rules.py")
    filename = "streaming/fraud_rules/rules.py"
  }
  source {
    content  = file("${path.module}/../streaming/fraud_rules/state_store.py")
    filename = "streaming/fraud_rules/state_store.py"
  }
  source {
    content  = file("${path.module}/../streaming/lambda_fraud_detector/__init__.py")
    filename = "streaming/lambda_fraud_detector/__init__.py"
  }
  source {
    content  = file("${path.module}/../streaming/lambda_fraud_detector/handler.py")
    filename = "streaming/lambda_fraud_detector/handler.py"
  }
}

resource "aws_lambda_function" "fraud_detector" {
  function_name = "${var.project_name}-fraud-detector"
  role          = aws_iam_role.lambda_fraud_detector.arn
  handler       = "streaming.lambda_fraud_detector.handler.handler"
  runtime       = "python3.11"
  memory_size   = var.lambda_memory_mb
  timeout       = var.lambda_timeout_seconds

  filename         = data.archive_file.lambda_fraud_detector.output_path
  source_code_hash = data.archive_file.lambda_fraud_detector.output_base64sha256

  environment {
    variables = {
      AWS_REGION                    = var.aws_region
      DYNAMODB_FRAUD_STATE_TABLE    = aws_dynamodb_table.fraud_state.name
      DYNAMODB_FRAUD_FLAGS_TABLE    = aws_dynamodb_table.fraud_flags.name
      DYNAMODB_SELF_EXCLUSION_TABLE = aws_dynamodb_table.self_exclusion_registry.name
    }
  }
}

resource "aws_lambda_event_source_mapping" "kinesis_to_lambda" {
  event_source_arn                   = aws_kinesis_stream.player_events.arn
  function_name                      = aws_lambda_function.fraud_detector.arn
  starting_position                  = "LATEST"
  batch_size                         = 100
  maximum_batching_window_in_seconds = 1
  parallelization_factor             = 1
}
