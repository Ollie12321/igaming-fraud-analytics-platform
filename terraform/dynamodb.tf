# Three tables, all on-demand billing (pay per request, ~$0 at idle: no
# fixed monthly floor like Redshift/MWAA):
#   1. fraud-state: rolling windows the detection rules maintain (card_bin
#                    history, known devices, Welford stats for bot detection)
#   2. fraud-flags: output of the detector, replicated to the warehouse for BI
#   3. self-exclusion-registry: reference data synced from the compliance system,
#                    used for a point lookup on every login

resource "aws_dynamodb_table" "fraud_state" {
  name         = var.lambda_state_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"

  attribute {
    name = "pk"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }
}

resource "aws_dynamodb_table" "fraud_flags" {
  name         = var.lambda_flags_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "flag_id"

  attribute {
    name = "flag_id"
    type = "S"
  }
}

resource "aws_dynamodb_table" "self_exclusion_registry" {
  name         = var.self_exclusion_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "player_id"

  attribute {
    name = "player_id"
    type = "S"
  }
}
