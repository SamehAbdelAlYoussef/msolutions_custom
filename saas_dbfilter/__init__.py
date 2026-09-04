import logging
from contextlib import closing

import odoo
import odoo.service.db as _dbsvc

_logger = logging.getLogger(__name__)


def _list_dbs_by_connect(force=False):
    """Core list_dbs(), but enumerating databases the current role may CONNECT
    to instead of only those it owns. Everything else matches core exactly."""
    if not odoo.tools.config['list_db'] and not force:
        raise odoo.exceptions.AccessDenied()

    if not odoo.tools.config['dbfilter'] and odoo.tools.config['db_name']:
        return sorted(odoo.tools.config['db_name'])

    chosen_template = odoo.tools.config['db_template']
    templates_list = tuple({'postgres', chosen_template})
    db = odoo.sql_db.db_connect('postgres')
    with closing(db.cursor()) as cr:
        try:
            cr.execute(
                "select datname from pg_database "
                "where has_database_privilege(current_user, datname, 'CONNECT') "
                "and not datistemplate and datallowconn and datname not in %s "
                "order by datname", (templates_list,))
            return [name for (name,) in cr.fetchall()]
        except Exception:
            _logger.exception('Listing databases failed:')
            return []


# Patch at import time (module is on server_wide_modules, so this runs at
# startup in every process, before any request is served).
_dbsvc.list_dbs = _list_dbs_by_connect
_logger.info("saas_dbfilter: list_dbs patched (enumerate by CONNECT privilege)")
