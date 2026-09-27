-- Store the complete LLM classification of a piece of content.
--
-- content already has text columns for the classified VALUES (topic, audience,
-- pain_point, hook, hook_type, format, emotion, cta) but nowhere to keep the
-- per-field confidence and evidence. This adds one nullable jsonb column:
--
--   { "<field>": { "value": text|null, "confidence": 0-1|null, "evidence": text|null }, ... }
--
-- for all eight fields. NULL means "not classified yet". The value columns are
-- still written alongside it so existing queries and the dashboard keep working.

alter table public.content
  add column if not exists classification jsonb;
