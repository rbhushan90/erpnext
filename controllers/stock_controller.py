# Copyright (c) 2013, Web Notes Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

from __future__ import unicode_literals
import webnotes
from webnotes.utils import cint, flt, cstr
from webnotes import msgprint, _
import webnotes.defaults

from controllers.accounts_controller import AccountsController
from accounts.general_ledger import make_gl_entries, delete_gl_entries

class StockController(AccountsController):
	def make_gl_entries(self, repost_future_gle=True):
		if self.doc.docstatus == 2:
			delete_gl_entries(voucher_type=self.doc.doctype, voucher_no=self.doc.name)
			
		if cint(webnotes.defaults.get_global_default("auto_accounting_for_stock")):
			warehouse_account = get_warehouse_account()
		
			if self.doc.docstatus==1:
				gl_entries = self.get_gl_entries(warehouse_account)
				make_gl_entries(gl_entries)

			if repost_future_gle:
				items, warehouse_account = self.get_items_and_warehouse_accounts(warehouse_account)
				update_gl_entries_after(self.doc.posting_date, self.doc.posting_time, 
					warehouse_account, items)
	
	def get_gl_entries(self, warehouse_account=None, default_expense_account=None,
			default_cost_center=None):
		from accounts.general_ledger import process_gl_map
		if not warehouse_account:
			warehouse_account = get_warehouse_account()
		
		stock_ledger = self.get_stock_ledger_details()
		voucher_details = self.get_voucher_details(stock_ledger, default_expense_account, 
			default_cost_center)
		
		gl_list = []
		warehouse_with_no_account = []
		for detail in voucher_details:
			sle_list = stock_ledger.get(detail.name)
			
			if sle_list:
				for sle in sle_list:
					if warehouse_account.get(sle.warehouse):
						# from warehouse account
						gl_list.append(self.get_gl_dict({
							"account": warehouse_account[sle.warehouse],
							"against": detail.expense_account,
							"cost_center": detail.cost_center,
							"remarks": self.doc.remarks or "Accounting Entry for Stock",
							"debit": flt(sle.stock_value_difference, 2)
						}))

						# to target warehouse / expense account
						gl_list.append(self.get_gl_dict({
							"account": detail.expense_account,
							"against": warehouse_account[sle.warehouse],
							"cost_center": detail.cost_center,
							"remarks": self.doc.remarks or "Accounting Entry for Stock",
							"credit": flt(sle.stock_value_difference, 2)
						}))
					elif sle.warehouse not in warehouse_with_no_account:
						warehouse_with_no_account.append(sle.warehouse)
						
		if warehouse_with_no_account:				
			msgprint(_("No accounting entries for following warehouses") + ": \n" + 
				"\n".join(warehouse_with_no_account))
		
		return process_gl_map(gl_list)
			
	def get_voucher_details(self, stock_ledger, default_expense_account, default_cost_center):
		if not default_expense_account:
			details = self.doclist.get({"parentfield": self.fname})
			for d in details:
				self.check_expense_account(d)
		else:
			details = [webnotes._dict({
				"name":d, 
				"expense_account": default_expense_account, 
				"cost_center": default_cost_center
			}) for d in stock_ledger.keys()]
			
		return details
		
	def get_stock_ledger_details(self):
		stock_ledger = {}
		for sle in webnotes.conn.sql("""select warehouse, stock_value_difference, voucher_detail_no
			from `tabStock Ledger Entry` where voucher_type=%s and voucher_no=%s""",
			(self.doc.doctype, self.doc.name), as_dict=True):
				stock_ledger.setdefault(sle.voucher_detail_no, []).append(sle)
		return stock_ledger
		
	def get_items_and_warehouse_accounts(self, warehouse_account=None):
		items, warehouses = [], []
		if not warehouse_account:
			warehouse_account = get_warehouse_account()
			
		if hasattr(self, "fname"):
			item_doclist = self.doclist.get({"parentfield": self.fname})
		elif self.doc.doctype == "Stock Reconciliation":
			import json
			item_doclist = []
			data = json.loads(self.doc.reconciliation_json)
			for row in data[data.index(self.head_row)+1:]:
				d = webnotes._dict(zip(["item_code", "warehouse", "qty", "valuation_rate"], row))
				item_doclist.append(d)
				
		if item_doclist:
			for d in item_doclist:
				if d.item_code and d.item_code not in items:
					items.append(d.item_code)
				if d.warehouse and d.warehouse not in warehouses:
					warehouses.append(d.warehouse)

			warehouse_account = {wh: warehouse_account[wh] for wh in warehouses 
				if warehouse_account.get(wh)}
		
		return items, warehouse_account
				
	def make_adjustment_entry(self, expected_gle, voucher_obj):
		from accounts.utils import get_stock_and_account_difference
		account_list = [d.account for d in expected_gle]
		acc_diff = get_stock_and_account_difference(account_list, expected_gle[0].posting_date)
		
		cost_center = self.get_company_default("cost_center")
		stock_adjustment_account = self.get_company_default("stock_adjustment_account")

		gl_entries = []
		for account, diff in acc_diff.items():
			if diff:
				gl_entries.append([
					# stock in hand account
					voucher_obj.get_gl_dict({
						"account": account,
						"against": stock_adjustment_account,
						"debit": diff,
						"remarks": "Adjustment Accounting Entry for Stock",
					}),
				
					# account against stock in hand
					voucher_obj.get_gl_dict({
						"account": stock_adjustment_account,
						"against": account,
						"credit": diff,
						"cost_center": cost_center or None,
						"remarks": "Adjustment Accounting Entry for Stock",
					}),
				])
				
		if gl_entries:
			from accounts.general_ledger import make_gl_entries
			make_gl_entries(gl_entries)
			
	def check_expense_account(self, item):
		if item.fields.has_key("expense_account") and not item.expense_account:
			msgprint(_("""Expense/Difference account is mandatory for item: """) + item.item_code, 
				raise_exception=1)
				
		if item.fields.has_key("expense_account") and not item.cost_center:
			msgprint(_("""Cost Center is mandatory for item: """) + item.item_code, 
				raise_exception=1)
				
	def get_sl_entries(self, d, args):		
		sl_dict = {
			"item_code": d.item_code,
			"warehouse": d.warehouse,
			"posting_date": self.doc.posting_date,
			"posting_time": self.doc.posting_time,
			"voucher_type": self.doc.doctype,
			"voucher_no": self.doc.name,
			"voucher_detail_no": d.name,
			"actual_qty": (self.doc.docstatus==1 and 1 or -1)*flt(d.stock_qty),
			"stock_uom": d.stock_uom,
			"incoming_rate": 0,
			"company": self.doc.company,
			"fiscal_year": self.doc.fiscal_year,
			"batch_no": cstr(d.batch_no).strip(),
			"serial_no": d.serial_no,
			"project": d.project_name,
			"is_cancelled": self.doc.docstatus==2 and "Yes" or "No"
		}
		
		sl_dict.update(args)
		return sl_dict
		
	def make_sl_entries(self, sl_entries, is_amended=None):
		from stock.stock_ledger import make_sl_entries
		make_sl_entries(sl_entries, is_amended)
		
	def make_cancel_gl_entries(self):
		if webnotes.conn.sql("""select name from `tabGL Entry` where voucher_type=%s 
			and voucher_no=%s""", (self.doc.doctype, self.doc.name)):
				self.make_gl_entries()
	
def update_gl_entries_after(posting_date, posting_time, warehouse_account=None, for_items=None):
	def _delete_gl_entries(voucher_type, voucher_no):
		webnotes.conn.sql("""delete from `tabGL Entry` 
			where voucher_type=%s and voucher_no=%s""", (voucher_type, voucher_no))
	
	if not warehouse_account:
		warehouse_account = get_warehouse_account()
	future_stock_vouchers = get_future_stock_vouchers(posting_date, posting_time, 
		warehouse_account, for_items)
	gle = get_voucherwise_gl_entries(future_stock_vouchers, posting_date)

	for voucher_type, voucher_no in future_stock_vouchers:
		existing_gle = gle.get((voucher_type, voucher_no), [])
		voucher_obj = webnotes.get_obj(voucher_type, voucher_no)
		expected_gle = voucher_obj.get_gl_entries(warehouse_account)
		if expected_gle:
			if not existing_gle or not compare_existing_and_expected_gle(existing_gle, 
				expected_gle):
					_delete_gl_entries(voucher_type, voucher_no)
					voucher_obj.make_gl_entries(repost_future_gle=False)
		else:
			_delete_gl_entries(voucher_type, voucher_no)
			
def compare_existing_and_expected_gle(existing_gle, expected_gle):
	matched = True
	for entry in expected_gle:
		for e in existing_gle:
			if entry.account==e.account and entry.against_account==e.against_account \
				and entry.cost_center==e.cost_center \
				and (entry.debit != e.debit or entry.credit != e.credit):
					matched = False
					break
	return matched

def get_future_stock_vouchers(posting_date, posting_time, warehouse_account=None, for_items=None):
	future_stock_vouchers = []
	
	condition = ""
	if for_items:
		condition = ''.join([' and item_code in (\'', '\', \''.join(for_items) ,'\')'])
	
	if warehouse_account:
		condition += ''.join([' and warehouse in (\'', '\', \''.join(warehouse_account.keys()) ,'\')'])
	
	for d in webnotes.conn.sql("""select distinct sle.voucher_type, sle.voucher_no 
		from `tabStock Ledger Entry` sle
		where timestamp(sle.posting_date, sle.posting_time) >= timestamp(%s, %s) %s
		order by timestamp(sle.posting_date, sle.posting_time) asc, name asc""" % 
		('%s', '%s', condition), (posting_date, posting_time), 
		as_dict=True):
			future_stock_vouchers.append([d.voucher_type, d.voucher_no])
	
	return future_stock_vouchers
			
def get_voucherwise_gl_entries(future_stock_vouchers, posting_date):
	gl_entries = {}
	if future_stock_vouchers:
		for d in webnotes.conn.sql("""select * from `tabGL Entry` 
			where posting_date >= %s and voucher_no in (%s)""" % 
			('%s', ', '.join(['%s']*len(future_stock_vouchers))), 
			tuple([posting_date] + [d[1] for d in future_stock_vouchers]), as_dict=1):
				gl_entries.setdefault((d.voucher_type, d.voucher_no), []).append(d)
	
	return gl_entries

def get_warehouse_account():
	warehouse_account = dict(webnotes.conn.sql("""select master_name, name from tabAccount 
		where account_type = 'Warehouse' and ifnull(master_name, '') != ''"""))
	return warehouse_account