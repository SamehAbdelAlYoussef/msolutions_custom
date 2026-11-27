{
    'name': 'AI Lead Gen',
    'version': '19.0.1.1.0',
    'category': 'Sales/CRM',
    'summary': 'Run the AI lead generation engine from CRM and review what it found',
    'description': """
Runs the external AI Lead Gen Engine (scrapers, Gemini qualification, contact enrichment) from the CRM
menu, shows each run with its results, and stores the AI score and the ready-to-send WhatsApp pitch on
the leads it creates.
""",
    'depends': ['crm'],
    'data': [
        'security/ir.model.access.csv',
        'data/ai_leadgen_session_data.xml',
        'data/crm_stage_data.xml',
        'data/ir_actions_server_data.xml',  # before the menus that point at it
        'data/ai_leadgen_ops_data.xml',  # before the menus that point at its action
        'views/ai_leadgen_session_views.xml',
        'views/ai_leadgen_run_views.xml',
        'views/ai_leadgen_ops_views.xml',
        'views/leadgen_source_views.xml',  # before the settings view that links to its action
        'views/crm_lead_views.xml',
        'views/res_config_settings_views.xml',
        'views/ai_leadgen_menus.xml',
        'data/ir_cron_data.xml',
    ],
    'license': 'LGPL-3',
}
