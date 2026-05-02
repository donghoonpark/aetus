CREATE EXTENSION IF NOT EXISTS timescaledb;

SELECT create_hypertable('device_metric_points', 'event_time', if_not_exists => TRUE);
SELECT create_hypertable('device_signal_frames', 'event_time', if_not_exists => TRUE);

ALTER TABLE device_metric_points SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'device_pk, metric_pk',
    timescaledb.compress_orderby = 'event_time DESC'
);

ALTER TABLE device_signal_frames SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'device_pk, signal_pk',
    timescaledb.compress_orderby = 'event_time DESC'
);

SELECT add_compression_policy('device_metric_points', INTERVAL '7 days', if_not_exists => TRUE);
SELECT add_retention_policy('device_metric_points', INTERVAL '1 year', if_not_exists => TRUE);
SELECT add_compression_policy('device_signal_frames', INTERVAL '7 days', if_not_exists => TRUE);
SELECT add_retention_policy('device_signal_frames', INTERVAL '1 year', if_not_exists => TRUE);
