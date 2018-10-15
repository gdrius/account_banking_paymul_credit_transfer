##############################################################################
#
#    Copyright (C) 2009 EduSense BV (<http://www.edusense.nl>).
#    Copyright (C) 2011 credativ Ltd (<http://www.credativ.co.uk>).
#    Copyright (C) 2017 Giedrius Slavinskas (<giedrius@inovera.lt>).
#    All Rights Reserved
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as published
#    by the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################
import logging
import base64
from datetime import date
from decimal import Decimal
from openerp import api, models, fields, exceptions, workflow
from openerp.tools import ustr
from openerp.tools.translate import _

from . import paymul


logger = logging.getLogger('account_banking_paymul_credit_transfer')


class PaymentOrder(models.Model):
    _inherit = 'payment.order'

    @api.multi
    def launch_wizard(self):
        for payment in self:
            if not payment.line_ids:
                raise exceptions.ValidationError(_('Payment order has no '
                                                   'payment lines'))
        return super(PaymentOrder, self).launch_wizard()


class PaymentLine(models.Model):
    """The standard payment order is using a mixture of details from the
    partner record and the res.partner.bank record. For, instance, the account
    holder name is coming from the res.partner.bank record, but the company
    name and address are coming from the partner address record. This is
    problematic because the HSBC payment format is validating for alphanumeric
    characters in the company name and address. So, "Great Company Ltd." and
    "Great Company s.a." will cause an error because they have full-stops in
    the name.

    A better approach is to use the name and address details from the
    res.partner.bank record always. This way, the address details can be
    sanitized for the payments, whilst being able to print the proper name and
    address throughout the rest of the system e.g. on invoices.
    """

    _inherit = 'payment.line'

    info_owner = fields.Text(compute='_compute_info_owner')
    info_partner = fields.Text(compute='_compute_info_partner')

    @api.model
    def _get_info_partner(self, bank_account):
        if not bank_account or not bank_account.partner_id:
            return ''
        partner = bank_account.partner_id

        name = bank_account.owner_name or partner.name
        street = bank_account.street or ''
        zip_city = ' '.join(filter(None, (bank_account.zip, bank_account.city)))

        country_name = (bank_account.country_id.name if
                        bank_account.country_id else '')
        return '\n'.join((name, street, zip_city, country_name))

    @api.depends('order_id.mode')
    def _compute_info_owner(self):
        for line in self:
            line.info_owner = self._get_info_partner(
                line.order_id.mode.bank_id)

    @api.depends('bank_id')
    def _compute_info_partner(self):
        for line in self:
            line.info_partner = self._get_info_partner(line.bank_id)


class ResPartnerBank(models.Model):
    _inherit = 'res.partner.bank'

    bank_client_id = fields.Char(string='Client ID')


PAYMUL_MEANS = {
    'ACH or EZONE': paymul.MEANS_ACH_OR_EZONE,
    'Faster Payment': paymul.MEANS_FASTER_PAYMENT,
    'Priority Payment': paymul.MEANS_PRIORITY_PAYMENT,
}


class BankingExportPaymulDownloadWizard(models.TransientModel):
    _name = 'banking.export.paymul.download.wizard'

    payment_id = fields.Many2one(comodel_name='payment.order',
                                 string='Payment Order', required=True,
                                 readonly=True)
    execution_date = fields.Date(string='Execution Date', readonly=True)
    reference = fields.Char(string='Reference', size=18)
    total_amount = fields.Float(string='Total Amount', readonly=True)
    no_transactions = fields.Integer(string='Number of Transactions',
                                     readonly=True)
    payment_file = fields.Binary(string="Paymul File", required=True,
                                 readonly=True)
    payment_file_name = fields.Char(compute='_compute_payment_file_name')

    @api.depends('payment_id', 'execution_date')
    def _compute_payment_file_name(self):
        for wizard in self:
            filename = '_'.join((
                wizard.payment_id.mode.bank_id.acc_number,
                wizard.execution_date,
                wizard.reference,
            )) + '.paymul'
            wizard.payment_file_name = filename.replace(' ', '_')

    @api.multi
    def do_save(self):
        wizard = self[0]
        workflow.trg_validate(self._uid, 'payment.order', wizard.payment_id.id,
                              'done', self._cr)

        self.env['ir.attachment'].create({
            'res_model': 'payment.order',
            'res_id': wizard.payment_id.id,
            'name': self.payment_file_name,
            'datas': self.payment_file,
        })
        return True



class BankingExportPaymulWizard(models.TransientModel):
    _name = 'banking.export.paymul.wizard'
    _description = 'Paymul Export'

    execution_date = fields.Date(
        string='Execution Date',
        help=("This is the date the file should be processed by the bank. "
              "Don't choose a date beyond the nearest date in your "
              "payments. The latest allowed date is 30 days from now.\n"
              "Please keep in mind that banks only execute on working "
              "days and typically use a delay of two days between "
              "execution date and effective transfer date."))
    reference = fields.Char(string='Reference', size=18,
                            help=('The bank will use this reference in '
                                  'feedback communication to refer to this '
                                  'run. 18 characters are available.'))

    @api.model
    def default_get(self, fields_):
        values = super(BankingExportPaymulWizard, self).default_get(fields_)

        payment = self.env['payment.order'].browse(
            self._context['active_ids'][0])

        execution_date = date.today()
        if payment.date_prefered == 'fixed' and payment.date_scheduled:
            dt = fields.Date.from_string(payment.date_scheduled)
            execution_date = max(execution_date, dt)

        elif payment.date_prefered == 'due':
            dates = []
            for line in payment.line_ids:
                if line.move_line_id.date_maturity:
                    dates.append(fields.Date.from_string(
                        line.move_line_id.date_maturity))

            if dates:
                execution_date = max(execution_date, min(dates))

        ref = ''.join(c if c.isalnum() else ' ' for c in payment.reference)
        ref = ' '.join(filter(None, ref.split()))

        values.update({
            'execution_date': fields.Date.to_string(execution_date),
            'reference': ref,
        })

        return values

    @api.model
    def _create_paymul_account(self, bank_account, origin_country=None,
                               is_origin_account=False):
        def parse_bank_account(number, country_code):
            try:
                sortcode, accountno = number.split(' ', 2)
            except:
                raise exceptions.ValidationError(
                    _("Invalid %s account number '%s'") % (country_code, number))
            return sortcode, accountno

        holder = bank_account.owner_name or bank_account.partner_id.name

        logger.info('Create account %s', holder)
        logger.info('-- %s', bank_account.country_id.code)
        logger.info('-- %s', bank_account.acc_number)

        if bank_account.state == 'iban':
            logger.info('IBAN: %s %s', bank_account.country_id.code,
                        bank_account.acc_number)
            return paymul.IBANAccount(iban=bank_account.acc_number,
                                      bic=bank_account.bank_bic,
                                      holder=holder, currency=None)
        elif bank_account.country_id.code == 'GB':
            logger.info('GB: %s %s', bank_account.country_id.code,
                        bank_account.acc_number)
            sortcode, accountno = parse_bank_account(
                bank_account.acc_number, bank_account.country_id.code)
            return paymul.UKAccount(number=accountno, sortcode=sortcode,
                                    holder=holder, currency=None)
        elif bank_account.country_id.code in ('US', 'CA'):
            logger.info('US/CA: %s %s', bank_account.country_id.code,
                        bank_account.acc_number)
            sortcode, accountno = parse_bank_account(
                bank_account.acc_number, bank_account.country_id.code)

            return paymul.NorthAmericanAccount(
                number=accountno, sortcode=sortcode, holder=holder,
                currency=None, swiftcode=bank_account.bank_bic,
                country=bank_account.country_id.code,
                origin_country=origin_country,
                is_origin_account=is_origin_account)

        logger.info('SWIFT: %s %s', bank_account.country_id.code,
                    bank_account.acc_number)
        sortcode, accountno = parse_bank_account(
            bank_account.acc_number, bank_account.country_id.code)

        return paymul.SWIFTAccount(number=accountno, sortcode=sortcode,
                                   holder=holder, currency=None,
                                   swiftcode=bank_account.bank_bic,
                                   country=bank_account.country_id.code)

    @api.model
    def _get_transaction_charges(self, paymul_account):
        if isinstance(paymul_account, paymul.IBANAccount):
            return paymul.CHARGES_EACH_OWN
        return paymul.CHARGES_PAYEE

    @api.model
    def _create_transaction(self, line):
        # Check on missing partner of bank account (this can happen!)
        if not line.bank_id or not line.bank_id.partner_id:
            raise exceptions.ValidationError(
                _('There is insufficient information. Both destination '
                  'address and account number must be provided'))

        dest_account = self._create_paymul_account(
            line.bank_id, line.order_id.mode.bank_id.country_id.code)

        payment_type = line.order_id.mode.type.name
        means = PAYMUL_MEANS.get(payment_type)

        if means is None:
            raise exceptions.ValidationError(
                _("Invalid payment type mode for HSBC '%s'") % payment_type)

        address = self.env['payment.line']._get_info_partner(line.bank_id)
        if not address:
            raise exceptions.ValidationError(
                _("No default address for transaction '%s'") % line.name)

        charges = self._get_transaction_charges(dest_account)
        return paymul.Transaction(amount=Decimal(str(line.amount_currency)),
                                  currency=line.currency.name,
                                  account=dest_account, means=means,
                                  name_address=address,
                                  customer_reference=line.name,
                                  payment_reference=line.name, charges=charges)

    @api.multi
    def do_export(self):
        wizard = self[0]
        payment = self.env['payment.order'].browse(
            self._context['active_ids'][0])

        bank_account = payment.mode.bank_id
        logger.info('Source - %s (%s) %s', bank_account.partner_id.name,
                    bank_account.acc_number, bank_account.country_id.code)
        src_account = self._create_paymul_account(bank_account,
                                                  bank_account.country_id.code,
                                                  is_origin_account=True)
        if not isinstance(src_account, paymul.UKAccount):
            raise exceptions.ValidationError(
                _("Your company's bank account has to have a valid UK "
                  "account number (not IBAN): %s") % ustr(type(src_account)))

        logger.info('Create transactions...')
        transactions = map(self._create_transaction, payment.bank_line_ids)

        batch = paymul.Batch(
            exec_date=fields.Date.from_string(wizard.execution_date),
            reference=wizard.reference,
            debit_account=src_account,
            name_address=self.env['payment.line']._get_info_partner(
                bank_account))
        batch.transactions = transactions

        ref = self.env['ir.sequence'].next_by_code('bank.paymul.identifier')
        message = paymul.Message(reference=ref)
        message.batches.append(batch)
        interchange = paymul.Interchange(client_id=bank_account.bank_client_id,
                                         reference=ref,
                                         message=message)

        wizard = self.env['banking.export.paymul.download.wizard'].create({
            'reference': wizard.reference,
            'payment_file': base64.b64encode(str(interchange)),
            'no_transactions': len(batch.transactions),
            'payment_id': payment.id,
            'total_amount': batch.amount(),
            'execution_date': batch.exec_date,
        })

        return {
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'banking.export.paymul.download.wizard',
            'domain': [],
            'context': dict(self._context),
            'type': 'ir.actions.act_window',
            'target': 'new',
            'res_id': wizard.id,
        }
