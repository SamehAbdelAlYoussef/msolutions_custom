# -*- coding: utf-8 -*-
# msolutions - Community-compatible accounting distribution.
"""
Base bank-statement import hooks on ``account.journal``.

The CSV / OFX / QIF importer modules (kept in the ported tree) each
``_inherit = 'account.journal'`` and call ``super()`` on these four methods.
Community ``account`` does not define them, so the shim provides the base
implementation and raises a clear ``UserError`` for unsupported formats.
"""

from odoo import _, models
from odoo.exceptions import UserError


class AccountJournal(models.Model):
    _inherit = "account.journal"

    def _get_bank_statements_available_import_formats(self):
        """Return the list of file formats this journal can import."""
        return []

    def _check_file_format(self, filename):
        """True when *filename* matches a format supported by this journal."""
        return False

    def _parse_bank_statement_file(self, raw_file):
        """Parse *raw_file* into bank statement values.

        Base implementation rejects the file: concrete importers override this
        and fall back to ``super()`` only for formats they do not handle.
        """
        raise UserError(_(
            "This file format is not supported by any installed bank statement "
            "importer."
        ))

    def _import_bank_statement(self, attachments):
        """Import the given attachments. Base implementation is unsupported."""
        raise UserError(_(
            "Bank statement import is not available for this file format."
        ))
