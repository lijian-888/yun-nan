-- Approved single-institution shared business data. No source rows changed.
-- Run explicitly as migration administrator, never at application startup.
BEGIN;
CREATE SCHEMA IF NOT EXISTS agent_query;
REVOKE ALL ON SCHEMA agent_query FROM PUBLIC;
CREATE TABLE IF NOT EXISTS agent_query.dataset_registry (
    dataset_id text PRIMARY KEY,
    query_view text NOT NULL UNIQUE,
    source_schema text NOT NULL,
    source_relation text NOT NULL,
    source_kind text NOT NULL,
    access_class text NOT NULL CHECK (access_class IN ('shared','admin')),
    description text NOT NULL DEFAULT '',
    excluded_columns text[] NOT NULL DEFAULT '{}'
);
REVOKE ALL ON ALL TABLES IN SCHEMA agent_query FROM PUBLIC;
DO $catalog$
DECLARE r record; projected text; hidden text[]; access_level text; view_name text; row_filter text;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='ynaas_longyun_api') THEN
        RAISE EXCEPTION 'Expected Yunnan API role is missing';
    END IF;
    FOR r IN
        SELECT c.oid,n.nspname,c.relname,c.relkind,obj_description(c.oid,'pg_class') AS description
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE c.relkind IN ('r','p','v','m') AND (
            n.nspname IN ('core','ai','governance','ingest','raw','ricedata','ncbi')
            OR (n.nspname='public' AND c.relname IN (
                'variety_basic','phenotype_observation','field_trial','breeding_material',
                'breeding_generation_record','breeding_pedigree_relationship','breeding_program',
                'breeding_program_material','breeding_selection_record','parent_recommendation_rule',
                'data_rule','data_template','template_version','trial_site','trial_entry',
                'trial_environment_metric','trial_management_event','trial_phenotype_observation',
                'trial_treatment','v_trial_material_summary')))
        ORDER BY n.nspname,c.relname
    LOOP
        view_name := r.nspname||'__'||r.relname;
        IF length(view_name)>63 THEN RAISE EXCEPTION 'View identifier too long: %',view_name; END IF;
        SELECT string_agg(format('%I',a.attname),', ' ORDER BY a.attnum)
        INTO projected
        FROM pg_attribute a WHERE a.attrelid=r.oid AND a.attnum>0 AND NOT a.attisdropped
          AND a.attname !~* '(password|passwd|secret|token|credential|authorization|api_key|private_key|(^|_)path($|_)|source_root|contact|email|phone|(^|_)owner_id$|created_by|updated_by|decided_by|confirmed_by|promoted_by|reviewed_by|approved_by|resolved_by)';
        -- Compute exclusions separately: the projection predicate above deliberately removes them.
        SELECT COALESCE(array_agg(a.attname::text ORDER BY a.attnum),'{}') INTO hidden
        FROM pg_attribute a WHERE a.attrelid=r.oid AND a.attnum>0 AND NOT a.attisdropped
          AND a.attname ~* '(password|passwd|secret|token|credential|authorization|api_key|private_key|(^|_)path($|_)|source_root|contact|email|phone|(^|_)owner_id$|created_by|updated_by|decided_by|confirmed_by|promoted_by|reviewed_by|approved_by|resolved_by)';
        IF projected IS NULL THEN RAISE EXCEPTION 'No safe columns in %.%',r.nspname,r.relname; END IF;
        access_level := CASE
            WHEN r.nspname='ingest' THEN 'admin'
            WHEN r.nspname='governance' AND r.relname NOT IN (
                'analysis_model_registry','analysis_model_parameter','breeding_target','breeding_target_trait',
                'evaluation_profile','evaluation_dimension_registry','data_dimension_registry','ai_use_case_requirement') THEN 'admin'
            WHEN r.nspname='raw' AND r.relname NOT IN ('source_record','document_content','media_asset') THEN 'admin'
            WHEN r.nspname='ai' AND r.relname ~ '(review_queue|unresolved_qc|submission|validation_issue)' THEN 'admin'
            ELSE 'shared' END;
        row_filter := '';
        -- Legacy app uploads stay limited to published, institution-wide business rows.
        IF r.nspname='public' AND r.relname='variety_basic' THEN row_filter := ' WHERE data_status=''published'''; END IF;
        IF r.nspname='public' AND r.relname='phenotype_observation' THEN row_filter := ' WHERE publish_status=''published'''; END IF;
        EXECUTE format('CREATE OR REPLACE VIEW agent_query.%I WITH (security_barrier=true) AS SELECT %s FROM %I.%I%s',
                       view_name,projected,r.nspname,r.relname,row_filter);
        EXECUTE format('REVOKE ALL ON agent_query.%I FROM PUBLIC',view_name);
        EXECUTE format('GRANT SELECT ON agent_query.%I TO ynaas_longyun_api',view_name);
        INSERT INTO agent_query.dataset_registry VALUES (
            r.nspname||'.'||r.relname,view_name,r.nspname,r.relname,r.relkind,access_level,COALESCE(r.description,''),hidden)
        ON CONFLICT(dataset_id) DO UPDATE SET access_class=EXCLUDED.access_class,
            description=EXCLUDED.description,excluded_columns=EXCLUDED.excluded_columns;
    END LOOP;
    GRANT USAGE ON SCHEMA agent_query TO ynaas_longyun_api;
    GRANT SELECT ON agent_query.dataset_registry TO ynaas_longyun_api;
END $catalog$;
COMMENT ON SCHEMA agent_query IS '云南共享业务只读查询目录；管理员级数据由应用角色再次校验；不含认证、私人会话或附件表。';
COMMIT;
