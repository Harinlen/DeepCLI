"""SQLAlchemy Core table definitions for shared Kernel SQLite stores."""

from __future__ import annotations

import sqlalchemy as sa

resource_metadata = sa.MetaData()
secret_metadata = sa.MetaData()

store_meta = sa.Table(
    "store_meta",
    resource_metadata,
    sa.Column("key", sa.Text, primary_key=True),
    sa.Column("value_json", sa.Text, nullable=False),
    sa.Column("updated_at", sa.Text, nullable=False),
)

global_resources = sa.Table(
    "global_resources",
    resource_metadata,
    sa.Column("resource_key", sa.Text, primary_key=True),
    sa.Column("payload_json", sa.Text, nullable=False),
    sa.Column("revision", sa.Integer, nullable=False),
    sa.Column("updated_at", sa.Text, nullable=False),
    sa.Column("updated_by_agent_id", sa.Text),
    sa.Column("payload_hash", sa.Text, nullable=False),
)

global_resource_events = sa.Table(
    "global_resource_events",
    resource_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("resource_key", sa.Text, nullable=False),
    sa.Column("revision", sa.Integer, nullable=False),
    sa.Column("event_type", sa.Text, nullable=False),
    sa.Column("updated_at", sa.Text, nullable=False),
    sa.Column("updated_by_agent_id", sa.Text),
    sa.Column("payload_hash", sa.Text, nullable=False),
    sa.Column("previous_payload_hash", sa.Text),
    sa.Index("idx_global_resource_events_key_revision", "resource_key", "revision"),
)

resource_revisions = sa.Table(
    "resource_revisions",
    resource_metadata,
    sa.Column("resource_key", sa.Text, primary_key=True),
    sa.Column("revision", sa.Integer, nullable=False),
    sa.Column("updated_at", sa.Text, nullable=False),
    sa.Column("payload_hash", sa.Text, nullable=False),
)

config_sections = sa.Table(
    "config_sections",
    resource_metadata,
    sa.Column("scope", sa.Text, primary_key=True),
    sa.Column("scope_id", sa.Text, primary_key=True),
    sa.Column("file", sa.Text, primary_key=True),
    sa.Column("section", sa.Text, primary_key=True),
    sa.Column("payload_json", sa.Text, nullable=False),
    sa.Column("revision", sa.Integer, nullable=False),
    sa.Column("updated_at", sa.Text, nullable=False),
    sa.Column("updated_by_agent_id", sa.Text),
    sa.Column("payload_hash", sa.Text, nullable=False),
)

config_events = sa.Table(
    "config_events",
    resource_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("scope", sa.Text, nullable=False),
    sa.Column("scope_id", sa.Text, nullable=False),
    sa.Column("file", sa.Text, nullable=False),
    sa.Column("section", sa.Text, nullable=False),
    sa.Column("revision", sa.Integer, nullable=False),
    sa.Column("event_type", sa.Text, nullable=False),
    sa.Column("updated_at", sa.Text, nullable=False),
    sa.Column("updated_by_agent_id", sa.Text),
    sa.Column("payload_hash", sa.Text, nullable=False),
    sa.Column("previous_payload_hash", sa.Text),
    sa.Index("idx_config_events_section", "scope", "scope_id", "file", "section", "revision"),
)

flag_sections = sa.Table(
    "flag_sections",
    resource_metadata,
    sa.Column("section", sa.Text, primary_key=True),
    sa.Column("payload_json", sa.Text, nullable=False),
    sa.Column("revision", sa.Integer, nullable=False, default=1),
    sa.Column("updated_at", sa.Text, nullable=False),
    sa.Column("updated_by_agent_id", sa.Text),
    sa.Column("payload_hash", sa.Text, nullable=False),
)

flag_events = sa.Table(
    "flag_events",
    resource_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("section", sa.Text, nullable=False),
    sa.Column("revision", sa.Integer, nullable=False),
    sa.Column("event_type", sa.Text, nullable=False),
    sa.Column("updated_at", sa.Text, nullable=False),
    sa.Column("updated_by_agent_id", sa.Text),
    sa.Column("payload_hash", sa.Text, nullable=False),
    sa.Column("previous_payload_hash", sa.Text),
)

migration_sources = sa.Table(
    "migration_sources",
    resource_metadata,
    sa.Column("source_id", sa.Text, primary_key=True),
    sa.Column("source_path", sa.Text, nullable=False),
    sa.Column("source_hash", sa.Text, nullable=False),
    sa.Column("source_kind", sa.Text, nullable=False),
    sa.Column("imported_at", sa.Text, nullable=False),
    sa.Column("imported_by", sa.Text, nullable=False),
    sa.Column("target_resource_keys_json", sa.Text, nullable=False),
    sa.Column("report_json", sa.Text, nullable=False),
)

agent_definitions = sa.Table(
    "agent_definitions",
    resource_metadata,
    sa.Column("agent_id", sa.Text, primary_key=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("identity_json", sa.Text, nullable=False),
    sa.Column("workspace", sa.Text, nullable=False),
    sa.Column("state_dir", sa.Text, nullable=False),
    sa.Column("runtime_json", sa.Text, nullable=False),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("deleted_at", sa.Text),
    sa.Column("state_dir_deletion_status", sa.Text),
    sa.Column("revision", sa.Integer, nullable=False),
    sa.Column("updated_at", sa.Text, nullable=False),
    sa.Column("updated_by_agent_id", sa.Text),
)

agent_bindings = sa.Table(
    "agent_bindings",
    resource_metadata,
    sa.Column("binding_id", sa.Text, primary_key=True),
    sa.Column("agent_id", sa.Text, nullable=False),
    sa.Column("binding_type", sa.Text, nullable=False),
    sa.Column("binding_json", sa.Text, nullable=False),
    sa.Column("enabled", sa.Integer, nullable=False),
    sa.Column("revision", sa.Integer, nullable=False),
    sa.Column("updated_at", sa.Text, nullable=False),
    sa.Column("updated_by_agent_id", sa.Text),
)

agent_directory_meta = sa.Table(
    "agent_directory_meta",
    resource_metadata,
    sa.Column("key", sa.Text, primary_key=True),
    sa.Column("value", sa.Integer, nullable=False),
)

agent_runtime_status = sa.Table(
    "agent_runtime_status",
    resource_metadata,
    sa.Column("agent_id", sa.Text, primary_key=True),
    sa.Column("desired_state", sa.Text, nullable=False),
    sa.Column("observed_state", sa.Text, nullable=False),
    sa.Column("pid", sa.Integer),
    sa.Column("started_at", sa.Text),
    sa.Column("stopped_at", sa.Text),
    sa.Column("last_exit_code", sa.Integer),
    sa.Column("last_error", sa.Text),
    sa.Column("heartbeat_at", sa.Text),
    sa.Column("route_status", sa.Text),
    sa.Column("route_seen_at", sa.Text),
    sa.Column("updated_at", sa.Text, nullable=False),
)

management_grants = sa.Table(
    "management_grants",
    resource_metadata,
    sa.Column("grant_id", sa.Text, primary_key=True),
    sa.Column("subject_agent_id", sa.Text, nullable=False),
    sa.Column("capability", sa.Text, nullable=False),
    sa.Column("resource_scope", sa.Text, nullable=False),
    sa.Column("resource_id", sa.Text),
    sa.Column("owner_agent_id", sa.Text),
    sa.Column("workspace", sa.Text),
    sa.Column("granted_by_agent_id", sa.Text, nullable=False),
    sa.Column("granted_at", sa.Text, nullable=False),
    sa.Column("expires_at", sa.Text),
    sa.Column("revoked_at", sa.Text),
    sa.Index("idx_management_grants_subject", "subject_agent_id", "capability", "resource_scope"),
)

access_adapters = sa.Table(
    "access_adapters",
    resource_metadata,
    sa.Column("adapter_id", sa.Text, primary_key=True),
    sa.Column("adapter_type", sa.Text, nullable=False),
    sa.Column("config_json", sa.Text, nullable=False),
    sa.Column("enabled", sa.Integer, nullable=False),
    sa.Column("revision", sa.Integer, nullable=False),
    sa.Column("updated_at", sa.Text, nullable=False),
    sa.Column("updated_by_agent_id", sa.Text),
    sa.Column("payload_hash", sa.Text, nullable=False),
)

access_adapter_events = sa.Table(
    "access_adapter_events",
    resource_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("adapter_id", sa.Text, nullable=False),
    sa.Column("event_type", sa.Text, nullable=False),
    sa.Column("revision", sa.Integer),
    sa.Column("actor_agent_id", sa.Text),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("payload_hash", sa.Text),
)

access_channel_bindings = sa.Table(
    "access_channel_bindings",
    resource_metadata,
    sa.Column("binding_id", sa.Text, primary_key=True),
    sa.Column("adapter_id", sa.Text, nullable=False),
    sa.Column("channel_key", sa.Text, nullable=False),
    sa.Column("target_agent_id", sa.Text, nullable=False),
    sa.Column("target_session_id", sa.Text),
    sa.Column("enabled", sa.Integer, nullable=False),
    sa.Column("revision", sa.Integer, nullable=False),
    sa.Column("updated_at", sa.Text, nullable=False),
    sa.Column("updated_by_agent_id", sa.Text),
    sa.UniqueConstraint("adapter_id", "channel_key"),
)

access_idempotency_keys = sa.Table(
    "access_idempotency_keys",
    resource_metadata,
    sa.Column("idempotency_key", sa.Text, primary_key=True),
    sa.Column("direction", sa.Text, nullable=False),
    sa.Column("adapter_id", sa.Text),
    sa.Column("external_message_id", sa.Text),
    sa.Column("internal_message_id", sa.Text, nullable=False),
    sa.Column("target_agent_id", sa.Text),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("result_json", sa.Text),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("updated_at", sa.Text, nullable=False),
    sa.Column("expires_at", sa.Text),
    sa.Index("idx_access_idempotency_external", "adapter_id", "external_message_id"),
)

scheduled_tasks = sa.Table(
    "scheduled_tasks",
    resource_metadata,
    sa.Column("task_id", sa.Text, primary_key=True),
    sa.Column("owner_agent_id", sa.Text, nullable=False),
    sa.Column("title", sa.Text, nullable=False),
    sa.Column("schedule_json", sa.Text, nullable=False),
    sa.Column("target_json", sa.Text, nullable=False),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("revision", sa.Integer, nullable=False),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("updated_at", sa.Text, nullable=False),
    sa.Column("updated_by_agent_id", sa.Text),
)

scheduled_task_events = sa.Table(
    "scheduled_task_events",
    resource_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("task_id", sa.Text, nullable=False),
    sa.Column("event_type", sa.Text, nullable=False),
    sa.Column("revision", sa.Integer),
    sa.Column("actor_agent_id", sa.Text),
    sa.Column("owner_agent_id", sa.Text),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("payload_hash", sa.Text),
    sa.Index("idx_scheduled_task_events_task", "task_id", "revision"),
)

agent_spawned_runs = sa.Table(
    "agent_spawned_runs",
    resource_metadata,
    sa.Column("run_id", sa.Text, primary_key=True),
    sa.Column("parent_session_id", sa.Text, nullable=False),
    sa.Column("requester_agent_id", sa.Text, nullable=False),
    sa.Column("target_agent_id", sa.Text, nullable=False),
    sa.Column("runtime", sa.Text, nullable=False),
    sa.Column("mode", sa.Text, nullable=False),
    sa.Column("session_id", sa.Text, nullable=False),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("task", sa.Text),
    sa.Column("last_message", sa.Text),
    sa.Column("result_json", sa.Text),
    sa.Column("provenance_json", sa.Text),
    sa.Column("binding_id", sa.Text),
    sa.Column("timeout_seconds", sa.Integer),
    sa.Column("wait_mode", sa.Text),
    sa.Column("reply_back_enabled", sa.Integer, nullable=False, server_default="0"),
    sa.Column("announce_enabled", sa.Integer, nullable=False, server_default="0"),
    sa.Column("acp_session_id", sa.Text),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("updated_at", sa.Text, nullable=False),
    sa.Column("stopped_at", sa.Text),
    sa.Column("revision", sa.Integer, nullable=False),
    sa.Index("idx_agent_spawned_runs_owner", "requester_agent_id", "status"),
    sa.Index("idx_agent_spawned_runs_session", "session_id"),
)

agent_spawned_run_events = sa.Table(
    "agent_spawned_run_events",
    resource_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("run_id", sa.Text, nullable=False),
    sa.Column("event_type", sa.Text, nullable=False),
    sa.Column("revision", sa.Integer, nullable=False),
    sa.Column("actor_agent_id", sa.Text),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("payload_json", sa.Text, nullable=False),
    sa.Index("idx_agent_spawned_run_events_run", "run_id", "revision"),
)

secrets = sa.Table(
    "secrets",
    secret_metadata,
    sa.Column("secret_id", sa.Text, primary_key=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("value_ciphertext", sa.LargeBinary, nullable=False),
    sa.Column("revision", sa.Integer, nullable=False),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("updated_at", sa.Text, nullable=False),
    sa.Column("created_by_agent_id", sa.Text),
    sa.Column("updated_by_agent_id", sa.Text),
    sa.Column("payload_hash", sa.Text, nullable=False),
    sa.Index("idx_secrets_name", "name", unique=True),
)

secret_events = sa.Table(
    "secret_events",
    secret_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("secret_id", sa.Text),
    sa.Column("event_type", sa.Text, nullable=False),
    sa.Column("actor_agent_id", sa.Text),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("metadata_json", sa.Text, nullable=False, default="{}"),
    sa.Column("payload_hash", sa.Text),
    sa.Column("previous_payload_hash", sa.Text),
    sa.Index("idx_secret_events_secret", "secret_id", "created_at"),
)

secret_migration_sources = sa.Table(
    "secret_migration_sources",
    secret_metadata,
    sa.Column("source_id", sa.Text, primary_key=True),
    sa.Column("source_path", sa.Text, nullable=False),
    sa.Column("source_hash", sa.Text, nullable=False),
    sa.Column("imported_at", sa.Text, nullable=False),
    sa.Column("imported_by", sa.Text, nullable=False),
    sa.Column("report_json", sa.Text, nullable=False),
)
