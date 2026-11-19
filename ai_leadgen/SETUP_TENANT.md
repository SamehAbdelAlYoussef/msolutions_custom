# AI Lead Gen: installing it on ONE tenant (this module is global, the tenants are not)

The module lives with the other addons in `msolutions_custom`, so every tenant *can* see it. It is installed **only** where an operator runs the
steps below. A tenant created with **Hide Apps** cannot install or remove apps from its own Apps screen, so this is an operator job.

## What the module is, and what it is not
It adds CRM > **AI Lead Gen** (Operations, Sources, Candidates, Runs, Target Groups, Settings) and some fields on `crm.lead`
(AI score, intent category, contact verification, publication date). It depends only on `crm` (installing it installs CRM in that tenant).
**The engine is not part of this module.** The engine (Python, a browser for Facebook, its own state) is a separate private project.
Without an engine on the server every engine-facing job in the module stays silent: you can use the screens and the CRM fields, and leads
arrive from an engine somewhere else through the normal Odoo API (see step 5).

## Steps (after `main` has been deployed by the GitHub workflow)
1. **Install on the tenant only** (the worker container has `--no-http` and `workers = 0`, so it is the safe place for a one-off job):
   ```bash
   docker exec odoo_worker odoo -d sameh -i ai_leadgen --stop-after-init --no-http
   ```
2. **Seed the Facebook groups** (idempotent; this data is deliberately not module data):
   ```bash
   docker exec -i odoo_worker odoo shell -d sameh --no-http <<'PY'
   print(env['leadgen.source']._seed_default_groups(), "groups created")
   env.cr.commit()
   PY
   ```
3. **Restart the worker** so its registry knows the new crons (`deploy/README.md`, trap 3):
   ```bash
   docker restart odoo_worker
   ```
4. In the tenant: **CRM > AI Lead Gen > Settings**: pick the AI provider (Gemini or DeepSeek) and paste its key. Keys are stored per database.
5. **Lead delivery from an engine:** in the tenant open *Preferences > Account Security > New API Key*, then give the engine
   `ODOO_URL=https://sameh.msolutions-eg.com`, `ODOO_DB=sameh`, `ODOO_USER=<that user's login>`, `ODOO_API_KEY=<the key>` in its `.env`.

## Check
`docker logs odoo_worker --since 10m | grep -i ai_leadgen` should show no error. Other tenants are untouched: `select datname from pg_database` does not change and
no other database gets the module unless an operator installs it there.

## Roll back
```bash
docker exec -i odoo_worker odoo shell -d sameh --no-http <<'PY'
env['ir.module.module'].search([('name', '=', 'ai_leadgen')]).button_immediate_uninstall()
env.cr.commit()
PY
```
then revert the merge on `main` (the workflow restarts the containers again). Uninstalling removes only this module's data in that tenant.
