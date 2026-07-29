# Raw event archive: Firehose delivers every Kinesis record here for
# backfill/replay and audit, independent of the hot (DynamoDB) path.

resource "aws_s3_bucket" "raw_events" {
  bucket = var.s3_raw_bucket_name
}

resource "aws_s3_bucket_versioning" "raw_events" {
  bucket = aws_s3_bucket.raw_events.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "raw_events" {
  bucket                  = aws_s3_bucket.raw_events.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "raw_events" {
  bucket = aws_s3_bucket.raw_events.id

  rule {
    id     = "expire-raw-after-90-days"
    status = "Enabled"

    filter {
      prefix = "events/"
    }

    expiration {
      days = 90
    }

    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }
}

resource "aws_kinesis_firehose_delivery_stream" "raw_events" {
  name        = "${var.project_name}-raw-archive"
  destination = "extended_s3"

  extended_s3_configuration {
    role_arn   = aws_iam_role.firehose.arn
    bucket_arn = aws_s3_bucket.raw_events.arn
    prefix     = "events/year=!{timestamp:yyyy}/month=!{timestamp:MM}/day=!{timestamp:dd}/"

    buffering_size     = 5
    buffering_interval = 300
    compression_format = "GZIP"
  }

  kinesis_source_configuration {
    kinesis_stream_arn = aws_kinesis_stream.player_events.arn
    role_arn           = aws_iam_role.firehose.arn
  }
}
