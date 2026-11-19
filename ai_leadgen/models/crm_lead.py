from odoo import fields, models


class CrmLead(models.Model):
    _inherit = 'crm.lead'

    ai_score = fields.Integer(string="AI Score", copy=False, help="Fit with our services, from 1 to 10.")
    ai_whatsapp_pitch = fields.Text(string="WhatsApp Pitch", copy=False)
    ai_intent_category = fields.Selection(
        [
            ('odoo_buyer', "Explicit Odoo Buyer"),
            ('erp_buyer', "ERP Buyer"),
            ('software_dev_buyer', "Software Development Buyer"),
            ('ai_automation_buyer', "AI Automation Buyer"),
            ('maintenance_customization_buyer', "Maintenance / Customization Buyer"),
            ('unqualified', "Unqualified Candidate"),
        ],
        string="Intent Category", copy=False, index=True,
    )
    ai_intent_level = fields.Selection(
        [('explicit_buyer', "Explicit buyer"), ('strong_intent', "Strong purchase intent"), ('general_prospect', "General prospect"),
         ('unqualified', "Unqualified"), ('advertisement', "Advertisement / spam")],
        string="Intent Level", copy=False)
    ai_contact_status = fields.Char(
        string="Contact Verification", copy=False,
        help="What was actually checked. A valid phone number is not a confirmed WhatsApp account.")
    ai_source_published = fields.Datetime(string="Request Published", copy=False, help="When the source published the request (UTC).")
