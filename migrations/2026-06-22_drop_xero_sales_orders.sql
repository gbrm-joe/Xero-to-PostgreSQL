-- 2026-06-22: Drop xero_sales_orders and xero_sales_order_items.
--
-- These tables were never populated by the sync (Xero's UK accounting API
-- has no "sales order" concept) and will never be used. Drop them from
-- every finance_<slug> schema so the schemas do not drift.
--
-- xero_sales_order_items has a FK to xero_sales_orders, so items are
-- dropped first. Both tables are empty in all schemas.

-- finance_gbrm
DROP TABLE IF EXISTS finance_gbrm.xero_sales_order_items;
DROP TABLE IF EXISTS finance_gbrm.xero_sales_orders;

-- finance_mgi
DROP TABLE IF EXISTS finance_mgi.xero_sales_order_items;
DROP TABLE IF EXISTS finance_mgi.xero_sales_orders;

-- finance_mgl
DROP TABLE IF EXISTS finance_mgl.xero_sales_order_items;
DROP TABLE IF EXISTS finance_mgl.xero_sales_orders;
