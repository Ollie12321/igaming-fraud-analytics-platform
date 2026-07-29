# The single real-time ingestion point for all player events (logins,
# payments, bonus claims, game rounds). One shard is genuinely enough for
# this project's volume; see variables.tf.

resource "aws_kinesis_stream" "player_events" {
  name             = var.kinesis_stream_name
  shard_count      = var.kinesis_shard_count
  retention_period = var.kinesis_retention_hours

  stream_mode_details {
    stream_mode = "PROVISIONED"
  }
}
