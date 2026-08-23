from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestSalesRequisition(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Users = cls.env['res.users'].with_context(no_reset_password=True)
        cls.requester = Users.create({
            'name': 'Rep One', 'login': 'req_rep',
            'email': 'rep@example.com',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])],
        })
        cls.approver1 = Users.create({
            'name': 'Approver One', 'login': 'req_app1',
            'email': 'app1@example.com',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])],
        })
        cls.approver2 = Users.create({
            'name': 'Approver Two', 'login': 'req_app2',
            'email': 'app2@example.com',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])],
        })
        cls.manager = Users.create({
            'name': 'Sales Manager', 'login': 'req_mgr',
            'email': 'mgr@example.com',
            'group_ids': [(6, 0, [
                cls.env.ref('sales_visit_plan.group_sales_manager').id,
            ])],
        })
        cls.product = cls.env['product.product'].create({
            'name': 'Gift Pen', 'type': 'consu',
        })

    def _new_requisition(self, chain=None):
        """Create a requisition owned by the requester, with an explicit
        two-step chain unless *chain* is given (use [] to keep defaults)."""
        if chain is None:
            chain = [
                (0, 0, {'sequence': 10, 'name': 'Step 1',
                        'approver_id': self.approver1.id}),
                (0, 0, {'sequence': 20, 'name': 'Step 2',
                        'approver_id': self.approver2.id}),
            ]
        vals = {
            'doctor_name': 'Dr. Test',
            'line_ids': [(0, 0, {'product_id': self.product.id, 'quantity': 3})],
        }
        if chain:
            vals['approval_line_ids'] = chain
        return self.env['sales.requisition'].with_user(self.requester).create(vals)

    def test_sequence_and_default_chain(self):
        """Reference is auto-numbered and the default chain is copied from
        the global stages when none is supplied."""
        req = self._new_requisition(chain=[])
        self.assertTrue(req.name.startswith('REQ/'))
        # 3 default stages ship as data
        self.assertEqual(len(req.approval_line_ids), 3)

    def test_full_approval_flow(self):
        req = self._new_requisition()
        req.with_user(self.requester).action_submit()
        self.assertEqual(req.state, 'to_approve')
        step1, step2 = req.approval_line_ids.sorted('sequence')
        self.assertEqual(step1.state, 'to_approve')
        self.assertEqual(step2.state, 'pending')
        self.assertEqual(req.current_approval_id, step1)

        # Approver 1 approves → step 2 becomes active
        step1.with_user(self.approver1).action_approve()
        self.assertEqual(step1.state, 'approved')
        self.assertEqual(step2.state, 'to_approve')
        self.assertEqual(req.state, 'to_approve')

        # Approver 2 approves → requisition fully approved + requester notified
        mails_before = self.env['mail.mail'].search_count([])
        step2.with_user(self.approver2).action_approve()
        self.assertEqual(req.state, 'approved')
        mails_after = self.env['mail.mail'].search_count([])
        self.assertGreater(mails_after, mails_before,
                           'A notification email should be queued on approval.')

    def test_wrong_approver_blocked(self):
        """Approver 2 cannot approve step 1 (not their step)."""
        req = self._new_requisition()
        req.with_user(self.requester).action_submit()
        step1 = req.approval_line_ids.sorted('sequence')[0]
        with self.assertRaises(UserError):
            step1.with_user(self.approver2).action_approve()

    def test_sequential_enforced(self):
        """Step 2's approver cannot jump ahead while step 2 is pending."""
        req = self._new_requisition()
        req.with_user(self.requester).action_submit()
        step2 = req.approval_line_ids.sorted('sequence')[1]
        with self.assertRaises(UserError):
            step2.with_user(self.approver2).action_approve()

    def test_rejection(self):
        req = self._new_requisition()
        req.with_user(self.requester).action_submit()
        step1 = req.approval_line_ids.sorted('sequence')[0]
        step1.with_user(self.approver1).action_reject()
        self.assertEqual(req.state, 'rejected')
        self.assertEqual(step1.state, 'rejected')

    def test_manager_cannot_approve_others(self):
        """Strict mode: even a Sales Manager cannot approve a step they are
        not the assigned approver of."""
        req = self._new_requisition()
        req.with_user(self.requester).action_submit()
        step1 = req.approval_line_ids.sorted('sequence')[0]
        with self.assertRaises(UserError):
            step1.with_user(self.manager).action_approve()

    def test_plan_chatter_documentation(self):
        """Every notification is archived on the Related Visit Plan chatter."""
        plan = self.env['sales.plan'].with_user(self.requester).create({
            'name': 'Cairo Plan', 'user_id': self.requester.id,
        })
        req = self._new_requisition()
        req.plan_id = plan.id
        req.with_user(self.requester).action_submit()
        step1, step2 = req.approval_line_ids.sorted('sequence')
        step1.with_user(self.approver1).action_approve()
        step2.with_user(self.approver2).action_approve()
        self.assertEqual(req.state, 'approved')
        # 3 notifications (approver1, approver2, requester) → 3 plan messages
        bodies = plan.message_ids.mapped('body')
        joined = '\n'.join(b or '' for b in bodies)
        self.assertIn('Fully Approved', joined,
                      'Final approval must be documented on the plan chatter.')
        self.assertIn('app1@example.com', joined,
                      "Approver 1's notification must be archived on the plan.")

    def test_submit_requires_products(self):
        req = self.env['sales.requisition'].with_user(self.requester).create({
            'doctor_name': 'Dr. Empty',
            'approval_line_ids': [(0, 0, {
                'sequence': 10, 'name': 'Step 1',
                'approver_id': self.approver1.id})],
        })
        with self.assertRaises(UserError):
            req.with_user(self.requester).action_submit()

    def test_reset_to_draft(self):
        req = self._new_requisition()
        req.with_user(self.requester).action_submit()
        req.with_user(self.manager).action_reset_to_draft()
        self.assertEqual(req.state, 'draft')
        self.assertTrue(all(
            s.state == 'pending' for s in req.approval_line_ids))
