create table knowledge.domain_type (
  host_id text not null references knowledge.host(host_id),
  domain text not null check (length(btrim(domain)) > 0),
  description text not null default '',
  status text not null default 'active' check (status in ('active', 'retired')),
  merged_into text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (host_id, domain),
  check (merged_into is null or (status = 'retired' and merged_into <> domain))
);

alter table knowledge.domain_type enable row level security;

insert into knowledge.domain_type(host_id, domain)
select distinct host_id, domain from knowledge.fragment
on conflict do nothing;
