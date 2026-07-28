{
    'name': 'Sales Visit Plan',
    'version': '19.0.1.0.0',
    'category': 'Sales/Sales',
    'summary': 'Manage field visit plans for pharmaceutical sales representatives',
    'description': """
        Sales Visit Plan Management Module
        ===================================
        This module enables pharmaceutical sales teams to:

        - Create structured visit plans with Kanban workflow (New → Approval → Approved).
        - Manage visit details (first visits, repeat visits) per doctor/pharmacy.
        - Track gifts given during visits using Odoo products.
        - Send automatic email notifications to managers for approval.
        - View nested Kanban boards: a master Kanban for plan status,
          and an embedded Kanban inside each plan for visit stages.
        - Convert first visits to repeat visits dynamically.

        Key Features:
        - Two-level Kanban visualization.
        - Role-based approvals (Salesperson vs. Sales Manager).
        - Automatic duration calculation between start and end dates.
        - Activity scheduling for managers upon approval requests.
        - Full mail tracking and chatter integration.
        - Copy-ready for distribution with safe defaults.
    """,
    'author': 'Msoulatioons',
    'depends': ['base', 'mail', 'product', 'crm'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/email_templates.xml',
        'views/sales_plan_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'sales_visit_plan/static/src/js/get_location.js',
        ],
    },
    'demo': [
        'demo/demo_data.xml',
    ],
    'installable': True,
    'auto_install': False,
    'application': True,
    'license': 'LGPL-3',
}
