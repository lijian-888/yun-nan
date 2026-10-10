-- Retire only the unused root-phenotype feature, after a verified DB backup.
-- This deployment's dedicated table and related intake records are empty.
-- Abort on unexpected data/dependencies instead of deleting shared records.
BEGIN;
SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '60s';
LOCK TABLE public.data_template, public.template_version, public.source_review,
    public.field_change_request, public.phenotype_observation IN SHARE ROW EXCLUSIVE MODE;

DO $$
BEGIN
    IF to_regclass('public.root_phenotype_observation') IS NOT NULL THEN
        LOCK TABLE public.root_phenotype_observation IN ACCESS EXCLUSIVE MODE;
        IF EXISTS (SELECT 1 FROM public.root_phenotype_observation) THEN
            RAISE EXCEPTION 'Unexpected root observations: inspect and back up before retirement';
        END IF;
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.data_template
        WHERE target_table = 'root_phenotype_observation'
          AND template_code <> 'rice_root_phenotype'
    ) THEN
        RAISE EXCEPTION 'Unexpected template targets the retired table; inspect before retirement';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.source_review s
        JOIN public.template_version v ON v.id = s.template_version_id
        JOIN public.data_template t ON t.id = v.template_id
        WHERE t.template_code = 'rice_root_phenotype'
    ) OR EXISTS (
        SELECT 1 FROM public.field_change_request f
        JOIN public.data_template t ON t.id = f.template_id
        WHERE t.template_code = 'rice_root_phenotype'
    ) OR EXISTS (
        SELECT 1 FROM public.phenotype_observation
        WHERE trait_code IN ('root_length', 'total_root_length', 'root_count',
            'root_surface_area', 'root_volume', 'average_root_diameter',
            'root_tip_count', 'root_dry_weight', 'root_angle', 'root_shoot_ratio')
    ) THEN
        RAISE EXCEPTION 'Unexpected root intake/draft data: inspect before retirement';
    END IF;
END $$;

UPDATE public.data_template SET current_version_id = NULL
WHERE template_code = 'rice_root_phenotype';
DELETE FROM public.template_version
WHERE template_id IN (SELECT id FROM public.data_template WHERE template_code = 'rice_root_phenotype');
DELETE FROM public.data_template WHERE template_code = 'rice_root_phenotype';
-- Deliberately RESTRICT: never cascade into shared tables, views or functions.
DROP TABLE IF EXISTS public.root_phenotype_observation RESTRICT;
COMMIT;
