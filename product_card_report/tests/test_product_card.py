import base64

from odoo.tests.common import TransactionCase, tagged

# 1x1 transparent PNG
PNG = base64.b64encode(
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89'
    b'\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x9a\xa0\xa0\x00\x00\x00\x00IEND\xaeB`\x82')


@tagged('post_install', '-at_install')
class TestProductCard(TransactionCase):

    def setUp(self):
        super().setUp()
        self.tmpl = self.env['product.template'].create({
            'name': 'Oak Dining Chair', 'default_code': 'CH-OAK-01',
            'description_sale': 'Solid oak, hand finished.', 'image_1920': PNG})

    def _html(self, xmlid, model, ids):
        report = self.env.ref(xmlid)
        html, _fmt = self.env['ir.actions.report']._render_qweb_html(report.report_name, ids)
        return html.decode()

    def test_card_contains_the_four_fields(self):
        html = self._html('product_card_report.action_report_product_card_template',
                          'product.template', self.tmpl.ids)
        self.assertIn('Oak Dining Chair', html)
        self.assertIn('CH-OAK-01', html)
        self.assertIn('Solid oak, hand finished.', html)
        self.assertIn('data:image/', html)          # the product image

    def test_variant_report_works(self):
        html = self._html('product_card_report.action_report_product_card_variant',
                          'product.product', self.tmpl.product_variant_id.ids)
        self.assertIn('CH-OAK-01', html)

    def test_one_page_per_product_is_a4(self):
        other = self.env['product.template'].create({'name': 'Second', 'default_code': 'S-2'})
        html = self._html('product_card_report.action_report_product_card_template',
                          'product.template', (self.tmpl | other).ids)
        self.assertEqual(html.count('page-break-after: always'), 1)   # between the two, none after the last
        report = self.env.ref('product_card_report.action_report_product_card_template')
        self.assertEqual(report.paperformat_id.format, 'A4')

    def test_pdf_renders(self):
        report = self.env.ref('product_card_report.action_report_product_card_template')
        pdf, _ = self.env['ir.actions.report'].with_context(force_report_rendering=True)._render_qweb_pdf(report, self.tmpl.ids)
        self.assertTrue(pdf.startswith(b'%PDF'))
