-- Let the dashboard read interpretations.
--
-- The dashboard reads with the publishable key (roles anon/authenticated) and
-- every table it shows has a SELECT policy of exactly this shape
-- ("dashboard can read content", "... comments", ...). interpretations has RLS
-- enabled with no policy, so the dashboard currently sees none of them (it
-- degrades to "not classified"). This adds the same read-only policy; it does
-- not change RLS on any other table and grants no write access.
--
-- Note this exposes interpretation results to anyone holding the publishable
-- key, exactly as content and comments already are.

create policy "dashboard can read interpretations"
  on public.interpretations
  for select
  to anon, authenticated
  using (true);
