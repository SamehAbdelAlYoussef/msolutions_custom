from odoo import fields, models


class AiLeadgenRunLine(models.Model):
    _name = 'ai.leadgen.run.line'
    _description = "AI Lead Gen Run Result"
    _order = 'id'

    run_id = fields.Many2one('ai.leadgen.run', required=True, index=True, ondelete='cascade')
    source = fields.Char()
    title = fields.Char()
    score = fields.Integer()
    outcome = fields.Selection(
        [
            ('pushed', "Pushed to CRM"),
            ('discarded', "Discarded"),
            ('deferred', "Retry next run"),
            ('error', "Error"),
            ('already_in_odoo', "Already in CRM"),
        ],
        required=True,
    )
    reason = fields.Char()
    contacts = fields.Char(help="Kinds of verified contact points found.")
    lead_id = fields.Many2one('crm.lead', ondelete='set null')
