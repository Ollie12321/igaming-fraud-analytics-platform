output "kinesis_stream_name" {
  value = aws_kinesis_stream.player_events.name
}

output "s3_raw_bucket" {
  value = aws_s3_bucket.raw_events.bucket
}

output "dynamodb_fraud_state_table" {
  value = aws_dynamodb_table.fraud_state.name
}

output "dynamodb_fraud_flags_table" {
  value = aws_dynamodb_table.fraud_flags.name
}

output "dynamodb_self_exclusion_table" {
  value = aws_dynamodb_table.self_exclusion_registry.name
}

output "lambda_function_name" {
  value = aws_lambda_function.fraud_detector.function_name
}
