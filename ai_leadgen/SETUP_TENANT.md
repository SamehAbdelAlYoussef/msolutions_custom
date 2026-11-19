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
The server's containers connect to PostgreSQL with different roles (`odoo_web` for the web containers, `odoo_provision` for `odoo_worker`),
so tables created by an install are owned by `odoo_provision` and the web role needs a GRANT. The workflow below does that too.
1. **Install or upgrade on the tenant only:** GitHub > Actions > *Install a module on ONE tenant* > Run workflow (tenant `sameh`, module `ai_leadgen`,
   tick *upgrade* after a code change). It installs/upgrades in that one database, seeds the default Facebook groups (idempotent), grants the web
   role access to the module's tables, restarts `odoo_worker` (new crons only fire after that) and prints a check. It refuses the control database.
2. **Engine:** *Deploy the AI Lead Gen engine* workflow (tenant `sameh`) builds one resource-limited container `leadgen_engine` from the private
   engine repo. Inside it, every 5 minutes: the Target Groups chosen in Odoo are pulled, one Facebook poll tick runs (only if a saved session exists)
   and the operations snapshot is reported to Odoo; every hour: Telegram + maps. The engine uses an Odoo API key generated on the server
   and never printed. *Run a fixed check inside the engine container* gives `check-odoo`, `preflight`, `check-ai`, `stats`, `telegram-status`, `run-now`, `logs`.
3. In the tenant: **CRM > AI Lead Gen > Settings** shows the AI provider. The engine reads its keys from its own settings file on the server.

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
