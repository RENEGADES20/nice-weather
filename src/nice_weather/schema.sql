PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta (
  version INTEGER NOT NULL
);
INSERT INTO schema_meta(version)
SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM schema_meta);

CREATE TABLE IF NOT EXISTS raw_snapshots (
  snapshot_id TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  kind TEXT NOT NULL,
  source_time TEXT,
  observed_at TEXT,
  issued_at TEXT,
  valid_from TEXT,
  valid_to TEXT,
  received_at TEXT NOT NULL,
  source_version TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  event_id TEXT,
  market_id TEXT,
  token_id TEXT,
  request_url TEXT,
  http_status INTEGER,
  duplicate_of_snapshot_id TEXT REFERENCES raw_snapshots(snapshot_id),
  payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_raw_snapshots_asof
  ON raw_snapshots(source, kind, received_at);

CREATE TABLE IF NOT EXISTS contract_versions (
  contract_version_id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL,
  event_slug TEXT NOT NULL,
  event_title TEXT NOT NULL,
  market_url TEXT NOT NULL,
  local_day TEXT NOT NULL,
  city_code TEXT NOT NULL,
  station_id TEXT NOT NULL,
  timezone TEXT NOT NULL,
  metric TEXT NOT NULL,
  unit TEXT NOT NULL,
  rounding TEXT NOT NULL,
  observation_start TEXT NOT NULL,
  observation_end TEXT NOT NULL,
  settlement_source TEXT NOT NULL,
  rule_text TEXT NOT NULL,
  rule_version TEXT NOT NULL,
  rule_hash TEXT NOT NULL,
  parse_status TEXT NOT NULL,
  ambiguities_json TEXT NOT NULL,
  event_active INTEGER NOT NULL,
  event_closed INTEGER NOT NULL,
  source_snapshot_id TEXT REFERENCES raw_snapshots(snapshot_id),
  received_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS contract_bins (
  bin_id TEXT PRIMARY KEY,
  contract_version_id TEXT NOT NULL REFERENCES contract_versions(contract_version_id),
  label TEXT NOT NULL,
  ordinal INTEGER NOT NULL,
  market_id TEXT NOT NULL,
  condition_id TEXT NOT NULL,
  yes_token_id TEXT NOT NULL,
  no_token_id TEXT NOT NULL,
  lower_bound REAL,
  upper_bound REAL,
  lower_inclusive INTEGER NOT NULL,
  upper_inclusive INTEGER NOT NULL,
  active INTEGER NOT NULL,
  closed INTEGER NOT NULL,
  accepting_orders INTEGER NOT NULL,
  tick_size REAL NOT NULL,
  minimum_order_size REAL NOT NULL,
  fee_rate REAL NOT NULL,
  fee_exponent REAL NOT NULL,
  UNIQUE(contract_version_id, ordinal)
);

CREATE TABLE IF NOT EXISTS order_book_levels (
  snapshot_id TEXT NOT NULL REFERENCES raw_snapshots(snapshot_id),
  token_id TEXT NOT NULL,
  market_id TEXT NOT NULL,
  book_hash TEXT NOT NULL,
  exchange_time TEXT NOT NULL,
  received_at TEXT NOT NULL,
  side TEXT NOT NULL CHECK(side IN ('bid', 'ask')),
  level_index INTEGER NOT NULL,
  price REAL NOT NULL,
  size REAL NOT NULL,
  PRIMARY KEY(snapshot_id, side, level_index)
);

CREATE TABLE IF NOT EXISTS weather_observations (
  observation_id TEXT PRIMARY KEY,
  snapshot_id TEXT NOT NULL REFERENCES raw_snapshots(snapshot_id),
  station_id TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  received_at TEXT NOT NULL,
  temperature_f REAL NOT NULL,
  raw_text TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS forecast_points (
  forecast_point_id TEXT PRIMARY KEY,
  snapshot_id TEXT NOT NULL REFERENCES raw_snapshots(snapshot_id),
  source TEXT NOT NULL,
  issued_at TEXT NOT NULL,
  valid_at TEXT NOT NULL,
  received_at TEXT NOT NULL,
  temperature_f REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS decisions (
  decision_id TEXT PRIMARY KEY,
  decision_time TEXT NOT NULL,
  mode TEXT NOT NULL,
  contract_version_id TEXT NOT NULL REFERENCES contract_versions(contract_version_id),
  input_set_hash TEXT NOT NULL,
  model_version TEXT NOT NULL,
  status TEXT NOT NULL,
  overall_action TEXT NOT NULL,
  health_level TEXT NOT NULL,
  reason_codes_json TEXT NOT NULL,
  probability_summary_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_decisions_latest
  ON decisions(status, decision_time DESC);

CREATE TABLE IF NOT EXISTS decision_inputs (
  decision_id TEXT NOT NULL REFERENCES decisions(decision_id),
  snapshot_id TEXT NOT NULL REFERENCES raw_snapshots(snapshot_id),
  role TEXT NOT NULL,
  PRIMARY KEY(decision_id, snapshot_id, role)
);

CREATE TABLE IF NOT EXISTS decision_outcomes (
  decision_id TEXT NOT NULL REFERENCES decisions(decision_id),
  bin_id TEXT NOT NULL REFERENCES contract_bins(bin_id),
  label TEXT NOT NULL,
  model_probability REAL NOT NULL,
  best_bid REAL,
  best_ask REAL,
  mid REAL,
  executable_quantity REAL NOT NULL,
  executable_price REAL,
  executable_depth REAL NOT NULL,
  gross_edge REAL,
  fee REAL NOT NULL,
  slippage REAL NOT NULL,
  uncertainty_buffer REAL NOT NULL,
  net_edge REAL,
  action TEXT NOT NULL,
  risk_approved INTEGER NOT NULL,
  reason_codes_json TEXT NOT NULL,
  paper_position REAL NOT NULL,
  PRIMARY KEY(decision_id, bin_id)
);

CREATE TABLE IF NOT EXISTS data_health (
  decision_id TEXT NOT NULL REFERENCES decisions(decision_id),
  source TEXT NOT NULL,
  level TEXT NOT NULL,
  received_at TEXT,
  source_time TEXT,
  age_seconds REAL,
  reason_codes_json TEXT NOT NULL,
  duplicate_count INTEGER NOT NULL,
  out_of_order_count INTEGER NOT NULL,
  gap_count INTEGER NOT NULL,
  message TEXT NOT NULL,
  PRIMARY KEY(decision_id, source)
);

CREATE TABLE IF NOT EXISTS paper_orders (
  order_id TEXT PRIMARY KEY,
  decision_id TEXT NOT NULL REFERENCES decisions(decision_id),
  bin_id TEXT NOT NULL REFERENCES contract_bins(bin_id),
  side TEXT NOT NULL CHECK(side IN ('buy', 'sell')),
  limit_price REAL NOT NULL,
  quantity REAL NOT NULL,
  filled_quantity REAL NOT NULL,
  average_fill_price REAL NOT NULL,
  reserved_cash REAL NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  stale_after_cycle INTEGER NOT NULL,
  reason_codes_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_fills (
  fill_id TEXT PRIMARY KEY,
  order_id TEXT NOT NULL REFERENCES paper_orders(order_id),
  decision_id TEXT NOT NULL REFERENCES decisions(decision_id),
  bin_id TEXT NOT NULL REFERENCES contract_bins(bin_id),
  book_snapshot_id TEXT NOT NULL REFERENCES raw_snapshots(snapshot_id),
  book_hash TEXT NOT NULL,
  side TEXT NOT NULL,
  price REAL NOT NULL,
  quantity REAL NOT NULL,
  fee REAL NOT NULL,
  filled_at TEXT NOT NULL,
  level_index INTEGER NOT NULL,
  UNIQUE(order_id, book_hash, side, level_index)
);

CREATE TABLE IF NOT EXISTS paper_accounts (
  decision_id TEXT PRIMARY KEY REFERENCES decisions(decision_id),
  cash REAL NOT NULL,
  reserved_cash REAL NOT NULL,
  used_notional REAL NOT NULL,
  realized_pnl REAL NOT NULL,
  unrealized_pnl REAL NOT NULL,
  total_pnl REAL NOT NULL,
  nav REAL NOT NULL,
  positions_json TEXT NOT NULL,
  scenario_pnl_json TEXT NOT NULL,
  mark_source TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS system_events (
  event_id TEXT PRIMARY KEY,
  occurred_at TEXT NOT NULL,
  level TEXT NOT NULL,
  source TEXT NOT NULL,
  event_type TEXT NOT NULL,
  message TEXT NOT NULL,
  context_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runner_heartbeats (
  heartbeat_id TEXT PRIMARY KEY,
  runner_id TEXT NOT NULL,
  mode TEXT NOT NULL,
  cycle INTEGER NOT NULL,
  occurred_at TEXT NOT NULL,
  decision_id TEXT,
  status TEXT NOT NULL
);

