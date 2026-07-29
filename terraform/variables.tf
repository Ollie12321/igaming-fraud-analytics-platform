variable "aws_region" {
  description = "AWS region for all resources"
  type        = string
  default     = "eu-west-2"
}

variable "aws_profile" {
  description = "Named AWS CLI profile to use (leave blank to use the default credential chain)"
  type        = string
  default     = ""
}

variable "environment" {
  description = "Deployment environment tag"
  type        = string
  default     = "demo"
}

variable "project_name" {
  description = "Short name used as a prefix for resource names"
  type        = string
  default     = "igaming-fraud-analytics"
}

variable "s3_raw_bucket_name" {
  description = "S3 bucket for raw event archival (Firehose destination + backfill landing zone)"
  type        = string
  default     = "igaming-fraud-analytics-raw"
}

variable "kinesis_stream_name" {
  type    = string
  default = "igaming-player-events"
}

variable "kinesis_shard_count" {
  description = "Kept at 1 deliberately: this project's volume doesn't need more, and each extra shard is a fixed hourly cost"
  type        = number
  default     = 1
}

variable "kinesis_retention_hours" {
  type    = number
  default = 24
}

variable "lambda_state_table_name" {
  type    = string
  default = "igaming-fraud-state"
}

variable "lambda_flags_table_name" {
  type    = string
  default = "igaming-fraud-flags"
}

variable "self_exclusion_table_name" {
  type    = string
  default = "igaming-self-exclusion-registry"
}

variable "lambda_memory_mb" {
  type    = number
  default = 256
}

variable "lambda_timeout_seconds" {
  type    = number
  default = 30
}
