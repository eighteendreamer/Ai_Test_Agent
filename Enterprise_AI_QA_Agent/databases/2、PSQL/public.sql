/*
 Navicat Premium Dump SQL

 Source Server         : Docker_PSQL
 Source Server Type    : PostgreSQL
 Source Server Version : 150002 (150002)
 Source Host           : localhost:5432
 Source Catalog        : QA-Agent
 Source Schema         : public

 Target Server Type    : PostgreSQL
 Target Server Version : 150002 (150002)
 File Encoding         : 65001

 Date: 26/07/2026 21:07:45
*/


-- ----------------------------
-- Type structure for vector
-- ----------------------------
DROP TYPE IF EXISTS "public"."vector";
CREATE TYPE "public"."vector" (
  INPUT = "public"."vector_in",
  OUTPUT = "public"."vector_out",
  RECEIVE = "public"."vector_recv",
  SEND = "public"."vector_send",
  TYPMOD_IN = "public"."vector_typmod_in",
  INTERNALLENGTH = VARIABLE,
  STORAGE = external,
  CATEGORY = U,
  DELIMITER = ','
);
ALTER TYPE "public"."vector" OWNER TO "postgres";

-- ----------------------------
-- Table structure for agent_mcp_servers
-- ----------------------------
DROP TABLE IF EXISTS "public"."agent_mcp_servers";
CREATE TABLE "public"."agent_mcp_servers" (
  "id" text COLLATE "pg_catalog"."default" NOT NULL,
  "name" text COLLATE "pg_catalog"."default" NOT NULL,
  "enabled" bool NOT NULL DEFAULT true,
  "confirmed_at" timestamptz(6),
  "metadata" jsonb NOT NULL DEFAULT '{}'::jsonb,
  "created_at" timestamptz(6) NOT NULL,
  "updated_at" timestamptz(6) NOT NULL,
  "config" jsonb NOT NULL DEFAULT '{}'::jsonb,
  "purpose" text COLLATE "pg_catalog"."default",
  "supported_protocols" jsonb NOT NULL DEFAULT '[]'::jsonb
)
;

-- ----------------------------
-- Table structure for agent_memories
-- ----------------------------
DROP TABLE IF EXISTS "public"."agent_memories";
CREATE TABLE "public"."agent_memories" (
  "id" text COLLATE "pg_catalog"."default" NOT NULL,
  "scope" text COLLATE "pg_catalog"."default" NOT NULL,
  "kind" text COLLATE "pg_catalog"."default" NOT NULL,
  "content" text COLLATE "pg_catalog"."default" NOT NULL,
  "summary" text COLLATE "pg_catalog"."default" NOT NULL DEFAULT ''::text,
  "tags" text[] COLLATE "pg_catalog"."default" NOT NULL DEFAULT ARRAY[]::text[],
  "session_id" text COLLATE "pg_catalog"."default",
  "turn_id" text COLLATE "pg_catalog"."default",
  "trace_id" text COLLATE "pg_catalog"."default",
  "source" text COLLATE "pg_catalog"."default",
  "stale" bool NOT NULL DEFAULT false,
  "mode_key" text COLLATE "pg_catalog"."default" NOT NULL DEFAULT 'default'::text,
  "metadata" jsonb NOT NULL DEFAULT '{}'::jsonb,
  "embedding" "public"."vector",
  "created_at" timestamptz(6) NOT NULL,
  "updated_at" timestamptz(6) NOT NULL
)
;

-- ----------------------------
-- Table structure for agent_perf_runs
-- ----------------------------
DROP TABLE IF EXISTS "public"."agent_perf_runs";
CREATE TABLE "public"."agent_perf_runs" (
  "run_id" text COLLATE "pg_catalog"."default" NOT NULL,
  "plan_id" text COLLATE "pg_catalog"."default" NOT NULL,
  "engine" text COLLATE "pg_catalog"."default" NOT NULL DEFAULT 'k6'::text,
  "backend" text COLLATE "pg_catalog"."default" NOT NULL DEFAULT 'docker'::text,
  "status" text COLLATE "pg_catalog"."default" NOT NULL DEFAULT 'pending'::text,
  "run_intent" text COLLATE "pg_catalog"."default" NOT NULL DEFAULT 'probe'::text,
  "target_url" text COLLATE "pg_catalog"."default",
  "metrics_json" jsonb,
  "verdict" text COLLATE "pg_catalog"."default",
  "started_at" timestamptz(6),
  "completed_at" timestamptz(6),
  "created_at" timestamptz(6) DEFAULT now()
)
;

-- ----------------------------
-- Table structure for agent_session_approvals
-- ----------------------------
DROP TABLE IF EXISTS "public"."agent_session_approvals";
CREATE TABLE "public"."agent_session_approvals" (
  "id" text COLLATE "pg_catalog"."default" NOT NULL,
  "session_id" text COLLATE "pg_catalog"."default" NOT NULL,
  "tool_key" text COLLATE "pg_catalog"."default" NOT NULL,
  "tool_name" text COLLATE "pg_catalog"."default" NOT NULL,
  "reason" text COLLATE "pg_catalog"."default" NOT NULL,
  "status" text COLLATE "pg_catalog"."default" NOT NULL,
  "created_at" timestamptz(6) NOT NULL,
  "resolved_at" timestamptz(6),
  "decision_note" text COLLATE "pg_catalog"."default",
  "metadata" jsonb NOT NULL DEFAULT '{}'::jsonb
)
;

-- ----------------------------
-- Table structure for agent_session_events
-- ----------------------------
DROP TABLE IF EXISTS "public"."agent_session_events";
CREATE TABLE "public"."agent_session_events" (
  "id" text COLLATE "pg_catalog"."default" NOT NULL,
  "session_id" text COLLATE "pg_catalog"."default" NOT NULL,
  "type" text COLLATE "pg_catalog"."default" NOT NULL,
  "timestamp" timestamptz(6) NOT NULL,
  "payload" jsonb NOT NULL DEFAULT '{}'::jsonb
)
;

-- ----------------------------
-- Table structure for agent_session_messages
-- ----------------------------
DROP TABLE IF EXISTS "public"."agent_session_messages";
CREATE TABLE "public"."agent_session_messages" (
  "id" text COLLATE "pg_catalog"."default" NOT NULL,
  "session_id" text COLLATE "pg_catalog"."default" NOT NULL,
  "role" text COLLATE "pg_catalog"."default" NOT NULL,
  "content" text COLLATE "pg_catalog"."default" NOT NULL,
  "created_at" timestamptz(6) NOT NULL,
  "metadata" jsonb NOT NULL DEFAULT '{}'::jsonb
)
;

-- ----------------------------
-- Table structure for agent_session_resources
-- ----------------------------
DROP TABLE IF EXISTS "public"."agent_session_resources";
CREATE TABLE "public"."agent_session_resources" (
  "id" text COLLATE "pg_catalog"."default" NOT NULL,
  "session_id" text COLLATE "pg_catalog"."default" NOT NULL,
  "kind" text COLLATE "pg_catalog"."default" NOT NULL,
  "resource_key" text COLLATE "pg_catalog"."default" NOT NULL,
  "status" text COLLATE "pg_catalog"."default" NOT NULL,
  "cleanup_policy" text COLLATE "pg_catalog"."default" NOT NULL,
  "created_at" timestamptz(6) NOT NULL,
  "updated_at" timestamptz(6) NOT NULL,
  "released_at" timestamptz(6),
  "metadata" jsonb NOT NULL DEFAULT '{}'::jsonb
)
;

-- ----------------------------
-- Table structure for agent_session_snapshots
-- ----------------------------
DROP TABLE IF EXISTS "public"."agent_session_snapshots";
CREATE TABLE "public"."agent_session_snapshots" (
  "id" text COLLATE "pg_catalog"."default" NOT NULL,
  "session_id" text COLLATE "pg_catalog"."default" NOT NULL,
  "version" int4 NOT NULL,
  "stage" text COLLATE "pg_catalog"."default" NOT NULL,
  "created_at" timestamptz(6) NOT NULL,
  "graph_state" jsonb NOT NULL DEFAULT '{}'::jsonb
)
;

-- ----------------------------
-- Table structure for agent_sessions
-- ----------------------------
DROP TABLE IF EXISTS "public"."agent_sessions";
CREATE TABLE "public"."agent_sessions" (
  "id" text COLLATE "pg_catalog"."default" NOT NULL,
  "title" text COLLATE "pg_catalog"."default" NOT NULL,
  "status" text COLLATE "pg_catalog"."default" NOT NULL,
  "session_mode" text COLLATE "pg_catalog"."default" NOT NULL,
  "runtime_mode" text COLLATE "pg_catalog"."default" NOT NULL,
  "mode_key" text COLLATE "pg_catalog"."default" NOT NULL DEFAULT 'default'::text,
  "created_at" timestamptz(6) NOT NULL,
  "updated_at" timestamptz(6) NOT NULL,
  "preferred_model" text COLLATE "pg_catalog"."default",
  "selected_agent" text COLLATE "pg_catalog"."default",
  "metadata" jsonb NOT NULL DEFAULT '{}'::jsonb,
  "event_count" int4 NOT NULL DEFAULT 0,
  "snapshot_count" int4 NOT NULL DEFAULT 0
)
;

-- ----------------------------
-- Table structure for agent_smoke_plan_catalog
-- ----------------------------
DROP TABLE IF EXISTS "public"."agent_smoke_plan_catalog";
CREATE TABLE "public"."agent_smoke_plan_catalog" (
  "plan_id" text COLLATE "pg_catalog"."default" NOT NULL,
  "project_scope" text COLLATE "pg_catalog"."default" NOT NULL,
  "target_url" text COLLATE "pg_catalog"."default" NOT NULL DEFAULT ''::text,
  "title" text COLLATE "pg_catalog"."default" NOT NULL DEFAULT ''::text,
  "status" text COLLATE "pg_catalog"."default" NOT NULL DEFAULT ''::text,
  "current_version" int4 NOT NULL DEFAULT 1,
  "approved_version" int4,
  "selected_case_count" int4 NOT NULL DEFAULT 0,
  "total_case_count" int4 NOT NULL DEFAULT 0,
  "last_run_status" text COLLATE "pg_catalog"."default" NOT NULL DEFAULT ''::text,
  "last_run_at" timestamptz(6),
  "plan_uri" text COLLATE "pg_catalog"."default" NOT NULL DEFAULT ''::text,
  "approved_plan_uri" text COLLATE "pg_catalog"."default" NOT NULL DEFAULT ''::text,
  "run_result_uri" text COLLATE "pg_catalog"."default" NOT NULL DEFAULT ''::text,
  "report_uri" text COLLATE "pg_catalog"."default" NOT NULL DEFAULT ''::text,
  "created_by_session_id" text COLLATE "pg_catalog"."default" NOT NULL DEFAULT ''::text,
  "metadata" jsonb NOT NULL DEFAULT '{}'::jsonb,
  "created_at" timestamptz(6) NOT NULL,
  "updated_at" timestamptz(6) NOT NULL
)
;

-- ----------------------------
-- Table structure for agent_smoke_plan_versions
-- ----------------------------
DROP TABLE IF EXISTS "public"."agent_smoke_plan_versions";
CREATE TABLE "public"."agent_smoke_plan_versions" (
  "id" text COLLATE "pg_catalog"."default" NOT NULL,
  "plan_id" text COLLATE "pg_catalog"."default" NOT NULL,
  "version" int4 NOT NULL,
  "revision_reason" text COLLATE "pg_catalog"."default" NOT NULL DEFAULT ''::text,
  "user_revision" text COLLATE "pg_catalog"."default" NOT NULL DEFAULT ''::text,
  "plan_uri" text COLLATE "pg_catalog"."default" NOT NULL DEFAULT ''::text,
  "plan_md_uri" text COLLATE "pg_catalog"."default" NOT NULL DEFAULT ''::text,
  "case_count" int4 NOT NULL DEFAULT 0,
  "selected_case_count" int4 NOT NULL DEFAULT 0,
  "risk_summary" jsonb NOT NULL DEFAULT '{}'::jsonb,
  "created_at" timestamptz(6) NOT NULL
)
;

-- ----------------------------
-- Table structure for agent_smoke_regression_candidates
-- ----------------------------
DROP TABLE IF EXISTS "public"."agent_smoke_regression_candidates";
CREATE TABLE "public"."agent_smoke_regression_candidates" (
  "case_id" text COLLATE "pg_catalog"."default" NOT NULL,
  "source_plan_id" text COLLATE "pg_catalog"."default" NOT NULL,
  "source_run_id" text COLLATE "pg_catalog"."default" NOT NULL DEFAULT ''::text,
  "project_scope" text COLLATE "pg_catalog"."default" NOT NULL DEFAULT ''::text,
  "case_type" text COLLATE "pg_catalog"."default" NOT NULL DEFAULT ''::text,
  "title" text COLLATE "pg_catalog"."default" NOT NULL DEFAULT ''::text,
  "case_uri" text COLLATE "pg_catalog"."default" NOT NULL DEFAULT ''::text,
  "stability_score" float8 NOT NULL DEFAULT 0,
  "status" text COLLATE "pg_catalog"."default" NOT NULL DEFAULT ''::text,
  "run_count" int4 NOT NULL DEFAULT 0,
  "pass_count" int4 NOT NULL DEFAULT 0,
  "fail_count" int4 NOT NULL DEFAULT 0,
  "flaky_count" int4 NOT NULL DEFAULT 0,
  "blocked_count" int4 NOT NULL DEFAULT 0,
  "last_status" text COLLATE "pg_catalog"."default" NOT NULL DEFAULT ''::text,
  "last_passed_at" timestamptz(6),
  "updated_at" timestamptz(6) NOT NULL
)
;

-- ----------------------------
-- Table structure for agent_smoke_run_history
-- ----------------------------
DROP TABLE IF EXISTS "public"."agent_smoke_run_history";
CREATE TABLE "public"."agent_smoke_run_history" (
  "run_id" text COLLATE "pg_catalog"."default" NOT NULL,
  "plan_id" text COLLATE "pg_catalog"."default" NOT NULL,
  "plan_version" int4 NOT NULL,
  "project_scope" text COLLATE "pg_catalog"."default" NOT NULL DEFAULT ''::text,
  "status" text COLLATE "pg_catalog"."default" NOT NULL DEFAULT ''::text,
  "verdict" text COLLATE "pg_catalog"."default" NOT NULL DEFAULT ''::text,
  "total_cases" int4 NOT NULL DEFAULT 0,
  "passed_cases" int4 NOT NULL DEFAULT 0,
  "failed_cases" int4 NOT NULL DEFAULT 0,
  "blocked_cases" int4 NOT NULL DEFAULT 0,
  "started_at" timestamptz(6) NOT NULL,
  "completed_at" timestamptz(6),
  "run_result_uri" text COLLATE "pg_catalog"."default" NOT NULL DEFAULT ''::text,
  "report_uri" text COLLATE "pg_catalog"."default" NOT NULL DEFAULT ''::text,
  "evidence_manifest_uri" text COLLATE "pg_catalog"."default" NOT NULL DEFAULT ''::text,
  "metadata" jsonb NOT NULL DEFAULT '{}'::jsonb
)
;

-- ----------------------------
-- Table structure for agent_tool_artifacts
-- ----------------------------
DROP TABLE IF EXISTS "public"."agent_tool_artifacts";
CREATE TABLE "public"."agent_tool_artifacts" (
  "id" text COLLATE "pg_catalog"."default" NOT NULL,
  "tool_job_id" text COLLATE "pg_catalog"."default" NOT NULL,
  "session_id" text COLLATE "pg_catalog"."default" NOT NULL,
  "turn_id" text COLLATE "pg_catalog"."default" NOT NULL,
  "trace_id" text COLLATE "pg_catalog"."default" NOT NULL,
  "tool_key" text COLLATE "pg_catalog"."default" NOT NULL,
  "artifact_type" text COLLATE "pg_catalog"."default" NOT NULL,
  "label" text COLLATE "pg_catalog"."default",
  "path" text COLLATE "pg_catalog"."default" NOT NULL,
  "storage_mode" text COLLATE "pg_catalog"."default" NOT NULL DEFAULT 'path_only'::text,
  "content_text" text COLLATE "pg_catalog"."default" NOT NULL DEFAULT ''::text,
  "metadata" jsonb NOT NULL DEFAULT '{}'::jsonb,
  "created_at" timestamptz(6) NOT NULL
)
;

-- ----------------------------
-- Table structure for agent_tool_jobs
-- ----------------------------
DROP TABLE IF EXISTS "public"."agent_tool_jobs";
CREATE TABLE "public"."agent_tool_jobs" (
  "id" text COLLATE "pg_catalog"."default" NOT NULL,
  "session_id" text COLLATE "pg_catalog"."default" NOT NULL,
  "turn_id" text COLLATE "pg_catalog"."default" NOT NULL,
  "trace_id" text COLLATE "pg_catalog"."default" NOT NULL,
  "call_id" text COLLATE "pg_catalog"."default" NOT NULL,
  "tool_key" text COLLATE "pg_catalog"."default" NOT NULL,
  "tool_name" text COLLATE "pg_catalog"."default" NOT NULL,
  "status" text COLLATE "pg_catalog"."default" NOT NULL,
  "attempt" int4 NOT NULL DEFAULT 1,
  "summary" text COLLATE "pg_catalog"."default" NOT NULL DEFAULT ''::text,
  "error_message" text COLLATE "pg_catalog"."default",
  "artifact_count" int4 NOT NULL DEFAULT 0,
  "input_payload" jsonb NOT NULL DEFAULT '{}'::jsonb,
  "output_payload" jsonb NOT NULL DEFAULT '{}'::jsonb,
  "metadata" jsonb NOT NULL DEFAULT '{}'::jsonb,
  "created_at" timestamptz(6) NOT NULL,
  "updated_at" timestamptz(6) NOT NULL,
  "heartbeat_at" timestamptz(6),
  "started_at" timestamptz(6),
  "completed_at" timestamptz(6)
)
;

-- ----------------------------
-- Function structure for array_to_vector
-- ----------------------------
DROP FUNCTION IF EXISTS "public"."array_to_vector"(_float4, int4, bool);
CREATE OR REPLACE FUNCTION "public"."array_to_vector"(_float4, int4, bool)
  RETURNS "public"."vector" AS '$libdir/vector', 'array_to_vector'
  LANGUAGE c IMMUTABLE STRICT
  COST 1;

-- ----------------------------
-- Function structure for array_to_vector
-- ----------------------------
DROP FUNCTION IF EXISTS "public"."array_to_vector"(_numeric, int4, bool);
CREATE OR REPLACE FUNCTION "public"."array_to_vector"(_numeric, int4, bool)
  RETURNS "public"."vector" AS '$libdir/vector', 'array_to_vector'
  LANGUAGE c IMMUTABLE STRICT
  COST 1;

-- ----------------------------
-- Function structure for array_to_vector
-- ----------------------------
DROP FUNCTION IF EXISTS "public"."array_to_vector"(_float8, int4, bool);
CREATE OR REPLACE FUNCTION "public"."array_to_vector"(_float8, int4, bool)
  RETURNS "public"."vector" AS '$libdir/vector', 'array_to_vector'
  LANGUAGE c IMMUTABLE STRICT
  COST 1;

-- ----------------------------
-- Function structure for array_to_vector
-- ----------------------------
DROP FUNCTION IF EXISTS "public"."array_to_vector"(_int4, int4, bool);
CREATE OR REPLACE FUNCTION "public"."array_to_vector"(_int4, int4, bool)
  RETURNS "public"."vector" AS '$libdir/vector', 'array_to_vector'
  LANGUAGE c IMMUTABLE STRICT
  COST 1;

-- ----------------------------
-- Function structure for cosine_distance
-- ----------------------------
DROP FUNCTION IF EXISTS "public"."cosine_distance"("public"."vector", "public"."vector");
CREATE OR REPLACE FUNCTION "public"."cosine_distance"("public"."vector", "public"."vector")
  RETURNS "pg_catalog"."float8" AS '$libdir/vector', 'cosine_distance'
  LANGUAGE c IMMUTABLE STRICT
  COST 1;

-- ----------------------------
-- Function structure for hnswhandler
-- ----------------------------
DROP FUNCTION IF EXISTS "public"."hnswhandler"(internal);
CREATE OR REPLACE FUNCTION "public"."hnswhandler"(internal)
  RETURNS "pg_catalog"."index_am_handler" AS '$libdir/vector', 'hnswhandler'
  LANGUAGE c VOLATILE
  COST 1;

-- ----------------------------
-- Function structure for inner_product
-- ----------------------------
DROP FUNCTION IF EXISTS "public"."inner_product"("public"."vector", "public"."vector");
CREATE OR REPLACE FUNCTION "public"."inner_product"("public"."vector", "public"."vector")
  RETURNS "pg_catalog"."float8" AS '$libdir/vector', 'inner_product'
  LANGUAGE c IMMUTABLE STRICT
  COST 1;

-- ----------------------------
-- Function structure for ivfflathandler
-- ----------------------------
DROP FUNCTION IF EXISTS "public"."ivfflathandler"(internal);
CREATE OR REPLACE FUNCTION "public"."ivfflathandler"(internal)
  RETURNS "pg_catalog"."index_am_handler" AS '$libdir/vector', 'ivfflathandler'
  LANGUAGE c VOLATILE
  COST 1;

-- ----------------------------
-- Function structure for l1_distance
-- ----------------------------
DROP FUNCTION IF EXISTS "public"."l1_distance"("public"."vector", "public"."vector");
CREATE OR REPLACE FUNCTION "public"."l1_distance"("public"."vector", "public"."vector")
  RETURNS "pg_catalog"."float8" AS '$libdir/vector', 'l1_distance'
  LANGUAGE c IMMUTABLE STRICT
  COST 1;

-- ----------------------------
-- Function structure for l2_distance
-- ----------------------------
DROP FUNCTION IF EXISTS "public"."l2_distance"("public"."vector", "public"."vector");
CREATE OR REPLACE FUNCTION "public"."l2_distance"("public"."vector", "public"."vector")
  RETURNS "pg_catalog"."float8" AS '$libdir/vector', 'l2_distance'
  LANGUAGE c IMMUTABLE STRICT
  COST 1;

-- ----------------------------
-- Function structure for vector
-- ----------------------------
DROP FUNCTION IF EXISTS "public"."vector"("public"."vector", int4, bool);
CREATE OR REPLACE FUNCTION "public"."vector"("public"."vector", int4, bool)
  RETURNS "public"."vector" AS '$libdir/vector', 'vector'
  LANGUAGE c IMMUTABLE STRICT
  COST 1;

-- ----------------------------
-- Function structure for vector_accum
-- ----------------------------
DROP FUNCTION IF EXISTS "public"."vector_accum"(_float8, "public"."vector");
CREATE OR REPLACE FUNCTION "public"."vector_accum"(_float8, "public"."vector")
  RETURNS "pg_catalog"."_float8" AS '$libdir/vector', 'vector_accum'
  LANGUAGE c IMMUTABLE STRICT
  COST 1;

-- ----------------------------
-- Function structure for vector_add
-- ----------------------------
DROP FUNCTION IF EXISTS "public"."vector_add"("public"."vector", "public"."vector");
CREATE OR REPLACE FUNCTION "public"."vector_add"("public"."vector", "public"."vector")
  RETURNS "public"."vector" AS '$libdir/vector', 'vector_add'
  LANGUAGE c IMMUTABLE STRICT
  COST 1;

-- ----------------------------
-- Function structure for vector_avg
-- ----------------------------
DROP FUNCTION IF EXISTS "public"."vector_avg"(_float8);
CREATE OR REPLACE FUNCTION "public"."vector_avg"(_float8)
  RETURNS "public"."vector" AS '$libdir/vector', 'vector_avg'
  LANGUAGE c IMMUTABLE STRICT
  COST 1;

-- ----------------------------
-- Function structure for vector_cmp
-- ----------------------------
DROP FUNCTION IF EXISTS "public"."vector_cmp"("public"."vector", "public"."vector");
CREATE OR REPLACE FUNCTION "public"."vector_cmp"("public"."vector", "public"."vector")
  RETURNS "pg_catalog"."int4" AS '$libdir/vector', 'vector_cmp'
  LANGUAGE c IMMUTABLE STRICT
  COST 1;

-- ----------------------------
-- Function structure for vector_combine
-- ----------------------------
DROP FUNCTION IF EXISTS "public"."vector_combine"(_float8, _float8);
CREATE OR REPLACE FUNCTION "public"."vector_combine"(_float8, _float8)
  RETURNS "pg_catalog"."_float8" AS '$libdir/vector', 'vector_combine'
  LANGUAGE c IMMUTABLE STRICT
  COST 1;

-- ----------------------------
-- Function structure for vector_dims
-- ----------------------------
DROP FUNCTION IF EXISTS "public"."vector_dims"("public"."vector");
CREATE OR REPLACE FUNCTION "public"."vector_dims"("public"."vector")
  RETURNS "pg_catalog"."int4" AS '$libdir/vector', 'vector_dims'
  LANGUAGE c IMMUTABLE STRICT
  COST 1;

-- ----------------------------
-- Function structure for vector_eq
-- ----------------------------
DROP FUNCTION IF EXISTS "public"."vector_eq"("public"."vector", "public"."vector");
CREATE OR REPLACE FUNCTION "public"."vector_eq"("public"."vector", "public"."vector")
  RETURNS "pg_catalog"."bool" AS '$libdir/vector', 'vector_eq'
  LANGUAGE c IMMUTABLE STRICT
  COST 1;

-- ----------------------------
-- Function structure for vector_ge
-- ----------------------------
DROP FUNCTION IF EXISTS "public"."vector_ge"("public"."vector", "public"."vector");
CREATE OR REPLACE FUNCTION "public"."vector_ge"("public"."vector", "public"."vector")
  RETURNS "pg_catalog"."bool" AS '$libdir/vector', 'vector_ge'
  LANGUAGE c IMMUTABLE STRICT
  COST 1;

-- ----------------------------
-- Function structure for vector_gt
-- ----------------------------
DROP FUNCTION IF EXISTS "public"."vector_gt"("public"."vector", "public"."vector");
CREATE OR REPLACE FUNCTION "public"."vector_gt"("public"."vector", "public"."vector")
  RETURNS "pg_catalog"."bool" AS '$libdir/vector', 'vector_gt'
  LANGUAGE c IMMUTABLE STRICT
  COST 1;

-- ----------------------------
-- Function structure for vector_in
-- ----------------------------
DROP FUNCTION IF EXISTS "public"."vector_in"(cstring, oid, int4);
CREATE OR REPLACE FUNCTION "public"."vector_in"(cstring, oid, int4)
  RETURNS "public"."vector" AS '$libdir/vector', 'vector_in'
  LANGUAGE c IMMUTABLE STRICT
  COST 1;

-- ----------------------------
-- Function structure for vector_l2_squared_distance
-- ----------------------------
DROP FUNCTION IF EXISTS "public"."vector_l2_squared_distance"("public"."vector", "public"."vector");
CREATE OR REPLACE FUNCTION "public"."vector_l2_squared_distance"("public"."vector", "public"."vector")
  RETURNS "pg_catalog"."float8" AS '$libdir/vector', 'vector_l2_squared_distance'
  LANGUAGE c IMMUTABLE STRICT
  COST 1;

-- ----------------------------
-- Function structure for vector_le
-- ----------------------------
DROP FUNCTION IF EXISTS "public"."vector_le"("public"."vector", "public"."vector");
CREATE OR REPLACE FUNCTION "public"."vector_le"("public"."vector", "public"."vector")
  RETURNS "pg_catalog"."bool" AS '$libdir/vector', 'vector_le'
  LANGUAGE c IMMUTABLE STRICT
  COST 1;

-- ----------------------------
-- Function structure for vector_lt
-- ----------------------------
DROP FUNCTION IF EXISTS "public"."vector_lt"("public"."vector", "public"."vector");
CREATE OR REPLACE FUNCTION "public"."vector_lt"("public"."vector", "public"."vector")
  RETURNS "pg_catalog"."bool" AS '$libdir/vector', 'vector_lt'
  LANGUAGE c IMMUTABLE STRICT
  COST 1;

-- ----------------------------
-- Function structure for vector_mul
-- ----------------------------
DROP FUNCTION IF EXISTS "public"."vector_mul"("public"."vector", "public"."vector");
CREATE OR REPLACE FUNCTION "public"."vector_mul"("public"."vector", "public"."vector")
  RETURNS "public"."vector" AS '$libdir/vector', 'vector_mul'
  LANGUAGE c IMMUTABLE STRICT
  COST 1;

-- ----------------------------
-- Function structure for vector_ne
-- ----------------------------
DROP FUNCTION IF EXISTS "public"."vector_ne"("public"."vector", "public"."vector");
CREATE OR REPLACE FUNCTION "public"."vector_ne"("public"."vector", "public"."vector")
  RETURNS "pg_catalog"."bool" AS '$libdir/vector', 'vector_ne'
  LANGUAGE c IMMUTABLE STRICT
  COST 1;

-- ----------------------------
-- Function structure for vector_negative_inner_product
-- ----------------------------
DROP FUNCTION IF EXISTS "public"."vector_negative_inner_product"("public"."vector", "public"."vector");
CREATE OR REPLACE FUNCTION "public"."vector_negative_inner_product"("public"."vector", "public"."vector")
  RETURNS "pg_catalog"."float8" AS '$libdir/vector', 'vector_negative_inner_product'
  LANGUAGE c IMMUTABLE STRICT
  COST 1;

-- ----------------------------
-- Function structure for vector_norm
-- ----------------------------
DROP FUNCTION IF EXISTS "public"."vector_norm"("public"."vector");
CREATE OR REPLACE FUNCTION "public"."vector_norm"("public"."vector")
  RETURNS "pg_catalog"."float8" AS '$libdir/vector', 'vector_norm'
  LANGUAGE c IMMUTABLE STRICT
  COST 1;

-- ----------------------------
-- Function structure for vector_out
-- ----------------------------
DROP FUNCTION IF EXISTS "public"."vector_out"("public"."vector");
CREATE OR REPLACE FUNCTION "public"."vector_out"("public"."vector")
  RETURNS "pg_catalog"."cstring" AS '$libdir/vector', 'vector_out'
  LANGUAGE c IMMUTABLE STRICT
  COST 1;

-- ----------------------------
-- Function structure for vector_recv
-- ----------------------------
DROP FUNCTION IF EXISTS "public"."vector_recv"(internal, oid, int4);
CREATE OR REPLACE FUNCTION "public"."vector_recv"(internal, oid, int4)
  RETURNS "public"."vector" AS '$libdir/vector', 'vector_recv'
  LANGUAGE c IMMUTABLE STRICT
  COST 1;

-- ----------------------------
-- Function structure for vector_send
-- ----------------------------
DROP FUNCTION IF EXISTS "public"."vector_send"("public"."vector");
CREATE OR REPLACE FUNCTION "public"."vector_send"("public"."vector")
  RETURNS "pg_catalog"."bytea" AS '$libdir/vector', 'vector_send'
  LANGUAGE c IMMUTABLE STRICT
  COST 1;

-- ----------------------------
-- Function structure for vector_spherical_distance
-- ----------------------------
DROP FUNCTION IF EXISTS "public"."vector_spherical_distance"("public"."vector", "public"."vector");
CREATE OR REPLACE FUNCTION "public"."vector_spherical_distance"("public"."vector", "public"."vector")
  RETURNS "pg_catalog"."float8" AS '$libdir/vector', 'vector_spherical_distance'
  LANGUAGE c IMMUTABLE STRICT
  COST 1;

-- ----------------------------
-- Function structure for vector_sub
-- ----------------------------
DROP FUNCTION IF EXISTS "public"."vector_sub"("public"."vector", "public"."vector");
CREATE OR REPLACE FUNCTION "public"."vector_sub"("public"."vector", "public"."vector")
  RETURNS "public"."vector" AS '$libdir/vector', 'vector_sub'
  LANGUAGE c IMMUTABLE STRICT
  COST 1;

-- ----------------------------
-- Function structure for vector_to_float4
-- ----------------------------
DROP FUNCTION IF EXISTS "public"."vector_to_float4"("public"."vector", int4, bool);
CREATE OR REPLACE FUNCTION "public"."vector_to_float4"("public"."vector", int4, bool)
  RETURNS "pg_catalog"."_float4" AS '$libdir/vector', 'vector_to_float4'
  LANGUAGE c IMMUTABLE STRICT
  COST 1;

-- ----------------------------
-- Function structure for vector_typmod_in
-- ----------------------------
DROP FUNCTION IF EXISTS "public"."vector_typmod_in"(_cstring);
CREATE OR REPLACE FUNCTION "public"."vector_typmod_in"(_cstring)
  RETURNS "pg_catalog"."int4" AS '$libdir/vector', 'vector_typmod_in'
  LANGUAGE c IMMUTABLE STRICT
  COST 1;

-- ----------------------------
-- Indexes structure for table agent_mcp_servers
-- ----------------------------
CREATE INDEX "idx_agent_mcp_servers_enabled" ON "public"."agent_mcp_servers" USING btree (
  "enabled" "pg_catalog"."bool_ops" ASC NULLS LAST
);
CREATE INDEX "idx_agent_mcp_servers_updated" ON "public"."agent_mcp_servers" USING btree (
  "updated_at" "pg_catalog"."timestamptz_ops" DESC NULLS FIRST
);

-- ----------------------------
-- Primary Key structure for table agent_mcp_servers
-- ----------------------------
ALTER TABLE "public"."agent_mcp_servers" ADD CONSTRAINT "agent_mcp_servers_pkey" PRIMARY KEY ("id");

-- ----------------------------
-- Indexes structure for table agent_memories
-- ----------------------------
CREATE INDEX "idx_agent_memories_created" ON "public"."agent_memories" USING btree (
  "created_at" "pg_catalog"."timestamptz_ops" DESC NULLS FIRST
);
CREATE INDEX "idx_agent_memories_embedding" ON "public"."agent_memories" (
  "embedding" "public"."vector_cosine_ops" ASC NULLS LAST
);
CREATE INDEX "idx_agent_memories_kind" ON "public"."agent_memories" USING btree (
  "kind" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops" ASC NULLS LAST
);
CREATE INDEX "idx_agent_memories_metadata" ON "public"."agent_memories" USING gin (
  "metadata" "pg_catalog"."jsonb_ops"
);
CREATE INDEX "idx_agent_memories_scope" ON "public"."agent_memories" USING btree (
  "scope" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops" ASC NULLS LAST
);
CREATE INDEX "idx_agent_memories_session_updated" ON "public"."agent_memories" USING btree (
  "session_id" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops" ASC NULLS LAST,
  "updated_at" "pg_catalog"."timestamptz_ops" DESC NULLS FIRST
);
CREATE INDEX "idx_agent_memories_stale" ON "public"."agent_memories" USING btree (
  "stale" "pg_catalog"."bool_ops" ASC NULLS LAST
);
CREATE INDEX "idx_agent_memories_tags" ON "public"."agent_memories" USING gin (
  "tags" COLLATE "pg_catalog"."default" "pg_catalog"."array_ops"
);

-- ----------------------------
-- Primary Key structure for table agent_memories
-- ----------------------------
ALTER TABLE "public"."agent_memories" ADD CONSTRAINT "agent_memories_pkey" PRIMARY KEY ("id");

-- ----------------------------
-- Indexes structure for table agent_perf_runs
-- ----------------------------
CREATE INDEX "idx_agent_perf_runs_plan_id" ON "public"."agent_perf_runs" USING btree (
  "plan_id" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops" ASC NULLS LAST
);
CREATE INDEX "idx_agent_perf_runs_target_url" ON "public"."agent_perf_runs" USING btree (
  "target_url" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops" ASC NULLS LAST
);

-- ----------------------------
-- Primary Key structure for table agent_perf_runs
-- ----------------------------
ALTER TABLE "public"."agent_perf_runs" ADD CONSTRAINT "agent_perf_runs_pkey" PRIMARY KEY ("run_id");

-- ----------------------------
-- Indexes structure for table agent_session_approvals
-- ----------------------------
CREATE INDEX "idx_agent_session_approvals_session_created" ON "public"."agent_session_approvals" USING btree (
  "session_id" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops" ASC NULLS LAST,
  "created_at" "pg_catalog"."timestamptz_ops" ASC NULLS LAST
);

-- ----------------------------
-- Primary Key structure for table agent_session_approvals
-- ----------------------------
ALTER TABLE "public"."agent_session_approvals" ADD CONSTRAINT "agent_session_approvals_pkey" PRIMARY KEY ("id");

-- ----------------------------
-- Indexes structure for table agent_session_events
-- ----------------------------
CREATE INDEX "idx_agent_session_events_session_timestamp" ON "public"."agent_session_events" USING btree (
  "session_id" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops" ASC NULLS LAST,
  "timestamp" "pg_catalog"."timestamptz_ops" ASC NULLS LAST
);
CREATE INDEX "idx_agent_session_events_session_timestamp_id" ON "public"."agent_session_events" USING btree (
  "session_id" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops" ASC NULLS LAST,
  "timestamp" "pg_catalog"."timestamptz_ops" ASC NULLS LAST,
  "id" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops" ASC NULLS LAST
);

-- ----------------------------
-- Primary Key structure for table agent_session_events
-- ----------------------------
ALTER TABLE "public"."agent_session_events" ADD CONSTRAINT "agent_session_events_pkey" PRIMARY KEY ("id");

-- ----------------------------
-- Indexes structure for table agent_session_messages
-- ----------------------------
CREATE INDEX "idx_agent_session_messages_session_created" ON "public"."agent_session_messages" USING btree (
  "session_id" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops" ASC NULLS LAST,
  "created_at" "pg_catalog"."timestamptz_ops" ASC NULLS LAST
);

-- ----------------------------
-- Primary Key structure for table agent_session_messages
-- ----------------------------
ALTER TABLE "public"."agent_session_messages" ADD CONSTRAINT "agent_session_messages_pkey" PRIMARY KEY ("id");

-- ----------------------------
-- Indexes structure for table agent_session_resources
-- ----------------------------
CREATE INDEX "idx_agent_session_resources_session_status" ON "public"."agent_session_resources" USING btree (
  "session_id" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops" ASC NULLS LAST,
  "status" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops" ASC NULLS LAST,
  "updated_at" "pg_catalog"."timestamptz_ops" DESC NULLS FIRST
);

-- ----------------------------
-- Uniques structure for table agent_session_resources
-- ----------------------------
ALTER TABLE "public"."agent_session_resources" ADD CONSTRAINT "agent_session_resources_session_id_kind_resource_key_key" UNIQUE ("session_id", "kind", "resource_key");

-- ----------------------------
-- Primary Key structure for table agent_session_resources
-- ----------------------------
ALTER TABLE "public"."agent_session_resources" ADD CONSTRAINT "agent_session_resources_pkey" PRIMARY KEY ("id");

-- ----------------------------
-- Indexes structure for table agent_session_snapshots
-- ----------------------------
CREATE INDEX "idx_agent_session_snapshots_session_version" ON "public"."agent_session_snapshots" USING btree (
  "session_id" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops" ASC NULLS LAST,
  "version" "pg_catalog"."int4_ops" DESC NULLS FIRST
);

-- ----------------------------
-- Uniques structure for table agent_session_snapshots
-- ----------------------------
ALTER TABLE "public"."agent_session_snapshots" ADD CONSTRAINT "agent_session_snapshots_session_id_version_key" UNIQUE ("session_id", "version");

-- ----------------------------
-- Primary Key structure for table agent_session_snapshots
-- ----------------------------
ALTER TABLE "public"."agent_session_snapshots" ADD CONSTRAINT "agent_session_snapshots_pkey" PRIMARY KEY ("id");

-- ----------------------------
-- Indexes structure for table agent_sessions
-- ----------------------------
CREATE INDEX "idx_agent_sessions_created" ON "public"."agent_sessions" USING btree (
  "created_at" "pg_catalog"."timestamptz_ops" DESC NULLS FIRST
);
CREATE INDEX "idx_agent_sessions_mode_updated" ON "public"."agent_sessions" USING btree (
  "mode_key" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops" ASC NULLS LAST,
  "updated_at" "pg_catalog"."timestamptz_ops" DESC NULLS FIRST
);
CREATE INDEX "idx_agent_sessions_status" ON "public"."agent_sessions" USING btree (
  "status" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops" ASC NULLS LAST
);
CREATE INDEX "idx_agent_sessions_updated" ON "public"."agent_sessions" USING btree (
  "updated_at" "pg_catalog"."timestamptz_ops" DESC NULLS FIRST
);

-- ----------------------------
-- Primary Key structure for table agent_sessions
-- ----------------------------
ALTER TABLE "public"."agent_sessions" ADD CONSTRAINT "agent_sessions_pkey" PRIMARY KEY ("id");

-- ----------------------------
-- Indexes structure for table agent_smoke_plan_catalog
-- ----------------------------
CREATE INDEX "idx_agent_smoke_plan_catalog_project_updated" ON "public"."agent_smoke_plan_catalog" USING btree (
  "project_scope" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops" ASC NULLS LAST,
  "updated_at" "pg_catalog"."timestamptz_ops" DESC NULLS FIRST
);

-- ----------------------------
-- Primary Key structure for table agent_smoke_plan_catalog
-- ----------------------------
ALTER TABLE "public"."agent_smoke_plan_catalog" ADD CONSTRAINT "agent_smoke_plan_catalog_pkey" PRIMARY KEY ("plan_id");

-- ----------------------------
-- Uniques structure for table agent_smoke_plan_versions
-- ----------------------------
ALTER TABLE "public"."agent_smoke_plan_versions" ADD CONSTRAINT "agent_smoke_plan_versions_plan_id_version_key" UNIQUE ("plan_id", "version");

-- ----------------------------
-- Primary Key structure for table agent_smoke_plan_versions
-- ----------------------------
ALTER TABLE "public"."agent_smoke_plan_versions" ADD CONSTRAINT "agent_smoke_plan_versions_pkey" PRIMARY KEY ("id");

-- ----------------------------
-- Primary Key structure for table agent_smoke_regression_candidates
-- ----------------------------
ALTER TABLE "public"."agent_smoke_regression_candidates" ADD CONSTRAINT "agent_smoke_regression_candidates_pkey" PRIMARY KEY ("case_id");

-- ----------------------------
-- Indexes structure for table agent_smoke_run_history
-- ----------------------------
CREATE INDEX "idx_agent_smoke_run_history_plan_started" ON "public"."agent_smoke_run_history" USING btree (
  "plan_id" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops" ASC NULLS LAST,
  "started_at" "pg_catalog"."timestamptz_ops" DESC NULLS FIRST
);

-- ----------------------------
-- Primary Key structure for table agent_smoke_run_history
-- ----------------------------
ALTER TABLE "public"."agent_smoke_run_history" ADD CONSTRAINT "agent_smoke_run_history_pkey" PRIMARY KEY ("run_id");

-- ----------------------------
-- Indexes structure for table agent_tool_artifacts
-- ----------------------------
CREATE INDEX "idx_agent_tool_artifacts_job_created" ON "public"."agent_tool_artifacts" USING btree (
  "tool_job_id" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops" ASC NULLS LAST,
  "created_at" "pg_catalog"."timestamptz_ops" ASC NULLS LAST
);
CREATE INDEX "idx_agent_tool_artifacts_session_created" ON "public"."agent_tool_artifacts" USING btree (
  "session_id" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops" ASC NULLS LAST,
  "created_at" "pg_catalog"."timestamptz_ops" ASC NULLS LAST
);

-- ----------------------------
-- Primary Key structure for table agent_tool_artifacts
-- ----------------------------
ALTER TABLE "public"."agent_tool_artifacts" ADD CONSTRAINT "agent_tool_artifacts_pkey" PRIMARY KEY ("id");

-- ----------------------------
-- Indexes structure for table agent_tool_jobs
-- ----------------------------
CREATE INDEX "idx_agent_tool_jobs_session_created" ON "public"."agent_tool_jobs" USING btree (
  "session_id" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops" ASC NULLS LAST,
  "created_at" "pg_catalog"."timestamptz_ops" DESC NULLS FIRST
);
CREATE INDEX "idx_agent_tool_jobs_status_updated" ON "public"."agent_tool_jobs" USING btree (
  "status" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops" ASC NULLS LAST,
  "updated_at" "pg_catalog"."timestamptz_ops" DESC NULLS FIRST
);

-- ----------------------------
-- Primary Key structure for table agent_tool_jobs
-- ----------------------------
ALTER TABLE "public"."agent_tool_jobs" ADD CONSTRAINT "agent_tool_jobs_pkey" PRIMARY KEY ("id");


-- ----------------------------
-- UI 操作录制（方案 ui_recording_development_plan.md v2.1 第 7 章）
-- 幂等段落：约束内联，整段可重复执行；运行时由 recording_store 以 IF NOT EXISTS 兜底
-- ----------------------------
CREATE TABLE IF NOT EXISTS "public"."ui_recording" (
  "id" text COLLATE "pg_catalog"."default" NOT NULL,
  "project_id" text COLLATE "pg_catalog"."default" NOT NULL,
  "name" text COLLATE "pg_catalog"."default" NOT NULL DEFAULT ''::text,
  "entry_url" text COLLATE "pg_catalog"."default" NOT NULL,
  "driver_kind" text COLLATE "pg_catalog"."default" NOT NULL DEFAULT 'embedded'::text,
  "status" text COLLATE "pg_catalog"."default" NOT NULL DEFAULT 'launching'::text,
  "session_id" text COLLATE "pg_catalog"."default",
  "approval_id" text COLLATE "pg_catalog"."default",
  "step_count" int4 NOT NULL DEFAULT 0,
  "created_at" timestamptz(6) NOT NULL,
  "updated_at" timestamptz(6) NOT NULL,
  "started_at" timestamptz(6),
  "ended_at" timestamptz(6),
  "finalize_metrics" jsonb NOT NULL DEFAULT '{}'::jsonb,
  "metadata" jsonb NOT NULL DEFAULT '{}'::jsonb,
  CONSTRAINT "ui_recording_pkey" PRIMARY KEY ("id")
);
CREATE INDEX IF NOT EXISTS "idx_ui_recording_project_updated" ON "public"."ui_recording" USING btree ("project_id", "updated_at" DESC);
CREATE INDEX IF NOT EXISTS "idx_ui_recording_status" ON "public"."ui_recording" USING btree ("status");

-- 事件流水：追加写、不可变；关键幂等约束 (recording_id, seq) 唯一——
-- 网络重试 / 重复批次不产生重复事件（方案 7.2 关口②）
CREATE TABLE IF NOT EXISTS "public"."ui_recording_event" (
  "id" text COLLATE "pg_catalog"."default" NOT NULL,
  "recording_id" text COLLATE "pg_catalog"."default" NOT NULL,
  "seq" int4 NOT NULL,
  "type" text COLLATE "pg_catalog"."default" NOT NULL,
  "timestamp" timestamptz(6) NOT NULL,
  "payload" jsonb NOT NULL DEFAULT '{}'::jsonb,
  "screenshot_ref" text COLLATE "pg_catalog"."default",
  CONSTRAINT "ui_recording_event_pkey" PRIMARY KEY ("id"),
  CONSTRAINT "ui_recording_event_recording_id_seq_key" UNIQUE ("recording_id", "seq")
);
