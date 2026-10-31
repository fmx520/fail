-- Supabase / Postgres drop log for the iPhone Duo pickup watcher.
-- One row per completed available -> sold-out cycle. The watcher also
-- writes open drops to drop_log.jsonl locally; this table is the durable view.
create table if not exists drops (
  id bigint generated always as identity primary key,
  store text not null,
  part text not null,
  config text not null,
  appeared_at timestamptz,
  disappeared_at timestamptz,
  created_at timestamptz not null default now()
);

-- Drops per store
-- select store, count(*) as drops from drops group by store order by drops desc;

-- Median duration (minutes) per store
-- select store, percentile_cont(0.5) within group (order by
--   extract(epoch from (disappeared_at - appeared_at)) / 60) as median_minutes
-- from drops where disappeared_at is not null group by store;

-- Time-of-day histogram, New York time
-- select extract(hour from appeared_at at time zone 'America/New_York') as hour_ny,
--   count(*) as drops from drops group by hour_ny order by hour_ny;
