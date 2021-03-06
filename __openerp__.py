# -*- encoding: utf-8 -*-
##############################################################################
#
#    Copyright (C) 2017 Giedrius Slavinskas (<giedrius@inovera.lt>).
#    All Rights Reserved
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
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
{
    'name': 'Account Banking PAYMUL Credit Transfer',
    'summary': 'Create HSBC PAYMUL files for Credit Transfers',
    'version': '8.0.0.1.0',
    'license': 'AGPL-3',
    'author': ("Giedrius Slavinskas, "
               "credativ Ltd, "
               "Odoo Community Association (OCA)"),
    'website': 'https://github.com/OCA/bank-payment',
    'category': 'Banking addons',
    'depends': ['account_payment'],
    'data': [
        'data/banking_export_paymul.xml',
        'views.xml',
    ],
    'description': '',
    'installable': True,
}
