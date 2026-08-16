import base64
import io

from odoo import models, fields, api, _

try:
    import xlsxwriter
except ImportError:
    xlsxwriter = None


class SalesVisitKpiWizard(models.TransientModel):
    _name = 'sales.visit.kpi.wizard'
    _description = 'Sales Visit Plan KPI Report'

    date_from = fields.Date(string='Date From')
    date_to = fields.Date(string='Date To')
    user_ids = fields.Many2many(
        'res.users',
        string='Salespersons',
        help='Leave empty to include all salespersons.',
    )
    file_data = fields.Binary(string='File', readonly=True)
    file_name = fields.Char(string='File Name', readonly=True)

    # ------------------------------------------------------------
    # Data aggregation
    # ------------------------------------------------------------
    def _get_plans(self):
        """Return the sales.plan records matching the wizard filters."""
        domain = []
        if self.date_from:
            domain.append(('date_start', '>=', self.date_from))
        if self.date_to:
            domain.append(('date_start', '<=', self.date_to))
        if self.user_ids:
            domain.append(('user_id', 'in', self.user_ids.ids))
        return self.env['sales.plan'].search(domain)

    def _build_kpi_rows(self, plans):
        """Aggregate KPIs per salesperson. Returns an ordered list of dicts."""
        stats = {}

        def _blank(user):
            return {
                'user': user,
                'plans': 0,
                'new': 0,
                'to_approve': 0,
                'approved': 0,
                'lines': 0,
                'doctor': 0,
                'pharmacy': 0,
                'meeting': 0,
                'first_visit': 0,
                'repeat_visit': 0,
                'completed': 0,
                'act_open': 0,
                'act_overdue': 0,
            }

        for plan in plans:
            user = plan.user_id
            row = stats.setdefault(user.id, _blank(user))
            row['plans'] += 1
            if plan.state in ('new', 'to_approve', 'approved'):
                row[plan.state] += 1

        lines = plans.mapped('line_ids')
        for line in lines:
            user = line.plan_id.user_id
            row = stats.setdefault(user.id, _blank(user))
            row['lines'] += 1
            if line.visit_type in ('doctor', 'pharmacy', 'meeting'):
                row[line.visit_type] += 1
            if line.visit_stage in ('first_visit', 'repeat_visit', 'completed'):
                row[line.visit_stage] += 1

        # ---- Open / overdue activities per salesperson ----------------
        today = fields.Date.today()
        plan_by_id = {p.id: p for p in plans}
        line_plan = {l.id: l.plan_id for l in lines}
        activities = self.env['mail.activity'].search([
            '|',
            '&', ('res_model', '=', 'sales.plan'),
                 ('res_id', 'in', list(plan_by_id)),
            '&', ('res_model', '=', 'sales.plan.line'),
                 ('res_id', 'in', list(line_plan)),
        ])
        for act in activities:
            if act.res_model == 'sales.plan':
                plan = plan_by_id.get(act.res_id)
            else:
                plan = line_plan.get(act.res_id)
            if not plan:
                continue
            salesperson = plan.user_id
            # Count only tasks assigned to the salesperson (not manager approvals)
            if act.user_id != salesperson:
                continue
            row = stats.setdefault(salesperson.id, _blank(salesperson))
            row['act_open'] += 1
            if act.date_deadline and act.date_deadline < today:
                row['act_overdue'] += 1

        # Sort by number of plans desc, then salesperson name
        return sorted(
            stats.values(),
            key=lambda r: (-r['plans'], r['user'].name or ''),
        )

    # ------------------------------------------------------------
    # XLSX generation
    # ------------------------------------------------------------
    def action_generate_xlsx(self):
        self.ensure_one()
        if xlsxwriter is None:
            from odoo.exceptions import UserError
            raise UserError(_(
                'The xlsxwriter library is not installed on the server. '
                'Install it with: pip install XlsxWriter'
            ))

        plans = self._get_plans()
        kpi_rows = self._build_kpi_rows(plans)

        output = io.BytesIO()
        book = xlsxwriter.Workbook(output, {'in_memory': True})

        # ---- Formats -------------------------------------------------
        title_fmt = book.add_format({
            'bold': True, 'font_size': 16, 'font_color': '#FFFFFF',
            'bg_color': '#0CA678', 'align': 'center', 'valign': 'vcenter',
        })
        sub_fmt = book.add_format({
            'font_size': 10, 'italic': True, 'align': 'center',
            'font_color': '#495057',
        })
        head_fmt = book.add_format({
            'bold': True, 'font_color': '#FFFFFF', 'bg_color': '#087F5B',
            'align': 'center', 'valign': 'vcenter', 'border': 1,
            'text_wrap': True,
        })
        text_fmt = book.add_format({'border': 1, 'align': 'left'})
        num_fmt = book.add_format({'border': 1, 'align': 'center'})
        total_txt = book.add_format({
            'bold': True, 'border': 1, 'align': 'left', 'bg_color': '#E6FCF5',
        })
        total_num = book.add_format({
            'bold': True, 'border': 1, 'align': 'center', 'bg_color': '#E6FCF5',
        })
        date_fmt = book.add_format({'border': 1, 'align': 'center',
                                    'num_format': 'yyyy-mm-dd'})
        overdue_fmt = book.add_format({
            'border': 1, 'align': 'center', 'bold': True,
            'font_color': '#FFFFFF', 'bg_color': '#E03131',
        })

        # ============================================================
        #  Sheet 1 — KPIs per salesperson
        # ============================================================
        ws = book.add_worksheet(_('Salesperson KPIs'))

        headers = [
            _('Salesperson'), _('Plans'), _('New'), _('Waiting Approval'),
            _('Approved'), _('Total Visits'), _('Doctors'), _('Pharmacies'),
            _('Meetings'), _('First Visit'), _('Repeat Visit'), _('Completed'),
            _('Open Activities'), _('Overdue Activities'),
        ]
        keys = ['plans', 'new', 'to_approve', 'approved', 'lines',
                'doctor', 'pharmacy', 'meeting', 'first_visit',
                'repeat_visit', 'completed', 'act_open', 'act_overdue']

        ws.merge_range(0, 0, 0, len(headers) - 1,
                       _('Sales Visit Plans — KPI Report'), title_fmt)
        ws.set_row(0, 28)
        period = _('Period: %(f)s → %(t)s',
                   f=self.date_from or _('Start'),
                   t=self.date_to or _('End'))
        ws.merge_range(1, 0, 1, len(headers) - 1, period, sub_fmt)

        hrow = 3
        for col, title in enumerate(headers):
            ws.write(hrow, col, title, head_fmt)
        ws.set_column(0, 0, 26)
        ws.set_column(1, len(headers) - 1, 12)

        totals = dict.fromkeys(keys, 0)
        r = hrow + 1
        for row in kpi_rows:
            ws.write(r, 0, row['user'].name or _('Undefined'), text_fmt)
            for i, key in enumerate(keys, start=1):
                cell_fmt = num_fmt
                if key == 'act_overdue' and row[key] > 0:
                    cell_fmt = overdue_fmt
                ws.write_number(r, i, row[key], cell_fmt)
                totals[key] += row[key]
            r += 1

        # Totals row
        ws.write(r, 0, _('Total'), total_txt)
        for i, key in enumerate(keys, start=1):
            ws.write_number(r, i, totals[key], total_num)

        ws.freeze_panes(hrow + 1, 1)

        # ============================================================
        #  Sheet 2 — Plans detail
        # ============================================================
        ws2 = book.add_worksheet(_('Plans Detail'))

        d_headers = [
            _('Plan Name'), _('Salesperson'), _('Manager'), _('Region'),
            _('Date From'), _('Date To'), _('Duration (Days)'),
            _('Visits'), _('Status'),
        ]
        state_label = {
            'new': _('New'),
            'to_approve': _('Waiting Approval'),
            'approved': _('Approved'),
        }
        ws2.merge_range(0, 0, 0, len(d_headers) - 1,
                        _('All Visit Plans'), title_fmt)
        ws2.set_row(0, 28)
        for col, title in enumerate(d_headers):
            ws2.write(2, col, title, head_fmt)
        ws2.set_column(0, 0, 30)
        ws2.set_column(1, 2, 22)
        ws2.set_column(3, 3, 22)
        ws2.set_column(4, len(d_headers) - 1, 13)

        r = 3
        for plan in plans.sorted(key=lambda p: (p.user_id.name or '',
                                                p.date_start or fields.Date.today())):
            ws2.write(r, 0, plan.name or '', text_fmt)
            ws2.write(r, 1, plan.user_id.name or '', text_fmt)
            ws2.write(r, 2, plan.manager_id.name or '', text_fmt)
            ws2.write(r, 3, plan.region or '', text_fmt)
            if plan.date_start:
                ws2.write_datetime(r, 4, fields.Datetime.to_datetime(plan.date_start), date_fmt)
            else:
                ws2.write(r, 4, '', num_fmt)
            if plan.date_end:
                ws2.write_datetime(r, 5, fields.Datetime.to_datetime(plan.date_end), date_fmt)
            else:
                ws2.write(r, 5, '', num_fmt)
            ws2.write_number(r, 6, plan.duration_days or 0, num_fmt)
            ws2.write_number(r, 7, plan.visit_count or 0, num_fmt)
            ws2.write(r, 8, state_label.get(plan.state, plan.state or ''), num_fmt)
            r += 1

        ws2.freeze_panes(3, 0)

        book.close()
        output.seek(0)

        self.file_data = base64.b64encode(output.read())
        self.file_name = _('KPIs_Sales_Visit_Plans.xlsx')

        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/sales.visit.kpi.wizard/%s/file_data/%s?download=true'
                   % (self.id, self.file_name),
            'target': 'self',
        }
