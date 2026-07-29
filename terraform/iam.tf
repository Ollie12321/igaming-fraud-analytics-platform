# Least-privilege roles: the Lambda can only touch the three DynamoDB tables
# and read Kinesis; Firehose can only write to its own S3 bucket and read
# from the one Kinesis stream. Neither role has blanket "*" resource access.

data "aws_caller_identity" "current" {}

# ---- Lambda fraud detector ----

resource "aws_iam_role" "lambda_fraud_detector" {
  name = "${var.project_name}-lambda-fraud-detector"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "lambda_kinesis_read" {
  name = "kinesis-read"
  role = aws_iam_role.lambda_fraud_detector.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "kinesis:GetRecords",
        "kinesis:GetShardIterator",
        "kinesis:DescribeStream",
        "kinesis:DescribeStreamSummary",
        "kinesis:ListShards",
        "kinesis:ListStreams",
        "kinesis:SubscribeToShard",
      ]
      Resource = aws_kinesis_stream.player_events.arn
    }]
  })
}

resource "aws_iam_role_policy" "lambda_dynamodb_access" {
  name = "dynamodb-access"
  role = aws_iam_role.lambda_fraud_detector.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:DeleteItem",
        "dynamodb:UpdateItem",
      ]
      Resource = [
        aws_dynamodb_table.fraud_state.arn,
        aws_dynamodb_table.fraud_flags.arn,
        aws_dynamodb_table.self_exclusion_registry.arn,
      ]
    }]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_basic_execution" {
  role       = aws_iam_role.lambda_fraud_detector.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# ---- Firehose (raw event archival to S3) ----

resource "aws_iam_role" "firehose" {
  name = "${var.project_name}-firehose"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "firehose.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "firehose_s3_write" {
  name = "s3-write"
  role = aws_iam_role.firehose.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "s3:AbortMultipartUpload",
        "s3:GetBucketLocation",
        "s3:GetObject",
        "s3:ListBucket",
        "s3:ListBucketMultipartUploads",
        "s3:PutObject",
      ]
      Resource = [
        aws_s3_bucket.raw_events.arn,
        "${aws_s3_bucket.raw_events.arn}/*",
      ]
    }]
  })
}

resource "aws_iam_role_policy" "firehose_kinesis_read" {
  name = "kinesis-read"
  role = aws_iam_role.firehose.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "kinesis:DescribeStream",
        "kinesis:GetShardIterator",
        "kinesis:GetRecords",
        "kinesis:ListShards",
      ]
      Resource = aws_kinesis_stream.player_events.arn
    }]
  })
}
